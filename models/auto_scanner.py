# models/auto_scanner.py
import os
import sys
import time
import threading
import requests
import json
import pandas as pd
import numpy as np
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv

# Ensure instant unbuffered log flushing for Render Cloud
sys.stdout.reconfigure(line_buffering=True)

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
from indicators.smc_confluence import InstitutionalSMCConfluenceEngine
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

def format_price_dynamic(price: float) -> str:
    if price < 0.0001:
        return f"${price:.8f}"
    elif price < 0.01:
        return f"${price:.6f}"
    elif price < 1.0:
        return f"${price:.4f}"
    elif price < 10.0:
        return f"${price:.3f}"
    else:
        return f"${price:,.2f}"

def format_price_raw(price: float) -> float:
    if price < 0.0001:
        return round(price, 8)
    elif price < 0.01:
        return round(price, 6)
    elif price < 1.0:
        return round(price, 4)
    elif price < 10.0:
        return round(price, 3)
    else:
        return round(price, 2)

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Anti Gravity Quant Scanner v25.0 Final Production Engine Active 24/7")

    def log_message(self, format, *args):
        return

def start_health_server():
    try:
        port = int(os.getenv("PORT", 10000))
        server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
        print(f"[✓] Render Cloud Server listening on port {port} (Main HTTP Process Active 24/7)", flush=True)
        server.serve_forever()
    except Exception as e:
        print(f"[!] Health server exception: {e}", flush=True)

def self_ping_keep_alive():
    url = "https://den-quant-scanner.onrender.com/"
    while True:
        time.sleep(120) # Ping every 2 mins to keep container 100% active
        try:
            requests.get(url, timeout=10)
        except Exception:
            pass

