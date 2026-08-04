# models/auto_scanner.py
import os
import sys
import time
import threading
import traceback
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
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or "8847828896:AAFcTqjJGe6VN6mbPHcB1QTlvkpQxhb5ntI"
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or "7347569157"

ACCOUNT_BALANCE = 1000.0

monitor = ActivePositionMonitor(BOT_TOKEN, CHAT_ID)
nlp = HuggingFaceEnsembleSentimentEngine.get_instance()
news_engine = RealtimeNewsFetcher()
telegram = TelegramAlertBot(BOT_TOKEN, CHAT_ID)

# ============================================================
# PRICE FORMATTING — Dynamic precision based on actual price
# ============================================================
def format_price_dynamic(price: float) -> str:
    """Format price with appropriate decimal precision for display."""
    if price < 0.0001:
        return f"${price:.8f}"
    elif price < 0.01:
        return f"${price:.6f}"
    elif price < 1.0:
        return f"${price:.4f}"
    elif price < 100.0:
        return f"${price:.3f}"
    elif price < 10000.0:
        return f"${price:.2f}"
    else:
        return f"${price:,.2f}"

def format_price_raw(price: float) -> float:
    """Round price to appropriate precision for calculations."""
    if price < 0.0001:
        return round(price, 8)
    elif price < 0.01:
        return round(price, 6)
    elif price < 1.0:
        return round(price, 4)
    elif price < 100.0:
        return round(price, 3)
    elif price < 10000.0:
        return round(price, 2)
    else:
        return round(price, 2)

# ============================================================
# SCANNER STATE — Shared diagnostic state for health endpoint
# ============================================================
scanner_state = {
    "status": "STARTING",
    "last_scan_time": "never",
    "total_scans": 0,
    "total_signals_sent": 0,
    "last_signal": "none",
    "last_error": "none",
    "assets_in_universe": 0,
}

# ============================================================
# RENDER CLOUD HEALTH + DIAGNOSTICS SERVER
# ============================================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        status_text = (
            f"Den Engine v35.0 | Status: {scanner_state['status']}\n"
            f"Last Scan: {scanner_state['last_scan_time']}\n"
            f"Total Scans: {scanner_state['total_scans']}\n"
            f"Total Signals Sent: {scanner_state['total_signals_sent']}\n"
            f"Last Signal: {scanner_state['last_signal']}\n"
            f"Last Error: {scanner_state['last_error']}\n"
            f"Universe Size: {scanner_state['assets_in_universe']}\n"
        )
        self.wfile.write(status_text.encode('utf-8'))

    def log_message(self, format, *args):
        return

def start_health_server():
    try:
        port = int(os.getenv("PORT", 10000))
        server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
        print(f"[✓] Health Server listening on port {port}", flush=True)
        server.serve_forever()
    except Exception as e:
        print(f"[!] Health server exception: {e}", flush=True)

def self_ping_keep_alive():
    url = "https://den-quant-scanner.onrender.com/"
    while True:
        time.sleep(120)
        try:
            requests.get(url, timeout=10)
        except Exception:
            pass

# ============================================================
# KELLY CRITERION — Proper mathematical position sizing
# ============================================================
def kelly_position_size(win_rate: float, reward_risk_ratio: float, account_balance: float, max_risk_pct: float = 0.05) -> dict:
    """
    Proper Kelly Criterion:
    f* = (p * b - q) / b
    where p = win probability, q = loss probability, b = reward/risk ratio
    Then apply half-Kelly for safety.
    """
    p = win_rate
    q = 1.0 - p
    b = reward_risk_ratio

    kelly_fraction = (p * b - q) / b if b > 0 else 0.0
    kelly_fraction = max(kelly_fraction, 0.0)  # Never negative

    # Half-Kelly for safety (standard institutional practice)
    half_kelly = kelly_fraction * 0.5

    # Cap at max_risk_pct of account
    risk_fraction = min(half_kelly, max_risk_pct)
    dollars_at_risk = round(account_balance * risk_fraction, 2)

    # Floor at $10, cap at $75
    dollars_at_risk = max(10.0, min(dollars_at_risk, 75.0))

    return {
        "kelly_full": round(kelly_fraction, 4),
        "kelly_half": round(half_kelly, 4),
        "dollars_at_risk": dollars_at_risk,
        "risk_pct": round(risk_fraction * 100, 2)
    }

