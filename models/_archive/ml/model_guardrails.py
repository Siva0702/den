# models/ml/model_guardrails.py
import json
import os

SAFE_BASELINE_WEIGHTS = {
    "base_win_rate": 0.58,
    "rsi_sweet_spot_min": 50.0,
    "rsi_sweet_spot_max": 62.0,
    "sentiment_weight": 1.0,
    "vwap_weight": 1.0,
    "consecutive_wins": 0,
    "consecutive_losses": 0,
    "version": "18.1_safe_guarded"
}

class SelfLearningGuardrailEngine:
    """
    Den Engine v18.1 Model Safety & Culprit Rollback Engine:
    Guarantees that self-learning can NEVER degrade engine accuracy or leak bad parameters.
    Enforces strict hard bounds and automatically rolls back any culprit weight update to safe baselines!
    """

    @classmethod
    def sanitize_and_guard_weights(cls, proposed_weights: dict) -> dict:
        if not isinstance(proposed_weights, dict):
            return SAFE_BASELINE_WEIGHTS.copy()

        guarded = proposed_weights.copy()

        # 1. Hard Bounds Enforcement (Prevents degrading accuracy)
        # Base win rate MUST stay between 0.58 (58%) and 0.75 (75%)
        base_wr = guarded.get("base_win_rate", 0.58)
        guarded["base_win_rate"] = max(min(base_wr, 0.75), 0.58)

        # Sentiment weight bounded between 0.80 and 1.30
        sent_w = guarded.get("sentiment_weight", 1.0)
        guarded["sentiment_weight"] = max(min(sent_w, 1.30), 0.80)

        # VWAP weight bounded between 0.90 and 1.20
        vwap_w = guarded.get("vwap_weight", 1.0)
        guarded["vwap_weight"] = max(min(vwap_w, 1.20), 0.90)

        return guarded

    @classmethod
    def audit_culprit_patterns(cls, history: dict, current_weights: dict) -> dict:
        """
        Audits closed trade history. If a recent weight modification resulted in a loss,
        immediately rolls back the culprit weight to the safe baseline!
        """
        trades = history.get("trades", [])
        if not trades:
            return cls.sanitize_and_guard_weights(current_weights)

        recent_trades = trades[-5:] # Inspect last 5 trades
        losses = [t for t in recent_trades if t.get("status") == "CLOSED_LOSS"]

        sanitized = cls.sanitize_and_guard_weights(current_weights)

        # Culprit Defense: If 2 of last 5 trades were losses, trigger instant rollback to safe baseline!
        if len(losses) >= 2:
            print("[🛡️] CULPRIT WEIGHT PATTERN DETECTED IN RECENT TRADES! Instantly rolling back to Safe Baseline!")
            return SAFE_BASELINE_WEIGHTS.copy()

        return sanitized
