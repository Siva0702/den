# models/data/exchange_feed.py
import requests
import pandas as pd
import numpy as np

class BitunixWeexLiveFeed:
    """
    Den Engine v35.0 Direct Binance Futures Exchange Feed:
    Fetches REAL kline data from Binance Futures API (same data source as Bitunix/Weex).
    Returns (None, False) if real data is unavailable — NEVER generates fake prices.
    """

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    # Tickers that need remapping on Binance Futures
    TICKER_MAP = {
        "PEPE/USDT": {"symbol": "1000PEPEUSDT", "divisor": 1000.0},
        "MATIC/USDT": {"symbol": "POLUSDT", "divisor": 1.0},
    }

    @classmethod
    def get_exchange_ohlcv(cls, ticker: str, base_price: float = 100.0) -> tuple:
        """Fetch real 15m klines from Binance Futures. Returns (DataFrame, is_real)."""
        
        # Resolve ticker mapping
        mapping = cls.TICKER_MAP.get(ticker)
        if mapping:
            symbol = mapping["symbol"]
            divisor = mapping["divisor"]
        else:
            symbol = ticker.replace("/", "").upper()
            divisor = 1.0

        # Binance Futures klines API
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=15m&limit=100"
        try:
            resp = requests.get(url, headers=cls.HEADERS, timeout=5)
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
                            'volume': float(k[5]) * divisor  # Scale volume inversely
                        })
                    return pd.DataFrame(records), True
            # Binance returned an error for this symbol
            print(f"[!] Binance Futures: {symbol} not found (status {resp.status_code})")
            return None, False
        except Exception as e:
            print(f"[!] Binance Futures fetch error for {symbol}: {e}")
            return None, False