def run_continuous_quant_hunter():
    try:
        upgrade_meta = AutonomousSelfUpgraderDaemon.execute_self_upgrade_cycle()
        learned_weights = upgrade_meta["weights"] if isinstance(upgrade_meta, dict) else {}
        universe = DynamicMarketUniverse.get_full_hunting_universe()

        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 🚀 DEN ENGINE v25.0 PRODUCTION | Scanning {len(universe)} Global Assets...", flush=True)

        active_positions = monitor.load_positions()
        if len(active_positions) >= 7:
            print(f"[🛡️] Multi-Position Cap Reached ({len(active_positions)} active trades). Scanner pausing new signal generation.", flush=True)
            return

        emergency_meta = EmergencyMacroWireEngine.scan_emergency_wire()
        wire_multiplier = emergency_meta["wire_multiplier"]

        headlines = news_engine.fetch_latest_headlines(limit=2)
        headline_text = headlines[0]['headline'] if headlines else "Global macro markets trading within active momentum volatility."
        sentiment = nlp.analyze_news_ensemble(headline_text)
        sm = sentiment["sentiment_multiplier"] * learned_weights.get("sentiment_weight", 1.0) if isinstance(learned_weights, dict) else sentiment["sentiment_multiplier"]

        calendar_meta = PredictiveMacroCalendarEngine.analyze_upcoming_macro_events()
        cal_multiplier = calendar_meta["event_multiplier"]

        regulatory_meta = USRegulatoryPolicyEngine.analyze_regulatory_climate()
        reg_multiplier = regulatory_meta["regulatory_multiplier"]

        macro_events = USMacroEventEngine.get_macro_event_multiplier()
        macro_multiplier = macro_events["macro_multiplier"]

        spy_df, _ = RealtimeMarketDataFeed.get_live_ohlcv("SPY/USDT", "Macro Benchmark", 540.0)
        macro_meta = MacroRegimeFilter.evaluate_macro_trend(spy_df)

        for item in universe:
            try:
                ticker = item["ticker"]
                asset_class = item["asset_class"]
                sector = item.get("sector", "Global")
                base_p = item.get("base_price", 100.0)
                
                df, is_real = RealtimeMarketDataFeed.get_live_ohlcv(ticker, asset_class, base_p)
                if df is None or len(df) < 15:
                    continue

                raw_current_price = float(df.iloc[-1]['close'])
                current_price = format_price_raw(raw_current_price)
                structure_flipped = df.iloc[-1]['close'] < df.iloc[-10]['low']
                
                if is_real:
                    monitor.check_active_positions(ticker, current_price, sm, structure_flipped)

                slippage_meta = InstitutionalSlippageDefense.audit_spread_and_slippage(df)
                if slippage_meta["is_high_slippage"]:
                    continue

                # OUTCOME-BASED COOLDOWN (Suppresses ticker ONLY while position is open)
                if not SignalCooldownEngine.can_send_signal(ticker):
                    continue

                if CorrelationDefenseEngine.check_correlation_overlap(ticker):
                    continue

                shield_meta = InstitutionalAntiManipulationShield.audit_manipulation(df)
                if shield_meta["is_manipulated"]:
                    continue

                velocity_meta = MomentumVelocityEngine.calculate_velocity(df)
                if velocity_meta["is_dead_chop"]:
                    continue

                regime_meta = MarketRegimeClassifier.classify_regime(df)
                funding_meta = FundingRateDefenseEngine.get_funding_rate(ticker)
                poc_meta = InstitutionalVolumeProfile.calculate_poc(df)
                orderflow = InstitutionalOrderFlowEngine.analyze_orderflow(df)

                has_ema = 'ema_20' in df.columns
                is_above_ema = df.iloc[-1]['close'] > df.iloc[-1]['ema_20'] if has_ema else df.iloc[-1]['close'] > df.iloc[-1]['open']
                preliminary_direction = "LONG" if is_above_ema else "SHORT"
                
                reasoning_meta = DeepReasoningQuantEngine.audit_setup_authenticity(
                    df, sm, orderflow["buy_ratio"], preliminary_direction
                )

                if not reasoning_meta["is_authentic_sure_shot"]:
                    continue

                smc_meta = InstitutionalSMCConfluenceEngine.audit_smc_confluence(df)
                
                base_wr_setting = (learned_weights.get("base_win_rate", 0.62) + smc_meta["win_rate_boost"]) if isinstance(learned_weights, dict) else (0.62 + smc_meta["win_rate_boost"])
                effective_multiplier = sm * wire_multiplier * cal_multiplier * reg_multiplier * macro_multiplier * macro_meta["macro_score"] * funding_meta["squeeze_tailwind"] * shield_meta["shield_multiplier"] * reasoning_meta["authenticity_score"] * slippage_meta["slippage_score"]
                
                signal = SureShotConfluenceEngine.evaluate_setup(df, effective_multiplier, base_win_rate=base_wr_setting)

                # STRICT 75.0%+ WIN RATE GATE FOR PRISTINE A+ SETUPS
                if signal["is_sure_shot"] and signal["win_rate"] >= 0.75 and is_real:
                    direction = signal["direction"]
                    entry = current_price
                    
                    risk_params = CapitalDefenseShield.get_dynamic_risk_params(ACCOUNT_BALANCE, signal["win_rate"])
                    target_risk_usd = risk_params["dollars_at_risk"]

                    # DYNAMIC PRECISION SL & TP CALCULATION
                    sl_multiplier = max(regime_meta["sl_multiplier"], shield_meta["sl_buffer_atr"])
                    sl_dist = max(signal["atr"] * sl_multiplier, entry * 0.008)
                    tp_dist = sl_dist * 3.0 # EXACT 1:3.0 KELLY PAYOUT

                    raw_sl = entry - sl_dist if direction == "LONG" else entry + sl_dist
                    raw_tp = entry + tp_dist if direction == "LONG" else entry - tp_dist
                    
                    sl = format_price_raw(raw_sl)
                    tp = format_price_raw(raw_tp)
                    
                    sl_pct = abs(entry - sl) / entry
                    tp_pct = abs(tp - entry) / entry
                    
                    if sl_pct < 0.001:
                        continue

                    rr_ratio = round(tp_pct / sl_pct, 2)
                    
                    duration_meta = PrecisionDurationEstimator.calculate_estimated_duration(
                        entry, tp, signal["atr"], velocity_meta["velocity_ratio"]
                    )

                    raw_ideal_leverage = max(round(1.0 / max(sl_pct * 2.5, 0.01)), 15)
                    lev_meta = ExchangeLeverageEngine.get_calibrated_leverage(ticker, raw_ideal_leverage)
                    chosen_leverage = lev_meta["recommended_leverage"]

                    # EXACT MATHEMATICAL KELLY PnL AND MARGIN SIZING
                    final_margin = round(target_risk_usd / max(chosen_leverage * sl_pct, 0.0001), 2)
                    actual_notional = round(final_margin * chosen_leverage, 2)
                    
                    exact_loss_usd = round(actual_notional * sl_pct, 2)
                    exact_gain_usd = round(actual_notional * tp_pct, 2)
                    roi_gain_pct = round((exact_gain_usd / final_margin) * 100, 1)

                    alert_msg = f"""
🎯 **SURE-SHOT SIGNAL: {ticker}** 🎯
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚡ **EXECUTION DATA (ENTRY CHEATSHEET)**
• **Asset & Exchange:** `{ticker}` ({lev_meta['primary_exchange']})
• **Direction:** `{direction}` 🚀
• **Entry Price:** `{format_price_dynamic(entry)}`
• **Take Profit (TP):** `{format_price_dynamic(tp)}` (+{tp_pct*100:.2f}%)  <-- SINGLE TP (1:3.0 R:R)
• **Stop Loss (SL):** `{format_price_dynamic(sl)}` (-{sl_pct*100:.2f}%)  [Wide Liquidity Shield]
• **Leverage:** `{chosen_leverage}x Isolated` (Max: `{lev_meta['max_exchange_leverage']}x`)
• **Required Margin:** `${final_margin:,.2f} USDT` (Notional: `${actual_notional:,.2f}`)
• **Risk-to-Reward:** `1:{rr_ratio:.2f}` (Kelly Payout Target)
• **Est. Trade Horizon:** `{duration_meta['formatted_label']}` ⏱️
• **Hard Risk (Loss):** `-${exact_loss_usd:,.2f} USDT` (-{exact_loss_usd/ACCOUNT_BALANCE*100:.1f}% Equity)
• **Target Gain (Win):** `+${exact_gain_usd:,.2f} USDT` (+{roi_gain_pct}% Margin ROI)

🧠 **SMC & MULTI-TIMEFRAME QUANT DRIVERS**
• **Model Version:** `v25.0 Final Production Engine`
• **SMC Setup:** `{smc_meta['smc_setup_type']}`
• **Model Win Rate:** `{signal['win_rate']*100:.1f}%` (Strict 75%+ Non-Negotiable Gate)
• **Stop-Hunt Defense:** `{shield_meta['status']}` (Wide SL Buffer: `{sl_multiplier}x ATR`)
• **Deep Audit:** `{reasoning_meta['reasoning_verdict']}`
• **Market Regime:** `{regime_meta['regime']}` (Vol Expansion: `{regime_meta['vol_expansion_ratio']}x`)
• **Taker Buy Orderflow:** `{orderflow['buy_ratio']}%` (Buying Influx)
• **US Regulatory Status:** `{regulatory_meta['regulatory_status']}`
• **Volume POC / VWAP:** `${poc_meta['poc']:,.2f}` / `${signal['vwap']:,.2f}` (Aligned: `{direction}`)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[✓] Smart Money Concepts (SMC) Fair Value Gap & Multi-Timeframe Alignment Verified
[✓] Market Maker Stop-Hunt & Liquidity Sweep Protection Active
                    """
                    
                    is_dispatched = telegram.send_alert(alert_msg)
                    if is_dispatched:
                        SignalCooldownEngine.record_signal_sent(ticker)
                        print(f"[✓] v25.0 SIGNAL DELIVERED SUCCESSFULLY TO TELEGRAM FOR {ticker}", flush=True)

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
            except Exception as item_err:
                print(f"[!] Error scanning {item.get('ticker')}: {item_err}", flush=True)
                continue
    except Exception as loop_err:
        print(f"[!] Error in quant hunter loop: {loop_err}", flush=True)

def start_background_scanner_loop():
    print("🚀 Starting Den Engine v25.0 Dedicated Background Scanner Thread...", flush=True)
    while True:
        try:
            run_continuous_quant_hunter()
        except Exception as e:
            print(f"[!] Exception in background scanner loop: {e}", flush=True)
        time.sleep(10)

if __name__ == "__main__":
    # 1. Start continuous 24/7 background quant scanner thread
    threading.Thread(target=start_background_scanner_loop, daemon=True).start()
    # 2. Start continuous keep-alive ping thread
    threading.Thread(target=self_ping_keep_alive, daemon=True).start()
    # 3. Main process serves HTTP health server so Render Web Service NEVER sleeps or suspends!
    print("🚀 Den Engine v25.0 Serving Main Process HTTP Health Server on Render Cloud...", flush=True)
    start_health_server()
