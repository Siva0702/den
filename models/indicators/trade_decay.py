# models/indicators/trade_decay.py
import numpy as np
import pandas as pd

class TradeDecayEngine:
    """
    Den Engine v39.3 Stagnation & Decay Exit.

    The failure the user described: signals fire on pairs that already pumped during the
    US afternoon, the session ends, volatility drains out, price grinds sideways for
    hours, and eventually the stop gets picked off. Nothing "broke" — there was no
    structure break for the invalidation engine to catch — the trade simply died of
    boredom and then bled out.

    The existing invalidation engine only fires on a STRUCTURE BREAK, so it is blind to
    exactly this. This module watches the other failure mode: a position that is still
    technically alive but has stopped making progress.

    Four measurements, all relative to the trade's own risk unit (R):

      PROGRESS   how far toward TP1 the trade has travelled versus how long it has held.
                 A trade 20% of the way to target after 70% of its expected duration is
                 failing regardless of where price sits.
      MOMENTUM   is the ATR still supporting the move, or has volatility collapsed since
                 entry? A setup entered at 2x ATR that now sits at 0.6x cannot reach a
                 target sized for the old volatility.
      DRIFT      is price drifting toward the stop while making no progress to target?
      EXHAUSTION has the trade already made its high-water mark and given most of it back?

    Output is a 0-100 decay score with an explicit recommendation. At 70+ the engine
    tells the user to close at market and take the small loss or scratch, rather than
    donate the full stop distance to a trade that has already stopped working.
    """

    # Expected bars-to-target by regime, on 15m candles.
    EXPECTED_BARS = {"VOLATILE": 12, "TRENDING": 16, "RANGING": 24, "CHOPPY": 28, "UNKNOWN": 20}

    MIN_BARS_BEFORE_JUDGING = 6      # ~90 minutes; do not panic on a fresh trade

    @classmethod
    def analyze(cls, pos: dict, df_15m: pd.DataFrame, current_price: float,
                atr_now: float = None) -> dict:
        """
        pos requires: direction, entry_price, stop_loss, epoch_time, and ideally
        tp_ladder, market_regime, atr_at_entry.
        """
        if df_15m is None or len(df_15m) < 30:
            return {"available": False}

        import time
        direction = pos.get("direction", "LONG")
        entry = float(pos.get("entry_price", current_price))
        sl = float(pos.get("stop_loss", entry))
        ladder = pos.get("tp_ladder") or []
        tp1 = float(ladder[0]) if ladder else float(pos.get("take_profit", entry))
        regime = pos.get("market_regime", "UNKNOWN")

        risk = abs(entry - sl)
        if risk <= 0 or entry <= 0:
            return {"available": False}

        held_seconds = max(time.time() - float(pos.get("epoch_time", time.time())), 0.0)
        bars_held = held_seconds / 900.0
        if bars_held < cls.MIN_BARS_BEFORE_JUDGING:
            return {"available": True, "decay_score": 0.0, "recommendation": "HOLD",
                    "reason": f"only {bars_held:.1f} bars held — too early to judge",
                    "factors": [], "bars_held": round(bars_held, 1)}

        expected = cls.EXPECTED_BARS.get(regime, 20)
        time_used = min(bars_held / expected, 2.0)

        # Progress toward TP1, in R.
        if direction == "LONG":
            progress_r = (current_price - entry) / risk
            target_r = (tp1 - entry) / risk
        else:
            progress_r = (entry - current_price) / risk
            target_r = (entry - tp1) / risk
        target_r = target_r if target_r > 0 else 1.0
        progress_frac = max(progress_r / target_r, -2.0)

        score = 0.0
        factors = []

        # 1. Time burned without progress — up to 35 points.
        if time_used >= 0.5:
            shortfall = max(time_used - max(progress_frac, 0.0), 0.0)
            pts = min(shortfall * 35.0, 35.0)
            if pts >= 8:
                score += pts
                factors.append(
                    f"Used {time_used * 100:.0f}% of expected time for {progress_frac * 100:.0f}% "
                    f"of the move [+{pts:.0f}]")

        # 2. Volatility collapse — up to 25 points.
        atr_entry = pos.get("atr_at_entry")
        if atr_now is None:
            tr = pd.concat([
                df_15m['high'] - df_15m['low'],
                (df_15m['high'] - df_15m['close'].shift()).abs(),
                (df_15m['low'] - df_15m['close'].shift()).abs()], axis=1).max(axis=1)
            atr_now = float(tr.rolling(14).mean().iloc[-1])
        if atr_entry and atr_now and float(atr_entry) > 0:
            ratio = atr_now / float(atr_entry)
            if ratio < 0.65:
                pts = min((0.65 - ratio) * 70.0, 25.0)
                score += pts
                factors.append(f"Volatility collapsed to {ratio * 100:.0f}% of entry ATR — "
                               f"target no longer reachable in this regime [+{pts:.0f}]")

        # 3. Drift toward the stop with no progress — up to 20 points.
        if progress_r < 0:
            drawdown_frac = abs(progress_r)
            if drawdown_frac >= 0.35:
                pts = min(drawdown_frac * 25.0, 20.0)
                score += pts
                factors.append(f"Drifted {drawdown_frac * 100:.0f}% of the way to stop "
                               f"without ever approaching target [+{pts:.0f}]")

        # 4. Gave back the high-water mark — up to 20 points.
        bars = int(min(max(bars_held, 4), len(df_15m) - 1))
        window = df_15m.iloc[-bars:]
        if len(window) >= 3:
            if direction == "LONG":
                peak_r = (float(window['high'].max()) - entry) / risk
            else:
                peak_r = (entry - float(window['low'].min())) / risk
            if peak_r >= target_r * 0.5 and progress_r < peak_r * 0.4:
                pts = min((peak_r - progress_r) / max(target_r, 0.01) * 20.0, 20.0)
                score += pts
                factors.append(f"Reached {peak_r:.2f}R then gave back to {progress_r:.2f}R — "
                               f"momentum has reversed [+{pts:.0f}]")

        # 5. Compression: range narrowing hard versus the trade's own risk unit.
        recent_range = float(df_15m['high'].iloc[-8:].max() - df_15m['low'].iloc[-8:].min())
        if recent_range < risk * 0.6 and time_used >= 0.6:
            score += 12
            factors.append(f"Last 8 bars spanned only {recent_range / risk:.2f}R — "
                           f"price is coiling, not trending [+12]")

        score = float(min(score, 100.0))

        if score >= 70:
            rec = "CLOSE_NOW"
            summary = ("This trade has stopped working. Closing at market now costs far less "
                       "than letting it drift into the stop.")
        elif score >= 45:
            rec = "TIGHTEN_OR_SCRATCH"
            summary = ("Losing its edge. Consider moving the stop to breakeven or scratching "
                       "at market.")
        else:
            rec = "HOLD"
            summary = "Still developing within normal parameters."

        # What the exit actually saves versus a full stop.
        notional = float(pos.get("margin", 50.0)) * float(pos.get("leverage", 15))
        pnl_now = notional * (progress_r * risk / entry)
        full_stop_loss = notional * (risk / entry)
        saved = max(pnl_now + full_stop_loss, 0.0)

        return {
            "available": True,
            "decay_score": round(score, 1),
            "recommendation": rec,
            "reason": summary,
            "factors": factors,
            "bars_held": round(bars_held, 1),
            "time_used_pct": round(time_used * 100, 0),
            "progress_r": round(progress_r, 2),
            "target_r": round(target_r, 2),
            "unrealised_usd": round(pnl_now, 2),
            "capital_saved_vs_stop": round(saved, 2),
        }
