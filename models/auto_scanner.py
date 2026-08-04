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
from indicators.duration_estimator import PrecisionDurationEstimator
from indicators.orderflow_imbalance import InstitutionalOrderFlowEngine
from indicators.anti_manipulation import InstitutionalAntiManipulationShield
from indicators.correlation_defense import CorrelationDefenseEngine
from indicators.regime_classifier import MarketRegimeClassifier
from indicators.exchange_leverage import ExchangeLeverageEngine
from indicators.deep_reasoning import DeepReasoningQuantEngine
from indicators.slippage_defense import InstitutionalSlippageDefense
from indicators.trailing_sl import DynamicBreakevenTrailingEngine
from portfolio.capital_defense import CapitalDefenseShield
from alerts.signal_cooldown import SignalCooldownEngine
from ml.self_learning import SelfLearningQuantEngine
from ml.internet_learning import InternetQuantLearningEngine
from ml.auto_upgrader import AutonomousSelfUpgraderDaemon
from news.macro_events import USMacroEventEngine
from news.regulatory_events import USRegulatoryPolicyEngine
from news.predictive_calendar import PredictiveMacroCalendarEngine
from news.emergency_wire import EmergencyMacroWireEngine
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
        self.wfile.write(b"Anti Gravity Quant Scanner v18.0 Autonomous Self-Upgrader Active 24/7")

    def log_message(self, format, *args):
        return

