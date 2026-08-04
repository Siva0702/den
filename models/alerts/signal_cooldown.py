# models/alerts/signal_cooldown.py
import json
import os

POSITIONS_FILE = "portfolio/active_positions.json"

class SignalCooldownEngine:
    """
    Den Engine v23.0 Outcome-Based Dynamic Cooldown:
    Replaces arbitrary 4-hour time limits with Active Position Outcome Tracking.
    - If a ticker has an ACTIVE OPEN TRADE, duplicate signals for that ticker are blocked.
    - As soon as the trade hits TP or SL (position closes), the ticker is IMMEDIATELY UNLOCKED for fresh signals!
    """

    @classmethod
    def can_send_signal(cls, ticker: str) -> bool:
        if os.path.exists(POSITIONS_FILE):
            try:
                with open(POSITIONS_FILE, "r") as f:
                    positions = json.load(f)
                    for pos in positions:
                        if pos.get("ticker") == ticker:
                            print(f"[🛡️] Signal Suppressed for {ticker}: Trade currently ACTIVE in position monitor.")
                            return False # Block duplicate signal while position is open!
            except Exception as e:
                print(f"[!] Error reading active positions for cooldown check: {e}")

        return True # Trade has closed or is not active -> Free to send fresh signal!

    @classmethod
    def record_signal_sent(cls, ticker: str):
        # Position is logged directly in active_positions.json by position monitor
        pass
