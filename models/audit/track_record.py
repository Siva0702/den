# models/audit/track_record.py
import json
import os
import time

HISTORY_FILE = "portfolio/trade_history.json"

class PerformanceTrackRecord:
    """
    Den Engine v7.0 Dual-Track Performance Audit System:
    Tracks:
    1. All Engine Generated Signals Win Rate & PnL
    2. User-Executed / Taken Trades Win Rate & PnL
    """
    @staticmethod
    def _ensure_file():
        if not os.path.exists("portfolio"):
            os.makedirs("portfolio")
        if not os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, "w") as f:
                json.dump({
                    "engine_signals": {"total": 0, "wins": 0, "losses": 0, "win_rate_pct": 0.0, "pnl_usd": 0.0},
                    "user_taken_trades": {"total": 0, "wins": 0, "losses": 0, "win_rate_pct": 0.0, "pnl_usd": 0.0},
                    "trades": []
                }, f, indent=2)

    @classmethod
    def log_trade_signal(cls, ticker: str, direction: str, entry: float, sl: float, tp: float, win_rate: float, ev: float, user_taken: bool = False):
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
            "user_taken": user_taken,
            "status": "OPEN",
            "exit_price": None,
            "pnl_usd": 0.0
        }

        data["trades"].append(trade_entry)
        data["engine_signals"]["total"] = len(data["trades"])
        
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

        # Re-calculate Engine Signals Stats
        all_closed = [t for t in data["trades"] if t["status"] in ["CLOSED_WIN", "CLOSED_LOSS"]]
        eng_wins = sum(1 for t in all_closed if t["status"] == "CLOSED_WIN")
        eng_total = len(all_closed)
        
        data["engine_signals"]["wins"] = eng_wins
        data["engine_signals"]["losses"] = eng_total - eng_wins
        data["engine_signals"]["win_rate_pct"] = round((eng_wins / eng_total * 100), 1) if eng_total > 0 else 0.0
        data["engine_signals"]["pnl_usd"] = round(sum(t["pnl_usd"] for t in all_closed), 2)

        # Re-calculate User Taken Stats
        user_closed = [t for t in all_closed if t.get("user_taken", False)]
        usr_wins = sum(1 for t in user_closed if t["status"] == "CLOSED_WIN")
        usr_total = len(user_closed)

        data["user_taken_trades"]["total"] = usr_total
        data["user_taken_trades"]["wins"] = usr_wins
        data["user_taken_trades"]["losses"] = usr_total - usr_wins
        data["user_taken_trades"]["win_rate_pct"] = round((usr_wins / usr_total * 100), 1) if usr_total > 0 else 0.0
        data["user_taken_trades"]["pnl_usd"] = round(sum(t["pnl_usd"] for t in user_closed), 2)

        with open(HISTORY_FILE, "w") as f:
            json.dump(data, f, indent=2)

        return data
