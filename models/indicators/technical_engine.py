# models/indicators/technical_engine.py
import pandas as pd
import numpy as np

class TechnicalAnalysisEngine:
    @staticmethod
    def calculate_indicators(df: pd.DataFrame) -> dict:
        """
        Expects a DataFrame with columns: ['open', 'high', 'low', 'close', 'volume']
        Calculates EMA Cross, RSI(14), ATR(14), and Fair Value Gaps (FVG).
        """
        # EMA 20 & 50
        df['ema_20'] = df['close'].ewm(span=20, adjust=False).mean()
        df['ema_50'] = df['close'].ewm(span=50, adjust=False).mean()
        
        # Relative Strength Index (RSI 14)
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))

        # Average True Range (ATR 14)
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        df['atr'] = true_range.rolling(14).mean()

        latest = df.iloc[-1]
        prev = df.iloc[-2]

        # Identify Bullish/Bearish Fair Value Gap (FVG) on last 3 candles
        fvg_bullish = df.iloc[-1]['low'] > df.iloc[-3]['high']
        fvg_bearish = df.iloc[-1]['high'] < df.iloc[-3]['low']

        return {
            "close_price": latest['close'],
            "ema_20": latest['ema_20'],
            "ema_50": latest['ema_50'],
            "rsi": round(latest['rsi'], 2),
            "atr": round(latest['atr'], 4),
            "ema_trend": "BULLISH" if latest['ema_20'] > latest['ema_50'] else "BEARISH",
            "fvg_detected": "BULLISH FVG" if fvg_bullish else ("BEARISH FVG" if fvg_bearish else "NONE")
        }
