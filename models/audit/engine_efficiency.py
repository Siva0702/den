# models/audit/engine_efficiency.py
import json
import os
import time

EFFICIENCY_FILE = "audit/engine_efficiency.json"

class EngineEfficiencyTracker:
    """
    Den Engine v35.0 Real-Time Engine Accuracy Tracker:
    Records ONLY actual TP/SL outcomes with REAL calculated PnL.
    Win Rate = Wins / (Wins + Losses) from actual price hits only.
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
            "total_wins": 0,
            "total_losses": 0,
            "realized_win_rate": 0.0,
            "total_engine_pnl_usd": 0.0,
            "gross_wins_usd": 0.0,
            "gross_losses_usd": 0.0,
            "profit_factor": 0.0,
            "history": []
        }

    @classmethod
    def record_trade_outcome(cls, ticker: str, direction: str, entry: float, exit_price: float, outcome: str, pnl_usd: float) -> dict:
        data = cls.load_efficiency_data()

        if outcome == "WIN":
            data["total_wins"] += 1
            data["gross_wins_usd"] = round(data.get("gross_wins_usd", 0.0) + pnl_usd, 2)
        else:
            data["total_losses"] += 1
            data["gross_losses_usd"] = round(data.get("gross_losses_usd", 0.0) + abs(pnl_usd), 2)

        total = data["total_wins"] + data["total_losses"]
        data["realized_win_rate"] = round((data["total_wins"] / total) * 100, 1) if total > 0 else 0.0
        data["total_engine_pnl_usd"] = round(data["total_engine_pnl_usd"] + pnl_usd, 2)
        data["profit_factor"] = round(data["gross_wins_usd"] / max(data["gross_losses_usd"], 0.01), 2)

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
