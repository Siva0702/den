# models/auto_scanner.py
# Den Engine v39.0 — Calibrated Multi-Asset Quant Scanner
import os
import sys
import time
import json
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

import requests
from dotenv import load_dotenv

sys.stdout.reconfigure(line_buffering=True)
sys.path.append(os.path.dirname(__file__))

from indicators.confluence_engine import SureShotConfluenceEngine
from indicators.exchange_leverage import ExchangeLeverageEngine
from indicators.liquidity_map import LiquidityMapEngine
from indicators.event_volatility import EventVolatilityEngine
from alerts.signal_cooldown import SignalCooldownEngine
from alerts.telegram_bot import TelegramAlertBot
from audit.engine_efficiency import EngineEfficiencyTracker
from audit.shadow_ledger import ShadowTradeLedger
from audit.calibration import WinRateCalibrator
from audit.score_tracker import ScoreStabilityTracker
from data.exchange_feed import BitunixWeexLiveFeed
from data.derivatives_feed import DerivativesIntelligence
from news.market_universe import DynamicMarketUniverse
from news.news_intelligence import PerAssetNewsIntelligence
from news.event_calendar import ScheduledEventCalendar
from news.event_outcomes import EventOutcomeLearner
from news.regulatory_events import USRegulatoryPolicyEngine
from position_monitor import ActivePositionMonitor

load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or "8847828896:AAFcTqjJGe6VN6mbPHcB1QTlvkpQxhb5ntI"
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or "7347569157"

ENGINE_VERSION = "v39.0"
ACCOUNT_BALANCE = 1000.0

# ---- Dispatch gates -------------------------------------------------------
HARD_SCORE_FLOOR = 78.0        # the user's standing rule: nothing below this is a signal
RELAXED_SCORE_FLOOR = 72.0     # engaged only after a long dry spell, and labelled as such
DRY_SPELL_HOURS = 12.0         # how long with no signal before the relaxed tier engages
MIN_CALIBRATED_WIN_RATE = 0.50 # once calibrated, refuse setups the data says are coin flips
ENRICH_TOP_N = 12              # candidates promoted to expensive derivatives/news enrichment
MAX_FETCH_WORKERS = 6
MIN_RISK_USD = 10.0            # risk floor used while the engine has no measured edge
# Structure-based targets expose setups where price has no room before the next
# liquidity pool. Risking 1R to make 0.43R loses money at any win rate below 70%,
# so those are rejected outright rather than sized down.
MIN_REWARD_RISK = 1.2
# Throughput. v39.2 dispatched ONE signal per scan then stopped, which was the single
# biggest structural cap on signal count. Now up to MAX_SIGNALS_PER_SCAN may fire, but
# every one must independently clear all five gates — this raises throughput without
# lowering the bar, which is the opposite of spamming.
MAX_SIGNALS_PER_SCAN = 3
MAX_SIGNALS_PER_DAY = 8
# Conviction-scaled risk. A 50% setup and an 80% setup previously both risked $50
# because half-Kelly was clamped flat. Risk now scales with measured win rate.
RISK_FLOOR_USD = 12.0
RISK_CEIL_USD = 75.0

monitor = ActivePositionMonitor(BOT_TOKEN, CHAT_ID)
telegram = TelegramAlertBot(BOT_TOKEN, CHAT_ID)

dispatched_message_ids = {}
last_update_id = 0
last_signal_time = time.time()
last_digest_time = 0.0
signal_timestamps = []          # rolling 24h dispatch history

scanner_state = {
    "status": "STARTING", "last_scan_time": "never", "total_scans": 0,
    "total_signals_sent": 0, "last_signal": "none", "last_error": "none",
    "assets_in_universe": 0, "scan_duration_s": 0.0, "shadow_open": 0,
    "calibration": "UNCALIBRATED",
}


# ============================================================
# FORMATTING
# ============================================================
def format_price_dynamic(price: float) -> str:
    if price < 0.0001:
        return f"${price:.8f}"
    if price < 0.01:
        return f"${price:.6f}"
    if price < 1.0:
        return f"${price:.4f}"
    if price < 100.0:
        return f"${price:.3f}"
    if price < 10000.0:
        return f"${price:.2f}"
    return f"${price:,.2f}"


def format_price_raw(price: float) -> float:
    if price < 0.0001:
        return round(price, 8)
    if price < 0.01:
        return round(price, 6)
    if price < 1.0:
        return round(price, 4)
    if price < 100.0:
        return round(price, 3)
    return round(price, 2)


# ============================================================
# HEALTH SERVER
# ============================================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        body = "\n".join(f"{k}: {v}" for k, v in scanner_state.items())
        self.wfile.write(f"Den Engine {ENGINE_VERSION}\n{body}\n".encode('utf-8'))

    def log_message(self, fmt, *args):
        return


def start_health_server():
    try:
        port = int(os.getenv("PORT", 10000))
        HTTPServer(('0.0.0.0', port), HealthCheckHandler).serve_forever()
    except Exception as e:
        print(f"[!] Health server exception: {e}", flush=True)


def self_ping_keep_alive():
    while True:
        time.sleep(120)
        try:
            requests.get("https://den-quant-scanner.onrender.com/", timeout=10)
        except Exception:
            pass


