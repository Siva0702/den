# models/audit/engine_efficiency.py
import json
import os
import time

# Same defect that broke shadow persistence: Render runs `python models/auto_scanner.py`
# so cwd is the REPO ROOT, and these relative paths resolved to <repo>/audit/... — a
# directory that does not exist — instead of <repo>/models/audit/. Every realised trade
# outcome was being written somewhere nothing reads, and never synced to Redis.
MODELS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EFFICIENCY_FILE = os.path.join(MODELS_DIR, "audit/engine_efficiency.json")
TRADE_HISTORY_FILE = os.path.join(MODELS_DIR, "portfolio/trade_history.json")

class EngineEfficiencyTracker:
    """
    Den Engine v38.0 Self-Learning Engine Accuracy & Performance Tracker:
    - Records EVERY trade outcome (WIN/LOSS) with real PnL
    - Tracks per-ticker performance (wins/losses/streak per ticker)
    - Stores factor score snapshots for self-learning weight adjustment
    - Writes to BOTH audit/engine_efficiency.json AND portfolio/trade_history.json
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
            "per_ticker": {},
            "total_breakeven": 0,
            "history": []
        }

    @classmethod
    def record_trade_outcome(cls, ticker: str, direction: str, entry: float,
                             exit_price: float, outcome: str, pnl_usd: float,
                             factor_scores: dict = None, win_rate_at_entry: float = 0.0,
                             user_positioned: bool = False) -> dict:
        """
        Record a completed trade outcome with optional factor score snapshot.
        factor_scores: dict of {factor_name: score} from confluence engine at signal time.
        """
        data = cls.load_efficiency_data()

        # BREAKEVEN is now a real outcome (reached TP1, trailed back to entry). The old
        # binary `if WIN else LOSS` booked every one of them as a LOSS, understating the
        # win rate and inflating gross losses with trades that lost nothing.
        if outcome == "WIN":
            data["total_wins"] += 1
            data["gross_wins_usd"] = round(data.get("gross_wins_usd", 0.0) + pnl_usd, 2)
        elif outcome in ("BREAKEVEN", "SCRATCH"):
            data["total_breakeven"] = data.get("total_breakeven", 0) + 1
        else:
            data["total_losses"] += 1
            data["gross_losses_usd"] = round(data.get("gross_losses_usd", 0.0) + abs(pnl_usd), 2)

        total = data["total_wins"] + data["total_losses"]
        data["realized_win_rate"] = round((data["total_wins"] / total) * 100, 1) if total > 0 else 0.0
        data["total_engine_pnl_usd"] = round(data["total_engine_pnl_usd"] + pnl_usd, 2)
        data["profit_factor"] = round(data["gross_wins_usd"] / max(data["gross_losses_usd"], 0.01), 2)

        # Per-ticker tracking for self-learning
        if "per_ticker" not in data:
            data["per_ticker"] = {}
        
        if ticker not in data["per_ticker"]:
            data["per_ticker"][ticker] = {
                "wins": 0, "losses": 0, "net_pnl": 0.0,
                "streak": 0, "last_outcome": None
            }
        
        tk = data["per_ticker"][ticker]
        if outcome == "WIN":
            tk["wins"] += 1
            tk["streak"] = max(tk["streak"], 0) + 1
        else:
            tk["losses"] += 1
            tk["streak"] = min(tk["streak"], 0) - 1
        tk["net_pnl"] = round(tk["net_pnl"] + pnl_usd, 2)
        tk["last_outcome"] = outcome

        # Trade record with factor snapshot
        trade_record = {
            "ticker": ticker,
            "direction": direction,
            "entry_price": entry,
            "exit_price": exit_price,
            "outcome": outcome,
            "pnl_usd": pnl_usd,
            "win_rate_at_entry": win_rate_at_entry,
            "user_positioned": user_positioned,
            "factor_scores": factor_scores or {},
            "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')
        }

        data["history"].append(trade_record)
        # History grew without bound — this file is synced to Redis on every resolution
        # and would eventually breach the ~1MB REST request limit, silently killing
        # persistence for the whole key.
        if len(data["history"]) > 4000:
            data["history"] = data["history"][-4000:]

        # Write to efficiency file
        os.makedirs("audit", exist_ok=True)
        try:
            with open(EFFICIENCY_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[!] Error writing engine efficiency data: {e}")

        # ALSO write to trade_history.json (fixes the self-learning disconnect)
        cls._sync_to_trade_history(trade_record)

        return data

    @classmethod
    def _sync_to_trade_history(cls, trade_record: dict):
        """Write trade outcome to portfolio/trade_history.json for self-learning module."""
        os.makedirs("portfolio", exist_ok=True)
        history_data = {"trades": [], "engine_signals": {"total": 0, "wins": 0, "losses": 0, "win_rate_pct": 0.0, "pnl_usd": 0.0}}
        
        if os.path.exists(TRADE_HISTORY_FILE):
            try:
                with open(TRADE_HISTORY_FILE, "r") as f:
                    history_data = json.load(f)
            except Exception:
                pass

        # Append trade to history
        trade_id = len(history_data.get("trades", [])) + 1
        history_data.setdefault("trades", []).append({
            "id": trade_id,
            "timestamp": trade_record["timestamp"],
            "ticker": trade_record["ticker"],
            "direction": trade_record["direction"],
            "entry_price": trade_record["entry_price"],
            "exit_price": trade_record["exit_price"],
            "model_win_rate": trade_record.get("win_rate_at_entry", 0.0) * 100,
            "status": "CLOSED",
            "outcome": trade_record["outcome"],
            "pnl_usd": trade_record["pnl_usd"],
            "factor_scores": trade_record.get("factor_scores", {}),
            "user_taken": trade_record.get("user_positioned", False)
        })

        # Update summary stats
        stats = history_data.setdefault("engine_signals", {})
        stats["total"] = stats.get("total", 0) + 1
        if trade_record["outcome"] == "WIN":
            stats["wins"] = stats.get("wins", 0) + 1
        else:
            stats["losses"] = stats.get("losses", 0) + 1
        total = stats["wins"] + stats["losses"]
        stats["win_rate_pct"] = round((stats["wins"] / total) * 100, 1) if total > 0 else 0.0
        stats["pnl_usd"] = round(stats.get("pnl_usd", 0.0) + trade_record["pnl_usd"], 2)

        try:
            with open(TRADE_HISTORY_FILE, "w") as f:
                json.dump(history_data, f, indent=2)
        except Exception as e:
            print(f"[!] Error writing trade history: {e}")

    @classmethod
    def get_ticker_adjustment(cls, ticker: str) -> float:
        """
        Self-learning: Return a score adjustment (-8 to +8) for a ticker
        based on historical performance.
        """
        data = cls.load_efficiency_data()
        per_ticker = data.get("per_ticker", {})
        
        if ticker not in per_ticker:
            return 0.0  # No history — neutral
        
        tk = per_ticker[ticker]
        wins = tk.get("wins", 0)
        losses = tk.get("losses", 0)
        streak = tk.get("streak", 0)
        total = wins + losses
        
        if total == 0:
            return 0.0
        
        adjustment = 0.0
        
        # Win rate based adjustment
        ticker_wr = wins / total
        if ticker_wr >= 0.75 and total >= 3:
            adjustment += 5.0  # Strong performer
        elif ticker_wr >= 0.60 and total >= 2:
            adjustment += 3.0
        elif ticker_wr <= 0.30 and total >= 3:
            adjustment -= 6.0  # Consistent loser — heavy penalty
        elif ticker_wr <= 0.40 and total >= 2:
            adjustment -= 4.0
        
        # Streak based adjustment
        if streak >= 3:
            adjustment += 3.0  # Hot streak
        elif streak >= 2:
            adjustment += 1.5
        elif streak <= -3:
            adjustment -= 5.0  # Cold streak — strong penalty
        elif streak <= -2:
            adjustment -= 3.0
        
        # Clamp to [-8, +8]
        return max(-8.0, min(8.0, adjustment))

    @classmethod
    def get_factor_performance(cls) -> dict:
        """
        Analyze which factors predicted wins vs losses.
        Returns dict of {factor_name: {"win_avg": float, "loss_avg": float, "edge": float}}
        """
        data = cls.load_efficiency_data()
        history = data.get("history", [])
        
        factor_wins = {}
        factor_losses = {}
        
        for trade in history:
            scores = trade.get("factor_scores", {})
            outcome = trade.get("outcome", "")
            for factor, score in scores.items():
                if outcome == "WIN":
                    factor_wins.setdefault(factor, []).append(score)
                elif outcome == "LOSS":
                    factor_losses.setdefault(factor, []).append(score)
        
        result = {}
        all_factors = set(list(factor_wins.keys()) + list(factor_losses.keys()))
        for factor in all_factors:
            win_scores = factor_wins.get(factor, [])
            loss_scores = factor_losses.get(factor, [])
            win_avg = sum(win_scores) / len(win_scores) if win_scores else 0.0
            loss_avg = sum(loss_scores) / len(loss_scores) if loss_scores else 0.0
            result[factor] = {
                "win_avg": round(win_avg, 2),
                "loss_avg": round(loss_avg, 2),
                "edge": round(win_avg - loss_avg, 2),  # Positive = factor predicts wins
                "total_samples": len(win_scores) + len(loss_scores)
            }
        
        return result
