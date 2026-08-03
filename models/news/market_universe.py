# models/news/market_universe.py
import requests

class DynamicMarketUniverse:
    """
    Dynamically tracks the entire high-opportunity universe across:
    1. Top Crypto Futures (BTC, ETH, SOL, XRP, DOGE, AVAX, LINK, NEAR, SUI, PEPE, WIF, RENDER, INJ, TIA, ARB, OP, APT, etc.)
    2. Mega-Cap & Momentum TradFi / Tokenized Equities (NVDA, TSLA, AAPL, AMZN, MSFT, AMD, META, GOOGL, COIN, PLTR, MSTR)
    3. Commodities & Macro FX (GOLD, SILVER, OIL, BRENT, NATGAS, DXY, EURUSD)
    """

    DEFAULT_CRYPTO_UNIVERSE = [
        "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "DOGE/USDT", 
        "AVAX/USDT", "LINK/USDT", "NEAR/USDT", "SUI/USDT", "PEPE/USDT", 
        "WIF/USDT", "FET/USDT", "RENDER/USDT", "INJ/USDT", "TIA/USDT", 
        "ARB/USDT", "OP/USDT", "APT/USDT", "SEI/USDT", "TAO/USDT",
        "BNB/USDT", "ADA/USDT", "DOT/USDT", "LTC/USDT", "MATIC/USDT"
    ]

    TRADFI_STOCKS_UNIVERSE = [
        {"ticker": "NVDA/USDT", "name": "NVIDIA Corp"},
        {"ticker": "TSLA/USDT", "name": "Tesla Inc"},
        {"ticker": "AAPL/USDT", "name": "Apple Inc"},
        {"ticker": "AMZN/USDT", "name": "Amazon.com"},
        {"ticker": "MSFT/USDT", "name": "Microsoft Corp"},
        {"ticker": "AMD/USDT", "name": "Advanced Micro Devices"},
        {"ticker": "META/USDT", "name": "Meta Platforms"},
        {"ticker": "GOOGL/USDT", "name": "Alphabet Inc"},
        {"ticker": "COIN/USDT", "name": "Coinbase Global"},
        {"ticker": "PLTR/USDT", "name": "Palantir Technologies"},
        {"ticker": "MSTR/USDT", "name": "MicroStrategy Inc"}
    ]

    COMMODITIES_MACRO_UNIVERSE = [
        {"ticker": "GOLD/USDT", "name": "Gold Spot (XAU)"},
        {"ticker": "SILVER/USDT", "name": "Silver Spot (XAG)"},
        {"ticker": "OIL/USDT", "name": "WTI Crude Oil"},
        {"ticker": "BRENT/USDT", "name": "Brent Crude Oil"},
        {"ticker": "NATGAS/USDT", "name": "Natural Gas"}
    ]

    @classmethod
    def get_full_hunting_universe(cls) -> list:
        universe = []
        # Add Crypto Universe
        for symbol in cls.DEFAULT_CRYPTO_UNIVERSE:
            universe.append({"ticker": symbol, "asset_class": "Crypto Futures", "base_price": cls._get_estimated_base_price(symbol)})
        # Add TradFi Universe
        for item in cls.TRADFI_STOCKS_UNIVERSE:
            universe.append({"ticker": item["ticker"], "asset_class": "Tokenized Equity", "base_price": cls._get_estimated_base_price(item["ticker"])})
        # Add Commodities Universe
        for item in cls.COMMODITIES_MACRO_UNIVERSE:
            universe.append({"ticker": item["ticker"], "asset_class": "Commodity", "base_price": cls._get_estimated_base_price(item["ticker"])})
        return universe

    @staticmethod
    def _get_estimated_base_price(ticker: str) -> float:
        prices = {
            "BTC/USDT": 65000.0, "ETH/USDT": 3400.0, "SOL/USDT": 150.0, "XRP/USDT": 0.60,
            "DOGE/USDT": 0.12, "AVAX/USDT": 28.0, "LINK/USDT": 14.0, "NEAR/USDT": 5.50,
            "SUI/USDT": 1.20, "PEPE/USDT": 0.00001, "WIF/USDT": 2.10, "NVDA/USDT": 125.0,
            "TSLA/USDT": 220.0, "AAPL/USDT": 225.0, "AMZN/USDT": 180.0, "MSFT/USDT": 440.0,
            "AMD/USDT": 150.0, "META/USDT": 500.0, "GOOGL/USDT": 175.0, "COIN/USDT": 220.0,
            "PLTR/USDT": 28.0, "MSTR/USDT": 1400.0, "GOLD/USDT": 2400.0, "SILVER/USDT": 28.50,
            "OIL/USDT": 78.0, "BRENT/USDT": 82.0, "NATGAS/USDT": 2.10
        }
        return prices.get(ticker, 100.0)
