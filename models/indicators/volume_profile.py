# models/indicators/volume_profile.py
import pandas as pd
import numpy as np

class InstitutionalVolumeProfile:
    """
    Den Engine v6.0 Volume Profile Point of Control (POC) Engine:
    Calculates the exact price node with maximum traded volume (POC).
    Ensures LONG entries occur ABOVE POC and SHORT entries BELOW POC.
    """
    @staticmethod
    def calculate_poc(df: pd.DataFrame, bins: int = 30) -> dict:
        data = df.copy()
        price_min = data['low'].min()
        price_max = data['high'].max()

        if price_max == price_min:
            return {"poc": data['close'].iloc[-1], "above_poc": True}

        price_bins = np.linspace(price_min, price_max, bins)
        data['bin'] = pd.cut(data['close'], bins=price_bins)
        vol_by_bin = data.groupby('bin', observed=False)['volume'].sum()

        max_bin = vol_by_bin.idxmax()
        poc_price = (max_bin.left + max_bin.right) / 2.0
        latest_close = data['close'].iloc[-1]

        return {
            "poc": round(poc_price, 2),
            "above_poc": latest_close > poc_price,
            "below_poc": latest_close < poc_price
        }
