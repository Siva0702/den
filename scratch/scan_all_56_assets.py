# scratch/scan_all_56_assets.py
import sys
import os
import requests
import pandas as pd
import numpy as np

sys.path.append('models')
from news.market_universe import DynamicMarketUniverse
from data.live_feed import RealtimeMarketDataFeed

def scan_full_56_universe():
    universe = DynamicMarketUniverse.get_full_hunting_universe()
    print("=" * 90)
    print(f"   DEN ENGINE v5.0 APEX — SCANNING ALL {len(universe)} GLOBAL ASSETS IN REAL TIME")
    print("=" * 90)

    results = []

    for item in universe:
        ticker = item["ticker"]
        asset_class = item["asset_class"]
        sector = item.get("sector", "Global")
        base_p = item.get("base_price", 100.0)

        df = RealtimeMarketDataFeed.get_live_ohlcv(ticker, asset_class, base_p)
        if df is None or len(df) < 15:
            continue

        close = df['close'].iloc[-1]
        ema20 = df['close'].ewm(span=20, adjust=False).mean().iloc[-1]
        ema50 = df['close'].ewm(span=50, adjust=False).mean().iloc[-1]

        # VWAP
        tp = (df['high'] + df['low'] + df['close']) / 3.0
        vwap = (tp * df['volume']).cumsum().iloc[-1] / df['volume'].cumsum().iloc[-1]

        # RSI 14
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi = (100 - (100 / (1 + (gain / loss)))).iloc[-1]

        vwap_dist = ((close - vwap) / vwap) * 100

        # Scoring Engine
        if close > vwap and ema20 > ema50:
            score = (100 - abs(rsi - 55) * 2.2) + vwap_dist
            direction = "LONG"
        elif close < vwap and ema20 < ema50:
            score = (100 - abs(rsi - 45) * 2.2) - vwap_dist
            direction = "SHORT"
        else:
            score = 45.0
            direction = "NEUTRAL"

        results.append({
            "ticker": ticker,
            "sector": sector,
            "asset_class": asset_class,
            "close": close,
            "vwap": vwap,
            "rsi": rsi,
            "score": score,
            "direction": direction
        })

    results.sort(key=lambda x: x["score"], reverse=True)

    print(f"{'RANK':<5} | {'TICKER':<14} | {'SECTOR':<22} | {'PRICE':<10} | {'VWAP':<10} | {'RSI':<6} | {'SCORE':<6} | {'BIAS'}")
    print("-" * 90)
    for idx, item in enumerate(results, 1):
        print(f"{idx:<5} | {item['ticker']:<14} | {item['sector']:<22} | ${item['close']:<9.2f} | ${item['vwap']:<9.2f} | {item['rsi']:<6.1f} | {item['score']:<6.1f} | {item['direction']}")
    print("=" * 90)

if __name__ == "__main__":
    scan_full_56_universe()
