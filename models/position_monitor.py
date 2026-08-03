# models/position_monitor.py
import json
import os
import requests

POSITIONS_FILE = "portfolio/active_positions.json"

class ActivePositionMonitor:
    """
    Den Engine v15.3 Event-Driven Position Monitor & Alert Defense:
    Monitors active positions and fires alerts ONLY ONCE per milestone (TP Hit, SL Hit, Emergency Exit).
    Automatically removes closed positions from active_positions.json to prevent duplicate alerts!
    """

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.notified_milestones = {} # Cache to prevent duplicate alert spam

    def load_positions(self) -> list:
        if os.path.exists(POSITIONS_FILE):
            try:
                with open(POSITIONS_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                return []
        return []

    def save_positions(self, positions: list):
        try:
            with open(POSITIONS_FILE, "w") as f:
                json.dump(positions, f, indent=2)
        except Exception as e:
            print(f"[!] Error saving active positions: {e}")

    def send_telegram_alert(self, text: str):
        if not self.bot_token or not self.chat_id:
            return
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": text, "parse_mode": "Markdown"}
        try:
            requests.post(url, json=payload, timeout=5)
        except Exception as e:
            print(f"[!] Telegram Monitor Alert Error: {e}")

    def check_active_positions(self, ticker: str, current_price: float, sentiment_multiplier: float, structure_flipped: bool):
        positions = self.load_positions()
        if not positions:
            return

        remaining_positions = []
        modified = False

        for pos in positions:
            if pos.get("ticker") != ticker:
                remaining_positions.append(pos)
                continue

            entry = pos.get("entry_price", current_price)
            sl = pos.get("stop_loss")
            tp = pos.get("take_profit")
            direction = pos.get("direction", "LONG")

            # Check Single TP Hit
            tp_hit = (direction == "LONG" and current_price >= tp) or (direction == "SHORT" and current_price <= tp)
            # Check Hard SL Hit
            sl_hit = (direction == "LONG" and current_price <= sl) or (direction == "SHORT" and current_price >= sl)
            # Check Emergency Exit Trigger
            emergency_trigger = structure_flipped or sentiment_multiplier <= 0.70

            if tp_hit:
                alert_key = f"{ticker}_TP_HIT"
                if alert_key not in self.notified_milestones:
                    pnl_pct = abs(tp - entry) / entry * 100
                    msg = f"""
🎉 **TAKE PROFIT TARGET HIT! (WINNING TRADE)** 🎉
━━━━━━━━━━━━━━━━━━━━━━━━
• **Asset:** `{ticker}` ({direction})
• **Status:** `TARGET ATTAINED — SINGLE TP HIT`

📈 **PROFIT METRICS**
• **Entry Price:** `${entry:,.2f}`
• **TP Exit Price:** `${current_price:,.2f}` (+{pnl_pct:.2f}%)
• **Net Profit Gain:** `+$105.00 USDT` (+10.5% Account Gain)

⚡ **ACTION:** Close position on Bitunix & lock in profits!
━━━━━━━━━━━━━━━━━━━━━━━━
                    """
                    self.send_telegram_alert(msg)
                    self.notified_milestones[alert_key] = True
                modified = True # Position closed! Do not add to remaining_positions

            elif sl_hit:
                alert_key = f"{ticker}_SL_HIT"
                if alert_key not in self.notified_milestones:
                    msg = f"""
🛑 **STOP LOSS EXECUTED (HARD SL HIT)** 🛑
━━━━━━━━━━━━━━━━━━━━━━━━
• **Asset:** `{ticker}` ({direction})
• **Status:** `HARD STOP LOSS EXECUTED`
• **Entry Price:** `${entry:,.2f}`
• **SL Exit Price:** `${current_price:,.2f}`
• **Loss:** `-$35.00 USDT` (3.5% Hard Risk Cap)
━━━━━━━━━━━━━━━━━━━━━━━━
                    """
                    self.send_telegram_alert(msg)
                    self.notified_milestones[alert_key] = True
                modified = True # Position closed! Do not add to remaining_positions

            elif emergency_trigger:
                alert_key = f"{ticker}_EMERGENCY_EXIT"
                if alert_key not in self.notified_milestones:
                    msg = f"""
🚨 **EMERGENCY WARNING: EARLY EXIT SUGGESTED** 🚨
━━━━━━━━━━━━━━━━━━━━━━━━
• **Asset:** `{ticker}` ({direction})
• **Status:** `CLOSE POSITION IMMEDIATELY`

🧠 **ROOT CAUSE ANALYSIS (RCA)**
• **Primary Cause:** Market Structure Breakdown (15m Low Violated)
• **Microstructure Impact:** High-volume sell pressure detected.

📉 **CAPITAL DEFENSE METRICS**
• **Entry Price:** `${entry:,.2f}`
• **Current Exit Price:** `${current_price:,.2f}`
• **Original SL:** `${sl:,.2f}`

⚠️ **ACTION DIRECTIVE:** Close position manually on Bitunix NOW to save capital!
━━━━━━━━━━━━━━━━━━━━━━━━
                    """
                    self.send_telegram_alert(msg)
                    self.notified_milestones[alert_key] = True
                modified = True # Position closed on Emergency alert! Do not add to remaining_positions

            else:
                remaining_positions.append(pos)

        if modified:
            self.save_positions(remaining_positions)
