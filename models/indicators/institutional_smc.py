# models/indicators/institutional_smc.py
import pandas as pd
import numpy as np

class InstitutionalSMCEngine:
    """
    Den Engine v5.0 Apex Smart Money Concepts (SMC) Engine:
    1. Institutional Order Block (OB) Detection
    2. Liquidity Sweep (Equal Highs/Lows Reclaim)
    3. Fair Value Gap (FVG) Imbalance
    """
    @staticmethod
    def analyze_smc_structure(df: pd.DataFrame) -> dict:
        data = df.copy()
        latest = data.iloc[-1]
        prev = data.iloc[-2]
        prior = data.iloc[-3]

        # 1. Fair Value Gap (FVG)
        bullish_fvg = latest['low'] > prior['high']
        bearish_fvg = latest['high'] < prior['low']

        # 2. Institutional Order Block (OB) - Last opposite candle before aggressive move
        is_bullish_ob = (prior['close'] < prior['open']) and (latest['close'] > prev['high'])
        is_bearish_ob = (prior['close'] > prior['open']) and (latest['close'] < prev['low'])

        # 3. Liquidity Sweep Reclaim
        recent_high = data['high'].iloc[-20:-2].max()
        recent_low = data['low'].iloc[-20:-2].min()
        
        sweep_high = latest['high'] > recent_high and latest['close'] < recent_high
        sweep_low = latest['low'] < recent_low and latest['close'] > recent_low

        smc_score = 0
        if bullish_fvg or is_bullish_ob or sweep_low:
            smc_score += 1
        if bearish_fvg or is_bearish_ob or sweep_high:
            smc_score -= 1

        return {
            "bullish_fvg": bullish_fvg,
            "bearish_fvg": bearish_fvg,
            "bullish_ob": is_bullish_ob,
            "bearish_ob": is_bearish_ob,
            "liquidity_sweep_low": sweep_low,
            "liquidity_sweep_high": sweep_high,
            "smc_score": smc_score
        }