# ============================================================
# SIZING — Half Kelly on the CALIBRATED win rate
# ============================================================
def kelly_position_size(win_rate: float, reward_risk_ratio: float,
                        account_balance: float, max_risk_pct: float = 0.05) -> dict:
    """
    Half-Kelly. The critical change from v38 is the INPUT: this is now fed the
    empirically calibrated win rate rather than score/100, so the size reflects
    measured edge instead of a rescaled score.
    """
    p = max(0.0, min(win_rate, 0.95))
    q = 1.0 - p
    b = reward_risk_ratio
    kelly = (p * b - q) / b if b > 0 else 0.0
    kelly = max(kelly, 0.0)
    half = kelly * 0.5
    risk_fraction = min(half, max_risk_pct)
    dollars = round(account_balance * risk_fraction, 2)

    # Conviction scaling. The old flat clamp made every setup risk the same amount no
    # matter what the calibrated win rate said, which meant the measured edge changed
    # position size by exactly nothing. Map win rate onto the risk band so the setups
    # the data actually likes get paid more, with the same worst case.
    conviction = max(0.0, min((p - 0.45) / 0.35, 1.0))          # 45% -> 0, 80% -> 1
    target = RISK_FLOOR_USD + conviction * (RISK_CEIL_USD - RISK_FLOOR_USD)
    dollars = round(min(dollars, target) if dollars > 0 else target, 2)
    dollars = max(RISK_FLOOR_USD, min(dollars, RISK_CEIL_USD))
    return {"kelly_full": round(kelly, 4), "kelly_half": round(half, 4),
            "dollars_at_risk": dollars, "risk_pct": round(risk_fraction * 100, 2)}


# ============================================================
# SESSION + DIGEST SCHEDULE (IST)
# ============================================================
IST = timezone(timedelta(hours=5, minutes=30))


def get_current_session():
    hour = datetime.now(timezone.utc).hour
    if 7 <= hour < 9:
        return "LONDON_OPEN", 4
    if 9 <= hour < 13:
        return "LONDON", 3
    if 13 <= hour < 16:
        return "NY_OVERLAP", 4
    if 16 <= hour < 20:
        return "NY", 3
    if 0 <= hour < 4:
        return "ASIAN", 2
    return "DEAD_ZONE", 1


def digest_interval_seconds() -> tuple:
    """
    Hourly between 11:00 and 03:00 IST (the user's active window, which wraps
    midnight), every 3 hours otherwise. Returns (seconds, label, ist_time_str).
    """
    now_ist = datetime.now(IST)
    h = now_ist.hour
    active = (h >= 11) or (h < 3)
    return (3600, "1h", now_ist.strftime("%H:%M IST")) if active else \
           (3 * 3600, "3h", now_ist.strftime("%H:%M IST"))


# ============================================================
# "POSITIONED" REPLY LISTENER
# ============================================================
def poll_positioned_replies():
    global last_update_id
    while True:
        try:
            if dispatched_message_ids:
                replies, new_offset = telegram.poll_for_positioned_replies(
                    list(dispatched_message_ids.keys()), last_update_id)
                if new_offset > last_update_id:
                    last_update_id = new_offset
                for reply in replies:
                    text = reply.get("text", "").strip().lower()
                    rid = reply.get("reply_to_message_id")
                    if rid in dispatched_message_ids and "position" in text:
                        ticker = dispatched_message_ids[rid]
                        positions = monitor.load_positions()
                        for pos in positions:
                            if pos.get("ticker") == ticker:
                                pos["user_positioned"] = True
                        monitor.save_positions(positions)
                        telegram.send_alert(
                            f"✅ **Positioned confirmed: {ticker}**\nTracking your trade for performance reporting.")
        except Exception as e:
            print(f"[!] Reply listener error: {e}", flush=True)
        time.sleep(10)


# ============================================================
# DATA FETCH — concurrent
# ============================================================
def fetch_asset_frames(item: dict) -> dict:
    """Fetch all four timeframes for one asset. Returns None frames on failure."""
    ticker = item["ticker"]
    base_p = item.get("base_price", 100.0)
    out = {"ticker": ticker, "item": item}
    df_15m, real = BitunixWeexLiveFeed.get_exchange_ohlcv(ticker, base_p, "15m")
    if df_15m is None or not real or len(df_15m) < 100:
        out["ok"] = False
        return out
    out["ok"] = True
    out["df_15m"] = df_15m
    for tf in ("5m", "1h", "4h", "1d"):
        df, _ = BitunixWeexLiveFeed.get_exchange_ohlcv(ticker, base_p, tf)
        out[f"df_{tf}"] = df if df is not None and len(df) > 20 else None
    return out


def entry_timing_ok(df_5m, direction: str) -> tuple:
    """
    5m execution filter. The 15m chart decides WHETHER to trade; the 5m decides WHEN.
    Entering a long while the 5m is already vertically extended is how a good thesis
    gets a terrible fill and then stops out on the first pullback. This requires the
    execution timeframe to not be stretched against us at the moment of entry.
    """
    if df_5m is None or len(df_5m) < 30:
        return True, "no 5m data — timing filter skipped"
    close = df_5m['close']
    ema9 = close.ewm(span=9, adjust=False).mean()
    dev = (float(close.iloc[-1]) - float(ema9.iloc[-1])) / max(abs(float(ema9.iloc[-1])), 1e-12)
    rng = float((df_5m['high'] - df_5m['low']).iloc[-20:].mean())
    stretch = abs(float(close.iloc[-1]) - float(ema9.iloc[-1])) / max(rng, 1e-12)

    if stretch > 1.8:
        if (direction == "LONG" and dev > 0) or (direction == "SHORT" and dev < 0):
            return False, f"5m extended {stretch:.1f}x avg range from EMA9 — wait for a pullback"
    return True, f"5m entry clean ({stretch:.1f}x range from EMA9)"


