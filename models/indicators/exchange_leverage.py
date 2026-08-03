# models/indicators/exchange_leverage.py

class ExchangeLeverageEngine:
    """
    Den Engine v16.1 Exchange Leverage & Pair Compatibility Matrix for Bitunix & Weex:
    Includes Tokenized Commodities: XAU/USDT (Gold) & XAG/USDT (Silver)
    - XAU/USDT (Gold Bullion): Max 100x Leverage
    - XAG/USDT (Silver Bullion): Max 75x Leverage
    - WTI/USDT & BRENT/USDT (Crude Oil): Max 50x Leverage
    """

    BITUNIX_MAX_LEVERAGE = {
        "BTC/USDT": 125, "ETH/USDT": 125, "SOL/USDT": 100, "XRP/USDT": 100, "DOGE/USDT": 100,
        "AVAX/USDT": 75, "LINK/USDT": 75, "NEAR/USDT": 75, "SUI/USDT": 75, "PEPE/USDT": 75,
        "XAU/USDT": 100, "XAG/USDT": 75, "GOLD/USDT": 100, "SILVER/USDT": 75, "OIL/USDT": 50,
        "WTI/USDT": 50, "BRENT/USDT": 50, "NGAS/USDT": 50,
        "GS/USDT": 50, "NVDA/USDT": 50, "TSLA/USDT": 50, "AAPL/USDT": 50, "AMZN/USDT": 50
    }

    WEEX_MAX_LEVERAGE = {
        "XAU/USDT": 100, "XAG/USDT": 100, "BAC/USDT": 100, "COIN/USDT": 100, "MSTR/USDT": 100, 
        "BABA/USDT": 100, "TSM/USDT": 100, "SSNLF/USDT": 50, "HOOD/USDT": 50
    }

    @classmethod
    def get_calibrated_leverage(cls, ticker: str, ideal_leverage: int) -> dict:
        exchange_name = "Bitunix / Weex"
        max_allowed = 50

        if ticker in cls.BITUNIX_MAX_LEVERAGE:
            exchange_name = "Bitunix"
            max_allowed = cls.BITUNIX_MAX_LEVERAGE[ticker]
        elif ticker in cls.WEEX_MAX_LEVERAGE:
            exchange_name = "Weex / Bitunix"
            max_allowed = cls.WEEX_MAX_LEVERAGE[ticker]
        else:
            max_allowed = 50

        final_leverage = min(ideal_leverage, max_allowed)

        return {
            "recommended_leverage": final_leverage,
            "max_exchange_leverage": max_allowed,
            "primary_exchange": exchange_name
        }
