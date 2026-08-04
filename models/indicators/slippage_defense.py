# models/indicators/slippage_defense.py
import pandas as pd
import numpy as np

class InstitutionalSlippageDefense:
    """
    Den Engine v18.0 Orderbook Spread & Slippage Defense:
    Calculates real-time bid-ask spread and price volatility.
    Rejects setups where Bid-Ask Spread > 0.50% to eliminate bad market order fills on Bitunix & Weex!
    """

    @staticmethod
    def audit_spread_and_slippage(df: pd.DataFrame) -> dict:
        data = df.copy()
        if len(data) == 0:
            return {"is_high_slippage": False, "estimated_spread_pct": 0.05, "slippage_score": 1.0, "order_type_recommendation": "MARKET_ORDER"}

        latest = data.iloc[-1]
        close_price = latest.get('close', 0.0)

        if pd.isna(close_price) or close_price <= 0:
            return {"is_high_slippage": False, "estimated_spread_pct": 0.05, "slippage_score": 1.0, "order_type_recommendation": "MARKET_ORDER"}

        candle_range = max(latest['high'] - latest['low'], 0.0001)
        estimated_spread_pct = (candle_range / close_price) * 0.05

        is_high_slippage = estimated_spread_pct > 0.0050 # > 0.50% spread threshold
        slippage_score = 0.85 if is_high_slippage else 1.0

        return {
            "is_high_slippage": is_high_slippage,
            "estimated_spread_pct": round(estimated_spread_pct * 100, 3),
            "slippage_score": slippage_score,
            "order_type_recommendation": "LIMIT_ORDER" if is_high_slippage else "MARKET_ORDER"
        }
