# models/audit/engine_efficiency.py
import json
import os
import time

EFFICIENCY_FILE = "audit/engine_efficiency.json"

class EngineEfficiencyTracker:
    """
    Den Engine v27.0 Real-Time Engine Accuracy & Efficiency Audit System:
    Tracks every dispatched signal's outcome (Hit TP or Hit SL) regardless of whether the user executed it.
    Calculates Realized Win Rate %, Net Engine PnL, and Profit Factor.
    """

    @classmethod
    def load_efficiency_data(cls) -> dict:
        if os.path.exists(EFFICIENCY_FILE):
            try:
                with open(EFFICIENCY_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "total_signals_dispatched": 0,
            "total_wins": 0,
            "total_losses": 0,
            "realized_win_rate": 0.0,
            "total_engine_pnl_usd": 0.0,
            "profit_factor": 0.0,
            "history": []
        }

    @classmethod
    def record_trade_outcome(cls, ticker: str, direction: str, entry: float, exit_price: float, outcome: str, pnl_usd: float) -> dict:
        data = cls.load_efficiency_data()
        
        data["total_signals_dispatched"] += 1
        if outcome == "WIN":
            data["total_wins"] += 1
        else:
            data["total_losses"] += 1

        total = data["total_signals_dispatched"]
        data["realized_win_rate"] = round((data["total_wins"] / total) * 100, 1) if total > 0 else 0.0
        data["total_engine_pnl_usd"] = round(data["total_engine_pnl_usd"] + pnl_usd, 2)
        
        total_wins_pnl = sum([h["pnl_usd"] for h in data["history"] if h["outcome"] == "WIN"] + ([pnl_usd] if outcome == "WIN" else []))
        total_loss_pnl = abs(sum([h["pnl_usd"] for h in data["history"] if h["outcome"] == "LOSS"] + ([pnl_usd] if outcome == "LOSS" else [])))
        data["profit_factor"] = round(total_wins_pnl / max(total_loss_pnl, 1.0), 2)

        data["history"].append({
            "ticker": ticker,
            "direction": direction,
            "entry_price": entry,
            "exit_price": exit_price,
            "outcome": outcome,
            "pnl_usd": pnl_usd,
            "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')
        })

        os.makedirs("audit", exist_ok=True)
        try:
            with open(EFFICIENCY_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[!] Error writing engine efficiency data: {e}")

        return data
