# models/nlp/ensemble_sentiment.py
import requests

class HuggingFaceEnsembleSentimentEngine:
    """
    Den Engine v4.0 Hugging Face Multi-Model Ensemble:
    Combines outputs from 3 specialized financial transformer models:
    1. Crypto & Volatility Model: burakutf/finetuned-finbert-crypto
    2. Fed Macro & Corporate Earnings Model: yiyanghkust/finbert-tone
    3. Core Financial News Classifier: ProsusAI/finbert
    Uses zero-RAM serverless HTTP requests for 100% free 24/7 cloud execution.
    """
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.endpoints = [
            "https://api-inference.huggingface.co/models/burakutf/finetuned-finbert-crypto",
            "https://api-inference.huggingface.co/models/yiyanghkust/finbert-tone",
            "https://api-inference.huggingface.co/models/ProsusAI/finbert"
        ]

    def analyze_news_ensemble(self, text: str) -> dict:
        scores_list = []
        payload = {"inputs": text}

        for endpoint in self.endpoints:
            try:
                resp = requests.post(endpoint, json=payload, timeout=3)
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list) and len(data) > 0:
                        scores = data[0] if isinstance(data[0], list) else data
                        neg = next((s['score'] for s in scores if s['label'].upper() in ['NEGATIVE', 'LABEL_0', 'BEARISH']), 0.1)
                        pos = next((s['score'] for s in scores if s['label'].upper() in ['POSITIVE', 'LABEL_2', 'BULLISH']), 0.1)
                        scores_list.append(pos - neg)
            except Exception:
                pass

        if scores_list:
            avg_score = sum(scores_list) / len(scores_list)
            sm = round(1.0 + (avg_score * 0.22), 2)
            dominant = "Positive" if avg_score > 0.05 else ("Negative" if avg_score < -0.05 else "Neutral")
            return {
                "ensemble_score": round(avg_score, 4),
                "sentiment_multiplier": sm,
                "dominant_sentiment": dominant,
                "models_queried": len(scores_list)
            }

        # High-Fidelity Keyword Fallback
        text_lower = text.lower()
        pos_words = ["bullish", "surge", "cuts", "rate cut", "rally", "approval", "record", "growth", "influx", "buy", "expansion", "profit", "partner", "earnings", "beat"]
        neg_words = ["bearish", "crash", "plunge", "hike", "rate hike", "ban", "lawsuit", "hack", "sec", "liquidation", "dump", "bankruptcy", "loss", "miss", "default"]
        
        pos_count = sum(1 for w in pos_words if w in text_lower)
        neg_count = sum(1 for w in neg_words if w in text_lower)
        
        if pos_count > neg_count:
            sm = 1.18
            dominant = "Positive"
        elif neg_count > pos_count:
            sm = 0.82
            dominant = "Negative"
        else:
            sm = 1.03
            dominant = "Neutral"

        return {
            "ensemble_score": 0.0,
            "sentiment_multiplier": sm,
            "dominant_sentiment": dominant,
            "models_queried": 0
        }

if __name__ == "__main__":
    engine = HuggingFaceEnsembleSentimentEngine.get_instance()
    headline = "US Federal Reserve signals aggressive rate cuts; Semiconductor & Risk Asset momentum surges."
    res = engine.analyze_news_ensemble(headline)
    print("Ensemble Sentiment Output:", res)
