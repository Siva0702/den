# models/indicators/event_volatility.py
import numpy as np
import pandas as pd

class EventVolatilityEngine:
    """
    Den Engine v39.1 Event-Driven Volatility Playbook.

    Built for the exact situation the user described: an unscheduled announcement sends a
    name from 104 to 130+, then it dumps back to 114. A trend-following engine does the
    worst possible thing there — it sees maximum momentum at 130 and buys the top. The
    money in that move was on the SHORT side, on the way back down.

    But fading a vertical move blind is how accounts die, because sometimes 104 -> 130 is
    a genuine repricing that never comes back. So the engine has to tell the two apart,
    and the discriminator is not price, it is PARTICIPATION:

      REPRICING   price holds most of its gain, open interest RISES (new positions being
                  built at the new level), pullbacks are shallow and orderly.
                  -> the level is real. Trade continuation, or stand aside.

      EXHAUSTION  price gives back a third or more, open interest FALLS (the move was
                  shorts covering, not buyers arriving), volume decays while price tries
                  to continue, and a lower high prints.
                  -> the move was emotional. Fade it back toward the origin.

    The hard rule that protects capital: while the candle is still vertical and no
    structure has formed, the verdict is NO_ENTRY. Not a weaker long, not a smaller
    short — no entry at all. Every fade requires a confirmed lower high (or higher low
    for a down-spike) before it becomes actionable. That single rule is the difference
    between catching the 130 -> 114 leg and being the person who bought 130.
    """

    SPIKE_ATR_MULT = 3.5        # single-bar range that qualifies as an event candle
    CUMULATIVE_ATR_MULT = 4.5   # multi-bar burst that qualifies
    LOOKBACK = 16               # bars searched for the spike
    FADE_MIN_RETRACE = 0.30     # give-back needed before exhaustion is even considered
    REPRICE_HOLD = 0.70         # holding this much of the move implies a real repricing

    # ------------------------------------------------------------------
    @classmethod
    def detect_spike(cls, df: pd.DataFrame, atr: float) -> dict:
        """Locate an event-scale move inside the lookback window."""
        if df is None or len(df) < cls.LOOKBACK + 5 or not atr or atr <= 0:
            return {"detected": False}

        window = df.iloc[-cls.LOOKBACK:].reset_index(drop=True)
        ranges = (window['high'] - window['low']).values
        max_idx = int(np.argmax(ranges))
        max_range = float(ranges[max_idx])

        # Cumulative burst: largest close-to-close excursion inside the window.
        closes = window['close'].values
        cum_up = float(closes.max() - closes.min())
        up_first = int(np.argmin(closes)) < int(np.argmax(closes))

        single_hit = max_range >= atr * cls.SPIKE_ATR_MULT
        cumulative_hit = cum_up >= atr * cls.CUMULATIVE_ATR_MULT

        if not (single_hit or cumulative_hit):
            return {"detected": False}

        # Direction comes from the event candle when there is one, otherwise from the
        # shape of the burst.
        if single_hit:
            bar = window.iloc[max_idx]
            direction = "UP" if float(bar['close']) >= float(bar['open']) else "DOWN"
        else:
            direction = "UP" if up_first else "DOWN"

        # Measure the FULL extent of the move, not just the event candle. A 104 -> 130
        # ramp spread over four bars has its origin at 104; taking the big candle's own
        # low would put the origin at 121 and mis-scale every retracement level and
        # target that follows.
        if direction == "UP":
            extreme = float(window['high'].max())
            peak_pos = int(np.where(window['high'].values >= extreme * 0.99999)[0].max())
            origin = float(window['low'].values[:peak_pos + 1].min())
        else:
            extreme = float(window['low'].min())
            peak_pos = int(np.where(window['low'].values <= extreme * 1.00001)[0].max())
            origin = float(window['high'].values[:peak_pos + 1].max())

        bars_since = len(window) - 1 - peak_pos
        span = abs(extreme - origin)
        magnitude_atr = span / atr if atr else 0.0
        if span <= 0:
            return {"detected": False}

        return {
            "detected": True,
            "direction": direction,
            "origin": origin,
            "extreme": extreme,
            "span": span,
            "span_pct": round(span / origin * 100, 2) if origin else 0.0,
            "magnitude_atr": round(magnitude_atr, 2),
            "bars_since_peak": bars_since,
            "trigger": "single_bar" if single_hit else "cumulative",
        }

    # ------------------------------------------------------------------
    @classmethod
    def _structure_confirmed(cls, df: pd.DataFrame, spike: dict) -> dict:
        """
        Has the move actually turned? For an up-spike we need a LOWER HIGH after the
        peak plus a close below the most recent minor swing low. Anything less and we
        are still inside the vertical, where fades get run over.
        """
        bars_since = spike["bars_since_peak"]
        if bars_since < 2:
            return {"confirmed": False, "reason": "peak is too recent — no structure yet"}

        post = df.iloc[-(bars_since + 1):]
        if len(post) < 3:
            return {"confirmed": False, "reason": "insufficient post-spike bars"}

        highs = post['high'].values
        lows = post['low'].values
        close = float(df['close'].iloc[-1])

        # Anchor on the LAST bar that made the extreme. A bar opening at the prior close
        # routinely ties the peak high; anchoring on the first occurrence would count
        # that tie as the "lower high" and never confirm a turn.
        if spike["direction"] == "UP":
            peak = float(highs.max())
            peak_pos = int(np.where(highs >= peak * 0.99999)[0].max())
            after_peak = highs[peak_pos + 1:]
            lower_high = len(after_peak) >= 2 and float(after_peak.max()) < peak * 0.998
            broke_down = close < float(lows[:-1].min()) if len(lows) > 1 else False
            confirmed = bool(lower_high and broke_down)
            reason = (f"lower high {float(after_peak.max()):.6g} below peak {peak:.6g}, close broke the "
                      f"post-spike low" if confirmed else
                      "no lower high / structure break yet — still inside the vertical")
        else:
            trough = float(lows.min())
            trough_pos = int(np.where(lows <= trough * 1.00001)[0].max())
            after_trough = lows[trough_pos + 1:]
            higher_low = len(after_trough) >= 2 and float(after_trough.min()) > trough * 1.002
            broke_up = close > float(highs[:-1].max()) if len(highs) > 1 else False
            confirmed = bool(higher_low and broke_up)
            reason = (f"higher low above trough {trough:.6g}, close broke the post-spike high"
                      if confirmed else
                      "no higher low / structure break yet — still inside the vertical")

        return {"confirmed": confirmed, "reason": reason}

    # ------------------------------------------------------------------
    @classmethod
    def analyze(cls, df: pd.DataFrame, atr: float, derivatives: dict = None,
                event_context: dict = None) -> dict:
        """
        Full post-event verdict. Returns action in
        {NONE, NO_ENTRY, FADE, CONTINUATION} plus levels when actionable.
        """
        spike = cls.detect_spike(df, atr)
        if not spike.get("detected"):
            return {"active": False, "action": "NONE"}

        close = float(df['close'].iloc[-1])
        origin, extreme, span = spike["origin"], spike["extreme"], spike["span"]

        # How much of the move has been handed back?
        if spike["direction"] == "UP":
            retrace = (extreme - close) / span
            held = (close - origin) / span
        else:
            retrace = (close - extreme) / span
            held = (origin - close) / span
        retrace = max(0.0, min(retrace, 1.5))
        held = max(-0.5, min(held, 1.5))

        # Participation: is new money arriving, or is this an unwind?
        oi = ((derivatives or {}).get("open_interest") or {})
        oi_rising = bool(oi.get("oi_rising"))
        oi_falling = bool(oi.get("oi_falling"))

        # Volume decay on the continuation attempt.
        recent_vol = float(df['volume'].iloc[-3:].mean())
        spike_vol = float(df['volume'].iloc[-(spike["bars_since_peak"] + 2):].max())
        vol_decay = recent_vol < spike_vol * 0.55 if spike_vol > 0 else False

        structure = cls._structure_confirmed(df, spike)
        notes = [
            f"{spike['direction']} event move of {spike['span_pct']:.2f}% "
            f"({spike['magnitude_atr']}x ATR), {spike['bars_since_peak']} bars since peak",
            f"Retraced {retrace * 100:.0f}% of the move, holding {held * 100:.0f}%",
        ]
        if oi_rising:
            notes.append(f"Open interest RISING ({oi.get('oi_change_12bar_pct')}%) — new positions at this level")
        elif oi_falling:
            notes.append(f"Open interest FALLING ({oi.get('oi_change_12bar_pct')}%) — unwind, not accumulation")
        if vol_decay:
            notes.append("Volume decaying since the spike — participation fading")

        # ---- Verdict ----------------------------------------------------
        if not structure["confirmed"]:
            return {
                "active": True, "action": "NO_ENTRY", "spike": spike,
                "retrace_pct": round(retrace * 100, 1), "held_pct": round(held * 100, 1),
                "classification": "IN_PROGRESS",
                "reason": f"Event move still unresolved — {structure['reason']}",
                "notes": notes,
                "warning": ("Do not enter during a vertical event move. This is where the "
                            "104 -> 130 -> 114 traps happen. Waiting for structure."),
            }

        exhaustion_score = 0
        if retrace >= cls.FADE_MIN_RETRACE:
            exhaustion_score += 40
        if oi_falling:
            exhaustion_score += 30
        if vol_decay:
            exhaustion_score += 20
        if held < cls.REPRICE_HOLD:
            exhaustion_score += 10

        reprice_score = 0
        if held >= cls.REPRICE_HOLD:
            reprice_score += 40
        if oi_rising:
            reprice_score += 35
        if retrace < 0.25:
            reprice_score += 25

        if exhaustion_score >= 60 and exhaustion_score > reprice_score:
            fade_dir = "SHORT" if spike["direction"] == "UP" else "LONG"
            # Stop goes beyond the spike extreme — the one level that invalidates the fade.
            buffer = max(atr * 0.5, span * 0.06)
            stop = extreme + buffer if fade_dir == "SHORT" else extreme - buffer
            targets = [
                origin + span * 0.50 if fade_dir == "SHORT" else origin - span * 0.50,
                origin + span * 0.382 if fade_dir == "SHORT" else origin - span * 0.382,
                origin,
            ]
            return {
                "active": True, "action": "FADE", "direction": fade_dir, "spike": spike,
                "classification": "EXHAUSTION",
                "exhaustion_score": exhaustion_score, "reprice_score": reprice_score,
                "retrace_pct": round(retrace * 100, 1), "held_pct": round(held * 100, 1),
                "entry": close, "invalidation": stop,
                "targets": [round(t, 8) for t in targets],
                "reason": (f"Event spike exhausted (score {exhaustion_score}). {structure['reason']}. "
                           f"Fading back toward origin {origin:.6g}."),
                "notes": notes,
                "score_bonus": 8.0,
            }

        if reprice_score >= 65 and reprice_score > exhaustion_score:
            cont_dir = "LONG" if spike["direction"] == "UP" else "SHORT"
            return {
                "active": True, "action": "CONTINUATION", "direction": cont_dir, "spike": spike,
                "classification": "REPRICING",
                "exhaustion_score": exhaustion_score, "reprice_score": reprice_score,
                "retrace_pct": round(retrace * 100, 1), "held_pct": round(held * 100, 1),
                "reason": (f"Level is holding with rising participation (score {reprice_score}) — "
                           f"genuine repricing, not a squeeze."),
                "notes": notes,
                "score_bonus": 4.0,
            }

        return {
            "active": True, "action": "NO_ENTRY", "spike": spike,
            "classification": "AMBIGUOUS",
            "exhaustion_score": exhaustion_score, "reprice_score": reprice_score,
            "retrace_pct": round(retrace * 100, 1), "held_pct": round(held * 100, 1),
            "reason": (f"Post-event signals conflict (exhaustion {exhaustion_score} vs "
                       f"repricing {reprice_score}) — no edge either way."),
            "notes": notes,
        }
