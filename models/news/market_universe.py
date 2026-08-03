# models/news/market_universe.py

class DynamicMarketUniverse:
    """
    Den Engine v17.2 Exchange-Verified Market Universe:
    Explicitly tracks exchange availability:
    - BAC/USDT: WEEX EXCLUSIVE ($62.33 Base Price)
    - GS/USDT: BITUNIX EXCLUSIVE ($1,029.00 Base Price)
    - Crypto & Bullion (XAU, XAG, BTC, ETH, SOL): BITUNIX & WEEX
    """

    CRYPTO_UNIVERSE = [
        "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "DOGE/USDT", 
        "AVAX/USDT", "LINK/USDT", "NEAR/USDT", "SUI/USDT", "PEPE/USDT", 
        "WIF/USDT", "FET/USDT", "RENDER/USDT", "INJ/USDT", "TIA/USDT", 
        "ARB/USDT", "OP/USDT", "APT/USDT", "SEI/USDT", "TAO/USDT",
        "BNB/USDT", "ADA/USDT", "DOT/USDT", "LTC/USDT", "MATIC/USDT"
    ]

    WEEX_EXCLUSIVE_EQUITIES = [
        {"ticker": "BAC/USDT", "sector": "Banking / Financials", "exchange": "Weex Exclusive", "base_price": 62.33},
        {"ticker": "COIN/USDT", "sector": "Crypto Exchange", "exchange": "Weex / Bitunix", "base_price": 220.0},
        {"ticker": "MSTR/USDT", "sector": "Bitcoin Treasury Proxy", "exchange": "Weex / Bitunix", "base_price": 1400.0},
        {"ticker": "BABA/USDT", "sector": "China Tech", "exchange": "Weex", "base_price": 80.0},
        {"ticker": "PDD/USDT", "sector": "China Commerce", "exchange": "Weex", "base_price": 130.0}
    ]

    BITUNIX_EXCLUSIVE_EQUITIES = [
        {"ticker": "GS/USDT", "sector": "Investment Banking", "exchange": "Bitunix Exclusive", "base_price": 1029.0},
        {"ticker": "NVDA/USDT", "sector": "Semiconductors / AI", "exchange": "Bitunix", "base_price": 125.0},
        {"ticker": "AMD/USDT", "sector": "Semiconductors", "exchange": "Bitunix", "base_price": 150.0},
        {"ticker": "INTC/USDT", "sector": "Semiconductors", "exchange": "Bitunix", "base_price": 30.0},
        {"ticker": "AAPL/USDT", "sector": "Consumer Tech", "exchange": "Bitunix", "base_price": 225.0},
        {"ticker": "TSLA/USDT", "sector": "EV / Autonomous AI", "exchange": "Bitunix", "base_price": 220.0},
        {"ticker": "MSFT/USDT", "sector": "Software / AI", "exchange": "Bitunix", "base_price": 440.0},
        {"ticker": "GOOGL/USDT", "sector": "Search / Cloud", "exchange": "Bitunix", "base_price": 175.0},
        {"ticker": "AMZN/USDT", "sector": "Cloud / E-Commerce", "exchange": "Bitunix", "base_price": 180.0},
        {"ticker": "META/USDT", "sector": "Social Media / AI", "exchange": "Bitunix", "base_price": 500.0},
        {"ticker": "PLTR/USDT", "sector": "AI / Defense", "exchange": "Bitunix", "base_price": 28.0}
    ]

    COMMODITIES_BULLION = [
        {"ticker": "XAU/USDT", "sector": "Tokenized Gold Bullion", "exchange": "Bitunix / Weex", "base_price": 2420.0},
        {"ticker": "XAG/USDT", "sector": "Tokenized Silver Bullion", "exchange": "Bitunix / Weex", "base_price": 28.50},
        {"ticker": "WTI/USDT", "sector": "WTI Crude Oil", "exchange": "Bitunix / Weex", "base_price": 78.0},
        {"ticker": "BRENT/USDT", "sector": "Brent Crude Oil", "exchange": "Bitunix / Weex", "base_price": 82.0},
        {"ticker": "NGAS/USDT", "sector": "Natural Gas", "exchange": "Bitunix / Weex", "base_price": 2.20}
    ]

    @classmethod
    def get_full_hunting_universe(cls) -> list:
        universe = []
        for symbol in cls.CRYPTO_UNIVERSE:
            universe.append({"ticker": symbol, "asset_class": "Crypto Futures", "sector": "Crypto", "exchange": "Bitunix / Weex", "base_price": cls._get_base_price(symbol)})
        for item in cls.WEEX_EXCLUSIVE_EQUITIES:
            universe.append({"ticker": item["ticker"], "asset_class": "Tokenized Equity", "sector": item["sector"], "exchange": item["exchange"], "base_price": item["base_price"]})
        for item in cls.BITUNIX_EXCLUSIVE_EQUITIES:
            universe.append({"ticker": item["ticker"], "asset_class": "Tokenized Equity", "sector": item["sector"], "exchange": item["exchange"], "base_price": item["base_price"]})
        for item in cls.COMMODITIES_BULLION:
            universe.append({"ticker": item["ticker"], "asset_class": "Commodity", "sector": item["sector"], "exchange": item["exchange"], "base_price": item["base_price"]})
        return universe

    @staticmethod
    def _get_base_price(ticker: str) -> float:
        prices = {
            "BTC/USDT": 63900.0, "ETH/USDT": 1870.0, "SOL/USDT": 74.0, "XRP/USDT": 1.08,
            "DOGE/USDT": 0.12, "AVAX/USDT": 28.0, "LINK/USDT": 14.0, "NEAR/USDT": 5.50,
            "BAC/USDT": 62.33, "GS/USDT": 1029.0, "XAU/USDT": 2420.0, "XAG/USDT": 28.50
        }
        return prices.get(ticker, 100.0)
