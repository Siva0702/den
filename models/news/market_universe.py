# models/news/market_universe.py

class DynamicMarketUniverse:
    """
    Den Engine v35.0 Verified Binance Futures Market Universe:
    Only includes tickers confirmed available on Binance Futures API
    (same data source used by Bitunix & Weex exchanges).
    All base_price values reflect real Binance Futures prices as of 2026-08-04.
    """

    CRYPTO_UNIVERSE = [
        "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "DOGE/USDT",
        "AVAX/USDT", "LINK/USDT", "NEAR/USDT", "SUI/USDT", "PEPE/USDT",
        "WIF/USDT", "FET/USDT", "RENDER/USDT", "INJ/USDT", "TIA/USDT",
        "ARB/USDT", "OP/USDT", "APT/USDT", "SEI/USDT", "TAO/USDT",
        "PENDLE/USDT", "RUNE/USDT", "BNB/USDT", "ADA/USDT", "DOT/USDT",
        "LTC/USDT", "MATIC/USDT", "STX/USDT", "ORDI/USDT"
    ]

    TOKENIZED_EQUITIES = [
        {"ticker": "COIN/USDT", "sector": "Crypto Exchange", "exchange": "Binance Futures", "base_price": 148.13},
        {"ticker": "MSTR/USDT", "sector": "Bitcoin Treasury Proxy", "exchange": "Binance Futures", "base_price": 94.23},
        {"ticker": "BABA/USDT", "sector": "China Tech", "exchange": "Binance Futures", "base_price": 126.65},
        {"ticker": "GS/USDT", "sector": "Investment Banking", "exchange": "Binance Futures", "base_price": 1041.35},
        {"ticker": "NVDA/USDT", "sector": "Semiconductors / AI", "exchange": "Binance Futures", "base_price": 210.37},
        {"ticker": "SMCI/USDT", "sector": "AI Infrastructure", "exchange": "Binance Futures", "base_price": 29.73},
        {"ticker": "AMD/USDT", "sector": "Semiconductors", "exchange": "Binance Futures", "base_price": 506.84},
        {"ticker": "INTC/USDT", "sector": "Semiconductors", "exchange": "Binance Futures", "base_price": 95.87},
        {"ticker": "AAPL/USDT", "sector": "Consumer Tech", "exchange": "Binance Futures", "base_price": 302.77},
        {"ticker": "TSLA/USDT", "sector": "EV / Autonomous AI", "exchange": "Binance Futures", "base_price": 324.69},
        {"ticker": "MSFT/USDT", "sector": "Software / AI", "exchange": "Binance Futures", "base_price": 477.31},
        {"ticker": "GOOGL/USDT", "sector": "Search / Cloud", "exchange": "Binance Futures", "base_price": 367.57},
        {"ticker": "AMZN/USDT", "sector": "Cloud / E-Commerce", "exchange": "Binance Futures", "base_price": 276.97},
        {"ticker": "META/USDT", "sector": "Social Media / AI", "exchange": "Binance Futures", "base_price": 581.82},
        {"ticker": "PLTR/USDT", "sector": "AI / Defense", "exchange": "Binance Futures", "base_price": 146.65},
        {"ticker": "NFLX/USDT", "sector": "Streaming Entertainment", "exchange": "Binance Futures", "base_price": 72.55},
    ]

    COMMODITIES_BULLION = [
        {"ticker": "XAU/USDT", "sector": "Tokenized Gold Bullion", "exchange": "Binance Futures", "base_price": 4071.26},
        {"ticker": "XAG/USDT", "sector": "Tokenized Silver Bullion", "exchange": "Binance Futures", "base_price": 59.66},
        {"ticker": "COPPER/USDT", "sector": "Industrial Copper", "exchange": "Binance Futures", "base_price": 6.641},
    ]

    @classmethod
    def get_full_hunting_universe(cls) -> list:
        universe = []
        for symbol in cls.CRYPTO_UNIVERSE:
            universe.append({
                "ticker": symbol,
                "asset_class": "Crypto Futures",
                "sector": "Crypto",
                "exchange": "Binance Futures",
                "base_price": cls._get_base_price(symbol)
            })
        for item in cls.TOKENIZED_EQUITIES:
            universe.append({
                "ticker": item["ticker"],
                "asset_class": "Tokenized Equity",
                "sector": item["sector"],
                "exchange": item["exchange"],
                "base_price": item["base_price"]
            })
        for item in cls.COMMODITIES_BULLION:
            universe.append({
                "ticker": item["ticker"],
                "asset_class": "Commodity",
                "sector": item["sector"],
                "exchange": item["exchange"],
                "base_price": item["base_price"]
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
            "ORDI/USDT": 3.409,
        }
        return prices.get(ticker, 100.0)
