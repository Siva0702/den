# models/data/live_feed.py
from data.exchange_feed import BitunixWeexLiveFeed

class RealtimeMarketDataFeed:
    """
    Den Engine v17.0 Direct Exchange Price Feed:
    Queries Bitunix & Weex exchange endpoints directly.
    """
    @staticmethod
    def get_live_ohlcv(ticker: str, asset_class: str, base_price: float = 100.0) -> tuple:
        return BitunixWeexLiveFeed.get_exchange_ohlcv(ticker, base_price)
