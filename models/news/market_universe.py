# models/news/market_universe.py

class DynamicMarketUniverse:
    """
    Den Engine v38.0 Multi-Sector Global Market Universe (Bitunix & WEEX Futures):
    - Crypto Futures (35 Core Verified Crypto Assets)
    - Major Market Benchmarks & Index Proxies (SPY/USDT, QQQ/USDT, IWM/USDT)
    - Big Tech & AI Leaders (NVDA, PLTR, TSLA, AAPL, MSFT, GOOGL, AMZN, META, AMD, INTC, SMCI, NFLX, CRM, ORCL, CSCO, IBM, UBER, ABNB, PANW, SNOW)
    - EV & Mobility (RIVN, NIO)
    - Financials & Fintech (JPM, V, MA, BAC, PYPL, HOOD, COIN, MSTR, BABA, GS, MARA, CLSK)
    - Defense & Aerospace (LMT, BA)
    - Healthcare & BioPharma (LLY, UNH, JNJ)
    - Energy & Bullion Commodities (XAU Gold, XAG Silver, COPPER, XLE Energy, CL Crude Oil)
    - Consumer & Retail Leaders (WMT, COST, DIS, NKE, MCD)
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

    MARKET_BENCHMARKS = [
        {"ticker": "SPY/USDT", "sector": "S&P 500 Index Proxy", "exchange": "Bitunix / WEEX", "base_price": 761.26},
        {"ticker": "QQQ/USDT", "sector": "Nasdaq-100 Index Proxy", "exchange": "Bitunix / WEEX", "base_price": 520.15},
        {"ticker": "IWM/USDT", "sector": "Russell 2000 Small Cap Proxy", "exchange": "Bitunix / WEEX", "base_price": 220.40},
    ]

    BIG_TECH_AI = [
        {"ticker": "PLTR/USDT", "sector": "AI / Defense Tech", "exchange": "Bitunix / WEEX", "base_price": 163.02},
        {"ticker": "NVDA/USDT", "sector": "Semiconductors / AI", "exchange": "Bitunix / WEEX", "base_price": 213.17},
        {"ticker": "TSLA/USDT", "sector": "EV / Autonomous AI", "exchange": "Bitunix / WEEX", "base_price": 329.15},
        {"ticker": "INTC/USDT", "sector": "Semiconductors", "exchange": "Bitunix / WEEX", "base_price": 101.38},
        {"ticker": "SMCI/USDT", "sector": "AI Infrastructure", "exchange": "Bitunix / WEEX", "base_price": 31.65},
        {"ticker": "AMD/USDT", "sector": "Semiconductors", "exchange": "Bitunix / WEEX", "base_price": 529.89},
        {"ticker": "AAPL/USDT", "sector": "Consumer Tech", "exchange": "Bitunix / WEEX", "base_price": 308.98},
        {"ticker": "MSFT/USDT", "sector": "Software / AI", "exchange": "Bitunix / WEEX", "base_price": 496.68},
        {"ticker": "GOOGL/USDT", "sector": "Search / Cloud", "exchange": "Bitunix / WEEX", "base_price": 380.18},
        {"ticker": "AMZN/USDT", "sector": "Cloud / E-Commerce", "exchange": "Bitunix / WEEX", "base_price": 277.29},
        {"ticker": "META/USDT", "sector": "Social Media / AI", "exchange": "Bitunix / WEEX", "base_price": 589.48},
        {"ticker": "NFLX/USDT", "sector": "Streaming Entertainment", "exchange": "Bitunix / WEEX", "base_price": 72.55},
        {"ticker": "CRM/USDT", "sector": "Enterprise Cloud Software", "exchange": "Bitunix / WEEX", "base_price": 280.50},
        {"ticker": "ORCL/USDT", "sector": "Cloud & Database Tech", "exchange": "Bitunix / WEEX", "base_price": 140.20},
        {"ticker": "CSCO/USDT", "sector": "Networking & Security", "exchange": "Bitunix / WEEX", "base_price": 55.40},
        {"ticker": "IBM/USDT", "sector": "Enterprise AI & Hybrid Cloud", "exchange": "Bitunix / WEEX", "base_price": 215.10},
        {"ticker": "UBER/USDT", "sector": "Mobility & Delivery Tech", "exchange": "Bitunix / WEEX", "base_price": 78.90},
        {"ticker": "ABNB/USDT", "sector": "Travel Tech Platforms", "exchange": "Bitunix / WEEX", "base_price": 135.60},
        {"ticker": "PANW/USDT", "sector": "Cybersecurity Leaders", "exchange": "Bitunix / WEEX", "base_price": 350.20},
        {"ticker": "SNOW/USDT", "sector": "Cloud Data Warehousing", "exchange": "Bitunix / WEEX", "base_price": 155.80},
    ]

    EV_MOBILITY = [
        {"ticker": "RIVN/USDT", "sector": "EV Trucks & Fleet", "exchange": "Bitunix / WEEX", "base_price": 14.50},
        {"ticker": "NIO/USDT", "sector": "China Premium EV", "exchange": "Bitunix / WEEX", "base_price": 4.80},
    ]

    FINANCIALS_FINTECH = [
        {"ticker": "JPM/USDT", "sector": "Global Banking", "exchange": "Bitunix / WEEX", "base_price": 235.40},
        {"ticker": "V/USDT", "sector": "Global Payments Network", "exchange": "Bitunix / WEEX", "base_price": 305.20},
        {"ticker": "MA/USDT", "sector": "Global Payments Network", "exchange": "Bitunix / WEEX", "base_price": 510.80},
        {"ticker": "BAC/USDT", "sector": "Commercial Banking", "exchange": "Bitunix / WEEX", "base_price": 42.10},
        {"ticker": "PYPL/USDT", "sector": "Digital Payments", "exchange": "Bitunix / WEEX", "base_price": 85.30},
        {"ticker": "HOOD/USDT", "sector": "Retail Trading Brokerage", "exchange": "Bitunix / WEEX", "base_price": 35.60},
        {"ticker": "COIN/USDT", "sector": "Crypto Exchange Leader", "exchange": "Bitunix / WEEX", "base_price": 152.06},
        {"ticker": "MSTR/USDT", "sector": "Bitcoin Treasury Proxy", "exchange": "Bitunix / WEEX", "base_price": 97.68},
        {"ticker": "BABA/USDT", "sector": "China E-Commerce Tech", "exchange": "Bitunix / WEEX", "base_price": 129.12},
        {"ticker": "GS/USDT", "sector": "Investment Banking", "exchange": "Bitunix / WEEX", "base_price": 1060.37},
        {"ticker": "MARA/USDT", "sector": "Bitcoin Mining Operations", "exchange": "Bitunix / WEEX", "base_price": 18.40},
        {"ticker": "CLSK/USDT", "sector": "Clean Energy Bitcoin Mining", "exchange": "Bitunix / WEEX", "base_price": 12.80},
    ]

    DEFENSE_AEROSPACE = [
        {"ticker": "LMT/USDT", "sector": "Defense & Aerospace", "exchange": "Bitunix / WEEX", "base_price": 540.20},
        {"ticker": "BA/USDT", "sector": "Commercial & Military Aircraft", "exchange": "Bitunix / WEEX", "base_price": 175.60},
    ]

    HEALTHCARE_PHARMA = [
        {"ticker": "LLY/USDT", "sector": "Pharma & GLP-1 Leader", "exchange": "Bitunix / WEEX", "base_price": 850.40},
        {"ticker": "UNH/USDT", "sector": "Healthcare Insurance & Services", "exchange": "Bitunix / WEEX", "base_price": 580.90},
        {"ticker": "JNJ/USDT", "sector": "Healthcare & Medical Tech", "exchange": "Bitunix / WEEX", "base_price": 162.30},
    ]

    ENERGY_COMMODITIES = [
        {"ticker": "XAU/USDT", "sector": "Gold Bullion Perpetual", "exchange": "Bitunix / WEEX", "base_price": 4087.98},
        {"ticker": "XAG/USDT", "sector": "Silver Bullion Perpetual", "exchange": "Bitunix / WEEX", "base_price": 59.73},
        {"ticker": "COPPER/USDT", "sector": "Industrial Metals", "exchange": "Bitunix / WEEX", "base_price": 6.64},
        {"ticker": "XLE/USDT", "sector": "Energy Sector ETF", "exchange": "Bitunix / WEEX", "base_price": 92.40},
        {"ticker": "CL/USDT", "sector": "Crude Oil Futures", "exchange": "Bitunix / WEEX", "base_price": 75.80},
    ]

    CONSUMER_RETAIL = [
        {"ticker": "WMT/USDT", "sector": "Retail Superstores", "exchange": "Bitunix / WEEX", "base_price": 88.50},
        {"ticker": "COST/USDT", "sector": "Consumer Wholesale Clubs", "exchange": "Bitunix / WEEX", "base_price": 915.20},
        {"ticker": "DIS/USDT", "sector": "Entertainment & Streaming", "exchange": "Bitunix / WEEX", "base_price": 98.40},
        {"ticker": "NKE/USDT", "sector": "Athletic Footwear & Apparel", "exchange": "Bitunix / WEEX", "base_price": 78.20},
        {"ticker": "MCD/USDT", "sector": "Global Fast Casual Dining", "exchange": "Bitunix / WEEX", "base_price": 295.60},
    ]

    @classmethod
    def get_full_hunting_universe(cls) -> list:
        universe = []
        for symbol in cls.CRYPTO_UNIVERSE:
            universe.append({
                "ticker": symbol,
                "asset_class": "Crypto Futures",
                "sector": "Crypto",
                "exchange": "Bitunix / WEEX",
                "base_price": cls._get_base_price(symbol)
            })
        
        all_traditional_groups = [
            (cls.MARKET_BENCHMARKS, "Index Benchmark"),
            (cls.BIG_TECH_AI, "Big Tech / AI"),
            (cls.EV_MOBILITY, "EV / Mobility"),
            (cls.FINANCIALS_FINTECH, "Financials / Fintech"),
            (cls.DEFENSE_AEROSPACE, "Defense / Aerospace"),
            (cls.HEALTHCARE_PHARMA, "Healthcare / BioPharma"),
            (cls.ENERGY_COMMODITIES, "Energy / Commodities"),
            (cls.CONSUMER_RETAIL, "Consumer / Retail")
        ]

        for group, asset_class in all_traditional_groups:
            for item in group:
                universe.append({
                    "ticker": item["ticker"],
                    "asset_class": asset_class,
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
            "ORDI/USDT": 3.409, "NOT/USDT": 0.015, "TON/USDT": 6.2, "JUP/USDT": 0.85,
            "W/USDT": 0.60, "ENA/USDT": 0.70, "BONK/USDT": 0.000025,
            "SPY/USDT": 761.26, "QQQ/USDT": 520.15, "IWM/USDT": 220.40,
            "PLTR/USDT": 163.02, "NVDA/USDT": 213.17, "TSLA/USDT": 329.15, "INTC/USDT": 101.38,
            "SMCI/USDT": 31.65, "AMD/USDT": 529.89, "AAPL/USDT": 308.98, "MSFT/USDT": 496.68,
            "GOOGL/USDT": 380.18, "AMZN/USDT": 277.29, "META/USDT": 589.48, "NFLX/USDT": 72.55,
            "CRM/USDT": 280.50, "ORCL/USDT": 140.20, "CSCO/USDT": 55.40, "IBM/USDT": 215.10,
            "UBER/USDT": 78.90, "ABNB/USDT": 135.60, "PANW/USDT": 350.20, "SNOW/USDT": 155.80,
            "RIVN/USDT": 14.50, "NIO/USDT": 4.80,
            "JPM/USDT": 235.40, "V/USDT": 305.20, "MA/USDT": 510.80, "BAC/USDT": 42.10,
            "PYPL/USDT": 85.30, "HOOD/USDT": 35.60, "COIN/USDT": 152.06, "MSTR/USDT": 97.68,
            "BABA/USDT": 129.12, "GS/USDT": 1060.37, "MARA/USDT": 18.40, "CLSK/USDT": 12.80,
            "LMT/USDT": 540.20, "BA/USDT": 175.60,
            "LLY/USDT": 850.40, "UNH/USDT": 580.90, "JNJ/USDT": 162.30,
            "XAU/USDT": 4087.98, "XAG/USDT": 59.73, "COPPER/USDT": 6.64, "XLE/USDT": 92.40, "CL/USDT": 75.80,
            "WMT/USDT": 88.50, "COST/USDT": 915.20, "DIS/USDT": 98.40, "NKE/USDT": 78.20, "MCD/USDT": 295.60
        }
        return prices.get(ticker, 100.0)
