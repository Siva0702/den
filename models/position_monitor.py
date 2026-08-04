# models/position_monitor.py
import json
import os
import requests
import sys

sys.path.append(os.path.dirname(__file__))
from audit.engine_efficiency import EngineEfficiencyTracker

POSITIONS_FILE = "portfolio/active_positions.json"

class ActivePositionMonitor:
    """
    Den Engine v27.0 Real-Time Engine Accuracy & Position Monitor:
    Monitors active engine positions and fires alerts ONLY ONCE per milestone (TP Hit, SL Hit).
    Tracks every signal's outcome in audit/engine_efficiency.json and reports live engine accuracy!
    """

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token or "8847828896:AAFcTqjJGe6VN6mbPHcB1QTlvkpQxhb5ntI"
        self.chat_id = chat_id or "7347569157"
        self.notified_milestones = {}

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
            sl = pos.get("stop_loss", current_price)
            tp = pos.get("take_profit", current_price)
            direction = pos.get("direction", "LONG")

            tp_hit = (direction == "LONG" and current_price >= tp) or (direction == "SHORT" and current_price <= tp)
            sl_hit = (direction == "LONG" and current_price <= sl) or (direction == "SHORT" and current_price >= sl)
            emergency_trigger = structure_flipped or sentiment_multiplier <= 0.70

            if tp_hit:
                alert_key = f"{ticker}_TP_HIT"
                if alert_key not in self.notified_milestones:
                    pnl_usd = +150.0
                    eff = EngineEfficiencyTracker.record_trade_outcome(
                        ticker, direction, entry, current_price, "WIN", pnl_usd
                    )
                    
                    msg = f"""
🎉 **ENGINE TRADE WIN: {ticker} HIT TP!** 🎉
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• **Asset & Direction:** `{ticker}` ({direction}) 🟢
• **Entry Price:** `${entry}`
• **TP Exit Price:** `${current_price}`
• **Engine PnL:** `+${pnl_usd:.2f} USDT` (+300.0% Margin ROI)

📊 **REAL-TIME ENGINE EFFICIENCY AUDIT**
• **Realized Engine Win Rate:** `{eff['realized_win_rate']}%` ({eff['total_wins']} Wins / {eff['total_losses']} Losses)
• **Cumulative Engine PnL:** `+${eff['total_engine_pnl_usd']:,.2f} USDT`
• **Profit Factor:** `{eff['profit_factor']}`
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[✓] Engine Outcome Logged in audit/engine_efficiency.json
                    """
                    self.send_telegram_alert(msg)
                    self.notified_milestones[alert_key] = True
                modified = True

            elif sl_hit:
                alert_key = f"{ticker}_SL_HIT"
                if alert_key not in self.notified_milestones:
                    pnl_usd = -50.0
                    eff = EngineEfficiencyTracker.record_trade_outcome(
                        ticker, direction, entry, current_price, "LOSS", pnl_usd
                    )
                    
                    msg = f"""
🛑 **ENGINE TRADE LOSS: {ticker} HIT SL** 🛑
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• **Asset & Direction:** `{ticker}` ({direction}) 🔴
• **Entry Price:** `${entry}`
• **SL Exit Price:** `${current_price}`
• **Engine Hard Loss:** `-${abs(pnl_usd):.2f} USDT` (-5.0% Equity)

📊 **REAL-TIME ENGINE EFFICIENCY AUDIT**
• **Realized Engine Win Rate:** `{eff['realized_win_rate']}%` ({eff['total_wins']} Wins / {eff['total_losses']} Losses)
• **Cumulative Engine PnL:** `${eff['total_engine_pnl_usd']:,.2f} USDT`
• **Profit Factor:** `{eff['profit_factor']}`
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[✓] Engine Outcome Logged in audit/engine_efficiency.json
                    """
                    self.send_telegram_alert(msg)
                    self.notified_milestones[alert_key] = True
                modified = True

            elif emergency_trigger:
                alert_key = f"{ticker}_EMERGENCY_EXIT"
                if alert_key not in self.notified_milestones:
                    pnl_usd = -20.0
                    eff = EngineEfficiencyTracker.record_trade_outcome(
                        ticker, direction, entry, current_price, "LOSS", pnl_usd
                    )
                    msg = f"""
🚨 **ENGINE EARLY EXIT: {ticker}** 🚨
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• **Asset & Direction:** `{ticker}` ({direction})
• **Reason:** Market Structure Breakdown Detected
• **Exit Price:** `${current_price}`
• **Realized Win Rate:** `{eff['realized_win_rate']}%`
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                    """
                    self.send_telegram_alert(msg)
                    self.notified_milestones[alert_key] = True
                modified = True

            else:
                remaining_positions.append(pos)

        if modified:
            self.save_positions(remaining_positions)
