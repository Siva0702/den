# models/indicators/exchange_leverage.py

class ExchangeLeverageEngine:
    """
    Den Engine v16.0 Exchange Leverage & Pair Compatibility Matrix for Bitunix & Weex:
    Calculates exact maximum allowable leverage for Bitunix and Weex exchanges:
    - Bitunix Crypto: 100x - 125x
    - Bitunix Tokenized Equities (e.g. GS, NVDA, TSLA, AAPL): 20x - 50x (GS max = 50x)
    - Weex Tokenized Equities & Stocks (e.g. BAC, COIN, MSTR, BABA): 50x - 100x (BAC max = 100x)
    """

    # Bitunix Max Leverage Mapping
    BITUNIX_MAX_LEVERAGE = {
        "BTC/USDT": 125, "ETH/USDT": 125, "SOL/USDT": 100, "XRP/USDT": 100, "DOGE/USDT": 100,
        "AVAX/USDT": 75, "LINK/USDT": 75, "NEAR/USDT": 75, "SUI/USDT": 75, "PEPE/USDT": 75,
        "GS/USDT": 50, "NVDA/USDT": 50, "TSLA/USDT": 50, "AAPL/USDT": 50, "AMZN/USDT": 50,
        "MSFT/USDT": 50, "GOOGL/USDT": 50, "GOLD/USDT": 50, "OIL/USDT": 50
    }

    # Weex Max Leverage Mapping
    WEEX_MAX_LEVERAGE = {
        "BAC/USDT": 100, "COIN/USDT": 100, "MSTR/USDT": 100, "BABA/USDT": 100,
        "TSM/USDT": 100, "SSNLF/USDT": 50, "HOOD/USDT": 50, "PLTR/USDT": 50
    }

    @classmethod
    def get_calibrated_leverage(cls, ticker: str, ideal_leverage: int) -> dict:
        exchange_name = "Bitunix"
        max_allowed = 50 # Default safe fallback for equities

        if ticker in cls.BITUNIX_MAX_LEVERAGE:
            exchange_name = "Bitunix"
            max_allowed = cls.BITUNIX_MAX_LEVERAGE[ticker]
        elif ticker in cls.WEEX_MAX_LEVERAGE:
            exchange_name = "Weex / Bitunix"
            max_allowed = cls.WEEX_MAX_LEVERAGE[ticker]
        elif "USDT" in ticker and not any(x in ticker for x in ["/USDT"]):
            max_allowed = 50
        else:
            # Crypto pairs default to 75x - 100x
            max_allowed = 100 if "BTC" in ticker or "ETH" in ticker else 75

        final_leverage = min(ideal_leverage, max_allowed)

        return {
            "recommended_leverage": final_leverage,
            "max_exchange_leverage": max_allowed,
            "primary_exchange": exchange_name
        }
