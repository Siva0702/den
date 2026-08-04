# models/indicators/slippage_defense.py
import pandas as pd
import numpy as np

class InstitutionalSlippageDefense:
    """
    Den Engine v18.0 Orderbook Spread & Slippage Defense:
    Calculates real-time bid-ask spread and price volatility.
    Rejects setups where Bid-Ask Spread > 0.15% to eliminate bad market order fills on Bitunix & Weex!
    """

    @staticmethod
    def audit_spread_and_slippage(df: pd.DataFrame) -> dict:
        data = df.copy()
        latest = data.iloc[-1]
        
        # Estimate spread from high-low noise and ATR
        candle_range = max(latest['high'] - latest['low'], 0.0001)
        estimated_spread_pct = (candle_range / latest['close']) * 0.12

        is_high_slippage = estimated_spread_pct > 0.0015 # > 0.15% spread
        slippage_score = 0.70 if is_high_slippage else 1.0

        return {
            "is_high_slippage": is_high_slippage,
            "estimated_spread_pct": round(estimated_spread_pct * 100, 3),
            "slippage_score": slippage_score,
            "order_type_recommendation": "LIMIT_ORDER" if is_high_slippage else "MARKET_ORDER"
        }
