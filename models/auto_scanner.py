# models/auto_scanner.py
import time
import sys
import os
import schedule
import pandas as pd
import numpy as np

# Path routing to support running from den root or models directory
sys.path.append(os.path.dirname(__file__))
from master_pipeline import run_a_to_z_quant_pipeline

# Watchlist across Crypto, Tokenized Stocks, and Commodities
WATCHLIST = [
    {"ticker": "SOL/USDT", "asset_class": "Crypto Futures", "base_price": 150.0},
    {"ticker": "BTC/USDT", "asset_class": "Crypto Futures", "base_price": 65000.0},
    {"ticker": "NVDA/USDT", "asset_class": "Tokenized Equity", "base_price": 125.0},
    {"ticker": "GOLD/USDT", "asset_class": "Commodity", "base_price": 2400.0}
]

def generate_ohlcv_feed(base_price: float = 150.0) -> pd.DataFrame:
    """
    Generates OHLCV candle data stream for technical analysis scanning.
    Replace with live exchange API feed (e.g., Bitunix / Binance / CCXT) when deploying live.
    """
    np.random.seed(int(time.time() * 1000) % 100000)
    dates = pd.date_range(end=pd.Timestamp.now(), periods=100, freq='15min')
    prices = base_price + np.cumsum(np.random.randn(100) * (base_price * 0.002))
    return pd.DataFrame({
        'open': prices,
        'high': prices + np.random.rand(100) * (base_price * 0.003),
        'low': prices - np.random.rand(100) * (base_price * 0.003),
        'close': prices + np.random.randn(100) * (base_price * 0.001),
        'volume': np.random.randint(100, 1000, size=100)
    }, index=dates)

def scan_all_markets():
    print("\n[⚡] Running automated multi-asset quant scan...")
    for item in WATCHLIST:
        try:
            print(f"Scanning {item['ticker']}...")
            df = generate_ohlcv_feed(item['base_price'])
            run_a_to_z_quant_pipeline(
                ticker=item['ticker'],
                asset_class=item['asset_class'],
                ohlcv_data=df,
                is_scalp=False,
                account_balance=1000.0
            )
        except Exception as e:
            print(f"[!] Error scanning {item['ticker']}: {e}")

if __name__ == "__main__":
    # Schedule the scanner to run every 15 minutes automatically
    schedule.every(15).minutes.do(scan_all_markets)

    print("🚀 Anti Gravity Automated Scanner active. Press Ctrl+C to exit.")
    scan_all_markets() # Run immediately on startup

    # Infinite loop for background daemon operation
    # To run as daemon: nohup python3 models/auto_scanner.py > scanner.log 2>&1 &
    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[!] Scanner stopped by user.")
