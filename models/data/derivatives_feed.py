# models/data/derivatives_feed.py
import time
import threading
import requests

class DerivativesIntelligence:
    """
    Den Engine v39.0 Derivatives Microstructure Intelligence.

    Pulls the free, keyless Binance USD-M futures data endpoints the engine was
    previously blind to:
      - Funding rate + mark/index premium  (positioning cost & crowding)
      - Open Interest history              (real breakout vs. short-covering fake)
      - Global long/short ACCOUNT ratio    (retail crowding -> stop-hunt fuel)
      - Taker buy/sell volume ratio        (aggressor imbalance)
      - L2 order book depth                (liquidity walls & resting stop pools)

    Every value is cached with a TTL because these endpoints are only refreshed
    every 5 minutes upstream anyway, and the scanner cannot afford 5 extra HTTP
    round-trips per asset per scan.

    Availability is honest: if an asset is not a Binance perp (equities, bullion
    proxies routed via Bybit/Bitget) this returns available=False and the
    confluence engine leaves those dimensions unscored rather than guessing.
    """

    HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                             "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    BASE = "https://fapi.binance.com"

    # symbol -> (payload, expiry_epoch)
    _cache = {}
    _lock = threading.Lock()

    TTL_FUNDING = 300.0
    TTL_OI = 300.0
    TTL_RATIO = 300.0
    TTL_DEPTH = 45.0

    # Reuse the same remap the kline feed uses so 1000PEPE etc. resolve.
    TICKER_MAP = {
        "PEPE/USDT": "1000PEPEUSDT",
        "SHIB/USDT": "1000SHIBUSDT",
        "BONK/USDT": "1000BONKUSDT",
        "MATIC/USDT": "POLUSDT",
    }

    @classmethod
    def _symbol(cls, ticker: str) -> str:
        return cls.TICKER_MAP.get(ticker, ticker.replace("/", "").upper())

    @classmethod
    def _cached_get(cls, key: str, url: str, ttl: float):
        now = time.time()
        with cls._lock:
            hit = cls._cache.get(key)
            if hit and hit[1] > now:
                return hit[0]
        try:
            resp = requests.get(url, headers=cls.HEADERS, timeout=5)
            if resp.status_code != 200:
                payload = None
            else:
                payload = resp.json()
        except Exception:
            payload = None

        with cls._lock:
            # Cache negatives briefly too, so dead symbols don't get retried every scan.
            cls._cache[key] = (payload, now + (ttl if payload is not None else 120.0))
        return payload

    # ------------------------------------------------------------------
    # Individual dimensions
    # ------------------------------------------------------------------
    @classmethod
    def get_funding(cls, ticker: str) -> dict:
        sym = cls._symbol(ticker)
        data = cls._cached_get(f"fund:{sym}", f"{cls.BASE}/fapi/v1/premiumIndex?symbol={sym}", cls.TTL_FUNDING)
        if not isinstance(data, dict) or "lastFundingRate" not in data:
            return {"available": False}
        try:
            rate = float(data["lastFundingRate"])
            mark = float(data.get("markPrice", 0.0))
            index = float(data.get("indexPrice", 0.0)) or mark
            premium = (mark - index) / index if index else 0.0
        except Exception:
            return {"available": False}

        # Annualised at 3 funding settlements/day.
        annualised = rate * 3 * 365
        if rate > 0.0005:
            regime, bias = "LONGS_OVERPAYING", "SHORT"
        elif rate < -0.0005:
            regime, bias = "SHORTS_OVERPAYING", "LONG"
        else:
            regime, bias = "NEUTRAL", "NONE"

        return {
            "available": True,
            "funding_rate": round(rate, 8),
            "funding_annualised_pct": round(annualised * 100, 2),
            "mark_index_premium_pct": round(premium * 100, 4),
            "funding_regime": regime,
            "contrarian_bias": bias,
            "is_extreme": abs(rate) > 0.001,
        }

    @classmethod
    def get_open_interest_delta(cls, ticker: str, period: str = "15m") -> dict:
        sym = cls._symbol(ticker)
        url = f"{cls.BASE}/futures/data/openInterestHist?symbol={sym}&period={period}&limit=12"
        data = cls._cached_get(f"oi:{sym}:{period}", url, cls.TTL_OI)
        if not isinstance(data, list) or len(data) < 4:
            return {"available": False}
        try:
            series = [float(d["sumOpenInterest"]) for d in data]
        except Exception:
            return {"available": False}

        latest, prev, oldest = series[-1], series[-2], series[0]
        delta_1 = (latest - prev) / prev if prev else 0.0
        delta_n = (latest - oldest) / oldest if oldest else 0.0
        return {
            "available": True,
            "oi_latest": latest,
            "oi_change_1bar_pct": round(delta_1 * 100, 3),
            "oi_change_12bar_pct": round(delta_n * 100, 3),
            "oi_rising": delta_n > 0.005,
            "oi_falling": delta_n < -0.005,
        }

    @classmethod
    def get_crowding(cls, ticker: str, period: str = "15m") -> dict:
        """Global long/short ACCOUNT ratio — retail positioning. Extremes are contrarian."""
        sym = cls._symbol(ticker)
        url = f"{cls.BASE}/futures/data/globalLongShortAccountRatio?symbol={sym}&period={period}&limit=4"
        data = cls._cached_get(f"ls:{sym}:{period}", url, cls.TTL_RATIO)
        if not isinstance(data, list) or not data:
            return {"available": False}
        try:
            long_acct = float(data[-1]["longAccount"])
            ratio = float(data[-1]["longShortRatio"])
        except Exception:
            return {"available": False}

        # >70% of accounts on one side is where stop-hunts feed.
        if long_acct >= 0.70:
            crowd, contrarian = "CROWDED_LONG", "SHORT"
        elif long_acct <= 0.30:
            crowd, contrarian = "CROWDED_SHORT", "LONG"
        else:
            crowd, contrarian = "BALANCED", "NONE"

        return {
            "available": True,
            "long_account_pct": round(long_acct * 100, 1),
            "short_account_pct": round((1.0 - long_acct) * 100, 1),
            "long_short_ratio": round(ratio, 3),
            "crowding": crowd,
            "contrarian_bias": contrarian,
            "is_extreme": long_acct >= 0.75 or long_acct <= 0.25,
        }

    @classmethod
    def get_taker_aggression(cls, ticker: str, period: str = "15m") -> dict:
        sym = cls._symbol(ticker)
        url = f"{cls.BASE}/futures/data/takerlongshortRatio?symbol={sym}&period={period}&limit=4"
        data = cls._cached_get(f"taker:{sym}:{period}", url, cls.TTL_RATIO)
        if not isinstance(data, list) or not data:
            return {"available": False}
        try:
            ratio = float(data[-1]["buySellRatio"])
        except Exception:
            return {"available": False}
        return {
            "available": True,
            "taker_buy_sell_ratio": round(ratio, 3),
            "aggressive_buyers": ratio > 1.15,
            "aggressive_sellers": ratio < 0.87,
        }

    @classmethod
    def get_orderbook_pressure(cls, ticker: str, limit: int = 100) -> dict:
        """
        Bid/ask depth imbalance plus the largest resting walls.
        A wall is where price gets rejected; the space beyond it is where stops sit.
        """
        sym = cls._symbol(ticker)
        url = f"{cls.BASE}/fapi/v1/depth?symbol={sym}&limit={limit}"
        data = cls._cached_get(f"depth:{sym}", url, cls.TTL_DEPTH)
        if not isinstance(data, dict) or "bids" not in data:
            return {"available": False}
        try:
            bids = [(float(p), float(q)) for p, q in data["bids"]]
            asks = [(float(p), float(q)) for p, q in data["asks"]]
        except Exception:
            return {"available": False}
        if not bids or not asks:
            return {"available": False}

        bid_vol = sum(q for _, q in bids)
        ask_vol = sum(q for _, q in asks)
        total = bid_vol + ask_vol
        if total <= 0:
            return {"available": False}

        imbalance = (bid_vol - ask_vol) / total
        best_bid, best_ask = bids[0][0], asks[0][0]
        mid = (best_bid + best_ask) / 2.0
        spread_bps = ((best_ask - best_bid) / mid) * 10000 if mid else 0.0

        top_bid_wall = max(bids, key=lambda x: x[1])
        top_ask_wall = max(asks, key=lambda x: x[1])
        avg_bid_q = bid_vol / len(bids)
        avg_ask_q = ask_vol / len(asks)

        return {
            "available": True,
            "bid_ask_imbalance": round(imbalance, 4),
            "book_bias": "BID_HEAVY" if imbalance > 0.15 else "ASK_HEAVY" if imbalance < -0.15 else "BALANCED",
            "spread_bps": round(spread_bps, 2),
            "mid_price": mid,
            "bid_wall_price": top_bid_wall[0],
            "bid_wall_strength": round(top_bid_wall[1] / avg_bid_q, 2) if avg_bid_q else 0.0,
            "ask_wall_price": top_ask_wall[0],
            "ask_wall_strength": round(top_ask_wall[1] / avg_ask_q, 2) if avg_ask_q else 0.0,
            "is_thin_book": spread_bps > 8.0,
        }

    # ------------------------------------------------------------------
    # Unified read
    # ------------------------------------------------------------------
    @classmethod
    def analyze(cls, ticker: str, include_depth: bool = True) -> dict:
        """
        Single call the confluence engine uses. Returns a normalised verdict plus
        explicit trap warnings. available=False means "we have no derivatives read
        on this asset" — callers must not treat that as a negative signal.
        """
        funding = cls.get_funding(ticker)
        if not funding.get("available"):
            return {"available": False, "reason": "not a Binance USD-M perpetual"}

        oi = cls.get_open_interest_delta(ticker)
        crowd = cls.get_crowding(ticker)
        taker = cls.get_taker_aggression(ticker)
        book = cls.get_orderbook_pressure(ticker) if include_depth else {"available": False}

        long_pressure = 0.0
        short_pressure = 0.0
        notes = []
        traps = []

        # Funding: pay-side is the crowded side. Fade extremes only.
        if funding.get("is_extreme"):
            if funding["contrarian_bias"] == "SHORT":
                short_pressure += 1.0
                traps.append(f"Extreme funding {funding['funding_annualised_pct']}% APR — longs are paying, squeeze risk")
            elif funding["contrarian_bias"] == "LONG":
                long_pressure += 1.0
                traps.append(f"Extreme negative funding {funding['funding_annualised_pct']}% APR — shorts are paying")

        # Crowding: >70% one-sided accounts is stop-hunt fuel.
        if crowd.get("available") and crowd.get("crowding") != "BALANCED":
            if crowd["contrarian_bias"] == "SHORT":
                short_pressure += 1.0
                traps.append(f"{crowd['long_account_pct']}% of accounts are LONG — stop pool sits below")
            else:
                long_pressure += 1.0
                traps.append(f"{crowd['short_account_pct']}% of accounts are SHORT — stop pool sits above")
            notes.append(f"Retail crowding: {crowd['crowding']}")

        # Taker aggression is a with-trend confirmation, not contrarian.
        if taker.get("available"):
            if taker.get("aggressive_buyers"):
                long_pressure += 1.0
                notes.append(f"Taker flow buy-skewed ({taker['taker_buy_sell_ratio']}x)")
            elif taker.get("aggressive_sellers"):
                short_pressure += 1.0
                notes.append(f"Taker flow sell-skewed ({taker['taker_buy_sell_ratio']}x)")

        # Book pressure.
        if book.get("available"):
            if book["book_bias"] == "BID_HEAVY":
                long_pressure += 0.5
            elif book["book_bias"] == "ASK_HEAVY":
                short_pressure += 0.5
            if book.get("is_thin_book"):
                traps.append(f"Thin book ({book['spread_bps']}bps spread) — slippage & wick risk elevated")

        if long_pressure > short_pressure:
            bias = "LONG"
        elif short_pressure > long_pressure:
            bias = "SHORT"
        else:
            bias = "NONE"

        return {
            "available": True,
            "funding": funding,
            "open_interest": oi,
            "crowding": crowd,
            "taker": taker,
            "book": book,
            "derivatives_bias": bias,
            "long_pressure": round(long_pressure, 2),
            "short_pressure": round(short_pressure, 2),
            "notes": notes,
            "trap_warnings": traps,
        }

    # ------------------------------------------------------------------
    @classmethod
    def classify_breakout(cls, ticker: str, direction: str) -> dict:
        """
        The fake-breakout test. A real breakout is funded by NEW positions:
        price extends AND open interest rises. Price extends while OI falls means
        the move is short-covering / long-liquidation — it retraces.
        """
        oi = cls.get_open_interest_delta(ticker)
        if not oi.get("available"):
            return {"available": False}

        if oi["oi_rising"]:
            verdict, confidence = "GENUINE_BREAKOUT", 1.0
            detail = f"OI +{oi['oi_change_12bar_pct']}% — new money funding the move"
        elif oi["oi_falling"]:
            verdict, confidence = "LIKELY_FAKE_SQUEEZE", 0.0
            detail = f"OI {oi['oi_change_12bar_pct']}% — move is position unwind, not accumulation"
        else:
            verdict, confidence = "INCONCLUSIVE", 0.5
            detail = f"OI flat ({oi['oi_change_12bar_pct']}%)"

        return {
            "available": True,
            "verdict": verdict,
            "confidence": confidence,
            "detail": detail,
            "direction_checked": direction,
        }
