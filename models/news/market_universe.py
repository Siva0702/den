# models/news/market_universe.py
import requests

class DynamicMarketUniverse:
    """
    Den Engine v10.2 Global 90+ Asset Multi-Sector Universe:
    1. Top Crypto Derivatives (25 Pairs)
    2. Semiconductors & AI Chips (11 Stocks: NVDA, AMD, INTC, AVGO, QCOM, MU, ARM, SMCI, AMAT, LRCX, ASML)
    3. Big Tech & Software (12 Stocks: AAPL, MSFT, GOOGL, AMZN, META, TSLA, NFLX, ORCL, CRM, ADBE, PLTR, SNOW)
    4. Crypto Proxies & Fintech (8 Stocks: COIN, MSTR, HOOD, PYPL, SQ, MARA, RIOT, CLSK)
    5. Global & Asian Giants (6 Stocks: TSM, SSNLF/Samsung, BABA, PDD, BIDU, SONY)
    6. Financials & Defense (5 Stocks: JPM, GS, BAC, V, MA)
    7. Biotech & Energy (5 Stocks: LLY, NVO, PFE, XOM, CVX)
    8. Macro Benchmarks (5 Indices: SPY, QQQ, IWM, EWJ/Nikkei, EEM)
    9. Commodities (5 Markets: GOLD, SILVER, OIL, BRENT, NATGAS)
    Total = 82 Liquid High-Beta Assets!
    """

    CRYPTO_UNIVERSE = [
        "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "DOGE/USDT", 
        "AVAX/USDT", "LINK/USDT", "NEAR/USDT", "SUI/USDT", "PEPE/USDT", 
        "WIF/USDT", "FET/USDT", "RENDER/USDT", "INJ/USDT", "TIA/USDT", 
        "ARB/USDT", "OP/USDT", "APT/USDT", "SEI/USDT", "TAO/USDT",
        "BNB/USDT", "ADA/USDT", "DOT/USDT", "LTC/USDT", "MATIC/USDT"
    ]

    SEMIS_AND_AI_CHIPS = [
        {"ticker": "NVDA/USDT", "sector": "Semiconductors / AI"},
        {"ticker": "AMD/USDT", "sector": "Semiconductors"},
        {"ticker": "INTC/USDT", "sector": "Semiconductors / Chips"},
        {"ticker": "AVGO/USDT", "sector": "Networking / Chips"},
        {"ticker": "QCOM/USDT", "sector": "Mobile Chips"},
        {"ticker": "MU/USDT", "sector": "Memory Chips"},
        {"ticker": "ARM/USDT", "sector": "Chip Design / Architecture"},
        {"ticker": "SMCI/USDT", "sector": "AI Server Hardware"},
        {"ticker": "AMAT/USDT", "sector": "Semiconductor Equipment"},
        {"ticker": "LRCX/USDT", "sector": "Semiconductor Wafer Tech"},
        {"ticker": "ASML/USDT", "sector": "EUV Lithography Systems"}
    ]

    BIG_TECH_AND_SOFTWARE = [
        {"ticker": "AAPL/USDT", "sector": "Consumer Electronics"},
        {"ticker": "MSFT/USDT", "sector": "Enterprise Software / AI"},
        {"ticker": "GOOGL/USDT", "sector": "Search / Cloud / AI"},
        {"ticker": "AMZN/USDT", "sector": "E-Commerce / Cloud"},
        {"ticker": "META/USDT", "sector": "Social Media / AI"},
        {"ticker": "TSLA/USDT", "sector": "EV / Autonomous AI"},
        {"ticker": "NFLX/USDT", "sector": "Streaming Entertainment"},
        {"ticker": "ORCL/USDT", "sector": "Cloud Infrastructure"},
        {"ticker": "CRM/USDT", "sector": "Enterprise CRM / AI"},
        {"ticker": "ADBE/USDT", "sector": "Creative Software / AI"},
        {"ticker": "PLTR/USDT", "sector": "AI / Defense Software"},
        {"ticker": "SNOW/USDT", "sector": "Cloud Data Warehousing"}
    ]

    FINTECH_AND_CRYPTO_PROXIES = [
        {"ticker": "COIN/USDT", "sector": "Crypto Exchange"},
        {"ticker": "MSTR/USDT", "sector": "Bitcoin Treasury Proxy"},
        {"ticker": "HOOD/USDT", "sector": "Retail Brokerage"},
        {"ticker": "PYPL/USDT", "sector": "Digital Payments"},
        {"ticker": "SQ/USDT", "sector": "Block / Cash App"},
        {"ticker": "MARA/USDT", "sector": "Bitcoin Mining"},
        {"ticker": "RIOT/USDT", "sector": "Bitcoin Mining"},
        {"ticker": "CLSK/USDT", "sector": "Clean Mining Tech"}
    ]

    GLOBAL_AND_ASIAN_GIANTS = [
        {"ticker": "TSM/USDT", "sector": "Taiwan Semi Foundry"},
        {"ticker": "SSNLF/USDT", "sector": "Samsung Electronics"},
        {"ticker": "BABA/USDT", "sector": "China Tech / E-Commerce"},
        {"ticker": "PDD/USDT", "sector": "Temu / China Commerce"},
        {"ticker": "BIDU/USDT", "sector": "Baidu China AI"},
        {"ticker": "SONY/USDT", "sector": "Japan Gaming / Consumer"}
    ]

    FINANCIALS_AND_PAYMENTS = [
        {"ticker": "JPM/USDT", "sector": "Banking / Financials"},
        {"ticker": "GS/USDT", "sector": "Investment Banking"},
        {"ticker": "BAC/USDT", "sector": "Banking / Financials"},
        {"ticker": "V/USDT", "sector": "Global Payments"},
        {"ticker": "MA/USDT", "sector": "Global Payments"}
    ]

    BIOTECH_AND_ENERGY = [
        {"ticker": "LLY/USDT", "sector": "Pharma / Biotech"},
        {"ticker": "NVO/USDT", "sector": "Novo Nordisk Pharma"},
        {"ticker": "PFE/USDT", "sector": "Pfizer Biotech"},
        {"ticker": "XOM/USDT", "sector": "ExxonMobil Energy"},
        {"ticker": "CVX/USDT", "sector": "Chevron Energy"}
    ]

    MACRO_INDICES_BENCHMARKS = [
        {"ticker": "SPY/USDT", "sector": "S&P 500 US Benchmark"},
        {"ticker": "QQQ/USDT", "sector": "Nasdaq 100 Tech Benchmark"},
        {"ticker": "IWM/USDT", "sector": "Russell 2000 Small Cap Benchmark"},
        {"ticker": "EWJ/USDT", "sector": "Japan MSCI Nikkei 225 Benchmark"},
        {"ticker": "EEM/USDT", "sector": "Emerging Markets Benchmark"}
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
        # 1. Crypto (25)
        for symbol in cls.CRYPTO_UNIVERSE:
            universe.append({"ticker": symbol, "asset_class": "Crypto Futures", "sector": "Crypto", "base_price": cls._get_base_price(symbol)})
        # 2. Semis & AI (11)
        for item in cls.SEMIS_AND_AI_CHIPS:
            universe.append({"ticker": item["ticker"], "asset_class": "Tokenized Equity", "sector": item["sector"], "base_price": cls._get_base_price(item["ticker"])})
        # 3. Big Tech & Software (12)
        for item in cls.BIG_TECH_AND_SOFTWARE:
            universe.append({"ticker": item["ticker"], "asset_class": "Tokenized Equity", "sector": item["sector"], "base_price": cls._get_base_price(item["ticker"])})
        # 4. Crypto Proxies & Fintech (8)
        for item in cls.FINTECH_AND_CRYPTO_PROXIES:
            universe.append({"ticker": item["ticker"], "asset_class": "Tokenized Equity", "sector": item["sector"], "base_price": cls._get_base_price(item["ticker"])})
        # 5. Global Giants (6)
        for item in cls.GLOBAL_AND_ASIAN_GIANTS:
            universe.append({"ticker": item["ticker"], "asset_class": "Global Equity", "sector": item["sector"], "base_price": cls._get_base_price(item["ticker"])})
        # 6. Financials (5)
        for item in cls.FINANCIALS_AND_PAYMENTS:
            universe.append({"ticker": item["ticker"], "asset_class": "Tokenized Equity", "sector": item["sector"], "base_price": cls._get_base_price(item["ticker"])})
        # 7. Biotech & Energy (5)
        for item in cls.BIOTECH_AND_ENERGY:
            universe.append({"ticker": item["ticker"], "asset_class": "Tokenized Equity", "sector": item["sector"], "base_price": cls._get_base_price(item["ticker"])})
        # 8. Macro Indices (5)
        for item in cls.MACRO_INDICES_BENCHMARKS:
            universe.append({"ticker": item["ticker"], "asset_class": "Macro Benchmark", "sector": item["sector"], "base_price": cls._get_base_price(item["ticker"])})
        # 9. Commodities (5)
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
            "MU/USDT": 110.0, "ARM/USDT": 140.0, "SMCI/USDT": 550.0, "NFLX/USDT": 650.0,
            "ORCL/USDT": 135.0, "HOOD/USDT": 22.0, "PYPL/USDT": 65.0, "SQ/USDT": 68.0,
            "MARA/USDT": 18.0, "RIOT/USDT": 10.0, "PDD/USDT": 130.0, "NVO/USDT": 130.0
        }
        return prices.get(ticker, 100.0)
