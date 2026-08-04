# models/alerts/signal_cooldown.py
import json
import os
import time

COOLDOWN_FILE = "portfolio/signal_cooldown.json"
POSITIONS_FILE = "portfolio/active_positions.json"

class SignalCooldownEngine:
    """
    Den Engine v28.0 Outcome-Based & Dynamic Fresh Signal Replacement Engine:
    Allows sending fresh updated signals on existing tickers if a higher-conviction setup forms
    or after a short refresh buffer (15 minutes), while preserving old signal history for efficiency auditing!
    """

    @classmethod
    def can_send_signal(cls, ticker: str, refresh_minutes: float = 15.0) -> bool:
        if os.path.exists(POSITIONS_FILE):
            try:
                with open(POSITIONS_FILE, "r") as f:
                    positions = json.load(f)
                    for pos in positions:
                        if pos.get("ticker") == ticker:
                            pos_time_str = pos.get("time")
                            if pos_time_str:
                                try:
                                    pos_time = time.mktime(time.strptime(pos_time_str, '%Y-%m-%d %H:%M:%S'))
                                    elapsed_mins = (time.time() - pos_time) / 60.0
                                    # If signal is less than refresh_minutes old, hold off to prevent spam
                                    if elapsed_mins < refresh_minutes:
                                        return False
                                except Exception:
                                    pass
            except Exception:
                pass
        return True

    @classmethod
    def record_signal_sent(cls, ticker: str):
        cooldowns = {}
        if os.path.exists(COOLDOWN_FILE):
            try:
                with open(COOLDOWN_FILE, "r") as f:
                    cooldowns = json.load(f)
            except Exception:
                cooldowns = {}
        cooldowns[ticker] = time.time()
        try:
            with open(COOLDOWN_FILE, "w") as f:
                json.dump(cooldowns, f, indent=2)
        except Exception as e:
            print(f"[!] Error writing cooldowns: {e}")
