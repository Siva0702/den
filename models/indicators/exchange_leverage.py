# models/indicators/exchange_leverage.py

import threading
import time

import requests


class ExchangeLeverageEngine:
    """
    Den Engine v35.0 Verified Binance Futures Leverage Matrix:
    All leverage limits calibrated to Binance Futures (same as Bitunix/Weex).
    """

    EQUITY_PAIRS = {
        "GS/USDT": 50, "NVDA/USDT": 50, "TSLA/USDT": 50, "AAPL/USDT": 50,
        "AMZN/USDT": 50, "MSFT/USDT": 50, "GOOGL/USDT": 50, "META/USDT": 50,
        "PLTR/USDT": 50, "NFLX/USDT": 50, "INTC/USDT": 50, "AMD/USDT": 50,
        "SMCI/USDT": 50, "COIN/USDT": 50, "MSTR/USDT": 50, "BABA/USDT": 50,
    }

    COMMODITY_PAIRS = {
        "XAU/USDT": 75, "XAG/USDT": 75, "COPPER/USDT": 50,
    }

    CRYPTO_PAIRS = {
        "BTC/USDT": 125, "ETH/USDT": 125, "SOL/USDT": 75, "XRP/USDT": 75,
        "DOGE/USDT": 75, "BNB/USDT": 75, "AVAX/USDT": 50, "LINK/USDT": 50,
        "NEAR/USDT": 50, "SUI/USDT": 50, "PEPE/USDT": 50, "WIF/USDT": 50,
        "FET/USDT": 50, "RENDER/USDT": 50, "INJ/USDT": 50, "TIA/USDT": 50,
        "ARB/USDT": 50, "OP/USDT": 50, "APT/USDT": 50, "SEI/USDT": 50,
        "TAO/USDT": 50, "PENDLE/USDT": 50, "RUNE/USDT": 50, "ADA/USDT": 50,
        "DOT/USDT": 50, "LTC/USDT": 50, "MATIC/USDT": 50, "STX/USDT": 50,
        "ORDI/USDT": 50,
    }

    # Index/ETF proxies are the least leverage-tolerant instruments in the universe.
    INDEX_PROXIES = {"SPY", "QQQ", "IWM", "XLE"}

    # Crypto bases not individually listed above still behave like crypto, not equity.
    KNOWN_CRYPTO_BASES = {
        "NOT", "TON", "JUP", "W", "ENA", "BONK", "SHIB", "POL", "CL",
    }

    # LIVE CAPS. The tables below are hardcoded literals that were never verified
    # against a venue and cannot track a listing change — BTC sat at 125x, and 39 assets
    # were missing entirely and silently defaulted to 50x. Bybit publishes real
    # per-instrument maxLeverage without authentication, so the true cap is fetched and
    # the tables become a fallback rather than the source of truth.
    _live_cache = {}
    _live_lock = threading.Lock()
    LIVE_TTL = 6 * 3600
    HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                             "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

    _venue_cache = {"data": None, "epoch": 0.0}

    @classmethod
    def venue_caps(cls) -> dict:
        """
        Real leverage limits from the venues actually traded: BITUNIX and WEEX.

        Every cap until now came from Binance/Bybit — exchanges the user does not trade
        on. Both target venues publish maxLeverage without authentication (Bitunix lists
        703 pairs), so the true constraint is now read from the right place. Where both
        list a symbol the LOWER cap wins, because a size that one venue rejects is a
        signal that cannot be executed as specified.
        """
        now = time.time()
        if cls._venue_cache["data"] and now - cls._venue_cache["epoch"] < cls.LIVE_TTL:
            return cls._venue_cache["data"]
        caps = {}
        try:
            d = requests.get("https://fapi.bitunix.com/api/v1/futures/market/trading_pairs",
                             headers=cls.HEADERS, timeout=6).json()
            for r in (d.get("data") or []):
                m = int(float(r.get("maxLeverage", 0) or 0))
                if m:
                    caps[r["symbol"].upper()] = {"bitunix": m}
        except Exception:
            pass
        try:
            w = requests.get("https://api-contract.weex.com/capi/v2/market/contracts",
                             headers=cls.HEADERS, timeout=6).json()
            for c in (w or []):
                sym = (c.get("symbol") or "").replace("cmt_", "").upper()
                m = int(float(c.get("maxLeverage", 0) or 0))
                if sym and m:
                    caps.setdefault(sym, {})["weex"] = m
        except Exception:
            pass
        if caps:
            cls._venue_cache = {"data": caps, "epoch": now}
        return caps

    @classmethod
    def liquidation_safe_leverage(cls, sl_pct: float, buffer: float = 1.6) -> int:
        """
        The cap nobody was applying, and the one that actually blows accounts.

        On isolated margin at Nx, liquidation sits roughly 1/N away from entry. With a
        2% stop at 50x, liquidation is ~2% away — you are liquidated at the same moment
        the stop triggers, or before it, losing the whole margin instead of the planned
        risk. Leverage must therefore be low enough that liquidation sits well BEYOND
        the stop. This returns the highest leverage keeping that true with `buffer`x
        headroom.
        """
        if sl_pct <= 0:
            return 1
        return max(int(1.0 / (sl_pct * buffer)), 1)

    @classmethod
    def live_max_leverage(cls, ticker: str):
        """Real venue cap, or None if unavailable. Cached; never raises."""
        sym = {"PEPE/USDT": "1000PEPEUSDT", "SHIB/USDT": "1000SHIBUSDT",
               "BONK/USDT": "1000BONKUSDT", "MATIC/USDT": "POLUSDT"}.get(
            ticker, ticker.replace("/", "").upper())
        now = time.time()
        with cls._live_lock:
            hit = cls._live_cache.get(sym)
            if hit and hit[1] > now:
                return hit[0]
        val = None
        try:
            r = requests.get(
                f"https://api.bybit.com/v5/market/instruments-info?category=linear&symbol={sym}",
                headers=cls.HEADERS, timeout=4)
            if r.status_code == 200:
                lst = (r.json().get("result") or {}).get("list") or []
                if lst:
                    val = int(float((lst[0].get("leverageFilter") or {}).get("maxLeverage", 0))) or None
        except Exception:
            val = None
        with cls._live_lock:
            # Cache negatives briefly so a dead symbol is not retried every scan.
            cls._live_cache[sym] = (val, now + (cls.LIVE_TTL if val else 900))
        return val

    @classmethod
    def get_calibrated_leverage(cls, ticker: str, ideal_leverage: int,
                                sl_pct: float = None) -> dict:
        live = cls.live_max_leverage(ticker)
        sym = ticker.replace("/", "").upper()
        venue = cls.venue_caps().get(sym, {})
        venue_cap = min(venue.values()) if venue else None
        liq_cap = cls.liquidation_safe_leverage(sl_pct) if sl_pct else None
        if ticker in cls.EQUITY_PAIRS:
            exchange_name = "Binance Futures (Equity)"
            max_allowed = cls.EQUITY_PAIRS[ticker]
        elif ticker in cls.COMMODITY_PAIRS:
            exchange_name = "Binance Futures (Commodity)"
            max_allowed = cls.COMMODITY_PAIRS[ticker]
        elif ticker in cls.CRYPTO_PAIRS:
            exchange_name = "Binance Futures (Crypto)"
            max_allowed = cls.CRYPTO_PAIRS[ticker]
        else:
            # UNLISTED ASSETS. 39 of the 87-asset universe were not in any table and
            # silently defaulted to 50x — including SPY, QQQ, IWM, JPM, V and MA. Venues
            # cap index and equity proxies far below that, so sizing for 50x when the
            # exchange allows 10x understates margin by 5x and takes five times the
            # intended risk. Default now falls back by ASSET CLASS, conservatively,
            # because being under-levered costs upside while being over-levered costs
            # the account.
            base = ticker.split("/")[0].upper()
            if base in cls.INDEX_PROXIES:
                exchange_name, max_allowed = "Binance Futures (Index)", 20
            elif base in cls.KNOWN_CRYPTO_BASES:
                exchange_name, max_allowed = "Binance Futures (Crypto)", 25
            else:
                exchange_name, max_allowed = "Binance Futures (Unlisted)", 20

        # Prefer the live cap; where both exist take the LOWER, since a stale table
        # promising more than the venue allows is the dangerous direction of wrong.
        # Every constraint applies; the tightest wins.
        sources = {"table": max_allowed}
        if live:
            sources["bybit"] = live
        if venue_cap:
            sources["venue"] = venue_cap
            exchange_name = "Bitunix/WEEX"
        if liq_cap:
            sources["liquidation"] = liq_cap
        max_allowed = min(sources.values())
        binding = min(sources, key=sources.get)

        final_leverage = max(min(ideal_leverage, max_allowed), 1)

        return {
            "recommended_leverage": final_leverage,
            "max_exchange_leverage": max_allowed,
            "live_cap": live,
            "venue_cap": venue_cap,
            "venue_detail": venue,
            "liquidation_cap": liq_cap,
            "binding_constraint": binding,
            "primary_exchange": exchange_name
        }
