import pandas as pd
import numpy as np

from indicators.institutional_smc import InstitutionalSMCEngine
from indicators.orderflow_imbalance import InstitutionalOrderFlowEngine
from indicators.volume_profile import InstitutionalVolumeProfile
from indicators.anti_manipulation import InstitutionalAntiManipulationShield
from indicators.liquidity_map import LiquidityMapEngine
from indicators.regime_engine import MarketRegimeEngine


class SureShotConfluenceEngine:
    """
    Den Engine v39.0 Confluence Engine.

    Rebuilt on a TRUE 0-100 scale. The previous version summed award constants to a
    theoretical maximum of 82 (87 minus the mutually exclusive RSI branch), then
    MULTIPLIED the result by a regulatory multiplier — so during a 0.65x regime the
    ceiling was 53 and the 78.0 dispatch gate was mathematically unreachable. The engine
    could not have fired even if every single dimension aligned perfectly.

    Here each pillar has a fixed budget and each awards a FRACTION of that budget, so a
    perfect setup scores exactly 100 and the number means the same thing every time:

        PILLAR 1  TREND        20 pts   EMA stack + slope on the execution timeframe
        PILLAR 2  HTF CONFIRM  20 pts   1D / 4H / 1H agreement with the trade
        PILLAR 3  ORDER FLOW   25 pts   who is actually lifting the offer, right now
        PILLAR 4  STRUCTURE    20 pts   BOS, SMC, sweep-and-reclaim probability
        PILLAR 5  RISK/DEFENSE 15 pts   what can go wrong — spends down from full marks
                               ------
                               100 pts

    Context modifiers (news, regulation, calibrated history) are then applied as a
    BOUNDED ADDITIVE TILT of at most +/-10 points, never as a multiplier. A macro
    headwind should move a setup from 82 to 74; it should not make scoring impossible.
    """

    PILLAR_BUDGET = {"trend": 20.0, "htf": 20.0, "orderflow": 25.0, "structure": 20.0, "defense": 15.0}
    MAX_CONTEXT_TILT = 10.0

    # ------------------------------------------------------------------
    # Primitives
    # ------------------------------------------------------------------
    @staticmethod
    def _calculate_atr(df, period=14):
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        return true_range.rolling(period).mean()

    @staticmethod
    def _calculate_rsi(df, period=14):
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).ewm(alpha=1 / period, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(alpha=1 / period, adjust=False).mean()
        loss_val = loss.iloc[-1]
        gain_val = gain.iloc[-1]
        rs = gain_val / (loss_val if loss_val > 1e-12 else 1e-12)
        rsi = 100 - (100 / (1 + rs))
        return rsi, gain, loss

    @staticmethod
    def _calculate_macd(df, fast=12, slow=26, signal=9):
        exp1 = df['close'].ewm(span=fast, adjust=False).mean()
        exp2 = df['close'].ewm(span=slow, adjust=False).mean()
        macd = exp1 - exp2
        sig = macd.ewm(span=signal, adjust=False).mean()
        return macd, sig, macd - sig

    @staticmethod
    def _calculate_vwap(df):
        pv = (df['high'] + df['low'] + df['close']) / 3 * df['volume']
        return pv.cumsum() / df['volume'].cumsum().clip(lower=0.0001)

    @staticmethod
    def _calc_trend(df):
        if df is None or len(df) < 50:
            return 0
        ema20 = df['close'].ewm(span=20, adjust=False).mean().iloc[-1]
        ema50 = df['close'].ewm(span=50, adjust=False).mean().iloc[-1]
        close = df['close'].iloc[-1]
        if close > ema20 > ema50:
            return 1
        if close < ema20 < ema50:
            return -1
        return 0

    # ------------------------------------------------------------------
    # PILLAR 1 — Trend (EMA)
    # ------------------------------------------------------------------
    @classmethod
    def _score_trend(cls, df) -> dict:
        budget = cls.PILLAR_BUDGET["trend"]
        close = df['close']
        ema9 = close.ewm(span=9, adjust=False).mean()
        ema21 = close.ewm(span=21, adjust=False).mean()
        ema50 = close.ewm(span=50, adjust=False).mean()
        ema200 = close.ewm(span=200, adjust=False).mean() if len(df) >= 100 else ema50

        c = float(close.iloc[-1])
        e9, e21, e50, e200 = (float(x.iloc[-1]) for x in (ema9, ema21, ema50, ema200))

        long_frac = 0.0
        short_frac = 0.0
        notes = []

        # Stack alignment carries 60% of the pillar.
        if c > e9 > e21 > e50:
            long_frac += 0.60
            notes.append("EMA stack fully bullish (price > 9 > 21 > 50)")
        elif c < e9 < e21 < e50:
            short_frac += 0.60
            notes.append("EMA stack fully bearish (price < 9 < 21 < 50)")
        elif c > e9 > e21:
            long_frac += 0.30
            notes.append("EMA 9/21 bullish, 50 not yet aligned")
        elif c < e9 < e21:
            short_frac += 0.30
            notes.append("EMA 9/21 bearish, 50 not yet aligned")

        # Long-term regime filter carries 20%.
        if c > e200:
            long_frac += 0.20
        else:
            short_frac += 0.20

        # Slope: a flat EMA is not a trend. 20%.
        if len(ema21) > 10:
            slope = (float(ema21.iloc[-1]) - float(ema21.iloc[-10])) / max(abs(float(ema21.iloc[-10])), 1e-12)
            if slope > 0.0015:
                long_frac += 0.20
                notes.append(f"EMA21 rising {slope * 100:+.2f}% over 10 bars")
            elif slope < -0.0015:
                short_frac += 0.20
                notes.append(f"EMA21 falling {slope * 100:+.2f}% over 10 bars")
            else:
                notes.append("EMA21 flat — no trend slope")

        return {
            "long": round(min(long_frac, 1.0) * budget, 2),
            "short": round(min(short_frac, 1.0) * budget, 2),
            "budget": budget,
            "notes": notes,
            "ema_bias": "Bullish" if long_frac > short_frac else "Bearish" if short_frac > long_frac else "Neutral",
        }

    # ------------------------------------------------------------------
    # PILLAR 2 — Higher timeframe confirmation
    # ------------------------------------------------------------------
    @classmethod
    def _score_htf(cls, df_15m, df_1h, df_4h, df_1d) -> dict:
        budget = cls.PILLAR_BUDGET["htf"]
        # Weighted: the daily governs, the 15m merely executes.
        weights = {"1d": 0.40, "4h": 0.30, "1h": 0.20, "15m": 0.10}
        trends = {
            "1d": cls._calc_trend(df_1d),
            "4h": cls._calc_trend(df_4h),
            "1h": cls._calc_trend(df_1h),
            "15m": cls._calc_trend(df_15m),
        }

        long_frac = sum(w for tf, w in weights.items() if trends[tf] > 0)
        short_frac = sum(w for tf, w in weights.items() if trends[tf] < 0)
        aligned = sum(1 for v in trends.values() if v != 0 and
                      (v > 0) == (long_frac > short_frac))

        available = sum(1 for tf, df in (("1d", df_1d), ("4h", df_4h), ("1h", df_1h)) if df is not None)
        notes = [f"HTF trends — 1D:{trends['1d']:+d} 4H:{trends['4h']:+d} 1H:{trends['1h']:+d} 15m:{trends['15m']:+d}"]
        if available < 3:
            notes.append(f"Only {available}/3 higher timeframes available — confirmation partial")

        return {
            "long": round(long_frac * budget, 2),
            "short": round(short_frac * budget, 2),
            "budget": budget,
            "notes": notes,
            "trends": trends,
            "aligned_count": aligned,
            "tf_align_raw": sum(trends.values()),
            "htf_bias": "Bullish" if long_frac > short_frac else "Bearish" if short_frac > long_frac else "Mixed",
        }

    # ------------------------------------------------------------------
    # PILLAR 3 — Order flow (the entry trigger)
    # ------------------------------------------------------------------
    @classmethod
    def _score_orderflow(cls, df, derivatives: dict, curr_vwap: float, rsi: float,
                         macd_hist, atr_percentile: float) -> dict:
        budget = cls.PILLAR_BUDGET["orderflow"]
        close = float(df['close'].iloc[-1])
        long_frac = 0.0
        short_frac = 0.0
        notes = []

        # Native candle-derived orderflow — 30% of pillar.
        of = InstitutionalOrderFlowEngine.analyze_orderflow(df)
        if of.get("is_aggressive_buying"):
            long_frac += 0.30
            notes.append(f"Aggressive buying — {of['buy_ratio']}% of 10-bar volume")
        elif of.get("is_aggressive_selling"):
            short_frac += 0.30
            notes.append(f"Aggressive selling — {of['sell_ratio']}% of 10-bar volume")

        # Real exchange taker flow — 20%. Higher quality than candle inference.
        taker = (derivatives or {}).get("taker", {})
        if taker.get("available"):
            if taker.get("aggressive_buyers"):
                long_frac += 0.20
                notes.append(f"Exchange taker flow buy-skewed ({taker['taker_buy_sell_ratio']}x)")
            elif taker.get("aggressive_sellers"):
                short_frac += 0.20
                notes.append(f"Exchange taker flow sell-skewed ({taker['taker_buy_sell_ratio']}x)")

        # Resting book pressure — 10%.
        book = (derivatives or {}).get("book", {})
        if book.get("available"):
            if book["book_bias"] == "BID_HEAVY":
                long_frac += 0.10
            elif book["book_bias"] == "ASK_HEAVY":
                short_frac += 0.10

        # Volume confirmation — 15%. Flow without volume is noise.
        if of.get("volume_surge"):
            surge_side = "long" if of.get("is_aggressive_buying") else "short" if of.get("is_aggressive_selling") else None
            if surge_side == "long":
                long_frac += 0.15
            elif surge_side == "short":
                short_frac += 0.15
            notes.append(f"Volume surge {of.get('volume_surge_ratio')}x 20-bar average")

        # VWAP — 10%.
        if close > curr_vwap:
            long_frac += 0.10
        else:
            short_frac += 0.10

        # Volume profile POC — 5%.
        vp = InstitutionalVolumeProfile.calculate_poc(df)
        if vp.get("above_poc"):
            long_frac += 0.05
        elif vp.get("below_poc"):
            short_frac += 0.05

        # Momentum: RSI regime-aware + MACD — 10%.
        if atr_percentile > 0.8:
            if rsi > 60:
                long_frac += 0.05
            elif rsi < 40:
                short_frac += 0.05
        else:
            if rsi < 35:
                long_frac += 0.05
            elif rsi > 65:
                short_frac += 0.05

        if len(macd_hist) >= 2:
            h_now, h_prev = float(macd_hist.iloc[-1]), float(macd_hist.iloc[-2])
            if h_now > 0 and h_now > h_prev:
                long_frac += 0.05
            elif h_now < 0 and h_now < h_prev:
                short_frac += 0.05

        return {
            "long": round(min(long_frac, 1.0) * budget, 2),
            "short": round(min(short_frac, 1.0) * budget, 2),
            "budget": budget,
            "notes": notes,
            "orderflow_raw": of,
        }

    # ------------------------------------------------------------------
    # PILLAR 4 — Structure / BOS probability
    # ------------------------------------------------------------------
    @classmethod
    def _detect_bos(cls, df) -> dict:
        """
        Break of structure against the most recent confirmed swing, not against the
        previous candle. A close beyond the last swing high/low is a BOS; a wick beyond
        it that closes back inside is a liquidity sweep, which is the opposite signal.
        """
        if len(df) < 30:
            return {"bos": "NONE", "detail": "insufficient bars"}

        highs, lows = LiquidityMapEngine._swing_points(df.iloc[-40:].reset_index(drop=True))
        close = float(df['close'].iloc[-1])
        high = float(df['high'].iloc[-1])
        low = float(df['low'].iloc[-1])

        last_swing_high = max((p for _, p in highs), default=None)
        last_swing_low = min((p for _, p in lows), default=None)

        if last_swing_high is not None and close > last_swing_high:
            return {"bos": "BULLISH", "level": last_swing_high,
                    "detail": f"Confirmed bullish BOS — close {close:.6g} above swing high {last_swing_high:.6g}"}
        if last_swing_low is not None and close < last_swing_low:
            return {"bos": "BEARISH", "level": last_swing_low,
                    "detail": f"Confirmed bearish BOS — close {close:.6g} below swing low {last_swing_low:.6g}"}
        if last_swing_high is not None and high > last_swing_high >= close:
            return {"bos": "FAILED_HIGH", "level": last_swing_high,
                    "detail": f"Wick above swing high {last_swing_high:.6g} rejected — liquidity sweep, not a break"}
        if last_swing_low is not None and low < last_swing_low <= close:
            return {"bos": "FAILED_LOW", "level": last_swing_low,
                    "detail": f"Wick below swing low {last_swing_low:.6g} reclaimed — liquidity sweep, not a break"}
        return {"bos": "NONE", "detail": "Price inside prior structure — no break"}

    @classmethod
    def _score_structure(cls, df, hunt_long: dict, hunt_short: dict) -> dict:
        budget = cls.PILLAR_BUDGET["structure"]
        long_frac = 0.0
        short_frac = 0.0
        notes = []

        # BOS — 40% of pillar.
        bos = cls._detect_bos(df)
        if bos["bos"] == "BULLISH":
            long_frac += 0.40
            notes.append(bos["detail"])
        elif bos["bos"] == "BEARISH":
            short_frac += 0.40
            notes.append(bos["detail"])
        elif bos["bos"] == "FAILED_HIGH":
            # A rejected push above is bearish continuation fuel.
            short_frac += 0.20
            notes.append(bos["detail"])
        elif bos["bos"] == "FAILED_LOW":
            long_frac += 0.20
            notes.append(bos["detail"])

        # SMC order blocks / fair value gaps — 30%.
        smc = InstitutionalSMCEngine.analyze_smc_structure(df)
        if smc.get("bullish_ob") or smc.get("bullish_fvg"):
            long_frac += 0.30
            notes.append("SMC: bullish order block / FVG present")
        if smc.get("bearish_ob") or smc.get("bearish_fvg"):
            short_frac += 0.30
            notes.append("SMC: bearish order block / FVG present")

        # Sweep-and-reclaim — 20%. This is the highest-quality entry there is.
        if hunt_long.get("sweep_reclaimed"):
            long_frac += 0.20
            notes.append("Downside liquidity swept and reclaimed — stops below are cleared")
        if hunt_short.get("sweep_reclaimed"):
            short_frac += 0.20
            notes.append("Upside liquidity swept and reclaimed — stops above are cleared")

        # Local candle structure — 10%.
        if df['high'].iloc[-1] > df['high'].iloc[-2] and df['low'].iloc[-1] > df['low'].iloc[-2]:
            long_frac += 0.10
        elif df['high'].iloc[-1] < df['high'].iloc[-2] and df['low'].iloc[-1] < df['low'].iloc[-2]:
            short_frac += 0.10

        return {
            "long": round(min(long_frac, 1.0) * budget, 2),
            "short": round(min(short_frac, 1.0) * budget, 2),
            "budget": budget,
            "notes": notes,
            "bos": bos,
            "smc": smc,
        }

    # ------------------------------------------------------------------
    # PILLAR 5 — Risk / defense (starts full, spends down)
    # ------------------------------------------------------------------
    @classmethod
    def _score_defense(cls, df, derivatives: dict, news: dict,
                       hunt_long: dict, hunt_short: dict,
                       calendar: dict = None, event_vol: dict = None) -> dict:
        budget = cls.PILLAR_BUDGET["defense"]
        long_frac = 1.0
        short_frac = 1.0
        warnings = []

        # Active manipulation wick — costs 40%.
        am = InstitutionalAntiManipulationShield.audit_manipulation(df)
        if am.get("is_manipulated"):
            long_frac -= 0.40
            short_frac -= 0.40
            warnings.append(am.get("status", "Manipulation detected"))

        # Parabolic extension — costs 25% on the side doing the extending.
        returns = df['close'].pct_change().abs()
        threshold = returns.iloc[-50:].quantile(0.95)
        last_ret = (df['close'].iloc[-1] - df['close'].iloc[-2]) / df['close'].iloc[-2]
        if abs(last_ret) > threshold * 2:
            if last_ret > 0:
                long_frac -= 0.25
            else:
                short_frac -= 0.25
            warnings.append(f"Parabolic extension ({last_ret * 100:+.2f}% bar) — chasing risk")

        # Stop-hunt exposure — costs up to 35%, scaled by measured hunt risk.
        if hunt_long.get("available"):
            long_frac -= 0.35 * (hunt_long.get("hunt_risk_score", 0) / 100.0)
            if hunt_long.get("hunt_risk_score", 0) >= 45:
                warnings.extend(hunt_long.get("warnings", [])[:1])
        if hunt_short.get("available"):
            short_frac -= 0.35 * (hunt_short.get("hunt_risk_score", 0) / 100.0)
            if hunt_short.get("hunt_risk_score", 0) >= 45:
                warnings.extend(hunt_short.get("warnings", [])[:1])

        # Fake breakout: price extending while open interest falls — costs 30%.
        oi = (derivatives or {}).get("open_interest", {})
        if oi.get("available") and oi.get("oi_falling"):
            long_frac -= 0.30
            short_frac -= 0.30
            warnings.append(f"Open interest {oi['oi_change_12bar_pct']}% — move is position unwind, not new money")

        # Crowded positioning — costs 25% on the crowded side only.
        crowd = (derivatives or {}).get("crowding", {})
        if crowd.get("available"):
            if crowd.get("crowding") == "CROWDED_LONG":
                long_frac -= 0.25
                warnings.append(f"{crowd['long_account_pct']}% of accounts are long — crowded side")
            elif crowd.get("crowding") == "CROWDED_SHORT":
                short_frac -= 0.25
                warnings.append(f"{crowd['short_account_pct']}% of accounts are short — crowded side")

        # Extreme funding — costs 20% on the paying side.
        funding = (derivatives or {}).get("funding", {})
        if funding.get("available") and funding.get("is_extreme"):
            if funding.get("contrarian_bias") == "SHORT":
                long_frac -= 0.20
            elif funding.get("contrarian_bias") == "LONG":
                short_frac -= 0.20
            warnings.append(f"Extreme funding {funding['funding_annualised_pct']}% APR — squeeze risk")

        # Thin book — costs 15% both sides.
        book = (derivatives or {}).get("book", {})
        if book.get("available") and book.get("is_thin_book"):
            long_frac -= 0.15
            short_frac -= 0.15
            warnings.append(f"Thin order book ({book['spread_bps']}bps) — slippage risk")

        # Manipulation chatter in the news — costs 35%.
        if (news or {}).get("manipulation_risk"):
            long_frac -= 0.35
            short_frac -= 0.35
            warnings.append(f"Manipulation chatter (score {news.get('manipulation_score')}) — move may be engineered")

        # Scheduled-event proximity. GRADED, not binary: the calendar returns a bounded
        # penalty that decays with time-to-event, so earnings three weeks out cost almost
        # nothing while earnings in 40 minutes cost nearly the whole pillar. Only the
        # narrow blackout window is an outright veto, handled by the caller.
        cal = calendar or {}
        if cal.get("score_penalty"):
            frac = min(cal["score_penalty"] / budget, 0.60)
            long_frac -= frac
            short_frac -= frac
            top = cal.get("top_contributors") or []
            if top and cal.get("event_risk_score", 0) >= 25:
                warnings.append(f"{top[0]['title']} in {top[0]['minutes_away'] / 60:+.1f}h "
                                f"(event risk {cal['event_risk_score']:.0f}/100)")

        # An unresolved event spike is a hard stand-aside on both sides — this is the
        # 104 -> 130 -> 114 protection.
        if (event_vol or {}).get("action") == "NO_ENTRY":
            long_frac -= 0.60
            short_frac -= 0.60
            warnings.append(event_vol.get("reason", "Unresolved event volatility"))

        return {
            "long": round(max(long_frac, 0.0) * budget, 2),
            "short": round(max(short_frac, 0.0) * budget, 2),
            "budget": budget,
            "warnings": warnings,
            "anti_manipulation": am,
        }

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------
    @classmethod
    def evaluate_setup(cls, ohlcv_15m, ohlcv_1h=None, ohlcv_4h=None, ohlcv_1d=None,
                       btc_df=None, ticker="", efficiency_history=None,
                       derivatives=None, news=None, regulatory_multiplier=1.0,
                       calendar=None, event_vol=None) -> dict:

        if ohlcv_15m is None or len(ohlcv_15m) < 100:
            return {"recommendation_label": "❌ NO TRADE", "reasoning": "Insufficient 15m data",
                    "total_score": 0, "direction": "NONE", "win_rate": 0}

        df = ohlcv_15m.copy()
        close = float(df['close'].iloc[-1])

        # Pre-computation
        atr_series = cls._calculate_atr(df)
        curr_atr = float(atr_series.iloc[-1])
        curr_vwap = float(cls._calculate_vwap(df).iloc[-1])
        rsi, _, _ = cls._calculate_rsi(df)
        _, _, macd_hist = cls._calculate_macd(df)
        atr_100 = atr_series.iloc[-100:] if len(atr_series) >= 100 else atr_series
        atr_percentile = float((atr_100 < curr_atr).mean())

        liquidity = LiquidityMapEngine.map_liquidity(df, curr_atr)
        hunt_long = LiquidityMapEngine.hunt_risk(df, "LONG", curr_atr, liquidity)
        hunt_short = LiquidityMapEngine.hunt_risk(df, "SHORT", curr_atr, liquidity)

        # Pillars
        p_trend = cls._score_trend(df)
        p_htf = cls._score_htf(df, ohlcv_1h, ohlcv_4h, ohlcv_1d)
        p_flow = cls._score_orderflow(df, derivatives, curr_vwap, rsi, macd_hist, atr_percentile)
        p_struct = cls._score_structure(df, hunt_long, hunt_short)
        p_def = cls._score_defense(df, derivatives, news, hunt_long, hunt_short, calendar, event_vol)

        # TWO-AXIS REGIME. The old label measured volatility only and could not tell a
        # bull market from a bear one — the single distinction that decides whether long
        # or short is the right side. Legacy label retained for continuity with existing
        # records; the new one is what conditions the score.
        regime_read = MarketRegimeEngine.classify(df, p_htf.get("trends"))
        market_regime = regime_read.get("legacy_regime", "RANGING")
        regime_full = regime_read.get("regime", "UNKNOWN")

        pillars = [p_trend, p_htf, p_flow, p_struct, p_def]
        base_long = sum(p["long"] for p in pillars)
        base_short = sum(p["short"] for p in pillars)

        # ---- Context tilt: additive and bounded, never multiplicative ----
        tilt_long = 0.0
        tilt_short = 0.0
        tilt_notes = []

        if news and news.get("available"):
            # news_multiplier lives in [0.80, 1.20]; map to +/-6 points.
            news_pts = (news["news_multiplier"] - 1.0) * 30.0
            tilt_long += news_pts
            tilt_short -= news_pts
            if abs(news_pts) > 0.5:
                tilt_notes.append(f"News {news['news_bias'].lower()} ({news_pts:+.1f} pts)")

        # Post-event playbook. A confirmed exhaustion fade is a high-quality, short-lived
        # edge, so it adds score to the side that fades the spike and subtracts from the
        # side that would be chasing it.
        ev = event_vol or {}
        if ev.get("action") in ("FADE", "CONTINUATION"):
            bonus = ev.get("score_bonus", 4.0)
            if ev.get("direction") == "LONG":
                tilt_long += bonus
                tilt_short -= bonus
            elif ev.get("direction") == "SHORT":
                tilt_short += bonus
                tilt_long -= bonus
            tilt_notes.append(f"Post-event {ev['action'].lower()} {ev.get('direction')} "
                              f"({ev.get('classification')}, {bonus:+.1f} pts)")

        # NOTE: the standalone regulatory multiplier was REMOVED in v39.1.
        # It was a keyword scan over one Google News query that applied a blanket 0.65x
        # to all 87 assets whenever it saw the words "recess" and "delayed" together —
        # including to gold, Boeing and UnitedHealth, which have nothing to do with US
        # crypto market-structure legislation. It was costing every long 7 points on a
        # hardcoded guess. Regulatory events now enter through the calendar as ordinary
        # scheduled events, weighted by real proximity and real asset relevance like
        # every other catalyst. `regulatory_multiplier` is retained in the signature only
        # for backward compatibility and is deliberately unused.

        # BTC correlation regime for crypto.
        btc_corr = 0.0
        if btc_df is not None and len(btc_df) >= 20 and ticker != "BTC/USDT":
            try:
                btc_ret = btc_df['close'].pct_change().fillna(0)
                ast_ret = df['close'].pct_change().fillna(0)
                n = min(len(btc_ret), len(ast_ret))
                btc_corr = float(ast_ret.iloc[-n:].corr(btc_ret.iloc[-n:]))
                if btc_corr > 0.7:
                    btc_trend = cls._calc_trend(btc_df)
                    if btc_trend > 0:
                        tilt_long += 3.0
                        tilt_short -= 3.0
                    elif btc_trend < 0:
                        tilt_short += 3.0
                        tilt_long -= 3.0
                    tilt_notes.append(f"BTC correlation {btc_corr:.2f} — beta regime applies")
            except Exception:
                btc_corr = 0.0

        # Per-ticker realised history from resolved trades.
        if efficiency_history and 'per_ticker' in (efficiency_history or {}):
            tk = efficiency_history['per_ticker'].get(ticker)
            if tk and (tk.get("wins", 0) + tk.get("losses", 0)) >= 4:
                w, l = tk["wins"], tk["losses"]
                edge = (w - l) / (w + l)
                tilt_long += edge * 4.0
                tilt_short += edge * 4.0
                tilt_notes.append(f"Realised history on {ticker}: {w}W/{l}L ({edge * 4.0:+.1f} pts)")

        # LEARNED REGIME/STRUCTURE ADJUSTMENT.
        # Measured from resolved outcomes, never hand-set. This is what corrects the
        # symmetric BOS reward that was promoting 25%-accuracy bullish breaks into the
        # 70+ band. Shrinkage means a thinly-observed condition barely moves the score.
        learn_notes = []
        try:
            from audit.regime_performance import RegimePerformance
            bos_state = p_struct["bos"]["bos"]
            adj_long = RegimePerformance.adjustment("LONG", bos_state, market_regime)
            adj_short = RegimePerformance.adjustment("SHORT", bos_state, market_regime)
            if adj_long.get("available"):
                tilt_long += adj_long["total"]
                tilt_short += adj_short["total"]
                if abs(adj_long["total"]) > 0.5 or abs(adj_short["total"]) > 0.5:
                    learn_notes.append(f"Learned edge: LONG {adj_long['total']:+.1f}, "
                                       f"SHORT {adj_short['total']:+.1f} pts "
                                       f"(BOS {bos_state}, {market_regime})")
        except Exception as e:
            print(f"[!] regime adjustment unavailable: {type(e).__name__}")
        tilt_notes.extend(learn_notes)

        # The learned component legitimately exceeds the base tilt cap, so the ceiling is
        # widened to accommodate it — still bounded, so no single input can run away.
        WIDE = cls.MAX_CONTEXT_TILT * 2.2
        tilt_long = max(-WIDE, min(tilt_long, WIDE))
        tilt_short = max(-WIDE, min(tilt_short, WIDE))

        final_long = max(0.0, min(base_long + tilt_long, 100.0))
        final_short = max(0.0, min(base_short + tilt_short, 100.0))

        if final_long > final_short:
            total_score, direction = final_long, "LONG"
            hunt = hunt_long
        elif final_short > final_long:
            total_score, direction = final_short, "SHORT"
            hunt = hunt_short
        else:
            total_score, direction, hunt = 0.0, "NONE", {}


        # Collect narrative
        factors_passed = []
        factors_failed = []
        side = "long" if direction == "LONG" else "short"
        for p in (p_trend, p_htf, p_flow, p_struct):
            share = p[side] / p["budget"] if p["budget"] else 0.0
            if share >= 0.5:
                factors_passed.extend(p.get("notes", [])[:3])
            elif share < 0.25:
                factors_failed.append(f"Weak {list(cls.PILLAR_BUDGET.keys())[pillars.index(p)]} pillar "
                                      f"({p[side]:.0f}/{p['budget']:.0f})")
        factors_failed.extend(p_def.get("warnings", []))
        factors_passed.extend(tilt_notes)

        is_sure_shot = (
            total_score >= 85
            and p_htf["aligned_count"] >= 3
            and not p_def["anti_manipulation"].get("is_manipulated")
            and hunt.get("hunt_risk_score", 100) < 45
        )
        if is_sure_shot:
            rec_label = "🔥 SURE SHOT"
        elif total_score >= 75:
            rec_label = "⚡ HIGH CONVICTION"
        elif total_score >= 55:
            rec_label = "✅ QUALIFIED"
        else:
            rec_label = "❌ NO TRADE"

        est_duration = "~30-90 minutes" if atr_percentile > 0.6 else "~2-6 hours"

        # Feature snapshot — this is what the shadow ledger learns from.
        feature_snapshot = {
            "pillar_trend": p_trend[side] if direction != "NONE" else 0,
            "pillar_htf": p_htf[side] if direction != "NONE" else 0,
            "pillar_orderflow": p_flow[side] if direction != "NONE" else 0,
            "pillar_structure": p_struct[side] if direction != "NONE" else 0,
            "pillar_defense": p_def[side] if direction != "NONE" else 0,
            "ema_bias": p_trend["ema_bias"],
            "htf_bias": p_htf["htf_bias"],
            "bos": p_struct["bos"]["bos"],
            "market_regime": market_regime,
            "regime": regime_full,
            "regime_direction": regime_read.get("direction"),
            "regime_volatility": regime_read.get("volatility"),
            "regime_favours": regime_read.get("favours"),
            "mean_reverting": regime_read.get("mean_reverting"),
            "atr_percentile": round(atr_percentile, 3),
            "rsi": round(float(rsi), 2),
            "btc_correlation": round(btc_corr, 3) if btc_corr else 0.0,
            "hunt_risk_score": hunt.get("hunt_risk_score"),
            "sweep_reclaimed": hunt.get("sweep_reclaimed"),
            "derivatives_bias": (derivatives or {}).get("derivatives_bias"),
            "crowding": ((derivatives or {}).get("crowding") or {}).get("crowding"),
            "long_account_pct": ((derivatives or {}).get("crowding") or {}).get("long_account_pct"),
            "funding_annualised_pct": ((derivatives or {}).get("funding") or {}).get("funding_annualised_pct"),
            "oi_change_12bar_pct": ((derivatives or {}).get("open_interest") or {}).get("oi_change_12bar_pct"),
            "news_bias": (news or {}).get("news_bias"),
            "news_blocked": (news or {}).get("block_entry"),
            "regulatory_multiplier": regulatory_multiplier,
            "event_risk_score": (calendar or {}).get("event_risk_score"),
            "event_verdict": (calendar or {}).get("verdict"),
            "minutes_to_next_event": ((calendar or {}).get("next_event") or {}).get("minutes_away"),
            "event_vol_action": (event_vol or {}).get("action"),
            "event_vol_class": (event_vol or {}).get("classification"),
        }

        return {
            "total_score": round(total_score, 2),
            "direction": direction,
            "is_sure_shot": is_sure_shot,
            "recommendation_label": rec_label,
            "pillar_breakdown": {
                "trend": {"score": p_trend[side] if direction != "NONE" else 0, "budget": p_trend["budget"]},
                "htf": {"score": p_htf[side] if direction != "NONE" else 0, "budget": p_htf["budget"]},
                "orderflow": {"score": p_flow[side] if direction != "NONE" else 0, "budget": p_flow["budget"]},
                "structure": {"score": p_struct[side] if direction != "NONE" else 0, "budget": p_struct["budget"]},
                "defense": {"score": p_def[side] if direction != "NONE" else 0, "budget": p_def["budget"]},
            },
            "factors_passed": factors_passed[:8],
            "factors_failed": factors_failed[:5],
            "reasoning": f"Scored {total_score:.1f}/100 in a {market_regime} regime. "
                         f"EMA {p_trend['ema_bias']}, HTF {p_htf['htf_bias']}, {p_struct['bos']['bos']} structure.",
            "entry_price": close,
            "vwap": round(curr_vwap, 8),
            "atr": round(curr_atr, 8),
            "rsi": round(float(rsi), 2),
            "atr_percentile": round(atr_percentile, 3),
            "estimated_duration": est_duration,
            "market_regime": market_regime,
            "regime_full": regime_full,
            "regime_detail": regime_read,
            "timeframe_alignment": p_htf["aligned_count"],
            "trend_strength_pct": round((p_htf[side] / p_htf["budget"]) * 100 if direction != "NONE" else 0, 0),
            "liquidity": liquidity,
            "hunt_risk": hunt,
            "feature_snapshot": feature_snapshot,
            "ema_bias": p_trend["ema_bias"],
            "htf_bias": p_htf["htf_bias"],
            "bos_status": p_struct["bos"]["bos"],
        }
