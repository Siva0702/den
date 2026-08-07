# models/data/derivatives_fallback.py
import threading
import time

import requests


class BybitDerivativesFallback:
    """
    Derivatives source used when Binance USD-M is unreachable.

    Binance fapi returns HTTP 451 to US datacentres. Render runs in one, so the primary
    feed was dead in production while working perfectly from a developer machine — the
    negative cache then held it dead for 900s at a time, for the whole session. Measured
    result: funding, open interest and crowding populated on 91 of 773 records (12%),
    with no error surfaced anywhere, because `available: False` is a legitimate value
    that callers are required to tolerate.

    Bybit v5 serves the same three dimensions, needs no key, is not geo-blocked, and
    lists the tokenised-equity perps (SNOWUSDT etc.) the universe trades.

    Return shapes match DerivativesIntelligence exactly so this is a drop-in — a caller
    cannot tell which venue answered, and nothing downstream needs to change.
    """

    BASE = "https://api.bybit.com"
    HEADERS = {"User-Agent": "DenEngine/42.0"}
    TIMEOUT = 6.0                    # primary uses 2.0s; this is a fallback, not a race
    TTL = 300.0
    TTL_NEGATIVE = 600.0

    _cache = {}
    _lock = threading.Lock()

    @classmethod
    def _symbol(cls, ticker: str) -> str:
        return {
            "PEPE/USDT": "1000PEPEUSDT",
            "SHIB/USDT": "1000SHIBUSDT",
            "BONK/USDT": "1000BONKUSDT",
            "MATIC/USDT": "POLUSDT",
        }.get(ticker, ticker.replace("/", "").upper())

    @classmethod
    def _get(cls, key: str, path: str, params: dict):
        now = time.time()
        with cls._lock:
            hit = cls._cache.get(key)
            if hit and hit[1] > now:
                return hit[0]
        payload = None
        try:
            r = requests.get(f"{cls.BASE}{path}", params=params,
                             headers=cls.HEADERS, timeout=cls.TIMEOUT)
            if r.status_code == 200:
                j = r.json()
                if j.get("retCode") == 0:
                    payload = j.get("result")
        except Exception:
            payload = None
        with cls._lock:
            cls._cache[key] = (payload, now + (cls.TTL if payload else cls.TTL_NEGATIVE))
        return payload

    # ------------------------------------------------------------------
    @classmethod
    def get_funding(cls, ticker: str) -> dict:
        sym = cls._symbol(ticker)
        res = cls._get(f"by:tick:{sym}", "/v5/market/tickers",
                       {"category": "linear", "symbol": sym})
        lst = (res or {}).get("list") or []
        if not lst:
            return {"available": False}
        try:
            d = lst[0]
            rate = float(d.get("fundingRate") or 0.0)
            mark = float(d.get("markPrice") or 0.0)
            index = float(d.get("indexPrice") or 0.0) or mark
            premium = (mark - index) / index if index else 0.0
        except (TypeError, ValueError):
            return {"available": False}

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
            "source": "bybit",
        }

    @classmethod
    def get_open_interest_delta(cls, ticker: str, period: str = "15m") -> dict:
        sym = cls._symbol(ticker)
        iv = {"5m": "5min", "15m": "15min", "30m": "30min", "1h": "1h"}.get(period, "15min")
        res = cls._get(f"by:oi:{sym}:{iv}", "/v5/market/open-interest",
                       {"category": "linear", "symbol": sym,
                        "intervalTime": iv, "limit": 12})
        lst = (res or {}).get("list") or []
        if len(lst) < 4:
            return {"available": False}
        try:
            # Bybit returns newest-first; reverse to match Binance's oldest-first.
            series = [float(x["openInterest"]) for x in lst][::-1]
        except (KeyError, TypeError, ValueError):
            return {"available": False}

        latest, prev, oldest = series[-1], series[-2], series[0]
        d1 = (latest - prev) / prev if prev else 0.0
        dn = (latest - oldest) / oldest if oldest else 0.0
        return {
            "available": True,
            "oi_latest": latest,
            "oi_change_1bar_pct": round(d1 * 100, 3),
            "oi_change_12bar_pct": round(dn * 100, 3),
            "oi_rising": dn > 0.005,
            "oi_falling": dn < -0.005,
            "source": "bybit",
        }

    @classmethod
    def get_crowding(cls, ticker: str, period: str = "15m") -> dict:
        sym = cls._symbol(ticker)
        iv = {"5m": "5min", "15m": "15min", "30m": "30min", "1h": "1h"}.get(period, "15min")
        res = cls._get(f"by:ls:{sym}:{iv}", "/v5/market/account-ratio",
                       {"category": "linear", "symbol": sym, "period": iv, "limit": 4})
        lst = (res or {}).get("list") or []
        if not lst:
            return {"available": False}
        try:
            long_acct = float(lst[0].get("buyRatio"))
            short_acct = float(lst[0].get("sellRatio"))
        except (TypeError, ValueError):
            return {"available": False}
        ratio = (long_acct / short_acct) if short_acct else 0.0

        if long_acct >= 0.70:
            crowd, contrarian = "CROWDED_LONG", "SHORT"
        elif long_acct <= 0.30:
            crowd, contrarian = "CROWDED_SHORT", "LONG"
        else:
            crowd, contrarian = "BALANCED", "NONE"

        return {
            "available": True,
            "long_account_pct": round(long_acct * 100, 1),
            "short_account_pct": round(short_acct * 100, 1),
            "long_short_ratio": round(ratio, 3),
            "crowding": crowd,
            "contrarian_bias": contrarian,
            "is_extreme": long_acct >= 0.75 or long_acct <= 0.25,
            "source": "bybit",
        }
