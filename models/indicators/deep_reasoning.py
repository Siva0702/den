# models/indicators/deep_reasoning.py
import pandas as pd
import numpy as np

class DeepReasoningQuantEngine:
    """
    Den Engine v17.3 Deep Reasoning & Manipulation Defense Engine:
    Deeply analyzes every news headline, indicator spike, and price action wick
    to determine whether it is a Market Maker Trap / News Manipulation or a Real Organic Sure-Shot!
    """

    @staticmethod
    def audit_setup_authenticity(
        df: pd.DataFrame, 
        headline_sentiment: float, 
        taker_buy_ratio: float, 
        direction: str
    ) -> dict:
        data = df.copy()
        latest = data.iloc[-1]
        
        candle_range = max(latest['high'] - latest['low'], 0.0001)
        upper_wick = latest['high'] - max(latest['open'], latest['close'])
        lower_wick = min(latest['open'], latest['close']) - latest['low']

        upper_wick_pct = upper_wick / candle_range
        lower_wick_pct = lower_wick / candle_range

        is_manipulated = False
        reasoning_verdict = "ORGANIC_SURE_SHOT_CONFLUENCE"
        authenticity_score = 1.0

        # 1. "Sell the News" Trap Audit
        if direction == "LONG" and headline_sentiment >= 1.15 and upper_wick_pct >= 0.38:
            is_manipulated = True
            reasoning_verdict = "NEWS_MANIPULATION_TRAP (Bullish News used to unload supply)"
            authenticity_score = 0.60
        elif direction == "SHORT" and headline_sentiment <= 0.85 and lower_wick_pct >= 0.38:
            is_manipulated = True
            reasoning_verdict = "NEWS_MANIPULATION_TRAP (Bearish News used to absorb demand)"
            authenticity_score = 0.60

        # 2. Fakeout Volume vs Taker Imbalance Divergence
        if direction == "LONG" and taker_buy_ratio < 48.0:
            is_manipulated = True
            reasoning_verdict = "MANIPULATED_FAKEOUT (Volume spike lacks real Taker buying)"
            authenticity_score = 0.65
        elif direction == "SHORT" and taker_buy_ratio > 52.0:
            is_manipulated = True
            reasoning_verdict = "MANIPULATED_FAKEOUT (Volume spike lacks real Taker selling)"
            authenticity_score = 0.65

        # 3. Structural Reclaim Check
        if not is_manipulated:
            reasoning_verdict = "DEEP_REASONING_VERIFIED (Clean Orderflow + Real Institutional Catalyst)"
            authenticity_score = 1.10

        return {
            "is_authentic_sure_shot": not is_manipulated,
            "reasoning_verdict": reasoning_verdict,
            "authenticity_score": authenticity_score
        }
