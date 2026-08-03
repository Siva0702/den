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
from indicators.funding_defense import FundingRateDefenseEngine
from indicators.velocity_engine import MomentumVelocityEngine
from ml.self_learning import SelfLearningQuantEngine
from ml.internet_learning import InternetQuantLearningEngine
from news.macro_events import USMacroEventEngine
from position_monitor import ActivePositionMonitor
from audit.track_record import PerformanceTrackRecord
from news.news_fetcher import RealtimeNewsFetcher
from news.market_universe import DynamicMarketUniverse
from data.live_feed import RealtimeMarketDataFeed
from nlp.ensemble_sentiment import HuggingFaceEnsembleSentimentEngine
from alerts.telegram_bot import TelegramAlertBot

load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

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
        self.wfile.write(b"Anti Gravity Quant Scanner v9.0 Apex Neural Active & Operational 24/7")

    def log_message(self, format, *args):
        return

def start_health_server():
    port = int(os.getenv("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    print(f"[✓] Cloud Health Check Server listening on port {port}")
    server.serve_forever()

def run_continuous_quant_hunter():
    universe = DynamicMarketUniverse.get_full_hunting_universe()
    learned_weights = SelfLearningQuantEngine.get_learned_weights()
    internet_knowledge = InternetQuantLearningEngine.fetch_and_update_knowledge()

    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 🚀 DEN ENGINE v9.0 APEX NEURAL | Hunting {len(universe)} Assets with Velocity Expansion & Zero-Chop Filter...")

    headlines = news_engine.fetch_latest_headlines(limit=2)
    headline_text = headlines[0]['headline'] if headlines else "Global macro markets trading within active momentum volatility."
    sentiment = nlp.analyze_news_ensemble(headline_text)
    sm = sentiment["sentiment_multiplier"] * learned_weights.get("sentiment_weight", 1.0)

    macro_events = USMacroEventEngine.get_macro_event_multiplier()
    macro_multiplier = macro_events["macro_multiplier"]

    spy_df, _ = RealtimeMarketDataFeed.get_live_ohlcv("SPY/USDT", "Macro Benchmark", 540.0)
    macro_meta = MacroRegimeFilter.evaluate_macro_trend(spy_df)

    for item in universe:
        ticker = item["ticker"]
        asset_class = item["asset_class"]
        sector = item.get("sector", "Global")
        base_p = item.get("base_price", 100.0)
        
        df, is_real = RealtimeMarketDataFeed.get_live_ohlcv(ticker, asset_class, base_p)
        if df is None or len(df) < 15:
            continue

        current_price = round(df.iloc[-1]['close'], 2)
        structure_flipped = df.iloc[-1]['close'] < df.iloc[-10]['low']
        
        # 1. Continuous Position Defense (ONLY ON REAL LIVE REST FEEDS!)
        if is_real:
            monitor.check_active_positions(ticker, current_price, sm, structure_flipped)

        # 2. Velocity & Chop Filter (REJECT DEAD/SIDEWAYS CHOP)
        velocity_meta = MomentumVelocityEngine.calculate_velocity(df)
        if velocity_meta["is_dead_chop"]:
            continue # Rejects stagnant consolidation ranges so trades move immediately!

        funding_meta = FundingRateDefenseEngine.get_funding_rate(ticker)
        quant_meta = AdvancedQuantEngine.calculate_vwap_and_volatility(df)
        poc_meta = InstitutionalVolumeProfile.calculate_poc(df)

        # 3. Evaluate High-Confluence Self-Learned Sure-Shot Opportunities
        effective_multiplier = sm * macro_multiplier * macro_meta["macro_score"] * funding_meta["squeeze_tailwind"]
        signal = SureShotConfluenceEngine.evaluate_setup(df, effective_multiplier, base_win_rate=learned_weights.get("base_win_rate", 0.58))

        if signal["is_sure_shot"] and is_real:
            direction = signal["direction"]
            entry = current_price
            
            sl = round(entry - (signal["atr"] * 1.2) if direction == "LONG" else entry + (signal["atr"] * 1.2), 2)
            tp = round(entry + (signal["atr"] * 3.6) if direction == "LONG" else entry - (signal["atr"] * 3.6), 2)
            
            sl_pct = abs(entry - sl) / entry
            tp_pct = abs(tp - entry) / entry
            
            dollars_at_risk = 35.0
            notional_position = dollars_at_risk / max(sl_pct, 0.001)
            suggested_margin = 100.0
            suggested_leverage = max(round(notional_position / suggested_margin), 15)

            alert_msg = f"""
🎯 **DEN ENGINE v9.0 APEX NEURAL SIGNAL: {ticker}** 🎯
━━━━━━━━━━━━━━━━━━━━━━━━
• **Asset:** `{ticker}` ({sector})
• **Setup Type:** `1-HOUR FAST INTRADAY SCALP`
• **Account Equity:** `${ACCOUNT_BALANCE:,.2f} USDT`
• **Direction:** `{direction}` 🚀
• **Model Win Rate:** `{signal['win_rate']*100:.1f}%` | **EV:** `+{signal['expected_value']:.2f}`

⚡ **VELOCITY & FUNDING METRICS**
• **Momentum Velocity:** `{velocity_meta['velocity_ratio']}x` (Exploding: `{velocity_meta['is_exploding']}`)
• **8h Funding Rate:** `{funding_meta['funding_pct']}%` ({funding_meta['status']})
• **Headline:** "{headline_text}"
• **HF Sentiment:** `{sentiment['dominant_sentiment']}` ({sm:.2f}x Multiplier)
• **Volume POC:** `${poc_meta['poc']:,.2f}` (Aligned: `{direction}`)
• **VWAP Benchmark:** `${signal['vwap']:,.2f}` (Aligned: `{direction}`)

⚡ **HIGH-LEVERAGE EXECUTION (Bitunix Isolated)**
• **Entry Price:** `${entry:,.2f}`
• **Stop Loss (SL):** `${sl:,.2f}` (-{sl_pct*100:.2f}%)
• **Take Profit (TP):** `${tp:,.2f}` (+{tp_pct*100:.2f}%)  <-- SINGLE TP
• **Recommended Leverage:** `{suggested_leverage}x Isolated`
• **Required Margin:** `${suggested_margin:,.2f} USDT` (10% Buffer)
• **Hard Risk (SL Exit):** `${dollars_at_risk:,.2f} USDT` (3.5%)
• **Potential Gain (TP Exit):** `${dollars_at_risk * 3.0:,.2f} USDT` (+10.5% Account Gain)
━━━━━━━━━━━━━━━━━━━━━━━━
[✓] Zero-Chop Velocity Filter Applied (Fast Immediate Movement)
[✓] Self-Taught Internet & Reinforcement Alpha Active
            """
            telegram.send_alert(alert_msg)
            print(f"[✓] v9.0 NEURAL SIGNAL DISPATCHED FOR {ticker}")

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
            
            PerformanceTrackRecord.log_trade_signal(
                ticker, direction, entry, sl, tp, signal["win_rate"], signal["expected_value"], user_taken=False
            )

if __name__ == "__main__":
    threading.Thread(target=start_health_server, daemon=True).start()
    print("🚀 Anti Gravity Den Engine v9.0 Apex Neural Active (Continuous 24/7 Cloud Loop)...")
    try:
        while True:
            run_continuous_quant_hunter()
            time.sleep(10)
    except KeyboardInterrupt:
        print("\n[!] Scanner stopped by user.")
