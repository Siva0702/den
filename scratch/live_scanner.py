# scratch/live_scanner.py
import requests
import pandas as pd
import numpy as np

symbols = [
    ("SOLUSDT", "SOL/USDT"),
    ("BTCUSDT", "BTC/USDT"),
    ("ETHUSDT", "ETH/USDT"),
    ("SUIUSDT", "SUI/USDT"),
    ("PEPEUSDT", "PEPE/USDT"),
    ("AVAXUSDT", "AVAX/USDT"),
    ("NEARUSDT", "NEAR/USDT"),
    ("WIFUSDT", "WIF/USDT"),
    ("RENDERUSDT", "RENDER/USDT")
]

print("=" * 70)
print("      LIVE REAL-TIME INTRADAY MARKET SCANNER (1-HOUR TARGET)")
print("=" * 70)

results = []

for raw_sym, display_sym in symbols:
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
            
            trend = "BULLISH" if (ema20 > ema50 and close > vwap) else ("BEARISH" if (ema20 < ema50 and close < vwap) else "NEUTRAL")
            
            # Distance calculations
            dist_to_vwap_pct = ((close - vwap) / vwap) * 100
            
            results.append({
                "ticker": display_sym,
                "close": close,
                "vwap": vwap,
                "trend": trend,
                "rsi": rsi,
                "atr": atr,
                "vwap_dist_pct": dist_to_vwap_pct
            })
            print(f"Ticker: {display_sym:10s} | Close: ${close:10.4f} | VWAP: ${vwap:10.4f} | Trend: {trend:8s} | RSI: {rsi:5.1f} | ATR: ${atr:.4f}")
    except Exception as e:
        print(f"Error fetching {raw_sym}: {e}")

print("=" * 70)