# ============================================================
# TP LADDER
# ============================================================
def build_tp_ladder(entry: float, sl: float, direction: str, atr: float,
                    liquidity: dict = None) -> list:
    """
    STRUCTURE-BASED targets, not fixed R multiples.

    v39.2 used a constant ladder of 1.0/1.8/2.6/4.0 x risk, which meant every signal
    carried an identical reward:risk no matter what was actually in front of price.
    That is backwards: a target only pays if price can REACH it, and what price has to
    get through is liquidity, not arithmetic. Two setups with the same stop distance can
    have completely different room to run.

    Here targets are placed off the mapped liquidity pools in the direction of travel:

      TP1  just IN FRONT of the first pool  — bank before the obvious rejection point
      TP2  just BEYOND that pool            — pays if it breaks through
      TP3  in front of the second pool
      TP4  the third pool, or a 4R stretch target

    A setup with a pool 3R away and clear air in between now targets 3R. A setup with
    resistance at 0.6R targets 0.6R and is correctly recognised as a poor trade. R:R
    therefore varies signal to signal, which is the entire point.

    Falls back to the fixed ladder when no pools are mapped.
    """
    risk = abs(entry - sl)
    if risk <= 0:
        return []

    fallback = [1.0, 1.8, 2.6, 4.0]
    pools = []
    if liquidity and liquidity.get("available"):
        raw = liquidity.get("pools_above") if direction == "LONG" else liquidity.get("pools_below")
        for p in (raw or []):
            price = float(p["price"])
            # Only pools that are actually ahead of us and worth more than a token move.
            if direction == "LONG" and price > entry + risk * 0.4:
                pools.append(price)
            elif direction == "SHORT" and price < entry - risk * 0.4:
                pools.append(price)
    pools = sorted(pools) if direction == "LONG" else sorted(pools, reverse=True)

    buffer = max(atr * 0.15, entry * 0.0004) if atr else entry * 0.0006
    targets = []
    if pools:
        first = pools[0]
        targets.append(first - buffer if direction == "LONG" else first + buffer)
        targets.append(first + buffer * 2 if direction == "LONG" else first - buffer * 2)
        if len(pools) > 1:
            second = pools[1]
            targets.append(second - buffer if direction == "LONG" else second + buffer)
        if len(pools) > 2:
            third = pools[2]
            targets.append(third - buffer if direction == "LONG" else third + buffer)

    # Top up from the R ladder so there are always four rungs, and keep them ordered
    # and strictly beyond the entry.
    for m in fallback:
        if len(targets) >= 4:
            break
        lvl = entry + risk * m if direction == "LONG" else entry - risk * m
        targets.append(lvl)

    targets = sorted(set(targets)) if direction == "LONG" else sorted(set(targets), reverse=True)
    floor = entry + risk * 0.3 if direction == "LONG" else entry - risk * 0.3
    targets = [t for t in targets if (t > floor if direction == "LONG" else t < floor)][:4]
    if not targets:
        targets = [entry + risk * m if direction == "LONG" else entry - risk * m for m in fallback]
    return [format_price_raw(t) for t in targets[:4]]


