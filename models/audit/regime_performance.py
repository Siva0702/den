# models/audit/regime_performance.py
import json
import math
import os
import threading
import time

WEIGHTS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "audit/regime_weights.json")

class RegimePerformance:
    """
    Den Engine v41.0 Regime-Conditional Score Adjustment — learned, not assumed.

    The engine's structure pillar awarded the same +40% for a bullish break of
    structure as for a bearish one. Measured over 404 trades:

        BEARISH BOS   n=105   70.5% accuracy   +0.725R/trade
        BULLISH BOS   n= 48   25.0% accuracy   -0.522R/trade

    Identical reward, opposite outcomes. Because a 70+ score requires maximum
    confluence, and bullish BOS is a large slice of that confluence, the scoring was
    systematically promoting its worst setups into its highest band. That is the whole
    explanation for scores above 70 winning 0% while 60-65 won 72%.

    Rather than hand-pick new weights — which is how the symmetric ones got there — this
    measures the adjustment from resolved outcomes:

        adjustment = (avg_R_for_this_condition - overall_avg_R) x CONFIDENCE x SCALE

    CONFIDENCE is a shrinkage factor n/(n+k), so a condition seen 5 times barely moves
    the score while one seen 100 times moves it fully. Adjustments are bounded, and any
    condition below MIN_SAMPLES is ignored entirely. The engine cannot learn a
    superstition from a handful of trades.
    """

    MIN_SAMPLES = 15
    SHRINK_K = 25.0            # pseudo-observations; n=25 gives half weight
    MAX_ADJUST = 12.0          # points, per condition family
    SCALE = 10.0               # R-difference -> score points
    REFRESH_SECONDS = 900.0

    _cache = {"built": 0.0, "model": None}
    _lock = threading.Lock()

    # ------------------------------------------------------------------
    @staticmethod
    def _r_multiple(t) -> float:
        e = float(t.get("entry", 0) or 0)
        s = float(t.get("stop_loss", 0) or 0)
        slp = abs(e - s) / e * 100 if e else 0
        return (float(t.get("pnl_pct", 0) or 0) / slp) if slp else 0.0

    # ------------------------------------------------------------------
    @classmethod
    def build(cls, force: bool = False) -> dict:
        now = time.time()
        with cls._lock:
            if not force and cls._cache["model"] and (now - cls._cache["built"]) < cls.REFRESH_SECONDS:
                return cls._cache["model"]

        from audit.shadow_ledger import ShadowTradeLedger
        rows = ShadowTradeLedger.current_version_records()
        if len(rows) < cls.MIN_SAMPLES * 2:
            model = {"available": False, "n": len(rows),
                     "reason": f"only {len(rows)} records; need {cls.MIN_SAMPLES * 2}"}
            with cls._lock:
                cls._cache = {"built": now, "model": model}
            return model

        overall = sum(cls._r_multiple(t) for t in rows) / len(rows)

        def family(key_fn, name):
            buckets = {}
            for t in rows:
                k = key_fn(t)
                if k is None:
                    continue
                b = buckets.setdefault(str(k), {"n": 0, "R": 0.0, "w": 0})
                b["n"] += 1
                b["R"] += cls._r_multiple(t)
                b["w"] += 1 if t.get("is_win") is True else 0
            out = {}
            for k, b in buckets.items():
                if b["n"] < cls.MIN_SAMPLES:
                    continue
                avg_r = b["R"] / b["n"]
                confidence = b["n"] / (b["n"] + cls.SHRINK_K)
                adj = (avg_r - overall) * confidence * cls.SCALE
                out[k] = {
                    "n": b["n"],
                    "accuracy_pct": round(b["w"] / b["n"] * 100, 1),
                    "avg_R": round(avg_r, 3),
                    "edge_vs_overall": round(avg_r - overall, 3),
                    "confidence": round(confidence, 3),
                    "adjustment": round(max(-cls.MAX_ADJUST, min(adj, cls.MAX_ADJUST)), 2),
                }
            return {"family": name, "buckets": dict(sorted(
                out.items(), key=lambda kv: -kv[1]["adjustment"]))}

        model = {
            "available": True,
            "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "n": len(rows),
            "overall_avg_R": round(overall, 4),
            # The condition that actually broke the score curve.
            "bos_direction": family(
                lambda t: f"{(t.get('features') or {}).get('bos', 'NONE')}|{t.get('direction')}",
                "bos_x_direction"),
            "regime_direction": family(
                lambda t: f"{(t.get('features') or {}).get('regime', t.get('market_regime', '?'))}"
                          f"|{t.get('direction')}",
                "regime_x_direction"),
            "direction": family(lambda t: t.get("direction"), "direction"),
        }
        try:
            os.makedirs(os.path.dirname(WEIGHTS_FILE), exist_ok=True)
            with open(WEIGHTS_FILE, "w") as f:
                json.dump(model, f, indent=2)
        except Exception as e:
            print(f"[!] regime weights write failed: {e}")
        with cls._lock:
            cls._cache = {"built": now, "model": model}
        return model

    # ------------------------------------------------------------------
    @classmethod
    def adjustment(cls, direction: str, bos: str = None, regime: str = None) -> dict:
        """
        Total learned adjustment in score points for this setup, with the reasoning.
        Returns 0 with an explicit reason whenever evidence is insufficient — the
        engine must never adjust a score on a pattern it has not actually measured.
        """
        m = cls.build()
        if not m.get("available"):
            return {"total": 0.0, "components": [], "available": False,
                    "reason": m.get("reason", "insufficient data")}

        total = 0.0
        parts = []
        for fam_key, lookup in (
            ("bos_direction", f"{bos or 'NONE'}|{direction}"),
            ("regime_direction", f"{regime}|{direction}"),
            ("direction", direction),
        ):
            fam = m.get(fam_key) or {}
            hit = (fam.get("buckets") or {}).get(lookup)
            if hit:
                total += hit["adjustment"]
                parts.append({
                    "family": fam_key, "key": lookup,
                    "adjustment": hit["adjustment"], "n": hit["n"],
                    "accuracy_pct": hit["accuracy_pct"], "avg_R": hit["avg_R"],
                })

        # Families overlap (direction appears in all three), so cap the combined move.
        total = max(-cls.MAX_ADJUST * 1.5, min(total, cls.MAX_ADJUST * 1.5))
        return {"total": round(total, 2), "components": parts, "available": True}

    # ------------------------------------------------------------------
    @classmethod
    def report(cls) -> str:
        m = cls.build(force=True)
        if not m.get("available"):
            return f"Regime weights: UNAVAILABLE — {m.get('reason')}"
        lines = [f"Learned regime weights — n={m['n']}, overall {m['overall_avg_R']:+.3f}R"]
        for fam in ("bos_direction", "regime_direction", "direction"):
            f = m.get(fam) or {}
            if not f.get("buckets"):
                continue
            lines.append(f"\n{f['family']}:")
            for k, b in list(f["buckets"].items())[:8]:
                lines.append(f"  {k:<28} n={b['n']:3}  acc={b['accuracy_pct']:5.1f}%  "
                             f"avgR={b['avg_R']:+.3f}  adj={b['adjustment']:+6.2f}")
        return "\n".join(lines)


if __name__ == "__main__":
    print(RegimePerformance.report())
