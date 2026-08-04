# models/indicators/orderflow_imbalance.py
import pandas as pd
import numpy as np

class InstitutionalOrderFlowEngine:
    """
    Den Engine v10.0 Institutional Order Flow & Taker Imbalance Engine:
    Uses volume-weighted directional analysis.
    """
    @staticmethod
    def analyze_orderflow(df: pd.DataFrame) -> dict:
        data = df.copy()
        
        # Calculate buy/sell volume
        data['buy_vol'] = np.where(data['close'] > data['open'], data['volume'], 
                                   np.where(data['close'] == data['open'], data['volume'] / 2.0, 0.0))
        data['sell_vol'] = np.where(data['close'] < data['open'], data['volume'], 
                                    np.where(data['close'] == data['open'], data['volume'] / 2.0, 0.0))
                                    
        # Rolling 10-bar buy/total ratio
        data['rolling_buy_vol'] = data['buy_vol'].rolling(10).sum()
        data['rolling_sell_vol'] = data['sell_vol'].rolling(10).sum()
        data['rolling_total_vol'] = data['rolling_buy_vol'] + data['rolling_sell_vol']
        
        # Handle zero total volume
        data['buy_ratio'] = np.where(data['rolling_total_vol'] > 0, 
                                     data['rolling_buy_vol'] / data['rolling_total_vol'], 0.5)
        
        # Volume delta & cumulative delta
        data['delta'] = data['buy_vol'] - data['sell_vol']
        data['cumulative_delta'] = data['delta'].cumsum()
        
        # Volume surge
        data['vol_sma20'] = data['volume'].rolling(20).mean()
        data['vol_surge_ratio'] = np.where(data['vol_sma20'] > 0, data['volume'] / data['vol_sma20'], 1.0)
        data['volume_surge'] = data['vol_surge_ratio'] > 1.5
        
        # Extract latest values
        latest_buy_ratio = data['buy_ratio'].iloc[-1]
        latest_sell_ratio = 1.0 - latest_buy_ratio
        
        is_aggressive_buying = latest_buy_ratio > 0.60
        is_aggressive_selling = latest_buy_ratio < 0.40
        
        if is_aggressive_buying:
            orderflow_score = 1.0 + (latest_buy_ratio - 0.5) * 0.4 # up to 1.2
            orderflow_score = min(orderflow_score, 1.2)
        elif is_aggressive_selling:
            orderflow_score = 1.0 - (0.5 - latest_buy_ratio) * 0.4 # down to 0.8
            orderflow_score = max(orderflow_score, 0.8)
        else:
            orderflow_score = 1.0

        return {
            "buy_ratio": round(latest_buy_ratio * 100, 1),
            "sell_ratio": round(latest_sell_ratio * 100, 1),
            "is_aggressive_buying": bool(is_aggressive_buying),
            "is_aggressive_selling": bool(is_aggressive_selling),
            "volume_surge": bool(data['volume_surge'].iloc[-1]),
            "volume_surge_ratio": round(float(data['vol_surge_ratio'].iloc[-1]), 2),
            "cumulative_delta": float(data['cumulative_delta'].iloc[-1]),
            "orderflow_score": round(orderflow_score, 2)
        }
