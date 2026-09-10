"""Deterministic one-minute execution, shared by live monitoring and replay.

OHLC cannot establish intrabar order. An already armed stop wins ties; a newly
tagged rung arms on the following bar. Opening gaps fill at the worse open.
Only complete, contiguous minutes strictly after entry can label a trade.
"""
import math
import time

LOGIC_VERSION = "v5-ordered-1m-cost-aware"
FEATURE_VERSION = "v2-closed-bars-directional"
TAKER_FEE = 0.0006  # explicit modelling assumption per side, not a venue quote
SLIPPAGE_BPS = 2.0  # stress allowance per side; actual fills are not available
MAX_HOLD_SECONDS = 36 * 3600


def finite(value, default=None):
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def trade_cost_pct(entry, exit_price, hold_hours=0, funding_rate=0):
    """Percent of entry notional. No assumed funding credits; costs may be worse."""
    fees = TAKER_FEE * (1 + exit_price / entry)
    slippage = SLIPPAGE_BPS / 10000 * (1 + exit_price / entry)
    funding = abs(finite(funding_rate, 0)) * math.ceil(max(0, hold_hours) / 8)
    return 100 * (fees + slippage + funding)


def advance_trade(trade, bars, now=None):
    """Mutate cursor/excursions; return an exit event or None. Never does I/O."""
    now = time.time() if now is None else now
    entry = finite(trade.get("entry", trade.get("entry_price")))
    stop = finite(trade.get("stop_loss"))
    opened = finite(trade.get("opened_epoch", trade.get("epoch_time")))
    direction = trade.get("direction")
    ladder = [finite(x) for x in (trade.get("tp_ladder") or [trade.get("take_profit")])]
    sign = 1 if direction == "LONG" else -1
    if (not entry or not stop or not opened or direction not in ("LONG", "SHORT") or
            not ladder or any(x is None or x <= 0 for x in ladder) or
            (entry - stop) * sign <= 0 or
            any((b - a) * sign <= 0 for a, b in zip([entry] + ladder, ladder))):
        trade["data_quality_error"] = "invalid trade levels"
        return None
    cursor = trade.get("last_bar_ts")
    expected = int(cursor + 60000) if cursor is not None else (int(opened // 60) + 1) * 60000
    seen = set()
    for bar in sorted(bars or [], key=lambda b: b.get("timestamp", 0)):
        ts = finite(bar.get("timestamp"))
        if ts is None or ts <= opened * 1000 or (cursor is not None and ts <= cursor):
            continue
        if ts + 60000 > now * 1000:
            continue
        if ts in seen:
            continue
        seen.add(ts)
        if ts != expected:
            trade["data_quality_error"] = "missing one-minute candles"
            return None
        provider = (trade.get("entry_provenance") or {}).get("provider")
        if provider and bar.get("provider") != provider:
            trade["data_quality_error"] = "execution provider mismatch"
            return None
        o, hi, lo, close = [finite(bar.get(k)) for k in ("open", "high", "low", "close")]
        if (any(x is None or x <= 0 for x in (o, hi, lo, close)) or
                hi < max(o, lo, close) or lo > min(o, hi, close)):
            trade["data_quality_error"] = "invalid one-minute candle"
            return None
        trade.pop("data_quality_error", None)
        trade["last_bar_ts"] = cursor = ts
        expected = ts + 60000
        trade["bars_observed"] = trade.get("bars_observed", 0) + 1
        trade["last_price"] = close
        trade["mae_pct"] = min(trade.get("mae_pct", 0), (lo-entry)/entry*100 if sign > 0 else (entry-hi)/entry*100)
        trade["mfe_pct"] = max(trade.get("mfe_pct", 0), (hi-entry)/entry*100 if sign > 0 else (entry-lo)/entry*100)
        hits = trade.setdefault("tp_levels_hit", [])
        rung = max(hits or [0])
        level = ladder[rung-1] if rung else stop
        stopped = lo <= level if sign > 0 else hi >= level
        outcome = None
        if stopped:
            exit_price = min(o, level) if sign > 0 else max(o, level)
            outcome = f"TP{rung}_HIT" if rung else "SL_HIT"
            trade["sl_touched"] = rung == 0
            trade["sl_touched_epoch"] = ts/1000 if rung == 0 else None
        else:
            probe = hi if sign > 0 else lo
            for i, target in enumerate(ladder, 1):
                if (probe-target)*sign >= 0 and i not in hits:
                    hits.append(i)
                    
                    if trade.get("first_tp_epoch") is None:
                        trade["first_tp_epoch"] = (ts+60000)/1000
            if len(ladder) in hits:
                outcome, exit_price = f"TP{len(ladder)}_HIT", ladder[-1]
            elif (ts+60000)/1000 - opened >= MAX_HOLD_SECONDS:
                outcome, exit_price = "TIMEOUT", close
        if outcome:
            closed = (ts + 60000)/1000
            gross = (exit_price-entry)/entry*100*sign
            cost = trade_cost_pct(entry, exit_price, (closed-opened)/3600, trade.get("funding_rate", 0))
            return {"outcome": outcome, "exit_price": exit_price, "closed_epoch": closed,
                    "gross_pnl_pct": gross, "cost_pct": cost, "net_pnl_pct": gross-cost,
                    "pnl_pct": gross, "is_win": gross-cost > 0,
                    "resolution": "1m", "logic_version": LOGIC_VERSION,
                    "hold_hours": (closed-opened)/3600}
    return None
