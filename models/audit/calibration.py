# models/audit/calibration.py
import json
import math
import os
import re
import statistics
import time

from audit.shadow_ledger import ShadowTradeLedger

class WinRateCalibrator:
    """
    Den Engine v39.0 Empirical Win-Rate Calibration.

    This replaces the single most damaging line in the old engine:

        win_rate = total_score / 100.0

    That was never a probability. It was the score with a percent sign on it, which is
    why the bot could report "81% win rate" on a setup it had no evidence about, and why
    the number could collapse to 42 minutes later. Kelly sizing was being fed that
    fiction, so position sizes were built on a number that meant nothing.

    What replaces it: every score bin gets its win rate measured from resolved shadow
    trades, then reported as a WILSON SCORE LOWER BOUND at 90% confidence. That has two
    properties that matter here:

      - 3 wins out of 3 does NOT read as 100%. It reads as 64.6%. Small samples are
        automatically distrusted instead of being trusted most.
      - The estimate rises only as evidence accumulates, so the engine gets more
        confident the more it has actually proven.

    Until MIN_SAMPLES_GLOBAL resolved trades exist the calibrator reports
    status=UNCALIBRATED and returns a deliberately conservative fallback. The engine
    must not pretend to know its edge before it has measured it.
    """

    Z_90 = 1.2816
    Z_95 = 1.9600

    MIN_SAMPLES_GLOBAL = 40     # below this, nothing is trusted
    MIN_SAMPLES_BIN = 12        # below this, a bin borrows from the global rate
    BIN_WIDTH = 10.0            # 5-pt bins spread 60 samples across 11 buckets, none usable
    PRIOR_STRENGTH = 15.0       # pseudo-observations pulling a thin bin toward global

    # FIX 4 — backtest setups are sampled every STEP bars but held for up to
    # MAX_HOLD bars, so consecutive setups on one asset share most of their outcome
    # window. They are NOT independent observations. Every Wilson bound and z-test
    # computed on the raw count was therefore overstated. Counts from the backtest
    # pool are deflated by this factor before any inference.
    BACKTEST_OVERLAP_FACTOR = 12.0     # MAX_HOLD_BARS(96) / STEP(8)

    _cache = {"epoch": 0.0, "model": None}
    CACHE_TTL = 120.0

    # ------------------------------------------------------------------
    @classmethod
    def wilson_lower_bound(cls, wins: int, n: int, z: float = None) -> float:
        """Lower bound of the confidence interval on a binomial proportion."""
        if n <= 0:
            return 0.0
        z = z if z is not None else cls.Z_90
        p = wins / n
        denom = 1.0 + (z * z) / n
        centre = p + (z * z) / (2.0 * n)
        margin = z * math.sqrt((p * (1.0 - p) + (z * z) / (4.0 * n)) / n)
        return max(0.0, (centre - margin) / denom)

    @classmethod
    def effective_n(cls, records: list) -> float:
        """Independent-observation count after discounting overlapping backtest samples."""
        live = sum(1 for r in records if r.get("source") != "backtest")
        bt = sum(1 for r in records if r.get("source") == "backtest")
        return live + bt / cls.BACKTEST_OVERLAP_FACTOR

    @staticmethod
    def _bh_fdr(pvals: list, alpha: float = 0.10) -> set:
        """
        Benjamini-Hochberg false-discovery-rate control.

        Testing ~19 factors at 90% confidence yields roughly two false positives by
        chance alone, which is very likely what the +6.7pp and +4.8pp "findings" were.
        BH keeps the expected proportion of false discoveries under alpha across the
        whole family of tests instead of controlling each one in isolation.
        """
        if not pvals:
            return set()
        indexed = sorted(enumerate(pvals), key=lambda kv: kv[1])
        m = len(indexed)
        keep = set()
        for rank, (idx, p) in enumerate(indexed, start=1):
            if p <= (rank / m) * alpha:
                keep = {i for i, _ in indexed[:rank]}
        return keep

    @staticmethod
    def _z_to_p(z: float) -> float:
        """Two-sided p-value from a z-score."""
        return math.erfc(abs(z) / math.sqrt(2.0))

    @staticmethod
    def _bin_key(score: float, width: float) -> int:
        return int(score // width) * int(width)

    @staticmethod
    def _isotonic(values: list, weights: list) -> list:
        """
        Weighted pool-adjacent-violators. Returns the closest non-decreasing sequence
        to `values`, with bins weighted by sample count so a 30-sample bin pulls harder
        than a 3-sample one.
        """
        if not values:
            return []
        blocks = [[v, w, 1] for v, w in zip(values, weights)]   # [mean, weight, size]
        i = 0
        while i < len(blocks) - 1:
            if blocks[i][0] <= blocks[i + 1][0] + 1e-12:
                i += 1
                continue
            # Violation: merge the two blocks into their weighted mean, then back up.
            v1, w1, s1 = blocks[i]
            v2, w2, s2 = blocks[i + 1]
            tw = w1 + w2
            merged = [(v1 * w1 + v2 * w2) / tw if tw else (v1 + v2) / 2, tw, s1 + s2]
            blocks[i:i + 2] = [merged]
            i = max(i - 1, 0)

        out = []
        for mean, _, size in blocks:
            out.extend([mean] * size)
        return out

    # ------------------------------------------------------------------
    @classmethod
    def build_model(cls, force: bool = False) -> dict:
        now = time.time()
        if not force and cls._cache["model"] and (now - cls._cache["epoch"]) < cls.CACHE_TTL:
            return cls._cache["model"]

        # Two evidence pools, deliberately kept distinguishable:
        #   live shadow trades  — full context (derivatives, news, calendar)
        #   backtested setups   — technicals only, no historical derivatives/news feed
        # Backtest records are down-weighted rather than excluded: they are real price
        # outcomes on real setups, but scored on less information than the live engine
        # sees, so treating them as equivalent would overstate confidence.
        # Only records resolved under the CURRENT logic version. Mixing versions is
        # how a 75.7% accuracy and a 30.3% accuracy got averaged into one number.
        live = ShadowTradeLedger.current_version_records()
        backtest = []
        try:
            from audit.backtester import WalkForwardBacktester
            backtest = WalkForwardBacktester.load()
        except Exception:
            backtest = []

        for r in live:
            r.setdefault("source", "live")
        for r in backtest:
            r.setdefault("source", "backtest")

        # FIX 1 — the two pools are no longer silently merged. They are scored
        # separately and reported separately. Previously `closed = live + backtest`
        # blended 4 live records into 6074 backtest records and quoted the result as a
        # "measured" win rate, which passed off a technicals-only backtest as if it
        # carried live derivatives/news/calendar context.
        closed = live + backtest
        total = len(closed)
        wins = sum(1 for t in closed if t.get("is_win"))
        global_rate = (wins / total) if total else 0.0

        model = {
            "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "total_samples": total,
            "global_win_rate": round(global_rate, 4),
            "global_wilson": round(cls.wilson_lower_bound(wins, total), 4),
            "status": "CALIBRATED" if total >= cls.MIN_SAMPLES_GLOBAL else "UNCALIBRATED",
            "score_bins": {},
            "regime_rates": {},
            "session_rates": {},
            "factor_lift": {},
            "sl_stats": {},
            "pools": {
                "live": {"n": len(live),
                         "wins": sum(1 for t in live if t.get("is_win")),
                         "win_rate": round(sum(1 for t in live if t.get("is_win")) / len(live), 4) if live else None,
                         "context": "full (derivatives + news + calendar)"},
                "backtest": {"n": len(backtest),
                             "wins": sum(1 for t in backtest if t.get("is_win")),
                             "win_rate": round(sum(1 for t in backtest if t.get("is_win")) / len(backtest), 4) if backtest else None,
                             "context": "technicals only — no historical derivatives/news"},
            },
            "primary_source": "live" if len(live) >= cls.MIN_SAMPLES_GLOBAL else "backtest",
        }

        if total == 0:
            cls._cache = {"epoch": now, "model": model}
            return model

        # ---- Score bins -------------------------------------------------
        bins = {}
        for t in closed:
            k = cls._bin_key(float(t.get("raw_score", 0.0)), cls.BIN_WIDTH)
            b = bins.setdefault(k, {"n": 0, "w": 0})
            b["n"] += 1
            b["w"] += 1 if t.get("is_win") else 0

        ordered = sorted(bins.items())
        shrunk_rates = []
        wilson_raw = []
        weights = []
        for k, b in ordered:
            n, w = b["n"], b["w"]
            alpha = w + global_rate * cls.PRIOR_STRENGTH
            beta = (n - w) + (1 - global_rate) * cls.PRIOR_STRENGTH
            shrunk_rates.append(alpha / (alpha + beta))
            wilson_raw.append(cls.wilson_lower_bound(w, n))
            weights.append(n)

        # MONOTONIC SMOOTHING (pool-adjacent-violators).
        # A higher score must never imply a lower win rate. Raw bins violate that
        # constantly on small samples — one run showed 55-60 at 75% and 60-65 at 40%,
        # which is noise, not a real inversion, and would have the engine preferring
        # weaker setups. Isotonic regression enforces the ordering we actually believe
        # while staying as close as possible to the observed data.
        iso = cls._isotonic(shrunk_rates, weights)
        # Smooth the Wilson sequence itself. Taking min(wilson, isotonic) would undo the
        # ordering that was just imposed, because the per-bin Wilson bounds are not
        # themselves monotonic. Running PAVA over the bounds keeps BOTH properties:
        # sample-size honesty and "higher score never scores worse".
        iso_wilson = cls._isotonic(wilson_raw, weights)

        for idx, (k, b) in enumerate(ordered):
            n, w = b["n"], b["w"]
            model["score_bins"][str(k)] = {
                "range": f"{k}-{k + int(cls.BIN_WIDTH)}",
                "n": n,
                "wins": w,
                "raw_rate": round(w / n, 4),
                "shrunk_rate": round(shrunk_rates[idx], 4),
                "isotonic_rate": round(iso[idx], 4),
                "wilson_raw": round(wilson_raw[idx], 4),
                "wilson_lb": round(iso_wilson[idx], 4),
                "reliable": n >= cls.MIN_SAMPLES_BIN,
            }

        # ---- Conditional rates -----------------------------------------
        for field, out in (("market_regime", "regime_rates"), ("session", "session_rates")):
            groups = {}
            for t in closed:
                key = t.get(field, "UNKNOWN")
                g = groups.setdefault(key, {"n": 0, "w": 0})
                g["n"] += 1
                g["w"] += 1 if t.get("is_win") else 0
            for key, g in groups.items():
                model[out][key] = {
                    "n": g["n"], "wins": g["w"],
                    "raw_rate": round(g["w"] / g["n"], 4),
                    "wilson_lb": round(cls.wilson_lower_bound(g["w"], g["n"]), 4),
                }

        # ---- Factor lift: the "why did it win/lose" layer ---------------
        # FIX 3 — hold out the most recent 30% by time for out-of-sample validation.
        timed = sorted(closed, key=lambda t: float(t.get("opened_epoch", 0) or 0))
        split = int(len(timed) * 0.70)
        train, holdout = timed[:split], timed[split:]
        model["factor_lift"] = cls._factor_lift(train, global_rate,
                                                holdout=holdout, apply_fdr=True)
        model["validation"] = {
            "train_n": len(train), "holdout_n": len(holdout),
            "train_effective_n": round(cls.effective_n(train), 1),
            "holdout_effective_n": round(cls.effective_n(holdout), 1),
        }

        # ---- Stop-loss statistics: data-driven SL placement -------------
        model["sl_stats"] = cls._sl_stats(closed)

        cls._cache = {"epoch": now, "model": model}
        return model

    # ------------------------------------------------------------------
    @classmethod
    def _factor_lift(cls, closed: list, global_rate: float,
                     holdout: list = None, apply_fdr: bool = False) -> dict:
        """
        For every factor the engine can observe, measure the win rate WITH the factor
        present versus ABSENT. The difference is that factor's lift. Factors with
        negative lift are actively costing money and should lose weight.
        """
        counts = {}

        def normalise(name: str) -> str:
            """
            Strip embedded numbers from factor labels before measuring lift.

            Factor strings carry live values — "Volume surge 1.64x 20-bar average",
            "EMA21 falling -0.68% over 10 bars". Every distinct float therefore created
            a DISTINCT factor with a handful of samples each, and the significance test
            then dutifully "discovered" that 1.64x volume surge is strongly bullish while
            1.51x is strongly bearish. That is pure noise mining on n=8 buckets. Collapsing
            the numbers turns thousands of one-off labels back into the handful of real
            factors they were always meant to be.
            """
            return re.sub(r"[-+]?\d*\.?\d+", "#", name).strip()

        def bump(name, present, win):
            slot = counts.setdefault(name, {"p_n": 0, "p_w": 0, "a_n": 0, "a_w": 0})
            if present:
                slot["p_n"] += 1
                slot["p_w"] += 1 if win else 0
            else:
                slot["a_n"] += 1
                slot["a_w"] += 1 if win else 0

        # TARGET LEAKAGE GUARD.
        # Post-mortem tags are derived FROM the outcome, so feeding them into lift is
        # circular: "CLEAN_WIN" predicts wins at +58pp for the trivial reason that it
        # only exists on winners. Left in, the engine would confidently promote a factor
        # it cannot observe until after the trade is over, and every calibrated win rate
        # downstream would be inflated. Only PRE-TRADE observable factors are eligible.
        OUTCOME_TAGS = {
            "CLEAN_WIN", "WIN_WITH_HEAT", "THESIS_WRONG", "STOP_TOO_TIGHT",
            "STOPPED_AFTER_PARTIAL_RUN", "NO_FOLLOW_THROUGH",
        }

        vocab = set()
        for t in closed:
            vocab.update(normalise(f) for f in (t.get("factors_passed", []) or []))
            vocab.update(tag for tag in ((t.get("post_mortem", {}) or {}).get("tags", []) or [])
                         if tag not in OUTCOME_TAGS)

        for t in closed:
            win = bool(t.get("is_win"))
            present = {normalise(f) for f in (t.get("factors_passed", []) or [])}
            present.update(tag for tag in ((t.get("post_mortem", {}) or {}).get("tags", []) or [])
                           if tag not in OUTCOME_TAGS)
            for name in vocab:
                bump(name, name in present, win)

        backtest_heavy = sum(1 for t in closed if t.get("source") == "backtest") > len(closed) * 0.5
        lift = {}
        for name, c in counts.items():
            if c["p_n"] < 8 or c["a_n"] < 8:
                continue
            rate_present = c["p_w"] / c["p_n"]
            rate_absent = c["a_w"] / c["a_n"]
            delta = rate_present - rate_absent

            # SIGNIFICANCE, not just sample count. A two-proportion z-test: with 34
            # samples a pure-noise factor can still show +16pp by luck, and acting on
            # that is how an engine learns superstitions. Anything under |z| = 1.64
            # (90% one-sided) is reported NEUTRAL regardless of how large the gap looks.
            # FIX 4 — deflate counts to EFFECTIVE sample size before the z-test, so
            # overlapping backtest windows cannot manufacture significance.
            deflate = cls.BACKTEST_OVERLAP_FACTOR if backtest_heavy else 1.0
            p_eff = max(c["p_n"] / deflate, 1.0)
            a_eff = max(c["a_n"] / deflate, 1.0)
            pooled = (c["p_w"] + c["a_w"]) / (c["p_n"] + c["a_n"])
            se = math.sqrt(pooled * (1 - pooled) * (1 / p_eff + 1 / a_eff)) if 0 < pooled < 1 else 0.0
            z = (delta / se) if se > 0 else 0.0
            pval = cls._z_to_p(z)
            significant = abs(z) >= 1.64

            if not significant:
                verdict = "NEUTRAL"
            elif delta > 0.12:
                verdict = "STRONG_POSITIVE"
            elif delta > 0.04:
                verdict = "POSITIVE"
            elif delta < -0.12:
                verdict = "STRONG_NEGATIVE"
            elif delta < -0.04:
                verdict = "NEGATIVE"
            else:
                verdict = "NEUTRAL"

            lift[name] = {
                "n_present": c["p_n"],
                "rate_present": round(rate_present, 4),
                "n_absent": c["a_n"],
                "rate_absent": round(rate_absent, 4),
                "lift": round(delta, 4),
                "z_score": round(z, 2),
                "p_value": round(pval, 5),
                "effective_n_present": round(p_eff, 1),
                "significant": significant,
                "wilson_present": round(cls.wilson_lower_bound(c["p_w"], c["p_n"]), 4),
                "verdict": verdict,
            }
        # FIX 3a — family-wise false discovery control across all factors tested.
        if apply_fdr and lift:
            names = list(lift.keys())
            survivors = cls._bh_fdr([lift[n]["p_value"] for n in names], alpha=0.10)
            for i, n in enumerate(names):
                lift[n]["fdr_survived"] = i in survivors
                if not lift[n]["fdr_survived"]:
                    lift[n]["significant"] = False
                    lift[n]["verdict"] = "NOT_SIGNIFICANT_AFTER_FDR"

        # FIX 3b — out-of-sample check. A factor that only works in-sample is a
        # curve fit, and the holdout is the only thing that can tell the difference.
        if holdout:
            oos = cls._factor_lift(holdout, global_rate)
            for n, f in lift.items():
                o = oos.get(n)
                if not o:
                    f["oos"] = None
                    f["confirmed_out_of_sample"] = False
                    continue
                same_sign = (f["lift"] > 0) == (o["lift"] > 0)
                f["oos"] = {"lift": o["lift"], "n": o["n_present"]}
                f["confirmed_out_of_sample"] = bool(same_sign and abs(o["lift"]) >= 0.02)
                if f.get("significant") and not f["confirmed_out_of_sample"]:
                    f["verdict"] = "FAILED_OUT_OF_SAMPLE"
                    f["significant"] = False

        return dict(sorted(lift.items(), key=lambda kv: kv[1]["lift"], reverse=True))

    # ------------------------------------------------------------------
    @classmethod
    def _sl_stats(cls, closed: list) -> dict:
        """
        Derive stop placement from how much heat WINNING trades actually take.

        If winners routinely draw down 1.8% before working and the stop sits at 1.2%,
        the engine is manufacturing its own losses. The recommended multiplier pushes
        the stop beyond the 85th percentile of winner MAE, which is precisely the
        'stopped out then it went to target' problem the user described.
        """
        # SURVIVOR BIAS FIX. This previously measured winners only — the trades that by
        # definition were NOT stopped — and concluded stops were adequate while 27 of 36
        # trades were dying on them. Heat must be measured across the whole book.
        population = [t for t in closed if t.get("mae_pct") is not None]
        if len(population) < 10:
            return {"available": False, "reason": f"only {len(population)} records"}

        winner_mae = sorted(abs(t.get("mae_pct", 0.0)) for t in population)
        sl_dists = [abs((t.get("post_mortem", {}) or {}).get("sl_distance_pct", 0.0)) for t in population]
        sl_dists = [d for d in sl_dists if d > 0]

        def pct(data, q):
            if not data:
                return 0.0
            idx = min(int(len(data) * q), len(data) - 1)
            return data[idx]

        p85_mae = pct(winner_mae, 0.85)
        median_sl = statistics.median(sl_dists) if sl_dists else 0.0
        recommended = (p85_mae * 1.15) / median_sl if median_sl > 0 else 1.0

        sl_then_tp = [t for t in closed if t.get("outcome") == "SL_THEN_TP"]

        return {
            "available": True,
            "winner_mae_median_pct": round(statistics.median(winner_mae), 4),
            "winner_mae_p85_pct": round(p85_mae, 4),
            "current_median_sl_pct": round(median_sl, 4),
            "recommended_sl_multiplier": round(max(1.0, min(recommended, 3.0)), 3),
            "sl_then_tp_count": len(sl_then_tp),
            "sl_then_tp_rate": round(len(sl_then_tp) / len(closed), 4),
            "interpretation": (
                f"{len(sl_then_tp)} of {len(closed)} shadow trades hit stop before reaching target. "
                f"Winners take up to {p85_mae:.2f}% heat (85th pct); stops currently sit at {median_sl:.2f}%."
            ),
        }

    # ------------------------------------------------------------------
    @classmethod
    def calibrated_win_rate(cls, raw_score: float, features: dict = None) -> dict:
        """
        The honest probability that this setup reaches target before stop.

        Returns a dict, never a bare float, because the caller must be able to see the
        sample size behind the number and refuse to trade on thin evidence.
        """
        model = cls.build_model()
        features = features or {}

        if model["status"] == "UNCALIBRATED":
            # Cold start. There is NO honest win rate here, so none is invented.
            # v39.0 returned 0.30 + (score-40)/100, which produced numbers like "57%"
            # that looked measured and were not — the exact failure that made the old
            # score/100 dangerous, reintroduced one layer down. win_rate is None and
            # callers must render it as "—". Kelly falls back to the risk floor.
            return {
                "win_rate": None,
                "status": "UNCALIBRATED",
                "confidence": "NONE",
                "samples": model["total_samples"],
                "samples_needed": cls.MIN_SAMPLES_GLOBAL - model["total_samples"],
                "basis": "no resolved trades yet — win rate is unknown, not estimated",
            }

        key = str(cls._bin_key(raw_score, cls.BIN_WIDTH))
        bin_data = model["score_bins"].get(key)

        if bin_data and bin_data["reliable"]:
            base = bin_data["wilson_lb"]
            confidence = "HIGH" if bin_data["n"] >= 30 else "MEDIUM"
            samples = bin_data["n"]
            basis = f"score bin {bin_data['range']} — {bin_data['wins']}/{bin_data['n']} wins, Wilson 90% LB"
        elif bin_data:
            # Thin bin: same monotonic Wilson estimate, just flagged low-confidence.
            # Using shrunk_rate here instead would break the ordering guarantee, since
            # shrunk rates are not PAVA-smoothed.
            base = bin_data["wilson_lb"]
            confidence = "LOW"
            samples = bin_data["n"]
            basis = f"score bin {bin_data['range']} thin (n={bin_data['n']}) — monotonic Wilson LB"
        else:
            base = model["global_wilson"]
            confidence = "LOW"
            samples = model["total_samples"]
            basis = "no samples in this score bin — using global Wilson LB"

        # Conditional adjustments, each capped so no single factor dominates.
        adjustments = []
        regime = features.get("market_regime")
        if regime and regime in model["regime_rates"]:
            r = model["regime_rates"][regime]
            if r["n"] >= cls.MIN_SAMPLES_BIN:
                delta = (r["wilson_lb"] - model["global_wilson"]) * 0.5
                base += delta
                adjustments.append(f"regime {regime}: {delta:+.3f}")

        session = features.get("session")
        if session and session in model["session_rates"]:
            s = model["session_rates"][session]
            if s["n"] >= cls.MIN_SAMPLES_BIN:
                delta = (s["wilson_lb"] - model["global_wilson"]) * 0.4
                base += delta
                adjustments.append(f"session {session}: {delta:+.3f}")

        # Factor lift from the trade's own passed factors.
        lift_total = 0.0
        for factor in (features.get("factors_passed") or []):
            fl = model["factor_lift"].get(re.sub(r"[-+]?\d*\.?\d+", "#", factor).strip())
            # Only statistically significant lift is allowed to move the estimate.
            if fl and fl["n_present"] >= 15 and fl.get("significant"):
                lift_total += fl["lift"] * 0.35
        lift_total = max(-0.12, min(lift_total, 0.12))
        if abs(lift_total) > 0.001:
            base += lift_total
            adjustments.append(f"factor lift: {lift_total:+.3f}")

        final = max(0.05, min(base, 0.92))
        return {
            "win_rate": round(final, 4),
            "status": "CALIBRATED",
            "confidence": confidence,
            "samples": samples,
            "basis": basis,
            "adjustments": adjustments,
        }

    # ------------------------------------------------------------------
    SNAPSHOT_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "audit/calibration_model.json")

    @classmethod
    def export_snapshot(cls) -> dict:
        """
        Persist the BUILT MODEL, not the raw trades.

        backtest_closed.json is 11MB of individual records, and syncing that to Redis
        fails outright (free-tier REST caps requests near 1MB) while burning the daily
        command budget. But calibration never reads those records at query time — it
        reads the aggregates built from them. Score bins, factor lift and SL stats are
        a few KB, so the model itself travels for ~0.1% of the cost of its inputs.
        """
        m = cls.build_model(force=True)
        snap = {
            "built_at": m["built_at"],
            "total_samples": m["total_samples"],
            "global_win_rate": m["global_win_rate"],
            "global_wilson": m["global_wilson"],
            "status": m["status"],
            "score_bins": m["score_bins"],
            "regime_rates": m["regime_rates"],
            "session_rates": m["session_rates"],
            "factor_lift": {k: v for k, v in m["factor_lift"].items() if v.get("significant")},
            "sl_stats": m["sl_stats"],
            "pools": m.get("pools", {}),
        }
        try:
            os.makedirs(os.path.dirname(cls.SNAPSHOT_FILE) or ".", exist_ok=True)
            with open(cls.SNAPSHOT_FILE, "w") as f:
                json.dump(snap, f)
        except Exception as e:
            print(f"[!] Calibration snapshot write failed: {e}")
        return snap

    @classmethod
    def load_snapshot(cls) -> bool:
        """
        Seed the calibrator from a persisted model when no local trade data exists.
        Lets a cold container boot fully calibrated instead of reporting '—' for weeks.
        Live records always take precedence once they exist.
        """
        if not os.path.exists(cls.SNAPSHOT_FILE):
            return False
        try:
            with open(cls.SNAPSHOT_FILE, "r") as f:
                snap = json.load(f)
        except Exception:
            return False
        if not snap.get("score_bins"):
            return False
        snap["seeded_from_snapshot"] = True
        cls._cache = {"epoch": time.time(), "model": snap}
        print(f"[calib] seeded from snapshot: {snap['total_samples']} samples, "
              f"{snap['status']}", flush=True)
        return True

    @classmethod
    def report(cls) -> str:
        """Human-readable calibration state, for the Telegram digest and CLI."""
        m = cls.build_model(force=True)
        pools = m.get("pools", {})
        lines = [
            f"Status: {m['status']} | primary source: {m.get('primary_source')}",
            f"  LIVE     n={pools.get('live',{}).get('n',0):5d}  "
            f"win_rate={(pools.get('live',{}).get('win_rate') or 0)*100:5.1f}%  "
            f"({pools.get('live',{}).get('context','')})",
            f"  BACKTEST n={pools.get('backtest',{}).get('n',0):5d}  "
            f"win_rate={(pools.get('backtest',{}).get('win_rate') or 0)*100:5.1f}%  "
            f"({pools.get('backtest',{}).get('context','')})",
        ]
        v = m.get("validation", {})
        if v:
            lines.append(f"  effective n (overlap-adjusted): train {v['train_effective_n']} "
                         f"of {v['train_n']}, holdout {v['holdout_effective_n']} of {v['holdout_n']}")
        if m["score_bins"]:
            lines.append("Score bins:")
            for k, b in sorted(m["score_bins"].items(), key=lambda kv: int(kv[0])):
                flag = "" if b["reliable"] else "  (thin)"
                lines.append(
                    f"  {b['range']:>8}: {b['wins']:3d}/{b['n']:3d} = {b['raw_rate']*100:5.1f}%  "
                    f"Wilson {b['wilson_lb']*100:5.1f}%{flag}"
                )
        sig = [(n, f) for n, f in m["factor_lift"].items() if f.get("significant")]
        tested = len(m["factor_lift"])
        if sig:
            lines.append(f"Factors surviving FDR + out-of-sample ({len(sig)} of {tested} tested):")
            shown = sig[:5] + [x for x in sig[-4:] if x not in sig[:5]]
            for name, f in shown:
                oos = f.get("oos") or {}
                lines.append(f"  {f['lift']*100:+6.1f}pp  z={f['z_score']:+5.2f} p={f['p_value']:.4f}  "
                             f"n_eff={f.get('effective_n_present')}  oos={oos.get('lift')}  {name[:44]}")
        elif m["factor_lift"]:
            fdr_killed = sum(1 for f in m["factor_lift"].values()
                             if f.get("verdict") == "NOT_SIGNIFICANT_AFTER_FDR")
            oos_killed = sum(1 for f in m["factor_lift"].values()
                             if f.get("verdict") == "FAILED_OUT_OF_SAMPLE")
            lines.append(f"Factor lift: {tested} tested, ZERO survived. "
                         f"{fdr_killed} killed by FDR, {oos_killed} failed out-of-sample.")
        if m["sl_stats"].get("available"):
            lines.append(f"SL: {m['sl_stats']['interpretation']}")
            lines.append(f"    recommended SL multiplier: {m['sl_stats']['recommended_sl_multiplier']}x")
        return "\n".join(lines)


if __name__ == "__main__":
    print(WinRateCalibrator.report())