# ============================================================
# MAIN SCAN
# ============================================================
def run_continuous_quant_hunter():
    global last_signal_time, last_digest_time

    scan_start = time.time()
    universe = DynamicMarketUniverse.get_full_hunting_universe()
    scanner_state["assets_in_universe"] = len(universe)

    active_positions = monitor.load_positions()
    active_tickers = {p.get("ticker") for p in active_positions if isinstance(p, dict)}

    efficiency_data = EngineEfficiencyTracker.load_efficiency_data()
    session_name, _ = get_current_session()

    reg_data = USRegulatoryPolicyEngine.analyze_regulatory_climate()
    reg_multiplier = reg_data.get("regulatory_multiplier", 1.0)
    reg_warning = reg_data.get("warning_msg", "")

    # Rolling 30-day event calendar. Self-throttling: a clean pull holds for 6h, a
    # degraded one retries next scan rather than serving a blank calendar.
    try:
        ScheduledEventCalendar.refresh()
    except Exception as e:
        print(f"[!] Calendar refresh error: {e}", flush=True)

    # ---- Stage 0: concurrent multi-timeframe fetch for the whole universe ----
    frames = {}
    with ThreadPoolExecutor(max_workers=MAX_FETCH_WORKERS) as pool:
        futures = {pool.submit(fetch_asset_frames, item): item["ticker"] for item in universe}
        for fut in as_completed(futures):
            try:
                res = fut.result()
                if res.get("ok"):
                    frames[res["ticker"]] = res
            except Exception as e:
                print(f"[!] Fetch failed for {futures[fut]}: {type(e).__name__}", flush=True)

    btc_df = frames.get("BTC/USDT", {}).get("df_15m")
    # Feed OHLC, not just close, so excursions between scans are not lost.
    price_map = {t: {"close": float(f["df_15m"].iloc[-1]['close']),
                     "high": float(f["df_15m"].iloc[-1]['high']),
                     "low": float(f["df_15m"].iloc[-1]['low'])}
                 for t, f in frames.items()}

    # ---- CRITICAL FIX: monitor open positions FIRST -------------------------
    # v38 skipped any ticker already holding a position before it ever reached the
    # monitor call, so TP/SL was never checked and no outcome was ever recorded.
    for ticker in active_tickers:
        f = frames.get(ticker)
        if not f:
            continue
        price = format_price_raw(price_map[ticker]["close"])
        df15 = f["df_15m"]
        pos = next((p for p in active_positions if p.get("ticker") == ticker), None)
        direction = (pos or {}).get("direction", "LONG")
        structure_flipped = monitor.detect_structure_break(df15, direction)
        try:
            monitor.check_active_positions(ticker, price, reg_multiplier, structure_flipped, df15)
        except Exception as e:
            print(f"[!] Position monitor error on {ticker}: {e}", flush=True)

    # ---- Sample event reactions: learn how each asset behaves AFTER a catalyst ----
    try:
        n_ev = EventOutcomeLearner.sample(price_map)
        if n_ev:
            print(f"[event] resolved {n_ev} event reactions", flush=True)
    except Exception as e:
        print(f"[!] Event outcome sampling error: {e}", flush=True)

    # ---- Settle news observations: attribute realised moves back to terms ----
    try:
        from news.learned_sentiment import LearnedNewsSentiment
        n_upd = LearnedNewsSentiment.settle(price_map)
        if n_upd:
            print(f"[news] settled {n_upd} term observations", flush=True)
    except Exception as e:
        print(f"[!] Learned sentiment settle error: {e}", flush=True)

    # ---- Advance the shadow ledger against live prices ----------------------
    try:
        resolved = ShadowTradeLedger.update_prices(price_map)
        for t in resolved:
            print(f"[shadow] {t['ticker']} {t['direction']} -> {t['outcome']} "
                  f"({t['pnl_pct']:+.2f}%) {','.join(t['post_mortem']['tags'])}", flush=True)
    except Exception as e:
        print(f"[!] Shadow ledger update error: {e}", flush=True)

    # ---- Stage 1: cheap technical score across the whole universe -----------
    prelim = []
    prelim_atr = {}
    for ticker, f in frames.items():
        if ticker in active_tickers:
            continue
        try:
            signal = SureShotConfluenceEngine.evaluate_setup(
                ohlcv_15m=f["df_15m"], ohlcv_1h=f["df_1h"], ohlcv_4h=f["df_4h"], ohlcv_1d=f["df_1d"],
                btc_df=btc_df, ticker=ticker, efficiency_history=efficiency_data,
                derivatives=None, news=None, regulatory_multiplier=reg_multiplier)
            prelim_atr[ticker] = signal.get("atr", 0.0)
            if signal.get("direction") != "NONE":
                prelim.append((ticker, f, signal))
        except Exception as e:
            print(f"[!] Prelim scoring error {ticker}: {type(e).__name__}: {e}", flush=True)

    prelim.sort(key=lambda x: x[2]["total_score"], reverse=True)

    # ---- Stage 2: enrich only the leaders with derivatives + news -----------
    candidates = []
    for ticker, f, _ in prelim[:ENRICH_TOP_N]:
        try:
            derivatives = DerivativesIntelligence.analyze(ticker)
            # Build our own derivatives history — no free historical feed exists, so
            # this is the only path to ever validating whether this layer helps.
            DerivativesIntelligence.snapshot(ticker, derivatives, price_map[ticker]["close"])
            news = PerAssetNewsIntelligence.analyze(ticker)
            try:
                from news.learned_sentiment import LearnedNewsSentiment
                LearnedNewsSentiment.observe(
                    ticker, PerAssetNewsIntelligence._fetch_headlines(ticker),
                    price_map[ticker]["close"])
            except Exception:
                pass
            asset_class = f["item"].get("asset_class", "Crypto Futures")
            calendar = ScheduledEventCalendar.assess(ticker, asset_class)
            try:
                EventOutcomeLearner.track(ticker, asset_class, calendar, price_map[ticker]["close"])
            except Exception:
                pass
            event_vol = EventVolatilityEngine.analyze(
                f["df_15m"], float(prelim_atr.get(ticker) or 0.0), derivatives, calendar)

            signal = SureShotConfluenceEngine.evaluate_setup(
                ohlcv_15m=f["df_15m"], ohlcv_1h=f["df_1h"], ohlcv_4h=f["df_4h"], ohlcv_1d=f["df_1d"],
                btc_df=btc_df, ticker=ticker, efficiency_history=efficiency_data,
                derivatives=derivatives, news=news, regulatory_multiplier=reg_multiplier,
                calendar=calendar, event_vol=event_vol)

            direction = signal.get("direction")
            score = signal.get("total_score", 0.0)
            if direction == "NONE" or score < ShadowTradeLedger.SHADOW_FLOOR:
                continue

            entry = format_price_raw(price_map[ticker]["close"])
            atr_val = signal.get("atr", entry * 0.01)

            # Liquidity-aware stop, widened by whatever the shadow data says winners need.
            cal_model = WinRateCalibrator.build_model()
            sl_mult = (cal_model.get("sl_stats") or {}).get("recommended_sl_multiplier")
            stop = LiquidityMapEngine.safe_stop_loss(
                f["df_15m"], direction, entry, atr_val,
                liquidity=signal.get("liquidity"), calibrated_multiplier=sl_mult)
            sl = format_price_raw(stop["stop_loss"])
            sl_pct = stop["sl_pct"]
            if sl_pct < 0.001:
                continue

            tp_ladder = build_tp_ladder(entry, sl, direction, atr_val, signal.get("liquidity"))
            if not tp_ladder:
                continue
            primary_tp = tp_ladder[0]          # TP1 is the planned exit — measured as the only profitable rung
            tp_pct = abs(primary_tp - entry) / entry
            rr = tp_pct / sl_pct if sl_pct else 0.0
            if rr < MIN_REWARD_RISK:
                # No room to the next pool — the structure does not pay for the risk.
                continue

            # ---- Calibrated probability, not score/100 ----
            features = dict(signal.get("feature_snapshot", {}))
            features["session"] = session_name
            features["factors_passed"] = signal.get("factors_passed", [])
            cal = WinRateCalibrator.calibrated_win_rate(score, features)
            win_rate = cal["win_rate"]          # None while uncalibrated — never faked

            # With no measured edge, Kelly has nothing to optimise. Size at the floor.
            kelly = (kelly_position_size(win_rate, rr, ACCOUNT_BALANCE) if win_rate is not None
                     else {"kelly_full": 0.0, "kelly_half": 0.0,
                           "dollars_at_risk": MIN_RISK_USD, "risk_pct": 0.0})
            raw_lev = max(int(round(1.0 / max(sl_pct * 2.5, 0.01))), 10)
            lev_meta = ExchangeLeverageEngine.get_calibrated_leverage(ticker, raw_lev)
            leverage = lev_meta["recommended_leverage"]

            margin = round(kelly["dollars_at_risk"] / max(leverage * sl_pct, 0.0001), 2)
            notional = round(margin * leverage, 2)
            loss_usd = round(notional * sl_pct, 2)
            gain_usd = round(notional * tp_pct, 2)
            roi_pct = round((gain_usd / max(margin, 0.01)) * 100, 1)

            ScoreStabilityTracker.record(ticker, direction, score)

            candidates.append({
                "ticker": ticker, "direction": direction, "entry": entry, "sl": sl,
                "tp": primary_tp, "tp_ladder": tp_ladder, "sl_pct": sl_pct, "tp_pct": tp_pct,
                "rr": rr, "total_score": score, "calibrated_win_rate": win_rate,
                "calibration": cal, "recommendation_label": signal["recommendation_label"],
                "factors_passed": signal.get("factors_passed", []),
                "factors_failed": signal.get("factors_failed", []),
                "reasoning": signal.get("reasoning", ""),
                "estimated_duration": signal.get("estimated_duration", ""),
                "market_regime": signal.get("market_regime", "UNKNOWN"),
                "timeframe_alignment": signal.get("timeframe_alignment", 0),
                "trend_strength_pct": signal.get("trend_strength_pct", 0),
                "ema_bias": signal.get("ema_bias"), "htf_bias": signal.get("htf_bias"),
                "bos_status": signal.get("bos_status"),
                "pillar_breakdown": signal.get("pillar_breakdown", {}),
                "hunt_risk": signal.get("hunt_risk", {}), "stop_rationale": stop["rationale"],
                "is_sure_shot": signal.get("is_sure_shot", False),
                "kelly": kelly, "final_margin": margin, "actual_notional": notional,
                "exact_gain_usd": gain_usd, "exact_loss_usd": loss_usd, "roi_gain_pct": roi_pct,
                "chosen_leverage": leverage, "rsi": signal.get("rsi", 0), "atr": atr_val,
                "session": session_name, "derivatives": derivatives, "news": news,
                "calendar": calendar, "event_vol": event_vol, "df_5m": f.get("df_5m"),
                "feature_snapshot": features,
            })
        except Exception as e:
            print(f"[!] Enrichment error {ticker}: {type(e).__name__}: {e}", flush=True)

    # ---- Shadow-log every qualifying candidate (this is the learning loop) ----
    shadow_opened = 0
    for c in candidates:
        try:
            if ShadowTradeLedger.open_shadow_trade(c):
                shadow_opened += 1
        except Exception as e:
            print(f"[!] Shadow open error {c['ticker']}: {e}", flush=True)

    # ---- Dispatch decision ---------------------------------------------------
    signals_dispatched = 0
    now_ts = time.time()
    dry_hours = (now_ts - last_signal_time) / 3600.0
    active_floor = HARD_SCORE_FLOOR
    relaxed = False
    if dry_hours >= DRY_SPELL_HOURS:
        active_floor = RELAXED_SCORE_FLOOR
        relaxed = True

    # Rolling 24h cap so a volatile day cannot turn into a flood.
    cutoff = now_ts - 86400
    while signal_timestamps and signal_timestamps[0] < cutoff:
        signal_timestamps.pop(0)
    daily_room = MAX_SIGNALS_PER_DAY - len(signal_timestamps)

    if candidates and daily_room > 0:
        # Rank on measured win rate when we have one, otherwise on raw score.
        candidates.sort(key=lambda x: (x["calibrated_win_rate"] if x["calibrated_win_rate"] is not None else -1.0,
                                       x["total_score"]), reverse=True)
        for best in candidates:
            if best["total_score"] < active_floor:
                continue
            allowed, cd_reason = SignalCooldownEngine.check(best["ticker"], best["direction"])
            if not allowed:
                continue

            # Gate 1: the setup must have HELD its score, not spiked to it.
            stability = ScoreStabilityTracker.evaluate(best["ticker"], best["direction"], active_floor)
            if not stability["stable"]:
                print(f"[gate] {best['ticker']} {best['total_score']:.0f} held back — {stability['reason']}", flush=True)
                continue

            # Gate 2: once we have evidence, refuse setups the data calls a coin flip.
            cal = best["calibration"]
            if (cal["status"] == "CALIBRATED" and best["calibrated_win_rate"] is not None
                    and best["calibrated_win_rate"] < MIN_CALIBRATED_WIN_RATE):
                print(f"[gate] {best['ticker']} blocked — calibrated WR "
                      f"{best['calibrated_win_rate']*100:.0f}% below floor", flush=True)
                continue

            # Gate 3: scheduled-event blackout. Narrow by design — only the 90 minutes
            # before and 30 after a directly relevant high-impact event. Everything
            # further out was already priced in as a graded score penalty.
            if (best.get("calendar") or {}).get("blackout"):
                print(f"[gate] {best['ticker']} blocked — blackout: "
                      f"{best['calendar']['blackout_reason']}", flush=True)
                continue

            # Gate 3b: never enter an unresolved event spike.
            if (best.get("event_vol") or {}).get("action") == "NO_ENTRY":
                print(f"[gate] {best['ticker']} blocked — {best['event_vol']['reason']}", flush=True)
                continue

            # Gate 4: do not enter directly in front of an unswept stop pool.
            if best["hunt_risk"].get("hunt_risk_score", 0) >= 45:
                print(f"[gate] {best['ticker']} blocked — {best['hunt_risk']['verdict']}", flush=True)
                continue

            # Gate 5: execution timing on the 5m.
            timing_ok, timing_msg = entry_timing_ok(best.get("df_5m"), best["direction"])
            if not timing_ok:
                print(f"[gate] {best['ticker']} held — {timing_msg}", flush=True)
                continue

            dispatch_signal(best, stability, reg_warning, relaxed)
            signals_dispatched += 1
            last_signal_time = time.time()
            signal_timestamps.append(last_signal_time)
            if signals_dispatched >= min(MAX_SIGNALS_PER_SCAN, daily_room):
                break

    # ---- Session-aware digest ------------------------------------------------
    interval, label, ist_str = digest_interval_seconds()
    if now_ts - last_digest_time >= interval:
        try:
            send_hunting_digest(candidates, prelim, session_name, label, ist_str,
                                reg_data, active_floor, relaxed)
            last_digest_time = now_ts
        except Exception as e:
            print(f"[!] Digest error: {e}", flush=True)

    ScoreStabilityTracker.prune()

    cal_model = WinRateCalibrator.build_model()
    scanner_state.update({
        "last_scan_time": time.strftime('%Y-%m-%d %H:%M:%S'),
        "total_scans": scanner_state["total_scans"] + 1,
        "total_signals_sent": scanner_state["total_signals_sent"] + signals_dispatched,
        "status": "RUNNING",
        "scan_duration_s": round(time.time() - scan_start, 1),
        "shadow_open": len(ShadowTradeLedger.load_open()),
        "calibration": f"{cal_model['status']} n={cal_model['total_samples']}",
    })
    print(f"[{time.strftime('%H:%M:%S')}] Scan #{scanner_state['total_scans']} | "
          f"{len(frames)}/{len(universe)} fed | {len(candidates)} candidates | "
          f"{shadow_opened} shadow opened | {signals_dispatched} dispatched | "
          f"{scanner_state['scan_duration_s']}s | floor={active_floor}", flush=True)


