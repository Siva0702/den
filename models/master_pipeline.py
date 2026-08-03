# models/master_pipeline.py
import sys
import os
import pandas as pd

# Path routing
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from models.nlp.sentiment_engine import HuggingFaceSentimentEngine
from models.news.news_fetcher import RealtimeNewsFetcher
from models.indicators.technical_engine import TechnicalAnalysisEngine
from models.alerts.telegram_bot import TelegramAlertBot

def run_a_to_z_quant_pipeline(
    ticker: str,
    asset_class: str,
    ohlcv_data: pd.DataFrame,
    is_scalp: bool = False,
    account_balance: float = 1000.0,
    telegram_token: str = None,
    telegram_chat_id: str = None
):
    print("=" * 60)
    print(f"      RUNNING A TO Z QUANT ANALYSIS PIPELINE: {ticker}      ")
    print("=" * 60)

    # 1. Technical Analysis Engine
    tech = TechnicalAnalysisEngine.calculate_indicators(ohlcv_data)
    entry_price = tech['close_price']
    
    # Set Stop Loss based on ATR (1.5x ATR distance)
    sl_distance = tech['atr'] * 1.5
    stop_loss = entry_price - sl_distance if tech['ema_trend'] == "BULLISH" else entry_price + sl_distance
    take_profit = entry_price + (sl_distance * 2.5) if tech['ema_trend'] == "BULLISH" else entry_price - (sl_distance * 2.5)
    
    # 2. Fetch Breaking News Wire
    news_engine = RealtimeNewsFetcher()
    headlines = news_engine.fetch_latest_headlines(limit=1)
    headline_text = headlines[0]['headline'] if headlines else "Market showing balanced momentum."
    
    # 3. Hugging Face Sentiment Model (uses singleton instance to load weights once)
    nlp = HuggingFaceSentimentEngine.get_instance()
    sentiment = nlp.analyze_news(headline_text)
    
    # 4. Base Probability & Kelly Sizing
    base_win_rate = 0.55
    adjusted_win_rate = min(max(base_win_rate * sentiment['sentiment_multiplier'], 0.35), 0.85)
    reward_to_risk = 2.5
    
    full_kelly = adjusted_win_rate - ((1.0 - adjusted_win_rate) / reward_to_risk)
    fraction = 0.25 if is_scalp else 0.50
    risk_pct = max(full_kelly * fraction, 0.01)
    dollars_at_risk = account_balance * risk_pct
    
    sl_pct = abs(entry_price - stop_loss) / entry_price
    position_value = dollars_at_risk / sl_pct
    leverage = max(round(position_value / (account_balance * 0.10)), 1)
    margin = position_value / leverage

    trade_type = "QUARTER-KELLY SCALP" if is_scalp else "HALF-KELLY KEY POSITION"

    # 5. Format Telegram Push Alert Message
    alert_msg = f"""
🚨 **ANTI GRAVITY A-Z QUANT SIGNAL** 🚨
━━━━━━━━━━━━━━━━━━━━━━━━
• **Asset:** `{ticker}` ({asset_class})
• **Setup:** `{trade_type}`
• **Trend:** `{tech['ema_trend']}` | **RSI:** `{tech['rsi']}` | **FVG:** `{tech['fvg_detected']}`

📰 **NEWS & AI SENTIMENT**
• **Headline:** "{headline_text}"
• **NLP Sentiment:** `{sentiment['dominant_sentiment']}` ({sentiment['sentiment_multiplier']}x Multiplier)
• **Model Win Rate (W):** `{adjusted_win_rate*100:.1f}%`

⚡ **EXECUTION COMMAND (Bitunix Isolated)**
• **Direction:** `{"LONG" if tech['ema_trend'] == "BULLISH" else "SHORT"}`
• **Entry Price:** `${entry_price:,.4f}`
• **Stop Loss:** `${stop_loss:,.4f}` (-{sl_pct*100:.2f}%)
• **Take Profit:** `${take_profit:,.4f}`
• **Leverage:** `{leverage}x Isolated`
• **Required Margin:** `${margin:,.2f} USDT`
• **Capital at Risk:** `${dollars_at_risk:,.2f} USDT` ({risk_pct*100:.2f}%)
━━━━━━━━━━━━━━━━━━━━━━━━
    """

    print(alert_msg)

    # 6. Dispatch Alert via Telegram Bot
    token = telegram_token or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = telegram_chat_id or os.getenv("TELEGRAM_CHAT_ID")
    if token and chat_id and token != "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        bot = TelegramAlertBot(token, chat_id)
        bot.send_alert(alert_msg)
        print("[✓] Push alert dispatched to phone.")

if __name__ == "__main__":
    import numpy as np
    # Generate Synthetic Candle Data for Testing Pipeline
    np.random.seed(42)
    dates = pd.date_range(end=pd.Timestamp.now(), periods=100, freq='15min')
    prices = 150 + np.cumsum(np.random.randn(100) * 0.5)
    df = pd.DataFrame({
        'open': prices,
        'high': prices + np.random.rand(100),
        'low': prices - np.random.rand(100),
        'close': prices + np.random.randn(100) * 0.2,
        'volume': np.random.randint(100, 1000, size=100)
    }, index=dates)

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    run_a_to_z_quant_pipeline(
        ticker="SOL/USDT",
        asset_class="Crypto Futures",
        ohlcv_data=df,
        is_scalp=False,
        account_balance=1000.0,
        telegram_token=token,
        telegram_chat_id=chat_id
    )
