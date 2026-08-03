# models/auto_scanner.py
import os
import sys
import time
import threading
import pandas as pd
import numpy as np
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv

# Import internal modules
sys.path.append(os.path.dirname(__file__))
from indicators.confluence_engine import SureShotConfluenceEngine
from indicators.advanced_quant import AdvancedQuantEngine
from position_monitor import ActivePositionMonitor
from news.news_fetcher import RealtimeNewsFetcher
from news.market_universe import DynamicMarketUniverse
from data.live_feed import RealtimeMarketDataFeed
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

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Anti Gravity Quant Scanner v3.0 Active & Operational 24/7")

    def log_message(self, format, *args):
        return # Suppress HTTP server logging

def start_health_server():
    port = int(os.getenv("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    print(f"[✓] Cloud Health Check Server listening on port {port}")
    server.serve_forever()

def run_continuous_quant_hunter():
    universe = DynamicMarketUniverse.get_full_hunting_universe()
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 🚀 DEN ENGINE v3.0 | Scanning {len(universe)} Assets Across Crypto, TradFi & Commodities...")

    # Fetch global news wire
    headlines = news_engine.fetch_latest_headlines(limit=2)
    headline_text = headlines[0]['headline'] if headlines else "Markets trading within active volatility bounds."
    sentiment = nlp.analyze_news(headline_text)
    sm = sentiment["sentiment_multiplier"]

    for item in universe:
        ticker = item["ticker"]
        asset_class = item["asset_class"]
        base_p = item.get("base_price", 100.0)
        
        # 1. Fetch Real-time Market OHLCV Data
        df = RealtimeMarketDataFeed.get_live_ohlcv(ticker, asset_class, base_p)
        if df is None or len(df) < 15:
            continue

        # 2. Advanced Quant Analysis (VWAP + Volatility Regimes)
        quant_meta = AdvancedQuantEngine.calculate_vwap_and_volatility(df)

        # 3. Check Open Trades for Invalidation / Early Exit First
        current_price = df.iloc[-1]['close']
        structure_flipped = df.iloc[-1]['close'] < df.iloc[-10]['low']
        monitor.check_active_positions(ticker, current_price, sm, structure_flipped)

        # 4. Evaluate High-Confluence Sure-Shot Opportunities
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
🎯 **DEN ENGINE v3.0 SURE-SHOT SIGNAL: {ticker}** 🎯
━━━━━━━━━━━━━━━━━━━━━━━━
• **Asset:** `{ticker}` ({asset_class})
• **Target ROI Goal:** `$750 – $3,000+/mo (75%–300%)`
• **Account Equity:** `${ACCOUNT_BALANCE:,.2f} USDT`
• **Direction:** `{direction}`
• **Model Win Rate:** `{signal['win_rate']*100:.1f}%` | **EV:** `+{signal['expected_value']:.2f}`

📰 **QUANT & VWAP DRIVERS**
• **Headline:** "{headline_text}"
• **NLP Sentiment:** `{sentiment['dominant_sentiment']}` ({sm}x Multiplier)
• **VWAP Benchmark:** `${signal['vwap']:,.4f}` (Aligned: `{signal['direction']}`)
• **Vol Regime:** `{quant_meta['volatility_regime']}`
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
    # Start Cloud Web Health Check Server in background thread
    threading.Thread(target=start_health_server, daemon=True).start()
    
    print("🚀 Anti Gravity Den Engine v3.0 Active (Continuous 24/7 Cloud Loop)...")
    try:
        while True:
            run_continuous_quant_hunter()
            time.sleep(10)
    except KeyboardInterrupt:
        print("\n[!] Scanner stopped by user.")
