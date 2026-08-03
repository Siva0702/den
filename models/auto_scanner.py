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
from indicators.macro_regime import MacroRegimeFilter
from indicators.volume_profile import InstitutionalVolumeProfile
from news.macro_events import USMacroEventEngine
from position_monitor import ActivePositionMonitor
from audit.track_record import PerformanceTrackRecord
from news.news_fetcher import RealtimeNewsFetcher
from news.market_universe import DynamicMarketUniverse
from data.live_feed import RealtimeMarketDataFeed
from nlp.ensemble_sentiment import HuggingFaceEnsembleSentimentEngine
from alerts.telegram_bot import TelegramAlertBot

load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8847828896:AAFcTqjJGe6VN6mbPHcB1QTlvkpQxhb5ntI")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "7347569157")

ACCOUNT_BALANCE = 1000.0

monitor = ActivePositionMonitor(BOT_TOKEN, CHAT_ID)
nlp = HuggingFaceEnsembleSentimentEngine.get_instance()
news_engine = RealtimeNewsFetcher()
telegram = TelegramAlertBot(BOT_TOKEN, CHAT_ID)

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Anti Gravity Quant Scanner v7.0 Apex 360 Active & Operational 24/7")

    def log_message(self, format, *args):
        return

def start_health_server():
    port = int(os.getenv("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    print(f"[✓] Cloud Health Check Server listening on port {port}")
    server.serve_forever()

def run_continuous_quant_hunter():
    universe = DynamicMarketUniverse.get_full_hunting_universe()
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 🚀 DEN ENGINE v7.0 APEX 360 | Scanning {len(universe)} Global Assets Across Macro, Fed, Volume POC & SMC...")

    # 1. Fetch Global Macro Wire & Hugging Face Multi-Model Ensemble Sentiment
    headlines = news_engine.fetch_latest_headlines(limit=2)
    headline_text = headlines[0]['headline'] if headlines else "Global macro markets trading within active momentum volatility."
    sentiment = nlp.analyze_news_ensemble(headline_text)
    sm = sentiment["sentiment_multiplier"]

    # 2. Fetch US Macro Key Event Multiplier
    macro_events = USMacroEventEngine.get_macro_event_multiplier()
    macro_multiplier = macro_events["macro_multiplier"]

    # 3. Fetch SPY Benchmark Data
    spy_df = RealtimeMarketDataFeed.get_live_ohlcv("SPY/USDT", "Macro Benchmark", 540.0)
    macro_meta = MacroRegimeFilter.evaluate_macro_trend(spy_df)

    for item in universe:
        ticker = item["ticker"]
        asset_class = item["asset_class"]
        sector = item.get("sector", "Global")
        base_p = item.get("base_price", 100.0)
        
        # 4. Fetch Real-time Live Market Chart
        df = RealtimeMarketDataFeed.get_live_ohlcv(ticker, asset_class, base_p)
        if df is None or len(df) < 15:
            continue

        quant_meta = AdvancedQuantEngine.calculate_vwap_and_volatility(df)
        poc_meta = InstitutionalVolumeProfile.calculate_poc(df)

        current_price = round(df.iloc[-1]['close'], 2)
        structure_flipped = df.iloc[-1]['close'] < df.iloc[-10]['low']
        
        # 5. Continuous Active Position Defense & TP/SL Monitoring
        monitor.check_active_positions(ticker, current_price, sm, structure_flipped)

        # 6. Evaluate High-Confluence 360 Sure-Shot Opportunities
        effective_multiplier = sm * macro_multiplier * macro_meta["macro_score"]
        signal = SureShotConfluenceEngine.evaluate_setup(df, effective_multiplier, base_win_rate=0.58)

        if signal["is_sure_shot"]:
            direction = signal["direction"]
            entry = current_price
            
            # Single TP Target (3.0x ATR for clean 1:3 R:R ratio)
            sl = round(entry - (signal["atr"] * 1.2) if direction == "LONG" else entry + (signal["atr"] * 1.2), 2)
            tp = round(entry + (signal["atr"] * 3.6) if direction == "LONG" else entry - (signal["atr"] * 3.6), 2)
            
            sl_pct = abs(entry - sl) / entry
            tp_pct = abs(tp - entry) / entry
            
            dollars_at_risk = 35.0
            notional_position = dollars_at_risk / max(sl_pct, 0.001)
            suggested_margin = 100.0
            suggested_leverage = max(round(notional_position / suggested_margin), 15)

            alert_msg = f"""
🎯 **DEN ENGINE v7.0 APEX 360 SIGNAL: {ticker}** 🎯
━━━━━━━━━━━━━━━━━━━━━━━━
• **Asset:** `{ticker}` ({sector})
• **Setup Type:** `1-HOUR INTRADAY SCALP`
• **Account Equity:** `${ACCOUNT_BALANCE:,.2f} USDT`
• **Direction:** `{direction}` 🚀
• **Model Win Rate:** `{signal['win_rate']*100:.1f}%` | **EV:** `+{signal['expected_value']:.2f}`

🌐 **360 MACRO, FED & QUANT DRIVERS**
• **Headline:** "{headline_text}"
• **HF Sentiment:** `{sentiment['dominant_sentiment']}` ({sm}x Multiplier)
• **US Macro Events:** `{macro_events['macro_event_impact']}` ({macro_multiplier}x Multiplier)
• **Macro Index Bias:** `{macro_meta['macro_bias']}` (SPY: `${macro_meta['spy_price']:,.2f}`)
• **Volume POC:** `${poc_meta['poc']:,.2f}` (Aligned: `{direction}`)
• **VWAP Benchmark:** `${signal['vwap']:,.2f}` (Aligned: `{direction}`)
• **Vol Regime:** `{quant_meta['volatility_regime']}`

⚡ **HIGH-LEVERAGE EXECUTION (Bitunix Isolated)**
• **Entry Price:** `${entry:,.2f}`
• **Stop Loss (SL):** `${sl:,.2f}` (-{sl_pct*100:.2f}%)
• **Take Profit (TP):** `${tp:,.2f}` (+{tp_pct*100:.2f}%)  <-- SINGLE TP
• **Recommended Leverage:** `{suggested_leverage}x Isolated`
• **Required Margin:** `${suggested_margin:,.2f} USDT` (10% Buffer)
• **Hard Risk (SL Exit):** `${dollars_at_risk:,.2f} USDT` (3.5%)
• **Potential Gain (TP Exit):** `${dollars_at_risk * 3.0:,.2f} USDT` (+10.5% Account Gain)
━━━━━━━━━━━━━━━━━━━━━━━━
[✓] Auto-Tracked 24/7 in Cloud Engine for TP/SL Hit Notification
            """
            telegram.send_alert(alert_msg)
            print(f"[✓] v7.0 APEX 360 SIGNAL DISPATCHED FOR {ticker}")

            # Register position for 24/7 TP/SL Hit Monitoring
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
            
            # Log into Dual-Track Audit File
            PerformanceTrackRecord.log_trade_signal(
                ticker, direction, entry, sl, tp, signal["win_rate"], signal["expected_value"], user_taken=False
            )

if __name__ == "__main__":
    threading.Thread(target=start_health_server, daemon=True).start()
    print("🚀 Anti Gravity Den Engine v7.0 Apex 360 Active (Continuous 24/7 Cloud Loop)...")
    try:
        while True:
            run_continuous_quant_hunter()
            time.sleep(10)
    except KeyboardInterrupt:
        print("\n[!] Scanner stopped by user.")
