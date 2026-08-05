# models/news/event_outcomes.py
import json
import os
import statistics
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone

OUTCOMES_FILE = "audit/event_outcomes.json"

class EventOutcomeLearner:
    """
    Den Engine v39.5 Event Outcome Learning.

    The calendar knows an event is COMING. This module learns what happens AFTER it.

    The user's case: AMD posted record earnings and the stock fell 8%. A engine that
    only knows "earnings on Tuesday" learns nothing from that. What matters is the
    repeatable pattern — for this asset, in this regime, a beat gets SOLD — and that is
    only learnable by recording the reaction every single time.

    For each event on the calendar the learner snapshots price at T-30min, then samples
    the reaction at +30min, +2h and +6h. Those become labelled records:

        {asset, event_kind, tier, reaction_30m, reaction_2h, reaction_6h, direction, faded}

    From enough of them it answers questions the engine currently guesses at:
      - does THIS asset typically pop or drop on earnings?
      - does the initial move hold, or does it fade? (`fade_rate`)
      - how long until the reaction stabilises, i.e. when is it safe to trade again?

    Until an asset has MIN_SAMPLES events on record, `guidance()` returns
    available=False and the engine falls back to standing clear. An unmeasured pattern
    is not a pattern.
    """

    SNAPSHOT_BEFORE_MIN = 30
    SAMPLES_MIN = (30, 120, 360)      # +30m, +2h, +6h
    MIN_SAMPLES = 3
    MAX_RECORDS = 4000

    _lock = threading.Lock()

    # ------------------------------------------------------------------
    @staticmethod
    def _atomic_write(path, payload):
        d = os.path.dirname(path) or "."
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(payload, f, indent=2, default=str)
            os.replace(tmp, path)
        except Exception as e:
            print(f"[!] Event outcome write failed: {e}")
            try:
                os.unlink(tmp)
            except Exception:
                pass

    @classmethod
    def _load(cls) -> dict:
        if not os.path.exists(OUTCOMES_FILE):
            return {"tracking": [], "resolved": []}
        try:
            with open(OUTCOMES_FILE, "r") as f:
                d = json.load(f)
                d.setdefault("tracking", [])
                d.setdefault("resolved", [])
                return d
        except Exception:
            return {"tracking": [], "resolved": []}

    # ------------------------------------------------------------------
    @classmethod
    def track(cls, ticker: str, asset_class: str, calendar_assessment: dict, price: float):
        """
        Called during enrichment. If a relevant event is imminent, snapshot the
        pre-event price so the reaction can be measured once it fires.
        """
        nxt = (calendar_assessment or {}).get("next_event")
        if not nxt or price is None:
            return
        minutes = nxt.get("minutes_away", 9e9)
        if not (0 <= minutes <= cls.SNAPSHOT_BEFORE_MIN):
            return
        if nxt.get("tier") not in ("CRITICAL", "HIGH"):
            return

        event_id = f"{ticker}|{nxt.get('title', '')}|{nxt.get('when_utc', '')}"
        with cls._lock:
            data = cls._load()
            if any(t["event_id"] == event_id for t in data["tracking"]):
                return
            if any(r["event_id"] == event_id for r in data["resolved"]):
                return
            data["tracking"].append({
                "event_id": event_id,
                "ticker": ticker,
                "asset_class": asset_class,
                "event_kind": nxt.get("kind", "MACRO"),
                "event_title": nxt.get("title", ""),
                "tier": nxt.get("tier"),
                "event_epoch": time.time() + minutes * 60.0,
                "price_before": float(price),
                "samples": {},
            })
            cls._atomic_write(OUTCOMES_FILE, data)
            print(f"[event] tracking {ticker} through '{nxt.get('title', '')[:40]}'", flush=True)

    # ------------------------------------------------------------------
    @classmethod
    def sample(cls, price_map: dict) -> int:
        """Called every scan. Fills in reaction samples and resolves finished events."""
        now = time.time()
        resolved_count = 0
        with cls._lock:
            data = cls._load()
            if not data["tracking"]:
                return 0
            still = []
            for t in data["tracking"]:
                px = price_map.get(t["ticker"])
                if isinstance(px, dict):
                    px = px.get("close")
                elapsed_min = (now - t["event_epoch"]) / 60.0

                if px:
                    for mark in cls.SAMPLES_MIN:
                        key = str(mark)
                        if key not in t["samples"] and elapsed_min >= mark:
                            p0 = t["price_before"]
                            t["samples"][key] = round((float(px) - p0) / p0 * 100.0, 4) if p0 else 0.0

                if elapsed_min > max(cls.SAMPLES_MIN) + 30:
                    s = t["samples"]
                    r30 = s.get("30"); r2 = s.get("120"); r6 = s.get("360")
                    if r30 is None and r2 is None and r6 is None:
                        continue           # never sampled — discard rather than guess
                    first = r30 if r30 is not None else (r2 if r2 is not None else r6)
                    last = r6 if r6 is not None else (r2 if r2 is not None else r30)
                    faded = (first is not None and last is not None
                             and abs(last) < abs(first) * 0.5)
                    reversed_ = (first is not None and last is not None
                                 and first * last < 0)
                    data["resolved"].append({
                        **t,
                        "reaction_30m": r30, "reaction_2h": r2, "reaction_6h": r6,
                        "initial_direction": "UP" if (first or 0) > 0 else "DOWN",
                        "faded": bool(faded), "reversed": bool(reversed_),
                        "resolved_at": datetime.now(timezone.utc).isoformat(),
                    })
                    resolved_count += 1
                else:
                    still.append(t)

            data["tracking"] = still
            if len(data["resolved"]) > cls.MAX_RECORDS:
                data["resolved"] = data["resolved"][-cls.MAX_RECORDS:]
            cls._atomic_write(OUTCOMES_FILE, data)
        return resolved_count

    # ------------------------------------------------------------------
    @classmethod
    def guidance(cls, ticker: str, event_kind: str = None) -> dict:
        """
        What history says about how this asset reacts to this kind of event.
        Returns available=False until there is enough evidence to be worth acting on.
        """
        data = cls._load()
        rows = [r for r in data["resolved"] if r["ticker"] == ticker
                and (event_kind is None or r["event_kind"] == event_kind)]

        if len(rows) < cls.MIN_SAMPLES:
            # Fall back to the asset class — sector behaviour is a weaker but real prior.
            klass = next((r["asset_class"] for r in data["resolved"] if r["ticker"] == ticker), None)
            rows = [r for r in data["resolved"] if klass and r["asset_class"] == klass
                    and (event_kind is None or r["event_kind"] == event_kind)]
            scope = "asset_class"
        else:
            scope = "ticker"

        if len(rows) < cls.MIN_SAMPLES:
            return {"available": False, "samples": len(rows),
                    "reason": f"only {len(rows)} resolved events — need {cls.MIN_SAMPLES}"}

        r30 = [r["reaction_30m"] for r in rows if r.get("reaction_30m") is not None]
        r6 = [r["reaction_6h"] for r in rows if r.get("reaction_6h") is not None]
        fades = sum(1 for r in rows if r.get("faded"))
        reversals = sum(1 for r in rows if r.get("reversed"))

        fade_rate = fades / len(rows)
        reversal_rate = reversals / len(rows)

        if reversal_rate >= 0.5:
            pattern = "REVERSES"
            advice = ("Initial reaction typically reverses — fade the first move rather "
                      "than following it.")
        elif fade_rate >= 0.5:
            pattern = "FADES"
            advice = ("Initial reaction typically fades — do not chase; wait for the "
                      "retracement.")
        else:
            pattern = "HOLDS"
            advice = "Initial reaction typically holds — continuation is the base case."

        return {
            "available": True,
            "scope": scope,
            "samples": len(rows),
            "median_30m_pct": round(statistics.median(r30), 3) if r30 else None,
            "median_6h_pct": round(statistics.median(r6), 3) if r6 else None,
            "mean_abs_move_pct": round(statistics.mean([abs(x) for x in r30]), 3) if r30 else None,
            "fade_rate": round(fade_rate, 3),
            "reversal_rate": round(reversal_rate, 3),
            "pattern": pattern,
            "advice": advice,
        }

    # ------------------------------------------------------------------
    @classmethod
    def stats(cls) -> dict:
        d = cls._load()
        return {"tracking": len(d["tracking"]), "resolved": len(d["resolved"])}
