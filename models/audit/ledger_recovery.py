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

    # Symbol AND divisor, mirroring the live feed. Remapping 1000BONKUSDT without
    # dividing by 1000 compared 1000x-scaled candles against an entry recorded at BONK
    # scale, producing MAE readings like -100,115% and a guaranteed false stop-out on
    # every 1000x-prefixed asset. The live feed had the divisor; this did not.
    TICKER_MAP = {
        "PEPE/USDT": ("1000PEPEUSDT", 1000.0),
        "SHIB/USDT": ("1000SHIBUSDT", 1000.0),
        "BONK/USDT": ("1000BONKUSDT", 1000.0),
        "MATIC/USDT": ("POLUSDT", 1.0),
    }

    # ------------------------------------------------------------------
    @classmethod
    def _klines_from(cls, ticker: str, start_ms: int, interval: str = "1m") -> pd.DataFrame:
        """
        Candles from start_ms to now. Defaults to 1m.

        On a 15m candle we cannot tell whether the high or the low came first, so the
        replay had to assume the stop was hit first — biasing every ambiguous trade into
        a loss. At 1m resolution there are 15 observations inside each 15m candle, so the
        ORDER of events is directly observable and almost no ambiguity remains. Binance
        USD-M does not publish sub-minute klines, so 1m is the finest available.
        """
        mapped = cls.TICKER_MAP.get(ticker)
        sym, div = mapped if mapped else (ticker.replace("/", "").upper(), 1.0)
        bybit_iv = {"1m": "1", "5m": "5", "15m": "15"}.get(interval, "1")
        attempts = [
            ("binance", f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}"
                        f"&interval={interval}&startTime={start_ms}&limit=1000"),
            ("bybit", f"https://api.bybit.com/v5/market/kline?category=linear&symbol={sym}"
                      f"&interval={bybit_iv}&start={start_ms}&limit=1000"),
            ("bitget", f"https://api.bitget.com/api/v2/mix/market/candles?symbol={sym}"
                       f"&granularity={interval}&startTime={start_ms}&limit=1000"
                       f"&productType=USDT-FUTURES"),
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
                        "timestamp": int(k[0]), "open": float(k[1]) / div, "high": float(k[2]) / div,
                        "low": float(k[3]) / div, "close": float(k[4]) / div, "volume": float(k[5]) * div}
                        for k in rows])
                if name == "bitget":
                    kl = data.get("data") or []
                    if not kl:
                        continue
                    return pd.DataFrame([{
                        "timestamp": int(k[0]), "open": float(k[1]) / div, "high": float(k[2]) / div,
                        "low": float(k[3]) / div, "close": float(k[4]) / div, "volume": float(k[5]) * div}
                        for k in kl])
                kl = (data.get("result") or {}).get("list") or []
                if not kl:
                    continue
                return pd.DataFrame([{
                    "timestamp": int(k[0]), "open": float(k[1]) / div, "high": float(k[2]) / div,
                    "low": float(k[3]) / div, "close": float(k[4]) / div, "volume": float(k[5]) * div}
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

        # TRAIL SITS ONE RUNG BEHIND THE HIGHEST TARGET REACHED.
        #   no rung yet -> original stop      (a hit here is a real LOSS)
        #   TP1 reached -> trail at ENTRY     (a hit here is BREAKEVEN, not a loss)
        #   TP2 reached -> trail at TP1       (locks +1R)
        #   TP3 reached -> trail at TP2
        #   TP4 reached -> close at TP4
        # Trailing TO the level just touched was wrong: the trail would sit exactly at
        # current price and trigger on the same candle, which is why every trade died at
        # TP1 and why 16 "stop-outs" were counted as losses when many were breakeven or
        # better. A trade that reaches TP1 can no longer lose.
        # TRAIL MOVES TO THE RUNG JUST REACHED — matching the live ledger and the
        # specified rule: "TP1 hit, temporary SL becomes TP1; if that is hit before TP2,
        # the result is TP1". I had implemented one-rung-behind here only, so the replay
        # and the live path were resolving the SAME trade differently and both were being
        # stamped v4. 195 records used the live rule, 8 the replay rule.
        # Same-candle triggering is prevented by the arming delay below, not by
        # sacrificing a whole rung of profit.
        def trail_level(n):
            if n <= 0:
                return sl
            return ladder[n - 1]           # the rung just reached

        for i in range(len(bars)):
            hi = float(bars.iloc[i]["high"])
            lo = float(bars.iloc[i]["low"])

            if direction == "LONG":
                mae = min(mae, (lo - entry) / entry * 100)
                mfe = max(mfe, (hi - entry) / entry * 100)
            else:
                mae = min(mae, (entry - hi) / entry * 100)
                mfe = max(mfe, (entry - lo) / entry * 100)

            n = max(tp_hit) if tp_hit else 0
            lvl = trail_level(n)
            # A rung arms one bar after it is tagged: price reaches a target from below,
            # so that bar's low sits under it by definition.
            if n > 0 and armed != n:
                armed = n
                reached_now = ([j + 1 for j, tp in enumerate(ladder) if hi >= tp]
                               if direction == "LONG" else
                               [j + 1 for j, tp in enumerate(ladder) if lo <= tp])
                for r in reached_now:
                    if r not in tp_hit:
                        tp_hit.append(r)
                if tp_hit and max(tp_hit) >= len(ladder):
                    return cls._out(rec, "TP4_HIT", ladder[-1], True, mae, mfe, tp_hit, i + 1)
                continue
            stopped = (lo <= lvl) if direction == "LONG" else (hi >= lvl)

            if stopped:
                if n == 0:
                    for k in range(i + 1, len(bars)):
                        tgt = ladder[0]
                        if ((direction == "LONG" and float(bars.iloc[k]["high"]) >= tgt) or
                                (direction == "SHORT" and float(bars.iloc[k]["low"]) <= tgt)):
                            return cls._out(rec, "SL_THEN_TP", sl, False, mae, mfe, tp_hit, i + 1)
                    return cls._out(rec, "SL_HIT", sl, False, mae, mfe, tp_hit, i + 1)
                return cls._out(rec, f"TP{n}_HIT", lvl, True, mae, mfe, tp_hit, i + 1)

            reached = ([j + 1 for j, tp in enumerate(ladder) if hi >= tp] if direction == "LONG"
                       else [j + 1 for j, tp in enumerate(ladder) if lo <= tp])
            for r in reached:
                if r not in tp_hit:
                    tp_hit.append(r)
            if tp_hit and max(tp_hit) >= len(ladder):
                return cls._out(rec, "TP4_HIT", ladder[-1], True, mae, mfe, tp_hit, i + 1)

        n = max(tp_hit) if tp_hit else 0
        if n >= 1:
            return cls._out(rec, f"TP{n}_RUNNING", trail_level(n), True, mae, mfe, tp_hit, len(bars), True)
        return cls._out(rec, "STILL_OPEN", float(bars.iloc[-1]["close"]), False,
                        mae, mfe, tp_hit, len(bars), True)

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
            "is_win": (None if is_win is None else bool(is_win)),
            "is_breakeven": is_win is None,
            "exit_price": exit_px,
            "pnl_pct": round(pnl, 4),
            "mae_pct": round(mae, 4),
            "mfe_pct": round(mfe, 4),
            "tp_levels_hit": tp_hit,
            "max_rung_reached_before_stop": max(tp_hit or [0]),
            "bars_held": bars_held,
            "hold_hours": round(bars_held / 60.0, 2),
            "resolution": "1m",
            "recovered": True,
            "logic_version": ShadowTradeLedger.LOGIC_VERSION,
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
        # DROP THE ENTRY CANDLE. startTime returns the candle CONTAINING the open, whose
        # high/low include movement from before the trade existed — so a stop 0.7% away
        # was being triggered by a 5.8% range that had already happened. We cannot know
        # where inside that candle the entry landed, so the only honest choice is to
        # begin at the next candle and forgo any outcome the entry bar might have
        # produced. 8 of 16 recovered stop-outs were this artefact.
        # At 1m resolution the entry minute is a far smaller blind spot than a whole
        # 15m candle, but it is still ambiguous, so it is dropped.
        bars = bars.iloc[1:].reset_index(drop=True)
        if bars.empty:
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
