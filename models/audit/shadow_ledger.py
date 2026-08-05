# models/audit/shadow_ledger.py
import json
import os
import tempfile
import threading
import time

SHADOW_OPEN_FILE = "audit/shadow_open.json"
SHADOW_CLOSED_FILE = "audit/shadow_closed.json"

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
    SHADOW_FLOOR = 55.0
    MAX_HOLD_SECONDS = 36 * 3600
    SL_GRACE_SECONDS = 6 * 3600   # after a stop, keep watching to learn if it was too tight
    # Win is defined by the PLANNED exit rung, not the last rung on the ladder.
    # Resolving on TP4 booked trades that banked TP1+TP2 as losses.
    PRIMARY_TP_INDEX = 0
    MAX_CLOSED_RECORDS = 8000

    _lock = threading.Lock()

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

    @staticmethod
    def _load(path: str, default):
        if not os.path.exists(path):
            return default
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
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
        score = float(candidate.get("total_score", 0.0))
        if score < cls.SHADOW_FLOOR:
            return False

        ticker = candidate.get("ticker")
        direction = candidate.get("direction")
        entry = float(candidate.get("entry", 0.0))
        if not ticker or direction not in ("LONG", "SHORT") or entry <= 0:
            return False

        with cls._lock:
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
                "raw_score": score,
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
            open_trades.append(record)
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

                # TP ladder progress
                for idx, tp_level in enumerate(t.get("tp_ladder", []) or [t.get("primary_tp")], start=1):
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

                if sl_first:
                    # Position would already be flat. Keep observing for a grace window
                    # purely to learn whether the stop was simply too tight.
                    if reached_final:
                        resolved.append(cls._resolve(t, price, "SL_THEN_TP"))
                    elif now - (t["sl_touched_epoch"] or now) > cls.SL_GRACE_SECONDS:
                        resolved.append(cls._resolve(t, price, "SL_HIT"))
                    else:
                        still_open.append(t)
                elif reached_final:
                    resolved.append(cls._resolve(t, price, "TP_FINAL"))
                elif t["sl_touched"] and t["tp_levels_hit"]:
                    # Partial target banked BEFORE the stop — a managed scratch.
                    resolved.append(cls._resolve(t, price, "PARTIAL_THEN_SL"))
                elif now - t.get("opened_epoch", now) > cls.MAX_HOLD_SECONDS:
                    resolved.append(cls._resolve(t, price, "TIMEOUT"))
                else:
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
        is_win = outcome in ("TP_FINAL",) or (outcome == "PARTIAL_THEN_SL" and pnl_pct > 0)

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
        })
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
        closed = cls.load_closed()
        if not closed:
            return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "open": len(cls.load_open())}

        wins = sum(1 for t in closed if t.get("is_win"))
        sl_then_tp = sum(1 for t in closed if t.get("outcome") == "SL_THEN_TP")
        timeouts = sum(1 for t in closed if t.get("outcome", "").startswith("TIMEOUT"))
        return {
            "total": len(closed),
            "wins": wins,
            "losses": len(closed) - wins,
            "win_rate": round(wins / len(closed) * 100, 1),
            "sl_then_tp_count": sl_then_tp,
            "sl_then_tp_pct": round(sl_then_tp / len(closed) * 100, 1),
            "timeouts": timeouts,
            "avg_mae_pct": round(sum(abs(t.get("mae_pct", 0)) for t in closed) / len(closed), 3),
            "avg_mfe_pct": round(sum(abs(t.get("mfe_pct", 0)) for t in closed) / len(closed), 3),
            "open": len(cls.load_open()),
        }
