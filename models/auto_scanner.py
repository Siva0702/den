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

# Target Capital Pool: $1,000 USDT (Targeting 75% - 300%+ Monthly ROI)
ACCOUNT_BALANCE = 1000.0

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
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Aggressive High-ROI Quant Scan Running ($1,000 USDT Account Base)...")

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
        signal = SureShotConfluenceEngine.evaluate_setup(df, sm, base_win_rate=0.55)

        if signal["is_sure_shot"]:
            # Format and send HIGH-CONFLUENCE SIGNAL ONLY
            direction = signal["direction"]
            entry = signal["entry_price"]
            
            # Tight Stop-Loss (1.2x ATR) for high position sizing & protection
            sl = entry - (signal["atr"] * 1.2) if direction == "LONG" else entry + (signal["atr"] * 1.2)
            tp = entry + (signal["atr"] * 3.6) if direction == "LONG" else entry - (signal["atr"] * 3.6)
            
            sl_pct = abs(entry - sl) / entry
            tp_pct = abs(tp - entry) / entry
            
            # High-Conviction Kelly Risk (3% to 5% of $1,000 = $30 to $50 risk)
            dollars_at_risk = ACCOUNT_BALANCE * min(max(signal["win_rate"] * 0.08, 0.03), 0.05)
            notional_position = dollars_at_risk / sl_pct
            
            # Post ~$100 USDT Isolated Margin per trade (10% of account equity)
            suggested_margin = 100.0
            suggested_leverage = max(round(notional_position / suggested_margin), 10)

            alert_msg = f"""
🚀 **ANTI GRAVITY AGGRESSIVE SIGNAL: {ticker}** 🚀
━━━━━━━━━━━━━━━━━━━━━━━━
• **Asset:** `{ticker}` ({item['asset_class']})
• **Target ROI Goal:** `$750 – $3,000+/mo (75%–300%)`
• **Account Equity:** `${ACCOUNT_BALANCE:,.2f} USDT`
• **Direction:** `{direction}`
• **Model Win Rate:** `{signal['win_rate']*100:.1f}%` | **EV:** `+{signal['expected_value']:.2f}`

📰 **CONFLUENCE DRIVERS**
• **Headline:** "{headline_text}"
• **NLP Sentiment:** `{sentiment['dominant_sentiment']}` ({sm}x Multiplier)
• **Technical Setup:** Structural Break + `{signal['fvg_detected']}`

⚡ **HIGH-LEVERAGE EXECUTION (Bitunix Isolated)**
• **Entry Price:** `${entry:,.4f}`
• **Tight Stop Loss:** `${sl:,.4f}` (-{sl_pct*100:.2f}%)
• **Take Profit Target:** `${tp:,.4f}` (+{tp_pct*100:.2f}%)
• **Recommended Leverage:** `{suggested_leverage}x Isolated`
• **Required Isolated Margin:** `${suggested_margin:,.2f} USDT` (10% Buffer)
• **Hard Risk (SL Exit):** `${dollars_at_risk:,.2f} USDT` ({dollars_at_risk/ACCOUNT_BALANCE*100:.1f}%)
• **Potential Gain (TP Exit):** `${dollars_at_risk * 3.0:,.2f} USDT` (+{dollars_at_risk * 3.0 / ACCOUNT_BALANCE * 100:.1f}%)
━━━━━━━━━━━━━━━━━━━━━━━━
            """
            telegram.send_alert(alert_msg)
            print(f"[✓] AGGRESSIVE SURE-SHOT ALERT DISPATCHED FOR {ticker}")

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
    print("🚀 Anti Gravity Aggressive Quant Scanner Active ($1,000 USDT Account Base, 5-min interval)...")
    try:
        while True:
            run_silent_quant_scan()
            time.sleep(300) # Scan quietly every 5 minutes
    except KeyboardInterrupt:
        print("\n[!] Scanner stopped by user.")
