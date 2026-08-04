# models/indicators/adaptive_threshold.py

class AdaptiveDynamicThresholdEngine:
    """
    Den Engine v20.0 Quantum Adaptive Velocity & Micro-Breakout Engine:
    Dynamically adjusts the win-rate gating threshold based on institutional orderflow and market regime:
    - High Taker Buy/Sell Influx (>= 65%) + Clean Shield -> Lowers Gate to 65.0% - 68.0% (Captures Fast Scalps!)
    - Standard Market Orderflow -> Sets Gate to 70.0%
    - Weak Orderflow (< 55%) -> Raises Gate to 75.0% (Defends Capital against Chop!)
    """

    @staticmethod
    def calculate_dynamic_gate(
        taker_buy_ratio: float, 
        is_clean_shield: bool, 
        volatility_expansion: float
    ) -> float:
        # Default Baseline Gate
        dynamic_gate = 0.70 # 70.0%

        # 1. High Institutional Taker Surge (Micro-Breakout Trigger)
        if is_clean_shield and (taker_buy_ratio >= 65.0 or taker_buy_ratio <= 35.0) and volatility_expansion >= 1.10:
            dynamic_gate = 0.65 # 65.0% Gate for Fast Momentum Scalps!
        elif is_clean_shield and (taker_buy_ratio >= 60.0 or taker_buy_ratio <= 40.0):
            dynamic_gate = 0.68 # 68.0% Gate
        elif taker_buy_ratio < 55.0 and taker_buy_ratio > 45.0:
            dynamic_gate = 0.75 # 75.0% Strict Gate for Low-Volume Consolidation

        return dynamic_gate
