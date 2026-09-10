# models/position_monitor.py
import json
import os
import requests
import sys
import time
import tempfile
import threading
from indicators.execution import advance_trade, LOGIC_VERSION

sys.path.append(os.path.dirname(__file__))
from audit.engine_efficiency import EngineEfficiencyTracker
from alerts.signal_cooldown import SignalCooldownEngine
from indicators.trade_decay import TradeDecayEngine

AUTO_SCRATCH_ON_DECAY = False   # see _send_decay_alert for the measurement
POSITIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "portfolio/active_positions.json")

class ActivePositionMonitor:
    """
    Den Engine v38.0 Real-Time Position Monitor & Engine Accuracy Tracker:
    - Monitors active positions against live exchange prices
    - Calculates REAL PnL from actual price movements
    - Records outcomes to BOTH audit/engine_efficiency.json AND portfolio/trade_history.json
    - Uses 🟢 for LONG wins, 🔴 for SHORT wins, 🏆 for big wins, 💔 for losses
    """

    _lock = threading.RLock()

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.notified_milestones = self.load_milestones()

    def load_milestones(self) -> dict:
        path = os.path.join(os.path.dirname(POSITIONS_FILE), "notified_milestones.json")
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def save_milestones(self):
        os.makedirs(os.path.dirname(POSITIONS_FILE), exist_ok=True)
        path = os.path.join(os.path.dirname(POSITIONS_FILE), "notified_milestones.json")
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
        os.makedirs(os.path.dirname(POSITIONS_FILE), exist_ok=True)
        with self._lock:
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(POSITIONS_FILE), suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(positions, f, indent=2, allow_nan=False)
                os.replace(tmp, POSITIONS_FILE)
                self.save_milestones()
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)

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
        # NOTE the explicit `+`. Without it Python concatenates the two adjacent string
        # literals at compile time and `* 28` then repeats the HEADER as well as the
        # rule, producing 28 copies of "LOSING MOMENTUM" in one alert.
        msg = (f"{head}\n"
               + "\u2501" * 28 + "\n"
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
        # The user treats a scratch alert as an executed close at breakeven, so the
        # audit must record it at that moment. Exit is the ENTRY price: scratching at
        # market is flat on the move, and the only cost booked is fees.
        # AUTO-SCRATCH IS OFF, and the measurement says it must stay off.
        #
        # Booking the decay alert as a breakeven close made a scratched trade unable to
        # win: it exits flat and still pays a full round-trip fee. 8 of the first 13
        # dispatched signals ended this way, including setups scoring 81.8 and 78.6.
        #
        # Meanwhile the shadow book — same model, same 78+ band, but allowed to run to
        # TP or SL — wins 72.9% at +0.708R out-of-sample over 318 trades. Stalling
        # momentum is ordinary noise inside a 73% setup, not a reason to exit.
        #
        # The alert still fires so the user can act. The engine no longer decides for
        # them, and the position resolves at TP or SL like every shadow trade it was
        # calibrated against.
        if AUTO_SCRATCH_ON_DECAY:
            try:
                from audit.dispatch_ledger import DispatchLedger
                DispatchLedger.record_close(pos, float(pos.get("entry_price") or price),
                                            "SCRATCHED_BREAKEVEN")
            except Exception as _e:
                print(f"[!] dispatch audit (scratch) failed: {_e}", flush=True)
        print(f"[decay] {ticker} {decay['recommendation']} score={decay['decay_score']}", flush=True)

    def check_active_positions(self, ticker, current_price, sentiment_multiplier,
                               structure_flipped, df_15m=None, bar_high=None, bar_low=None, bars=None):
        """Persist ordered-bar progress; use the same exits as shadow and replay."""
        from audit.dispatch_ledger import DispatchLedger
        with self._lock:
            positions = self.load_positions()
            remaining = []
            for pos in positions:
                if pos.get("ticker") != ticker:
                    remaining.append(pos)
                    continue
                # Reconstruct legacy open trades from entry rather than inherit old cursors.
                if pos.get("config_version") != LOGIC_VERSION:
                    for key in ("last_bar_ts", "tp_levels_hit", "first_tp_epoch"):
                        pos.pop(key, None)
                    pos["config_version"] = LOGIC_VERSION
                event = pos.get("execution_event") or advance_trade(pos, bars or [])
                if event is None:
                    remaining.append(pos)
                    continue
                pos["execution_event"] = event
                exit_price = event["exit_price"]
                # Never infer exit reason from a later sampled close. Persist the actual event.
                if not DispatchLedger.record_close(pos, exit_price, event["outcome"]):
                    remaining.append(pos)
                    continue
                notional = float(pos.get("margin", 0))*float(pos.get("leverage", 0))
                net_usd = notional*event["net_pnl_pct"]/100
                won = net_usd > 0
                EngineEfficiencyTracker.record_trade_outcome(
                    ticker, pos["direction"], pos["entry_price"], exit_price,
                    "WIN" if won else "LOSS", round(net_usd, 2),
                    factor_scores=pos.get("factor_scores", {}),
                    win_rate_at_entry=pos.get("win_rate", 0),
                    user_positioned=pos.get("user_positioned", False),
                    trade_id=pos.get("dispatch_id") or f"{ticker}|{pos.get('epoch_time')}")
                SignalCooldownEngine.record_outcome(ticker, pos["direction"], is_win=won)
                self.send_telegram_alert(
                    f"{'WIN' if won else 'LOSS'} | {ticker} {pos['direction']}\n"
                    f"Exit: {self._format_price(exit_price)} ({event['outcome']})\n"
                    f"Estimated net result: ${net_usd:+.2f} after modelled costs.\n"
                    "Paper execution; confirm actual exchange fills separately.")
            self.save_positions(remaining)
