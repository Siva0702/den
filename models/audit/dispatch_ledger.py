# models/audit/dispatch_ledger.py
import json
import os
import tempfile
import threading
import time

MODELS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DISPATCH_FILE = os.path.join(MODELS_DIR, "audit/dispatch_ledger.json")


class DispatchLedger:
    """
    Append-only audit trail for DISPATCHED signals — the trades actually sent to the user.

    This did not exist, and its absence made the engine unauditable on the only trades
    that matter. Positions live in portfolio/active_positions.json while running and are
    simply DROPPED from that list when they hit TP or SL — `remaining_positions` never
    receives them, so `save_positions` writes them out of existence. Once gone there was
    no entry, no exit, no P&L anywhere. A signal that hit target left no trace outside
    the user's Telegram history.

    Three other places looked like a record and were not:
      - den:dispatched_signals   went stale days ago and stopped being written
      - signal_cooldown          keys on "TICKER|DIRECTION" and keeps only the LATEST,
                                 so a re-dispatch silently overwrites the prior one
      - total_signals_sent       an in-memory counter, reset to 0 on every restart

    The shadow ledger is NOT a substitute. Dispatched positions carry their own entry and
    stop, distinct from any shadow trade on the same ticker (ABNB dispatched at 178.59
    against a shadow record at 178.05 — different trade, different outcome). Reading one
    to report on the other produces confidently wrong numbers.

    Design rules:
      - APPEND ONLY. A dispatch row is never rewritten, only completed in place by id.
      - Closes are detected by DIFFING the position list, so every exit path is captured
        including ones added later. No per-branch hook to forget.
      - Never raises into the scan loop. Audit failure must not stop trading.
    """

    MAX_RECORDS = 5000

    # Bitunix / WEEX USDT-perp fees. Taker on BOTH legs is the conservative
    # assumption: a market entry and a stop-out are both taker. This is charged on
    # NOTIONAL, not margin, so at high leverage it is not a rounding error — at 100x a
    # 0.06% round trip costs 12% of the margin posted. Reporting gross P&L at these
    # leverages materially overstates the result.
    TAKER_FEE = 0.0006          # 0.06% per side
    MAKER_FEE = 0.0002
    FUNDING_INTERVAL_H = 8.0
    _lock = threading.Lock()

    # ------------------------------------------------------------------
    @staticmethod
    def _atomic_write(payload):
        d = os.path.dirname(DISPATCH_FILE) or "."
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(payload, f, indent=2, default=str)
            os.replace(tmp, DISPATCH_FILE)
        except Exception:
            try:
                os.unlink(tmp)
            except Exception:
                pass
            raise
        try:
            from audit.portable_store import PortableStateStore
            PortableStateStore.save_state("dispatch_ledger", payload)
        except Exception:
            pass

    @classmethod
    def load(cls) -> list:
        if os.path.exists(DISPATCH_FILE):
            try:
                with open(DISPATCH_FILE, "r") as f:
                    r = json.load(f)
                    if isinstance(r, list):
                        return r
            except Exception:
                pass
        try:
            from audit.portable_store import PortableStateStore
            r = PortableStateStore.load_state("dispatch_ledger")
            if isinstance(r, list):
                return r
        except Exception:
            pass
        return []

    # ------------------------------------------------------------------
    @staticmethod
    def _key(pos: dict) -> str:
        return (f"{pos.get('ticker')}|{pos.get('direction')}|"
                f"{int(float(pos.get('epoch_time') or 0))}")

    @classmethod
    def record_dispatch(cls, candidate: dict) -> str:
        """Called the instant a signal is sent. Returns the dispatch id."""
        try:
            now = time.time()
            did = (f"{candidate.get('ticker')}|{candidate.get('direction')}|"
                   f"{int(now)}")
            row = {
                "dispatch_id": did,
                "ticker": candidate.get("ticker"),
                "direction": candidate.get("direction"),
                "dispatched_epoch": now,
                "dispatched_time": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now)),
                "entry": candidate.get("entry"),
                "stop_loss": candidate.get("sl"),
                "tp_ladder": candidate.get("tp_ladder"),
                "primary_tp": candidate.get("tp"),
                "sl_pct": candidate.get("sl_pct"),
                "reward_risk": candidate.get("rr"),
                "model_score": candidate.get("model_score"),
                "model_prob": candidate.get("model_prob"),
                "model_version": candidate.get("model_version"),
                "entry_provenance": candidate.get("entry_provenance"),
                "model_evidence": candidate.get("model_evidence"),
                "margin": candidate.get("final_margin"),
                "leverage": candidate.get("chosen_leverage"),
                "pillar_score": candidate.get("pillar_score"),
                "adjusted_score": candidate.get("adjusted_score"),
                "calibrated_win_rate": candidate.get("calibrated_win_rate"),
                "kelly_vetoed": candidate.get("kelly_vetoed"),
                "leverage": candidate.get("chosen_leverage"),
                "margin": candidate.get("final_margin"),
                "features": candidate.get("feature_snapshot"),
                "status": "OPEN",
                "exit_price": None, "exit_reason": None,
                "closed_epoch": None, "closed_time": None,
                "pnl_pct": None, "r_multiple": None,
            }
            with cls._lock:
                rows = cls.load()
                rows.append(row)
                cls._atomic_write(rows[-cls.MAX_RECORDS:])
            return did
        except Exception as e:
            print(f"[!] dispatch audit (open) failed: {e}", flush=True)
            return ""

    @classmethod
    def record_close(cls, pos: dict, exit_price: float, reason: str) -> bool:
        """
        Complete the open row for this position. Matched on ticker+direction+entry, so
        it works even when the caller never saw the dispatch id.
        """
        try:
            tk, dr = pos.get("ticker"), pos.get("direction")
            entry = float(pos.get("entry_price") or pos.get("entry") or 0.0)
            stop = float(pos.get("stop_loss") or 0.0)
            exit_price = float(exit_price or 0.0)
            if not tk or entry <= 0:
                return False

            sign = 1.0 if str(dr).upper() == "LONG" else -1.0

            # Execution supplied a gap-aware fill. Do not improve it back to the stop.
            gross_pct = (exit_price - entry) / entry * 100.0 * sign
            sl_pct = abs(entry - stop) / entry * 100.0 if stop else 0.0
            event = pos.get("execution_event") or {}
            now = float(event.get("closed_epoch") or time.time())

            # ---- COSTS -------------------------------------------------------
            # Everything below is expressed as a % of NOTIONAL so it subtracts
            # directly from the gross move, which keeps R honest: R must be measured
            # net, or a 0.5% edge at 0.12% costs looks 24% better than it is.
            lev = float(pos.get("leverage") or pos.get("chosen_leverage") or 0) or None
            margin = float(pos.get("margin") or pos.get("final_margin") or 0.0)
            notional = margin * lev if (margin and lev) else None

            fee_pct = cls.TAKER_FEE * (1 + exit_price/entry) * 100.0        # open + close, both taker

            opened = float(pos.get("epoch_time") or 0.0)
            hold_h = max((now - opened) / 3600.0, 0.0) if opened else 0.0
            # Funding is charged only to positions open AT a settlement stamp, so a
            # 46-minute trade usually pays none. Count the stamps actually crossed.
            settlements = max(0, int(now // (cls.FUNDING_INTERVAL_H*3600)) - int(opened // (cls.FUNDING_INTERVAL_H*3600))) if opened else 0
            fr = pos.get("funding_rate")
            try:
                fr = float(fr) if fr is not None else 0.0
            except (TypeError, ValueError):
                fr = 0.0
            # Longs pay positive funding, shorts receive it.
            funding_pct = settlements * fr * 100.0 * sign

            from indicators.execution import trade_cost_pct
            cost_pct = event.get("cost_pct", trade_cost_pct(entry, exit_price, hold_h, fr))
            pnl_pct = gross_pct - cost_pct
            r_mult = (pnl_pct / sl_pct) if sl_pct else 0.0
            gross_r = (gross_pct / sl_pct) if sl_pct else 0.0

            with cls._lock:
                rows = cls.load()
                target = None
                for r in reversed(rows):
                    if (r.get("status") == "OPEN" and r.get("ticker") == tk
                            and r.get("direction") == dr
                            and abs(float(r.get("entry") or 0)-entry) <= max(entry*1e-8, 1e-10)
                            and (not pos.get("dispatch_id") or r.get("dispatch_id") == pos["dispatch_id"])):
                        target = r
                        break
                if target is None:
                    # Already completed (e.g. scratched at breakeven when the decay
                    # alert fired) — the position keeps being monitored afterwards, so
                    # the later TP/SL diff must not book the same trade twice.
                    recent = [r for r in rows if r.get("ticker") == tk
                              and r.get("direction") == dr
                              and r.get("status") == "CLOSED"
                              and abs(float(r.get("entry") or 0)-entry) <= max(entry*1e-8, 1e-10)
                              and ((pos.get("dispatch_id") and r.get("dispatch_id") == pos["dispatch_id"])
                                   or (not pos.get("dispatch_id") and abs(float(r.get("dispatched_epoch") or 0)-opened) < 60))]
                    if recent:
                        return True
                if target is None:
                    # Dispatched before this ledger existed, or by another path. Record
                    # it anyway — an orphan close is evidence, silence is not.
                    target = {
                        "dispatch_id": f"{tk}|{dr}|orphan-{int(now)}",
                        "ticker": tk, "direction": dr,
                        "dispatched_epoch": float(pos.get("epoch_time") or 0) or None,
                        "entry": entry, "stop_loss": stop,
                        "tp_ladder": pos.get("tp_ladder"),
                        "orphan": True,
                    }
                    rows.append(target)
                target.update({
                    "status": "CLOSED",
                    "exit_price": exit_price,
                    "exit_reason": reason,
                    "closed_epoch": now,
                    "closed_time": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now)),
                    "gross_pnl_pct": round(gross_pct, 4),
                    "cost_pct": round(cost_pct, 4),
                    "execution_version": event.get("logic_version"),
                    "cost_basis": "estimated taker fees, slippage and funding allowance",
                    "fee_pct": round(fee_pct, 4),
                    "funding_pct": round(funding_pct, 4),
                    "funding_settlements": settlements,
                    "total_cost_pct": round(cost_pct, 4),
                    "notional": round(notional, 2) if notional else None,
                    "fee_usd": round(notional * fee_pct / 100.0, 2) if notional else None,
                    "hold_hours": round(hold_h, 3),
                    "pnl_pct": round(pnl_pct, 4),          # NET of fees and funding
                    "gross_r": round(gross_r, 4),
                    "r_multiple": round(r_mult, 4),        # NET
                    "is_win": r_mult > 0,
                    "net_pnl_usd": round(notional * pnl_pct / 100, 2) if notional else None,
                })
                cls._atomic_write(rows[-cls.MAX_RECORDS:])
            return True
        except Exception as e:
            print(f"[!] dispatch audit (close) failed: {e}", flush=True)
            return False

    @classmethod
    def sync_closures(cls, before: list, after: list, price_map: dict = None) -> int:
        """
        Diff the position list around a monitor pass and complete whatever vanished.

        Hooking each exit branch means every NEW branch is a silent audit hole. Diffing
        the list cannot miss one, because a position that is gone is closed by
        definition, whatever code removed it.
        """
        try:
            keep = {cls._key(p) for p in (after or []) if isinstance(p, dict)}
            n = 0
            for p in (before or []):
                if not isinstance(p, dict) or cls._key(p) in keep:
                    continue
                px = None
                if price_map:
                    px = (price_map.get(p.get("ticker")) or {}).get("close")
                px = float(px) if px else float(p.get("last_price") or 0.0)
                if not px:
                    px = float(p.get("take_profit") or p.get("entry_price") or 0.0)
                entry = float(p.get("entry_price") or 0.0)
                stop = float(p.get("stop_loss") or 0.0)
                sign = 1.0 if str(p.get("direction")).upper() == "LONG" else -1.0
                reason = "TP_HIT" if (px - entry) * sign > 0 else "SL_HIT"
                if stop and abs(px - stop) / max(stop, 1e-9) < 0.0015:
                    reason = "SL_HIT"
                if cls.record_close(p, px, reason):
                    n += 1
            return n
        except Exception as e:
            print(f"[!] dispatch audit (sync) failed: {e}", flush=True)
            return 0

    # ------------------------------------------------------------------
    @classmethod
    def report(cls, capital: float = 1000.0, risk_usd: float = 30.0) -> dict:
        rows = cls.load()
        closed = [r for r in rows if r.get("status") == "CLOSED"]
        openr = [r for r in rows if r.get("status") == "OPEN"]
        if not closed:
            return {"available": False, "dispatched_total": len(rows),
                    "open": len(openr), "closed": 0,
                    "reason": "no dispatched signal has closed since the audit began"}
        Rs = [float(r.get("r_multiple") or 0.0) for r in closed]
        wins = sum(1 for r in closed if r.get("is_win"))
        gw = sum(x for x in Rs if x > 0)
        gl = -sum(x for x in Rs if x < 0)
        pnl = sum(Rs) * risk_usd
        return {
            "available": True,
            "dispatched_total": len(rows),
            "open": len(openr),
            "closed": len(closed),
            "wins": wins, "losses": len(closed) - wins,
            "accuracy_pct": round(wins / len(closed) * 100, 1),
            "total_R": round(sum(Rs), 3),
            "avg_R": round(sum(Rs) / len(closed), 3),
            "profit_factor": round(gw / gl, 3) if gl else None,
            "risk_per_trade_usd": risk_usd,
            "pnl_usd": round(pnl, 2),
            "starting_capital": capital,
            "ending_equity": round(capital + pnl, 2),
            "return_pct": round(pnl / capital * 100, 2),
            "orphans": sum(1 for r in closed if r.get("orphan")),
        }
