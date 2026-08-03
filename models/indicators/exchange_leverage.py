# models/indicators/exchange_leverage.py

class ExchangeLeverageEngine:
    """
    Den Engine v17.2 Verified Exchange Pair & Leverage Matrix:
    - BAC/USDT: WEEX EXCLUSIVE (Max 100x Leverage, $62.33 Weex Price)
    - GS/USDT: BITUNIX EXCLUSIVE (Max 50x Leverage, $1,029.00 Bitunix Price)
    - Crypto (BTC, ETH, SOL, XRP, DOGE): BITUNIX & WEEX (Max 100x - 125x Leverage)
    - Bullion (XAU, XAG): BITUNIX & WEEX (Max 75x - 100x Leverage)
    """

    BITUNIX_EXCLUSIVE_PAIRS = {
        "GS/USDT": 50, "NVDA/USDT": 50, "TSLA/USDT": 50, "AAPL/USDT": 50,
        "AMZN/USDT": 50, "MSFT/USDT": 50, "GOOGL/USDT": 50, "META/USDT": 50, "PLTR/USDT": 50
    }

    WEEX_EXCLUSIVE_PAIRS = {
        "BAC/USDT": 100, "BABA/USDT": 100, "PDD/USDT": 100
    }

    DUAL_EXCHANGE_PAIRS = {
        "BTC/USDT": 125, "ETH/USDT": 125, "SOL/USDT": 100, "XRP/USDT": 100, "DOGE/USDT": 100,
        "AVAX/USDT": 75, "LINK/USDT": 75, "NEAR/USDT": 75, "SUI/USDT": 75, "PEPE/USDT": 75,
        "XAU/USDT": 100, "XAG/USDT": 75, "WTI/USDT": 50, "BRENT/USDT": 50, "NGAS/USDT": 50,
        "COIN/USDT": 100, "MSTR/USDT": 100
    }

    @classmethod
    def get_calibrated_leverage(cls, ticker: str, ideal_leverage: int) -> dict:
        if ticker in cls.WEEX_EXCLUSIVE_PAIRS:
            exchange_name = "Weex Exclusive"
            max_allowed = cls.WEEX_EXCLUSIVE_PAIRS[ticker]
        elif ticker in cls.BITUNIX_EXCLUSIVE_PAIRS:
            exchange_name = "Bitunix Exclusive"
            max_allowed = cls.BITUNIX_EXCLUSIVE_PAIRS[ticker]
        elif ticker in cls.DUAL_EXCHANGE_PAIRS:
            exchange_name = "Bitunix / Weex"
            max_allowed = cls.DUAL_EXCHANGE_PAIRS[ticker]
        else:
            exchange_name = "Bitunix"
            max_allowed = 50

        final_leverage = min(ideal_leverage, max_allowed)

        return {
            "recommended_leverage": final_leverage,
            "max_exchange_leverage": max_allowed,
            "primary_exchange": exchange_name
        }
