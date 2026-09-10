# models/audit/ledger_recovery.py
import copy
from collections import Counter
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from indicators.execution import advance_trade
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
    def replay_result(cls, rec, bars):
        """Diagnostic result, without conflating unavailable and unresolved paths."""
        out = copy.deepcopy(rec)
        for key in ("last_bar_ts", "data_quality_error", "first_tp_epoch"):
            out.pop(key, None)
        out.update(tp_levels_hit=[], bars_observed=0, mae_pct=0.0, mfe_pct=0.0,
                   sl_touched=False, sl_touched_epoch=None)
        event = advance_trade(out, [] if bars is None else bars.to_dict("records"))
        error = out.get("data_quality_error")
        if error:
            return {"status": error, "record": None}
        if event is None:
            return {"status": "no history" if bars is None or bars.empty else "unresolved",
                    "record": None}
        out.update(event)
        out.update(recovered=True, still_running=False,
                   pre_recovery_outcome=rec.get("outcome"),
                   pre_recovery_pnl_pct=rec.get("pnl_pct"),
                   tp_levels_hit_count=len(out["tp_levels_hit"]),
                   max_rung_reached_before_stop=max(out["tp_levels_hit"] or [0]))
        out["post_mortem"] = ShadowTradeLedger.post_mortem(out)
        return {"status": "resolved", "record": out}

    @classmethod
    def _replay(cls, rec, bars):
        return cls.replay_result(rec, bars)["record"]

    @classmethod
    def recover_result(cls, rec):
        from indicators.execution import finite
        opened = finite(rec.get("opened_epoch"), 0)
        if opened <= 0:
            return {"status": "invalid opened_epoch", "record": None}
        from data.exchange_feed import BitunixWeexLiveFeed
        rows = BitunixWeexLiveFeed.execution_history(rec["ticker"], opened,
                    provider=(rec.get("entry_provenance") or {}).get("provider"))
        return cls.replay_result(rec, pd.DataFrame(rows) if rows else None)

    @classmethod
    def recover_one(cls, rec):
        return cls.recover_result(rec)["record"]

    @classmethod
    def run(cls, records=None, write=False):
        records = records if records is not None else ShadowTradeLedger.load_closed()
        results = [None] * len(records)
        with ThreadPoolExecutor(max_workers=cls.MAX_WORKERS) as pool:
            futures = {pool.submit(cls.recover_result, r): i for i, r in enumerate(records)}
            for future in as_completed(futures):
                i = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = {"status": "exception:"+type(exc).__name__, "record": None}
                results[i] = dict(result, shadow_id=records[i].get("shadow_id"), index=i)
        counts = Counter(r["status"] for r in results)
        resolved = [r["record"] for r in results if r["status"] == "resolved"]
        # No partial, mixed-version rewrite and no pretend transfer to the open book.
        if write:
            if len(resolved) != len(records):
                raise ValueError("Incomplete replay: original ledger preserved; inspect status_counts")
            if any(not (r.get("entry_provenance") or {}).get("provider") for r in records):
                raise ValueError("Entry venue provenance missing: replay is research only")
            ShadowTradeLedger._atomic_write(SHADOW_CLOSED_FILE, resolved)
        changes = Counter(r.get("pre_recovery_outcome") or "?" for r in resolved
                          if r.get("pre_recovery_outcome") != r["outcome"])
        wins = sum(bool(r.get("is_win")) for r in resolved)
        return {"input_records": len(records), "recovered": len(resolved),
                "status_counts": dict(counts), "preserved_originals": len(records)-len(resolved),
                "unchanged": sum(r.get("pre_recovery_outcome") == r["outcome"] for r in resolved),
                "outcome_changes": dict(changes), "new_wins": wins,
                "new_accuracy_pct": round(wins/len(resolved)*100, 1) if resolved else None,
                "returned_to_open": 0, "still_open_records": [],
                "failed_no_data": counts["no history"], "results": results}


if __name__ == "__main__":
    print("Dry-run only: replaying without changing the ledger.")
    summary = LedgerRecovery.run(write=False)
    summary.pop("results", None)
    print(json.dumps(summary, indent=2))
