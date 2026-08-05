# models/indicators/liquidity_map.py
import numpy as np
import pandas as pd

class LiquidityMapEngine:
    """
    Den Engine v39.0 Liquidity Pool Mapping & Stop-Hunt Defense.

    Stops do not sit at random prices. They cluster in predictable places: just beyond
    equal highs and equal lows, beyond obvious swing points, and at round numbers. Market
    makers push price into those pools precisely because that is where the resting orders
    are. A stop placed INSIDE a pool is not a stop, it is a donation.

    The old engine placed SL at a flat 1.5x ATR with no knowledge of where the pools were,
    which is the direct mechanism behind "hit SL first, then TP" — the trade thesis was
    fine, the stop was parked in the hunting ground.

    This module does three things:

      1. Maps the liquidity pools above and below current price.
      2. Places the stop BEYOND the nearest pool plus an ATR buffer, so a routine sweep
         cannot reach it.
      3. Reports whether price is currently sitting just in FRONT of an unswept pool,
         which is the single worst moment to enter — and whether a sweep has already
         completed and reclaimed, which is the best.
    """

    # ------------------------------------------------------------------
    @staticmethod
    def _swing_points(df: pd.DataFrame, left: int = 2, right: int = 2) -> tuple:
        """Fractal swing highs/lows: a bar higher (lower) than `left` before and `right` after."""
        highs, lows = [], []
        h = df['high'].values
        l = df['low'].values
        n = len(df)
        for i in range(left, n - right):
            window_h = h[i - left:i + right + 1]
            window_l = l[i - left:i + right + 1]
            if h[i] == window_h.max() and (window_h == h[i]).sum() == 1:
                highs.append((i, float(h[i])))
            if l[i] == window_l.min() and (window_l == l[i]).sum() == 1:
                lows.append((i, float(l[i])))
        return highs, lows

    # ------------------------------------------------------------------
    @classmethod
    def _cluster(cls, points: list, tolerance_pct: float) -> list:
        """
        Group swing points that sit within tolerance of each other. Two or more swings at
        effectively the same price = equal highs/lows = a stop cluster worth hunting.
        """
        if not points:
            return []
        ordered = sorted(points, key=lambda p: p[1])
        clusters = []
        current = [ordered[0]]

        for idx, price in ordered[1:]:
            ref = current[-1][1]
            if ref > 0 and abs(price - ref) / ref <= tolerance_pct:
                current.append((idx, price))
            else:
                clusters.append(current)
                current = [(idx, price)]
        clusters.append(current)

        pools = []
        for c in clusters:
            prices = [p for _, p in c]
            pools.append({
                "price": float(np.mean(prices)),
                "touches": len(c),
                "last_index": max(i for i, _ in c),
                # More touches and more recent = more stops resting there.
                "strength": len(c) * (1.0 + max(i for i, _ in c) / 100.0),
            })
        return pools

    # ------------------------------------------------------------------
    @classmethod
    def map_liquidity(cls, df: pd.DataFrame, atr: float, lookback: int = 80) -> dict:
        """Build the pool map above and below current price."""
        if df is None or len(df) < 30:
            return {"available": False}

        data = df.iloc[-lookback:].reset_index(drop=True)
        close = float(data['close'].iloc[-1])
        if close <= 0:
            return {"available": False}

        # Tolerance scales with volatility: in a wide-ATR market "equal" is looser.
        tol = max((atr / close) * 0.35, 0.0008) if atr and atr > 0 else 0.0015

        swing_highs, swing_lows = cls._swing_points(data)
        high_pools = cls._cluster(swing_highs, tol)
        low_pools = cls._cluster(swing_lows, tol)

        above = sorted([p for p in high_pools if p["price"] > close], key=lambda p: p["price"])
        below = sorted([p for p in low_pools if p["price"] < close], key=lambda p: p["price"], reverse=True)

        # Round-number magnets. Traders bunch stops on round figures.
        magnitude = 10 ** int(np.floor(np.log10(close))) if close > 0 else 1
        step = magnitude / 2.0
        round_above = float(np.ceil(close / step) * step)
        round_below = float(np.floor(close / step) * step)

        nearest_above = above[0] if above else None
        nearest_below = below[0] if below else None

        def distance_atr(level):
            if level is None or not atr or atr <= 0:
                return None
            return round(abs(level["price"] - close) / atr, 2)

        return {
            "available": True,
            "close": close,
            "atr": atr,
            "pools_above": above[:4],
            "pools_below": below[:4],
            "nearest_pool_above": nearest_above,
            "nearest_pool_below": nearest_below,
            "distance_above_atr": distance_atr(nearest_above),
            "distance_below_atr": distance_atr(nearest_below),
            "round_above": round_above,
            "round_below": round_below,
            "tolerance_pct": round(tol * 100, 4),
        }

    # ------------------------------------------------------------------
    @classmethod
    def safe_stop_loss(cls, df: pd.DataFrame, direction: str, entry: float, atr: float,
                       liquidity: dict = None, min_buffer_atr: float = 0.6,
                       calibrated_multiplier: float = None) -> dict:
        """
        Place the stop beyond the nearest opposing liquidity pool.

        For a LONG the danger is a sweep DOWN through the pool below. The stop therefore
        goes below that pool, not above it. If no pool is mapped we fall back to an ATR
        stop, but never tighter than the calibrated floor derived from how much heat real
        winners actually take (see calibration._sl_stats).
        """
        liquidity = liquidity or cls.map_liquidity(df, atr)
        base_mult = calibrated_multiplier if calibrated_multiplier else 1.5
        atr_stop_dist = max(atr * base_mult, entry * 0.006) if atr and atr > 0 else entry * 0.008

        rationale = []
        if direction == "LONG":
            fallback = entry - atr_stop_dist
            pool = liquidity.get("nearest_pool_below") if liquidity.get("available") else None
            if pool and pool["price"] < entry:
                buffered = pool["price"] - max(atr * min_buffer_atr, entry * 0.0015)
                sl = min(fallback, buffered)
                rationale.append(
                    f"Stop parked {abs(entry - sl) / entry * 100:.2f}% below entry — "
                    f"beyond the {pool['touches']}-touch liquidity pool at {pool['price']:.6g}"
                )
            else:
                sl = fallback
                rationale.append(f"No mapped pool below; ATR stop at {base_mult:.2f}x")
        else:
            fallback = entry + atr_stop_dist
            pool = liquidity.get("nearest_pool_above") if liquidity.get("available") else None
            if pool and pool["price"] > entry:
                buffered = pool["price"] + max(atr * min_buffer_atr, entry * 0.0015)
                sl = max(fallback, buffered)
                rationale.append(
                    f"Stop parked {abs(sl - entry) / entry * 100:.2f}% above entry — "
                    f"beyond the {pool['touches']}-touch liquidity pool at {pool['price']:.6g}"
                )
            else:
                sl = fallback
                rationale.append(f"No mapped pool above; ATR stop at {base_mult:.2f}x")

        sl_pct = abs(entry - sl) / entry if entry else 0.0

        # A stop so wide the trade cannot pay is worse than no trade.
        capped = False
        if sl_pct > 0.05:
            sl = entry * (1 - 0.05) if direction == "LONG" else entry * (1 + 0.05)
            sl_pct = 0.05
            capped = True
            rationale.append("Pool-based stop exceeded 5% — capped; setup is too wide to size sensibly")

        return {
            "stop_loss": sl,
            "sl_pct": sl_pct,
            "sl_distance_atr": round(abs(entry - sl) / atr, 2) if atr and atr > 0 else None,
            "capped": capped,
            "rationale": rationale,
        }

    # ------------------------------------------------------------------
    @classmethod
    def hunt_risk(cls, df: pd.DataFrame, direction: str, atr: float, liquidity: dict = None) -> dict:
        """
        Are we entering directly in front of an unswept liquidity pool?

        Entering a LONG when an untouched pool sits half an ATR below is asking to be
        stopped before the move. Conversely, a pool that has ALREADY been swept and
        reclaimed is the highest-quality entry available: the stops are gone, and the
        move that follows has no fuel left beneath it.
        """
        if df is None or len(df) < 20:
            return {"available": False}

        liquidity = liquidity or cls.map_liquidity(df, atr)
        if not liquidity.get("available"):
            return {"available": False}

        warnings = []
        risk = 0.0
        close = liquidity["close"]

        if direction == "LONG":
            dist = liquidity.get("distance_below_atr")
            pool = liquidity.get("nearest_pool_below")
        else:
            dist = liquidity.get("distance_above_atr")
            pool = liquidity.get("nearest_pool_above")

        if pool is not None and dist is not None:
            if dist < 0.5:
                risk += 45
                warnings.append(
                    f"Unswept {pool['touches']}-touch liquidity pool only {dist} ATR away at "
                    f"{pool['price']:.6g} — high probability of a sweep before continuation"
                )
            elif dist < 1.0:
                risk += 20
                warnings.append(f"Liquidity pool {dist} ATR away at {pool['price']:.6g} — sweep risk moderate")

        # Has a sweep already completed and reclaimed? That is the safe entry.
        recent = df.iloc[-6:]
        prior = df.iloc[-30:-6]
        reclaimed = False
        if len(prior) >= 10:
            if direction == "LONG":
                prior_low = float(prior['low'].min())
                swept = float(recent['low'].min()) < prior_low
                reclaimed = swept and close > prior_low
            else:
                prior_high = float(prior['high'].max())
                swept = float(recent['high'].max()) > prior_high
                reclaimed = swept and close < prior_high

        if reclaimed:
            risk = max(0.0, risk - 35)
            warnings.append("Liquidity already swept and reclaimed — stops below are cleared, entry timing favourable")

        # Round-number proximity.
        rn = liquidity["round_below"] if direction == "LONG" else liquidity["round_above"]
        if atr and atr > 0 and abs(close - rn) / atr < 0.35:
            risk += 12
            warnings.append(f"Price within 0.35 ATR of round number {rn:.6g} — magnet for resting stops")

        risk = max(0.0, min(risk, 100.0))
        return {
            "available": True,
            "hunt_risk_score": round(risk, 1),
            "sweep_reclaimed": reclaimed,
            "verdict": (
                "HIGH_HUNT_RISK" if risk >= 45 else
                "MODERATE_HUNT_RISK" if risk >= 20 else
                "LOW_HUNT_RISK"
            ),
            "warnings": warnings,
        }
