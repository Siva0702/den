# models/indicators/duration_estimator.py
import pandas as pd
import numpy as np

class PrecisionDurationEstimator:
    """
    Den Engine v9.1 Precision Trade Duration Estimator:
    Calculates exact estimated minutes to hit Take Profit (TP) based on ATR velocity.
    Formula: Duration (mins) = (|TP - Entry| / (15m ATR * MVI)) * 15 mins
    """
    @staticmethod
    def calculate_estimated_duration(entry: float, tp: float, atr: float, velocity_ratio: float = 1.2) -> dict:
        tp_distance = abs(tp - entry)
        atr_per_candle = max(atr, 0.0001)
        
        candles_needed = tp_distance / (atr_per_candle * max(velocity_ratio, 0.8))
        est_minutes = int(round(candles_needed * 15))
        
        min_duration = max(est_minutes - 10, 15)
        max_duration = est_minutes + 15

        if min_duration <= 45:
            label = f"{min_duration} – {max_duration} mins (Fast Intraday Scalp)"
        elif min_duration <= 120:
            label = f"{min_duration // 60}h {min_duration % 60}m – {max_duration // 60}h {max_duration % 60}m (Intraday Trend Drive)"
        else:
            label = f"{min_duration // 60}h – {max_duration // 60}h (Multi-Hour Horizon)"

        return {
            "est_minutes": est_minutes,
            "min_duration": min_duration,
            "max_duration": max_duration,
            "formatted_label": label
        }
