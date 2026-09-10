# models/audit/score_model.py
import json
import os
import math
import tempfile
import threading
import time

import numpy as np
from indicators.execution import finite, trade_cost_pct, FEATURE_VERSION
from audit import scoring_context

MODEL_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "audit/score_model.json")


class CalibratedScoreModel:
    """Small probability model with purged chronological train/calibration/test blocks.

    A fitted model may score shadow candidates; only independent net-profit evidence
    can approve dispatch. The score maps probability to a fixed display scale and is
    never itself a win rate. Validation must be repeated prospectively after changes.
    """

    MODEL_VERSION = "v3-context-purged-net-outcomes"
    MIN_TRAIN = 100
    MIN_CALIBRATION = 40
    MIN_TEST = 40
    MIN_TAIL = 30
    MIN_HISTORY_DAYS = 7
    MAX_AGE_SECONDS = 7 * 86400
    MIN_SAMPLES = 200
    MODEL_SHADOW_FLOOR = 40.0
    L2 = 1.0
    LR = 0.08
    EPOCHS = 900
    REFRESH_SECONDS = 900.0
    # Fixed policy anchor, not a claim of a proven win rate or signal quota.
    P78 = 0.70

    _cache = {"built": 0.0, "model": None}
    _lock = threading.Lock()

    # ---------------- feature extraction ----------------
    NUM = ["pillar_trend", "pillar_htf", "pillar_orderflow", "pillar_structure",
           "pillar_defense", "atr_percentile", "rsi", "btc_correlation",
           "hunt_risk_score", "regulatory_multiplier", "event_risk_score"]
    CAT = {
        "ema_bias": ["Bullish", "Bearish", "Neutral"],
        "htf_bias": ["Bullish", "Bearish", "Mixed"],
        "bos": ["BULLISH", "BEARISH", "FAILED_HIGH", "FAILED_LOW", "NONE"],
        "market_regime": ["TRENDING", "RANGING", "CHOPPY", "VOLATILE"],
        "news_bias": ["BULLISH", "BEARISH", "NONE"],
        "session": ["ASIAN", "LONDON_OPEN", "LONDON", "NY_OVERLAP", "NY", "DEAD_ZONE"],
    }

    @classmethod
    def feature_names(cls) -> list:
        names = list(cls.NUM) + ["reward_risk", "sl_pct", "timeframe_alignment",
                                 "is_short", "sweep_reclaimed", "news_blocked"]
        for k, vals in cls.CAT.items():
            names += [f"{k}={v}" for v in vals]
        # interaction: the condition that broke the original score
        names += ["bearish_bos_x_short", "bullish_bos_x_long"]
        return names + scoring_context.names()

    @classmethod
    def vectorise(cls, feats: dict, direction: str, tf_align=0) -> np.ndarray:
        f = feats or {}
        v = []
        for k in cls.NUM:
            try:
                v.append(float(f.get(k) if f.get(k) is not None else 0.0))
            except (TypeError, ValueError):
                v.append(0.0)
        for k, default in (("reward_risk", 1.0), ("sl_pct", 0.01)):
            try:
                v.append(float(f.get(k) if f.get(k) is not None else default))
            except (TypeError, ValueError):
                v.append(default)
        v.append(float(tf_align or 0))
        is_short = 1.0 if str(direction).upper() == "SHORT" else 0.0
        v.append(is_short)
        v.append(1.0 if f.get("sweep_reclaimed") else 0.0)
        v.append(1.0 if f.get("news_blocked") else 0.0)
        for k, vals in cls.CAT.items():
            cur = str(f.get(k) if f.get(k) is not None else "")
            for val in vals:
                v.append(1.0 if cur.upper() == val.upper() else 0.0)
        bos = str(f.get("bos", "")).upper()
        v.append(1.0 if (bos == "BEARISH" and is_short) else 0.0)
        v.append(1.0 if (bos == "BULLISH" and not is_short) else 0.0)
        v += scoring_context.vector(f, str(direction))
        return np.nan_to_num(np.asarray(v, dtype=float), nan=0.0, posinf=0.0, neginf=0.0)

    @classmethod
    def _matrix(cls, rows):
        X = np.vstack([cls.vectorise(r.get("features"), r.get("direction"),
                                     r.get("timeframe_alignment", 0)) for r in rows])
        y = np.asarray([1.0 if cls.net_return(r) > 0 else 0.0 for r in rows])
        return X, y

    # ---------------- training ----------------
    @staticmethod
    def _sigmoid(z):
        return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))

    @classmethod
    def _fit_logistic(cls, X, y, mu=None, sd=None):
        if mu is None:
            mu, sd = X.mean(axis=0), X.std(axis=0)
        sd = np.where(sd < 1e-9, 1.0, sd)
        Z = (X - mu) / sd
        n, d = Z.shape
        w = np.zeros(d)
        b = 0.0
        for _ in range(cls.EPOCHS):
            p = cls._sigmoid(Z @ w + b)
            g = p - y
            w -= cls.LR * ((Z.T @ g) / n + cls.L2 * w / n)
            b -= cls.LR * g.mean()
        return {"w": w, "b": b, "mu": mu, "sd": sd}

    @classmethod
    def _predict(cls, m, X):
        Z = (X - m["mu"]) / m["sd"]
        return cls._sigmoid(Z @ m["w"] + m["b"])

    # ---------------- calibration ----------------
    @staticmethod
    def _reliability(p, y, edges=(0.0, .3, .4, .5, .6, .7, 1.01)):
        out = []
        for lo, hi in zip(edges[:-1], edges[1:]):
            m = (p >= lo) & (p < hi)
            if m.sum() == 0:
                continue
            out.append({"bin": f"{lo:.2f}-{hi:.2f}", "n": int(m.sum()),
                        "predicted": round(float(p[m].mean()), 3),
                        "actual": round(float(y[m].mean()), 3)})
        return out

    @classmethod
    def _platt(cls, p, y):
        """1-D logistic on the raw score — corrects systematic over/under-confidence."""
        z = np.log(np.clip(p, 1e-6, 1 - 1e-6) / (1 - np.clip(p, 1e-6, 1 - 1e-6)))
        X = z.reshape(-1, 1)
        return cls._fit_logistic(X, y)

    @classmethod
    def _apply_platt(cls, cal, p):
        z = np.log(np.clip(p, 1e-6, 1 - 1e-6) / (1 - np.clip(p, 1e-6, 1 - 1e-6)))
        return cls._predict(cal, z.reshape(-1, 1))

    # ---------------- score mapping ----------------
    @classmethod
    def prob_to_score(cls, prob: float) -> float:
        """
        Anchored piecewise-linear map. P78 -> 78 by construction, so the dispatch gate
        stops being an arbitrary number and starts being a measured expectancy.
        """
        p = float(max(0.0, min(1.0, prob)))
        if p <= cls.P78:
            return round(78.0 * (p / cls.P78), 2) if cls.P78 > 0 else 0.0
        return round(78.0 + 22.0 * ((p - cls.P78) / max(1.0 - cls.P78, 1e-9)), 2)

    @staticmethod
    def net_return(row):
        if finite(row.get("net_pnl_pct")) is not None:
            return float(row["net_pnl_pct"])
        entry = finite(row.get("entry"), 0)
        exit_price = finite(row.get("exit_price"), entry)
        if entry <= 0:
            return 0.0
        hold = max(0, (float(row["closed_epoch"])-float(row["opened_epoch"]))/3600)
        return float(row.get("pnl_pct", 0)) - trade_cost_pct(entry, exit_price, hold, row.get("funding_rate", 0))

    @classmethod
    def clean_rows(cls, rows):
        valid, seen = [], set()
        for r in sorted(rows, key=lambda r: finite(r.get("opened_epoch"), 0)):
            op, cl = finite(r.get("opened_epoch"), 0), finite(r.get("closed_epoch"), 0)
            e, sl = finite(r.get("entry"), 0), finite(r.get("stop_loss"), 0)
            key = r.get("shadow_id") or (r.get("ticker"), r.get("direction"), op)
            if (key in seen or not r.get("features") or r.get("still_running") or
                    r.get("data_quality_error") or r.get("source") == "backtest" or
                    r.get("outcome") in ("TIMEOUT_NO_DATA", "STILL_OPEN") or
                    op <= 0 or cl <= op or cl > time.time() or e <= 0 or sl <= 0 or e == sl or
                    r.get("direction") not in ("LONG", "SHORT") or
                    finite(r.get("pnl_pct")) is None):
                continue
            feats = r.get("features")
            if not isinstance(feats, dict) or any(feats.get(k) is not None and finite(feats.get(k)) is None
                    for k in cls.NUM + scoring_context.NUMERIC + ["sl_pct", "reward_risk"]):
                continue
            ladder = r.get("tp_ladder")
            if ladder:
                sign = 1 if r["direction"] == "LONG" else -1
                levels = [finite(x) for x in ladder]
                if any(x is None or x <= 0 for x in levels) or any(
                        (b-a)*sign <= 0 for a,b in zip([e]+levels, levels)):
                    continue
            if (e-sl)*(1 if r["direction"] == "LONG" else -1) <= 0:
                continue
            seen.add(key)
            valid.append(r)
        return valid

    @classmethod
    def chronological_split(cls, rows):
        n = len(rows)
        a, b = int(n * .6), int(n * .8)
        if not a or b >= n:
            return [], [], []
        cal_start = float(rows[a]["opened_epoch"])
        test_start = float(rows[b]["opened_epoch"])
        train = [r for r in rows[:a] if float(r["closed_epoch"]) < cal_start
                 and float(r["opened_epoch"]) < cal_start]
        cal = [r for r in rows[a:b] if float(r["closed_epoch"]) < test_start
               and float(r["opened_epoch"]) < test_start]
        return train, cal, rows[b:]

    @staticmethod
    def _wilson(wins, n, z=1.644854):
        if not n:
            return 0.0
        p = wins / n
        return (p + z*z/(2*n) - z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))) / (1+z*z/n)

    @staticmethod
    def _metrics(p, y):
        p = np.clip(p, 1e-6, 1-1e-6)
        return {"brier": float(np.mean((p-y)**2)),
                "log_loss": float(-np.mean(y*np.log(p) + (1-y)*np.log(1-p)))}

    @classmethod
    def build(cls, rows=None, force=False, persist=True):
        now = time.time()
        live = rows is None
        if live:
            with cls._lock:
                if not force and cls._cache["model"] and now-cls._cache["built"] < cls.REFRESH_SECONDS:
                    return cls._cache["model"]
            from audit.shadow_ledger import ShadowTradeLedger
            rows = [r for r in ShadowTradeLedger.current_version_records()
                    if (r.get("features") or {}).get("scoring_context_version") == scoring_context.CONTEXT_VERSION]
        rows = cls.clean_rows(rows)
        train, cal_rows, test = cls.chronological_split(rows)
        model = {"available": False, "validated": False, "n": len(rows),
                 "version": cls.MODEL_VERSION,
                 "split": {"train": len(train), "calibration": len(cal_rows), "test": len(test),
                           "purged": len(rows)-len(train)-len(cal_rows)-len(test)}}
        if len(rows) < cls.MIN_SAMPLES or len(train) < cls.MIN_TRAIN or len(cal_rows) < cls.MIN_CALIBRATION or len(test) < cls.MIN_TEST:
            model["reason"] = "insufficient independent chronological train/calibration/test data"
        else:
            X, y = cls._matrix(train)
            Xc, yc = cls._matrix(cal_rows)
            Xt, yt = cls._matrix(test)
            if len(set(y)) < 2 or len(set(yc)) < 2:
                model["reason"] = "training and calibration each require wins and losses"
            else:
                base = cls._fit_logistic(X, y)
                # Calibrator sees predictions on trades the base model has NEVER seen.
                cal = cls._platt(cls._predict(base, Xc), yc)
                pc = cls._apply_platt(cal, cls._predict(base, Xc))
                pt = cls._apply_platt(cal, cls._predict(base, Xt))
                metrics = cls._metrics(pt, yt)
                baseline = cls._metrics(np.full(len(yt), float(y.mean())), yt)
                mask = pt >= cls.P78
                tail = [r for r, keep in zip(test, mask) if keep]
                daily = {}
                for r in tail:
                    day = int(float(r["opened_epoch"]) // 86400)
                    sl_pct = abs(float(r["entry"])-float(r["stop_loss"]))/float(r["entry"])*100
                    daily.setdefault(day, []).append(cls.net_return(r)/sl_pct)
                day_means = np.asarray([np.mean(rs) for rs in daily.values()])
                lower_r = float(day_means.mean()-2*day_means.std(ddof=1)/math.sqrt(len(day_means))) if len(day_means) >= 3 else None
                failures = []
                if metrics["brier"] >= baseline["brier"] or metrics["log_loss"] >= baseline["log_loss"]:
                    failures.append("test probability error does not beat the constant base-rate forecast")
                if len(tail) < cls.MIN_TAIL:
                    failures.append("too few untouched test trades above the fixed probability threshold")
                if lower_r is None or lower_r <= 0:
                    failures.append("test tail has no positive daily-block net expectancy lower bound")
                if (float(rows[-1]["opened_epoch"])-float(rows[0]["opened_epoch"]))/86400 < cls.MIN_HISTORY_DAYS:
                    failures.append("less than seven days of history")
                if now-max(float(r["closed_epoch"]) for r in rows) > cls.MAX_AGE_SECONDS:
                    failures.append("outcome evidence is stale")
                from indicators.execution import LOGIC_VERSION
                if any(r.get("logic_version") != LOGIC_VERSION for r in rows):
                    failures.append("legacy execution labels require verified replay")
                if any((r.get("features") or {}).get("scoring_context_version") != scoring_context.CONTEXT_VERSION for r in rows):
                    failures.append("legacy context snapshots require prospective collection")
                if any((r.get("features") or {}).get("feature_version") != FEATURE_VERSION for r in rows):
                    failures.append("legacy feature snapshots are not comparable to current features")
                support = []
                # Use calibration blocks, not a test-tuned cutoff, for local uncertainty.
                for lo, hi in zip([0, .4, .5, .6, .7, .8, .9], [.4, .5, .6, .7, .8, .9, 1.01]):
                    m = (pc >= lo) & (pc < hi)
                    matched = [r for r, keep in zip(cal_rows, m) if keep]
                    # Correlated setups in the same hour do not count as independent trials.
                    effective_n = len({int(float(r["opened_epoch"])//3600) for r in matched})
                    rs = [cls.net_return(r)/(abs(float(r["entry"])-float(r["stop_loss"]))/float(r["entry"])*100)
                          for r in matched]
                    wins_r = [r for r in rs if r > 0]
                    losses_r = [-r for r in rs if r <= 0]
                    support.append({"lo": lo, "hi": hi, "n": int(m.sum()), "effective_n": effective_n,
                                    "lower": cls._wilson(float(yc[m].mean())*effective_n, effective_n) if m.any() else 0,
                                    "win_payoff_r": float(np.quantile(wins_r, .1)) if wins_r else 0,
                                    "loss_payoff_r": max(losses_r) if losses_r else 1.0})

                model.update(available=True, validated=not failures, reason="; ".join(failures) or "passed chronological validation",
                             built_at=time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now)),
                             base_rate=float(y.mean()), P78=cls.P78, feature_names=cls.feature_names(),
                             validation={"model": metrics, "baseline": baseline,
                                         "tail_n": len(tail), "tail_days": len(daily), "tail_net_R_lower": lower_r,
                                         "reliability": cls._reliability(pt, yt), "failures": failures},
                             support=support,
                             evidence_end=max(float(r["closed_epoch"]) for r in rows))
                for prefix, fitted in (("", base), ("cal_", cal)):
                    model.update({prefix+k: v.tolist() if isinstance(v, np.ndarray) else float(v)
                                  for k, v in fitted.items()})
        if live:
            with cls._lock:
                cls._cache = {"built": now, "model": model}
            if persist:
                os.makedirs(os.path.dirname(MODEL_FILE), exist_ok=True)
                fd, tmp = tempfile.mkstemp(dir=os.path.dirname(MODEL_FILE), suffix=".tmp")
                try:
                    with os.fdopen(fd, "w") as f:
                        json.dump(model, f, indent=2, allow_nan=False)
                    os.replace(tmp, MODEL_FILE)
                finally:
                    if os.path.exists(tmp):
                        os.unlink(tmp)
        return model

    @classmethod
    def score(cls, feats, direction, tf_align=0):
        m = cls.build()
        if not m.get("available"):
            return {"available": False, "tradable": False, "reason": m.get("reason"),
                    "score": None, "prob": None, "prob_lower": None, "version": cls.MODEL_VERSION}
        if m.get("feature_names") != cls.feature_names() or m.get("version") != cls.MODEL_VERSION:
            return {"available": False, "tradable": False, "reason": "model feature schema mismatch",
                    "score": None, "prob": None, "prob_lower": None, "version": cls.MODEL_VERSION}
        base = {k: np.asarray(m[k]) for k in ("w", "b", "mu", "sd")}
        cal = {k: np.asarray(m["cal_"+k]) for k in ("w", "b", "mu", "sd")}
        x = cls.vectorise(feats, direction, tf_align).reshape(1, -1)
        p = float(cls._apply_platt(cal, cls._predict(base, x))[0])
        support = next((b for b in m["support"] if b["lo"] <= p < b["hi"]), {})
        lower = min(p, support.get("lower", 0))
        # Missing/nonfinite numeric features and far-outside-training observations abstain.
        bad_input = any(feats.get(k) is not None and finite(feats.get(k)) is None for k in cls.NUM + scoring_context.NUMERIC + ["sl_pct", "reward_risk"])
        missing = feats.get("scoring_context_version") != scoring_context.CONTEXT_VERSION or feats.get("feature_version") != FEATURE_VERSION or any(finite(feats.get(k)) is None for k in cls.NUM[:5] + ["rsi", "atr_percentile", "sl_pct", "reward_risk"])
        ood = bool(np.any(np.abs((x-base["mu"])/base["sd"]) > 8))
        ready = (m.get("validated", False) and support.get("effective_n", 0) >= 20 and not bad_input and not missing and not ood
                 and time.time()-m["evidence_end"] <= cls.MAX_AGE_SECONDS)
        reason = m["reason"] if not m.get("validated") else ("outside model support" if not ready else "validated")
        return {"available": True, "tradable": ready, "reason": reason, "prob": p,
                "prob_lower": lower, "score": cls.prob_to_score(p), "samples": support.get("n", 0),
                "version": cls.MODEL_VERSION, "validation": m["validation"],
                "effective_samples": support.get("effective_n", 0),
                "win_payoff_r": support.get("win_payoff_r"), "loss_payoff_r": support.get("loss_payoff_r")}
