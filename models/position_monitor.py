# models/position_monitor.py
import json
import os
import requests

POSITION_FILE = "portfolio/active_positions.json"

class ActivePositionMonitor:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self._ensure_file_exists()

    def _ensure_file_exists(self):
        if not os.path.exists("portfolio"):
            os.makedirs("portfolio")
        if not os.path.exists(POSITION_FILE):
            with open(POSITION_FILE, "w") as f:
                json.dump([], f)

    def load_positions(self) -> list:
        with open(POSITION_FILE, "r") as f:
            return json.load(f)

    def save_positions(self, positions: list):
        with open(POSITION_FILE, "w") as f:
            json.dump(positions, f, indent=2)

    def check_active_positions(self, current_ticker: str, current_price: float, current_sentiment_multiplier: float, structure_flipped: bool):
        """
        Evaluates open positions for early thesis invalidation.
        Sends emergency alerts if:
        1. News sentiment reverses violently against position
        2. Market Structure flips (Inverse FVG / Structural Break)
        """
        positions = self.load_positions()
        remaining_positions = []

        for pos in positions:
            if pos["ticker"] != current_ticker:
                remaining_positions.append(pos)
                continue

            direction = pos["direction"]
            entry = pos["entry_price"]
            sl = pos["stop_loss"]

            # Threat Condition 1: Severe Sentiment Reversal
            sentiment_threat = (direction == "LONG" and current_sentiment_multiplier < 0.85) or \
                               (direction == "SHORT" and current_sentiment_multiplier > 1.15)

            # Threat Condition 2: Market Structure Reversal
            structure_threat = structure_flipped

            if sentiment_threat or structure_threat:
                # Trigger Emergency Early Exit Alert
                threat_reason = "Adverse Sentiment Spike" if sentiment_threat else "Market Structure Breakdown"
                self.send_emergency_exit_alert(pos, current_price, threat_reason)
                print(f"[🚨] EARLY EXIT TRIGGERED for {current_ticker}: {threat_reason}")
                # Position removed from active tracking (Closed Early)
            else:
                remaining_positions.append(pos)

        self.save_positions(remaining_positions)

    def send_emergency_exit_alert(self, position: dict, current_price: float, reason: str):
        pnl_pct = ((current_price - position["entry_price"]) / position["entry_price"]) * 100
        if position["direction"] == "SHORT":
            pnl_pct = -pnl_pct

        alert_msg = f"""
🚨 **EMERGENCY WARNING: EARLY EXIT / THREAT TO SL** 🚨
━━━━━━━━━━━━━━━━━━━━━━━━
• **Asset:** `{position['ticker']}` ({position['direction']})
• **Status:** `CLOSE POSITION IMMEDIATELY`
• **Reason:** `{reason}`

📉 **POSITION STATE**
• **Entry Price:** `${position['entry_price']:,.4f}`
• **Current Price:** `${current_price:,.4f}`
• **Current PnL:** `{pnl_pct:+.2f}%`
• **Original SL:** `${position['stop_loss']:,.4f}`

⚠️ **CAPITAL DEFENSE ACTION:**
Thesis invalidated before hitting full Stop Loss. Exit manually on Bitunix to preserve capital!
━━━━━━━━━━━━━━━━━━━━━━━━
        """
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            requests.post(url, json={"chat_id": self.chat_id, "text": alert_msg, "parse_mode": "Markdown"}, timeout=5)
        except Exception as e:
            print(f"[!] Emergency Exit Alert Failed: {e}")
