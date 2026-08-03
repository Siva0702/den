# models/alerts/telegram_bot.py
import requests

class TelegramAlertBot:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

    def send_alert(self, message_text: str) -> bool:
        """
        Sends an instant push alert to your phone via Telegram.
        """
        payload = {
            "chat_id": self.chat_id,
            "text": message_text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True
        }
        try:
            response = requests.post(self.api_url, json=payload, timeout=5)
            return response.status_code == 200
        except Exception as e:
            print(f"[!] Telegram Alert Failed: {e}")
            return False

if __name__ == "__main__":
    import os
    BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN_HERE")
    CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "YOUR_TELEGRAM_CHAT_ID_HERE")
    
    bot = TelegramAlertBot(BOT_TOKEN, CHAT_ID)
    print("Telegram Alert Bot initialized ready for deployment.")
