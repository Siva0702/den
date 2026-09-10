# models/auto_scanner.py
# Den Engine v39.0 — Calibrated Multi-Asset Quant Scanner
import os
import math
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

from indicators.execution import trade_cost_pct, LOGIC_VERSION, MAX_HOLD_SECONDS
from indicators.risk_policy import kelly_size, dispatch_eligibility, candidate_rank
from indicators.confluence_engine import SureShotConfluenceEngine
from indicators.exchange_leverage import ExchangeLeverageEngine
from indicators.liquidity_map import LiquidityMapEngine
from indicators.event_volatility import EventVolatilityEngine
from indicators.correlation_defense import CorrelationDefenseEngine
from alerts.signal_cooldown import SignalCooldownEngine
from alerts.telegram_bot import TelegramAlertBot
from audit.decision_report import build_status, save_status, render_status
from audit.engine_efficiency import EngineEfficiencyTracker
from audit.shadow_ledger import ShadowTradeLedger
from audit.dispatch_ledger import DispatchLedger
from audit.calibration import WinRateCalibrator
from audit.score_model import CalibratedScoreModel
from audit.scoring_context import CONTEXT_VERSION
from audit.score_tracker import ScoreStabilityTracker
from audit.redis_state_sync import UnifiedStateSync as GitStateSync
from data.exchange_feed import BitunixWeexLiveFeed
from data.liquidation_proxy import LiquidationProxy
from data.market_clock import market_clock_features
from data.derivatives_feed import DerivativesIntelligence
from news.market_universe import DynamicMarketUniverse
from news.news_intelligence import PerAssetNewsIntelligence
from news.event_calendar import ScheduledEventCalendar
from news.event_outcomes import EventOutcomeLearner
from position_monitor import ActivePositionMonitor

load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

ENGINE_VERSION = "v43.0-audited"
ACCOUNT_BALANCE = float(os.getenv("DEN_ACCOUNT_BALANCE", "1000"))
PUBLIC_HEALTH_URL = os.getenv("DEN_PUBLIC_HEALTH_URL", "http://13.140.188.62:10000/")

# Fixed candidate threshold; inactivity never lowers it.
HARD_SCORE_FLOOR = 78.0
ENRICH_TOP_N = 30
MAX_FETCH_WORKERS = 16
MIN_STOP_PCT = 0.008  # explicit planning assumption, to be tested prospectively
MAX_SIGNALS_PER_SCAN = 3
MAX_SIGNALS_PER_DAY = 8
# All monetary risk limits and candidate eligibility live in indicators/risk_policy.py.

monitor = ActivePositionMonitor(BOT_TOKEN, CHAT_ID)
telegram = TelegramAlertBot(BOT_TOKEN, CHAT_ID)

dispatched_message_ids = {}
last_update_id = 0
last_signal_time = time.time()
last_digest_time = 0.0
signal_timestamps = []          # rolling 24h dispatch history
PROCESS_START = time.time()
PRELIM_CACHE = {}               # ticker -> (candle_timestamp, signal)
LAST_SCAN_EPOCH = [time.time()]

