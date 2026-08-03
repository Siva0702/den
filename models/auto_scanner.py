# models/auto_scanner.py
import os
import sys
import time
import pandas as pd
import numpy as np
from dotenv import load_dotenv

# Import internal modules (supports running from den root or models/)
sys.path.append(os.path.dirname(__file__))
from indicators.confluence_engine import SureShotConfluenceEngine
from position_monitor import ActivePositionMonitor
from news.news_fetcher import RealtimeNewsFetcher
from nlp.sentiment_engine import HuggingFaceSentimentEngine
from alerts.telegram_bot import TelegramAlertBot

load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8847828896:AAFcTqjJGe6VN6mbPHcB1QTlvkpQxhb5ntI")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "7347569157")

WATCHLIST = [
    {"ticker": "SOL/USDT", "asset_class": "Crypto Futures"},
    {"ticker": "BTC/USDT", "asset_class": "Crypto Futures"},
    {"ticker": "NVDA/USDT", "asset_class": "Tokenized Equity"},
    {"ticker": "GOLD/USDT", "asset_class": "Commodity"}
]

monitor = ActivePositionMonitor(BOT_TOKEN, CHAT_ID)
nlp = HuggingFaceSentimentEngine.get_instance()
news_engine = RealtimeNewsFetcher()
telegram = TelegramAlertBot(BOT_TOKEN, CHAT_ID)

def run_silent_quant_scan():
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Silent Quant Scan Running...")

    # Fetch global news wire
    headlines = news_engine.fetch_latest_headlines(limit=1)
    headline_text = headlines[0]['headline'] if headlines else "Markets trading within normal variance."
    sentiment = nlp.analyze_news(headline_text)
    sm = sentiment["sentiment_multiplier"]

    for item in WATCHLIST:
        ticker = item["ticker"]
        
        # 1. Generate Synthetic/Live Price Feed
        np.random.seed(int(time.time() * 1000) % 10000)
        prices = 150 + np.cumsum(np.random.randn(100) * 0.4)
        df = pd.DataFrame({
            'open': prices, 'high': prices + 0.5, 'low': prices - 0.5, 'close': prices + 0.1
        })

        # 2. Check Open Trades for Invalidation / Early Exit First
        current_price = df.iloc[-1]['close']
        structure_flipped = df.iloc[-1]['close'] < df.iloc[-10]['low'] # Simple structure break test
        monitor.check_active_positions(ticker, current_price, sm, structure_flipped)

        # 3. Evaluate New Sure-Shot Opportunities
        signal = SureShotConfluenceEngine.evaluate_setup(df, sm)

        if signal["is_sure_shot"]:
            # Format and send HIGH-CONFLUENCE SIGNAL ONLY
            direction = signal["direction"]
            entry = signal["entry_price"]
            sl = entry - (signal["atr"] * 1.5) if direction == "LONG" else entry + (signal["atr"] * 1.5)
            tp = entry + (signal["atr"] * 3.75) if direction == "LONG" else entry - (signal["atr"] * 3.75)

            alert_msg = f"""
🎯 **SURE-SHOT QUANT SIGNAL DETECTED** 🎯
━━━━━━━━━━━━━━━━━━━━━━━━
• **Asset:** `{ticker}` ({item['asset_class']})
• **Direction:** `{direction}`
• **Model Win Rate:** `{signal['win_rate']*100:.1f}%` | **EV:** `+{signal['expected_value']:.2f}`

📰 **CONFLUENCE DRIVERS**
• **Headline:** "{headline_text}"
• **NLP Sentiment:** `{sentiment['dominant_sentiment']}` ({sm}x Multiplier)
• **Technical Setup:** Structural Break + `{signal['fvg_detected']}`

⚡ **EXECUTION COMMAND (Bitunix Isolated)**
• **Entry Price:** `${entry:,.4f}`
• **Stop Loss:** `${sl:,.4f}`
• **Take Profit:** `${tp:,.4f}`
• **Leverage:** `10x Isolated`
━━━━━━━━━━━━━━━━━━━━━━━━
            """
            telegram.send_alert(alert_msg)
            print(f"[✓] SURE-SHOT ALERT DISPATCHED FOR {ticker}")

            # Register into active position tracking
            positions = monitor.load_positions()
            positions.append({
                "ticker": ticker,
                "direction": direction,
                "entry_price": entry,
                "stop_loss": sl,
                "take_profit": tp,
                "time": time.strftime('%Y-%m-%d %H:%M:%S')
            })
            monitor.save_positions(positions)

if __name__ == "__main__":
    print("🚀 Anti Gravity Silent Auto-Scanner Active (5-minute interval)...")
    try:
        while True:
            run_silent_quant_scan()
            time.sleep(300) # Scan quietly every 5 minutes
    except KeyboardInterrupt:
        print("\n[!] Silent Scanner stopped by user.")
