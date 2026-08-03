# models/data/live_feed.py
import requests
import pandas as pd
import numpy as np
import time

class RealtimeMarketDataFeed:
    """
    Fetches real-time market data directly aligned with Binance & Bitunix Futures REST API.
    Returns (DataFrame, is_real_live_feed: bool).
    """

    @staticmethod
    def fetch_bitunix_crypto_ohlcv(symbol: str = "BTCUSDT", interval: str = "15m", limit: int = 100):
        clean_symbol = symbol.replace("/", "").upper()
        url = f"https://api.binance.com/api/v3/klines?symbol={clean_symbol}&interval={interval}&limit={limit}"
        try:
            resp = requests.get(url, timeout=4)
            if resp.status_code == 200:
                data = resp.json()
                df = pd.DataFrame(data, columns=[
                    'timestamp', 'open', 'high', 'low', 'close', 'volume',
                    'close_time', 'quote_asset_volume', 'number_of_trades',
                    'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
                ])
                for col in ['open', 'high', 'low', 'close', 'volume']:
                    df[col] = df[col].astype(float)
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                df.set_index('timestamp', inplace=True)
                return df[['open', 'high', 'low', 'close', 'volume']], True
        except Exception as e:
            print(f"[!] Live Crypto Feed Fallback for {symbol}: {e}")
        return None, False

    @staticmethod
    def fetch_live_tradfi_ohlcv(ticker: str = "NVDA", interval: str = "15m"):
        clean_ticker = ticker.split("/")[0].upper()
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{clean_ticker}?interval={interval}&range=5d"
        headers = {'User-Agent': 'Mozilla/5.0'}
        try:
            resp = requests.get(url, headers=headers, timeout=4)
            if resp.status_code == 200:
                result = resp.json()['chart']['result'][0]
                timestamps = result['timestamp']
                quote = result['indicators']['quote'][0]
                df = pd.DataFrame({
                    'open': quote['open'],
                    'high': quote['high'],
                    'low': quote['low'],
                    'close': quote['close'],
                    'volume': quote['volume']
                }, index=pd.to_datetime(timestamps, unit='s'))
                df.dropna(inplace=True)
                return df, True
        except Exception as e:
            print(f"[!] Live TradFi Feed Fallback for {ticker}: {e}")
        return None, False

    @classmethod
    def get_live_ohlcv(cls, ticker: str, asset_class: str, base_price: float = 100.0):
        df, is_real = None, False
        if asset_class == "Crypto Futures":
            df, is_real = cls.fetch_bitunix_crypto_ohlcv(ticker)
        elif asset_class in ["Tokenized Equity", "Commodity", "Macro Benchmark", "Global Equity"]:
            df, is_real = cls.fetch_live_tradfi_ohlcv(ticker)

        if df is None or len(df) < 20:
            np.random.seed(int(time.time() * 1000 + hash(ticker)) % 100000)
            prices = base_price + np.cumsum(np.random.randn(100) * (base_price * 0.002))
            df = pd.DataFrame({
                'open': prices,
                'high': prices + (base_price * 0.003),
                'low': prices - (base_price * 0.003),
                'close': prices + (base_price * 0.001),
                'volume': np.random.randint(1000, 50000, size=100)
            }, index=pd.date_range(end=pd.Timestamp.now(), periods=100, freq='15min'))
            is_real = False

        return df, is_real

    @staticmethod
    def format_2dp(value: float) -> str:
        if value is None:
            return "0.00"
        return f"{value:,.2f}"
