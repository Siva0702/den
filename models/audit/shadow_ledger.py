# models/audit/shadow_ledger.py
import json
import os
import tempfile
import threading
import time

MISSED_FILE_NAME = "audit/shadow_missed.json"
MODELS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHADOW_OPEN_FILE = os.path.join(MODELS_DIR, "audit/shadow_open.json")
SHADOW_CLOSED_FILE = os.path.join(MODELS_DIR, "audit/shadow_closed.json")
MISSED_FILE = os.path.join(MODELS_DIR, MISSED_FILE_NAME)

class ShadowTradeLedger:
    """
    Den Engine v39.0 Shadow (Paper) Trade Ledger.

    The engine only DISPATCHES its highest-conviction setup, which means it learns
    from a handful of trades a week — far too few to calibrate anything. This ledger
    fixes that: every candidate at or above SHADOW_FLOOR is opened as a VIRTUAL trade
    with a full feature snapshot, tracked bar-by-bar against real prices, and resolved
    to a real outcome. Nothing is sent to Telegram. Nothing risks capital.

    That gives the calibration layer hundreds of labelled outcomes per week instead of
    a handful, which is the only honest way to answer "what is this score actually
    worth?".

    Two things it records that a naive win/loss log cannot:

      MAE (max adverse excursion) — how far the trade went AGAINST us before working.
        This is what exposes stop-hunt vulnerability and lets SL distance be set from
        data instead of a fixed 1.5x ATR.

      MFE (max favourable excursion) — how far it went our way before reversing.
        This is what sizes the TP ladder honestly.

    The combination detects the outcome the user specifically called useless:
    SL_THEN_TP — price took the stop out first and only then ran to target.
    """

    # Measured: score bins 40-70 win 48-55% (coin flips); 70-80 wins 74.7% (Wilson LB).
    # Logging sub-70 setups floods calibration with noise that drowns the real signal.
    # Shadow trades cost nothing and carry no risk, so the floor exists only to keep
    # calibration from drowning in noise — not to protect capital. 40 captures the
    # weak setups too, which is exactly what tells us WHERE the score starts working.
    SHADOW_FLOOR = 40.0
    MAX_HOLD_SECONDS = 36 * 3600
    SL_GRACE_SECONDS = 6 * 3600   # after a stop, keep watching to learn if it was too tight
    # Win is defined by the PLANNED exit rung, not the last rung on the ladder.
    # Resolving on TP4 booked trades that banked TP1+TP2 as losses.
    PRIMARY_TP_INDEX = 0
    MAX_CLOSED_RECORDS = 8000
    # Two identical records inside this window are one setup logged twice. Beyond it,
    # the same level recurring is a separate opportunity and must be kept.
    DEDUP_WINDOW_SECONDS = 4 * 3600

    # LEDGER LOGIC VERSION.
    # Every resolved record is stamped with the version of the resolution logic that
    # produced it. Today proved why this is not optional: records resolved under the
    # truncating trail said 75.7% accuracy, records resolved under the corrected trail
    # said 30.3%, and both sat in the same file being averaged into one meaningless
    # number. Calibration must never mix logic versions — a win under v1 and a win under
    # v3 are not the same measurement.
    #
    # Bump this whenever resolution semantics change. Records on an older version are
    # quarantined out of calibration and queued for replay against real candles.
    #   v1  resolve at final ladder rung
    #   v2  resolve at TP1 (planned exit), trail sits ON the rung just hit
    #   v3  trail sits ONE RUNG BEHIND; TP1 -> breakeven, TP2 -> TP1; BREAKEVEN is neutral
    LOGIC_VERSION = "v4-trail-one-behind-1m"

    _lock = threading.Lock()
    _last_open_candle = {}      # "TICKER|DIR" -> candle timestamp of last open

    # ------------------------------------------------------------------
    @staticmethod
    def _atomic_write(path: str, payload):
        directory = os.path.dirname(path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(payload, f, indent=2, default=str)
            os.replace(tmp, path)
        except Exception as e:
            print(f"[!] ShadowLedger write failed for {path}: {e}")
            try:
                os.unlink(tmp)
            except Exception:
                pass

        try:
            key_name = "shadow_open" if "shadow_open" in path else ("shadow_closed" if "shadow_closed" in path else None)
            if key_name:
                from audit.portable_store import PortableStateStore
                PortableStateStore.save_state(key_name, payload)
        except Exception:
            pass

    @staticmethod
    def _load(path: str, default):
        key_name = "shadow_open" if "shadow_open" in path else ("shadow_closed" if "shadow_closed" in path else None)
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    res = json.load(f)
                    if res:
                        return res
            except Exception:
                pass

        if key_name:
            try:
                from audit.portable_store import PortableStateStore
                db_data = PortableStateStore.load_state(key_name)
                if db_data is not None:
                    return db_data
            except Exception:
                pass

        return default

    @classmethod
    def load_open(cls) -> list:
        return cls._load(SHADOW_OPEN_FILE, [])

    @classmethod
    def load_closed(cls) -> list:
        return cls._load(SHADOW_CLOSED_FILE, [])

    # ------------------------------------------------------------------
    @classmethod
    def open_shadow_trade(cls, candidate: dict) -> bool:
        """
        Open a virtual position from a scanner candidate. Returns True if newly opened.
        Idempotent per (ticker, direction) while a shadow trade is live, so the 15s
        scan loop cannot spam duplicates of the same setup.
        """
        from audit.score_model import CalibratedScoreModel
        model_score = candidate.get("model_score")
        score = float(candidate.get("total_score", 0.0))
        if model_score is not None:
            if float(model_score) < CalibratedScoreModel.MODEL_SHADOW_FLOOR:
                return False
        elif score < cls.SHADOW_FLOOR:
            return False

        ticker = candidate.get("ticker")
        direction = candidate.get("direction")
        entry = float(candidate.get("entry", 0.0))
        if not ticker or direction not in ("LONG", "SHORT") or entry <= 0:
            return False

        # CHURN GUARD.
        # Candle-gated rescoring reuses a cached signal, so `entry` stays frozen at the
        # candle's close price. If live price has already passed TP1, the trade opens and
        # resolves TP1_HIT on the very next scan — which clears the open-trade dedup and
        # lets an identical trade open again 35 seconds later, forever. IBM logged 21
        # identical TP1_HIT records at entry 224.61 this way, each one inflating the win
        # count and the equity curve with a single trade counted 21 times.
        #
        # Two conditions close the loop:
        #   1. reject an entry that is ALREADY past its first target or beyond its stop —
        #      that is not an entry, it is a missed move
        #   2. require a NEW candle before the same ticker+direction may reopen
        ladder = candidate.get("tp_ladder") or []
        sl = float(candidate.get("sl", 0.0) or 0.0)
        live = candidate.get("live_price")
        if ladder and live:
            live = float(live)
            tp1 = float(ladder[0])
            already_past = (live >= tp1 if direction == "LONG" else live <= tp1)
            beyond_stop = (live <= sl if direction == "LONG" else live >= sl)
            if already_past or beyond_stop:
                # NOT a discard — this is evidence, just not tradeable evidence.
                #
                # "Price already past TP1" means the engine called the direction
                # correctly and we simply had no entry. That says the SCORING worked and
                # the EXECUTION did not, which is a completely different diagnosis from
                # "the score was wrong". Counting it as a win would inflate accuracy with
                # trades nobody could have taken (IBM: one missed move logged 21 times).
                # Counting it as nothing throws away the only clean read we have on
                # whether the model picks direction.
                #
                # So it is recorded separately as DIRECTIONAL evidence and kept out of
                # the tradeable win rate entirely.
                cls._record_missed(ticker, direction, score, entry, live, tp1, sl,
                                   "THESIS_CORRECT" if already_past else "THESIS_WRONG",
                                   candidate)
                return False

        candle_ts = candidate.get("candle_ts")
        key = f"{ticker}|{direction}"

        with cls._lock:
            if candle_ts is not None and cls._last_open_candle.get(key) == candle_ts:
                return False        # already opened on this candle
            open_trades = cls.load_open()
            for t in open_trades:
                if t.get("ticker") == ticker and t.get("direction") == direction:
                    return False

            record = {
                "shadow_id": f"{ticker.replace('/', '')}-{direction}-{int(time.time())}",
                "ticker": ticker,
                "direction": direction,
                "entry": entry,
                "stop_loss": float(candidate.get("sl", 0.0)),
                "tp_ladder": candidate.get("tp_ladder", []),
                "primary_tp": float(candidate.get("tp", 0.0)),
                # raw_score is the pillar sum BEFORE any learned tilt. adjusted_score is
                # what the gates compared against. Storing only one made the field
                # ambiguous once the tilt shipped: reconstructions were adding the
                # adjustment to a number that already contained it.
                "raw_score": float(candidate.get("pillar_score") if
                                   candidate.get("pillar_score") is not None else score),
                "adjusted_score": float(candidate.get("adjusted_score") if
                                        candidate.get("adjusted_score") is not None else score),
                "learned_adjustment": candidate.get("learned_adjustment"),
                "model_score": candidate.get("model_score"),
                "model_prob": candidate.get("model_prob"),
                "calibrated_win_rate": candidate.get("calibrated_win_rate"),
                "opened_epoch": time.time(),
                "opened_time": time.strftime("%Y-%m-%d %H:%M:%S"),

                # Full feature snapshot — this is what calibration learns from.
                "features": candidate.get("feature_snapshot", {}),
                "factors_passed": candidate.get("factors_passed", []),
                "factors_failed": candidate.get("factors_failed", []),
                "market_regime": candidate.get("market_regime", "UNKNOWN"),
                "session": candidate.get("session", "UNKNOWN"),
                "timeframe_alignment": candidate.get("timeframe_alignment", 0),

                # Excursion tracking, seeded at entry.
                "mae_price": entry,
                "mfe_price": entry,
                "mae_pct": 0.0,
                "mfe_pct": 0.0,
                "tp_levels_hit": [],
                "sl_touched": False,
                "sl_touched_epoch": None,
                "first_tp_epoch": None,
                "bars_observed": 0,
                "last_price": entry,
            }
            record["candle_ts"] = candle_ts
            record["config_version"] = cls.LOGIC_VERSION
            # Tag setups Kelly refused to fund. They cost nothing to track and are the
            # only way to test whether the veto is actually correct.
            record["kelly_vetoed"] = bool(candidate.get("kelly_vetoed"))
            record["kelly_full"] = candidate.get("kelly_full")
            open_trades.append(record)
            if candle_ts is not None:
                cls._last_open_candle[key] = candle_ts
            cls._atomic_write(SHADOW_OPEN_FILE, open_trades)
        return True

    # ------------------------------------------------------------------
    @classmethod
    def update_prices(cls, price_map: dict) -> list:
        """
        Advance every open shadow trade against the latest prices.

        price_map accepts either {ticker: last_price} or, preferably,
        {ticker: {"close": c, "high": h, "low": l}}.

        Using close alone was a real defect: a scan every 15s samples the CLOSE, so any
        excursion that happened between scans — or inside the current candle — was
        invisible. ORDI ran 1.64% in favour and through TP1 while the ledger recorded a
        0.29% MFE and no target hit, because the move never happened to land on a sample.
        Feeding the bar HIGH and LOW makes MAE/MFE and target detection reflect what
        price actually did rather than where it happened to be when we looked.

        Crucially this does NOT stop at the first stop-loss touch. It keeps the trade
        alive to see whether price subsequently reached target, which is what produces
        the SL_THEN_TP label. A system that closes and forgets can never learn that its
        stops are simply too tight.
        """
        resolved = []
        with cls._lock:
            open_trades = cls.load_open()
            if not open_trades:
                return []

            still_open = []
            now = time.time()

            for t in open_trades:
                price = price_map.get(t["ticker"])
                if price is None:
                    if now - t.get("opened_epoch", now) > cls.MAX_HOLD_SECONDS:
                        resolved.append(cls._resolve(t, t.get("last_price", t["entry"]), "TIMEOUT_NO_DATA"))
                    else:
                        still_open.append(t)
                    continue

                if isinstance(price, dict):
                    close = float(price.get("close"))
                    high = float(price.get("high", close))
                    low = float(price.get("low", close))
                else:
                    close = high = low = float(price)

                entry = t["entry"]
                direction = t["direction"]
                sl = t["stop_loss"]
                t["last_price"] = close
                t["bars_observed"] = t.get("bars_observed", 0) + 1

                # Excursions measured against the bar extremes, not the sampled close.
                if direction == "LONG":
                    if low < t["mae_price"]:
                        t["mae_price"] = low
                    if high > t["mfe_price"]:
                        t["mfe_price"] = high
                    t["mae_pct"] = round((t["mae_price"] - entry) / entry * 100, 4)
                    t["mfe_pct"] = round((t["mfe_price"] - entry) / entry * 100, 4)
                    sl_touch = low <= sl
                    tp_probe = high
                else:
                    if high > t["mae_price"]:
                        t["mae_price"] = high
                    if low < t["mfe_price"]:
                        t["mfe_price"] = low
                    t["mae_pct"] = round((entry - t["mae_price"]) / entry * 100, 4)
                    t["mfe_pct"] = round((entry - t["mfe_price"]) / entry * 100, 4)
                    sl_touch = high >= sl
                    tp_probe = low
                price = close

                if sl_touch and not t["sl_touched"]:
                    t["sl_touched"] = True
                    t["sl_touched_epoch"] = now

                # TP ladder progress.
                # RULE: the result is whichever level price touches FIRST. Once the stop
                # has been tagged the position is flat, so no further rung may be counted
                # — otherwise a trade that banked TP1, reversed through the stop, then
                # drifted up to TP2 would record TP2 as "reached" when it was
                # uncapturable. Freezing the counter at the stop keeps every rung's reach
                # rate a valid counterfactual: "would this target have been hit BEFORE the
                # stop, if it had been my only target?"
                if t["sl_touched"]:
                    ladder_iter = []
                else:
                    ladder_iter = t.get("tp_ladder", []) or [t.get("primary_tp")]
                for idx, tp_level in enumerate(ladder_iter, start=1):
                    if tp_level is None:
                        continue
                    tp_level = float(tp_level)
                    hit = tp_probe >= tp_level if direction == "LONG" else tp_probe <= tp_level
                    if hit and idx not in t["tp_levels_hit"]:
                        t["tp_levels_hit"].append(idx)
                        if t.get("first_tp_epoch") is None:
                            t["first_tp_epoch"] = now

                final_tp = None
                ladder = t.get("tp_ladder") or []
                if ladder:
                    idx = min(cls.PRIMARY_TP_INDEX, len(ladder) - 1)
                    final_tp = float(ladder[idx])
                elif t.get("primary_tp"):
                    final_tp = float(t["primary_tp"])

                reached_final = final_tp is not None and (
                    tp_probe >= final_tp if direction == "LONG" else tp_probe <= final_tp
                )

                # --- Resolution, decided on EVENT ORDER, not just event presence ---
                # A live position is closed by whichever level price reaches first.
                # Getting this order wrong is how a paper ledger flatters itself: a
                # trade that was stopped out and only later ran to target must never
                # be booked as a win.
                sl_first = t["sl_touched"] and (
                    t.get("first_tp_epoch") is None
                    or t["sl_touched_epoch"] <= t["first_tp_epoch"]
                )

                # ---- RATCHETING TRAIL STOP -------------------------------------
                # The stop ratchets up to each rung as it is reached. Before TP1 the
                # original stop is live. Once TP1 prints, the stop moves TO TP1 and the
                # position runs for TP2; if price falls back to TP1 first, we are out at
                # TP1 and the result is TP1_HIT. The same repeats up the ladder, so a
                # runner that reaches TP3 before turning is booked as TP3, not TP1.
                #
                # First touch always decides: anything price does after the trail stop is
                # tagged is uncapturable and never counted.
                ladder = t.get("tp_ladder") or ([t["primary_tp"]] if t.get("primary_tp") else [])
                trail_idx = max(t["tp_levels_hit"]) if t["tp_levels_hit"] else 0

                if trail_idx > 0:
                    trail_stop = float(ladder[trail_idx - 1])
                    # The trail ARMS on the next bar, never on the bar that tagged the
                    # rung. Price reaches TP1 from below, so that bar's low sits under
                    # TP1 by definition — checking the trail immediately closed every
                    # trade the instant it first touched target, which is why nothing
                    # ever ran to TP2+. A rung must be held for one bar before its level
                    # can stop us out.
                    armed_at = t.get("trail_armed_idx")
                    if armed_at != trail_idx:
                        t["trail_armed_idx"] = trail_idx
                        still_open.append(t)
                        continue
                    stop_hit = (low <= trail_stop) if direction == "LONG" else (high >= trail_stop)
                    if trail_idx >= len(ladder):
                        # Top rung reached — nothing left to run for.
                        resolved.append(cls._resolve(t, trail_stop, f"TP{trail_idx}_HIT"))
                        continue
                    if stop_hit:
                        # Pessimistic within-bar ordering: if a bar both advances a rung
                        # and tags the trail, assume the trail went first.
                        resolved.append(cls._resolve(t, trail_stop, f"TP{trail_idx}_HIT"))
                        continue
                    if now - t.get("opened_epoch", now) > cls.MAX_HOLD_SECONDS:
                        resolved.append(cls._resolve(t, trail_stop, f"TP{trail_idx}_HIT"))
                        continue
                    still_open.append(t)
                    continue

                # No rung reached yet — the ORIGINAL stop is still the live one.
                if sl_touch:
                    t["sl_touched"] = True
                    t["sl_touched_epoch"] = t["sl_touched_epoch"] or now
                if t["sl_touched"]:
                    if now - (t["sl_touched_epoch"] or now) > cls.SL_GRACE_SECONDS:
                        # Keep the SL_THEN_TP label for stop-placement research, but the
                        # exit is at the stop either way — the position was flat.
                        label = "SL_THEN_TP" if t["tp_levels_hit"] else "SL_HIT"
                        resolved.append(cls._resolve(t, sl, label))
                    else:
                        still_open.append(t)
                    continue
                if now - t.get("opened_epoch", now) > cls.MAX_HOLD_SECONDS:
                    resolved.append(cls._resolve(t, price, "TIMEOUT"))
                    continue
                still_open.append(t)

            cls._atomic_write(SHADOW_OPEN_FILE, still_open)

            if resolved:
                closed = cls.load_closed()
                closed.extend(resolved)
                if len(closed) > cls.MAX_CLOSED_RECORDS:
                    closed = closed[-cls.MAX_CLOSED_RECORDS:]
                cls._atomic_write(SHADOW_CLOSED_FILE, closed)

        return resolved

    # ------------------------------------------------------------------
    @classmethod
    def _resolve(cls, trade: dict, exit_price: float, outcome: str) -> dict:
        entry = trade["entry"]
        direction = trade["direction"]
        if direction == "LONG":
            pnl_pct = (exit_price - entry) / entry * 100
        else:
            pnl_pct = (entry - exit_price) / entry * 100

        # A win is a clean win. SL_THEN_TP is explicitly NOT counted as a win, because
        # in live trading the stop would have closed the position before the recovery.
        # A win is defined by reaching the PLANNED exit rung before the stop. The
        # trade keeps being observed past that point purely to measure how far price
        # ran, which never changes whether it was a win.
        is_win = outcome.startswith("TP") and outcome.endswith("_HIT")

        trade = dict(trade)
        trade.update({
            "closed_epoch": time.time(),
            "closed_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "exit_price": exit_price,
            "outcome": outcome,
            "is_win": bool(is_win),
            "pnl_pct": round(pnl_pct, 4),
            "hold_hours": round((time.time() - trade.get("opened_epoch", time.time())) / 3600.0, 2),
            "tp_levels_hit_count": len(trade.get("tp_levels_hit", [])),
            # Observational only: how far price ran before the stop was tagged. The
            # RESULT is the exit level above; these rungs never change it.
            "max_rung_reached_before_stop": max(trade.get("tp_levels_hit") or [0]),
        })
        trade["logic_version"] = cls.LOGIC_VERSION
        trade["post_mortem"] = cls.post_mortem(trade)
        return trade

    # ------------------------------------------------------------------
    @classmethod
    def post_mortem(cls, trade: dict) -> dict:
        """
        Deep per-trade attribution: why did this setup work, or why did it fail?
        The findings here are aggregated by calibration.py into factor-level lift.
        """
        entry = trade["entry"]
        sl = trade.get("stop_loss", entry)
        outcome = trade.get("outcome")
        mae_pct = abs(trade.get("mae_pct", 0.0))
        mfe_pct = abs(trade.get("mfe_pct", 0.0))
        sl_dist_pct = abs(entry - sl) / entry * 100 if entry else 0.0

        findings = []
        tags = []

        # How close did the trade come to the stop before working?
        stop_proximity = (mae_pct / sl_dist_pct) if sl_dist_pct > 0 else 0.0

        if outcome == "SL_THEN_TP":
            tags.append("STOP_TOO_TIGHT")
            findings.append(
                f"Target was reached, but only after the stop was taken out. "
                f"MAE {mae_pct:.2f}% vs SL {sl_dist_pct:.2f}% — the thesis was right, the stop placement was wrong."
            )
        elif outcome == "SL_HIT":
            if stop_proximity >= 0.95 and mfe_pct >= sl_dist_pct * 0.5:
                tags.append("STOPPED_AFTER_PARTIAL_RUN")
                findings.append(
                    f"Ran {mfe_pct:.2f}% in favour (>{sl_dist_pct*0.5:.2f}% half-stop) before reversing — "
                    f"entry timing was sound, exit management was not."
                )
            else:
                tags.append("THESIS_WRONG")
                findings.append(
                    f"Went against us almost immediately (MFE only {mfe_pct:.2f}%) — the directional read itself failed."
                )
        elif outcome == "TP_FINAL":
            if stop_proximity <= 0.35:
                tags.append("CLEAN_WIN")
                findings.append(
                    f"Clean trade: worst drawdown was only {stop_proximity*100:.0f}% of stop distance. High-quality entry."
                )
            else:
                tags.append("WIN_WITH_HEAT")
                findings.append(
                    f"Won, but took {stop_proximity*100:.0f}% of stop distance in heat first — entry was early."
                )
        elif outcome == "TIMEOUT":
            tags.append("NO_FOLLOW_THROUGH")
            findings.append(
                f"Neither target nor stop in {trade.get('hold_hours', 0)}h — setup lacked momentum. "
                f"Range-bound conditions; regime filter should have excluded it."
            )

        # Feature-level observations
        feats = trade.get("features", {}) or {}
        deriv_bias = feats.get("derivatives_bias")
        if deriv_bias and deriv_bias != "NONE":
            agreed = deriv_bias == trade["direction"]
            findings.append(
                f"Derivatives positioning was {deriv_bias} ({'with' if agreed else 'AGAINST'} the trade)."
            )
            tags.append("DERIV_ALIGNED" if agreed else "DERIV_CONFLICT")

        if feats.get("breakout_verdict") == "LIKELY_FAKE_SQUEEZE":
            tags.append("FAKE_BREAKOUT_WARNED")
            findings.append("Open interest was falling at entry — this was flagged as a squeeze, not a breakout.")

        if feats.get("news_bias") and feats["news_bias"] != "NONE":
            agreed = (feats["news_bias"] == "BULLISH" and trade["direction"] == "LONG") or \
                     (feats["news_bias"] == "BEARISH" and trade["direction"] == "SHORT")
            tags.append("NEWS_ALIGNED" if agreed else "NEWS_CONFLICT")

        if feats.get("crowding") in ("CROWDED_LONG", "CROWDED_SHORT"):
            crowd_side = "LONG" if feats["crowding"] == "CROWDED_LONG" else "SHORT"
            if crowd_side == trade["direction"]:
                tags.append("TRADED_WITH_CROWD")
                findings.append(
                    f"Entered on the crowded side ({feats.get('long_account_pct')}% of accounts). "
                    f"Crowded positioning is stop-hunt fuel."
                )

        return {
            "tags": tags,
            "findings": findings,
            "stop_proximity_ratio": round(stop_proximity, 3),
            "mae_pct": round(mae_pct, 3),
            "mfe_pct": round(mfe_pct, 3),
            "sl_distance_pct": round(sl_dist_pct, 3),
            "reward_captured_ratio": round(mfe_pct / sl_dist_pct, 3) if sl_dist_pct > 0 else 0.0,
        }

    # ------------------------------------------------------------------
    @classmethod
    def summary(cls) -> dict:
        """
        Full breakdown, not a headline. The previous version reported only total/wins/
        win_rate, which said nothing about WHERE trades ended — and where they end is
        the whole diagnostic. Exit-rung counts show whether targets are set sensibly;
        SL vs SL_THEN_TP separates a wrong read from a stop that was merely too tight.
        """
        cls.audit_integrity(repair=True)
        closed = cls.load_closed()
        open_trades = cls.load_open()
        counts = {"TP1_HIT": 0, "TP2_HIT": 0, "TP3_HIT": 0, "TP4_HIT": 0,
                  "SL_HIT": 0, "SL_THEN_TP": 0, "PARTIAL_THEN_SL": 0,
                  "TIMEOUT": 0, "TIMEOUT_NO_DATA": 0}
        gross_win = gross_loss = 0.0
        for t in closed:
            oc = t.get("outcome", "")
            counts[oc] = counts.get(oc, 0) + 1
            pnl = float(t.get("pnl_pct", 0.0) or 0.0)
            if t.get("is_win"):
                gross_win += pnl
            else:
                gross_loss += abs(pnl)

        # Breakeven is neither. Folding it into `losses` understated accuracy — a trade
        # that reached target and trailed back to entry lost nothing and must not be
        # counted against the win rate, nor inflate gross loss.
        total = len(closed)
        wins = sum(1 for t in closed if t.get("is_win") is True)
        breakeven = sum(1 for t in closed if t.get("is_breakeven"))
        losses = total - wins - breakeven
        tp_total = sum(counts[f"TP{i}_HIT"] for i in (1, 2, 3, 4))
        return {
            "open": len(open_trades),
            "total": total,
            "wins": wins,
            "losses": losses,
            # Decided trades only: breakeven is excluded from the denominator.
            "accuracy_pct": round(wins / (wins + losses) * 100, 1) if (wins + losses) else 0.0,
            "tp1": counts["TP1_HIT"], "tp2": counts["TP2_HIT"],
            "tp3": counts["TP3_HIT"], "tp4": counts["TP4_HIT"],
            "tp_total": tp_total,
            "sl_hit": counts["SL_HIT"],
            "sl_then_tp": counts["SL_THEN_TP"],
            "partial_then_sl": counts["PARTIAL_THEN_SL"],
            "timeouts": counts["TIMEOUT"] + counts["TIMEOUT_NO_DATA"],
            "breakeven": breakeven,
            "gross_win_pct": round(gross_win, 2),
            "gross_loss_pct": round(gross_loss, 2),
            "net_pct": round(gross_win - gross_loss, 2),
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
            "sl_then_tp_pct": round(counts["SL_THEN_TP"] / total * 100, 1) if total else 0.0,
            "avg_mae_pct": round(sum(abs(t.get("mae_pct", 0)) for t in closed) / total, 3) if total else 0.0,
            "avg_mfe_pct": round(sum(abs(t.get("mfe_pct", 0)) for t in closed) / total, 3) if total else 0.0,
            # Win rate over ALL resolutions, breakeven included in the denominator —
            # deliberately different from accuracy, and labelled as such.
            "win_rate": round(wins / total * 100, 1) if total else 0.0,
        }

    @classmethod
    def _record_missed(cls, ticker, direction, score, entry, live, tp1, sl, verdict, candidate):
        """Log a setup whose entry was unavailable, with the direction it proved out."""
        try:
            with cls._lock:
                rows = cls._load(MISSED_FILE, [])
                key = (ticker, direction, round(float(entry), 10), verdict)
                for r in rows:
                    if (r["ticker"], r["direction"], round(float(r["entry"]), 10),
                            r["verdict"]) == key:
                        r["repeat_count"] = r.get("repeat_count", 1) + 1
                        r["last_seen"] = time.strftime("%Y-%m-%d %H:%M:%S")
                        cls._atomic_write(MISSED_FILE, rows)
                        return
                rows.append({
                    "ticker": ticker, "direction": direction, "raw_score": score,
                    "entry": entry, "live_at_detect": live, "tp1": tp1, "stop_loss": sl,
                    "verdict": verdict, "repeat_count": 1,
                    "market_regime": candidate.get("market_regime", "UNKNOWN"),
                    "session": candidate.get("session", "UNKNOWN"),
                    "first_seen": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "last_seen": time.strftime("%Y-%m-%d %H:%M:%S"),
                })
                if len(rows) > 3000:
                    rows = rows[-3000:]
                cls._atomic_write(MISSED_FILE, rows)
        except Exception as e:
            print(f"[!] missed-signal log failed: {e}")

    @classmethod
    def directional_accuracy(cls) -> dict:
        """
        Was the engine RIGHT about direction, separately from whether we could trade it?

        Tradeable accuracy answers "did my money make money". Directional accuracy
        answers "does the scoring model actually read the market". If directional is
        high while tradeable is not, the model works and the ENTRY TIMING is the
        problem — a far more fixable diagnosis than a broken score.
        """
        missed = cls._load(MISSED_FILE, [])
        correct = sum(r.get("repeat_count", 1) for r in missed if r["verdict"] == "THESIS_CORRECT")
        wrong = sum(r.get("repeat_count", 1) for r in missed if r["verdict"] == "THESIS_WRONG")
        setups_correct = sum(1 for r in missed if r["verdict"] == "THESIS_CORRECT")
        setups_wrong = sum(1 for r in missed if r["verdict"] == "THESIS_WRONG")

        closed = cls.load_closed()
        traded_right = sum(1 for t in closed if t.get("is_win"))
        traded_total = len(closed)

        n_setups = setups_correct + setups_wrong
        combined_right = traded_right + setups_correct
        combined_total = traded_total + n_setups
        return {
            "missed_setups": n_setups,
            "missed_correct": setups_correct,
            "missed_wrong": setups_wrong,
            "missed_directional_pct": round(setups_correct / n_setups * 100, 1) if n_setups else None,
            "persistence_events": correct + wrong,     # how often they re-appeared
            "tradeable_accuracy_pct": round(traded_right / traded_total * 100, 1) if traded_total else 0.0,
            "combined_directional_pct": round(combined_right / combined_total * 100, 1) if combined_total else None,
            "interpretation": (
                "entry timing is the bottleneck — the model reads direction better than it fills"
                if n_setups >= 5 and setups_correct / max(n_setups, 1) > 0.6 else
                "not enough missed setups to separate model quality from execution"),
        }

    @classmethod
    def purge_stale_open(cls) -> dict:
        """
        Drop open trades whose STOPS came from a superseded configuration.

        A trade opened under the old 0.6% floor will resolve against that stop and then
        be stamped with the current logic version — silently contaminating the clean
        baseline with an outcome the current engine would never have produced. Its
        levels cannot be retro-fitted either, because entry was taken at a price the
        new stop logic may not even have accepted.
        """
        with cls._lock:
            open_trades = cls.load_open()
            keep, purged = [], []
            for t in open_trades:
                if t.get("config_version") == cls.LOGIC_VERSION:
                    keep.append(t)
                else:
                    purged.append(t)
            if purged:
                cls._atomic_write(SHADOW_OPEN_FILE, keep)
                print(f"[ledger] purged {len(purged)} open trades from a superseded "
                      f"stop configuration", flush=True)
        return {"kept": len(keep), "purged": len(purged)}

    @classmethod
    def version_report(cls) -> dict:
        """
        Which logic version resolved each record, and how much of the ledger is stale.
        Calibration consumes `current_only`; everything else is quarantined until it is
        replayed, so an obsolete measurement can never silently inflate a win rate.
        """
        closed = cls.load_closed()
        by_ver = {}
        for t in closed:
            v = t.get("logic_version", "v1-legacy")
            slot = by_ver.setdefault(v, {"n": 0, "wins": 0, "losses": 0, "breakeven": 0})
            slot["n"] += 1
            if t.get("is_breakeven"):
                slot["breakeven"] += 1
            elif t.get("is_win") is True:
                slot["wins"] += 1
            else:
                slot["losses"] += 1
        for v, slot in by_ver.items():
            decided = slot["wins"] + slot["losses"]
            slot["accuracy_pct"] = round(slot["wins"] / decided * 100, 1) if decided else 0.0
            slot["current"] = (v == cls.LOGIC_VERSION)
        current = by_ver.get(cls.LOGIC_VERSION, {"n": 0})
        stale = sum(s["n"] for v, s in by_ver.items() if v != cls.LOGIC_VERSION)
        return {
            "current_version": cls.LOGIC_VERSION,
            "by_version": by_ver,
            "current_only": current["n"],
            "stale_records": stale,
            "needs_replay": stale > 0,
            "calibration_safe": stale == 0,
        }

    @classmethod
    def current_version_records(cls) -> list:
        """Only records resolved under the CURRENT logic. This is what calibration sees."""
        return [t for t in cls.load_closed() if t.get("logic_version") == cls.LOGIC_VERSION]

    @classmethod
    def audit_integrity(cls, repair: bool = False) -> dict:
        """
        Standing integrity check on the closed ledger. Runs on every summary so
        corruption surfaces immediately instead of silently inflating accuracy.

        Catches the churn signature: multiple resolved records sharing a
        (ticker, direction, entry, outcome) tuple. Those are the SAME trade recorded N
        times, and each copy adds a phantom win to accuracy and a phantom R to equity.
        """
        # A setup is LIVE from entry until TP4 or the trail stops it out, so any
        # re-detection inside that window is the same setup, not a new trade. Two
        # records only collapse when they share ticker+direction+entry+outcome AND land
        # inside DEDUP_WINDOW_SECONDS of each other. The same entry price recurring
        # hours later is a genuinely separate opportunity and is preserved — otherwise
        # a legitimate re-entry at a level that worked twice would be silently erased.
        # shadow_id is the primary key and must be checked FIRST. The value-based check
        # below cannot see two rows carrying the same id, so a file with 21 duplicate
        # shadow_ids reported clean: True, duplicates: 0 — the audit was blind to the
        # exact corruption it exists to catch.
        raw = cls.load_closed()
        by_id = {}
        id_dupes = []
        for t in raw:
            sid = t.get("shadow_id")
            if sid and sid in by_id:
                id_dupes.append(t)
            elif sid:
                by_id[sid] = t
        if id_dupes:
            raw = [t for t in raw if id(t) not in {id(d) for d in id_dupes}]

        closed = sorted(raw, key=lambda t: t.get("closed_epoch", 0) or 0)
        seen = {}
        dupes = list(id_dupes)
        for t in closed:
            k = (t.get("ticker"), t.get("direction"), round(float(t.get("entry", 0) or 0), 10),
                 t.get("outcome"))
            ts = float(t.get("closed_epoch", 0) or 0)
            prior = seen.get(k)
            if prior is not None and abs(ts - float(prior.get("closed_epoch", 0) or 0)) <= cls.DEDUP_WINDOW_SECONDS:
                dupes.append(t)
            else:
                seen[k] = t

        report = {
            "total_records": len(closed),
            "unique_trades": len(closed) - len(dupes),   # records KEPT, not distinct keys
            "duplicates": len(dupes),
            "duplicate_shadow_ids": len(id_dupes),
            "duplicate_pct": round(len(dupes) / len(closed) * 100, 1) if closed else 0.0,
            "affected_tickers": sorted({d.get("ticker") for d in dupes}),
            "clean": not dupes,
        }
        # Keep every record that was not flagged as a within-window duplicate.
        if repair and dupes:
            dupe_ids = {id(d) for d in dupes}
            deduped = [t for t in closed if id(t) not in dupe_ids]
            deduped.sort(key=lambda t: t.get("closed_epoch", 0) or 0)
            cls._atomic_write(SHADOW_CLOSED_FILE, deduped)
            report["repaired"] = True
            print(f"[ledger] purged {len(dupes)} duplicate records "
                  f"({', '.join(report['affected_tickers'][:5])})", flush=True)
        return report

    @classmethod
    def cohort_dashboard(cls, vetoed: bool) -> dict:
        """
        Deep breakdown of one cohort. Kept separate from cohort_report() because the
        two answer different questions: that one compares cohorts, this one dissects a
        single cohort to find WHERE inside it the edge lives or dies.
        """
        import statistics
        rows = [t for t in cls.current_version_records() if bool(t.get("kelly_vetoed")) == vetoed]
        if not rows:
            return {"available": False, "n": 0}

        def R(t):
            e = float(t.get("entry", 0) or 0)
            sl = float(t.get("stop_loss", 0) or 0)
            slp = abs(e - sl) / e * 100 if e else 0
            return (float(t.get("pnl_pct", 0) or 0) / slp) if slp else 0.0

        Rs = [R(t) for t in rows]
        w = sum(1 for t in rows if t.get("is_win") is True)
        be = sum(1 for t in rows if t.get("is_breakeven"))
        l = len(rows) - w - be

        def bucket(field, fn=None):
            out = {}
            for t in rows:
                k = fn(t) if fn else (t.get(field) or "UNKNOWN")
                b = out.setdefault(str(k), {"n": 0, "w": 0, "R": 0.0})
                b["n"] += 1
                b["w"] += 1 if t.get("is_win") is True else 0
                b["R"] += R(t)
            for k, b in out.items():
                b["acc"] = round(b["w"] / b["n"] * 100, 1)
                b["R"] = round(b["R"], 2)
                b["avg_R"] = round(b["R"] / b["n"], 3)
            return dict(sorted(out.items(), key=lambda kv: -kv[1]["n"]))

        oc = {}
        for t in rows:
            oc[t.get("outcome", "?")] = oc.get(t.get("outcome", "?"), 0) + 1

        per_ticker = {}
        for t in rows:
            b = per_ticker.setdefault(t["ticker"], {"n": 0, "w": 0, "R": 0.0})
            b["n"] += 1
            b["w"] += 1 if t.get("is_win") is True else 0
            b["R"] += R(t)
        ranked = sorted(({"ticker": k, **v, "R": round(v["R"], 2)} for k, v in per_ticker.items()),
                        key=lambda x: -x["R"])

        stops = [abs(float(t["entry"]) - float(t["stop_loss"])) / float(t["entry"]) * 100
                 for t in rows if float(t.get("entry", 0) or 0) > 0]
        maes = [abs(float(t.get("mae_pct", 0) or 0)) for t in rows]
        mfes = [abs(float(t.get("mfe_pct", 0) or 0)) for t in rows]
        holds = [float(t.get("hold_hours", 0) or 0) for t in rows]

        return {
            "available": True,
            "cohort": "KELLY-VETOED" if vetoed else "KELLY-FUNDED",
            "n": len(rows), "wins": w, "breakeven": be, "losses": l,
            "accuracy_pct": round(w / (w + l) * 100, 1) if (w + l) else 0.0,
            "total_R": round(sum(Rs), 2),
            "avg_R": round(sum(Rs) / len(Rs), 3),
            "best_R": round(max(Rs), 2), "worst_R": round(min(Rs), 2),
            "outcomes": oc,
            "sl_then_tp": oc.get("SL_THEN_TP", 0),
            "acc_if_stops_fixed": round((w + oc.get("SL_THEN_TP", 0)) / max(w + l, 1) * 100, 1),
            "by_score_bin": bucket(None, lambda t: f"{int(float(t.get('raw_score', 0)) // 10 * 10)}-"
                                                   f"{int(float(t.get('raw_score', 0)) // 10 * 10) + 10}"),
            "by_regime": bucket("market_regime"),
            "by_direction": bucket("direction"),
            "best_tickers": ranked[:5],
            "worst_tickers": list(reversed(ranked[-5:])),
            "median_stop_pct": round(statistics.median(stops), 2) if stops else 0.0,
            "median_mae_pct": round(statistics.median(maes), 2) if maes else 0.0,
            "median_mfe_pct": round(statistics.median(mfes), 2) if mfes else 0.0,
            "median_hold_h": round(statistics.median(holds), 2) if holds else 0.0,
        }

    @classmethod
    def cohort_report(cls) -> dict:
        """
        Kelly-funded and Kelly-vetoed cohorts, scored separately.

        The main board must reflect only what Kelly would actually FUND — mixing refused
        setups into it reports performance the account would never have experienced.
        The vetoed cohort is scored alongside it purely as a test of the veto itself.

        SL_THEN_TP is broken out per cohort because it is the most diagnostic outcome
        available: right direction, wrong stop. A vetoed trade that ends SL_THEN_TP is
        evidence the refusal was about stop placement rather than a bad read, which is
        fixable — and counting it as a plain loss would hide that.
        """
        rows = cls.current_version_records()

        def score(cohort):
            if not cohort:
                return {"n": 0}
            w = sum(1 for t in cohort if t.get("is_win") is True)
            be = sum(1 for t in cohort if t.get("is_breakeven"))
            l = len(cohort) - w - be
            oc = {}
            R = 0.0
            for t in cohort:
                oc[t.get("outcome", "?")] = oc.get(t.get("outcome", "?"), 0) + 1
                e = float(t.get("entry", 0) or 0)
                sl = float(t.get("stop_loss", 0) or 0)
                slp = abs(e - sl) / e * 100 if e else 0
                if slp:
                    R += float(t.get("pnl_pct", 0) or 0) / slp
            st = oc.get("SL_THEN_TP", 0)
            return {
                "n": len(cohort), "wins": w, "breakeven": be, "losses": l,
                "accuracy_pct": round(w / (w + l) * 100, 1) if (w + l) else 0.0,
                "total_R": round(R, 2), "avg_R": round(R / len(cohort), 3),
                "sl_then_tp": st,
                "sl_then_tp_pct": round(st / len(cohort) * 100, 1),
                # Right direction, wrong stop — recoverable with better stop placement.
                "recoverable_wins": st,
                "accuracy_if_stops_fixed": round((w + st) / max(w + l, 1) * 100, 1),
                "outcomes": oc,
            }

        # Membership is FROZEN at trade-open time and never recomputed. Recomputing it
        # from the current calibrated win rate made records migrate between cohorts as
        # calibration drifted — the split went 7/38 to 49/10 on the same trades, which
        # makes the comparison untestable. Records opened before the flag existed are
        # UNCLASSIFIED and excluded rather than retro-assigned.
        classified = [t for t in rows if "kelly_vetoed" in t]
        funded = [t for t in classified if not t.get("kelly_vetoed")]
        vetoed = [t for t in classified if t.get("kelly_vetoed")]
        untagged = [t for t in rows if "kelly_vetoed" not in t]
        f, v = score(funded), score(vetoed)
        # The verdict must COMPARE the cohorts, not just check whether vetoed trades
        # lost money. "Refused trades lost money" sounds like vindication even when the
        # funded ones lost MORE per trade — which is the case that actually matters,
        # because it means the win rate Kelly is fed does not rank setups correctly.
        verdict = "INSUFFICIENT DATA"
        if v.get("n", 0) >= 10 and f.get("n", 0) >= 5:
            fa, va = f["avg_R"], v["avg_R"]
            if fa > va + 0.15:
                verdict = (f"KELLY IS SELECTING WELL — funded {fa:+.3f}R/trade vs "
                           f"vetoed {va:+.3f}R/trade")
            elif va > fa + 0.15:
                verdict = (f"⚠️ KELLY IS SELECTING BACKWARDS — funded {fa:+.3f}R/trade is WORSE "
                           f"than vetoed {va:+.3f}R/trade. The calibrated win rate feeding "
                           f"Kelly does not rank setups correctly.")
            else:
                verdict = f"NO SEPARATION — funded {fa:+.3f}R vs vetoed {va:+.3f}R per trade"
        elif v.get("n", 0) >= 10:
            verdict = f"vetoed {v['avg_R']:+.3f}R/trade — too few funded trades to compare"
        f["avg_R_per_trade"] = f.get("avg_R")
        v["avg_R_per_trade"] = v.get("avg_R")
        return {"kelly_funded": f, "kelly_vetoed": v, "untagged": len(untagged),
                "veto_verdict": verdict}

    @classmethod
    def backfill_kelly_veto(cls) -> dict:
        """
        DISABLED. Retro-classifying records against the CURRENT calibrated win rate is
        what made cohorts unstable: the same trade could be 'funded' one hour and
        'vetoed' the next as calibration moved. Cohort membership is now frozen at open
        and historical records stay UNCLASSIFIED rather than being guessed at.
        """
        return {"disabled": True, "reason": "cohort membership is frozen at trade-open",
                "tagged": 0, "skipped_no_winrate": 0,
                "total": len(cls.load_closed())}

    @classmethod
    def _backfill_kelly_veto_legacy(cls) -> dict:
        """
        Retro-classify historical records against the Kelly veto.

        Records written before the veto existed carry no `kelly_vetoed` flag, but every
        input needed to reconstruct the decision is already stored: the calibrated win
        rate at entry, and the R:R implied by entry/stop/TP1. Recomputing it means the
        veto can be judged against 45 real outcomes immediately instead of waiting days
        for new ones — and the verdict is derived, never assumed.

        Records without a recorded win rate are left untagged rather than guessed.
        """
        with cls._lock:
            rows = cls.load_closed()
            tagged = skipped = 0
            for t in rows:
                if "kelly_vetoed" in t and t.get("kelly_backfilled") is not True:
                    continue
                wr = t.get("calibrated_win_rate")
                e = float(t.get("entry", 0) or 0)
                sl = float(t.get("stop_loss", 0) or 0)
                lad = t.get("tp_ladder") or []
                if wr is None:
                    # No win rate was stored, but the calibrator can supply the rate for
                    # this score bin. Derived, not invented — and flagged as such.
                    try:
                        from audit.calibration import WinRateCalibrator
                        wr = WinRateCalibrator.calibrated_win_rate(
                            float(t.get("raw_score", 0) or 0), {}).get("win_rate")
                        t["kelly_wr_source"] = "derived_from_bin"
                    except Exception:
                        wr = None
                if wr is None or e <= 0 or sl <= 0 or not lad:
                    skipped += 1
                    continue
                risk = abs(e - sl)
                reward = abs(float(lad[0]) - e)
                b = reward / risk if risk else 0.0
                p_ = float(wr)
                raw = ((p_ * b) - (1 - p_)) / b if b > 0 else -1.0
                t["kelly_vetoed"] = raw <= 0
                t["kelly_full"] = round(raw, 4)
                t["kelly_backfilled"] = True
                tagged += 1
            cls._atomic_write(SHADOW_CLOSED_FILE, rows)
        return {"tagged": tagged, "skipped_no_winrate": skipped, "total": len(rows)}

    @classmethod
    def kelly_veto_report(cls) -> dict:
        """
        Was Kelly right to refuse?

        Kelly's veto is a falsifiable claim: below break-even, betting loses money.
        Vetoed setups are still opened as SHADOW trades (they cost nothing), so their
        real outcomes accumulate. If vetoed trades turn out to WIN more often than they
        lose, Kelly is wrong about this engine and the evidence will say so plainly
        rather than the question staying an opinion.
        """
        rows = [t for t in cls.current_version_records() if t.get("kelly_vetoed")]
        if not rows:
            return {"available": False, "vetoed_resolved": 0,
                    "note": "no vetoed setups have resolved yet"}
        w = sum(1 for t in rows if t.get("is_win") is True)
        be = sum(1 for t in rows if t.get("is_breakeven"))
        l = len(rows) - w - be
        total_R = 0.0
        for t in rows:
            e = float(t.get("entry", 0) or 0)
            sl = float(t.get("stop_loss", 0) or 0)
            slp = abs(e - sl) / e * 100 if e else 0
            if slp:
                total_R += float(t.get("pnl_pct", 0) or 0) / slp
        acc = w / (w + l) * 100 if (w + l) else 0.0
        return {
            "available": True,
            "vetoed_resolved": len(rows),
            "wins": w, "breakeven": be, "losses": l,
            "accuracy_pct": round(acc, 1),
            "total_R": round(total_R, 2),
            "avg_R": round(total_R / len(rows), 3),
            "verdict": ("KELLY WAS RIGHT — vetoed trades lost money" if total_R < -0.5 else
                        "KELLY WAS WRONG — vetoed trades were profitable" if total_R > 0.5 else
                        "INCONCLUSIVE — vetoed trades roughly break even"),
        }

    @classmethod
    def stop_diagnosis(cls) -> dict:
        """
        Stop width against what price ACTUALLY does. Measured over EVERY trade, not
        just winners — winners are the trades that did not get stopped, so measuring
        only them concludes the stops are fine while most of the book dies on them.
        """
        import statistics
        rows = [t for t in cls.current_version_records()
                if float(t.get('entry', 0) or 0) > 0 and float(t.get('stop_loss', 0) or 0) > 0]
        if len(rows) < 8:
            return {"available": False, "reason": f"only {len(rows)} records"}
        stops = [abs(float(t['entry']) - float(t['stop_loss'])) / float(t['entry']) * 100 for t in rows]
        maes = [abs(float(t.get('mae_pct', 0) or 0)) for t in rows]
        ms, mm = statistics.median(stops), statistics.median(maes)
        ordered = sorted(maes)
        p75 = ordered[min(int(len(ordered) * 0.75), len(ordered) - 1)]
        return {
            "available": True,
            "median_stop_pct": round(ms, 2),
            "median_mae_pct": round(mm, 2),
            "mae_p75_pct": round(p75, 2),
            "ratio": round(mm / ms, 1) if ms else 0.0,
            "too_tight": mm > ms,
            "recommended_multiplier": round(max(1.0, min(p75 * 1.15 / ms, 3.0)), 2) if ms else 1.5,
            "n": len(rows),
        }

    @classmethod
    def equity_curve(cls, starting_capital: float = 1000.0, risk_per_trade: float = 30.0) -> dict:
        """
        What the ledger would have done to a real $1000 account.

        Shadow records store price outcomes, not position sizes, so PnL is expressed in
        R-multiples — pnl_pct divided by the trade's own stop distance — then multiplied
        by a fixed dollar risk. That keeps the equity curve independent of leverage
        guesswork: a trade that made 1R made one unit of risk, whatever size it was.
        """
        closed = sorted(cls.load_closed(), key=lambda t: t.get("closed_epoch", 0) or 0)
        equity = starting_capital
        peak = starting_capital
        max_dd = 0.0
        wins = losses = 0
        r_total = 0.0
        for t in closed:
            entry = float(t.get("entry", 0) or 0)
            sl = float(t.get("stop_loss", 0) or 0)
            if entry <= 0 or sl <= 0:
                continue
            sl_pct = abs(entry - sl) / entry * 100.0
            if sl_pct <= 0:
                continue
            r = float(t.get("pnl_pct", 0.0) or 0.0) / sl_pct
            r_total += r
            equity += risk_per_trade * r
            peak = max(peak, equity)
            dd = (peak - equity) / peak * 100 if peak else 0.0
            max_dd = max(max_dd, dd)
            if t.get("is_win"):
                wins += 1
            else:
                losses += 1

        n = wins + losses
        return {
            "starting_capital": starting_capital,
            "risk_per_trade": risk_per_trade,
            "final_equity": round(equity, 2),
            "net_profit": round(equity - starting_capital, 2),
            "return_pct": round((equity - starting_capital) / starting_capital * 100, 2),
            "total_R": round(r_total, 2),
            "avg_R": round(r_total / n, 3) if n else 0.0,
            "max_drawdown_pct": round(max_dd, 2),
            "trades": n,
        }

    @classmethod
    def performance_ranking(cls, top_n: int = 10) -> dict:
        """Best and worst performers by net PnL, for the on-demand ledger report."""
        closed = cls.load_closed()
        by_ticker = {}
        for t in closed:
            slot = by_ticker.setdefault(t["ticker"], {"n": 0, "w": 0, "pnl": 0.0})
            slot["n"] += 1
            slot["w"] += 1 if t.get("is_win") else 0
            slot["pnl"] += float(t.get("pnl_pct", 0.0) or 0.0)
        rows = [{"ticker": k, "trades": v["n"], "wins": v["w"],
                 "win_rate": round(v["w"] / v["n"] * 100, 1),
                 "net_pct": round(v["pnl"], 2)} for k, v in by_ticker.items()]
        # Best and worst must be DISJOINT and sign-correct. Slicing rows[:10] and
        # rows[-10:] overlaps whenever fewer than 2*top_n tickers have traded, which is
        # why a +0.36% winner was appearing under "WORST 10" and losers were padding
        # out "TOP 10". Split on the sign instead of on position.
        rows.sort(key=lambda r: r["net_pct"], reverse=True)
        winners = [r for r in rows if r["net_pct"] > 0]
        losers = [r for r in rows if r["net_pct"] < 0]
        return {
            "best": winners[:top_n],                                   # most profitable first
            "worst": sorted(losers, key=lambda r: r["net_pct"])[:top_n],  # most negative first
            "flat": len(rows) - len(winners) - len(losers),
            "tickers_traded": len(rows),
        }
