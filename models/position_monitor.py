# models/position_monitor.py
import json
import os
import sys
import time
import requests

sys.path.append(os.path.dirname(__file__))
from audit.track_record import PerformanceTrackRecord

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
        try:
            with open(POSITION_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return []

    def save_positions(self, positions: list):
        with open(POSITION_FILE, "w") as f:
            json.dump(positions, f, indent=2)

    def check_active_positions(self, current_ticker: str, current_price: float, current_sentiment_multiplier: float, structure_flipped: bool):
        """
        Event-Driven Smart Notifications Engine:
        Only alerts when significant milestone events occur:
        1. TP Hit -> WINNER alert
        2. SL Hit -> RISK DEFENSE alert
        3. 50% Distance to TP -> MOVE STOP LOSS TO BREAKEVEN alert (Zero Risk!)
        4. Breaking Bad News / Structure Flip -> EMERGENCY EARLY EXIT alert
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
            tp = pos["take_profit"]

            tp_hit = (direction == "LONG" and current_price >= tp) or (direction == "SHORT" and current_price <= tp)
            sl_hit = (direction == "LONG" and current_price <= sl) or (direction == "SHORT" and current_price >= sl)

            # Calculate 50% Progress to TP for Breakeven SL Adjustment
            tp_dist = abs(tp - entry)
            current_dist = (current_price - entry) if direction == "LONG" else (entry - current_price)
            progress_pct = (current_dist / max(tp_dist, 0.0001)) * 100.0

            # Emergency Bad News or Macro Threat
            sentiment_threat = (direction == "LONG" and current_sentiment_multiplier < 0.85) or \
                               (direction == "SHORT" and current_sentiment_multiplier > 1.15)

            if tp_hit:
                self.send_tp_hit_alert(pos, current_price)
                PerformanceTrackRecord.record_trade_close(current_ticker, current_price, is_win=True, pnl_usd=105.0)
                print(f"[🎉] SINGLE TAKE PROFIT HIT for {current_ticker} at ${current_price:.2f}")
            elif sl_hit:
                self.send_sl_hit_alert(pos, current_price)
                PerformanceTrackRecord.record_trade_close(current_ticker, current_price, is_win=False, pnl_usd=-35.0)
                print(f"[🛑] STOP LOSS EXECUTED for {current_ticker} at ${current_price:.2f}")
            elif sentiment_threat or structure_flipped:
                threat_reason = "Emergency Bad News / Adverse Sentiment Spike" if sentiment_threat else "Market Structure Breakdown"
                self.send_emergency_exit_alert(pos, current_price, threat_reason)
                print(f"[🚨] EMERGENCY EARLY EXIT ALERT for {current_ticker}: {threat_reason}")
            else:
                # Smart Event Alert: Move Stop-Loss to Breakeven when 50%+ to TP!
                if progress_pct >= 50.0 and not pos.get("be_alert_sent", False):
                    pos["be_alert_sent"] = True
                    self.send_breakeven_sl_alert(pos, current_price, progress_pct)
                remaining_positions.append(pos)

        self.save_positions(remaining_positions)

    def send_breakeven_sl_alert(self, position: dict, current_price: float, progress_pct: float):
        pnl_pct = abs((current_price - position["entry_price"]) / position["entry_price"]) * 100

        alert_msg = f"""
🔒 **SMART ACTION: MOVE STOP LOSS TO BREAKEVEN** 🔒
━━━━━━━━━━━━━━━━━━━━━━━━
• **Asset:** `{position['ticker']}` ({position['direction']})
• **Status:** `50%+ PROGRESS TO TAKE PROFIT ({progress_pct:.1f}%)`

📈 **POSITION STATE**
• **Entry Price:** `${position['entry_price']:,.2f}`
• **Current Price:** `${current_price:,.2f}` (+{pnl_pct:.2f}%)
• **Take Profit Target:** `${position['take_profit']:,.2f}`

⚡ **ACTION DIRECTIVE (Bitunix)**
• Change **Stop-Loss** on Bitunix to `${position['entry_price']:,.2f}` (Breakeven Entry).
• **RESULT:** Trade is now 100% Risk-Free (Zero Risk of Loss)!
━━━━━━━━━━━━━━━━━━━━━━━━
        """
        self._dispatch_telegram(alert_msg)

    def send_tp_hit_alert(self, position: dict, current_price: float):
        pnl_pct = abs((current_price - position["entry_price"]) / position["entry_price"]) * 100
        gain_usd = round(105.0, 2)

        stats = PerformanceTrackRecord.record_trade_close(position["ticker"], current_price, is_win=True, pnl_usd=105.0)
        eng_winrate = stats["engine_signals"]["win_rate_pct"]
        usr_winrate = stats["user_taken_trades"]["win_rate_pct"]

        alert_msg = f"""
🎉 **TAKE PROFIT TARGET HIT! (WINNING TRADE)** 🎉
━━━━━━━━━━━━━━━━━━━━━━━━
• **Asset:** `{position['ticker']}` ({position['direction']})
• **Status:** `TARGET ATTAINED — SINGLE TP HIT`

📈 **PROFIT METRICS**
• **Entry Price:** `${position['entry_price']:,.2f}`
• **TP Exit Price:** `${current_price:,.2f}` (+{pnl_pct:.2f}%)
• **Net Profit Gain:** `+${gain_usd:,.2f} USDT` (+10.5% Account Gain)

📊 **DUAL-TRACK AUDIT RECORD**
• **Engine Overall Win Rate:** `{eng_winrate}%` ({stats['engine_signals']['wins']}/{stats['engine_signals']['total']} Signals)
• **User Taken Trades Win Rate:** `{usr_winrate}%` ({stats['user_taken_trades']['wins']}/{stats['user_taken_trades']['total']} Executed)

⚡ **ACTION:** Close position on Bitunix & lock in profits!
━━━━━━━━━━━━━━━━━━━━━━━━
        """
        self._dispatch_telegram(alert_msg)

    def send_sl_hit_alert(self, position: dict, current_price: float):
        pnl_pct = abs((current_price - position["entry_price"]) / position["entry_price"]) * 100

        stats = PerformanceTrackRecord.record_trade_close(position["ticker"], current_price, is_win=False, pnl_usd=-35.0)
        eng_winrate = stats["engine_signals"]["win_rate_pct"]
        usr_winrate = stats["user_taken_trades"]["win_rate_pct"]

        alert_msg = f"""
🛑 **STOP LOSS EXECUTED (RISK PROTECTED)** 🛑
━━━━━━━━━━━━━━━━━━━━━━━━
• **Asset:** `{position['ticker']}` ({position['direction']})
• **Status:** `STOP LOSS EXECUTED`

📉 **EXIT METRICS**
• **Entry Price:** `${position['entry_price']:,.2f}`
• **SL Exit Price:** `${current_price:,.2f}` (-{pnl_pct:.2f}%)
• **Hard Risk Loss:** `-$35.00 USDT` (3.5% Equity Risk)

📊 **DUAL-TRACK AUDIT RECORD**
• **Engine Overall Win Rate:** `{eng_winrate}%` ({stats['engine_signals']['wins']}/{stats['engine_signals']['total']} Signals)
• **User Taken Trades Win Rate:** `{usr_winrate}%` ({stats['user_taken_trades']['wins']}/{stats['user_taken_trades']['total']} Executed)

🛡️ **CAPITAL DEFENSE:** Hard SL executed cleanly. Capital preserved for next setup!
━━━━━━━━━━━━━━━━━━━━━━━━
        """
        self._dispatch_telegram(alert_msg)

    def send_emergency_exit_alert(self, position: dict, current_price: float, reason: str):
        alert_msg = f"""
🚨 **EMERGENCY WARNING: CLOSE POSITION IMMEDIATELY** 🚨
━━━━━━━━━━━━━━━━━━━━━━━━
• **Asset:** `{position['ticker']}` ({position['direction']})
• **Reason:** `{reason}`

📉 **CAPITAL DEFENSE ACTION**
• **Entry Price:** `${position['entry_price']:,.2f}`
• **Current Price:** `${current_price:,.2f}`
• **Original SL:** `${position['stop_loss']:,.2f}`

⚠️ **ACTION:** Close position manually on Bitunix NOW before SL is touched to save capital!
━━━━━━━━━━━━━━━━━━━━━━━━
        """
        self._dispatch_telegram(alert_msg)

    def _dispatch_telegram(self, message: str):
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            requests.post(url, json={"chat_id": self.chat_id, "text": message, "parse_mode": "Markdown"}, timeout=5)
        except Exception as e:
            print(f"[!] Telegram Alert Failed: {e}")
