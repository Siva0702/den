# models/audit/backtester.py
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from indicators.confluence_engine import SureShotConfluenceEngine
from indicators.liquidity_map import LiquidityMapEngine
from audit.shadow_ledger import ShadowTradeLedger

BACKTEST_FILE = "audit/backtest_closed.json"

class WalkForwardBacktester:
    """
    Den Engine v39.2 Walk-Forward Backtester.

    The calibration layer needs resolved outcomes before it can quote a win rate, and
    collecting those live takes weeks. Exchanges will hand over 1500 bars of 15m history
    for free — about 15 days — so instead of waiting, we replay it.

    For each asset the engine walks forward bar by bar. At each step it sees ONLY the
    bars up to that point, scores the setup exactly as the live scanner would, places a
    liquidity-aware stop and a TP ladder, then walks the FUTURE bars to see what actually
    happened. Resolution uses the same event-order logic as the live shadow ledger, so a
    trade stopped before target is a loss, not a win.

    HONEST LIMITATION, and it matters: derivatives positioning, news and the event
    calendar have no free historical feed, so backtested setups are scored on TECHNICALS
    ONLY. Every record is tagged `source: backtest` and `context_complete: false`. Live
    shadow trades carry the full context. Calibration keeps them separable so the two are
    never silently blended into one over-confident number.

    What this unlocks immediately:
      - a measured win rate per score bin instead of "—"
      - which TP level is actually worth targeting, measured rather than assumed
      - stop distances derived from real winner drawdown
    """

    LOOKBACK_BARS = 1500
    WARMUP = 260          # bars the engine needs before it can score anything
    STEP = 8              # sample every 8 bars (2h) so setups do not overlap heavily
    MAX_HOLD_BARS = 96    # 24h on 15m
    SHADOW_FLOOR = 40.0
    # The rung that DEFINES a win. Resolving on TP4 made the engine call a trade that
    # banked TP1 and TP2 a "loss", producing a 13.4% win rate that measured nothing but
    # how rarely 4R prints. Measured expectancy says TP1 is the correct planned exit.
    PRIMARY_TP_INDEX = 0
    MAX_WORKERS = 4

    # ------------------------------------------------------------------
    @staticmethod
    def _resample(df: pd.DataFrame, factor: int) -> pd.DataFrame:
        """Build a higher timeframe from 15m bars (1h=4, 4h=16, 1d=96)."""
        if len(df) < factor * 2:
            return None
        usable = len(df) - (len(df) % factor)
        chunk = df.iloc[:usable]
        g = chunk.groupby(chunk.index // factor)
        out = pd.DataFrame({
            'open': g['open'].first(), 'high': g['high'].max(),
            'low': g['low'].min(), 'close': g['close'].last(),
            'volume': g['volume'].sum(),
        }).reset_index(drop=True)
        return out if len(out) >= 50 else None

    # ------------------------------------------------------------------
    @classmethod
    def _simulate(cls, future: pd.DataFrame, direction: str, entry: float,
                  sl: float, ladder: list) -> dict:
        """
        Walk the future bars and resolve exactly as the live ledger does — on event
        ORDER. Within a single bar we cannot know whether the high or the low came
        first, so we resolve pessimistically: if a bar touches both the stop and a
        target, the stop is assumed to have hit first. That biases the backtest
        against us, which is the only safe direction for it to be wrong.
        """
        mae = mfe = 0.0
        won_at = None
        sl_touched = False
        first_tp_bar = None
        tp_hit = []
        primary_tp = float(ladder[cls.PRIMARY_TP_INDEX]) if ladder else None
        final_tp = primary_tp

        for i in range(len(future)):
            bar = future.iloc[i]
            hi, lo = float(bar['high']), float(bar['low'])

            if direction == "LONG":
                mae = min(mae, (lo - entry) / entry * 100)
                mfe = max(mfe, (hi - entry) / entry * 100)
                hit_sl = lo <= sl
                reached = [j + 1 for j, tp in enumerate(ladder) if hi >= float(tp)]
            else:
                mae = min(mae, (entry - hi) / entry * 100)
                mfe = max(mfe, (entry - lo) / entry * 100)
                hit_sl = hi >= sl
                reached = [j + 1 for j, tp in enumerate(ladder) if lo <= float(tp)]

            new_tp = [r for r in reached if r not in tp_hit]
            # Once the planned exit is banked the position is closed at target; a later
            # stop touch cannot turn it back into a loss.
            if hit_sl and won_at is not None:
                return {"outcome": "TP_THEN_SL", "exit": primary_tp, "is_win": True,
                        "mae_pct": mae, "mfe_pct": mfe, "tp_hit": tp_hit, "bars_held": won_at}
            if hit_sl and not sl_touched:
                sl_touched = True
                # Pessimistic intrabar ordering: stop first unless a target was
                # already banked on an earlier bar.
                if first_tp_bar is None:
                    exit_px = sl
                    # keep walking to learn whether it would have reached target later
                    for k in range(i + 1, len(future)):
                        b2 = future.iloc[k]
                        if final_tp is not None:
                            if (direction == "LONG" and float(b2['high']) >= final_tp) or \
                               (direction == "SHORT" and float(b2['low']) <= final_tp):
                                return {"outcome": "SL_THEN_TP", "exit": exit_px, "is_win": False,
                                        "mae_pct": mae, "mfe_pct": mfe, "tp_hit": tp_hit,
                                        "bars_held": i + 1}
                    return {"outcome": "SL_HIT", "exit": exit_px, "is_win": False,
                            "mae_pct": mae, "mfe_pct": mfe, "tp_hit": tp_hit, "bars_held": i + 1}
                return {"outcome": "PARTIAL_THEN_SL", "exit": sl, "is_win": False,
                        "mae_pct": mae, "mfe_pct": mfe, "tp_hit": tp_hit, "bars_held": i + 1}

            if new_tp:
                tp_hit.extend(new_tp)
                if first_tp_bar is None:
                    first_tp_bar = i
                # Do NOT return here. The trade is won, but we keep walking so every
                # higher rung's reach rate is measured. Returning at TP1 made TP2-TP4
                # look unreachable when in fact we had simply stopped observing.
                if primary_tp is not None and max(tp_hit) >= cls.PRIMARY_TP_INDEX + 1:
                    if won_at is None:
                        won_at = i + 1
                    if max(tp_hit) >= len(ladder):
                        return {"outcome": "TP_ALL_RUNGS", "exit": primary_tp, "is_win": True,
                                "mae_pct": mae, "mfe_pct": mfe, "tp_hit": tp_hit,
                                "bars_held": won_at}

        if won_at is not None:
            return {"outcome": "TP_PARTIAL", "exit": primary_tp, "is_win": True,
                    "mae_pct": mae, "mfe_pct": mfe, "tp_hit": tp_hit, "bars_held": won_at}
        last = float(future.iloc[-1]['close'])
        return {"outcome": "TIMEOUT", "exit": last, "is_win": False,
                "mae_pct": mae, "mfe_pct": mfe, "tp_hit": tp_hit, "bars_held": len(future)}

    # ------------------------------------------------------------------
    @classmethod
    def backtest_asset(cls, ticker: str, base_price: float = 100.0) -> list:
        from data.exchange_feed import BitunixWeexLiveFeed
        df, real = BitunixWeexLiveFeed.get_exchange_ohlcv(ticker, base_price, "15m",
                                                          limit=cls.LOOKBACK_BARS)
        if df is None or not real or len(df) < cls.WARMUP + cls.MAX_HOLD_BARS + 50:
            return []

        results = []
        end = len(df) - cls.MAX_HOLD_BARS
        for i in range(cls.WARMUP, end, cls.STEP):
            hist = df.iloc[:i].reset_index(drop=True)
            try:
                sig = SureShotConfluenceEngine.evaluate_setup(
                    ohlcv_15m=hist,
                    ohlcv_1h=cls._resample(hist, 4),
                    ohlcv_4h=cls._resample(hist, 16),
                    ohlcv_1d=cls._resample(hist, 96),
                    btc_df=None, ticker=ticker, efficiency_history=None,
                    derivatives=None, news=None, calendar=None, event_vol=None)
            except Exception:
                continue

            score = sig.get("total_score", 0.0)
            direction = sig.get("direction", "NONE")
            if direction == "NONE" or score < cls.SHADOW_FLOOR:
                continue

            entry = float(hist['close'].iloc[-1])
            atr = sig.get("atr", entry * 0.01)
            try:
                stop = LiquidityMapEngine.safe_stop_loss(hist, direction, entry, atr,
                                                         liquidity=sig.get("liquidity"))
            except Exception:
                continue
            sl = stop["stop_loss"]
            risk = abs(entry - sl)
            if risk <= 0 or stop["sl_pct"] < 0.001:
                continue

            mult = [1.0, 1.8, 2.6, 4.0]
            ladder = [entry + risk * m for m in mult] if direction == "LONG" else \
                     [entry - risk * m for m in mult]

            future = df.iloc[i:i + cls.MAX_HOLD_BARS].reset_index(drop=True)
            sim = cls._simulate(future, direction, entry, sl, ladder)

            rec = {
                "shadow_id": f"BT-{ticker.replace('/', '')}-{i}",
                "source": "backtest",
                "context_complete": False,
                "ticker": ticker, "direction": direction, "entry": entry,
                "stop_loss": sl, "tp_ladder": ladder, "raw_score": score,
                "opened_epoch": time.time(), "closed_epoch": time.time(),
                "features": sig.get("feature_snapshot", {}),
                "factors_passed": sig.get("factors_passed", []),
                "factors_failed": sig.get("factors_failed", []),
                "market_regime": sig.get("market_regime", "UNKNOWN"),
                "session": "BACKTEST",
                "timeframe_alignment": sig.get("timeframe_alignment", 0),
                "mae_pct": round(sim["mae_pct"], 4),
                "mfe_pct": round(sim["mfe_pct"], 4),
                "tp_levels_hit": sim["tp_hit"],
                "tp_levels_hit_count": len(sim["tp_hit"]),
                "outcome": sim["outcome"], "is_win": sim["is_win"],
                "exit_price": sim["exit"], "bars_held": sim["bars_held"],
                "hold_hours": round(sim["bars_held"] * 0.25, 2),
                "pnl_pct": round(((sim["exit"] - entry) / entry * 100) if direction == "LONG"
                                 else ((entry - sim["exit"]) / entry * 100), 4),
            }
            rec["post_mortem"] = ShadowTradeLedger.post_mortem(rec)
            results.append(rec)
        return results

    # ------------------------------------------------------------------
    @classmethod
    def run(cls, universe=None, max_assets: int = None) -> dict:
        if universe is None:
            from news.market_universe import DynamicMarketUniverse
            universe = DynamicMarketUniverse.get_full_hunting_universe()
        if max_assets:
            universe = universe[:max_assets]

        all_results = []
        done = 0
        with ThreadPoolExecutor(max_workers=cls.MAX_WORKERS) as pool:
            futures = {pool.submit(cls.backtest_asset, it["ticker"], it.get("base_price", 100.0)): it["ticker"]
                       for it in universe}
            for fut in as_completed(futures):
                done += 1
                try:
                    res = fut.result()
                    all_results.extend(res)
                    if res:
                        print(f"  [{done}/{len(universe)}] {futures[fut]:14} {len(res):4d} setups", flush=True)
                except Exception as e:
                    print(f"  [!] {futures[fut]}: {type(e).__name__}: {e}", flush=True)

        os.makedirs("audit", exist_ok=True)
        with open(BACKTEST_FILE, "w") as f:
            json.dump(all_results, f, default=str)

        return cls.summarise(all_results)

    # ------------------------------------------------------------------
    @classmethod
    def summarise(cls, records: list = None) -> dict:
        if records is None:
            records = cls.load()
        if not records:
            return {"total": 0}

        wins = sum(1 for r in records if r["is_win"])
        by_outcome = {}
        for r in records:
            by_outcome[r["outcome"]] = by_outcome.get(r["outcome"], 0) + 1

        # TP-level reach rates: how often each rung is actually touched.
        tp_reach = {}
        for lvl in (1, 2, 3, 4):
            n = sum(1 for r in records if lvl in (r.get("tp_levels_hit") or []))
            tp_reach[f"TP{lvl}"] = round(n / len(records) * 100, 1)

        return {
            "total": len(records),
            "wins": wins,
            "win_rate": round(wins / len(records) * 100, 1),
            "outcomes": by_outcome,
            "tp_reach_pct": tp_reach,
            "avg_mae_pct": round(sum(abs(r["mae_pct"]) for r in records) / len(records), 3),
            "avg_mfe_pct": round(sum(abs(r["mfe_pct"]) for r in records) / len(records), 3),
        }

    @classmethod
    def load(cls) -> list:
        if not os.path.exists(BACKTEST_FILE):
            return []
        try:
            with open(BACKTEST_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return []

    # ------------------------------------------------------------------
    @classmethod
    def optimal_tp_analysis(cls, records: list = None) -> dict:
        """
        Answers 'which TP should I actually take?' with measured expectancy per rung
        instead of a rule of thumb. Expectancy is in R (risk units), so the rungs are
        directly comparable: exiting everything at TP1 earns 1R when it hits and -1R
        when it does not.
        """
        records = records if records is not None else cls.load()
        if not records:
            return {"available": False}

        mult = {1: 1.0, 2: 1.8, 3: 2.6, 4: 4.0}
        out = {}
        for lvl, r_mult in mult.items():
            hits = sum(1 for r in records if lvl in (r.get("tp_levels_hit") or []))
            n = len(records)
            p = hits / n
            expectancy = p * r_mult - (1 - p) * 1.0
            out[f"TP{lvl}"] = {
                "r_multiple": r_mult,
                "reach_rate_pct": round(p * 100, 1),
                "expectancy_R": round(expectancy, 3),
                "verdict": "PROFITABLE" if expectancy > 0 else "LOSS-MAKING",
            }
        best = max(out.items(), key=lambda kv: kv[1]["expectancy_R"])
        return {"available": True, "levels": out, "best_exit": best[0],
                "best_expectancy_R": best[1]["expectancy_R"], "sample": len(records)}


if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    print(f"Walk-forward backtest starting (assets={limit or 'all'})...", flush=True)
    t0 = time.time()
    summary = WalkForwardBacktester.run(max_assets=limit)
    print(f"\nCompleted in {time.time() - t0:.0f}s")
    print(json.dumps(summary, indent=2))
    print("\nOptimal TP analysis:")
    print(json.dumps(WalkForwardBacktester.optimal_tp_analysis(), indent=2))
