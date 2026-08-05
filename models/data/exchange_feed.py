# models/data/exchange_feed.py
import threading
import time
import requests
import pandas as pd
import numpy as np

class BitunixWeexLiveFeed:
    """
    Den Engine v36.0 Global Multi-Exchange Failover Adapter:
    Fetches real kline data from Binance Futures, with automatic failover to Bybit
    and Bitget REST APIs. Eliminates HTTP 451 (US IP geo-blocking) on Render Cloud!
    """

    # Which exchange actually served each symbol last time. On a US datacenter IP
    # Binance returns HTTP 451, so every request was burning three timeouts before
    # landing on Bybit — 397s per scan on Render vs 50s locally. Remembering the
    # winner turns that back into one call.
    # Higher timeframes barely move between 15-second scans: a 4h candle changes once
    # every 4 hours, yet the scanner refetched all 87 of them every cycle. Caching by
    # timeframe removes ~60% of all HTTP requests, which is the single biggest speed
    # lever available — bigger than any per-request tuning.
    _tf_cache = {}
    _tf_lock = threading.Lock()
    # Seconds per candle. Cache entries expire at the next CANDLE CLOSE rather than
    # on a fixed timer: a 15m candle is immutable between :00 and :15, so refetching it
    # every 20 seconds returned identical bytes 45 times per candle. Expiring on the
    # boundary means exactly one fetch per candle per asset — strictly less work for
    # strictly the same information.
    TF_SECONDS = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
    CANDLE_BUFFER = 4.0        # let the exchange finalise the close before refetching

    _route = {}
    _route_lock = threading.Lock()
    TIMEOUT = 2.0

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    TICKER_MAP = {
        "PEPE/USDT": {"symbol": "1000PEPEUSDT", "divisor": 1000.0},
        "SHIB/USDT": {"symbol": "1000SHIBUSDT", "divisor": 1000.0},
        "BONK/USDT": {"symbol": "1000BONKUSDT", "divisor": 1000.0},
        "MATIC/USDT": {"symbol": "POLUSDT", "divisor": 1.0},
    }

    @classmethod
    def get_exchange_ohlcv(cls, ticker: str, base_price: float = 100.0, interval: str = "15m",
                           limit: int = 100) -> tuple:
        """Fetch real klines from Binance, Bybit, or Bitget. Returns (DataFrame, is_real)."""
        mapping = cls.TICKER_MAP.get(ticker)
        if mapping:
            symbol = mapping["symbol"]
            divisor = mapping["divisor"]
        else:
            symbol = ticker.replace("/", "").upper()
            divisor = 1.0

        binance_int = {"15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}.get(interval, "15m")
        bybit_int = {"15m": "15", "1h": "60", "4h": "240", "1d": "D"}.get(interval, "15")
        bitget_int = {"15m": "15m", "1h": "1H", "4h": "4H", "1d": "1D"}.get(interval, "15m")

        def _binance():
            url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={binance_int}&limit={limit}"
            r = requests.get(url, headers=cls.HEADERS, timeout=cls.TIMEOUT)
            if r.status_code != 200:
                return None
            raw = r.json()
            if not isinstance(raw, list) or not raw:
                return None
            return [{'timestamp': int(k[0]), 'open': float(k[1]) / divisor, 'high': float(k[2]) / divisor,
                     'low': float(k[3]) / divisor, 'close': float(k[4]) / divisor,
                     'volume': float(k[5]) * divisor} for k in raw]

        def _bybit():
            url = (f"https://api.bybit.com/v5/market/kline?category=linear&symbol={symbol}"
                   f"&interval={bybit_int}&limit={min(limit, 1000)}")
            r = requests.get(url, headers=cls.HEADERS, timeout=cls.TIMEOUT)
            if r.status_code != 200:
                return None
            kl = (r.json().get("result") or {}).get("list") or []
            if not kl:
                return None
            return [{'timestamp': int(k[0]), 'open': float(k[1]) / divisor, 'high': float(k[2]) / divisor,
                     'low': float(k[3]) / divisor, 'close': float(k[4]) / divisor,
                     'volume': float(k[5]) * divisor} for k in reversed(kl)]

        def _bitget():
            url = (f"https://api.bitget.com/api/v2/mix/market/candles?symbol={symbol}"
                   f"&granularity={bitget_int}&limit={min(limit, 1000)}&productType=USDT-FUTURES")
            r = requests.get(url, headers=cls.HEADERS, timeout=cls.TIMEOUT)
            if r.status_code != 200:
                return None
            kl = r.json().get("data") or []
            if not kl:
                return None
            return [{'timestamp': int(k[0]), 'open': float(k[1]) / divisor, 'high': float(k[2]) / divisor,
                     'low': float(k[3]) / divisor, 'close': float(k[4]) / divisor,
                     'volume': float(k[5]) * divisor} for k in reversed(kl)]

        # Serve from the timeframe cache when still fresh.
        ck = f"{symbol}:{interval}:{limit}"
        now_ts = time.time()
        # Expire precisely when this timeframe's next candle closes.
        span = cls.TF_SECONDS.get(interval, 900)
        expiry = (int(now_ts // span) + 1) * span + cls.CANDLE_BUFFER
        with cls._tf_lock:
            hit = cls._tf_cache.get(ck)
            if hit and hit[1] > now_ts:
                return hit[0].copy(), True

        providers = [("binance", _binance), ("bybit", _bybit), ("bitget", _bitget)]

        # Sticky routing: try whatever worked for this symbol last time, first.
        with cls._route_lock:
            preferred = cls._route.get(symbol)
        if preferred:
            providers.sort(key=lambda kv: kv[0] != preferred)

        for name, fn in providers:
            try:
                records = fn()
            except Exception:
                records = None
            if records:
                with cls._route_lock:
                    cls._route[symbol] = name
                df = pd.DataFrame(records)
                with cls._tf_lock:
                    cls._tf_cache[ck] = (df, expiry)
                    if len(cls._tf_cache) > 2000:
                        cls._tf_cache = {k: v for k, v in cls._tf_cache.items() if v[1] > now_ts}
                return df.copy(), True

        print(f"[!] All Exchange Feeds (Binance/Bybit/Bitget) failed or non-existent for symbol: {symbol}")
        return None, False
