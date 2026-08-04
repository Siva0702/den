# models/indicators/exchange_leverage.py

class ExchangeLeverageEngine:
    """
    Den Engine v35.0 Verified Binance Futures Leverage Matrix:
    All leverage limits calibrated to Binance Futures (same as Bitunix/Weex).
    """

    EQUITY_PAIRS = {
        "GS/USDT": 50, "NVDA/USDT": 50, "TSLA/USDT": 50, "AAPL/USDT": 50,
        "AMZN/USDT": 50, "MSFT/USDT": 50, "GOOGL/USDT": 50, "META/USDT": 50,
        "PLTR/USDT": 50, "NFLX/USDT": 50, "INTC/USDT": 50, "AMD/USDT": 50,
        "SMCI/USDT": 50, "COIN/USDT": 50, "MSTR/USDT": 50, "BABA/USDT": 50,
    }

    COMMODITY_PAIRS = {
        "XAU/USDT": 75, "XAG/USDT": 75, "COPPER/USDT": 50,
    }

    CRYPTO_PAIRS = {
        "BTC/USDT": 125, "ETH/USDT": 125, "SOL/USDT": 75, "XRP/USDT": 75,
        "DOGE/USDT": 75, "BNB/USDT": 75, "AVAX/USDT": 50, "LINK/USDT": 50,
        "NEAR/USDT": 50, "SUI/USDT": 50, "PEPE/USDT": 50, "WIF/USDT": 50,
        "FET/USDT": 50, "RENDER/USDT": 50, "INJ/USDT": 50, "TIA/USDT": 50,
        "ARB/USDT": 50, "OP/USDT": 50, "APT/USDT": 50, "SEI/USDT": 50,
        "TAO/USDT": 50, "PENDLE/USDT": 50, "RUNE/USDT": 50, "ADA/USDT": 50,
        "DOT/USDT": 50, "LTC/USDT": 50, "MATIC/USDT": 50, "STX/USDT": 50,
        "ORDI/USDT": 50,
    }

    @classmethod
    def get_calibrated_leverage(cls, ticker: str, ideal_leverage: int) -> dict:
        if ticker in cls.EQUITY_PAIRS:
            exchange_name = "Binance Futures (Equity)"
            max_allowed = cls.EQUITY_PAIRS[ticker]
        elif ticker in cls.COMMODITY_PAIRS:
            exchange_name = "Binance Futures (Commodity)"
            max_allowed = cls.COMMODITY_PAIRS[ticker]
        elif ticker in cls.CRYPTO_PAIRS:
            exchange_name = "Binance Futures (Crypto)"
            max_allowed = cls.CRYPTO_PAIRS[ticker]
        else:
            exchange_name = "Binance Futures"
            max_allowed = 50

        final_leverage = min(ideal_leverage, max_allowed)

        return {
            "recommended_leverage": final_leverage,
            "max_exchange_leverage": max_allowed,
            "primary_exchange": exchange_name
        }
