# models/nlp/sentiment_engine.py
from transformers import AutoTokenizer, AutoModelForSequenceClassification
import torch

class HuggingFaceSentimentEngine:
    def __init__(self):
        # Load crypto-specific FinBERT model from Hugging Face
        self.crypto_model_name = "burakutf/finetuned-finbert-crypto"
        self.tokenizer = AutoTokenizer.from_pretrained(self.crypto_model_name)
        self.model = AutoModelForSequenceClassification.from_pretrained(self.crypto_model_name)
        self.labels = {0: "Negative", 1: "Neutral", 2: "Positive"}

    def analyze_news(self, text: str) -> dict:
        inputs = self.tokenizer(text, return_tensors="pt", truncation=True, padding=True, max_length=128)
        with torch.no_grad():
            outputs = self.model(**inputs)
            probs = torch.softmax(outputs.logits, dim=1).squeeze().tolist()
        
        neg, neu, pos = probs[0], probs[1], probs[2]
        
        # Calculate Sentiment Multiplier (Sm) between 0.80 and 1.20
        # Positive news boosts baseline win rate expectation; Negative depresses it.
        sentiment_score = pos - neg
        sentiment_multiplier = round(1.0 + (sentiment_score * 0.20), 2)
        
        return {
            "negative": round(neg, 4),
            "neutral": round(neu, 4),
            "positive": round(pos, 4),
            "sentiment_multiplier": sentiment_multiplier,
            "dominant_sentiment": self.labels[probs.index(max(probs))]
        }

if __name__ == "__main__":
    engine = HuggingFaceSentimentEngine()
    
    # Test Macro Event
    headline = "US Federal Reserve signals aggressive rate cuts; Liquidity influx expected across risk assets."
    result = engine.analyze_news(headline)
    
    print("=" * 60)
    print("      ANTI GRAVITY HF SENTIMENT ANALYSIS ENGINE")
    print("=" * 60)
    print(f"Headline: {headline}")
    print(f"Dominant Sentiment   : {result['dominant_sentiment']}")
    print(f"Sentiment Multiplier : {result['sentiment_multiplier']}x")
    print("=" * 60)
