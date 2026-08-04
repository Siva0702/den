# models/auto_scanner.py
# Den Engine v38.0 — World's Best Quant System
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
from indicators.anti_manipulation import InstitutionalAntiManipulationShield
from indicators.exchange_leverage import ExchangeLeverageEngine
from alerts.signal_cooldown import SignalCooldownEngine
from audit.engine_efficiency import EngineEfficiencyTracker
from position_monitor import ActivePositionMonitor
from news.market_universe import DynamicMarketUniverse
from news.regulatory_events import USRegulatoryPolicyEngine
from data.exchange_feed import BitunixWeexLiveFeed
from alerts.telegram_bot import TelegramAlertBot

load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or "8847828896:AAFcTqjJGe6VN6mbPHcB1QTlvkpQxhb5ntI"
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or "7347569157"

ACCOUNT_BALANCE = 1000.0
ENGINE_VERSION = "v38.2"

monitor = ActivePositionMonitor(BOT_TOKEN, CHAT_ID)
telegram = TelegramAlertBot(BOT_TOKEN, CHAT_ID)

# Track dispatched message IDs for "positioned" reply detection
dispatched_message_ids = {}  # {message_id: ticker}
last_update_id = 0
last_signal_time = time.time()
last_digest_time = 0

# ============================================================
# PRICE FORMATTING — Dynamic precision based on actual price
# ============================================================
def format_price_dynamic(price: float) -> str:
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
# SCANNER STATE
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
# RENDER CLOUD HEALTH SERVER
# ============================================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        status_text = (
            f"Den Engine {ENGINE_VERSION} Quant System | Status: {scanner_state['status']}\n"
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
# KELLY CRITERION POSITION SIZING
# ============================================================
def kelly_position_size(win_rate: float, reward_risk_ratio: float, account_balance: float, max_risk_pct: float = 0.05) -> dict:
    p = win_rate
    q = 1.0 - p
    b = reward_risk_ratio
    kelly_fraction = (p * b - q) / b if b > 0 else 0.0
    kelly_fraction = max(kelly_fraction, 0.0)
    half_kelly = kelly_fraction * 0.5
    risk_fraction = min(half_kelly, max_risk_pct)
    dollars_at_risk = round(account_balance * risk_fraction, 2)
    dollars_at_risk = max(10.0, min(dollars_at_risk, 75.0))
    return {
        "kelly_full": round(kelly_fraction, 4),
        "kelly_half": round(half_kelly, 4),
        "dollars_at_risk": dollars_at_risk,
        "risk_pct": round(risk_fraction * 100, 2)
    }

# ============================================================
# SESSION AWARENESS
# ============================================================
def get_current_session():
    """Returns current trading session based on UTC time."""
    hour = int(time.strftime('%H'))
    if 7 <= hour < 9:
        return "LONDON_OPEN", 4
    elif 9 <= hour < 13:
        return "LONDON", 3
    elif 13 <= hour < 16:
        return "NY_OVERLAP", 4  # London/NY overlap — peak liquidity
    elif 16 <= hour < 20:
        return "NY", 3
    elif 0 <= hour < 4:
        return "ASIAN", 2
    else:
        return "DEAD_ZONE", 1

# ============================================================
# "POSITIONED" REPLY LISTENER
# ============================================================
def poll_positioned_replies():
    """Background thread: polls Telegram for 'positioned' replies to signal messages."""
    global last_update_id
    while True:
        try:
            if dispatched_message_ids:
                replies, new_offset = telegram.poll_for_positioned_replies(
                    list(dispatched_message_ids.keys()), last_update_id
                )
                if new_offset > last_update_id:
                    last_update_id = new_offset
                
                for reply in replies:
                    reply_text = reply.get("text", "").strip().lower()
                    reply_to_id = reply.get("reply_to_message_id")
                    
                    if reply_to_id in dispatched_message_ids and "position" in reply_text:
                        ticker = dispatched_message_ids[reply_to_id]
                        
                        # Mark position as user-taken
                        positions = monitor.load_positions()
                        for pos in positions:
                            if pos.get("ticker") == ticker:
                                pos["user_positioned"] = True
                                print(f"[✓] User POSITIONED on {ticker}!", flush=True)
                        monitor.save_positions(positions)
                        
                        # Acknowledge
                        telegram.send_alert(f"✅ **Positioned confirmed: {ticker}**\nTracking your trade for performance reporting.")
        except Exception as e:
            print(f"[!] Reply listener error: {e}", flush=True)
        
        time.sleep(10)

