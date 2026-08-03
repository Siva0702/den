# models/news/market_universe.py
import requests

class DynamicMarketUniverse:
    """
    Den Engine v4.0 Sector-Diversified Global Hunting Universe:
    1. Top Crypto Derivatives (BTC, ETH, SOL, XRP, DOGE, AVAX, LINK, NEAR, SUI, PEPE, WIF, FET, RENDER, INJ, TIA, ARB, OP, APT, SEI, TAO, BNB, ADA, DOT, LTC, MATIC)
    2. US Tech & Semiconductors (NVDA, TSLA, AAPL, AMZN, MSFT, AMD, META, GOOGL, INTC, AVGO, QCOM)
    3. Global & Asian Equities (TSM, SSNLF/Samsung, BABA, SONY)
    4. Macro Benchmarks & Global Indices (SPY, QQQ, IWM, EWJ/Japan Nikkei 225, EEM)
    5. Financials, Energy, Biotech & Crypto Stocks (JPM, XOM, LLY, COIN, PLTR, MSTR)
    6. Commodities & Macro FX (GOLD, SILVER, OIL, BRENT, NATGAS)
    """

    CRYPTO_UNIVERSE = [
        "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "DOGE/USDT", 
        "AVAX/USDT", "LINK/USDT", "NEAR/USDT", "SUI/USDT", "PEPE/USDT", 
        "WIF/USDT", "FET/USDT", "RENDER/USDT", "INJ/USDT", "TIA/USDT", 
        "ARB/USDT", "OP/USDT", "APT/USDT", "SEI/USDT", "TAO/USDT",
        "BNB/USDT", "ADA/USDT", "DOT/USDT", "LTC/USDT", "MATIC/USDT"
    ]

    TECH_SEMIS_STOCKS = [
        {"ticker": "NVDA/USDT", "sector": "Semiconductors / AI"},
        {"ticker": "TSLA/USDT", "sector": "EV / Tech"},
        {"ticker": "AAPL/USDT", "sector": "Consumer Electronics"},
        {"ticker": "AMZN/USDT", "sector": "E-Commerce / Cloud"},
        {"ticker": "MSFT/USDT", "sector": "Enterprise Software / AI"},
        {"ticker": "AMD/USDT", "sector": "Semiconductors"},
        {"ticker": "INTC/USDT", "sector": "Semiconductors / Chips"},
        {"ticker": "AVGO/USDT", "sector": "Networking / Chips"},
        {"ticker": "QCOM/USDT", "sector": "Mobile Chips"},
        {"ticker": "META/USDT", "sector": "Social Media / AI"},
        {"ticker": "GOOGL/USDT", "sector": "Search / Cloud"}
    ]

    GLOBAL_ASIAN_EQUITIES = [
        {"ticker": "TSM/USDT", "sector": "Taiwan Semi Foundry"},
        {"ticker": "SSNLF/USDT", "sector": "Samsung Electronics"},
        {"ticker": "BABA/USDT", "sector": "China Tech / E-Commerce"},
        {"ticker": "SONY/USDT", "sector": "Japan Electronics / Gaming"}
    ]

    MACRO_INDICES_BENCHMARKS = [
        {"ticker": "SPY/USDT", "sector": "S&P 500 US Benchmark"},
        {"ticker": "QQQ/USDT", "sector": "Nasdaq 100 Tech Benchmark"},
        {"ticker": "IWM/USDT", "sector": "Russell 2000 Small Cap Benchmark"},
        {"ticker": "EWJ/USDT", "sector": "Japan MSCI Nikkei 225 Benchmark"},
        {"ticker": "EEM/USDT", "sector": "Emerging Markets Benchmark"}
    ]

    FINANCIALS_ENERGY_BIOTECH = [
        {"ticker": "JPM/USDT", "sector": "Banking / Financials"},
        {"ticker": "XOM/USDT", "sector": "Energy / Oil"},
        {"ticker": "LLY/USDT", "sector": "Pharma / Biotech"},
        {"ticker": "COIN/USDT", "sector": "Crypto Exchange"},
        {"ticker": "PLTR/USDT", "sector": "AI / Defense Software"},
        {"ticker": "MSTR/USDT", "sector": "Bitcoin Proxy / Tech"}
    ]

    COMMODITIES_MACRO_FX = [
        {"ticker": "GOLD/USDT", "sector": "Precious Metals"},
        {"ticker": "SILVER/USDT", "sector": "Precious Metals"},
        {"ticker": "OIL/USDT", "sector": "WTI Crude Oil"},
        {"ticker": "BRENT/USDT", "sector": "Brent Crude Oil"},
        {"ticker": "NATGAS/USDT", "sector": "Natural Gas"}
    ]

    @classmethod
    def get_full_hunting_universe(cls) -> list:
        universe = []
        # 1. Crypto
        for symbol in cls.CRYPTO_UNIVERSE:
            universe.append({"ticker": symbol, "asset_class": "Crypto Futures", "sector": "Crypto", "base_price": cls._get_base_price(symbol)})
        # 2. Tech & Semis
        for item in cls.TECH_SEMIS_STOCKS:
            universe.append({"ticker": item["ticker"], "asset_class": "Tokenized Equity", "sector": item["sector"], "base_price": cls._get_base_price(item["ticker"])})
        # 3. Global & Asian
        for item in cls.GLOBAL_ASIAN_EQUITIES:
            universe.append({"ticker": item["ticker"], "asset_class": "Global Equity", "sector": item["sector"], "base_price": cls._get_base_price(item["ticker"])})
        # 4. Macro Benchmarks (SPY, QQQ, Nikkei/EWJ)
        for item in cls.MACRO_INDICES_BENCHMARKS:
            universe.append({"ticker": item["ticker"], "asset_class": "Macro Benchmark", "sector": item["sector"], "base_price": cls._get_base_price(item["ticker"])})
        # 5. Financials & Energy
        for item in cls.FINANCIALS_ENERGY_BIOTECH:
            universe.append({"ticker": item["ticker"], "asset_class": "Tokenized Equity", "sector": item["sector"], "base_price": cls._get_base_price(item["ticker"])})
        # 6. Commodities
        for item in cls.COMMODITIES_MACRO_FX:
            universe.append({"ticker": item["ticker"], "asset_class": "Commodity", "sector": item["sector"], "base_price": cls._get_base_price(item["ticker"])})
        return universe

    @staticmethod
    def _get_base_price(ticker: str) -> float:
        prices = {
            "BTC/USDT": 63900.0, "ETH/USDT": 1870.0, "SOL/USDT": 74.0, "XRP/USDT": 1.08,
            "DOGE/USDT": 0.12, "AVAX/USDT": 28.0, "LINK/USDT": 14.0, "NEAR/USDT": 5.50,
            "SUI/USDT": 1.20, "PEPE/USDT": 0.00001, "WIF/USDT": 2.10, "NVDA/USDT": 125.0,
            "TSLA/USDT": 220.0, "AAPL/USDT": 225.0, "AMZN/USDT": 180.0, "MSFT/USDT": 440.0,
            "AMD/USDT": 150.0, "INTC/USDT": 30.0, "AVGO/USDT": 160.0, "QCOM/USDT": 170.0,
            "META/USDT": 500.0, "GOOGL/USDT": 175.0, "TSM/USDT": 165.0, "SSNLF/USDT": 55.0,
            "BABA/USDT": 80.0, "SONY/USDT": 85.0, "SPY/USDT": 540.0, "QQQ/USDT": 470.0,
            "IWM/USDT": 210.0, "EWJ/USDT": 68.0, "EEM/USDT": 42.0, "JPM/USDT": 200.0,
            "XOM/USDT": 115.0, "LLY/USDT": 800.0, "COIN/USDT": 220.0, "PLTR/USDT": 28.0,
            "MSTR/USDT": 1400.0, "GOLD/USDT": 2400.0, "SILVER/USDT": 28.50, "OIL/USDT": 78.0,
            "BRENT/USDT": 82.0, "NATGAS/USDT": 2.10
        }
        return prices.get(ticker, 100.0)
