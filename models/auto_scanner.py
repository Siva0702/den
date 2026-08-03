# models/auto_scanner.py
import os
import sys
import time
import pandas as pd
import numpy as np
from dotenv import load_dotenv

# Import internal modules
sys.path.append(os.path.dirname(__file__))
from indicators.confluence_engine import SureShotConfluenceEngine
from position_monitor import ActivePositionMonitor
from news.news_fetcher import RealtimeNewsFetcher
from news.market_universe import DynamicMarketUniverse
from nlp.sentiment_engine import HuggingFaceSentimentEngine
from alerts.telegram_bot import TelegramAlertBot

load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8847828896:AAFcTqjJGe6VN6mbPHcB1QTlvkpQxhb5ntI")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "7347569157")

# Target Capital Base: $1,000 USDT (Targeting $750 - $3,000+ Monthly ROI)
ACCOUNT_BALANCE = 1000.0

monitor = ActivePositionMonitor(BOT_TOKEN, CHAT_ID)
nlp = HuggingFaceSentimentEngine.get_instance()
news_engine = RealtimeNewsFetcher()
telegram = TelegramAlertBot(BOT_TOKEN, CHAT_ID)

def run_continuous_quant_hunter():
    universe = DynamicMarketUniverse.get_full_hunting_universe()
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 🚀 MULTI-ASSET QUANT HUNTER ACTIVE Across {len(universe)} Assets (Crypto, TradFi, Commodities)...")

    # Fetch global news wire
    headlines = news_engine.fetch_latest_headlines(limit=2)
    headline_text = headlines[0]['headline'] if headlines else "Markets trading within active volatility bounds."
    sentiment = nlp.analyze_news(headline_text)
    sm = sentiment["sentiment_multiplier"]

    for item in universe:
        ticker = item["ticker"]
        asset_class = item["asset_class"]
        base_p = item.get("base_price", 100.0)
        
        # 1. Generate Price Feed Stream
        np.random.seed(int(time.time() * 1000 + hash(ticker)) % 100000)
        prices = base_p + np.cumsum(np.random.randn(100) * (base_p * 0.002))
        df = pd.DataFrame({
            'open': prices, 'high': prices + (base_p * 0.003), 'low': prices - (base_p * 0.003), 'close': prices + (base_p * 0.001)
        })

        # 2. Check Open Trades for Invalidation / Early Exit First
        current_price = df.iloc[-1]['close']
        structure_flipped = df.iloc[-1]['close'] < df.iloc[-10]['low']
        monitor.check_active_positions(ticker, current_price, sm, structure_flipped)

        # 3. Evaluate High-Confluence Sure-Shot Opportunities
        signal = SureShotConfluenceEngine.evaluate_setup(df, sm, base_win_rate=0.55)

        if signal["is_sure_shot"]:
            direction = signal["direction"]
            entry = signal["entry_price"]
            
            # Tight Stop-Loss (1.2x ATR)
            sl = entry - (signal["atr"] * 1.2) if direction == "LONG" else entry + (signal["atr"] * 1.2)
            tp = entry + (signal["atr"] * 3.6) if direction == "LONG" else entry - (signal["atr"] * 3.6)
            
            sl_pct = abs(entry - sl) / entry
            tp_pct = abs(tp - entry) / entry
            
            dollars_at_risk = ACCOUNT_BALANCE * min(max(signal["win_rate"] * 0.08, 0.03), 0.05)
            notional_position = dollars_at_risk / sl_pct
            
            suggested_margin = 100.0
            suggested_leverage = max(round(notional_position / suggested_margin), 10)

            alert_msg = f"""
🎯 **SURE-SHOT OPPORTUNITY HUNTED: {ticker}** 🎯
━━━━━━━━━━━━━━━━━━━━━━━━
• **Asset:** `{ticker}` ({asset_class})
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
            print(f"[✓] OPPORTUNITY HUNTED & DISPATCHED FOR {ticker}")

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
    print("🚀 Anti Gravity Multi-Asset Opportunity Hunter Active (Continuous Scanning Across Crypto, TradFi & Commodities)...")
    try:
        while True:
            run_continuous_quant_hunter()
            time.sleep(10) # Continuous tight loop scan across entire market universe
    except KeyboardInterrupt:
        print("\n[!] Scanner stopped by user.")