# ============================================================
# MAIN SCAN ENGINE — Den Engine v38.0
# ============================================================
def run_continuous_quant_hunter():
    try:
        universe = DynamicMarketUniverse.get_full_hunting_universe()
        scanner_state["assets_in_universe"] = len(universe)

        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 🚀 DEN ENGINE {ENGINE_VERSION} | Scanning {len(universe)} Assets...", flush=True)

        active_positions = monitor.load_positions()
        active_tickers = [p.get("ticker") for p in active_positions if isinstance(p, dict)]

        # Load efficiency history for self-learning
        efficiency_data = EngineEfficiencyTracker.load_efficiency_data()

        # Fetch BTC as market benchmark (always available)
        btc_df_15m, btc_real = BitunixWeexLiveFeed.get_exchange_ohlcv("BTC/USDT", 64000.0, "15m")
        if not btc_real:
            btc_df_15m = None

        # Session awareness
        session_name, session_score = get_current_session()

        # Real-time US Regulatory & Political Climate Tracking (Clarity Act / Recess / SEC)
        reg_data = USRegulatoryPolicyEngine.analyze_regulatory_climate()
        reg_multiplier = reg_data.get("regulatory_multiplier", 1.0)
        reg_warning = reg_data.get("warning_msg", "")
        if reg_data.get("is_recess_delay"):
            print(f"[!] {reg_data['regulatory_status']} | Multiplier: {reg_multiplier}", flush=True)

        signals_dispatched = 0
        assets_skipped = 0
        candidates = []

        for item in universe:
            try:
                ticker = item["ticker"]
                base_p = item.get("base_price", 100.0)

                # Skip if already in active position
                if ticker in active_tickers:
                    continue

                # Cooldown check
                if not SignalCooldownEngine.can_send_signal(ticker):
                    continue

                # ---- FETCH MULTI-TIMEFRAME DATA ----
                df_15m, real_15m = BitunixWeexLiveFeed.get_exchange_ohlcv(ticker, base_p, "15m")
                if df_15m is None or not real_15m or len(df_15m) < 50:
                    assets_skipped += 1
                    continue

                # Higher timeframes (best effort — don't block on failure)
                df_1h, _ = BitunixWeexLiveFeed.get_exchange_ohlcv(ticker, base_p, "1h")
                df_4h, _ = BitunixWeexLiveFeed.get_exchange_ohlcv(ticker, base_p, "4h")
                df_1d, _ = BitunixWeexLiveFeed.get_exchange_ohlcv(ticker, base_p, "1d")

                current_price = format_price_raw(float(df_15m.iloc[-1]['close']))
                structure_flipped = df_15m.iloc[-1]['close'] < df_15m.iloc[-10]['low'] if len(df_15m) > 10 else False

                # Check existing positions against live price with dynamic invalidation scoring
                monitor.check_active_positions(ticker, current_price, reg_multiplier, structure_flipped, df_15m)

                # ---- CONFLUENCE ENGINE v38.0 — 0-to-100 Dynamic Scoring ----
                signal = SureShotConfluenceEngine.evaluate_setup(
                    ohlcv_15m=df_15m,
                    ohlcv_1h=df_1h if df_1h is not None and len(df_1h) > 20 else None,
                    ohlcv_4h=df_4h if df_4h is not None and len(df_4h) > 20 else None,
                    ohlcv_1d=df_1d if df_1d is not None and len(df_1d) > 20 else None,
                    btc_df=btc_df_15m,
                    ticker=ticker,
                    efficiency_history=efficiency_data,
                    sentiment_multiplier=reg_multiplier
                )

                total_score = signal.get("total_score", 0)
                direction = signal.get("direction", "NONE")
                rec_label = signal.get("recommendation_label", "❌ NO TRADE")

                # Only consider trades with QUALIFIED or above (score >= 50)
                if direction == "NONE" or total_score < 50:
                    continue

                entry = current_price
                atr_val = signal.get("atr", entry * 0.01)
                win_rate = signal.get("win_rate", 0.0)

                reward_risk_ratio = 3.0
                kelly = kelly_position_size(win_rate, reward_risk_ratio, ACCOUNT_BALANCE)

                sl_dist = max(atr_val * 1.5, entry * 0.008)
                tp_dist = sl_dist * reward_risk_ratio

                raw_sl = entry - sl_dist if direction == "LONG" else entry + sl_dist
                raw_tp = entry + tp_dist if direction == "LONG" else entry - tp_dist
                sl = format_price_raw(raw_sl)
                tp = format_price_raw(raw_tp)

                sl_pct = abs(entry - sl) / entry
                tp_pct = abs(tp - entry) / entry
                if sl_pct < 0.001:
                    continue

                raw_ideal_leverage = max(round(1.0 / max(sl_pct * 2.5, 0.01)), 15)
                lev_meta = ExchangeLeverageEngine.get_calibrated_leverage(ticker, raw_ideal_leverage)
                chosen_leverage = lev_meta["recommended_leverage"]

                target_risk_usd = kelly["dollars_at_risk"]
                final_margin = round(target_risk_usd / max(chosen_leverage * sl_pct, 0.0001), 2)
                actual_notional = round(final_margin * chosen_leverage, 2)
                exact_loss_usd = round(actual_notional * sl_pct, 2)
                exact_gain_usd = round(actual_notional * tp_pct, 2)
                roi_gain_pct = round((exact_gain_usd / max(final_margin, 0.01)) * 100, 1)

                candidates.append({
                    "ticker": ticker,
                    "direction": direction,
                    "entry": entry,
                    "sl": sl,
                    "tp": tp,
                    "sl_pct": sl_pct,
                    "tp_pct": tp_pct,
                    "win_rate": win_rate,
                    "total_score": total_score,
                    "recommendation_label": rec_label,
                    "factors_passed": signal.get("factors_passed", []),
                    "factors_failed": signal.get("factors_failed", []),
                    "reasoning": signal.get("reasoning", ""),
                    "estimated_duration": signal.get("estimated_duration", "~2-4 hours"),
                    "market_regime": signal.get("market_regime", "UNKNOWN"),
                    "timeframe_alignment": signal.get("timeframe_alignment", 0),
                    "is_sure_shot": signal.get("is_sure_shot", False),
                    "kelly": kelly,
                    "final_margin": final_margin,
                    "actual_notional": actual_notional,
                    "exact_gain_usd": exact_gain_usd,
                    "exact_loss_usd": exact_loss_usd,
                    "roi_gain_pct": roi_gain_pct,
                    "chosen_leverage": chosen_leverage,
                    "lev_meta": lev_meta,
                    "rsi": signal.get("rsi", 0),
                    "atr": atr_val,
                })

            except Exception as item_err:
                print(f"[!] Error scanning {item.get('ticker', '?')}: {item_err}", flush=True)
                continue

        # ---- SELECT TOP #1 BEST CANDIDATE ----
        if candidates:
            candidates.sort(key=lambda x: (x["total_score"], x["win_rate"]), reverse=True)
            best = candidates[0]

            # Only dispatch if score meets high conviction threshold (78+ = HIGH CONVICTION / SURE SHOT ONLY)
            if best["total_score"] >= 78.0:
                ticker = best["ticker"]
                direction = best["direction"]
                entry = best["entry"]
                sl = best["sl"]
                tp = best["tp"]
                sl_pct = best["sl_pct"]
                tp_pct = best["tp_pct"]
                total_score = best["total_score"]
                win_rate_pct = round(best["win_rate"] * 100, 1)
                rec_label = best["recommendation_label"]
                kelly = best["kelly"]
                final_margin = best["final_margin"]
                exact_gain_usd = best["exact_gain_usd"]
                exact_loss_usd = best["exact_loss_usd"]
                roi_gain_pct = best["roi_gain_pct"]
                chosen_leverage = best["chosen_leverage"]
                est_duration = best["estimated_duration"]
                regime = best["market_regime"]
                tf_align = best["timeframe_alignment"]
                factors_passed = best["factors_passed"]
                factors_failed = best["factors_failed"]
                reasoning = best["reasoning"]

                # Direction emojis
                dir_dot = "🟢" if direction == "LONG" else "🔴"
                action_str = "LONG (BUY)" if direction == "LONG" else "SHORT (SELL)"

                # Build factor reasons
                reasons_text = ""
                for fp in factors_passed[:6]:
                    reasons_text += f"• {fp} ✅\n"
                
                missing_text = ""
                for ff in factors_failed[:3]:
                    missing_text += f"• {ff}\n"

                macro_alert_block = f"\n🏛️ **POLITICAL / MACRO HEADWIND:**\n_{reg_warning}_\n" if reg_warning else ""

                alert_msg = f"""{dir_dot} **{rec_label} — {action_str}: {ticker}**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏆 **SCORE:** `{total_score:.0f}/100` | Win Rate: `{win_rate_pct}%`
📊 **Regime:** `{regime}` | TF Align: `{tf_align}/4`
📍 **ENTRY:** `{format_price_dynamic(entry)}`
🎯 **TAKE PROFIT:** `{format_price_dynamic(tp)}` (+{tp_pct*100:.2f}%)
🛡️ **STOP LOSS:** `{format_price_dynamic(sl)}` (-{sl_pct*100:.2f}%)

💰 **MARGIN:** `${final_margin:,.2f} USDT` (`{chosen_leverage}x Isolated`)
📈 **TARGET:** `+${exact_gain_usd:,.2f}` (+{roi_gain_pct}% ROI)
📉 **RISK:** `-${exact_loss_usd:,.2f}`
⏱️ **EST. DURATION:** `{est_duration}`
🏛️ **EXCHANGE:** `Bitunix / Weex Futures`
{macro_alert_block}
📋 **KEY REASONS:**
{reasons_text}
{"⚠️ **MISSING:**" + chr(10) + missing_text if missing_text else ""}
💡 _{reasoning}_

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_Reply **positioned** to track this trade_"""

                msg_id = telegram.send_alert(alert_msg)
                if msg_id:
                    dispatched_message_ids[msg_id] = ticker
                    SignalCooldownEngine.record_signal_sent(ticker)
                    signals_dispatched += 1
                    scanner_state["last_signal"] = f"{ticker} {direction} @ {entry} ({rec_label})"
                    print(f"[✓] DISPATCHED: {ticker} {direction} @ {format_price_dynamic(entry)} Score={total_score:.0f} (msg_id={msg_id})", flush=True)

                    # Build factor scores dict for self-learning
                    factor_scores_dict = {}
                    for i, fp in enumerate(factors_passed):
                        factor_scores_dict[f"passed_{i}"] = 1.0
                    for i, ff in enumerate(factors_failed):
                        factor_scores_dict[f"failed_{i}"] = 0.0

                    # Save to active positions
                    positions = monitor.load_positions()
                    updated_positions = [p for p in positions if p.get("ticker") != ticker]
                    updated_positions.append({
                        "ticker": ticker,
                        "direction": direction,
                        "entry_price": entry,
                        "stop_loss": sl,
                        "take_profit": tp,
                        "win_rate": best["win_rate"],
                        "total_score": total_score,
                        "margin": final_margin,
                        "leverage": chosen_leverage,
                        "notional": best["actual_notional"],
                        "factor_scores": factor_scores_dict,
                        "user_positioned": False,
                        "epoch_time": time.time(),
                        "time": time.strftime('%Y-%m-%d %H:%M:%S')
                    })
                    monitor.save_positions(updated_positions)

                    # Save to dispatched signals log
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
                        "total_score": total_score,
                        "recommendation": rec_label,
                        "margin": final_margin,
                        "leverage": chosen_leverage,
                        "status": "DISPATCHED",
                        "time": time.strftime('%Y-%m-%d %H:%M:%S')
                    })
                    global last_signal_time
                    last_signal_time = time.time()

        # ---- SESSION-AWARE NO-SIGNAL HEARTBEAT DIGEST ----
        # During London & US Sessions: Every 1 HOUR (3600s) if no trades dispatched
        # During Asian & Off-Hours: Every 3 HOURS (10800s) if no trades dispatched
        global last_digest_time
        now_ts = time.time()
        
        is_active_session = session_name in ["LONDON_OPEN", "LONDON", "NY_OVERLAP", "NY"]
        required_interval = 3600 if is_active_session else 3 * 3600
        interval_label = "1h" if is_active_session else "3h"

        if (now_ts - last_signal_time >= required_interval) and (now_ts - last_digest_time >= required_interval) and candidates:
            try:
                candidates.sort(key=lambda x: (x["total_score"], x["win_rate"]), reverse=True)
                top_20 = candidates[:20]
                
                digest_lines = []
                for idx, item in enumerate(top_20, 1):
                    emoji = "🟢" if item["direction"] == "LONG" else "🔴" if item["direction"] == "SHORT" else "⬜"
                    wr_pct = round(item["win_rate"] * 100, 1)
                    p_str = format_price_dynamic(item["entry"])
                    digest_lines.append(
                        f"`{idx:2d}.` {emoji} **{item['ticker']}** — Score: `{item['total_score']:.0f}/100` | WR: `{wr_pct}%` | Price: `{p_str}`"
                    )
                
                digest_text = "\n".join(digest_lines)
                reg_status_short = reg_data.get("regulatory_status", "NEUTRAL")

                digest_msg = f"""🛰️ **DEN ENGINE HUNTING DIGEST (No Trades in {interval_label})**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 **SESSION:** `{session_name}` | High Conviction Filter Active (78+ Score Required)
🏛️ **MACRO:** _{reg_status_short}_

🏆 **TOP 20 LIVE HUNTED CANDIDATES:**
{digest_text}

💡 _All 87 assets monitored 24/7 across 4 timeframes (1D/4H/1H/15m)._
━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""
                telegram.send_alert(digest_msg)
                last_digest_time = now_ts
                print(f"[✓] {interval_label} Heartbeat Digest ({session_name}) dispatched to Telegram.", flush=True)
            except Exception as digest_err:
                print(f"[!] Heartbeat Digest Error: {digest_err}", flush=True)

        scanner_state["last_scan_time"] = time.strftime('%Y-%m-%d %H:%M:%S')
        scanner_state["total_scans"] += 1
        scanner_state["total_signals_sent"] += signals_dispatched
        scanner_state["status"] = "RUNNING"
        print(f"[{time.strftime('%H:%M:%S')}] Scan #{scanner_state['total_scans']} complete: {signals_dispatched} signals, {assets_skipped} skipped, session={session_name}", flush=True)

    except Exception as loop_err:
        error_msg = f"{type(loop_err).__name__}: {loop_err}"
        scanner_state["last_error"] = error_msg
        scanner_state["status"] = "ERROR_RECOVERING"
        print(f"[!] CRITICAL scan loop error: {error_msg}", flush=True)
        traceback.print_exc()
        try:
            telegram.send_alert(f"⚠️ **Scanner Error**\n```\n{error_msg}\n```\nRecovering in 15s...")
        except Exception:
            pass

# ============================================================
# BACKGROUND SCANNER LOOP — Runs every 15 seconds on Render
# ============================================================
def start_background_scanner_loop():
    print(f"🚀 Den Engine {ENGINE_VERSION} Quant System Background Scanner Starting...", flush=True)
    scanner_state["status"] = "INITIALIZING"

    # Quick connectivity test
    try:
        test_resp = requests.get('https://api.bybit.com/v5/market/kline?category=linear&symbol=BTCUSDT&interval=15&limit=1', timeout=5)
        if test_resp.status_code == 200:
            scanner_state["status"] = "EXCHANGE_FEEDS_OK"
            scanner_state["last_error"] = "none"
            print(f"[✓] Exchange feeds reachable from Render cloud.", flush=True)
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
    t_reply = threading.Thread(target=poll_positioned_replies, daemon=True)
    t_reply.start()
    print(f"🚀 Den Engine {ENGINE_VERSION} HTTP Health Server Active on Render Cloud...", flush=True)
    start_health_server()
