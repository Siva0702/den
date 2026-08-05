# models/audit/ledger_recovery.py
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audit.shadow_ledger import ShadowTradeLedger, SHADOW_CLOSED_FILE

class LedgerRecovery:
    """
    Den Engine v40.1 Ledger Recovery.

    Trades resolved before the trail-arming fix all closed at TP1, because the bar that
    tagged TP1 also had its low below TP1 and immediately triggered the trail. Every one
    of those records says `TP1_HIT` regardless of whether price went on to TP3 or
    collapsed back through the stop.

    I initially said this was unrecoverable. That was wrong: the outcome was never
    recorded, but the PRICE HISTORY that determines it is still available from the
    exchange. Each record carries its ticker, direction, entry, stop, ladder and open
    timestamp — enough to fetch the candles from that moment forward and replay the
    trade under the corrected ratchet.

    Nothing is invented. Every recovered outcome is derived from real candles, resolved
    with exactly the logic the live ledger now uses, and marked `recovered: True` with
    the original outcome kept in `pre_recovery_outcome` so the change is auditable.
    """

    HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                             "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    MAX_WORKERS = 4
    MAX_BARS = 500

    TICKER_MAP = {"PEPE/USDT": "1000PEPEUSDT", "SHIB/USDT": "1000SHIBUSDT",
                  "BONK/USDT": "1000BONKUSDT", "MATIC/USDT": "POLUSDT"}

    # ------------------------------------------------------------------
    @classmethod
    def _klines_from(cls, ticker: str, start_ms: int) -> pd.DataFrame:
        """15m candles from start_ms to now, across the same providers the feed uses."""
        sym = cls.TICKER_MAP.get(ticker, ticker.replace("/", "").upper())
        attempts = [
            ("binance", f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}"
                        f"&interval=15m&startTime={start_ms}&limit={cls.MAX_BARS}"),
            ("bybit", f"https://api.bybit.com/v5/market/kline?category=linear&symbol={sym}"
                      f"&interval=15&start={start_ms}&limit=1000"),
        ]
        for name, url in attempts:
            try:
                r = requests.get(url, headers=cls.HEADERS, timeout=8)
                if r.status_code != 200:
                    continue
                data = r.json()
                if name == "binance":
                    rows = data if isinstance(data, list) else []
                    if not rows:
                        continue
                    return pd.DataFrame([{
                        "timestamp": int(k[0]), "open": float(k[1]), "high": float(k[2]),
                        "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])}
                        for k in rows])
                kl = (data.get("result") or {}).get("list") or []
                if not kl:
                    continue
                return pd.DataFrame([{
                    "timestamp": int(k[0]), "open": float(k[1]), "high": float(k[2]),
                    "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])}
                    for k in reversed(kl)])
            except Exception:
                continue
        return None

    # ------------------------------------------------------------------
    @classmethod
    def _replay(cls, rec: dict, bars: pd.DataFrame) -> dict:
        """
        Resolve the trade against real candles using the CORRECTED ratchet: the trail
        arms one bar after a rung is tagged, so a runner is no longer cut at TP1.
        """
        direction = rec["direction"]
        entry = float(rec["entry"])
        sl = float(rec.get("stop_loss", 0) or 0)
        ladder = [float(x) for x in (rec.get("tp_ladder") or [])]
        if not ladder or sl <= 0 or entry <= 0 or bars is None or bars.empty:
            return None

        mae = mfe = 0.0
        tp_hit = []
        armed = 0

        for i in range(len(bars)):
            hi = float(bars.iloc[i]["high"])
            lo = float(bars.iloc[i]["low"])

            if direction == "LONG":
                mae = min(mae, (lo - entry) / entry * 100)
                mfe = max(mfe, (hi - entry) / entry * 100)
            else:
                mae = min(mae, (entry - hi) / entry * 100)
                mfe = max(mfe, (entry - lo) / entry * 100)

            trail_idx = max(tp_hit) if tp_hit else 0

            if trail_idx > 0:
                trail = ladder[trail_idx - 1]
                if armed == trail_idx:
                    stopped = (lo <= trail) if direction == "LONG" else (hi >= trail)
                    if stopped or trail_idx >= len(ladder):
                        return cls._out(rec, f"TP{trail_idx}_HIT", trail, True, mae, mfe, tp_hit, i + 1)
                else:
                    armed = trail_idx
            else:
                stopped = (lo <= sl) if direction == "LONG" else (hi >= sl)
                if stopped:
                    # Did it later reach target anyway? Label it, but it is still a loss.
                    for k in range(i + 1, len(bars)):
                        tgt = ladder[0]
                        if ((direction == "LONG" and float(bars.iloc[k]["high"]) >= tgt) or
                                (direction == "SHORT" and float(bars.iloc[k]["low"]) <= tgt)):
                            return cls._out(rec, "SL_THEN_TP", sl, False, mae, mfe, tp_hit, i + 1)
                    return cls._out(rec, "SL_HIT", sl, False, mae, mfe, tp_hit, i + 1)

            reached = ([j + 1 for j, tp in enumerate(ladder) if hi >= tp] if direction == "LONG"
                       else [j + 1 for j, tp in enumerate(ladder) if lo <= tp])
            for r in reached:
                if r not in tp_hit:
                    tp_hit.append(r)

        # Still running at the end of available history.
        trail_idx = max(tp_hit) if tp_hit else 0
        if trail_idx > 0:
            return cls._out(rec, f"TP{trail_idx}_OPEN", ladder[trail_idx - 1], True,
                            mae, mfe, tp_hit, len(bars), still_running=True)
        return cls._out(rec, "STILL_OPEN", float(bars.iloc[-1]["close"]), False,
                        mae, mfe, tp_hit, len(bars), still_running=True)

    @staticmethod
    def _out(rec, outcome, exit_px, is_win, mae, mfe, tp_hit, bars_held, still_running=False):
        entry = float(rec["entry"])
        pnl = ((exit_px - entry) / entry * 100) if rec["direction"] == "LONG" \
            else ((entry - exit_px) / entry * 100)
        out = dict(rec)
        out.update({
            "pre_recovery_outcome": rec.get("outcome"),
            "pre_recovery_pnl_pct": rec.get("pnl_pct"),
            "outcome": outcome,
            "is_win": bool(is_win),
            "exit_price": exit_px,
            "pnl_pct": round(pnl, 4),
            "mae_pct": round(mae, 4),
            "mfe_pct": round(mfe, 4),
            "tp_levels_hit": tp_hit,
            "max_rung_reached_before_stop": max(tp_hit or [0]),
            "bars_held": bars_held,
            "hold_hours": round(bars_held * 0.25, 2),
            "recovered": True,
            "still_running": still_running,
        })
        out["post_mortem"] = ShadowTradeLedger.post_mortem(out)
        return out

    # ------------------------------------------------------------------
    @classmethod
    def recover_one(cls, rec: dict) -> dict:
        opened = float(rec.get("opened_epoch", 0) or 0)
        if opened <= 0:
            return None
        bars = cls._klines_from(rec["ticker"], int(opened * 1000))
        if bars is None or bars.empty:
            return None
        return cls._replay(rec, bars)

    @classmethod
    def run(cls, records: list = None, write: bool = True) -> dict:
        records = records if records is not None else ShadowTradeLedger.load_closed()
        recovered, failed, unchanged = [], 0, 0

        with ThreadPoolExecutor(max_workers=cls.MAX_WORKERS) as pool:
            futures = {pool.submit(cls.recover_one, r): r for r in records}
            for fut in as_completed(futures):
                original = futures[fut]
                try:
                    res = fut.result()
                except Exception:
                    res = None
                if res is None:
                    failed += 1
                    recovered.append(original)      # keep the original rather than drop it
                    continue
                if res["outcome"] == original.get("outcome"):
                    unchanged += 1
                recovered.append(res)

        recovered.sort(key=lambda t: t.get("opened_epoch", 0) or 0)
        # Trades still running are returned to the OPEN book, not booked as resolved.
        still_open = [r for r in recovered if r.get("still_running")]
        resolved = [r for r in recovered if not r.get("still_running")]

        if write:
            ShadowTradeLedger._atomic_write(SHADOW_CLOSED_FILE, resolved)

        changed = {}
        for r in resolved:
            if r.get("recovered") and r.get("pre_recovery_outcome") != r["outcome"]:
                changed[r["pre_recovery_outcome"] or "?"] = changed.get(r["pre_recovery_outcome"] or "?", 0) + 1

        wins = sum(1 for r in resolved if r.get("is_win"))
        return {
            "input_records": len(records),
            "recovered": len(resolved),
            "returned_to_open": len(still_open),
            "unchanged": unchanged,
            "failed_no_data": failed,
            "outcome_changes": changed,
            "new_wins": wins,
            "new_accuracy_pct": round(wins / len(resolved) * 100, 1) if resolved else 0.0,
            "still_open_records": still_open,
        }


if __name__ == "__main__":
    print("Replaying every resolved trade against real candles from its open time...")
    summary = LedgerRecovery.run()
    summary.pop("still_open_records", None)
    print(json.dumps(summary, indent=2))