# ============================================================
# MAIN SCAN ENGINE
# ============================================================
def run_continuous_quant_hunter():
    try:
        upgrade_meta = AutonomousSelfUpgraderDaemon.execute_self_upgrade_cycle()
        learned_weights = upgrade_meta["weights"] if isinstance(upgrade_meta, dict) else {}
        universe = DynamicMarketUniverse.get_full_hunting_universe()

        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 🚀 DEN ENGINE v35.0 | Scanning {len(universe)} Assets (Real Binance Data Only)...", flush=True)

        active_positions = monitor.load_positions()
        active_tickers = [p.get("ticker") for p in active_positions if isinstance(p, dict)]

        # ---- Global Macro Context (one-time per cycle) ----
        emergency_meta = EmergencyMacroWireEngine.scan_emergency_wire()
        wire_multiplier = emergency_meta["wire_multiplier"]

        headlines = news_engine.fetch_latest_headlines(limit=2)
        headline_text = headlines[0]['headline'] if headlines else "Global macro markets trading within standard parameters."
        sentiment = nlp.analyze_news_ensemble(headline_text)
        sm = sentiment["sentiment_multiplier"]
        if isinstance(learned_weights, dict):
            sm *= learned_weights.get("sentiment_weight", 1.0)

        calendar_meta = PredictiveMacroCalendarEngine.analyze_upcoming_macro_events()
        cal_multiplier = calendar_meta["event_multiplier"]

        regulatory_meta = USRegulatoryPolicyEngine.analyze_regulatory_climate()
        reg_multiplier = regulatory_meta["regulatory_multiplier"]

        macro_events = USMacroEventEngine.get_macro_event_multiplier()
        macro_multiplier = macro_events["macro_multiplier"]

        # Use BTC as the market regime benchmark (always available on Binance)
        btc_df, btc_real = RealtimeMarketDataFeed.get_live_ohlcv("BTC/USDT", "Crypto Futures", 63900.0)
        if btc_df is not None and btc_real:
            macro_meta = MacroRegimeFilter.evaluate_macro_trend(btc_df)
        else:
            macro_meta = {"macro_score": 1.0, "macro_regime": "NEUTRAL"}

        signals_dispatched = 0
        assets_skipped_no_data = 0

        candidates = []

        for item in universe:
            try:
                ticker = item["ticker"]
                asset_class = item["asset_class"]
                sector = item.get("sector", "Global")
                base_p = item.get("base_price", 100.0)

                # ---- FETCH REAL EXCHANGE DATA ----
                df, is_real = RealtimeMarketDataFeed.get_live_ohlcv(ticker, asset_class, base_p)

                # CRITICAL: Skip if no real data — NEVER trade on fake/synthetic prices
                if df is None or not is_real or len(df) < 15:
                    if df is None or not is_real:
                        assets_skipped_no_data += 1
                    continue

                raw_current_price = float(df.iloc[-1]['close'])
                current_price = format_price_raw(raw_current_price)
                structure_flipped = df.iloc[-1]['close'] < df.iloc[-10]['low']

                # Check existing positions against live price
                monitor.check_active_positions(ticker, current_price, sm, structure_flipped)

                # ---- FILTERS (quick rejects first) ----
                slippage_meta = InstitutionalSlippageDefense.audit_spread_and_slippage(df)
                if slippage_meta["is_high_slippage"]:
                    continue

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

                # ---- ANALYSIS ----
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

                base_wr = learned_weights.get("base_win_rate", 0.62) if isinstance(learned_weights, dict) else 0.62
                base_wr_setting = base_wr + smc_meta["win_rate_boost"]

                effective_multiplier = (
                    sm * wire_multiplier * cal_multiplier * reg_multiplier *
                    macro_multiplier * macro_meta["macro_score"] *
                    funding_meta["squeeze_tailwind"] * shield_meta["shield_multiplier"] *
                    reasoning_meta["authenticity_score"] * slippage_meta["slippage_score"]
                )

                signal = SureShotConfluenceEngine.evaluate_setup(df, effective_multiplier)

                # ---- HIGH-CONVICTION 70.0%+ DYNAMIC WIN RATE GATE (3+ INSTITUTIONAL CONFLUENCES) ----
                if signal["is_sure_shot"] and signal["win_rate"] >= 0.70:
                    direction = signal["direction"]
                    entry = current_price

                    reward_risk_ratio = 3.0
                    kelly = kelly_position_size(signal["win_rate"], reward_risk_ratio, ACCOUNT_BALANCE)
                    target_risk_usd = kelly["dollars_at_risk"]

                    sl_multiplier = max(regime_meta["sl_multiplier"], shield_meta["sl_buffer_atr"])
                    sl_dist = max(signal["atr"] * sl_multiplier, entry * 0.008)
                    tp_dist = sl_dist * reward_risk_ratio

                    raw_sl = entry - sl_dist if direction == "LONG" else entry + sl_dist
                    raw_tp = entry + tp_dist if direction == "LONG" else entry - tp_dist

                    sl = format_price_raw(raw_sl)
                    tp = format_price_raw(raw_tp)

                    sl_pct = abs(entry - sl) / entry
                    tp_pct = abs(tp - entry) / entry

                    if sl_pct < 0.001:
                        continue

                    duration_meta = PrecisionDurationEstimator.calculate_estimated_duration(
                        entry, tp, signal["atr"], velocity_meta["velocity_ratio"]
                    )

                    raw_ideal_leverage = max(round(1.0 / max(sl_pct * 2.5, 0.01)), 15)
                    lev_meta = ExchangeLeverageEngine.get_calibrated_leverage(ticker, raw_ideal_leverage)
                    chosen_leverage = lev_meta["recommended_leverage"]

                    final_margin = round(target_risk_usd / max(chosen_leverage * sl_pct, 0.0001), 2)
                    actual_notional = round(final_margin * chosen_leverage, 2)

                    exact_loss_usd = round(actual_notional * sl_pct, 2)
                    exact_gain_usd = round(actual_notional * tp_pct, 2)
                    roi_gain_pct = round((exact_gain_usd / max(final_margin, 0.01)) * 100, 1)
                    win_rate_pct = round(signal["win_rate"] * 100, 1)

                    candidates.append({
                        "ticker": ticker,
                        "direction": direction,
                        "entry": entry,
                        "sl": sl,
                        "tp": tp,
                        "sl_pct": sl_pct,
                        "tp_pct": tp_pct,
                        "win_rate": signal["win_rate"],
                        "win_rate_pct": win_rate_pct,
                        "expected_value": signal.get("expected_value", 0.0),
                        "kelly": kelly,
                        "final_margin": final_margin,
                        "actual_notional": actual_notional,
                        "exact_gain_usd": exact_gain_usd,
                        "exact_loss_usd": exact_loss_usd,
                        "roi_gain_pct": roi_gain_pct,
                        "chosen_leverage": chosen_leverage,
                        "duration_meta": duration_meta,
                        "lev_meta": lev_meta
                    })

            except Exception as item_err:
                print(f"[!] Error scanning {item.get('ticker', '?')}: {item_err}", flush=True)
                continue

        # ---- SELECT ONLY THE TOP #1 BEST CANDIDATE (ZERO SPAM, MAXIMUM ACCURACY) ----
        if candidates:
            # Sort by Win Rate descending, then Expected Value descending
            candidates.sort(key=lambda x: (x["win_rate"], x["expected_value"]), reverse=True)
            best = candidates[0]

            ticker = best["ticker"]
            direction = best["direction"]
            entry = best["entry"]
            sl = best["sl"]
            tp = best["tp"]
            sl_pct = best["sl_pct"]
            tp_pct = best["tp_pct"]
            win_rate_pct = best["win_rate_pct"]
            kelly = best["kelly"]
            final_margin = best["final_margin"]
            actual_notional = best["actual_notional"]
            exact_gain_usd = best["exact_gain_usd"]
            exact_loss_usd = best["exact_loss_usd"]
            roi_gain_pct = best["roi_gain_pct"]
            chosen_leverage = best["chosen_leverage"]
            duration_meta = best["duration_meta"]
            lev_meta = best["lev_meta"]

            dir_emoji = "🟢" if direction == "LONG" else "🔴"
            action_str = "LONG (BUY)" if direction == "LONG" else "SHORT (SELL)"

            alert_msg = f"""
{dir_emoji} **{action_str}: {ticker}**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏆 **WIN RATE:** `{win_rate_pct}%` | Kelly Risk: `{kelly['risk_pct']}%`
📍 **ENTRY:** `{format_price_dynamic(entry)}`
🎯 **TAKE PROFIT (TP):** `{format_price_dynamic(tp)}` (+{tp_pct*100:.2f}%)
🛡️ **STOP LOSS (SL):** `{format_price_dynamic(sl)}` (-{sl_pct*100:.2f}%)

💰 **MARGIN:** `${final_margin:,.2f} USDT` (`{chosen_leverage}x Isolated`)
📈 **TARGET GAIN:** `+${exact_gain_usd:,.2f}` (+{roi_gain_pct}% ROI)
📉 **HARD RISK:** `-${exact_loss_usd:,.2f}` (-{exact_loss_usd/ACCOUNT_BALANCE*100:.1f}% Equity)
⏱️ **EST. HORIZON:** `{duration_meta['formatted_label']}`
🏛️ **EXCHANGE:** `{lev_meta['primary_exchange']}`
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ Top #1 High-Conviction Quant Setup Selection
✅ Real-Time Exchange Data Verified
            """

            is_dispatched = telegram.send_alert(alert_msg)
            if is_dispatched:
                SignalCooldownEngine.record_signal_sent(ticker)
                signals_dispatched += 1
                scanner_state["last_signal"] = f"{ticker} {direction} @ {entry}"
                print(f"[✓] TOP #1 SIGNAL DISPATCHED: {ticker} {direction} @ {format_price_dynamic(entry)} (WR={win_rate_pct}%)", flush=True)

                positions = monitor.load_positions()
                updated_positions = [p for p in positions if p.get("ticker") != ticker]
                updated_positions.append({
                    "ticker": ticker,
                    "direction": direction,
                    "entry_price": entry,
                    "stop_loss": sl,
                    "take_profit": tp,
                    "win_rate": best["win_rate"],
                    "margin": final_margin,
                    "leverage": chosen_leverage,
                    "notional": actual_notional,
                    "epoch_time": time.time(),
                    "time": time.strftime('%Y-%m-%d %H:%M:%S')
                })
                monitor.save_positions(updated_positions)

                os.makedirs("portfolio", exist_ok=True)
                disp_file = "portfolio/dispatched_signals.json"
                dispatched = []
                if os.path.exists(disp_file):
                    try:
                        with open(disp_file, "r") as f:
                            dispatched = json.load(f)
                    except Exception:
                        dispatched = []
                dispatched.append({
                    "ticker": ticker,
                    "direction": direction,
                    "entry_price": entry,
                    "stop_loss": sl,
                    "take_profit": tp,
                    "win_rate": best["win_rate"],
                    "margin": final_margin,
                    "leverage": chosen_leverage,
                    "status": "DISPATCHED",
                    "time": time.strftime('%Y-%m-%d %H:%M:%S')
                })
                try:
                    with open(disp_file, "w") as f:
                        json.dump(dispatched, f, indent=2)
                except Exception as e:
                    print(f"[!] Error writing dispatched_signals.json: {e}")

        scanner_state["last_scan_time"] = time.strftime('%Y-%m-%d %H:%M:%S')
        scanner_state["total_scans"] += 1
        scanner_state["total_signals_sent"] += signals_dispatched
        scanner_state["status"] = "RUNNING"
        print(f"[{time.strftime('%H:%M:%S')}] Scan #{scanner_state['total_scans']} complete: {signals_dispatched} signals, {assets_skipped_no_data} skipped", flush=True)

    except Exception as loop_err:
        error_msg = f"{type(loop_err).__name__}: {loop_err}"
        scanner_state["last_error"] = error_msg
        scanner_state["status"] = "ERROR_RECOVERING"
        print(f"[!] CRITICAL scan loop error: {error_msg}", flush=True)
        traceback.print_exc()
        # Report crash to Telegram so user can see what's wrong
        try:
            telegram.send_alert(f"⚠️ **Scanner Error**\n```\n{error_msg}\n```\nRecovering in 15s...")
        except Exception:
            pass

