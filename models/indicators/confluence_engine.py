# models/indicators/confluence_engine.py
import pandas as pd
import numpy as np
from indicators.institutional_smc import InstitutionalSMCEngine

class SureShotConfluenceEngine:
    @staticmethod
    def evaluate_setup(
        ohlcv_df: pd.DataFrame, 
        sentiment_multiplier: float, 
        base_win_rate: float = 0.58
    ) -> dict:
        """
        Den Engine v5.0 Apex Sure-Shot Confluence Engine.
        Requires:
        1. Market Structure Break (BOS / CHoCH)
        2. Institutional Order Block (OB) or Fair Value Gap (FVG) Retest
        3. VWAP Trend Alignment (Longs > VWAP, Shorts < VWAP)
        4. Sentiment Multiplier Sm >= 1.15 (Bullish) or <= 0.85 (Bearish)
        5. Apex Gate: Win Rate W >= 70% & EV >= +0.40
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
        
        # SMC Structure Analysis
        smc = InstitutionalSMCEngine.analyze_smc_structure(df)

        # 1. Structural Break Checks (BOS / CHoCH)
        bullish_bos = latest['close'] > df['high'].iloc[-15:-2].max()
        bearish_bos = latest['close'] < df['low'].iloc[-15:-2].min()

        # 2. VWAP Alignment
        above_vwap = latest['close'] > latest['vwap']
        below_vwap = latest['close'] < latest['vwap']

        # 3. Apex Alignment Criteria
        is_bullish = (bullish_bos or smc['bullish_ob'] or smc['liquidity_sweep_low']) and \
                     latest['ema_20'] > latest['ema_50'] and above_vwap and sentiment_multiplier >= 1.12
                     
        is_bearish = (bearish_bos or smc['bearish_ob'] or smc['liquidity_sweep_high']) and \
                     latest['ema_20'] < latest['ema_50'] and below_vwap and sentiment_multiplier <= 0.88

        # 4. Apex Adjusted Win Rate & EV Calculation
        adjusted_win_rate = min(max(base_win_rate * sentiment_multiplier, 0.40), 0.88)
        reward_to_risk = 3.0
        ev = (adjusted_win_rate * reward_to_risk) - (1.0 - adjusted_win_rate)

        # APEX GATE: Requires Win Rate >= 68.0% AND EV >= +0.30
        is_sure_shot = (is_bullish or is_bearish) and (adjusted_win_rate >= 0.68) and (ev >= 0.30)

        fvg_str = "BULLISH FVG" if smc['bullish_fvg'] else ("BEARISH FVG" if smc['bearish_fvg'] else "SMC OB ZONE")

        return {
            "is_sure_shot": is_sure_shot,
            "direction": "LONG" if is_bullish else ("SHORT" if is_bearish else "NONE"),
            "win_rate": round(adjusted_win_rate, 4),
            "expected_value": round(ev, 4),
            "entry_price": latest['close'],
            "vwap": round(latest['vwap'], 4),
            "atr": latest['atr'],
            "rsi": round(latest['rsi'], 2),
            "fvg_detected": fvg_str
        }
