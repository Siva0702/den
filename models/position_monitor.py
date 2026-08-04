# models/position_monitor.py
import json
import os
import requests
import sys
import time

sys.path.append(os.path.dirname(__file__))
from audit.engine_efficiency import EngineEfficiencyTracker

POSITIONS_FILE = "portfolio/active_positions.json"

class ActivePositionMonitor:
    """
    Den Engine v35.0 Real-Time Position Monitor & Engine Accuracy Tracker:
    Monitors active positions and calculates REAL PnL from actual price movements.
    Reports live engine efficiency on every TP/SL hit to Telegram.
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
        os.makedirs("portfolio", exist_ok=True)
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
            requests.post(url, json=payload, timeout=10)
        except Exception as e:
            print(f"[!] Telegram Monitor Alert Error: {e}")

    def _calculate_real_pnl(self, pos: dict, exit_price: float) -> dict:
        """Calculate REAL PnL from actual entry/exit prices and position parameters."""
        entry = pos.get("entry_price", exit_price)
        direction = pos.get("direction", "LONG")
        margin = pos.get("margin", 50.0)
        leverage = pos.get("leverage", 15)
        notional = margin * leverage

        if direction == "LONG":
            price_change_pct = (exit_price - entry) / entry
        else:
            price_change_pct = (entry - exit_price) / entry

        pnl_usd = round(notional * price_change_pct, 2)
        pnl_pct = round(price_change_pct * 100, 2)
        roi_pct = round((pnl_usd / max(margin, 0.01)) * 100, 1)

        return {
            "pnl_usd": pnl_usd,
            "pnl_pct": pnl_pct,
            "roi_pct": roi_pct,
            "notional": notional,
            "margin": margin,
            "leverage": leverage
        }

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

            if tp_hit:
                alert_key = f"{ticker}_TP_HIT_{int(pos.get('epoch_time', 0))}"
                if alert_key not in self.notified_milestones:
                    pnl_data = self._calculate_real_pnl(pos, current_price)
                    eff = EngineEfficiencyTracker.record_trade_outcome(
                        ticker, direction, entry, current_price, "WIN", pnl_data["pnl_usd"]
                    )

                    msg = f"""
🎉 **ENGINE WIN: {ticker} HIT TP!** 🎉
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📍 **Entry:** `${entry:,.4f}`
🎯 **TP Exit:** `${current_price:,.4f}`
📈 **Real PnL:** `+${pnl_data['pnl_usd']:,.2f} USDT` (+{pnl_data['roi_pct']}% ROI)
💰 **Margin Used:** `${pnl_data['margin']:,.2f}` ({pnl_data['leverage']}x)

📊 **ENGINE ACCURACY**
• Win Rate: `{eff['realized_win_rate']}%` ({eff['total_wins']}W / {eff['total_losses']}L)
• Net PnL: `${eff['total_engine_pnl_usd']:,.2f} USDT`
• Profit Factor: `{eff['profit_factor']}`
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                    """
                    self.send_telegram_alert(msg)
                    self.notified_milestones[alert_key] = True
                modified = True

            elif sl_hit:
                alert_key = f"{ticker}_SL_HIT_{int(pos.get('epoch_time', 0))}"
                if alert_key not in self.notified_milestones:
                    pnl_data = self._calculate_real_pnl(pos, current_price)
                    eff = EngineEfficiencyTracker.record_trade_outcome(
                        ticker, direction, entry, current_price, "LOSS", pnl_data["pnl_usd"]
                    )

                    msg = f"""
🛑 **ENGINE LOSS: {ticker} HIT SL** 🛑
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📍 **Entry:** `${entry:,.4f}`
🛡️ **SL Exit:** `${current_price:,.4f}`
📉 **Real Loss:** `-${abs(pnl_data['pnl_usd']):,.2f} USDT` ({pnl_data['roi_pct']}% ROI)
💰 **Margin Used:** `${pnl_data['margin']:,.2f}` ({pnl_data['leverage']}x)

📊 **ENGINE ACCURACY**
• Win Rate: `{eff['realized_win_rate']}%` ({eff['total_wins']}W / {eff['total_losses']}L)
• Net PnL: `${eff['total_engine_pnl_usd']:,.2f} USDT`
• Profit Factor: `{eff['profit_factor']}`
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                    """
                    self.send_telegram_alert(msg)
                    self.notified_milestones[alert_key] = True
                modified = True

            else:
                remaining_positions.append(pos)

        if modified:
            self.save_positions(remaining_positions)