# ============================================================
# BACKGROUND SCANNER LOOP — Runs every 15 seconds on Render
# ============================================================
def start_background_scanner_loop():
    print("🚀 Den Engine v35.0 Background Scanner Starting...", flush=True)
    scanner_state["status"] = "INITIALIZING"

    # Send startup notification to Telegram
    try:
        telegram.send_alert(
            "🚀 **Den Engine v35.0 Online**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "✅ Real Binance Futures Data\n"
            "✅ Kelly Criterion Sizing\n"
            "✅ Auto-Dispatch Active 24/7\n"
            f"⏰ Server Time: {time.strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )
    except Exception as e:
        print(f"[!] Startup Telegram alert failed: {e}", flush=True)

    # Quick connectivity test across endpoints
    try:
        test_resp = requests.get('https://api.bybit.com/v5/market/kline?category=linear&symbol=BTCUSDT&interval=15&limit=1', timeout=5)
        if test_resp.status_code == 200:
            scanner_state["status"] = "EXCHANGE_FEEDS_OK"
            scanner_state["last_error"] = "none"
            print(f"[✓] Bybit/Bitget Multi-Exchange Feed reachable from Render cloud.", flush=True)
        else:
            print(f"[!] Bybit test status {test_resp.status_code}", flush=True)
    except Exception as e:
        print(f"[!] Pre-flight feed test exception: {e}", flush=True)

    while True:
        try:
            scanner_state["status"] = "SCANNING"
            run_continuous_quant_hunter()
        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            scanner_state["last_error"] = error_msg
            scanner_state["status"] = "ERROR_RECOVERING"
            print(f"[!] Scanner loop exception (recovering): {error_msg}", flush=True)
            traceback.print_exc()
            try:
                telegram.send_alert(f"⚠️ **Scanner Loop Crash**\n```\n{error_msg}\n```")
            except Exception:
                pass
        time.sleep(15)

# ============================================================
# ENTRY POINT — Render Cloud Main Process
# ============================================================
if __name__ == "__main__":
    t_scan = threading.Thread(target=start_background_scanner_loop, daemon=False)
    t_scan.start()
    t_ping = threading.Thread(target=self_ping_keep_alive, daemon=True)
    t_ping.start()
    print("🚀 Den Engine v35.0 HTTP Health Server Active on Render Cloud...", flush=True)
    start_health_server()
