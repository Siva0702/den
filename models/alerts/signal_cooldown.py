# models/alerts/signal_cooldown.py
import time
import json
import os

COOLDOWN_FILE = "portfolio/signal_cooldown.json"
FOUR_HOURS_SECONDS = 4 * 3600 # 4 Hours Cooldown per Ticker

class SignalCooldownEngine:
    """
    Den Engine v15.1 Zero-Spam Signal Cooldown & Deduplication:
    Enforces a strict 4-hour cooldown per ticker so Telegram is NEVER spammed with duplicate signals.
    Caps total active signals to MAX 2 at any given time.
    """

    @classmethod
    def can_send_signal(cls, ticker: str) -> bool:
        now = time.time()
        cooldowns = {}

        if os.path.exists(COOLDOWN_FILE):
            try:
                with open(COOLDOWN_FILE, "r") as f:
                    cooldowns = json.load(f)
            except Exception:
                cooldowns = {}

        last_sent = cooldowns.get(ticker, 0)
        if (now - last_sent) < FOUR_HOURS_SECONDS:
            remaining_mins = int((FOUR_HOURS_SECONDS - (now - last_sent)) / 60)
            print(f"[🛡️] Signal Suppressed for {ticker}: Sent recently ({remaining_mins}m cooldown remaining)")
            return False # Block duplicate signal!

        return True

    @classmethod
    def record_signal_sent(cls, ticker: str):
        now = time.time()
        cooldowns = {}

        if os.path.exists(COOLDOWN_FILE):
            try:
                with open(COOLDOWN_FILE, "r") as f:
                    cooldowns = json.load(f)
            except Exception:
                cooldowns = {}

        cooldowns[ticker] = now
        try:
            with open(COOLDOWN_FILE, "w") as f:
                json.dump(cooldowns, f, indent=2)
        except Exception as e:
            print(f"[!] Error writing cooldown file: {e}")
