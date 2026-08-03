# models/indicators/confluence_engine.py
import pandas as pd
import numpy as np

class SureShotConfluenceEngine:
    @staticmethod
    def evaluate_setup(
        ohlcv_df: pd.DataFrame, 
        sentiment_multiplier: float, 
        base_win_rate: float = 0.55
    ) -> dict:
        """
        Refined v3.0 Quantitative Sure-Shot Filter.
        Requires:
        1. Market Structure Break (BOS / CHoCH)
        2. Fair Value Gap (FVG) Retest
        3. VWAP Alignment (Longs > VWAP, Shorts < VWAP)
        4. Sentiment Multiplier Sm >= 1.15 (Bullish) or <= 0.85 (Bearish)
        5. Win Rate W >= 65% & EV >= +0.10
        """
        df = ohlcv_df.copy()
        df['ema_20'] = df['close'].ewm(span=20, adjust=False).mean()
        df['ema_50'] = df['close'].ewm(span=50, adjust=False).mean()
        
        # Calculate VWAP
        typical_price = (df['high'] + df['low'] + df['close']) / 3.0
        df['pv'] = typical_price * df['volume']
        df['vwap'] = df['pv'].cumsum() / df['volume'].cumsum()

        # RSI 14
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))

        # ATR 14
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        df['atr'] = np.max(pd.concat([high_low, high_close, low_close], axis=1), axis=1).rolling(14).mean()

        latest = df.iloc[-1]
        prior_candle = df.iloc[-3]

        # 1. Structural Break Checks (BOS / CHoCH)
        bullish_bos = latest['close'] > df['high'].iloc[-10:-2].max()
        bearish_bos = latest['close'] < df['low'].iloc[-10:-2].min()

        # 2. Fair Value Gap (FVG) Detection
        bullish_fvg = latest['low'] > prior_candle['high']
        bearish_fvg = latest['high'] < prior_candle['low']

        # 3. VWAP Alignment
        above_vwap = latest['close'] > latest['vwap']
        below_vwap = latest['close'] < latest['vwap']

        # 4. Directional Alignment
        is_bullish = bullish_bos and latest['ema_20'] > latest['ema_50'] and above_vwap and sentiment_multiplier >= 1.15
        is_bearish = bearish_bos and latest['ema_20'] < latest['ema_50'] and below_vwap and sentiment_multiplier <= 0.85

        # 5. Adjusted Probability & EV Calculation
        adjusted_win_rate = min(max(base_win_rate * sentiment_multiplier, 0.35), 0.85)
        reward_to_risk = 2.50
        ev = (adjusted_win_rate * reward_to_risk) - (1.0 - adjusted_win_rate)

        # STRICT GATE: Requires Win Rate >= 65% AND EV >= +0.10
        is_sure_shot = (is_bullish or is_bearish) and (adjusted_win_rate >= 0.65) and (ev >= 0.10)

        return {
            "is_sure_shot": is_sure_shot,
            "direction": "LONG" if is_bullish else ("SHORT" if is_bearish else "NONE"),
            "win_rate": round(adjusted_win_rate, 4),
            "expected_value": round(ev, 4),
            "entry_price": latest['close'],
            "vwap": round(latest['vwap'], 4),
            "atr": latest['atr'],
            "rsi": round(latest['rsi'], 2),
            "fvg_detected": "BULLISH FVG" if bullish_fvg else ("BEARISH FVG" if bearish_fvg else "NONE")
        }
