# models/research/strategy_research.py
import json
import os
import sys
import time
import xml.etree.ElementTree as ET

import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audit.calibration import WinRateCalibrator
from audit.shadow_ledger import ShadowTradeLedger

PROPOSALS_FILE = "audit/upgrade_proposals.json"
APPROVED_FILE = "audit/approved_weights.json"

class StrategyResearchEngine:
    """
    Den Engine v39.0 Self-Improvement Engine — PROPOSAL ONLY.

    The user asked for an engine that keeps itself current by browsing the internet and
    adopting the latest strategies. This module does the research and writes concrete,
    evidence-backed proposals — but it deliberately does NOT hot-patch scoring logic on a
    live system that is sizing real money.

    That restraint is the user's own rule, enforced in code: "this should never downgrade
    the engine or fuck us up no matter what." An unattended loop that rewrites its own
    scoring weights from scraped blog posts is precisely how an engine silently degrades,
    and the degradation is invisible until the losses arrive. Anything auto-applied is
    also unfalsifiable — you can never tell whether a losing week came from the market or
    from last night's self-edit.

    So there are two proposal sources, and one gate:

      INTERNAL (high trust)  — derived from this engine's OWN resolved shadow trades.
                               Factor lift, stop distance, regime and session filters.
                               These are measured on our data, so they carry statistics.

      EXTERNAL (low trust)   — public research and strategy discussion, surfaced as
                               reading material with a relevance note. Never scored,
                               never auto-weighted, because a headline is not evidence.

      GATE                   — a proposal only takes effect once its id is listed in
                               audit/approved_weights.json. Until then it is inert text.
    """

    HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                             "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

    RESEARCH_FEEDS = [
        ("Quant research", "https://news.google.com/rss/search?q=quantitative+trading+strategy+research+when:7d&hl=en-US&gl=US&ceid=US:en"),
        ("Crypto market structure", "https://news.google.com/rss/search?q=crypto+futures+funding+rate+open+interest+strategy+when:7d&hl=en-US&gl=US&ceid=US:en"),
        ("Order flow / SMC", "https://news.google.com/rss/search?q=order+flow+trading+liquidity+sweep+smart+money+when:7d&hl=en-US&gl=US&ceid=US:en"),
    ]

    MIN_SAMPLES_FOR_PROPOSAL = 25

    # ------------------------------------------------------------------
    @staticmethod
    def _load(path, default):
        if not os.path.exists(path):
            return default
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            return default

    @staticmethod
    def _save(path, payload):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        try:
            with open(path, "w") as f:
                json.dump(payload, f, indent=2, default=str)
        except Exception as e:
            print(f"[!] Research write failed: {e}")

    # ------------------------------------------------------------------
    @classmethod
    def approved_ids(cls) -> set:
        data = cls._load(APPROVED_FILE, {})
        return set(data.get("approved_proposal_ids", []))

    @classmethod
    def is_approved(cls, proposal_id: str) -> bool:
        return proposal_id in cls.approved_ids()

    # ------------------------------------------------------------------
    @classmethod
    def internal_proposals(cls) -> list:
        """
        Proposals derived from our own resolved shadow trades. Every one carries the
        sample size and effect size behind it, so it can be judged rather than trusted.
        """
        model = WinRateCalibrator.build_model(force=True)
        proposals = []

        if model["total_samples"] < cls.MIN_SAMPLES_FOR_PROPOSAL:
            return [{
                "id": "await-evidence",
                "type": "STATUS",
                "title": "Not enough resolved shadow trades to propose changes",
                "detail": (f"{model['total_samples']} resolved, "
                           f"{cls.MIN_SAMPLES_FOR_PROPOSAL} needed. The engine will not "
                           f"propose weight changes on evidence this thin."),
                "confidence": "NONE",
                "auto_applicable": False,
            }]

        # --- Factors that are actively costing money -----------------------
        for name, f in model["factor_lift"].items():
            if f["lift"] <= -0.08 and f["n_present"] >= 20:
                proposals.append({
                    "id": f"demote::{name}",
                    "type": "FACTOR_WEIGHT",
                    "title": f"Demote factor: {name}",
                    "detail": (f"Setups with this factor win {f['rate_present'] * 100:.1f}% "
                               f"(n={f['n_present']}) versus {f['rate_absent'] * 100:.1f}% without it — "
                               f"a lift of {f['lift'] * 100:+.1f}pp. This factor is currently adding "
                               f"score to setups that lose more often."),
                    "evidence": f,
                    "confidence": "HIGH" if f["n_present"] >= 40 else "MEDIUM",
                    "auto_applicable": False,
                })
            elif f["lift"] >= 0.12 and f["n_present"] >= 20:
                proposals.append({
                    "id": f"promote::{name}",
                    "type": "FACTOR_WEIGHT",
                    "title": f"Promote factor: {name}",
                    "detail": (f"Setups with this factor win {f['rate_present'] * 100:.1f}% "
                               f"(n={f['n_present']}) versus {f['rate_absent'] * 100:.1f}% without it — "
                               f"a lift of {f['lift'] * 100:+.1f}pp. Worth more score weight than it "
                               f"currently receives."),
                    "evidence": f,
                    "confidence": "HIGH" if f["n_present"] >= 40 else "MEDIUM",
                    "auto_applicable": False,
                })

        # --- Stop distance -------------------------------------------------
        sl = model.get("sl_stats") or {}
        if sl.get("available") and sl.get("sl_then_tp_rate", 0) > 0.12:
            proposals.append({
                "id": "widen-stops",
                "type": "RISK_PARAMETER",
                "title": f"Widen stops to {sl['recommended_sl_multiplier']}x ATR",
                "detail": (f"{sl['sl_then_tp_count']} shadow trades "
                           f"({sl['sl_then_tp_rate'] * 100:.0f}%) were stopped out and THEN reached "
                           f"target — the directional read was right and the stop was too tight. "
                           f"Winners take up to {sl['winner_mae_p85_pct']:.2f}% heat at the 85th "
                           f"percentile; stops currently sit at {sl['current_median_sl_pct']:.2f}%."),
                "evidence": sl,
                "confidence": "HIGH",
                "auto_applicable": True,   # already consumed by liquidity_map at runtime
            })

        # --- Regime / session filters ---------------------------------------
        for field, label in (("regime_rates", "regime"), ("session_rates", "session")):
            for key, r in (model.get(field) or {}).items():
                if r["n"] >= 20 and r["raw_rate"] < max(0.30, model["global_win_rate"] - 0.15):
                    proposals.append({
                        "id": f"suppress::{label}::{key}",
                        "type": "FILTER",
                        "title": f"Stop trading {label} = {key}",
                        "detail": (f"{r['wins']}/{r['n']} = {r['raw_rate'] * 100:.1f}% win rate "
                                   f"(Wilson LB {r['wilson_lb'] * 100:.1f}%) versus a global "
                                   f"{model['global_win_rate'] * 100:.1f}%. This bucket is a "
                                   f"consistent loser."),
                        "evidence": r,
                        "confidence": "HIGH" if r["n"] >= 40 else "MEDIUM",
                        "auto_applicable": False,
                    })

        # --- Score threshold ------------------------------------------------
        reliable = {k: b for k, b in model["score_bins"].items() if b["reliable"]}
        if reliable:
            good = [int(k) for k, b in reliable.items() if b["wilson_lb"] >= 0.55]
            if good:
                proposals.append({
                    "id": "retune-threshold",
                    "type": "THRESHOLD",
                    "title": f"Evidence supports a dispatch floor near {min(good)}",
                    "detail": (f"Score bins at or above {min(good)} show a Wilson-90 lower bound of "
                               f"55%+ on real resolved outcomes. Bins below that do not clear the bar."),
                    "evidence": {k: reliable[k] for k in sorted(reliable, key=lambda x: int(x))},
                    "confidence": "MEDIUM",
                    "auto_applicable": False,
                })

        return proposals

    # ------------------------------------------------------------------
    @classmethod
    def external_research(cls, limit_per_feed: int = 5) -> list:
        """Surface public strategy discussion as READING MATERIAL. Never weighted."""
        found = []
        for topic, url in cls.RESEARCH_FEEDS:
            try:
                resp = requests.get(url, headers=cls.HEADERS, timeout=8)
                if resp.status_code != 200:
                    continue
                root = ET.fromstring(resp.content)
                for item in root.findall(".//item")[:limit_per_feed]:
                    t = item.find("title")
                    link = item.find("link")
                    if t is not None and t.text:
                        found.append({
                            "topic": topic,
                            "title": t.text,
                            "url": link.text if link is not None else "",
                            "trust": "UNVERIFIED — public source, not evidence",
                        })
            except Exception as e:
                print(f"[!] Research feed '{topic}' failed: {type(e).__name__}")
        return found

    # ------------------------------------------------------------------
    @classmethod
    def run(cls) -> dict:
        internal = cls.internal_proposals()
        external = cls.external_research()
        approved = cls.approved_ids()

        report = {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "shadow_summary": ShadowTradeLedger.summary(),
            "internal_proposals": internal,
            "external_reading": external,
            "approved_ids": sorted(approved),
            "pending_count": len([p for p in internal if p["id"] not in approved and p["type"] != "STATUS"]),
            "how_to_approve": (
                f"Add the proposal id to 'approved_proposal_ids' in {APPROVED_FILE}. "
                f"Nothing in this file changes engine behaviour until you do."
            ),
        }
        cls._save(PROPOSALS_FILE, report)
        return report

    # ------------------------------------------------------------------
    @classmethod
    def report_text(cls) -> str:
        r = cls.run()
        lines = [f"Den Engine self-improvement report — {r['generated_at']}",
                 f"Shadow book: {r['shadow_summary']}", ""]
        lines.append(f"INTERNAL PROPOSALS ({r['pending_count']} pending approval):")
        for p in r["internal_proposals"]:
            mark = "✓ approved" if p["id"] in r["approved_ids"] else "· pending"
            lines.append(f"  [{mark}] ({p['confidence']}) {p['title']}")
            lines.append(f"      {p['detail']}")
        lines.append("")
        lines.append("EXTERNAL READING (unverified, never auto-applied):")
        for e in r["external_reading"][:8]:
            lines.append(f"  - [{e['topic']}] {e['title'][:110]}")
        lines.append("")
        lines.append(r["how_to_approve"])
        return "\n".join(lines)


if __name__ == "__main__":
    print(StrategyResearchEngine.report_text())
