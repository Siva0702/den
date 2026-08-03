# models/news/macro_events.py
import requests
import datetime

class USMacroEventEngine:
    """
    Den Engine v7.0 Macro Event & US Key Decision Engine:
    1. US Federal Reserve (FOMC Rate Decisions & Speech Sentiment)
    2. US CPI / PPI Inflation Releases
    3. SEC Regulatory & Global Geopolitical Wire Events
    """

    @staticmethod
    def get_macro_event_multiplier() -> dict:
        today = datetime.date.today()
        
        # Keyword scan on global headlines
        url = "https://news.google.com/rss/search?q=Federal+Reserve+OR+US+CPI+OR+SEC+crypto+when:1d&hl=en-US&gl=US&ceid=US:en"
        event_impact = "NORMAL"
        multiplier = 1.05

        try:
            resp = requests.get(url, timeout=4)
            if resp.status_code == 200:
                text = resp.text.lower()
                if any(w in text for w in ["rate cut", "fed dovish", "inflation cooling", "stimulus"]):
                    event_impact = "BULLISH_MACRO_TAILWIND"
                    multiplier = 1.15
                elif any(w in text for w in ["rate hike", "fed hawkish", "inflation surges", "sec lawsuit", "crackdown"]):
                    event_impact = "BEARISH_MACRO_HEADWIND"
                    multiplier = 0.85
        except Exception:
            pass

        return {
            "macro_event_impact": event_impact,
            "macro_multiplier": multiplier,
            "date": str(today)
        }