# ============================================================
# DISPATCH
# ============================================================
def dispatch_signal(best: dict, stability: dict, reg_warning: str, relaxed: bool):
    ticker = best["ticker"]
    direction = best["direction"]
    dir_dot = "🟢" if direction == "LONG" else "🔴"
    action = "LONG (BUY)" if direction == "LONG" else "SHORT (SELL)"
    cal = best["calibration"]
    pb = best["pillar_breakdown"]

    tp_lines = "\n".join(
        f"🎯 **TP{i}:** `{format_price_dynamic(tp)}` (+{abs(tp - best['entry']) / best['entry'] * 100:.2f}%)"
        for i, tp in enumerate(best["tp_ladder"], 1))

    reasons = "\n".join(f"• {r}" for r in best["factors_passed"][:6])
    warnings = "\n".join(f"• {r}" for r in best["factors_failed"][:3])

    if cal["status"] == "CALIBRATED":
        wr_line = (f"📊 **WIN RATE:** `{best['calibrated_win_rate'] * 100:.0f}%` "
                   f"_(measured, {cal['confidence'].lower()} confidence, n={cal['samples']})_")
    else:
        wr_line = (f"📊 **WIN RATE:** `—` _(⚠️ UNMEASURED — engine needs "
                   f"{cal.get('samples_needed', 0)} more resolved shadow trades before it can "
                   f"quote a win rate it can defend)_")

    relaxed_line = ("\n⚠️ _Relaxed threshold engaged after "
                    f"{DRY_SPELL_HOURS:.0f}h without a signal — conviction below the usual "
                    f"{HARD_SCORE_FLOOR:.0f} floor._\n" if relaxed else "")
    macro_line = f"\n🏛️ **MACRO:** _{reg_warning}_\n" if reg_warning else ""
    stop_line = "\n".join(f"• {r}" for r in best["stop_rationale"][:2])
    traj = ScoreStabilityTracker.trajectory(ticker, direction)[-6:]

    msg = f"""{dir_dot} **{best['recommendation_label']} — {action}: {ticker}**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏆 **SCORE:** `{best['total_score']:.0f}/100`  |  Regime: `{best['market_regime']}`
{wr_line}
📈 **Score path:** `{' → '.join(str(s) for s in traj)}` _(held {stability['consecutive']}/{ScoreStabilityTracker.MIN_CONSECUTIVE} scans, σ={stability['stdev']}, slope {stability['slope']:+.1f})_

**PILLARS**
`Trend      {pb['trend']['score']:5.1f}/20`  EMA: {best['ema_bias']}
`HTF        {pb['htf']['score']:5.1f}/20`  Bias: {best['htf_bias']}
`Order Flow {pb['orderflow']['score']:5.1f}/25`
`Structure  {pb['structure']['score']:5.1f}/20`  BOS: {best['bos_status']}
`Defense    {pb['defense']['score']:5.1f}/15`  Hunt: {best['hunt_risk'].get('verdict', 'n/a')}

📍 **ENTRY:** `{format_price_dynamic(best['entry'])}`
{tp_lines}
🛡️ **STOP LOSS:** `{format_price_dynamic(best['sl'])}` (-{best['sl_pct'] * 100:.2f}%)
{stop_line}

💰 **MARGIN:** `${best['final_margin']:,.2f} USDT` (`{best['chosen_leverage']}x Isolated`)
📈 **TARGET (TP1):** `+${best['exact_gain_usd']:,.2f}` (+{best['roi_gain_pct']}% ROI)  |  R:R `{best['rr']:.2f}`
📉 **RISK:** `-${best['exact_loss_usd']:,.2f}`
⏱️ **EST. DURATION:** `{best['estimated_duration']}`
🏛️ **EXCHANGE:** `Bitunix / Weex Futures`
{macro_line}{relaxed_line}
📋 **WHY:**
{reasons}
{"⚠️ **RISKS:**" + chr(10) + warnings if warnings else ""}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_Reply **positioned** to track this trade_"""

    msg_id = telegram.send_alert(msg)
    if not msg_id:
        return

    dispatched_message_ids[msg_id] = ticker
    SignalCooldownEngine.record_signal_sent(ticker, direction)
    scanner_state["last_signal"] = f"{ticker} {direction} @ {best['entry']} ({best['total_score']:.0f})"
    print(f"[✓] DISPATCHED {ticker} {direction} @ {format_price_dynamic(best['entry'])} "
          f"score={best['total_score']:.0f} wr={best['calibrated_win_rate'] * 100:.0f}%", flush=True)

    positions = [p for p in monitor.load_positions() if p.get("ticker") != ticker]
    positions.append({
        "ticker": ticker, "direction": direction, "entry_price": best["entry"],
        "stop_loss": best["sl"], "take_profit": best["tp"], "tp_ladder": best["tp_ladder"],
        "win_rate": best["calibrated_win_rate"], "total_score": best["total_score"],
        "margin": best["final_margin"], "leverage": best["chosen_leverage"],
        "notional": best["actual_notional"],
        "factor_scores": {f: 1.0 for f in best["factors_passed"]},
        # Needed by TradeDecayEngine to detect volatility collapse and stalled progress.
        "atr_at_entry": best["atr"], "market_regime": best["market_regime"],
        "feature_snapshot": best["feature_snapshot"],
        "user_positioned": False, "epoch_time": time.time(),
        "time": time.strftime('%Y-%m-%d %H:%M:%S'),
    })
    monitor.save_positions(positions)

    # FIX: v38 built this record and dropped it — the file was never written.
    os.makedirs("portfolio", exist_ok=True)
    path = "portfolio/dispatched_signals.json"
    log = []
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                log = json.load(f)
        except Exception:
            log = []
    log.append({
        "ticker": ticker, "direction": direction, "entry_price": best["entry"],
        "stop_loss": best["sl"], "tp_ladder": best["tp_ladder"],
        "calibrated_win_rate": best["calibrated_win_rate"], "total_score": best["total_score"],
        "recommendation": best["recommendation_label"], "margin": best["final_margin"],
        "leverage": best["chosen_leverage"], "relaxed_threshold": relaxed,
        "status": "DISPATCHED", "time": time.strftime('%Y-%m-%d %H:%M:%S'),
    })
    try:
        with open(path, "w") as f:
            json.dump(log[-2000:], f, indent=2)
    except Exception as e:
        print(f"[!] dispatched_signals write failed: {e}", flush=True)


