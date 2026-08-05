# models/nlp/sentiment_engine.py
import requests

class HuggingFaceSentimentEngine:
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.api_url = "https://api-inference.huggingface.co/models/burakutf/finetuned-finbert-crypto"

    def analyze_news(self, text: str) -> dict:
        """
        Uses Hugging Face Serverless API (0 RAM overhead) with financial keyword fallback.
        Exits RAM limits completely to fit Render 512MB free tier effortlessly.
        """
        payload = {"inputs": text}
        try:
            resp = requests.post(self.api_url, json=payload, timeout=4)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list) and len(data) > 0:
                    scores = data[0] if isinstance(data[0], list) else data
                    neg = next((s['score'] for s in scores if s['label'].upper() in ['NEGATIVE', 'LABEL_0']), 0.1)
                    neu = next((s['score'] for s in scores if s['label'].upper() in ['NEUTRAL', 'LABEL_1']), 0.8)
                    pos = next((s['score'] for s in scores if s['label'].upper() in ['POSITIVE', 'LABEL_2']), 0.1)
                    
                    sentiment_score = pos - neg
                    sm = round(1.0 + (sentiment_score * 0.20), 2)
                    dominant = "Positive" if pos > max(neg, neu) else ("Negative" if neg > max(pos, neu) else "Neutral")
                    return {
                        "negative": round(neg, 4),
                        "neutral": round(neu, 4),
                        "positive": round(pos, 4),
                        "sentiment_multiplier": sm,
                        "dominant_sentiment": dominant
                    }
        except Exception as e:
            print(f"[!] HF API Fallback: {e}")

        # Intelligent Financial Keyword Engine (Zero RAM)
        text_lower = text.lower()
        pos_words = ["bullish", "surge", "cuts", "rate cut", "rally", "approval", "record", "growth", "influx", "buy", "expansion", "profit", "partner"]
        neg_words = ["bearish", "crash", "plunge", "hike", "rate hike", "ban", "lawsuit", "hack", "sec", "liquidation", "dump", "bankruptcy", "loss"]
        
        pos_count = sum(1 for w in pos_words if w in text_lower)
        neg_count = sum(1 for w in neg_words if w in text_lower)
        
        if pos_count > neg_count:
            sm = 1.15
            dominant = "Positive"
        elif neg_count > pos_count:
            sm = 0.85
            dominant = "Negative"
        else:
            sm = 1.03
            dominant = "Neutral"

        return {
            "negative": 0.1,
            "neutral": 0.8,
            "positive": 0.1,
            "sentiment_multiplier": sm,
            "dominant_sentiment": dominant
        }

if __name__ == "__main__":
    engine = HuggingFaceSentimentEngine.get_instance()
    headline = "US Federal Reserve signals aggressive rate cuts; Liquidity influx expected across risk assets."
    result = engine.analyze_news(headline)
    print("=" * 60)
    print(f"Dominant Sentiment   : {result['dominant_sentiment']}")
    print(f"Sentiment Multiplier : {result['sentiment_multiplier']}x")
    print("=" * 60)
