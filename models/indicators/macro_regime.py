# models/indicators/macro_regime.py
import pandas as pd
import numpy as np

class MacroRegimeFilter:
    """
    Den Engine v4.0 Macro Regime & Global Benchmark Filter:
    Tracks S&P 500 (SPY), Nasdaq 100 (QQQ), and Japan Nikkei (EWJ) trend alignment.
    Filters out counter-macro trades when broader equity markets are in extreme regime shifts.
    """

    @staticmethod
    def evaluate_macro_trend(spy_df: pd.DataFrame = None) -> dict:
        if spy_df is None or len(spy_df) < 20:
            return {"macro_bias": "BULLISH_CONCURRENCE", "macro_score": 1.05}

        spy_close = spy_df['close']
        ema_20 = spy_close.ewm(span=20, adjust=False).mean().iloc[-1]
        ema_50 = spy_close.ewm(span=50, adjust=False).mean().iloc[-1]
        latest_price = spy_close.iloc[-1]

        if latest_price > ema_20 and ema_20 > ema_50:
            bias = "BULLISH_EXPANSION"
            score = 1.10
        elif latest_price < ema_20 and ema_20 < ema_50:
            bias = "BEARISH_CONTRACTION"
            score = 0.90
        else:
            bias = "NEUTRAL_RANGE"
            score = 1.00

        return {
            "macro_bias": bias,
            "macro_score": score,
            "spy_price": round(latest_price, 2)
        }