def start_health_server():
    port = int(os.getenv("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    print(f"[✓] Cloud Health Check Server listening on port {port}")
    server.serve_forever()

def run_continuous_quant_hunter():
    # 0. Trigger Autonomous Self-Upgrade Cycle
    upgrade_meta = AutonomousSelfUpgraderDaemon.execute_self_upgrade_cycle()
    learned_weights = upgrade_meta["weights"]
    universe = DynamicMarketUniverse.get_full_hunting_universe()

    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 🚀 DEN ENGINE v18.0 AUTONOMOUS SELF-UPGRADER | Scanning {len(universe)} Global Assets...")

    # Active Position Cap (7 Positions)
    active_positions = monitor.load_positions()
    if len(active_positions) >= 7:
        print(f"[🛡️] Multi-Position Cap Reached ({len(active_positions)} active trades). Scanner pausing new signal generation.")
        time.sleep(10)
        return

    emergency_meta = EmergencyMacroWireEngine.scan_emergency_wire()
    wire_multiplier = emergency_meta["wire_multiplier"]

    headlines = news_engine.fetch_latest_headlines(limit=2)
    headline_text = headlines[0]['headline'] if headlines else "Global macro markets trading within active momentum volatility."
    sentiment = nlp.analyze_news_ensemble(headline_text)
    sm = sentiment["sentiment_multiplier"] * learned_weights.get("sentiment_weight", 1.0)

    calendar_meta = PredictiveMacroCalendarEngine.analyze_upcoming_macro_events()
    cal_multiplier = calendar_meta["event_multiplier"]

    regulatory_meta = USRegulatoryPolicyEngine.analyze_regulatory_climate()
    reg_multiplier = regulatory_meta["regulatory_multiplier"]

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
        
        # 1. Continuous Position Defense
        if is_real:
            monitor.check_active_positions(ticker, current_price, sm, structure_flipped)

        # 2. Orderbook Spread & Slippage Protection
        slippage_meta = InstitutionalSlippageDefense.audit_spread_and_slippage(df)
        if slippage_meta["is_high_slippage"]:
            continue # Rejects illiquid spreads to protect execution!

        # 3. Strict 4-Hour Ticker Cooldown Audit
        if not SignalCooldownEngine.can_send_signal(ticker):
            continue

        # 4. Portfolio Correlation Overlap Defense
        if CorrelationDefenseEngine.check_correlation_overlap(ticker):
            continue

        # 5. Anti-Manipulation Shield Audit
        shield_meta = InstitutionalAntiManipulationShield.audit_manipulation(df)
        if shield_meta["is_manipulated"]:
            continue

        # 6. Velocity & Chop Filter
        velocity_meta = MomentumVelocityEngine.calculate_velocity(df)
        if velocity_meta["is_dead_chop"]:
            continue

        regime_meta = MarketRegimeClassifier.classify_regime(df)
        funding_meta = FundingRateDefenseEngine.get_funding_rate(ticker)
        poc_meta = InstitutionalVolumeProfile.calculate_poc(df)
        orderflow = InstitutionalOrderFlowEngine.analyze_orderflow(df)

        # 7. DEEP REASONING & MANIPULATION AUDIT
        preliminary_direction = "LONG" if df.iloc[-1]['close'] > df.iloc[-1]['ema_20'] if 'ema_20' in df.columns else True else "SHORT"
        reasoning_meta = DeepReasoningQuantEngine.audit_setup_authenticity(
            df, sm, orderflow["buy_ratio"], preliminary_direction
        )

        if not reasoning_meta["is_authentic_sure_shot"]:
            print(f"[🛡️] Deep Reasoning Blocked {ticker}: {reasoning_meta['reasoning_verdict']}")
            continue

        # 8. Evaluate High-Confluence 70%+ Win-Rate Opportunities
        effective_multiplier = sm * wire_multiplier * cal_multiplier * reg_multiplier * macro_multiplier * macro_meta["macro_score"] * funding_meta["squeeze_tailwind"] * shield_meta["shield_multiplier"] * reasoning_meta["authenticity_score"] * slippage_meta["slippage_score"]
        signal = SureShotConfluenceEngine.evaluate_setup(df, effective_multiplier, base_win_rate=learned_weights.get("base_win_rate", 0.58))

        if signal["is_sure_shot"] and is_real:
            direction = signal["direction"]
            entry = current_price
            
            risk_params = CapitalDefenseShield.get_dynamic_risk_params(ACCOUNT_BALANCE, signal["win_rate"])
            target_risk_usd = risk_params["dollars_at_risk"]

            sl = round(entry - (signal["atr"] * regime_meta["sl_multiplier"]) if direction == "LONG" else entry + (signal["atr"] * regime_meta["sl_multiplier"]), 2)
            tp = round(entry + (signal["atr"] * regime_meta["tp_multiplier"]) if direction == "LONG" else entry - (signal["atr"] * regime_meta["tp_multiplier"]), 2)
            
            sl_pct = abs(entry - sl) / entry
            tp_pct = abs(tp - entry) / entry
            
            duration_meta = PrecisionDurationEstimator.calculate_estimated_duration(
                entry, tp, signal["atr"], velocity_meta["velocity_ratio"]
            )

            # Bitunix & Weex Calibrated Leverage & Dynamic Margin Calculation
            raw_ideal_leverage = max(round(1.0 / max(sl_pct * 2.5, 0.01)), 15)
            lev_meta = ExchangeLeverageEngine.get_calibrated_leverage(ticker, raw_ideal_leverage)
            chosen_leverage = lev_meta["recommended_leverage"]

            # Dynamic Margin Sizing
            target_notional = target_risk_usd / max(sl_pct, 0.0005)
            calculated_margin = round(target_notional / chosen_leverage, 2)
            final_margin = min(max(calculated_margin, 20.0), 200.0)
            actual_notional = final_margin * chosen_leverage
            
            exact_loss_usd = round(actual_notional * sl_pct, 2)
            exact_gain_usd = round(actual_notional * tp_pct, 2)
            roi_gain_pct = round((exact_gain_usd / final_margin) * 100, 1)

            # REDESIGNED ULTRA-CLEAN PAYLOAD WITH AUTONOMOUS SELF-UPGRADED MODEL
            alert_msg = f"""
🎯 **SURE-SHOT SIGNAL: {ticker}** 🎯
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚡ **EXECUTION DATA (ENTRY CHEATSHEET)**
• **Asset & Exchange:** `{ticker}` ({lev_meta['primary_exchange']})
• **Direction:** `{direction}` 🚀
• **Entry Price:** `${entry:,.2f}`
• **Take Profit (TP):** `${tp:,.2f}` (+{tp_pct*100:.2f}%)  <-- SINGLE TP
• **Stop Loss (SL):** `${sl:,.2f}` (-{sl_pct*100:.2f}%)
• **Leverage:** `{chosen_leverage}x Isolated` (Max: `{lev_meta['max_exchange_leverage']}x`)
• **Required Margin:** `${final_margin:,.2f} USDT` (Notional: `${actual_notional:,.2f}`)
• **Est. Trade Horizon:** `{duration_meta['formatted_label']}` ⏱️
• **Hard Risk (Loss):** `-${exact_loss_usd:,.2f} USDT` (-{exact_loss_usd/ACCOUNT_BALANCE*100:.1f}% Equity)
• **Target Gain (Win):** `+${exact_gain_usd:,.2f} USDT` (+{roi_gain_pct}% Margin ROI)

🧠 **AUTONOMOUS SELF-UPGRADED QUANT DRIVERS**
• **Model Version:** `v18.0 Autonomous Self-Upgrader`
• **Deep Audit:** `{reasoning_meta['reasoning_verdict']}`
• **Model Win Rate:** `{signal['win_rate']*100:.1f}%` (EV: `+{signal['expected_value']:.2f}`)
• **Slippage Audit:** `{slippage_meta['estimated_spread_pct']}%` (Rec: `{slippage_meta['order_type_recommendation']}`)
• **Market Regime:** `{regime_meta['regime']}` (Vol Expansion: `{regime_meta['vol_expansion_ratio']}x`)
• **Taker Buy Orderflow:** `{orderflow['buy_ratio']}%` (Buying Influx)
• **Anti-Manipulation:** `{shield_meta['status']}`
• **US Regulatory Status:** `{regulatory_meta['regulatory_status']}`
• **Volume POC / VWAP:** `${poc_meta['poc']:,.2f}` / `${signal['vwap']:,.2f}` (Aligned: `{direction}`)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[✓] Autonomous 24/7 Self-Upgrading Model Active
[✓] Orderbook Spread & Slippage Protection Verified
            """
            telegram.send_alert(alert_msg)
            SignalCooldownEngine.record_signal_sent(ticker)
            print(f"[✓] v18.0 AUTONOMOUS SIGNAL DISPATCHED FOR {ticker}")

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
    print("🚀 Anti Gravity Den Engine v18.0 Autonomous Self-Upgrader Active (Continuous 24/7 Cloud Loop)...")
    try:
        while True:
            run_continuous_quant_hunter()
            time.sleep(10)
    except KeyboardInterrupt:
        print("\n[!] Scanner stopped by user.")
