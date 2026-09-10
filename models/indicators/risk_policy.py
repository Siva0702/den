"""Cost-aware sizing and auditable dispatch eligibility. No network or state I/O."""
import math
from indicators.execution import finite, trade_cost_pct


def kelly_size(win_rate, reward_risk, balance, max_risk_pct=.01, cost_r=0.0,
               win_payoff_r=None, loss_payoff_r=None):
    vals = [finite(x) for x in (win_rate, reward_risk, balance, max_risk_pct, cost_r)]
    veto = {"veto": True, "dollars_at_risk": 0.0, "risk_pct": 0.0,
            "kelly_full": 0.0, "kelly_half": 0.0, "expected_net_R": None}
    if any(x is None for x in vals):
        return dict(veto, veto_reason="missing or nonfinite sizing input")
    p, rr, balance, cap, cost = vals
    if not 0 < p < 1 or rr <= 0 or balance <= 0 or not 0 < cap <= 1 or cost < 0:
        return dict(veto, veto_reason="invalid sizing input")
    win, loss = rr-cost, 1+cost
    if win_payoff_r is not None:
        win = min(win, finite(win_payoff_r, 0))
    if loss_payoff_r is not None:
        loss = max(loss, finite(loss_payoff_r, float("inf")))
    if not all(math.isfinite(x) for x in (win, loss)):
        return dict(veto, veto_reason="invalid observed payoff")
    expectancy = p*win - (1-p)*loss
    if win <= 0 or expectancy <= 0:
        return dict(veto, veto_reason="no positive expectancy after costs", expected_net_R=expectancy)
    b = win/loss
    full = (p*b-(1-p))/b
    fraction = min(.5*full, cap)
    # Round DOWN so cents cannot override a very small Kelly limit.
    dollars = math.floor(balance*fraction*100)/100
    return {"veto": dollars <= 0, "veto_reason": "below minimum precision" if dollars <= 0 else "",
            "kelly_full": full, "kelly_half": .5*full, "dollars_at_risk": dollars,
            "risk_pct": dollars/balance*100, "expected_net_R": expectancy,
            "expected_log_growth": p*math.log1p(fraction*b)+(1-p)*math.log1p(-fraction),
            "win_payoff_r": win, "loss_payoff_r": loss}


def dispatch_eligibility(candidate, positions, balance, score_floor=78):
    reasons = []
    m = candidate.get("model_evidence") or {}
    if not m.get("tradable"):
        reasons.append("model abstained: " + m.get("reason", "no validated model"))
    entry, stop = finite(candidate.get("entry"), 0), finite(candidate.get("sl"), 0)
    direction = candidate.get("direction")
    sign = 1 if direction == "LONG" else -1
    ladder = [finite(t, 0) for t in candidate.get("tp_ladder", [])]
    if (direction not in ("LONG", "SHORT") or entry <= 0 or stop <= 0 or
            (entry-stop)*sign <= 0 or not ladder or any(t <= 0 for t in ladder) or
            any((b-a)*sign <= 0 for a,b in zip([entry]+ladder, ladder))):
        reasons.append("invalid entry, stop or target geometry")
    if finite(candidate.get("model_prob"), 0) < .70:
        reasons.append("win estimate below fixed probability threshold")
    p = finite(candidate.get("calibrated_win_rate"))
    score = finite(candidate.get("total_score"))
    if score is None or score < score_floor:
        reasons.append("score below fixed threshold")
    if p is None or p < .5:
        reasons.append("insufficient probability lower bound")
    k = candidate.get("kelly") or {}
    risk = finite(k.get("dollars_at_risk"), 0)
    if k.get("veto", True) or risk <= 0 or finite(k.get("expected_net_R"), -1) <= 0:
        reasons.append("no positive cost-adjusted Kelly allocation")
    if (candidate.get("news") or {}).get("block_entry"):
        reasons.append("news risk blocks entry")
    if (candidate.get("calendar") or {}).get("available") is False:
        reasons.append("event calendar unavailable or stale")
    if (candidate.get("calendar") or {}).get("blackout"):
        reasons.append("scheduled-event blackout")
    if (candidate.get("event_vol") or {}).get("action") == "NO_ENTRY":
        reasons.append("unresolved event volatility")
    if finite((candidate.get("hunt_risk") or {}).get("hunt_risk_score"), 100) >= 45:
        reasons.append("stop-hunt exposure")
    if finite(candidate.get("rr"), 0) < 1.2:
        reasons.append("insufficient reward/risk")
    if not candidate.get("quote_fresh", False):
        reasons.append("fresh execution quote unavailable")
    if balance <= 0:
        reasons.append("invalid account balance")
    else:
        used_risk, used_margin = 0.0, 0.0
        for pos in positions:
            entry, stop = finite(pos.get("entry_price"), 0), finite(pos.get("stop_loss"), 0)
            margin, lev = finite(pos.get("margin"), 0), finite(pos.get("leverage"), 0)
            if not entry or not stop or margin <= 0 or lev <= 0:
                reasons.append("open position has unknown exposure")
                continue
            used_risk += max(finite(pos.get("planned_risk_usd"), 0),
                             margin*lev*(abs(entry-stop)/entry + trade_cost_pct(entry, stop)/100))
            used_margin += margin
        if used_risk + risk > balance*.10:
            reasons.append("portfolio risk budget exceeded")
        if used_margin + finite(candidate.get("final_margin"), balance) > balance*.50:
            reasons.append("portfolio margin budget exceeded")
    return {"allowed": not reasons, "reasons": reasons}


def candidate_rank(candidate):
    """Prefer conservative expected account growth, rather than win rate alone."""
    k = candidate.get("kelly") or {}
    return (finite(k.get("expected_log_growth"), -1), finite(k.get("expected_net_R"), -1),
            finite(candidate.get("model_prob"), -1), finite(candidate.get("total_score"), 0))
