# models/indicators/micro_entry.py
import numpy as np
import pandas as pd

class MicroEntryEngine:
    """
    Den Engine v41.1 One-Minute Entry Quality.

    The engine scores on 15m candles, so by the time a break of structure is confirmed
    the move is already up to fifteen minutes old. Everything that happened inside that
    candle — the thrust, the exhaustion wick, the deceleration — is invisible.

    That blindness has a measurable signature. The three setups that scored above 78
    all died with MFE of +0.08%, +0.12% and +0.09%: they never moved in our favour at
    all. Maximum confluence on a 15m chart appears to mean the move has already
    happened, and we are buying the end of it.

    This module reads the same moment at 1-minute resolution and asks four questions a
    15m candle cannot answer:

      EXTENSION    how far is price stretched from its own 1m VWAP, in ATR units?
                   Entering 3 ATR above VWAP is chasing, whatever the 15m says.

      MATURITY     how much of the current 15m candle's range is already spent?
                   Entering at 90% of the bar's range leaves almost nothing left.

      MOMENTUM     is the 1m thrust still accelerating, or already decaying?
                   A decelerating push into a "confirmed breakout" is exhaustion.

      EXHAUSTION   is the most recent 1m action printing rejection wicks against
                   the intended direction?

    Output is an entry-quality score and a bounded penalty. It never adds points — a
    good micro entry is the expected case, so this can only reduce a score that the 15m
    picture has already inflated.
    """

    MAX_PENALTY = 14.0

    # ------------------------------------------------------------------
    @staticmethod
    def _atr(df: pd.DataFrame, period: int = 14) -> float:
        tr = pd.concat([df['high'] - df['low'],
                        (df['high'] - df['close'].shift()).abs(),
                        (df['low'] - df['close'].shift()).abs()], axis=1).max(axis=1)
        v = tr.rolling(period).mean().iloc[-1]
        return float(v) if not np.isnan(v) else 0.0

    # ------------------------------------------------------------------
    @classmethod
    def analyze(cls, df_1m: pd.DataFrame, direction: str,
                df_15m: pd.DataFrame = None) -> dict:
        if df_1m is None or len(df_1m) < 30 or direction not in ("LONG", "SHORT"):
            return {"available": False, "penalty": 0.0, "entry_quality": None}

        close = df_1m['close']
        price = float(close.iloc[-1])
        atr1 = cls._atr(df_1m)
        if atr1 <= 0 or price <= 0:
            return {"available": False, "penalty": 0.0, "entry_quality": None}

        penalty = 0.0
        flags = []

        # --- 1. EXTENSION from 1m VWAP ---------------------------------
        tp = (df_1m['high'] + df_1m['low'] + df_1m['close']) / 3.0
        vol = df_1m['volume'].replace(0, np.nan)
        vwap = float((tp * vol).sum() / vol.sum()) if vol.sum() > 0 else float(close.mean())
        ext_atr = (price - vwap) / atr1
        # Positive extension is bad for a LONG, good-ish for a SHORT, and vice versa.
        against = ext_atr if direction == "LONG" else -ext_atr
        if against > 2.5:
            p = min((against - 2.5) * 3.0, 6.0)
            penalty += p
            flags.append(f"Extended {against:.1f} ATR from 1m VWAP — chasing [{p:+.1f}]")

        # --- 2. MATURITY of the current 15m move ------------------------
        maturity = None
        if df_15m is not None and len(df_15m) >= 2:
            bar = df_15m.iloc[-1]
            rng = float(bar['high']) - float(bar['low'])
            if rng > 0:
                pos = (price - float(bar['low'])) / rng
                maturity = pos if direction == "LONG" else 1.0 - pos
                if maturity > 0.80:
                    p = min((maturity - 0.80) * 25.0, 5.0)
                    penalty += p
                    flags.append(f"Entering at {maturity*100:.0f}% of the 15m bar range — "
                                 f"little room left [{p:+.1f}]")

        # --- 3. MOMENTUM decay over the last 10 minutes -----------------
        ema5 = close.ewm(span=5, adjust=False).mean()
        recent = float(ema5.iloc[-1] - ema5.iloc[-5])
        prior = float(ema5.iloc[-5] - ema5.iloc[-10])
        signed_recent = recent if direction == "LONG" else -recent
        signed_prior = prior if direction == "LONG" else -prior
        decaying = signed_prior > 0 and signed_recent < signed_prior * 0.4
        if decaying:
            penalty += 4.0
            flags.append("1m momentum decaying into the entry — thrust is fading [+4.0]")

        # --- 4. EXHAUSTION wicks against the trade ----------------------
        last3 = df_1m.iloc[-3:]
        wick_ratio = 0.0
        for _, b in last3.iterrows():
            rng = max(float(b['high']) - float(b['low']), 1e-12)
            body_hi = max(float(b['open']), float(b['close']))
            body_lo = min(float(b['open']), float(b['close']))
            w = (float(b['high']) - body_hi) / rng if direction == "LONG" \
                else (body_lo - float(b['low'])) / rng
            wick_ratio = max(wick_ratio, w)
        if wick_ratio > 0.55:
            p = min((wick_ratio - 0.55) * 12.0, 4.0)
            penalty += p
            flags.append(f"Rejection wick {wick_ratio*100:.0f}% of a 1m bar against the "
                         f"trade [{p:+.1f}]")

        penalty = round(min(penalty, cls.MAX_PENALTY), 2)
        quality = round(max(0.0, 100.0 - (penalty / cls.MAX_PENALTY) * 100.0), 1)

        return {
            "available": True,
            "entry_quality": quality,
            "penalty": penalty,
            "extension_atr": round(against, 2),
            "bar_maturity": round(maturity, 3) if maturity is not None else None,
            "momentum_decaying": bool(decaying),
            "rejection_wick": round(wick_ratio, 3),
            "flags": flags,
            "verdict": ("LATE — move already spent" if penalty >= 9 else
                        "STRETCHED" if penalty >= 4 else "CLEAN"),
        }
