# models/indicators/regime_engine.py
import numpy as np
import pandas as pd

class MarketRegimeEngine:
    """
    Den Engine v41.0 Two-Axis Market Regime Classifier.

    The previous classifier measured ONE thing — volatility — and returned
    TRENDING / VOLATILE / RANGING / CHOPPY. It could not tell a bull market from a
    bear market, which is precisely the distinction that matters most for whether a
    long or a short is the right side.

    The cost of that blindness is measurable: across 404 resolved trades, SHORT made
    +85R and LONG lost -51R while BTC moved +0.17% and ETH +1.81%. In a flat market
    the engine was systematically wrong on one side and systematically right on the
    other, and no regime label it produced could express that.

    So regime is now classified on two independent axes:

      DIRECTION   BULL / BEAR / NEUTRAL
                  EMA structure (20/50/200 stack), EMA50 slope, higher-timeframe
                  agreement, and position within the recent range.

      VOLATILITY  EXPANSION / NORMAL / COMPRESSION
                  ATR percentile against its own 100-bar history, plus Bollinger
                  bandwidth percentile. Expansion means moves carry; compression
                  means they revert.

    The pair gives nine states, and they behave very differently:
      BULL_EXPANSION  breakouts follow through, longs work
      BEAR_EXPANSION  breakdowns follow through, shorts work
      NEUTRAL_COMPRESSION  everything mean-reverts; breakout entries are traps

    Nothing here is scored. The classifier only reports state — how much each state is
    worth is measured from outcomes by RegimePerformance, so the engine learns which
    regimes it can actually trade rather than being told.
    """

    # ------------------------------------------------------------------
    @staticmethod
    def _percentile(series: pd.Series, value: float) -> float:
        s = series.dropna()
        if len(s) < 10:
            return 0.5
        return float((s < value).mean())

    # ------------------------------------------------------------------
    @classmethod
    def _direction_axis(cls, df: pd.DataFrame, htf_trends: dict = None) -> dict:
        """
        Directional bias from structure, not from a single indicator.
        Four independent votes so one noisy input cannot flip the label.
        """
        close = df['close']
        c = float(close.iloc[-1])
        ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
        ema50s = close.ewm(span=50, adjust=False).mean()
        ema50 = float(ema50s.iloc[-1])
        ema200 = float(close.ewm(span=200, adjust=False).mean().iloc[-1]) if len(df) >= 150 else ema50

        votes = []
        detail = {}

        # 1. EMA stack
        if c > ema20 > ema50:
            votes.append(1); detail["stack"] = "bullish"
        elif c < ema20 < ema50:
            votes.append(-1); detail["stack"] = "bearish"
        else:
            votes.append(0); detail["stack"] = "mixed"

        # 2. Long-horizon regime filter
        votes.append(1 if c > ema200 else -1)
        detail["above_ema200"] = c > ema200

        # 3. EMA50 slope over 20 bars — a flat EMA is not a trend in either direction
        if len(ema50s) > 20:
            prev = float(ema50s.iloc[-20])
            slope = (ema50 - prev) / abs(prev) if prev else 0.0
            detail["ema50_slope_pct"] = round(slope * 100, 3)
            votes.append(1 if slope > 0.002 else -1 if slope < -0.002 else 0)
        else:
            votes.append(0)

        # 4. Position inside the recent range: near highs is bullish, near lows bearish
        window = df.iloc[-60:] if len(df) >= 60 else df
        hi, lo = float(window['high'].max()), float(window['low'].min())
        pos = (c - lo) / (hi - lo) if hi > lo else 0.5
        detail["range_position"] = round(pos, 3)
        votes.append(1 if pos > 0.66 else -1 if pos < 0.34 else 0)

        # Higher timeframes get double weight — they set the context the 15m trades in.
        if htf_trends:
            for tf in ("1d", "4h"):
                v = htf_trends.get(tf, 0)
                votes.extend([v, v])

        score = sum(votes)
        strength = abs(score) / max(len(votes), 1)
        if score >= 2:
            label = "BULL"
        elif score <= -2:
            label = "BEAR"
        else:
            label = "NEUTRAL"
        return {"label": label, "score": score, "strength": round(strength, 3), "detail": detail}

    # ------------------------------------------------------------------
    @classmethod
    def _volatility_axis(cls, df: pd.DataFrame) -> dict:
        """
        Is volatility expanding or compressing relative to this instrument's OWN
        history? An absolute ATR threshold is meaningless across 87 assets.
        """
        high, low, close = df['high'], df['low'], df['close']
        tr = pd.concat([high - low,
                        (high - close.shift()).abs(),
                        (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        cur_atr = float(atr.iloc[-1]) if not np.isnan(atr.iloc[-1]) else 0.0
        atr_pct = cls._percentile(atr.iloc[-100:], cur_atr)

        # Bollinger bandwidth: independent read on the same question.
        ma = close.rolling(20).mean()
        sd = close.rolling(20).std()
        bw = ((ma + 2 * sd) - (ma - 2 * sd)) / ma.replace(0, np.nan)
        cur_bw = float(bw.iloc[-1]) if not np.isnan(bw.iloc[-1]) else 0.0
        bw_pct = cls._percentile(bw.iloc[-100:], cur_bw)

        combined = (atr_pct + bw_pct) / 2.0
        if combined >= 0.70:
            label = "EXPANSION"
        elif combined <= 0.30:
            label = "COMPRESSION"
        else:
            label = "NORMAL"
        return {"label": label, "atr_percentile": round(atr_pct, 3),
                "bandwidth_percentile": round(bw_pct, 3), "combined": round(combined, 3),
                "atr": cur_atr}

    # ------------------------------------------------------------------
    @classmethod
    def classify(cls, df: pd.DataFrame, htf_trends: dict = None) -> dict:
        """
        Full regime read. Returns both axes plus the combined label used as the
        conditioning key everywhere downstream.
        """
        if df is None or len(df) < 60:
            return {"available": False, "regime": "UNKNOWN",
                    "direction": "NEUTRAL", "volatility": "NORMAL"}

        d = cls._direction_axis(df, htf_trends)
        v = cls._volatility_axis(df)
        regime = f"{d['label']}_{v['label']}"

        # Which side the regime itself favours, before any setup-specific scoring.
        if d["label"] == "BULL" and v["label"] == "EXPANSION":
            favours, conviction = "LONG", "high"
        elif d["label"] == "BEAR" and v["label"] == "EXPANSION":
            favours, conviction = "SHORT", "high"
        elif d["label"] == "BULL":
            favours, conviction = "LONG", "moderate"
        elif d["label"] == "BEAR":
            favours, conviction = "SHORT", "moderate"
        else:
            favours, conviction = "NONE", "low"

        # Compression is where breakout logic goes to die: moves revert rather than run.
        mean_reverting = v["label"] == "COMPRESSION" or d["label"] == "NEUTRAL"

        return {
            "available": True,
            "regime": regime,
            "direction": d["label"],
            "direction_score": d["score"],
            "direction_strength": d["strength"],
            "volatility": v["label"],
            "atr_percentile": v["atr_percentile"],
            "bandwidth_percentile": v["bandwidth_percentile"],
            "favours": favours,
            "conviction": conviction,
            "mean_reverting": mean_reverting,
            "detail": d["detail"],
            "legacy_regime": ("VOLATILE" if v["atr_percentile"] > 0.7 else
                              "CHOPPY" if v["atr_percentile"] < 0.3 else
                              "TRENDING" if abs(d["score"]) >= 3 else "RANGING"),
        }
