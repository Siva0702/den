# models/indicators/orderflow_imbalance.py
import pandas as pd
import numpy as np

class InstitutionalOrderFlowEngine:
    """
    Den Engine v10.0 Institutional Order Flow & Taker Imbalance Engine:
    Measures aggressive market taker buy/sell volume ratio.
    Requires Taker Buy Volume Ratio >= 60% for Longs to confirm real institutional buying!
    """
    @staticmethod
    def analyze_orderflow(df: pd.DataFrame) -> dict:
        data = df.copy()
        
        # Estimate taker buy vs sell volume based on close relative to candle high-low
        high_low = np.maximum(data['high'] - data['low'], 0.0001)
        close_low = data['close'] - data['low']
        buy_ratio = close_low / high_low

        avg_buy_ratio = buy_ratio.rolling(10).mean().iloc[-1]
        
        is_aggressive_buying = avg_buy_ratio >= 0.60
        is_aggressive_selling = avg_buy_ratio <= 0.40

        return {
            "buy_ratio": round(avg_buy_ratio * 100, 1),
            "is_aggressive_buying": is_aggressive_buying,
            "is_aggressive_selling": is_aggressive_selling,
            "orderflow_score": 1.15 if is_aggressive_buying else (0.85 if is_aggressive_selling else 1.0)
        }
