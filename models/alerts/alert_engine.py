# models/alerts/alert_engine.py
import json
import requests
import sys
import os

# Import our Kelly and NLP engines
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
from models.crypto.kelly_calculator import calculate_kelly_position
from models.nlp.sentiment_engine import HuggingFaceSentimentEngine

class AntiGravityAlertEngine:
    def __init__(self, telegram_bot_token: str = None, telegram_chat_id: str = None):
        self.telegram_token = telegram_bot_token
        self.telegram_chat_id = telegram_chat_id
        self.nlp = HuggingFaceSentimentEngine()

    def generate_signal(
        self,
        ticker: str,
        asset_class: str,          # "Crypto", "Tokenized Equity", "Commodity"
        news_headline: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        base_win_rate: float,
        reward_to_risk: float,
        is_scalp: bool = False,
        account_balance: float = 1000.0
    ):
        # 1. Run NLP Sentiment Analysis
        sentiment = self.nlp.analyze_news(news_headline)
        sm = sentiment["sentiment_multiplier"]
        
        # 2. Adjust Win Rate via Sentiment Multiplier
        adjusted_win_rate = min(max(base_win_rate * sm, 0.35), 0.85)
        
        # 3. Calculate Kelly Math
        full_kelly = adjusted_win_rate - ((1.0 - adjusted_win_rate) / reward_to_risk)
        fraction = 0.25 if is_scalp else 0.50
        allocated_risk_pct = max(full_kelly * fraction, 0.01)
        dollars_at_risk = account_balance * allocated_risk_pct
        
        sl_dist = abs(entry_price - stop_loss) / entry_price
        position_usd = dollars_at_risk / sl_dist
        leverage = round(position_usd / (account_balance * 0.10))
        margin = position_usd / max(leverage, 1)

        trade_type = "QUARTER-KELLY SCALP" if is_scalp else "HALF-KELLY KEY POSITION"

        # 4. Format Structured Alert Payload
        alert_payload = f"""
🚀 **ANTI GRAVITY QUANT ALERT: {ticker} ({asset_class.upper()})**
────────────────────────────────────────
• **Setup Type:** {trade_type}
• **News Factor:** "{news_headline}"
• **NLP Sentiment:** {sentiment['dominant_sentiment']} (Multiplier: {sm}x)
• **Adjusted Win Rate (W):** {adjusted_win_rate*100:.1f}% | **R:R:** 1:{reward_to_risk:.2f}

📈 **EXECUTION LEVELS (Isolated Margin)**
• **Entry Price:** ${entry_price:,.4f}
• **Stop Loss:** ${stop_loss:,.4f} (-{sl_dist*100:.2f}%)
• **Take Profit:** ${take_profit:,.4f}
• **Recommended Leverage:** {leverage}x
• **Required Isolated Margin:** ${margin:,.2f} USDT
• **Capital at Risk:** ${dollars_at_risk:,.2f} USDT ({allocated_risk_pct*100:.2f}%)
────────────────────────────────────────
        """
        
        print(alert_payload)
        
        # 5. Dispatch Telegram Alert if credentials exist
        if self.telegram_token and self.telegram_chat_id:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            payload = {"chat_id": self.telegram_chat_id, "text": alert_payload, "parse_mode": "Markdown"}
            requests.post(url, json=payload)
            print("[✓] Signal dispatched to Telegram.")

if __name__ == "__main__":
    alert_system = AntiGravityAlertEngine()
    
    # Example 1: Tokenized Stock Trade Signal (NVDA Breakout)
    alert_system.generate_signal(
        ticker="NVDA/USDT (Tokenized Stock)",
        asset_class="Tokenized Equity",
        news_headline="US Commerce Dept approves major AI chip export licenses; Earnings momentum surges.",
        entry_price=125.00,
        stop_loss=121.00,
        take_profit=135.00,
        base_win_rate=0.55,
        reward_to_risk=2.5,
        is_scalp=False,
        account_balance=1000.0
    )
