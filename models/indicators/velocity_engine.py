# models/indicators/velocity_engine.py
import pandas as pd
import numpy as np

class MomentumVelocityEngine:
    """
    Den Engine v9.0 Fast Expansion & Momentum Velocity Index (MVI):
    Filters out sideways/dead consolidation chop.
    Only allows setups when price momentum velocity is actively accelerating!
    """
    @staticmethod
    def calculate_velocity(df: pd.DataFrame) -> dict:
        data = df.copy()
        
        # 1. Candle Body Velocity vs ATR
        body_size = np.abs(data['close'] - data['open'])
        high_low = data['high'] - data['low']
        atr_14 = high_low.rolling(14).mean()

        latest_body = body_size.iloc[-1]
        latest_atr = max(atr_14.iloc[-1], 0.0001)
        velocity_ratio = latest_body / latest_atr

        # 2. Bollinger Band Expansion Check (Squeeze Breakout)
        bb_middle = data['close'].rolling(20).mean()
        bb_std = data['close'].rolling(20).std()
        bb_width = (2.0 * bb_std) / bb_middle
        
        bb_width_mean = bb_width.rolling(50).mean().iloc[-1]
        is_exploding = bb_width.iloc[-1] > (bb_width_mean * 1.15)
        is_dead_chop = bb_width.iloc[-1] < (bb_width_mean * 0.65) and velocity_ratio < 0.8

        return {
            "velocity_ratio": round(velocity_ratio, 2),
            "is_exploding": is_exploding,
            "is_dead_chop": is_dead_chop,
            "momentum_score": round(velocity_ratio * (1.2 if is_exploding else 0.8), 2)
        }
