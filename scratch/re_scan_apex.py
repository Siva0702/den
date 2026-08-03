# scratch/re_scan_apex.py
import requests
import pandas as pd
import numpy as np

def perform_fresh_live_scan():
    print("=" * 75)
    print("      LIVE REAL-TIME FRESH APEX SCAN (RIGHT NOW)")
    print("=" * 75)

    symbols = ["ETHUSDT", "SOLUSDT", "BTCUSDT", "SUIUSDT", "AVAXUSDT", "NEARUSDT", "WIFUSDT", "PEPEUSDT"]
    results = []

    for raw_sym in symbols:
        url = f"https://api.binance.com/api/v3/klines?symbol={raw_sym}&interval=15m&limit=100"
        try:
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                df = pd.DataFrame(data, columns=['ts', 'open', 'high', 'low', 'close', 'vol', 'ct', 'qav', 'nt', 'tbba', 'tbqa', 'ig'])
                for col in ['open', 'high', 'low', 'close', 'vol']:
                    df[col] = df[col].astype(float)
                
                close = df['close'].iloc[-1]
                ema20 = df['close'].ewm(span=20, adjust=False).mean().iloc[-1]
                ema50 = df['close'].ewm(span=50, adjust=False).mean().iloc[-1]
                
                # VWAP
                tp = (df['high'] + df['low'] + df['close']) / 3.0
                vwap = (tp * df['vol']).cumsum().iloc[-1] / df['vol'].cumsum().iloc[-1]
                
                # RSI 14
                delta = df['close'].diff()
                gain = (delta.where(delta > 0, 0)).rolling(14).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
                rsi = (100 - (100 / (1 + (gain / loss)))).iloc[-1]
                
                # ATR 14
                hl = df['high'] - df['low']
                hc = np.abs(df['high'] - df['close'].shift())
                lc = np.abs(df['low'] - df['close'].shift())
                atr = np.max(pd.concat([hl, hc, lc], axis=1), axis=1).rolling(14).mean().iloc[-1]
                
                # Volume Surge Ratio
                avg_vol = df['vol'].rolling(20).mean().iloc[-1]
                vol_ratio = df['vol'].iloc[-1] / max(avg_vol, 1)

                vwap_dist = ((close - vwap) / vwap) * 100
                
                # Apex Confluence Scoring Engine
                if close > vwap and ema20 > ema50:
                    score = (100 - abs(rsi - 55) * 2.5) + (vol_ratio * 10) + vwap_dist
                    direction = "LONG"
                elif close < vwap and ema20 < ema50:
                    score = (100 - abs(rsi - 45) * 2.5) + (vol_ratio * 10) - vwap_dist
                    direction = "SHORT"
                else:
                    score = 40.0
                    direction = "NEUTRAL"

                results.append({
                    "symbol": raw_sym.replace("USDT", "/USDT"),
                    "raw_symbol": raw_sym,
                    "close": close,
                    "vwap": vwap,
                    "rsi": rsi,
                    "atr": atr,
                    "vol_ratio": vol_ratio,
                    "vwap_dist_pct": vwap_dist,
                    "score": score,
                    "direction": direction
                })
        except Exception as e:
            print(f"Error scanning {raw_sym}: {e}")

    results.sort(key=lambda x: x["score"], reverse=True)

    print(f"{'RANK':<5} | {'TICKER':<10} | {'PRICE':<10} | {'VWAP':<10} | {'RSI':<6} | {'SCORE':<7} | {'BIAS'}")
    print("-" * 75)
    for idx, item in enumerate(results, 1):
        print(f"{idx:<5} | {item['symbol']:<10} | ${item['close']:<9.2f} | ${item['vwap']:<9.2f} | {item['rsi']:<6.1f} | {item['score']:<7.1f} | {item['direction']}")
    print("=" * 75)

if __name__ == "__main__":
    perform_fresh_live_scan()
