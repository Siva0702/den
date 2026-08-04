# models/alerts/signal_cooldown.py
import json
import os
import time

COOLDOWN_FILE = "portfolio/signal_cooldown.json"
POSITIONS_FILE = "portfolio/active_positions.json"

class SignalCooldownEngine:
    """
    Den Engine v31.0 Timezone-Proof Outcome & Dynamic Signal Replacement Engine:
    Uses 100% UTC Epoch Timestamps (time.time()) to eliminate timezone discrepancies
    between local developer systems (IST) and Render Cloud servers (UTC)!
    """

    @classmethod
    def can_send_signal(cls, ticker: str, refresh_minutes: float = 15.0) -> bool:
        if os.path.exists(POSITIONS_FILE):
            try:
                with open(POSITIONS_FILE, "r") as f:
                    positions = json.load(f)
                    for pos in positions:
                        if pos.get("ticker") == ticker:
                            pos_epoch = pos.get("epoch_time")
                            if pos_epoch is not None:
                                elapsed_mins = (time.time() - float(pos_epoch)) / 60.0
                                # If signal was sent less than refresh_minutes ago, hold off to prevent spam
                                if 0 <= elapsed_mins < refresh_minutes:
                                    return False
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
