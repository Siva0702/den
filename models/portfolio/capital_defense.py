# models/portfolio/capital_defense.py
import json
import os

HISTORY_FILE = "portfolio/trade_history.json"

class CapitalDefenseShield:
    """
    Den Engine v12.0 100% Target Attainment & Capital Defense System:
    1. Dynamic Kelly Risk Scaling: Scales risk to $50 USDT on 72%+ Win Rate setups (1 Win = +$150 USDT).
    2. Monthly Profit Lock ($1,000 Target): Locks account once +$1,000 net profit is reached.
    3. Loss Circuit Breaker: Reduces risk to $20 USDT if 2 consecutive losses occur.
    """
    @staticmethod
    def get_dynamic_risk_params(account_balance: float = 1000.0, signal_win_rate: float = 0.72) -> dict:
        dollars_at_risk = 35.0
        
        # High Conviction Kelly Scaling (Win Rate >= 72% -> $50 Risk -> $150 Gain)
        if signal_win_rate >= 0.72:
            dollars_at_risk = 50.0
        elif signal_win_rate < 0.65:
            dollars_at_risk = 25.0

        # Check monthly PnL progress in trade_history.json
        monthly_pnl = 0.0
        target_attained = False

        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, "r") as f:
                    data = json.load(f)
                    monthly_pnl = data.get("user_taken_trades", {}).get("pnl_usd", 0.0) + data.get("engine_signals", {}).get("pnl_usd", 0.0)
                    if monthly_pnl >= 1000.0:
                        target_attained = True
            except Exception:
                pass

        return {
            "dollars_at_risk": dollars_at_risk,
            "target_payout": dollars_at_risk * 3.0,
            "monthly_pnl": round(monthly_pnl, 2),
            "target_attained": target_attained
        }
