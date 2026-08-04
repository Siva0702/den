import pandas as pd
import numpy as np
from indicators.institutional_smc import InstitutionalSMCEngine
from indicators.orderflow_imbalance import InstitutionalOrderFlowEngine
from indicators.volume_profile import InstitutionalVolumeProfile
from indicators.anti_manipulation import InstitutionalAntiManipulationShield

class SureShotConfluenceEngine:
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
        gain = (delta.where(delta > 0, 0)).ewm(alpha=1/period, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/period, adjust=False).mean()
        
        # Math Fix: Wilder's smoothing with scalar fallback (1e-12 epsilon for low price assets)
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
        hist = macd - sig
        return macd, sig, hist

    @staticmethod
    def _calculate_vwap(df):
        # Math Fix: cumsum / cumsum
        pv = (df['high'] + df['low'] + df['close']) / 3 * df['volume']
        vwap = pv.cumsum() / df['volume'].cumsum().clip(lower=0.0001)
        return vwap

    @staticmethod
    def _calc_trend(df):
        if df is None or len(df) < 50:
            return 0
        ema20 = df['close'].ewm(span=20, adjust=False).mean().iloc[-1]
        ema50 = df['close'].ewm(span=50, adjust=False).mean().iloc[-1]
        close = df['close'].iloc[-1]
        if close > ema20 > ema50:
            return 1
        elif close < ema20 < ema50:
            return -1
        return 0

    @staticmethod
    def evaluate_setup(ohlcv_15m, ohlcv_1h=None, ohlcv_4h=None, ohlcv_1d=None,
                       btc_df=None, ticker="", efficiency_history=None,
                       sentiment_multiplier=1.0) -> dict:
        
        if ohlcv_15m is None or len(ohlcv_15m) < 100:
            return {"recommendation_label": "❌ NO TRADE", "reasoning": "Insufficient 15m data", "total_score": 0, "win_rate": 0}

        df = ohlcv_15m.copy()
        close = df['close'].iloc[-1]
        
        score_long = 0.0
        score_short = 0.0
        factors_passed = []
        factors_failed = []

        # -- Pre-computations --
        atr = SureShotConfluenceEngine._calculate_atr(df)
        curr_atr = atr.iloc[-1]
        df['vwap'] = SureShotConfluenceEngine._calculate_vwap(df)
        curr_vwap = df['vwap'].iloc[-1]
        rsi, _, _ = SureShotConfluenceEngine._calculate_rsi(df)
        macd, macd_signal, macd_hist = SureShotConfluenceEngine._calculate_macd(df)

        # Volatility & Percentiles
        atr_100 = atr.iloc[-100:] if len(atr) >= 100 else atr
        atr_percentile = (atr_100 < curr_atr).mean()
        
        # CATEGORY 1: Multi-Timeframe Trend (0-18)
        trend_1d = SureShotConfluenceEngine._calc_trend(ohlcv_1d)
        trend_4h = SureShotConfluenceEngine._calc_trend(ohlcv_4h)
        trend_1h = SureShotConfluenceEngine._calc_trend(ohlcv_1h)
        trend_15m = SureShotConfluenceEngine._calc_trend(df)
        
        tf_align = trend_1d + trend_4h + trend_1h + trend_15m
        
        if tf_align >= 3:
            score_long += 15
            factors_passed.append("Strong MTF Bullish Alignment")
        elif tf_align <= -3:
            score_short += 15
            factors_passed.append("Strong MTF Bearish Alignment")
        else:
            factors_failed.append("Mixed or Weak MTF Alignment")

        # CATEGORY 2: Market Structure (0-14)
        hh_hl = df['high'].iloc[-1] > df['high'].iloc[-2] and df['low'].iloc[-1] > df['low'].iloc[-2]
        ll_lh = df['high'].iloc[-1] < df['high'].iloc[-2] and df['low'].iloc[-1] < df['low'].iloc[-2]
        
        if hh_hl:
            score_long += 8
            factors_passed.append("Local HH/HL Structure")
        elif ll_lh:
            score_short += 8
            factors_passed.append("Local LL/LH Structure")

        # CATEGORY 3: Smart Money Concepts (0-16)
        smc_data = InstitutionalSMCEngine.analyze_smc_structure(df)
        if smc_data.get('bullish_ob') or smc_data.get('bullish_fvg'):
            score_long += 10
            factors_passed.append("SMC: Bullish OB/FVG detected")
        if smc_data.get('bearish_ob') or smc_data.get('bearish_fvg'):
            score_short += 10
            factors_passed.append("SMC: Bearish OB/FVG detected")

        if smc_data.get('liquidity_sweep_low'):
            score_long += 6
            factors_passed.append("SMC: Liquidity Sweep Low Reclaim")
        if smc_data.get('liquidity_sweep_high'):
            score_short += 6
            factors_passed.append("SMC: Liquidity Sweep High Reclaim")

        # CATEGORY 4: Momentum & Flow (0-18)
        # Volume profile
        vp_data = InstitutionalVolumeProfile.calculate_poc(df)
        if vp_data.get('above_poc'):
            score_long += 4
        if vp_data.get('below_poc'):
            score_short += 4

        # Orderflow
        of_data = InstitutionalOrderFlowEngine.analyze_orderflow(df)
        if of_data.get('is_aggressive_buying'):
            score_long += 8
            factors_passed.append("Orderflow: Aggressive Buying")
        if of_data.get('is_aggressive_selling'):
            score_short += 8
            factors_passed.append("Orderflow: Aggressive Selling")
            
        vol_surge = of_data.get('volume_surge', False)
        if vol_surge:
            score_long += 3
            score_short += 3 # Amplify both sides based on direction

        # RSI Dynamic
        if atr_percentile > 0.8: # Trending volatile
            if rsi > 60: score_long += 5
            if rsi < 40: score_short += 5
        else: # Ranging
            if rsi < 35: score_long += 5
            if rsi > 65: score_short += 5

        # MACD
        if macd_hist.iloc[-1] > 0 and macd_hist.iloc[-1] > macd_hist.iloc[-2]:
            score_long += 4
        if macd_hist.iloc[-1] < 0 and macd_hist.iloc[-1] < macd_hist.iloc[-2]:
            score_short += 4
            
        # VWAP
        if close > curr_vwap:
            score_long += 4
        else:
            score_short += 4

        # CATEGORY 5: Regime & Context (0-16)
        if atr_percentile > 0.7:
            market_regime = "VOLATILE"
        elif atr_percentile < 0.3:
            market_regime = "CHOPPY"
        elif abs(tf_align) >= 3:
            market_regime = "TRENDING"
        else:
            market_regime = "RANGING"

        btc_corr = 0
        if btc_df is not None and len(btc_df) >= 20:
            btc_ret = btc_df['close'].pct_change().fillna(0)
            ast_ret = df['close'].pct_change().fillna(0)
            btc_corr = ast_ret.corr(btc_ret)
            if btc_corr > 0.7:
                btc_trend = SureShotConfluenceEngine._calc_trend(btc_df)
                if btc_trend > 0: score_long += 5
                if btc_trend < 0: score_short += 5

        # CATEGORY 6: Defense & Risk (0-10)
        am_data = InstitutionalAntiManipulationShield.audit_manipulation(df)
        if not am_data.get('is_manipulated'):
            score_long += 5
            score_short += 5
            factors_passed.append("Clean Orderflow (No Wick Traps)")
        else:
            factors_failed.append(am_data.get('status', 'Manipulated'))
            
        # Parabolic Extension Check (Dynamic)
        returns = df['close'].pct_change().abs()
        max_recent_return = returns.iloc[-50:].quantile(0.95)
        current_return = abs(df['close'].iloc[-1] - df['close'].iloc[-2]) / df['close'].iloc[-2]
        if current_return > max_recent_return * 2:
            factors_failed.append("Parabolic Extension Warning")
            # Don't add defense points
        else:
            score_long += 5
            score_short += 5

        # CATEGORY 7: Self-Learning (-8 to +8)
        hist_adj = 0
        if efficiency_history and 'history' in efficiency_history:
            recent_trades = [t for t in efficiency_history['history'] if t.get('ticker') == ticker]
            wins = sum(1 for t in recent_trades if t.get('outcome') == 'WIN')
            losses = sum(1 for t in recent_trades if t.get('outcome') == 'LOSS')
            if wins > losses:
                hist_adj = 5
            elif losses > wins:
                hist_adj = -5

        # Calculate Final Scores
        final_long = (score_long + hist_adj) * sentiment_multiplier
        final_short = (score_short + hist_adj) * sentiment_multiplier

        if final_long > final_short:
            total_score = final_long
            direction = "LONG"
        elif final_short > final_long:
            total_score = final_short
            direction = "SHORT"
        else:
            total_score = 0
            direction = "NONE"

        # Capping / Uncapping
        total_score = max(0, total_score) # Start at 0, no upper cap, but effectively around 100 based on points

        win_rate = min(total_score / 100.0, 0.99)
        
        is_sure_shot = total_score >= 85 and abs(tf_align) >= 3 and not am_data.get('is_manipulated')
        
        if is_sure_shot:
            rec_label = "🔥 SURE SHOT"
        elif total_score >= 70:
            rec_label = "⚡ HIGH CONVICTION"
        elif total_score >= 50:
            rec_label = "✅ QUALIFIED"
        else:
            rec_label = "❌ NO TRADE"

        reasoning = f"Setup scored {total_score:.1f} in {market_regime} market. "
        if direction != "NONE":
            reasoning += f"Alignment leans {direction}."

        est_duration = "~2-4 hours"
        if tf_align != 0 and atr_percentile > 0.6:
            est_duration = "~30-60 minutes"

        return {
            "total_score": round(total_score, 2),
            "win_rate": round(win_rate, 4),
            "direction": direction,
            "is_sure_shot": is_sure_shot,
            "recommendation_label": rec_label,
            "factors_passed": factors_passed,
            "factors_failed": factors_failed,
            "reasoning": reasoning,
            "entry_price": close,
            "vwap": round(curr_vwap, 4),
            "atr": round(curr_atr, 4),
            "rsi": round(rsi, 2),
            "estimated_duration": est_duration,
            "market_regime": market_regime,
            "timeframe_alignment": abs(tf_align)
        }
