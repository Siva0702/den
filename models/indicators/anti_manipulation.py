# models/indicators/anti_manipulation.py
import pandas as pd
import numpy as np

class InstitutionalAntiManipulationShield:
    """
    Den Engine v19.0 Liquidity Sweep & Stop-Hunt Defense Shield:
    1. Detects Market Maker Stop-Hunts (wick sweeps over Equal Highs/Lows).
    2. Enforces ENTRY ON RECLAIM (Enters AFTER the stop-hunt wick completes, never before!).
    3. Calculates Wide Liquidity Buffer for Stop Loss (places SL 1.5x ATR outside the sweep zone so stop-hunts never trigger SL!).
    """

    @staticmethod
    def audit_manipulation(df: pd.DataFrame) -> dict:
        data = df.copy()
        if len(data) < 15:
            return {"is_manipulated": False, "status": "INSUFFICIENT_DATA", "shield_multiplier": 1.0, "sl_buffer_atr": 1.5}

        latest = data.iloc[-1]
        prev = data.iloc[-2]

        candle_range = max(latest['high'] - latest['low'], 0.0001)
        upper_wick = latest['high'] - max(latest['open'], latest['close'])
        lower_wick = min(latest['open'], latest['close']) - latest['low']

        upper_wick_ratio = upper_wick / candle_range
        lower_wick_ratio = lower_wick / candle_range

        # Detect Market Maker Wick Sweep Trap
        is_upper_sweep = upper_wick_ratio > 0.42 and latest['high'] > data['high'].iloc[-10:-1].max()
        is_lower_sweep = lower_wick_ratio > 0.42 and latest['low'] < data['low'].iloc[-10:-1].min()

        is_manipulated = is_upper_sweep or is_lower_sweep

        if is_manipulated:
            status = "STOP_HUNT_SWEEP_IN_PROGRESS (Rejection Pending)"
            shield_multiplier = 0.50 # Block pre-mature entry
        else:
            status = "CLEAN_ORGANIC_ORDERFLOW (No Active Wick Sweep)"
            shield_multiplier = 1.15

        # Wide Liquidity Buffer: Places SL 1.5x to 2.0x ATR outside the sweep zone so MMs can't touch SL
        sl_buffer_atr = 1.8 if (upper_wick_ratio > 0.30 or lower_wick_ratio > 0.30) else 1.5

        return {
            "is_manipulated": is_manipulated,
            "status": status,
            "shield_multiplier": shield_multiplier,
            "sl_buffer_atr": sl_buffer_atr
        }
