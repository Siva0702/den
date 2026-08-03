# models/audit/track_record.py
import json
import os
import time

HISTORY_FILE = "portfolio/trade_history.json"

class PerformanceTrackRecord:
    """
    Den Engine Audit & Performance Tracker:
    Maintains a 100% transparent track record of all dispatched signals,
    wins, losses, win rates, and cumulative account PnL.
    """
    @staticmethod
    def _ensure_file():
        if not os.path.exists("portfolio"):
            os.makedirs("portfolio")
        if not os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, "w") as f:
                json.dump({"total_trades": 0, "wins": 0, "losses": 0, "win_rate_pct": 0.0, "total_pnl_usd": 0.0, "trades": []}, f, indent=2)

    @classmethod
    def log_trade_signal(cls, ticker: str, direction: str, entry: float, sl: float, tp: float, win_rate: float, ev: float):
        cls._ensure_file()
        with open(HISTORY_FILE, "r") as f:
            data = json.load(f)

        trade_entry = {
            "id": len(data["trades"]) + 1,
            "timestamp": time.strftime('%Y-%m-%d %H:%M:%S'),
            "ticker": ticker,
            "direction": direction,
            "entry_price": round(entry, 2),
            "stop_loss": round(sl, 2),
            "take_profit": round(tp, 2),
            "model_win_rate": round(win_rate * 100, 1),
            "expected_value": round(ev, 2),
            "status": "OPEN",
            "exit_price": None,
            "pnl_usd": 0.0
        }

        data["trades"].append(trade_entry)
        data["total_trades"] = len(data["trades"])
        
        with open(HISTORY_FILE, "w") as f:
            json.dump(data, f, indent=2)

    @classmethod
    def record_trade_close(cls, ticker: str, exit_price: float, is_win: bool, pnl_usd: float):
        cls._ensure_file()
        with open(HISTORY_FILE, "r") as f:
            data = json.load(f)

        for trade in reversed(data["trades"]):
            if trade["ticker"] == ticker and trade["status"] == "OPEN":
                trade["status"] = "CLOSED_WIN" if is_win else "CLOSED_LOSS"
                trade["exit_price"] = round(exit_price, 2)
                trade["pnl_usd"] = round(pnl_usd, 2)
                break

        closed_trades = [t for t in data["trades"] if t["status"] in ["CLOSED_WIN", "CLOSED_LOSS"]]
        wins = sum(1 for t in closed_trades if t["status"] == "CLOSED_WIN")
        total = len(closed_trades)
        
        data["wins"] = wins
        data["losses"] = total - wins
        data["win_rate_pct"] = round((wins / total * 100), 1) if total > 0 else 0.0
        data["total_pnl_usd"] = round(sum(t["pnl_usd"] for t in closed_trades), 2)

        with open(HISTORY_FILE, "w") as f:
            json.dump(data, f, indent=2)

        return data
