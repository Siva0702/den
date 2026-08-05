# models/audit/score_tracker.py
import statistics
import threading
import time
from collections import defaultdict, deque

class ScoreStabilityTracker:
    """
    Den Engine v39.0 Score Stability & Decay Guard.

    Solves the specific failure the user described: a pair shows 81, the trade is taken,
    and minutes later the same setup scores 42. A score that unstable was never an 81 —
    it was a momentary spike in a noisy function, and acting on the spike is how you buy
    the exact top of a wick.

    The fix is to refuse to trade an instantaneous reading. A setup must EARN dispatch by
    holding its score across consecutive scans:

      1. PERSISTENCE — at or above threshold for MIN_CONSECUTIVE scans in a row.
      2. STABILITY   — standard deviation across the recent window under MAX_STDEV.
                       A score oscillating 80/55/78/49 is noise, not conviction.
      3. SLOPE       — the trend across the window must not be decaying. A setup at 79
                       and falling is a setup that has already happened.

    All three must pass. This trades fewer signals for signals that were real for
    minutes rather than for one 15-second sample.
    """

    WINDOW = 12                 # samples retained per setup (~3 min at 15s cadence)
    MIN_CONSECUTIVE = 3         # scans that must clear threshold back-to-back
    MAX_STDEV = 9.0             # score points; above this the reading is noise
    MAX_DECAY_SLOPE = -1.5      # points per scan; steeper than this is a fading setup
    STALE_SECONDS = 900         # drop tracking for setups not seen in 15 min

    _history = {}
    _lock = threading.Lock()

    @staticmethod
    def _key(ticker: str, direction: str) -> str:
        return f"{ticker}|{direction}"

    # ------------------------------------------------------------------
    @classmethod
    def record(cls, ticker: str, direction: str, score: float) -> None:
        key = cls._key(ticker, direction)
        with cls._lock:
            if key not in cls._history:
                cls._history[key] = deque(maxlen=cls.WINDOW)
            cls._history[key].append((time.time(), float(score)))

    @classmethod
    def prune(cls) -> None:
        cutoff = time.time() - cls.STALE_SECONDS
        with cls._lock:
            dead = [k for k, v in cls._history.items() if not v or v[-1][0] < cutoff]
            for k in dead:
                del cls._history[k]

    # ------------------------------------------------------------------
    @classmethod
    def _slope(cls, values: list) -> float:
        """Least-squares slope in score points per sample."""
        n = len(values)
        if n < 2:
            return 0.0
        mean_x = (n - 1) / 2.0
        mean_y = sum(values) / n
        num = sum((i - mean_x) * (v - mean_y) for i, v in enumerate(values))
        den = sum((i - mean_x) ** 2 for i in range(n))
        return num / den if den else 0.0

    # ------------------------------------------------------------------
    @classmethod
    def evaluate(cls, ticker: str, direction: str, threshold: float) -> dict:
        """
        Verdict on whether this setup is stable enough to dispatch.
        Call AFTER record() for the current scan.
        """
        with cls._lock:
            samples = list(cls._history.get(cls._key(ticker, direction), []))

        values = [s for _, s in samples]
        n = len(values)

        if n < cls.MIN_CONSECUTIVE:
            return {
                "stable": False,
                "reason": f"only {n}/{cls.MIN_CONSECUTIVE} confirming scans so far",
                "samples": n, "consecutive": 0, "stdev": 0.0, "slope": 0.0,
                "current": values[-1] if values else 0.0,
            }

        # Consecutive scans at or above threshold, counted back from the newest.
        consecutive = 0
        for v in reversed(values):
            if v >= threshold:
                consecutive += 1
            else:
                break

        window = values[-cls.MIN_CONSECUTIVE * 2:] if n >= cls.MIN_CONSECUTIVE * 2 else values
        stdev = statistics.pstdev(window) if len(window) > 1 else 0.0
        slope = cls._slope(window)
        current = values[-1]
        peak = max(values)

        failures = []
        if consecutive < cls.MIN_CONSECUTIVE:
            failures.append(f"held threshold for only {consecutive}/{cls.MIN_CONSECUTIVE} consecutive scans")
        if stdev > cls.MAX_STDEV:
            failures.append(f"score unstable (σ={stdev:.1f} > {cls.MAX_STDEV}) — reading is noise, not conviction")
        if slope < cls.MAX_DECAY_SLOPE:
            failures.append(f"score decaying ({slope:+.1f}/scan) — setup is fading, not forming")

        # Guard against dispatching a setup already well off its own peak.
        drawdown_from_peak = peak - current
        if drawdown_from_peak > 12.0:
            failures.append(f"already {drawdown_from_peak:.0f} points off peak ({peak:.0f}) — the move happened without us")

        return {
            "stable": not failures,
            "reason": "; ".join(failures) if failures else
                      f"held {consecutive} consecutive scans, σ={stdev:.1f}, slope {slope:+.1f}",
            "samples": n,
            "consecutive": consecutive,
            "stdev": round(stdev, 2),
            "slope": round(slope, 2),
            "current": round(current, 2),
            "peak": round(peak, 2),
            "drawdown_from_peak": round(drawdown_from_peak, 2),
        }

    # ------------------------------------------------------------------
    @classmethod
    def trajectory(cls, ticker: str, direction: str) -> list:
        """Recent score path, for inclusion in the alert so the user sees the history."""
        with cls._lock:
            return [round(s, 1) for _, s in cls._history.get(cls._key(ticker, direction), [])]