scanner_state = {
    "status": "STARTING", "last_scan_time": "never", "total_scans": 0,
    "total_signals_sent": 0, "last_signal": "none", "last_error": "none",
    "assets_in_universe": 0, "scan_duration_s": 0.0, "shadow_open": 0,
    "calibration": "UNCALIBRATED", "phase": "init", "heartbeat": "never",
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
            requests.get(PUBLIC_HEALTH_URL, timeout=10)
        except Exception:
            pass


# ============================================================
# SIZING — Half Kelly on the CALIBRATED win rate
# ============================================================
def kelly_position_size(win_rate, reward_risk_ratio, account_balance, max_risk_pct=.01, cost_r=0.0, **payoffs):
    return kelly_size(win_rate, reward_risk_ratio, account_balance, max_risk_pct, cost_r, **payoffs)


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
def build_calendar_report(hours: int = 168) -> str:
    """On-demand 7-day event calendar, triggered by texting 'calendar' to the bot."""
    events = ScheduledEventCalendar.upcoming(hours=hours, tiers=("CRITICAL", "HIGH", "MEDIUM"))
    if not events:
        return ("📅 **EVENT CALENDAR**\n" + "━" * 28 +
                "\n_No scheduled high-impact events in the next 7 days._")

    by_day = {}
    for e in events:
        day = e["when_utc"][:10]
        by_day.setdefault(day, []).append(e)

    icon = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡"}
    blocks = []
    for day in sorted(by_day)[:7]:
        rows = "\n".join(
            f"  {icon.get(e['tier'], '⚪')} `{e['when_utc'][11:16]}` "
            f"{e['title'][:46]}"
            f"{'  _(' + str(round(e['minutes_away'] / 60, 1)) + 'h)_' if e['minutes_away'] < 48 * 60 else ''}"
            for e in by_day[day][:8])
        blocks.append(f"**{day}**\n{rows}")

    earnings = [e for e in events if e["kind"] == "EARNINGS"]
    macro = [e for e in events if e["kind"] == "MACRO"]
    return ("📅 **EVENT CALENDAR — NEXT 7 DAYS**\n" + "━" * 28 + "\n" +
            f"`{len(macro)}` macro · `{len(earnings)}` earnings · "
            f"🔴 critical 🟠 high 🟡 medium\n\n" +
            "\n\n".join(blocks) +
            "\n\n_Entries are blacked out ±90min before / 30min after a critical event "
            "for the affected asset; scores are graded down as events approach._\n" + "━" * 28)


def build_cohort_dashboard(vetoed: bool) -> str:
    """One dashboard per cohort. Separate reports because they answer different
    questions: the funded board is what the account actually experienced, the vetoed
    board is the running test of whether refusing those trades was correct."""
    d = ShadowTradeLedger.cohort_dashboard(vetoed=vetoed)
    if not d.get("available"):
        return f"📊 **{'KELLY-VETOED' if vetoed else 'KELLY-FUNDED'}** — _no resolved trades yet_"

    def tbl(bk, label):
        if not bk:
            return f"_{label}: no data_"
        rows = "\n".join(
            f"`{k:<9}` n`{v['n']:2d}` acc`{v['acc']:5.1f}%` `{v['avg_R']:+.3f}R`"
            for k, v in list(bk.items())[:6])
        return f"**{label}**\n{rows}"

    tk = lambda rs, e: "\n".join(
        f"{e} `{r['ticker']:<11}` `{r['R']:+6.2f}R` {r['w']}/{r['n']}" for r in rs) or "_none_"

    exits = " · ".join(f"`{k} {v}`" for k, v in sorted(d["outcomes"].items(), key=lambda x: -x[1]))
    icon = "🚫" if vetoed else "💰"
    note = ("_Trades Kelly REFUSED to fund. Tracked on paper only — this is the running\n"
            "test of whether the veto is correct._" if vetoed else
            "_Trades Kelly WOULD have funded. This is the board that reflects the account._")

    return f"""{icon} **{d['cohort']} DASHBOARD**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{note}

`{d['n']}` trades │ `{d['wins']}`W `{d['breakeven']}`BE `{d['losses']}`L │ **acc `{d['accuracy_pct']}%`**
Total `{d['total_R']:+.2f}R` │ avg `{d['avg_R']:+.3f}R` │ best `{d['best_R']:+.2f}R` │ worst `{d['worst_R']:+.2f}R`

**EXITS**
{exits}
`SL→TP {d['sl_then_tp']}` — accuracy would be `{d['acc_if_stops_fixed']}%` with stops fixed

{tbl(d['by_score_bin'], 'BY SCORE BIN')}

{tbl(d['by_regime'], 'BY REGIME')}

{tbl(d['by_direction'], 'BY DIRECTION')}

🏆 **BEST**
{tk(d['best_tickers'], '🟢')}

💀 **WORST**
{tk(d['worst_tickers'], '🔴')}

📏 median stop `{d['median_stop_pct']}%` │ MAE `{d['median_mae_pct']}%` │ MFE `{d['median_mfe_pct']}%` │ hold `{d['median_hold_h']}h`
━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""


def build_ledger_report() -> str:
    """Full on-demand ledger report, triggered by texting 'shadow ledger' to the bot."""
    s = ShadowTradeLedger.summary()
    rank = ShadowTradeLedger.performance_ranking(10)
    cal = WinRateCalibrator.build_model(force=True)

    def rows(items, sign):
        if not items:
            return "_no resolved trades yet_"
        return "\n".join(
            f"`{r['ticker']:<12}` {sign} `{r['net_pct']:+6.2f}%` | {r['wins']}/{r['trades']} "
            f"(`{r['win_rate']:.0f}%`)" for r in items)

    open_rows = "\n".join(
        f"`{t['ticker']:<12}` {'🟢' if t['direction']=='LONG' else '🔴'} score `{t['raw_score']:.0f}` "
        f"MAE `{t['mae_pct']:+.2f}%` MFE `{t['mfe_pct']:+.2f}%` rungs `{t['tp_levels_hit']}`"
        for t in sorted(ShadowTradeLedger.load_open(),
                        key=lambda x: -x['raw_score'])[:10]) or "_none open_"

    cal_line = (f"`{cal['status']}` on `{cal['total_samples']}` samples"
                if cal['status'] == 'CALIBRATED'
                else f"`LEARNING` — `{WinRateCalibrator.MIN_SAMPLES_GLOBAL - cal['total_samples']}` more needed")

    eq = ShadowTradeLedger.equity_curve(ACCOUNT_BALANCE)
    da = ShadowTradeLedger.directional_accuracy()
    kv = ShadowTradeLedger.kelly_veto_report()
    co = ShadowTradeLedger.cohort_report()
    vr = ShadowTradeLedger.version_report()
    kv = ShadowTradeLedger.kelly_veto_report()
    co = ShadowTradeLedger.cohort_report()
    exits = " · ".join(f"`{k} {v}`" for k, v in [
        ("SL_HIT", s['sl_hit']), ("SL_THEN_TP", s['sl_then_tp']),
        ("BREAKEVEN", s.get('breakeven', 0)), ("TP1", s['tp1']),
        ("TP2", s['tp2']), ("TP3", s['tp3']), ("TP4", s['tp4'])] if v)
    sd = ShadowTradeLedger.stop_diagnosis()
    stop_line = (f"\n🛡️ **STOPS** median `{sd['median_stop_pct']}%` · median MAE "
                 f"`{sd['median_mae_pct']}%` · ratio `{sd['ratio']}x` "
                 f"{'⚠️ too tight' if sd['too_tight'] else '✅'}" if sd.get('available') else "")

    return f"""📒 **SHADOW LEDGER**  `{vr['current_version']}`
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
`{s['total']}` trades │ `{s['wins']}` wins │ `{s.get('breakeven', 0)}` breakeven │ `{s['losses']}` losses
**Accuracy `{s['accuracy_pct']}%`**  ·  Total `{eq['total_R']:+.2f}R`  ·  Avg `{eq['avg_R']:+.3f}R`/trade
**Capital** `${eq['starting_capital']:,.0f}` → `${eq['final_equity']:,.2f}`  (`{eq['return_pct']:+.1f}%`)  ·  Max DD `{eq['max_drawdown_pct']:.1f}%`

{exits}{stop_line}

⚖️ **KELLY-FUNDED (the board)** `{co['kelly_funded'].get('n',0)}` trades │ \
`{co['kelly_funded'].get('wins',0)}`W/`{co['kelly_funded'].get('losses',0)}`L │ \
acc `{co['kelly_funded'].get('accuracy_pct',0)}%` │ `{co['kelly_funded'].get('total_R',0):+.2f}R`
`SL→TP {co['kelly_funded'].get('sl_then_tp',0)}` — acc would be \
`{co['kelly_funded'].get('accuracy_if_stops_fixed',0)}%` with stops fixed

_Cohorts frozen at open; `{co['untagged']}` legacy records unclassified._
🚫 **KELLY-VETOED (test only)** `{co['kelly_vetoed'].get('n',0)}` trades │ \
`{co['kelly_vetoed'].get('wins',0)}`W/`{co['kelly_vetoed'].get('losses',0)}`L │ \
acc `{co['kelly_vetoed'].get('accuracy_pct',0)}%` │ `{co['kelly_vetoed'].get('total_R',0):+.2f}R` │ \
`SL→TP {co['kelly_vetoed'].get('sl_then_tp',0)}`
_{co['veto_verdict']}_

⚖️ **LEGACY VETO TEST** — `{kv['vetoed_resolved']}` refused setups resolved\
{f" │ `{kv['wins']}`W/`{kv['losses']}`L │ `{kv['total_R']:+.2f}R` │ _{kv['verdict']}_" if kv.get('available') else " │ _none yet_"}

🧭 **DIRECTIONAL** — missed `{da['missed_setups']}` │ right `{da['missed_correct']}` │ wrong `{da['missed_wrong']}` │ `{da['missed_directional_pct'] if da['missed_directional_pct'] is not None else '—'}%`
_{da['interpretation']}_

👁️ Open `{s['open']}`  ·  stale records `{vr['stale_records']}` {'✅ calibration-safe' if vr['calibration_safe'] else '⚠️ replay pending'}

🏆 **TOP 10 PERFORMERS**
{rows(rank['best'], '🟢')}

💀 **WORST 10**
{rows(rank['worst'], '🔴')}

📋 **OPEN NOW (top 10 by score)**
{open_rows}

🧠 **CALIBRATION:** {cal_line}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""


def build_dispatch_report() -> str:
    rows = DispatchLedger.load()
    closed = [r for r in rows if r.get("status") == "CLOSED" and r.get("execution_version") == LOGIC_VERSION]
    old = sum(r.get("status") == "CLOSED" and r.get("execution_version") != LOGIC_VERSION for r in rows)
    opened = sum(r.get("status") == "OPEN" for r in rows)
    lines = ["DEN | PAPER SIGNAL RESULTS", f"Open: {opened} | resolved under current rules: {len(closed)}"]
    if closed:
        wins = sum(r.get("is_win") is True for r in closed)
        lines.append(f"Net wins: {wins}/{len(closed)} ({100*wins/len(closed):.1f}%)")
        known = [r for r in closed if r.get("net_pnl_usd") is not None]
        if known:
            lines.append(f"Estimated net P&L: ${sum(r['net_pnl_usd'] for r in known):+.2f} across {len(known)} sized trades")
        for r in closed[-3:]:
            lines.append(f"{r['ticker']} {r['direction']}: {r.get('exit_reason')} ({r.get('pnl_pct', 0):+.2f}% net)")
    else:
        lines.append("No current-version signal outcomes yet. Profitability is unproven.")
    if old:
        lines.append(f"{old} legacy outcomes retained separately; excluded from these results.")
    lines.append("Estimates include modelled costs. Actual exchange fills are not connected.")
    return "\n".join(lines)


def build_help_report() -> str:
    return ("DEN — three things to read\n"
            "/status — trade or wait, model evidence and dollar-risk policy\n"
            "/signals — dispatched paper outcomes after costs\n"
            "/calendar — scheduled events\n"
            "Only SIGNAL messages are setups. WAIT means no trade. "
            "Reply positioned to a signal if you entered it.")


def poll_positioned_replies():
    global last_update_id
    while True:
        try:
            # Poll ALL messages: commands are plain messages, not replies, so the
            # old reply-filtered poll could never see them.
            replies, new_offset = telegram.poll_all_messages(last_update_id)
            if new_offset > last_update_id:
                last_update_id = new_offset
            if True:
                for reply in replies:
                    text = reply.get("text", "").strip().lower()
                    rid = reply.get("reply_to_message_id")
                    # On-demand ledger report.
                    if text in ("/research_kelly",):
                        try:
                            telegram.send_alert(build_cohort_dashboard(vetoed=False))
                        except Exception as e:
                            print(f"[!] Kelly dashboard error: {e}", flush=True)
                        continue
                    if text in ("/veto", "veto") or ("veto" in text and "board" in text):
                        try:
                            telegram.send_alert(build_cohort_dashboard(vetoed=True))
                        except Exception as e:
                            print(f"[!] Veto dashboard error: {e}", flush=True)
                        continue
                    if "calendar" in text or text in ("/calendar", "/cal", "/events"):
                        try:
                            telegram.send_alert(build_calendar_report())
                        except Exception as e:
                            print(f"[!] Calendar report error: {e}", flush=True)
                        continue
                    if text in ("/signals", "/dispatch", "/dispatched", "signals"):
                        try:
                            telegram.send_alert(build_dispatch_report())
                        except Exception as e:
                            print(f"[!] Dispatch report error: {e}", flush=True)
                        continue
                    if text in ("/status", "status", "/kelly", "kelly"):
                        telegram.send_alert(render_status())
                        continue
                    if text in ("/help", "/commands", "help", "/start"):
                        try:
                            telegram.send_alert(build_help_report())
                        except Exception as e:
                            print(f"[!] Help report error: {e}", flush=True)
                        continue
                    if ("shadow" in text and "ledger" in text) or text in ("/ledger", "/shadow", "ledger"):
                        try:
                            telegram.send_alert(render_status())
                        except Exception as e:
                            print(f"[!] Ledger report error: {e}", flush=True)
                        continue
                    if rid in dispatched_message_ids and "position" in text:
                        ticker = dispatched_message_ids[rid]
                        with monitor._lock:
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
    df_15m, real = BitunixWeexLiveFeed.get_exchange_ohlcv(ticker, base_p, "15m", limit=260, closed_snapshot=True)
    if df_15m is None or not real or len(df_15m) < 100:
        out["ok"] = False
        return out
    out["ok"] = True
    out["df_15m"] = df_15m
    # 5m deliberately NOT fetched here. It is consumed by entry_timing_ok() on the
    # ONE candidate that reaches dispatch, so fetching it for all 87 assets threw away
    # 86 responses every scan — roughly 20% of all calls, for data nothing reads.
    # It is fetched lazily at the dispatch gate instead.
    for tf in ("1h", "4h", "1d"):
        df, _ = BitunixWeexLiveFeed.get_exchange_ohlcv(ticker, base_p, tf, closed_snapshot=True)
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
        return False, "no 5m data — entry timing cannot be verified"
    df_5m = df_5m.iloc[:-1]
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
    return [float(t) for t in targets[:4]]



def realised_range_pct(df_15m, bars: int = 4):
    """
    Absolute realised range over the prior hour, as a percentage of price.

    atr_percentile cannot express this: it is RELATIVE to an asset's own recent history,
    so a genuinely dead market still reads mid-range once it has been dead a while. This
    is the absolute measure — how far price actually travelled — which is what "stuck at
    the same price for hours" means. Closed bars only; the forming bar is incomplete.
    """
    try:
        if df_15m is None or len(df_15m) < bars + 1:
            return None
        w = df_15m.iloc[-(bars + 1):-1]
        hi, lo = float(w['high'].max()), float(w['low'].min())
        ref = float(w['close'].iloc[-1])
        return round((hi - lo) / ref * 100.0, 4) if ref else None
    except Exception:
        return None


# ============================================================
# MAIN SCAN
# ============================================================
def run_continuous_quant_hunter():
    global last_signal_time, last_digest_time

    scan_start = time.time()
    scanner_state["phase"] = "fetching"
    scanner_state["heartbeat"] = time.strftime('%Y-%m-%d %H:%M:%S')
    universe = DynamicMarketUniverse.get_full_hunting_universe()
    scanner_state["assets_in_universe"] = len(universe)

    active_positions = monitor.load_positions()
    active_tickers = {p.get("ticker") for p in active_positions if isinstance(p, dict)}

    efficiency_data = EngineEfficiencyTracker.load_efficiency_data()
    session_name, _ = get_current_session()

    # USRegulatoryPolicyEngine archived 2026-09-10 — see
    # models/_archive/unvalidated/WHY_REGULATORY_EVENTS_WAS_ARCHIVED.md
    # It returned 0.65 on 1903 of 1903 records: a constant, not a signal. Regulatory
    # catalysts already arrive through the event calendar with real proximity and
    # asset-relevance weighting. reg_multiplier stays at 1.0 for signature compatibility
    # (confluence_engine ignores it regardless).
    reg_data = {"regulatory_status": "NEUTRAL", "regulatory_multiplier": 1.0}
    reg_multiplier = 1.0
    reg_warning = ""

    # Calendar refresh runs in its own daemon thread (see calendar_refresher). A cold
    # pull takes ~17s locally and far longer on a free-tier box, and it was blocking
    # every scan before a single asset was fetched — the likely reason production sat
    # at total_scans=0. Scans now use whatever the calendar last cached.

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

    scanner_state["phase"] = f"fetched {len(frames)}/{len(universe)}"
    scanner_state["heartbeat"] = time.strftime('%Y-%m-%d %H:%M:%S')
    btc_df = frames.get("BTC/USDT", {}).get("df_15m")
    # Feed OHLC, not just close, so excursions between scans are not lost.
    price_map = {t: {"close": float(f["df_15m"].iloc[-1]['close']),
                     "high": float(f["df_15m"].iloc[-1]['high']),
                     "low": float(f["df_15m"].iloc[-1]['low'])}
                 for t, f in frames.items()}

    # Preserve every minute and its timestamp. Never collapse a path into a range.
    open_shadow = [t for t in ShadowTradeLedger.load_open() if t.get("config_version") == LOGIC_VERSION]
    target_tickers = set(frames) | active_tickers | {t["ticker"] for t in open_shadow}
    since = {}
    for p in open_shadow + active_positions:
        tk = p.get("ticker")
        cursor = p.get("last_bar_ts")
        op = p.get("opened_epoch", p.get("epoch_time", scan_start))
        start = cursor/1000 if cursor is not None else float(op)
        since[tk] = min(since.get(tk, start), start)

    def _m1(tk):
        position = next((p for p in active_positions + open_shadow if p.get("ticker") == tk), {})
        provider = (position.get("entry_provenance") or {}).get("provider")
        m1, real = BitunixWeexLiveFeed.get_exchange_ohlcv(tk, 0, "1m", limit=3, provider=provider)
        if m1 is None or not real:
            return tk, None
        bars = [dict(b, provider=m1.attrs.get("provider")) for b in m1.to_dict("records")]
        if tk in since and (int(since[tk]//60)+1)*60000 < int(m1.iloc[0]["timestamp"]):
            bars = BitunixWeexLiveFeed.execution_history(tk, since[tk], provider=provider or m1.attrs.get("provider"))
        return tk, {"close": float(m1.iloc[-1]["close"]),
                    "bars": bars, "quote_bar_ts": int(m1.iloc[-2]["timestamp"]),
                    "fetched_at": m1.attrs.get("fetched_at", 0)}

    with ThreadPoolExecutor(max_workers=MAX_FETCH_WORKERS) as pool:
        for fut in as_completed([pool.submit(_m1, tk) for tk in target_tickers]):
            try:
                tk, quote = fut.result()
                if quote:
                    price_map[tk] = quote
            except Exception as e:
                print(f"[data] minute feed unavailable: {type(e).__name__}", flush=True)

    for ticker in active_tickers:
        f = frames.get(ticker) or {}
        quote = price_map.get(ticker) or {}
        if not quote.get("bars"):
            continue
        pos = next((p for p in active_positions if p.get("ticker") == ticker), {})
        try:
            monitor.check_active_positions(
                ticker, quote["close"], reg_multiplier,
                monitor.detect_structure_break(f.get("df_15m"), pos.get("direction", "LONG")),
                f.get("df_15m"), bars=quote["bars"])
        except Exception as e:
            print(f"[!] Position monitor error on {ticker}: {e}", flush=True)
    active_positions = monitor.load_positions()
    active_tickers = {p.get("ticker") for p in active_positions}

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
        if resolved:
            try:
                ShadowTradeLedger.audit_integrity(repair=False)
                WinRateCalibrator.export_snapshot()
            except Exception:
                pass
            threading.Thread(target=GitStateSync.push_state,
                             args=("resolved trades",), daemon=True).start()
        for t in resolved:
            print(f"[shadow] {t['ticker']} {t['direction']} -> {t['outcome']} "
                  f"({t['pnl_pct']:+.2f}%) {','.join(t['post_mortem']['tags'])}", flush=True)
    except Exception as e:
        print(f"[!] Shadow ledger update error: {e}", flush=True)

    # ---- Stage 1: cheap technical score across the whole universe -----------
    scanner_state["phase"] = "screening"
    # CANDLE-GATED RESCORING. Scoring is deterministic in the bars it is given, so an
    # asset whose 15m candle has not closed since the last scan will produce exactly the
    # score it produced before. Reusing it costs nothing and skips the whole pillar
    # stack for most assets on most scans.
    prelim = []
    prelim_atr = {}
    reused = 0
    for ticker, f in frames.items():
        if ticker in active_tickers:
            continue
        try:
            bar_ts = int(f["df_15m"].iloc[-1].get("timestamp", 0) or 0)
            cached = PRELIM_CACHE.get(ticker)
            if cached and cached[0] == bar_ts:
                signal = cached[1]
                reused += 1
            else:
                signal = SureShotConfluenceEngine.evaluate_setup(
                    ohlcv_15m=f["df_15m"], ohlcv_1h=f["df_1h"], ohlcv_4h=f["df_4h"], ohlcv_1d=f["df_1d"],
                    btc_df=btc_df, ticker=ticker, efficiency_history=efficiency_data,
                    derivatives=None, news=None, regulatory_multiplier=reg_multiplier)
                PRELIM_CACHE[ticker] = (bar_ts, signal)
            prelim_atr[ticker] = signal.get("atr", 0.0)
            if signal.get("direction") != "NONE":
                prelim.append((ticker, f, signal))
        except Exception as e:
            print(f"[!] Prelim scoring error {ticker}: {type(e).__name__}: {e}", flush=True)

    prelim.sort(key=lambda x: x[2]["total_score"], reverse=True)

    # ---- Stage 2: enrich only the leaders with derivatives + news -----------
    candidates = []
    shadow_opened = 0
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
                f["df_15m"].iloc[:-1], float(prelim_atr.get(ticker) or 0.0), derivatives, calendar)

            signal = SureShotConfluenceEngine.evaluate_setup(
                ohlcv_15m=f["df_15m"], ohlcv_1h=f["df_1h"], ohlcv_4h=f["df_4h"], ohlcv_1d=f["df_1d"],
                btc_df=btc_df, ticker=ticker, efficiency_history=efficiency_data,
                derivatives=derivatives, news=news, regulatory_multiplier=reg_multiplier,
                calendar=calendar, event_vol=event_vol)

            direction = signal.get("direction")
            score = signal.get("total_score", 0.0)
            if direction == "NONE" or score < ShadowTradeLedger.SHADOW_FLOOR:
                continue

            # Refresh after enrichment, which may spend seconds fetching news.
            quote_df, quote_real = BitunixWeexLiveFeed.get_exchange_ohlcv(ticker, 0, "1m", limit=3)
            if quote_df is None or not quote_real or float(quote_df.iloc[-3:]["volume"].sum()) <= 0:
                continue
            price_map[ticker] = {"close": float(quote_df.iloc[-1]["close"]),
                                 "bars": [dict(b, provider=quote_df.attrs.get("provider")) for b in quote_df.to_dict("records")],
                                 "quote_bar_ts": int(quote_df.iloc[-2]["timestamp"]),
                                 "fetched_at": quote_df.attrs.get("fetched_at", 0)}
            entry = price_map[ticker]["close"]
            atr_val = signal.get("atr", entry * 0.01)

            # Liquidity-aware stop, widened by whatever the shadow data says winners need.
            sl_mult = None  # stop changes require independent replay evidence
            stop = LiquidityMapEngine.safe_stop_loss(
                f["df_15m"], direction, entry, atr_val,
                liquidity=signal.get("liquidity"), calibrated_multiplier=sl_mult)
            sl = float(stop["stop_loss"])
            sl_pct = abs(entry-sl)/entry
            if sl_pct < 0.001:
                continue

            # Explicit cost floor; widening changes R and the path-dependent outcome.
            # The model must validate records generated with these levels.
            if sl_pct < MIN_STOP_PCT:
                widened = MIN_STOP_PCT
                sl = entry * (1 - widened) if direction == "LONG" else entry * (1 + widened)
                sl_pct = widened

            tp_ladder = build_tp_ladder(entry, sl, direction, atr_val, signal.get("liquidity"))
            if not tp_ladder:
                continue
            primary_tp = tp_ladder[0]          # conservative planning reward; execution can trail further
            tp_pct = abs(primary_tp - entry) / entry
            rr = tp_pct / sl_pct if sl_pct else 0.0
            # NOTE: the R:R gate deliberately does NOT live here. It is a DISPATCH
            # criterion, and applying it during candidate construction silently starved
            # the shadow ledger — every setup that cleared the score floor was dropped
            # before it could be logged, so the engine stopped learning entirely.
            # Low-R:R setups are still recorded as virtual trades; R:R is carried in the
            # feature snapshot so calibration can measure whether 1.2 is the right cut
            # instead of us asserting it.

            # ---- Calibrated probability, not score/100 ----
            features = dict(signal.get("feature_snapshot", {}))
            features["scoring_context_version"] = CONTEXT_VERSION
            features["session"] = session_name
            features["taker_buy_sell_ratio"] = ((derivatives or {}).get("taker") or {}).get("taker_buy_sell_ratio")
            features["bid_ask_imbalance"] = ((derivatives or {}).get("book") or {}).get("bid_ask_imbalance")
            features["factors_passed"] = signal.get("factors_passed", [])
            features["reward_risk"] = round(rr, 3)
            features["sl_pct"] = round(sl_pct, 5)
            # Captured, NOT gated on. Weekday data says a liveness filter at entry costs
            # more than it saves (every threshold tested lowered average R), but that is
            # measured on weekdays only. These make the weekend answerable.
            features.update(market_clock_features())
            features["range_1h_pct"] = realised_range_pct(f["df_15m"])
            # Liquidation cascade read, derived from OI destruction rather than a paid
            # feed. Captured only; the model decides whether it carries any signal.
            try:
                _d15 = f["df_15m"].iloc[:-1]
                _chg = ((float(_d15['close'].iloc[-1]) - float(_d15['close'].iloc[-13]))
                        / float(_d15['close'].iloc[-13]) * 100.0) if len(_d15) >= 13 else None
                features.update(LiquidationProxy.features(LiquidationProxy.analyze(
                    (derivatives or {}).get("open_interest"), _chg,
                    (derivatives or {}).get("crowding"))))
            except Exception:
                pass

            # ML Calibrated Score Model
            score_mod = CalibratedScoreModel.score(features, direction, signal.get("timeframe_alignment", 0))
            model_avail = bool(score_mod.get("available"))
            model_score = score_mod.get("score") if model_avail else None
            model_prob = score_mod.get("prob") if model_avail else None
            effective_score = model_score if (model_avail and model_score is not None) else score

            # WinRateCalibrator bin lookup stays on the pillar/adjusted scale `score`
            # Kelly position sizing consumes model_prob directly when model is available
            win_rate = score_mod.get("prob_lower") if model_avail else None
            cal = {"status": "CALIBRATED" if score_mod.get("tradable") else "UNCALIBRATED",
                   "win_rate": win_rate, "samples": score_mod.get("samples", 0),
                   "basis": score_mod.get("reason")}

            funding_rate = float(((derivatives or {}).get("funding") or {}).get("funding_rate", 0) or 0)
            cost_pct = trade_cost_pct(entry, primary_tp, MAX_HOLD_SECONDS/3600, funding_rate)/100
            cost_r = cost_pct/sl_pct
            kelly = kelly_position_size(win_rate, rr, ACCOUNT_BALANCE, cost_r=cost_r,
                                        win_payoff_r=score_mod.get("win_payoff_r"),
                                        loss_payoff_r=score_mod.get("loss_payoff_r"))
            # Cap the REQUEST as well as the exchange limit. A 0.4% stop asks for 100x,
            # and while the venue cap trims it, requesting absurd leverage means the
            # binding constraint is the exchange rather than our own risk view.
            # Kelly vetoed this bet: it is barred from DISPATCH, but still tracked as a
            # shadow trade so the veto itself can be proven right or wrong from outcomes.
            kelly_vetoed = bool(kelly.get("veto"))
            # Request cap raised 40 -> 50 so the BINDING constraint is liquidation
            # distance and the venue cap, not an arbitrary number. Leverage does not
            # change risk while the stop holds — risk is stop distance x size. It
            # changes margin consumed, so freeing margin lets one $1000 account carry
            # more concurrent positions at the same risk per trade. The liquidation
            # cap in get_calibrated_leverage still trims anything that would put
            # liquidation near the stop.
            # No arbitrary ceiling. Ask for the most leverage the LIQUIDATION distance
            # allows, and let the real venue cap trim it. The old `min(..., 50)` meant
            # an asset offering 150x was held to 50x for no risk reason, wasting margin
            # that could carry another position. Leverage does not change risk while the
            # stop holds — it changes how much margin the same risk consumes.
            raw_lev = max(ExchangeLeverageEngine.liquidation_safe_leverage(sl_pct), 5)
            lev_meta = ExchangeLeverageEngine.get_calibrated_leverage(ticker, raw_lev, sl_pct=sl_pct)
            leverage = lev_meta["recommended_leverage"]

            modeled_loss_pct = sl_pct * kelly.get("loss_payoff_r", 1 + cost_r)
            margin = math.floor(kelly["dollars_at_risk"] / max(leverage * modeled_loss_pct, 0.0001) * 100)/100
            notional = round(margin * leverage, 2)
            loss_usd = round(notional * modeled_loss_pct, 2)
            gain_usd = round(notional * (tp_pct - cost_pct), 2)
            roi_pct = round((gain_usd / max(margin, 0.01)) * 100, 1)

            ScoreStabilityTracker.record(ticker, direction, effective_score,
                                         observation_id=price_map[ticker]["quote_bar_ts"])

            candidates.append({
                "ticker": ticker, "direction": direction, "entry": entry, "sl": sl,
                "tp": primary_tp, "tp_ladder": tp_ladder, "sl_pct": sl_pct, "tp_pct": tp_pct,
                "rr": rr, "total_score": effective_score, "calibrated_win_rate": win_rate,
                "pillar_score": signal.get("pillar_score"),
                "adjusted_score": score,
                "learned_adjustment": signal.get("learned_adjustment"),
                "model_score": model_score,
                "model_prob": model_prob,
                "entry_provenance": {"provider": quote_df.attrs.get("provider"),
                                     "fetched_at": quote_df.attrs.get("fetched_at"),
                                     "bar_timestamp": int(quote_df.iloc[-1]["timestamp"]),
                                     "price": entry, "price_type": "forming_1m_close",
                                     "ticker": ticker},
                "model_evidence": score_mod, "model_version": score_mod.get("version"),
                "funding_rate": funding_rate,
                "quote_fresh": time.time()-price_map[ticker].get("fetched_at", 0) <= 60,
                # Label off the score the GATES use. It was computed inside the
                # confluence engine from the pillar sum (>=85/75/55), so a setup could
                # dispatch at model 89 while displaying "NO TRADE" from a pillar 40.
                "calibration": cal,
                "recommendation_label": (
                    "\U0001F525 HIGH CONVICTION" if effective_score >= 90 else
                    "\u26A1 HIGH CONVICTION" if effective_score >= 78 else
                    "\u2705 QUALIFIED" if effective_score >= 60 else
                    "\u274C NO TRADE") if model_avail else signal["recommendation_label"],
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
                "live_price": float(price_map[ticker]["close"]),
                "candle_ts": int(f["df_15m"].iloc[-1].get("timestamp", 0) or 0),
                "kelly_vetoed": kelly_vetoed, "kelly_full": kelly.get("kelly_full"),
                "session": session_name, "derivatives": derivatives, "news": news,
                "calendar": calendar, "event_vol": event_vol, "df_5m": f.get("df_5m"),
                "feature_snapshot": features,
            })
            # Open at this quote immediately. Waiting for every asset's enrichment
            # made early candidates enter retrospectively at minutes-old prices.
            if ShadowTradeLedger.open_shadow_trade(candidates[-1]):
                shadow_opened += 1
        except Exception as e:
            print(f"[!] Enrichment error {ticker}: {type(e).__name__}: {e}", flush=True)

    all_candidates = list(candidates)
    for c in all_candidates:
        c["dispatch_decision"] = dispatch_eligibility(c, active_positions, ACCOUNT_BALANCE, HARD_SCORE_FLOOR)

    # ---- Dispatch decision ---------------------------------------------------
    signals_dispatched = 0
    dispatched_this_scan = []
    now_ts = time.time()
    active_floor = HARD_SCORE_FLOOR
    relaxed = False

    # Rolling 24h cap so a volatile day cannot turn into a flood.
    cutoff = now_ts - 86400
    while signal_timestamps and signal_timestamps[0] < cutoff:
        signal_timestamps.pop(0)
    daily_room = MAX_SIGNALS_PER_DAY - len(signal_timestamps)

    if candidates and daily_room > 0:
        candidates.sort(key=candidate_rank, reverse=True)
        # CORRELATION PRE-SELECTION.
        # Candidates are already ranked by calibrated win rate then score, but relying on
        # arrival order to pick the survivor is fragile — a higher-ranked candidate can
        # fail an earlier gate and leave a weaker correlated peer to slip through. Choose
        # the single strongest member of each correlated group up front, so what reaches
        # the gates is the best available representative rather than the first one seen.
        by_group = {}
        filtered = []
        for c in candidates:
            grp = None
            for g, members in CorrelationDefenseEngine.CORRELATED_GROUPS.items():
                if c["ticker"] in members:
                    grp = f"{g}|{c['direction']}"
                    break
            if grp is None:
                filtered.append(c)
                continue
            prev = by_group.get(grp)
            key = candidate_rank(c)
            if prev is None or key > prev[0]:
                by_group[grp] = (key, c)
        filtered.extend(c for _, c in by_group.values())
        filtered.sort(key=candidate_rank, reverse=True)
        dropped = len(candidates) - len(filtered)
        if dropped:
            print(f"[corr] {dropped} correlated peers dropped in favour of the strongest in each group",
                  flush=True)
        candidates = filtered

        for best in candidates:
            if best["total_score"] < active_floor:
                continue
            eligibility = dispatch_eligibility(best, monitor.load_positions(), ACCOUNT_BALANCE, active_floor)
            best["dispatch_decision"] = eligibility
            if not eligibility["allowed"]:
                print(f"[gate] {best['ticker']}: {'; '.join(eligibility['reasons'])}", flush=True)
                continue
            allowed, cd_reason = SignalCooldownEngine.check(best["ticker"], best["direction"])
            if not allowed:
                best["dispatch_decision"] = {"allowed": False, "reasons": [cd_reason]}
                continue

            # Gate 1: the setup must have HELD its score, not spiked to it.
            stability = ScoreStabilityTracker.evaluate(best["ticker"], best["direction"], active_floor)
            if not stability["stable"]:
                best["dispatch_decision"] = {"allowed": False, "reasons": [stability["reason"]]}
                print(f"[gate] {best['ticker']} {best['total_score']:.0f} held back — {stability['reason']}", flush=True)
                continue

            # Gate 5: execution timing on the 5m.
            # Lazy 5m fetch: one call, only for the setup about to be dispatched.
            _df5 = best.get("df_5m")
            if _df5 is None:
                try:
                    _r = BitunixWeexLiveFeed.get_exchange_ohlcv(best["ticker"], 0, "5m")
                    _df5 = _r[0] if isinstance(_r, tuple) else _r
                except Exception:
                    _df5 = None
            timing_ok, timing_msg = entry_timing_ok(_df5, best["direction"])
            if not timing_ok:
                best["dispatch_decision"] = {"allowed": False, "reasons": [timing_msg]}
                print(f"[gate] {best['ticker']} held — {timing_msg}", flush=True)
                continue

            # Gate 6: correlation. Three correlated longs in one scan is one bet at
            # triple size, not three signals.
            ok_corr, corr_why = CorrelationDefenseEngine.check_pending(
                best["ticker"], best["direction"], monitor.load_positions() + dispatched_this_scan)
            if not ok_corr:
                best["dispatch_decision"] = {"allowed": False, "reasons": [corr_why]}
                print(f"[gate] {best['ticker']} blocked — {corr_why}", flush=True)
                continue

            if not dispatch_signal(best, stability, reg_warning, relaxed):
                best["dispatch_decision"] = {"allowed": False, "reasons": ["quote moved or signal delivery failed"]}
                continue
            best["signal_sent"] = True
            dispatched_this_scan.append({"ticker": best["ticker"], "direction": best["direction"]})
            signals_dispatched += 1
            last_signal_time = time.time()
            signal_timestamps.append(last_signal_time)
            if signals_dispatched >= min(MAX_SIGNALS_PER_SCAN, daily_room):
                break

    model = CalibratedScoreModel.build()
    status = build_status(model, all_candidates, signals_dispatched, monitor.load_positions())
    save_status(status)
    scanner_state.update(decision=status["decision"], model_status=status["model_status"],
                         decision_reason=status["reason"], verified_outcomes=status["verified_outcomes"])

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

    LAST_SCAN_EPOCH[0] = time.time()
    scanner_state.update({
        "last_scan_time": time.strftime('%Y-%m-%d %H:%M:%S'),
        "total_scans": scanner_state["total_scans"] + 1,
        "total_signals_sent": scanner_state["total_signals_sent"] + signals_dispatched,
        "status": "RUNNING",
        "scan_duration_s": round(time.time() - scan_start, 1),
        "shadow_open": len(ShadowTradeLedger.load_open()),
        "calibration": f"{status['model_status']} n={status['verified_outcomes']}",
    })
    print(f"[{time.strftime('%H:%M:%S')}] Scan #{scanner_state['total_scans']} | "
          f"{len(frames)}/{len(universe)} fed | {len(candidates)} candidates | "
          f"{shadow_opened} shadow | {reused} reused | {signals_dispatched} dispatched | "
          f"{scanner_state['scan_duration_s']}s | floor={active_floor}", flush=True)


# ============================================================
# DISPATCH
# ============================================================
def dispatch_signal(best: dict, stability: dict, reg_warning: str, relaxed: bool):
    ticker = best["ticker"]
    fresh, real = BitunixWeexLiveFeed.get_exchange_ohlcv(ticker, 0, "1m", limit=3,
                            provider=(best.get("entry_provenance") or {}).get("provider"))
    if fresh is None or not real or abs(float(fresh.iloc[-1]["close"])-best["entry"]) > abs(best["entry"]-best["sl"])*.1:
        return False
    direction = best["direction"]
    msg = (f"SIGNAL | {ticker} {direction}\n"
           f"Entry: {format_price_dynamic(best['entry'])}\n"
           f"Stop: {format_price_dynamic(best['sl'])}\n"
           f"Targets: {', '.join(format_price_dynamic(t) for t in best['tp_ladder'])}\n\n"
           f"Model win estimate: {(best.get('model_prob') or 0)*100:.0f}% "
           f"(sizing bound: {best['calibrated_win_rate']*100:.0f}%).\n"
           f"Estimated average net result at this size: ${best['kelly']['expected_net_R'] * best['actual_notional'] * best['sl_pct']:+.2f} per trade.\n"
           f"Half-Kelly risk including estimated costs: ${best['exact_loss_usd']:.2f} "
           f"({best['kelly']['risk_pct']:.2f}% of configured capital).\n"
           f"Margin: ${best['final_margin']:.2f} at {best['chosen_leverage']}x isolated.\n\n"
           "Exit rule: after a target is reached, trail to that target from the next minute. "
           "Close at the final target or stop. Gaps can exceed the planned loss.\n"
           "Reply positioned if entered. Prices and P&L are estimates, not exchange fills.")

    msg_id = telegram.send_alert(msg)
    if not msg_id:
        return False

    dispatch_id = DispatchLedger.record_dispatch(best)
    if not dispatch_id:
        scanner_state["last_error"] = "Signal delivered but dispatch audit write failed"
    dispatched_message_ids[msg_id] = ticker
    SignalCooldownEngine.record_signal_sent(ticker, direction)
    scanner_state["last_signal"] = f"{ticker} {direction} @ {best['entry']} ({best['total_score']:.0f})"
    print(f"[✓] DISPATCHED {ticker} {direction} @ {format_price_dynamic(best['entry'])} "
          f"score={best['total_score']:.0f} wr={best['calibrated_win_rate'] * 100:.0f}%", flush=True)

    positions = [p for p in monitor.load_positions() if p.get("ticker") != ticker]
    positions.append({
        "ticker": ticker, "direction": direction, "entry_price": best["entry"],
        "dispatch_id": dispatch_id,
        "stop_loss": best["sl"], "take_profit": best["tp"], "tp_ladder": best["tp_ladder"],
        "win_rate": best["calibrated_win_rate"], "total_score": best["total_score"],
        "margin": best["final_margin"], "leverage": best["chosen_leverage"],
        "notional": best["actual_notional"],
        "planned_risk_usd": best["exact_loss_usd"],
        "factor_scores": {f: 1.0 for f in best["factors_passed"]},
        # Needed by TradeDecayEngine to detect volatility collapse and stalled progress.
        "atr_at_entry": best["atr"], "market_regime": best["market_regime"],
        "feature_snapshot": best["feature_snapshot"],
        "user_positioned": False, "epoch_time": time.time(),
        "config_version": LOGIC_VERSION, "model_version": best.get("model_version"),
        "funding_rate": best.get("funding_rate", 0),
        "entry_provenance": best.get("entry_provenance"),
        "time": time.strftime('%Y-%m-%d %H:%M:%S'),
    })
    monitor.save_positions(positions)

    # FIX: v38 built this record and dropped it — the file was never written.
    os.makedirs(os.path.join(os.path.dirname(__file__), "portfolio"), exist_ok=True)
    path = os.path.join(os.path.dirname(__file__), "portfolio/dispatched_signals.json")
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

    return True


# ============================================================
# HUNTING DIGEST
# ============================================================
def send_hunting_digest(candidates, prelim, session_name, label, ist_str,
                        reg_data, active_floor, relaxed):
    if telegram.send_alert(render_status()):
        print(f"[status] delivered at {ist_str}", flush=True)


# ============================================================
# LOOP
# ============================================================
def start_background_scanner_loop():
    print(f"🚀 Den Engine {ENGINE_VERSION} starting...", flush=True)
    scanner_state["status"] = "INITIALIZING"
    # Restore ledger/lexicon/derivatives history written before the last restart.
    try:
        GitStateSync.pull_on_startup()
    except Exception as e:
        print(f"[sync] startup pull error (continuing): {e}", flush=True)
    # Seed the calibrator from the 2.2KB model snapshot so a cold container starts
    # CALIBRATED instead of quoting "—" for weeks. Live records override it as soon
    # as enough of them exist.
    # Audit the restored ledger BEFORE anything reads it. A corrupted pull would
    # otherwise poison calibration for the whole session.
    vr = ShadowTradeLedger.version_report()
    if vr["needs_replay"]:
        print(f"[ledger] {vr['stale_records']} legacy outcomes quarantined; "
              "verified replay is required before model training", flush=True)
    # Historical and open records remain intact; no destructive automatic migration.
    signal_timestamps[:] = sorted(float(r["dispatched_epoch"]) for r in DispatchLedger.load()
                                 if float(r.get("dispatched_epoch") or 0) > time.time()-86400)
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


def calendar_refresher():
    """Keeps the 30-day calendar warm without ever blocking a scan."""
    while True:
        try:
            ScheduledEventCalendar.refresh()
        except Exception as e:
            print(f"[!] Calendar refresher: {e}", flush=True)
        time.sleep(1800)


def learning_reporter():
    """
    Hourly self-learning report to Telegram.

    The ledger is only useful if it is visibly alive. This reports what the engine has
    actually resolved and learned in the last hour, and — critically — shouts if the
    ledger has gone STALE, because a silent learning loop is indistinguishable from a
    working one until you look, and by then weeks are gone.
    """
    time.sleep(120)          # let the first scan land before reporting
    last_resolved = 0
    while True:
        try:
            summary = ShadowTradeLedger.summary()
            closed = ShadowTradeLedger.load_closed()
            model = WinRateCalibrator.build_model(force=True)
            deriv = DerivativesIntelligence.history_stats()

            new_since = summary["total"] - last_resolved
            last_resolved = summary["total"]

            hb = scanner_state.get("heartbeat", "never")
            scans = scanner_state.get("total_scans", 0)
            # A first scan takes ~150s on Render. The reporter previously fired at
            # T+120s and screamed STALE while the very first scan was still running —
            # a false alarm on every restart. Only warn once enough time has passed
            # that a scan should genuinely have finished.
            uptime = time.time() - PROCESS_START
            stale = (scans == 0 and uptime > 600) or (
                scans > 0 and (time.time() - LAST_SCAN_EPOCH[0]) > 1800)

            recent = closed[-5:]
            rows = "\n".join(
                f"`{t['ticker']:<11}` {'🟢' if t['is_win'] else '🔴'} {t['outcome']:<11} "
                f"`{t['pnl_pct']:+.2f}%` score `{t['raw_score']:.0f}`"
                for t in reversed(recent)) or "_none resolved yet_"

            if model["status"] == "CALIBRATED":
                bins = "\n".join(
                    f"`{b['range']:>7}` {b['wins']:3d}/{b['n']:<4d} = `{b['raw_rate']*100:4.1f}%`"
                    for _, b in sorted(model["score_bins"].items(), key=lambda kv: int(kv[0])))
            else:
                bins = f"_needs {WinRateCalibrator.MIN_SAMPLES_GLOBAL - model['total_samples']} more resolved trades_"

            if stale:
                warn = (f"\n🚨 **LEDGER STALE** — no scan completed in "
                        f"{uptime / 60:.0f} min. Learning is NOT happening.\n")
            elif scans == 0:
                warn = f"\n⏳ _First scan in progress ({uptime:.0f}s elapsed, ~150s typical)._\n"
            else:
                warn = ""

            msg = f"""🧠 **SELF-LEARNING REPORT** — {datetime.now(IST).strftime('%H:%M IST')}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━{warn}
⚙️ Scans: `{scans}` | last: `{scanner_state.get('last_scan_time')}` | phase: `{scanner_state.get('phase', '-')}`

👁️ **LEDGER:** `{summary['open']}` open, `{summary['total']}` resolved (`+{new_since}` this hour)
📊 **Win rate:** `{summary['win_rate']}%` | SL-then-TP `{summary.get('sl_then_tp_pct', 0)}%`
📉 Avg MAE `{summary.get('avg_mae_pct', 0)}%` | Avg MFE `{summary.get('avg_mfe_pct', 0)}%`

🕐 **LAST RESOLVED:**
{rows}

🎯 **SCORE BINS (measured):**
{bins}

💾 **STATE SYNC:** `{GitStateSync.status()}`
🔬 **DERIVATIVES HISTORY:** `{deriv['rows']}` rows, `{deriv['tickers']}` assets, `{deriv['span_hours']}h` span
━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""
            telegram.send_alert(msg)
            print(f"[✓] Learning report sent (resolved={summary['total']}, +{new_since})", flush=True)
        except Exception as e:
            print(f"[!] Learning reporter error: {e}", flush=True)
        time.sleep(3600)


if __name__ == "__main__":
    threading.Thread(target=start_background_scanner_loop, daemon=False).start()
    threading.Thread(target=self_ping_keep_alive, daemon=True).start()
    threading.Thread(target=poll_positioned_replies, daemon=True).start()
    threading.Thread(target=calendar_refresher, daemon=True).start()
    threading.Thread(target=learning_reporter, daemon=True).start()
    threading.Thread(target=GitStateSync.sync_daemon, daemon=True).start()
    start_health_server()
