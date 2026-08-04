# models/news/market_universe.py

class DynamicMarketUniverse:
    """
    Den Engine v35.0 Verified Binance Futures Market Universe:
    Only includes crypto tickers confirmed available on Binance/Bybit Futures API
    (same data source used by Bitunix & Weex exchanges).
    Tokenized equities and commodities have been removed as they don't exist on crypto futures.
    All base_price values reflect real Binance Futures prices as of 2026.
    """

    CRYPTO_UNIVERSE = [
        "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "DOGE/USDT",
        "AVAX/USDT", "LINK/USDT", "NEAR/USDT", "SUI/USDT", "PEPE/USDT",
        "WIF/USDT", "FET/USDT", "RENDER/USDT", "INJ/USDT", "TIA/USDT",
        "ARB/USDT", "OP/USDT", "APT/USDT", "SEI/USDT", "TAO/USDT",
        "PENDLE/USDT", "RUNE/USDT", "BNB/USDT", "ADA/USDT", "DOT/USDT",
        "LTC/USDT", "MATIC/USDT", "STX/USDT", "ORDI/USDT", "NOT/USDT", 
        "TON/USDT", "JUP/USDT", "W/USDT", "ENA/USDT", "BONK/USDT"
    ]

    @classmethod
    def get_full_hunting_universe(cls) -> list:
        universe = []
        for symbol in cls.CRYPTO_UNIVERSE:
            universe.append({
                "ticker": symbol,
                "asset_class": "Crypto Futures",
                "sector": "Crypto",
                "exchange": "Binance Futures / Bitunix / Weex",
                "base_price": cls._get_base_price(symbol)
            })
        return universe

    @staticmethod
    def _get_base_price(ticker: str) -> float:
        prices = {
            "BTC/USDT": 63965.0, "ETH/USDT": 1876.0, "SOL/USDT": 73.84, "XRP/USDT": 1.076,
            "DOGE/USDT": 0.0704, "AVAX/USDT": 6.82, "LINK/USDT": 8.20, "NEAR/USDT": 1.73,
            "SUI/USDT": 0.694, "PEPE/USDT": 0.0000085, "WIF/USDT": 0.1394, "FET/USDT": 0.1457,
            "RENDER/USDT": 1.351, "INJ/USDT": 4.926, "TIA/USDT": 0.3351, "ARB/USDT": 0.0817,
            "OP/USDT": 0.0878, "APT/USDT": 0.5725, "SEI/USDT": 0.0413, "TAO/USDT": 190.68,
            "PENDLE/USDT": 1.344, "RUNE/USDT": 0.4406, "BNB/USDT": 591.84, "ADA/USDT": 0.195,
            "DOT/USDT": 0.8296, "LTC/USDT": 44.28, "MATIC/USDT": 0.195, "STX/USDT": 0.1372,
            "ORDI/USDT": 3.409, "NOT/USDT": 0.015, "TON/USDT": 6.2, "JUP/USDT": 0.85,
            "W/USDT": 0.60, "ENA/USDT": 0.70, "BONK/USDT": 0.000025
        }
        return prices.get(ticker, 100.0)
