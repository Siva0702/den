# models/data/exchange_feed.py
import requests
import pandas as pd
import numpy as np

class BitunixWeexLiveFeed:
    """
    Den Engine v36.0 Global Multi-Exchange Failover Adapter:
    Fetches real 15m kline data from Binance Futures, with automatic failover to Bybit
    and Bitget REST APIs. Eliminates HTTP 451 (US IP geo-blocking) on Render Cloud!
    """

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    TICKER_MAP = {
        "PEPE/USDT": {"symbol": "1000PEPEUSDT", "divisor": 1000.0},
        "MATIC/USDT": {"symbol": "POLUSDT", "divisor": 1.0},
    }

    @classmethod
    def get_exchange_ohlcv(cls, ticker: str, base_price: float = 100.0) -> tuple:
        """Fetch real 15m klines from Binance, Bybit, or Bitget. Returns (DataFrame, is_real)."""
        mapping = cls.TICKER_MAP.get(ticker)
        if mapping:
            symbol = mapping["symbol"]
            divisor = mapping["divisor"]
        else:
            symbol = ticker.replace("/", "").upper()
            divisor = 1.0

        # 1. Try Binance Futures API
        try:
            url_binance = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=15m&limit=100"
            resp = requests.get(url_binance, headers=cls.HEADERS, timeout=3)
            if resp.status_code == 200:
                raw_klines = resp.json()
                if isinstance(raw_klines, list) and len(raw_klines) > 0:
                    records = []
                    for k in raw_klines:
                        records.append({
                            'timestamp': int(k[0]),
                            'open': float(k[1]) / divisor,
                            'high': float(k[2]) / divisor,
                            'low': float(k[3]) / divisor,
                            'close': float(k[4]) / divisor,
                            'volume': float(k[5]) * divisor
                        })
                    return pd.DataFrame(records), True
        except Exception:
            pass

        # 2. Try Bybit Linear Futures API (No HTTP 451 US Geo-block)
        try:
            url_bybit = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval=15&limit=100"
            resp = requests.get(url_bybit, headers=cls.HEADERS, timeout=3)
            if resp.status_code == 200:
                data = resp.json()
                klines = data.get("result", {}).get("list", [])
                if klines and len(klines) > 0:
                    records = []
                    # Bybit returns newest first, so reverse to chronological
                    for k in reversed(klines):
                        records.append({
                            'timestamp': int(k[0]),
                            'open': float(k[1]) / divisor,
                            'high': float(k[2]) / divisor,
                            'low': float(k[3]) / divisor,
                            'close': float(k[4]) / divisor,
                            'volume': float(k[5]) * divisor
                        })
                    return pd.DataFrame(records), True
        except Exception:
            pass

        # 3. Try Bitget Futures Market API
        try:
            url_bitget = f"https://api.bitget.com/api/v2/mix/market/candles?symbol={symbol}&granularity=15m&limit=100&productType=USDT-FUTURES"
            resp = requests.get(url_bitget, headers=cls.HEADERS, timeout=3)
            if resp.status_code == 200:
                data = resp.json()
                klines = data.get("data", [])
                if klines and len(klines) > 0:
                    records = []
                    for k in klines:
                        records.append({
                            'timestamp': int(k[0]),
                            'open': float(k[1]) / divisor,
                            'high': float(k[2]) / divisor,
                            'low': float(k[3]) / divisor,
                            'close': float(k[4]) / divisor,
                            'volume': float(k[5]) * divisor
                        })
                    return pd.DataFrame(records), True
        except Exception:
            pass

        print(f"[!] All Exchange Feeds (Binance/Bybit/Bitget) failed or non-existent for symbol: {symbol}")
        return None, False
