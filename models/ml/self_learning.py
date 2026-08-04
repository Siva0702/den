# models/ml/self_learning.py
import json
import os
from ml.model_guardrails import SelfLearningGuardrailEngine, SAFE_BASELINE_WEIGHTS

HISTORY_FILE = "portfolio/trade_history.json"
WEIGHTS_FILE = "models/ml/learned_weights.json"

class SelfLearningQuantEngine:
    """
    Den Engine Autonomous Self-Learning Reinforcement Module:
    Learns strictly to IMPROVE accuracy.
    Includes hard-coded Safety Guardrails and Culprit Rollback to prevent any degradation!
    """
    @classmethod
    def get_learned_weights(cls) -> dict:
        if not os.path.exists("models/ml"):
            os.makedirs("models/ml")

        if not os.path.exists(WEIGHTS_FILE):
            try:
                with open(WEIGHTS_FILE, "w") as f:
                    json.dump(SAFE_BASELINE_WEIGHTS, f, indent=2)
            except Exception:
                pass
            return SAFE_BASELINE_WEIGHTS.copy()

        try:
            with open(WEIGHTS_FILE, "r") as f:
                loaded = json.load(f)
                return SelfLearningGuardrailEngine.sanitize_and_guard_weights(loaded)
        except Exception:
            return SAFE_BASELINE_WEIGHTS.copy()

    @classmethod
    def train_and_update(cls) -> dict:
        """
        Executes during continuous scan loops.
        Analyzes winning vs losing trade characteristics and auto-tunes parameters.
        ALWAYS enforces safety guardrails and culprit rollback!
        """
        weights = cls.get_learned_weights()

        if not os.path.exists(HISTORY_FILE):
            return weights

        try:
            with open(HISTORY_FILE, "r") as f:
                history = json.load(f)
            
            # Check for culprit patterns and perform automatic rollback if needed
            weights = SelfLearningGuardrailEngine.audit_culprit_patterns(history, weights)

            closed_trades = [t for t in history.get("trades", []) if t.get("status") in ["CLOSED_WIN", "CLOSED_LOSS"]]
            if len(closed_trades) >= 3:
                wins = [t for t in closed_trades if t["status"] == "CLOSED_WIN"]
                win_rate = len(wins) / len(closed_trades)

                if win_rate >= 0.70:
                    weights["base_win_rate"] = min(weights["base_win_rate"] + 0.01, 0.65)
                    weights["sentiment_weight"] = round(weights["sentiment_weight"] * 1.02, 2)
                elif win_rate < 0.60:
                    # Instantly roll back to baseline if win rate drops below 60%
                    print("[🛡️] Win Rate dropped below 60%! Activating Culprit Rollback to Safe Baseline!")
                    weights = SAFE_BASELINE_WEIGHTS.copy()

            # Final Guardrail Sanitization before saving
            weights = SelfLearningGuardrailEngine.sanitize_and_guard_weights(weights)

            with open(WEIGHTS_FILE, "w") as f:
                json.dump(weights, f, indent=2)
                
        except Exception as e:
            print(f"[!] Self-learning training error: {e}")
            weights = SAFE_BASELINE_WEIGHTS.copy()

        return weights