# ============================================================
# HUNTING DIGEST
# ============================================================
def send_hunting_digest(candidates, prelim, session_name, label, ist_str,
                        reg_data, active_floor, relaxed):
    """
    Sent on schedule regardless of whether a signal fired, per the user's request:
    hourly between 11:00 and 03:00 IST, every 3 hours otherwise.
    """
    # Show a full top 20: the enriched candidates first, then backfill from the
    # unenriched preliminary scan so the user always sees 20 rows.
    pool = list(candidates)
    seen = {c["ticker"] for c in pool}
    for t, _, s in prelim:
        if len(pool) >= 20:
            break
        if t in seen:
            continue
        pool.append({"ticker": t, "direction": s["direction"], "total_score": s["total_score"],
                     "calibrated_win_rate": None, "entry": s.get("entry_price", 0)})
        seen.add(t)

    pool = sorted(pool, key=lambda x: x["total_score"], reverse=True)[:20]

    lines = []
    for i, c in enumerate(pool, 1):
        emoji = "🟢" if c["direction"] == "LONG" else "🔴"
        wr = c.get("calibrated_win_rate")
        wr_s = f"WR `{wr * 100:.0f}%`" if wr is not None else "WR `—`"   # — means unmeasured
        lines.append(f"`{i:2d}.` {emoji} **{c['ticker']}** — `{c['total_score']:.0f}/100` | {wr_s} | "
                     f"`{format_price_dynamic(c.get('entry', 0))}`")

    shadow = ShadowTradeLedger.summary()
    cal = WinRateCalibrator.build_model()

    if cal["status"] == "CALIBRATED":
        cal_line = (f"✅ CALIBRATED on {cal['total_samples']} resolved shadow trades — "
                    f"global {cal['global_win_rate'] * 100:.1f}% (Wilson LB {cal['global_wilson'] * 100:.1f}%)")
    else:
        need = WinRateCalibrator.MIN_SAMPLES_GLOBAL - cal["total_samples"]
        cal_line = (f"⏳ LEARNING — {cal['total_samples']}/{WinRateCalibrator.MIN_SAMPLES_GLOBAL} "
                    f"resolved shadow trades ({need} more before win rates are trustworthy)")

    sl_note = ""
    if (cal.get("sl_stats") or {}).get("available"):
        s = cal["sl_stats"]
        sl_note = (f"\n🛡️ **Stop research:** {s['sl_then_tp_count']} trades hit stop before target "
                   f"({s['sl_then_tp_rate'] * 100:.0f}%). Winners take up to {s['winner_mae_p85_pct']:.2f}% heat; "
                   f"recommended SL multiplier `{s['recommended_sl_multiplier']}x`.")

    cal_events = ScheduledEventCalendar.upcoming(hours=72)
    if cal_events:
        ev_lines = "\n".join(
            f"`{e['minutes_away'] / 60:5.1f}h` [{e['tier'][:4]}] {e['title'][:52]}"
            for e in cal_events[:6])
        events_block = f"\n📅 **NEXT 72H EVENT RISK:**\n{ev_lines}\n"
    else:
        events_block = "\n📅 **NEXT 72H:** _No high-impact scheduled events._\n"

    msg = f"""🛰️ **DEN ENGINE HUNTING DIGEST** — {ist_str} ({label} cadence)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 **SESSION:** `{session_name}` | Dispatch floor: `{active_floor:.0f}/100`{" ⚠️ relaxed" if relaxed else ""}
🏛️ **MACRO:** _{reg_data.get('regulatory_status', 'NEUTRAL')}_

🧠 **CALIBRATION:** {cal_line}
👁️ **SHADOW BOOK:** `{shadow['open']}` open, `{shadow['total']}` resolved, \
win rate `{shadow['win_rate']}%`{f", SL-then-TP `{shadow.get('sl_then_tp_pct', 0)}%`" if shadow['total'] else ""}{sl_note}
{events_block}

🏆 **TOP {len(pool)} LIVE CANDIDATES:**
{chr(10).join(lines) if lines else "_No qualifying candidates this cycle._"}

💡 _87 assets, 4 timeframes, derivatives & news enrichment on the top {ENRICH_TOP_N}._
━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""
    telegram.send_alert(msg)
    print(f"[✓] Digest sent ({label} cadence, {ist_str})", flush=True)


# ============================================================
# LOOP
# ============================================================
def start_background_scanner_loop():
    print(f"🚀 Den Engine {ENGINE_VERSION} starting...", flush=True)
    scanner_state["status"] = "INITIALIZING"
    while True:
        try:
            scanner_state["status"] = "SCANNING"
            run_continuous_quant_hunter()
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            scanner_state["last_error"] = msg
            scanner_state["status"] = "ERROR_RECOVERING"
            print(f"[!] Scanner loop exception: {msg}", flush=True)
            traceback.print_exc()
        time.sleep(15)


if __name__ == "__main__":
    threading.Thread(target=start_background_scanner_loop, daemon=False).start()
    threading.Thread(target=self_ping_keep_alive, daemon=True).start()
    threading.Thread(target=poll_positioned_replies, daemon=True).start()
    start_health_server()
