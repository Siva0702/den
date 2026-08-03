# models/indicators/advanced_quant.py
import pandas as pd
import numpy as np

class AdvancedQuantEngine:
    @staticmethod
    def calculate_vwap_and_volatility(df: pd.DataFrame) -> dict:
        """
        Calculates VWAP, Volatility Regimes, and Volume Delta Imbalances.
        """
        data = df.copy()
        
        # 1. Volume Weighted Average Price (VWAP)
        typical_price = (data['high'] + data['low'] + data['close']) / 3.0
        data['pv'] = typical_price * data['volume']
        data['vwap'] = data['pv'].cumsum() / data['volume'].cumsum()
        
        # VWAP Standard Deviation Bands
        data['vwap_std'] = (data['close'] - data['vwap']).rolling(20).std()
        data['vwap_upper'] = data['vwap'] + (1.5 * data['vwap_std'])
        data['vwap_lower'] = data['vwap'] - (1.5 * data['vwap_std'])

        # 2. Volatility Compression / Expansion Squeeze
        data['bb_middle'] = data['close'].rolling(20).mean()
        data['bb_std'] = data['close'].rolling(20).std()
        data['bb_upper'] = data['bb_middle'] + (2.0 * data['bb_std'])
        data['bb_lower'] = data['bb_middle'] - (2.0 * data['bb_std'])
        data['bb_width'] = (data['bb_upper'] - data['bb_lower']) / data['bb_middle']

        latest = data.iloc[-1]
        mean_width = data['bb_width'].rolling(50).mean().iloc[-1]

        # Determine Volatility Regime
        is_squeeze = latest['bb_width'] < (mean_width * 0.70)
        is_expansion = latest['bb_width'] > (mean_width * 1.30)
        vol_regime = "COMPRESSION (BREAKOUT IMMINENT)" if is_squeeze else ("EXPANSION (HIGH VOLATILITY)" if is_expansion else "NORMAL")

        # 3. Volume Delta Spikes (Taker Buy Imbalance)
        avg_vol = data['volume'].rolling(20).mean().iloc[-1]
        vol_spike_ratio = round(latest['volume'] / max(avg_vol, 1), 2)
        has_volume_surge = vol_spike_ratio >= 1.8

        # 4. VWAP Position & Confluence Alignment
        above_vwap = latest['close'] > latest['vwap']

        return {
            "close": latest['close'],
            "vwap": round(latest['vwap'], 4),
            "vwap_upper": round(latest['vwap_upper'], 4),
            "vwap_lower": round(latest['vwap_lower'], 4),
            "above_vwap": above_vwap,
            "volatility_regime": vol_regime,
            "volume_spike_ratio": vol_spike_ratio,
            "has_volume_surge": has_volume_surge
        }
