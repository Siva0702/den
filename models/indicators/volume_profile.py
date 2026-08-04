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

        if price_max < 0.0001:
            precision = 8
        elif price_max < 0.01:
            precision = 6
        elif price_max < 1.0:
            precision = 4
        elif price_max < 100.0:
            precision = 3
        else:
            precision = 2

        if price_max == price_min:
            return {
                "poc": round(data['close'].iloc[-1], precision),
                "above_poc": True,
                "below_poc": False
            }

        price_bins = np.linspace(price_min, price_max, bins)
        
        expanded_prices = []
        expanded_vols = []
        for _, row in data.iterrows():
            candle_prices = np.linspace(row['low'], row['high'], 5)
            expanded_prices.extend(candle_prices)
            expanded_vols.extend([row['volume']/5]*5)
            
        dist_df = pd.DataFrame({'price': expanded_prices, 'volume': expanded_vols})
        dist_df['bin'] = pd.cut(dist_df['price'], bins=price_bins, include_lowest=True)
        vol_by_bin = dist_df.groupby('bin', observed=False)['volume'].sum()

        max_bin = vol_by_bin.idxmax()
        poc_price = (max_bin.left + max_bin.right) / 2.0
        latest_close = data['close'].iloc[-1]

        return {
            "poc": round(poc_price, precision),
            "above_poc": latest_close > poc_price,
            "below_poc": latest_close < poc_price
        }
