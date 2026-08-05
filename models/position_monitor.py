# models/position_monitor.py
import json
import os
import requests
import sys
import time

sys.path.append(os.path.dirname(__file__))
from audit.engine_efficiency import EngineEfficiencyTracker
from alerts.signal_cooldown import SignalCooldownEngine
from indicators.trade_decay import TradeDecayEngine

POSITIONS_FILE = "portfolio/active_positions.json"

class ActivePositionMonitor:
    """
    Den Engine v38.0 Real-Time Position Monitor & Engine Accuracy Tracker:
    - Monitors active positions against live exchange prices
    - Calculates REAL PnL from actual price movements
    - Records outcomes to BOTH audit/engine_efficiency.json AND portfolio/trade_history.json
    - Uses 🟢 for LONG wins, 🔴 for SHORT wins, 🏆 for big wins, 💔 for losses
    """

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token or "8847828896:AAFcTqjJGe6VN6mbPHcB1QTlvkpQxhb5ntI"
        self.chat_id = chat_id or "7347569157"
        self.notified_milestones = self.load_milestones()

    def load_milestones(self) -> dict:
        path = "portfolio/notified_milestones.json"
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def save_milestones(self):
        os.makedirs("portfolio", exist_ok=True)
        path = "portfolio/notified_milestones.json"
        try:
            with open(path, "w") as f:
                json.dump(self.notified_milestones, f, indent=2)
        except Exception as e:
            print(f"[!] Error saving notified milestones: {e}")

    def load_positions(self) -> list:
        if os.path.exists(POSITIONS_FILE):
            try:
                with open(POSITIONS_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                return []
        return []

    def save_positions(self, positions: list):
        os.makedirs("portfolio", exist_ok=True)
        try:
            with open(POSITIONS_FILE, "w") as f:
                json.dump(positions, f, indent=2)
            self.save_milestones()
        except Exception as e:
            print(f"[!] Error saving active positions: {e}")

    def _format_price(self, price: float) -> str:
        """Dynamic precision formatting."""
        if price < 0.0001:
            return f"${price:.8f}"
        elif price < 0.01:
            return f"${price:.6f}"
        elif price < 1.0:
            return f"${price:.4f}"
        elif price < 100.0:
            return f"${price:.3f}"
        elif price < 10000.0:
            return f"${price:.2f}"
        else:
            return f"${price:,.2f}"

    def send_telegram_alert(self, text: str):
        if not self.bot_token or not self.chat_id:
            return
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": text, "parse_mode": "Markdown"}
        try:
            requests.post(url, json=payload, timeout=10)
        except Exception as e:
            print(f"[!] Telegram Monitor Alert Error: {e}")

    def _calculate_real_pnl(self, pos: dict, exit_price: float) -> dict:
        """Calculate REAL PnL from actual entry/exit prices and position parameters."""
        entry = pos.get("entry_price", exit_price)
        direction = pos.get("direction", "LONG")
        margin = pos.get("margin", 50.0)
        leverage = pos.get("leverage", 15)
        notional = margin * leverage

        if direction == "LONG":
            price_change_pct = (exit_price - entry) / entry
        else:
            price_change_pct = (entry - exit_price) / entry

        pnl_usd = round(notional * price_change_pct, 2)
        pnl_pct = round(price_change_pct * 100, 2)
        roi_pct = round((pnl_usd / max(margin, 0.01)) * 100, 1)

        return {
            "pnl_usd": pnl_usd,
            "pnl_pct": pnl_pct,
            "roi_pct": roi_pct,
            "notional": notional,
            "margin": margin,
            "leverage": leverage
        }

    @staticmethod
    def detect_structure_break(df_15m, direction: str, lookback: int = 10) -> bool:
        """
        Direction-aware structure break.

        v38 hardcoded `close < low[-10]`, which for a SHORT position fires when the
        trade is WORKING — it was sending early-exit warnings on winners and staying
        silent on losers. A break is only a break against the position's direction:
        a LONG is invalidated by losing the recent swing low, a SHORT by reclaiming
        the recent swing high.
        """
        if df_15m is None or len(df_15m) <= lookback:
            return False
        try:
            close = float(df_15m['close'].iloc[-1])
            if direction == "LONG":
                return close < float(df_15m['low'].iloc[-lookback:-1].min())
            return close > float(df_15m['high'].iloc[-lookback:-1].max())
        except Exception:
            return False

    def calculate_invalidation_score(self, pos: dict, current_price: float, structure_flipped: bool, df_15m=None) -> dict:
        """Calculates dynamic Early Exit / Invalidation Score (0% to 100%) across multiple factors."""
        direction = pos.get("direction", "LONG")
        entry = pos.get("entry_price", current_price)
        sl = pos.get("stop_loss", current_price)
        margin = pos.get("margin", 50.0)
        leverage = pos.get("leverage", 15)
        notional = margin * leverage

        score = 0
        factors = []

        # 1. Market Structure Break (+40%)
        if structure_flipped:
            score += 40
            factors.append("15m Swing Structure Break (CHoCH / BOS Flipped) [+40%]")

        # 2. Price Position Relative to Entry vs SL (+20%)
        sl_distance = abs(entry - sl)
        if sl_distance > 0:
            adverse_dist = abs(entry - current_price) if ((direction == "LONG" and current_price < entry) or (direction == "SHORT" and current_price > entry)) else 0
            adverse_pct = adverse_dist / sl_distance
            if adverse_pct >= 0.5:
                score += 20
                factors.append(f"Adverse Drawdown ({adverse_pct*100:.0f}% of SL distance crossed) [+20%]")

        if df_15m is not None and len(df_15m) >= 20:
            closes = df_15m['close']
            volumes = df_15m['volume']

            # 3. EMA Momentum Counter-Crossover (+20%)
            ema9 = closes.ewm(span=9, adjust=False).mean()
            ema21 = closes.ewm(span=21, adjust=False).mean()
            if direction == "LONG" and ema9.iloc[-1] < ema21.iloc[-1]:
                score += 20
                factors.append("15m EMA Momentum Bearish Cross [+20%]")
            elif direction == "SHORT" and ema9.iloc[-1] > ema21.iloc[-1]:
                score += 20
                factors.append("15m EMA Momentum Bullish Cross [+20%]")

            # 4. Adverse Volume Spike (+20%) — only counts if the surge is AGAINST us.
            # A volume surge in our favour is confirmation, not invalidation.
            vol_sma = volumes.iloc[-20:].mean()
            curr_vol = volumes.iloc[-1]
            if curr_vol > vol_sma * 1.5:
                last_bar_up = float(closes.iloc[-1]) > float(df_15m['open'].iloc[-1])
                adverse = (direction == "LONG" and not last_bar_up) or (direction == "SHORT" and last_bar_up)
                if adverse:
                    score += 20
                    factors.append(f"Adverse Volume Surge ({curr_vol/max(vol_sma, 0.01):.1f}x 20-bar avg) [+20%]")

        score = min(score, 100)

        # Calculate Saved Dollars vs Full SL
        full_sl_loss = abs(notional * (abs(entry - sl) / max(entry, 0.0001)))
        current_loss = abs(notional * (abs(entry - current_price) / max(entry, 0.0001)))
        saved_usd = max(round(full_sl_loss - current_loss, 2), 0.0)

        if score >= 75:
            urgency = "🚨 URGENT INVALIDATION (75%+ Conviction)"
        elif score >= 50:
            urgency = "⚠️ MODERATE INVALIDATION (50%+ Conviction)"
        else:
            urgency = "ℹ️ MINOR FRICTION (<50% Conviction)"

        return {
            "invalidation_score": score,
            "urgency_label": urgency,
            "factors": factors,
            "saved_usd": saved_usd
        }

    def _send_decay_alert(self, ticker, pos, price, decay):
        direction = pos.get("direction", "LONG")
        dot = "\U0001F7E2" if direction == "LONG" else "\U0001F534"
        factors = "\n".join(f"\u2022 {f}" for f in decay["factors"][:4])
        urgent = decay["recommendation"] == "CLOSE_NOW"
        head = (f"\U0001F6A8 **EXIT NOW: {ticker} HAS STOPPED WORKING** {dot}" if urgent
                else f"\u26A0\uFE0F **{ticker} LOSING MOMENTUM \u2014 CONSIDER SCRATCHING** {dot}")
        action = ("\U0001F525 **ACTION:** Close at market on Bitunix/Weex now. This trade is not "
                  "reaching target \u2014 exiting here preserves most of the margin."
                  if urgent else
                  "\u26A1 **ACTION:** Move stop to breakeven, or scratch at market.")
        msg = (f"{head}\n"
               "\u2501" * 28 + "\n"
               f"\U0001F4C9 **DECAY SCORE:** `{decay['decay_score']:.0f}/100`\n"
               f"\U0001F4CD **Entry:** `{self._format_price(pos.get('entry_price', price))}`\n"
               f"\u26A1 **Now:** `{self._format_price(price)}`\n"
               f"\U0001F4CA **Progress:** `{decay['progress_r']}R` of `{decay['target_r']}R` target, "
               f"after `{decay['time_used_pct']:.0f}%` of expected time (`{decay['bars_held']}` bars)\n"
               f"\U0001F4B5 **Unrealised:** `${decay['unrealised_usd']:,.2f}` | "
               f"**Saved vs full stop:** `+${decay['capital_saved_vs_stop']:,.2f}`\n\n"
               f"\U0001F4CB **WHY IT IS DYING:**\n{factors}\n\n"
               f"_{decay['reason']}_\n{action}\n" + "\u2501" * 28)
        self.send_telegram_alert(msg)
        print(f"[decay] {ticker} {decay['recommendation']} score={decay['decay_score']}", flush=True)

    def check_active_positions(self, ticker: str, current_price: float, sentiment_multiplier: float, structure_flipped: bool, df_15m=None):
        positions = self.load_positions()
        if not positions:
            return

        remaining_positions = []
        modified = False

        for pos in positions:
            if pos.get("ticker") != ticker:
                remaining_positions.append(pos)
                continue

            entry = pos.get("entry_price", current_price)
            sl = pos.get("stop_loss", current_price)
            tp = pos.get("take_profit", current_price)
            direction = pos.get("direction", "LONG")
            factor_scores = pos.get("factor_scores", {})
            win_rate_at_entry = pos.get("win_rate", 0.0)
            user_positioned = pos.get("user_positioned", False)

            tp_hit = (direction == "LONG" and current_price >= tp) or (direction == "SHORT" and current_price <= tp)
            sl_hit = (direction == "LONG" and current_price <= sl) or (direction == "SHORT" and current_price >= sl)

            if tp_hit:
                alert_key = f"{ticker}_TP_HIT_{int(pos.get('epoch_time', 0))}"
                if alert_key not in self.notified_milestones:
                    pnl_data = self._calculate_real_pnl(pos, current_price)
                    
                    # Record to BOTH tracking files (fixes self-learning disconnect)
                    eff = EngineEfficiencyTracker.record_trade_outcome(
                        ticker, direction, entry, current_price, "WIN", pnl_data["pnl_usd"],
                        factor_scores=factor_scores,
                        win_rate_at_entry=win_rate_at_entry,
                        user_positioned=user_positioned
                    )

                    # Dynamic emojis
                    dir_dot = "🟢" if direction == "LONG" else "🔴"
                    big_win = pnl_data["roi_pct"] >= 50.0
                    win_emoji = "🏆" if big_win else "🎉"

                    msg = f"""
{win_emoji} **ENGINE WIN: {ticker} HIT TP!** {dir_dot}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📍 **Entry:** `{self._format_price(entry)}`
🎯 **TP Exit:** `{self._format_price(current_price)}`
📈 **Real PnL:** `+${pnl_data['pnl_usd']:,.2f} USDT` (+{pnl_data['roi_pct']}% ROI)
💰 **Margin:** `${pnl_data['margin']:,.2f}` ({pnl_data['leverage']}x)
{"👤 **You positioned this trade!**" if user_positioned else ""}

📊 **ENGINE ACCURACY**
• Win Rate: `{eff['realized_win_rate']}%` ({eff['total_wins']}W / {eff['total_losses']}L)
• Net PnL: `${eff['total_engine_pnl_usd']:,.2f} USDT`
• Profit Factor: `{eff['profit_factor']}`
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                    """
                    self.send_telegram_alert(msg)
                    self.notified_milestones[alert_key] = True
                    # Clears any loss streak on this ticker.
                    SignalCooldownEngine.record_outcome(ticker, direction, is_win=True)
                modified = True

            elif sl_hit:
                alert_key = f"{ticker}_SL_HIT_{int(pos.get('epoch_time', 0))}"
                if alert_key not in self.notified_milestones:
                    pnl_data = self._calculate_real_pnl(pos, current_price)
                    
                    # Record to BOTH tracking files
                    eff = EngineEfficiencyTracker.record_trade_outcome(
                        ticker, direction, entry, current_price, "LOSS", pnl_data["pnl_usd"],
                        factor_scores=factor_scores,
                        win_rate_at_entry=win_rate_at_entry,
                        user_positioned=user_positioned
                    )

                    dir_dot = "🟢" if direction == "LONG" else "🔴"

                    msg = f"""
💔 **ENGINE LOSS: {ticker} HIT SL** {dir_dot}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📍 **Entry:** `{self._format_price(entry)}`
🛡️ **SL Exit:** `{self._format_price(current_price)}`
📉 **Real Loss:** `-${abs(pnl_data['pnl_usd']):,.2f} USDT` ({pnl_data['roi_pct']}% ROI)
💰 **Margin:** `${pnl_data['margin']:,.2f}` ({pnl_data['leverage']}x)
{"👤 **You positioned this trade!**" if user_positioned else ""}

📊 **ENGINE ACCURACY**
• Win Rate: `{eff['realized_win_rate']}%` ({eff['total_wins']}W / {eff['total_losses']}L)
• Net PnL: `${eff['total_engine_pnl_usd']:,.2f} USDT`

🧠 **Self-Learning:** Engine will penalize similar setups on {ticker}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                    """
                    self.send_telegram_alert(msg)
                    self.notified_milestones[alert_key] = True
                    # Locks this direction for 2h; a second consecutive loss locks the
                    # whole ticker for 6h. The opposite direction stays available.
                    SignalCooldownEngine.record_outcome(ticker, direction, is_win=False)
                modified = True

            elif structure_flipped and not (tp_hit or sl_hit):
                alert_key = f"{ticker}_EARLY_EXIT_{int(pos.get('epoch_time', 0))}"
                if alert_key not in self.notified_milestones:
                    pnl_data = self._calculate_real_pnl(pos, current_price)
                    inv_meta = self.calculate_invalidation_score(pos, current_price, structure_flipped, df_15m)
                    
                    inv_score = inv_meta["invalidation_score"]
                    urgency = inv_meta["urgency_label"]
                    saved_usd = inv_meta["saved_usd"]
                    factors_text = "\n".join([f"• {f}" for f in inv_meta["factors"]])
                    dir_dot = "🟢" if direction == "LONG" else "🔴"

                    if inv_score >= 75:
                        msg = f"""
🚨 **URGENT COMMAND: CLOSE {ticker} IMMEDIATELY AT MARKET!** {dir_dot}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚡ **100% HIGH-CONFIDENCE INVALIDATION ({inv_score}% SCORE)**
📍 **Entry:** `{self._format_price(entry)}`
⚡ **Current Price:** `{self._format_price(current_price)}`
📉 **Unrealized PnL:** `${pnl_data['pnl_usd']:,.2f} USDT` ({pnl_data['roi_pct']}% ROI)
💵 **CAPITAL SAVED VS SL:** `+${saved_usd:,.2f} USDT` (Preserves margin!)

📋 **HIGH-CONFIDENCE INVALIDATION REASONS:**
{factors_text}

🔥 **ACTION REQUIRED:** Close trade AT MARKET on Bitunix/Weex IMMEDIATELY to prevent full SL loss!
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                        """
                    else:
                        msg = f"""
⚠️ **EARLY EXIT WARNING: {ticker} INVALIDATION SCORE {inv_score}%** {dir_dot}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 **URGENCY:** `{urgency}`
📍 **Entry:** `{self._format_price(entry)}`
⚡ **Current Price:** `{self._format_price(current_price)}`
📉 **Unrealized PnL:** `${pnl_data['pnl_usd']:,.2f} USDT` ({pnl_data['roi_pct']}% ROI)
💵 **Capital Saved vs SL:** `+${saved_usd:,.2f} USDT`

📋 **INVALIDATION DIMENSIONS:**
{factors_text}

⚡ **ACTION:** Consider closing position early at market to preserve capital.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                        """
                    self.send_telegram_alert(msg)
                    self.notified_milestones[alert_key] = True
                remaining_positions.append(pos)

            else:
                # Not stopped, not targeted, structure intact — but is it still WORKING?
                # This is the sideways-decay case: a pumped pair that stalls after the
                # session ends and bleeds into the stop without ever breaking structure.
                decay = TradeDecayEngine.analyze(pos, df_15m, current_price)
                if decay.get("available") and decay["recommendation"] in ("CLOSE_NOW", "TIGHTEN_OR_SCRATCH"):
                    key = f"{ticker}_DECAY_{decay['recommendation']}_{int(pos.get('epoch_time', 0))}"
                    if key not in self.notified_milestones:
                        self._send_decay_alert(ticker, pos, current_price, decay)
                        self.notified_milestones[key] = True
                remaining_positions.append(pos)

        if modified:
            self.save_positions(remaining_positions)
