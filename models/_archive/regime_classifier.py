# models/indicators/regime_classifier.py
import pandas as pd
import numpy as np

class MarketRegimeClassifier:
    """
    Den Engine v15.0 Volatility & Regime Classifier:
    Classifies market regime into:
    1. TRENDING_EXPANSION (High Momentum Breakouts -> 3.6x ATR Target)
    2. HIGH_VOLATILITY_SURGE (High Expansion -> 4.2x ATR Target)
    3. LOW_VOLATILITY_SQUEEZE (Range Compression -> 2.8x ATR Target)
    """

    @staticmethod
    def classify_regime(df: pd.DataFrame) -> dict:
        data = df.copy()
        
        # Volatility ratio
        high_low = data['high'] - data['low']
        atr = high_low.rolling(14).mean().iloc[-1]
        std_dev = data['close'].rolling(20).std().iloc[-1]
        
        mean_atr = high_low.rolling(50).mean().iloc[-1]
        vol_expansion_ratio = atr / max(mean_atr, 0.0001)

        regime = "TRENDING_MOMENTUM"
        tp_multiplier = 3.6
        sl_multiplier = 1.2

        if vol_expansion_ratio >= 1.35:
            regime = "HIGH_VOLATILITY_EXPANSION"
            tp_multiplier = 4.2
            sl_multiplier = 1.4
        elif vol_expansion_ratio <= 0.75:
            regime = "LOW_VOLATILITY_COMPRESSION"
            tp_multiplier = 2.8
            sl_multiplier = 1.0

        return {
            "regime": regime,
            "vol_expansion_ratio": round(vol_expansion_ratio, 2),
            "tp_multiplier": tp_multiplier,
            "sl_multiplier": sl_multiplier
        }
