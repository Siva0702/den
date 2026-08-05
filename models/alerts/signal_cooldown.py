# models/alerts/signal_cooldown.py
import json
import os
import tempfile
import threading
import time

COOLDOWN_FILE = "portfolio/signal_cooldown.json"
POSITIONS_FILE = "portfolio/active_positions.json"

class SignalCooldownEngine:
    """
    Den Engine v39.2 Directional Cooldown & Loss Lockout.

    v38 kept one 15-minute timer per TICKER. Two problems with that:

      1. A LONG signal blocked a legitimate SHORT on the same asset. When a setup
         genuinely flips, that is often the highest-conviction moment available, and the
         engine was muting exactly that.
      2. It had no memory of outcomes. After a stop-out it would happily re-signal the
         same asset 15 minutes later — re-entering the thing that just proved you wrong,
         which is how one bad read becomes four.

    v39.2 keys everything on (ticker, direction) and adds three graded locks:

      NORMAL       30 min per ticker+direction after a dispatch
      LOSS LOCK    2 h on the losing direction after a stop-out
      STREAK LOCK  6 h on the whole ticker after two consecutive losses, because a
                   second loss usually means the regime was misread, not the entry
      FLIP ALLOW   the opposite direction stays open after a loss — being stopped out
                   of a long is evidence FOR a short, not against it
    """

    NORMAL_COOLDOWN_MIN = 30.0
    LOSS_LOCK_HOURS = 2.0
    STREAK_LOCK_HOURS = 6.0
    STREAK_THRESHOLD = 2

    _lock = threading.Lock()

    # ------------------------------------------------------------------
    @staticmethod
    def _atomic_write(path, payload):
        d = os.path.dirname(path) or "."
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp, path)
        except Exception as e:
            print(f"[!] Cooldown write failed: {e}")
            try:
                os.unlink(tmp)
            except Exception:
                pass

    @classmethod
    def _load(cls) -> dict:
        if not os.path.exists(COOLDOWN_FILE):
            return {"dispatches": {}, "losses": {}, "streaks": {}}
        try:
            with open(COOLDOWN_FILE, "r") as f:
                data = json.load(f)
        except Exception:
            return {"dispatches": {}, "losses": {}, "streaks": {}}

        # Migrate the flat v38 {ticker: epoch} shape without losing live timers.
        if "dispatches" not in data:
            data = {"dispatches": {f"{k}|LONG": v for k, v in data.items()},
                    "losses": {}, "streaks": {}}
        for key in ("dispatches", "losses", "streaks"):
            data.setdefault(key, {})
        return data

    @staticmethod
    def _key(ticker: str, direction: str) -> str:
        return f"{ticker}|{direction}"

    # ------------------------------------------------------------------
    @classmethod
    def can_send_signal(cls, ticker: str, direction: str = "LONG",
                        refresh_minutes: float = None) -> bool:
        return cls.check(ticker, direction, refresh_minutes)[0]

    @classmethod
    def check(cls, ticker: str, direction: str = "LONG", refresh_minutes: float = None) -> tuple:
        """Returns (allowed, human-readable reason)."""
        now = time.time()
        window = (refresh_minutes if refresh_minutes is not None else cls.NORMAL_COOLDOWN_MIN) * 60.0

        with cls._lock:
            data = cls._load()

        streak_until = data["streaks"].get(ticker, 0)
        if isinstance(streak_until, (int, float)) and streak_until > now:
            return False, (f"ticker locked {(streak_until - now) / 3600:.1f}h after "
                           f"{cls.STREAK_THRESHOLD} consecutive losses")

        loss_until = data["losses"].get(cls._key(ticker, direction), 0)
        if loss_until > now:
            return False, (f"{direction} locked {(loss_until - now) / 3600:.1f}h after a "
                           f"stop-out (opposite direction still permitted)")

        last = data["dispatches"].get(cls._key(ticker, direction), 0)
        if last and (now - last) < window:
            return False, f"{direction} cooling down, {(window - (now - last)) / 60:.0f} min remaining"

        if os.path.exists(POSITIONS_FILE):
            try:
                with open(POSITIONS_FILE, "r") as f:
                    for pos in json.load(f):
                        if pos.get("ticker") == ticker and pos.get("direction") == direction:
                            return False, f"{direction} position already open"
            except Exception:
                pass

        return True, "clear"

    # ------------------------------------------------------------------
    @classmethod
    def record_signal_sent(cls, ticker: str, direction: str = "LONG"):
        with cls._lock:
            data = cls._load()
            data["dispatches"][cls._key(ticker, direction)] = time.time()
            cls._atomic_write(COOLDOWN_FILE, data)

    @classmethod
    def record_outcome(cls, ticker: str, direction: str, is_win: bool):
        """
        Called when a real position resolves. A win clears the streak; a loss locks the
        losing direction, and a second consecutive loss locks the whole ticker.
        """
        now = time.time()
        counter_key = f"{ticker}|streak"
        with cls._lock:
            data = cls._load()

            if is_win:
                data["streaks"].pop(ticker, None)
                data["streaks"].pop(counter_key, None)
                data["losses"].pop(cls._key(ticker, direction), None)
            else:
                data["losses"][cls._key(ticker, direction)] = now + cls.LOSS_LOCK_HOURS * 3600
                count = int(data["streaks"].get(counter_key, 0) or 0) + 1
                data["streaks"][counter_key] = count
                if count >= cls.STREAK_THRESHOLD:
                    data["streaks"][ticker] = now + cls.STREAK_LOCK_HOURS * 3600
                    data["streaks"][counter_key] = 0
                    print(f"[cooldown] {ticker} locked {cls.STREAK_LOCK_HOURS}h — "
                          f"{cls.STREAK_THRESHOLD} consecutive losses", flush=True)

            # Expire stale entries so the file cannot grow without bound.
            data["losses"] = {k: v for k, v in data["losses"].items() if v > now}
            data["streaks"] = {k: v for k, v in data["streaks"].items()
                               if k.endswith("|streak") or (isinstance(v, (int, float)) and v > now)}
            cls._atomic_write(COOLDOWN_FILE, data)
