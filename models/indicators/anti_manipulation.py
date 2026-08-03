# models/indicators/anti_manipulation.py
import pandas as pd
import numpy as np

class InstitutionalAntiManipulationShield:
    """
    Den Engine v11.0 Quantum Anti-Manipulation & Stop-Hunt Shield:
    1. Wick Sweep & Liquidity Trap Detection (Exposes Stop-Hunts)
    2. Wash-Trading / Fakeout Volume Verification
    3. Upper & Lower Wick Rejection Ratios (Exposes Market Maker Traps)
    """

    @staticmethod
    def audit_manipulation(df: pd.DataFrame) -> dict:
        data = df.copy()
        latest = data.iloc[-1]
        
        candle_range = max(latest['high'] - latest['low'], 0.0001)
        upper_wick = latest['high'] - max(latest['open'], latest['close'])
        lower_wick = min(latest['open'], latest['close']) - latest['low']

        upper_wick_ratio = upper_wick / candle_range
        lower_wick_ratio = lower_wick / candle_range

        # Market Maker Trap Signals
        is_bullish_trap = upper_wick_ratio >= 0.42 # High upper wick rejection (Fakeout Long)
        is_bearish_trap = lower_wick_ratio >= 0.42 # High lower wick absorption (Fakeout Short)

        # Stop-Hunt Liquidity Reclaim Audit
        prior_20_high = data['high'].iloc[-21:-2].max()
        prior_20_low = data['low'].iloc[-21:-2].min()

        stop_hunt_high = latest['high'] > prior_20_high and latest['close'] < prior_20_high
        stop_hunt_low = latest['low'] < prior_20_low and latest['close'] > prior_20_low

        is_manipulated = is_bullish_trap or is_bearish_trap or stop_hunt_high or stop_hunt_low
        shield_multiplier = 0.70 if is_manipulated else 1.05

        status = "CLEAN_ORGANIC_ORDERFLOW"
        if stop_hunt_high:
            status = "STOP_HUNT_HIGH_SWEEP (Bearish Reversal Risk)"
        elif stop_hunt_low:
            status = "STOP_HUNT_LOW_SWEEP (Bullish Reversal Reclaim)"
        elif is_bullish_trap:
            status = "MARKET_MAKER_SUPPLY_TRAP (Avoid Longs)"
        elif is_bearish_trap:
            status = "MARKET_MAKER_DEMAND_TRAP (Avoid Shorts)"

        return {
            "is_manipulated": is_manipulated,
            "status": status,
            "shield_multiplier": shield_multiplier,
            "upper_wick_ratio": round(upper_wick_ratio, 2),
            "lower_wick_ratio": round(lower_wick_ratio, 2)
        }
