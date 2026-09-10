"""One plain-language status for the scanner, Telegram and local inspection."""
import json
import os
import tempfile
import time
from collections import Counter

STATUS_FILE = os.path.join(os.path.dirname(__file__), "decision_status.json")


def build_status(model, candidates, sent=0, positions=None):
    counts = Counter()
    shortlist = []
    for c in candidates:
        decision = c.get("dispatch_decision") or {"allowed": False, "reasons": ["awaiting execution checks"]}
        reasons = decision.get("reasons", [])
        counts.update(reasons)
        shortlist.append({"ticker": c["ticker"], "direction": c["direction"],
                          "decision": "SIGNAL SENT" if c.get("signal_sent") else "WAIT",
                          "reason": "; ".join(reasons) or "awaiting dispatch",
                          "probability": c.get("model_prob"),
                          "probability_lower": c.get("calibrated_win_rate"),
                          "expected_net_R": (c.get("kelly") or {}).get("expected_net_R"),
                          "risk_usd": (c.get("kelly") or {}).get("dollars_at_risk", 0)})
    if not model.get("available"):
        explanation = "Collecting verified paper outcomes; there is not enough evidence to trust a win estimate."
    elif not model.get("validated"):
        explanation = "Recent testing did not establish a reliable trading edge after costs."
    elif sent:
        explanation = "A setup passed the evidence, entry and risk checks."
    else:
        explanation = counts.most_common(1)[0][0] if counts else "No setup has completed all entry checks."
    return {"updated_epoch": time.time(), "decision": "SIGNAL SENT" if sent else "WAIT",
            "model_status": "VALIDATED" if model.get("validated") else "LEARNING",
            "reason": explanation, "model_detail": model.get("reason"),
            "verified_outcomes": model.get("n", 0), "signals_sent_this_scan": sent,
            "open_positions": len(positions or []), "blocked_reasons": dict(counts),
            "candidates": shortlist, "validation": model.get("validation"),
            "execution": "paper signals; exchange fills are not connected"}


def save_status(status):
    os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(STATUS_FILE), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(status, f, indent=2, allow_nan=False)
        os.replace(tmp, STATUS_FILE)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def render_status(status=None):
    if status is None:
        try:
            with open(STATUS_FILE) as f:
                status = json.load(f)
        except (OSError, ValueError):
            return "WAIT — scanner has not completed an audited scan yet."
    age = time.time()-status.get("updated_epoch", 0)
    if age > 300:
        return f"WAIT — scanner status is stale ({age/60:.0f} minutes old). Check the scanner process."
    return (f"DEN | {status['decision']}\n"
            f"Model: {status['model_status']} | verified outcomes: {status['verified_outcomes']}\n"
            f"Why: {status['reason']}\n"
            f"Signals this scan: {status['signals_sent_this_scan']} | open: {status['open_positions']}\n"
            "Risk: half-Kelly, at most 1% of configured capital per trade; costs included.\n"
            "WAIT means no trade. Only SIGNAL alerts contain an actionable setup.\n"
            "Results are paper estimates until matched to exchange fills.")


if __name__ == "__main__":
    print(render_status())
