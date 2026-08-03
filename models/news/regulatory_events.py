# models/news/regulatory_events.py
import requests

class USRegulatoryPolicyEngine:
    """
    Den Engine v10.3 US Legislative & Regulatory Policy Tracker:
    Monitors key legislative acts and regulatory developments:
    - US Clarity Act / FIT21 (Financial Innovation and Technology for 21st Century Act)
    - SAB 121 / Digital Asset Market Structure Legislation
    - SEC / CFTC Rulemaking & Federal Reserve Policy Acts
    - Corporate AI & Semiconductor Legislation (CHIPS Act / Antitrust)
    """

    KEY_LEGISLATION_KEYWORDS = [
        "clarity act", "fit21", "sab 121", "market structure act", 
        "sec approval", "cftc guidance", "chips act", "crypto legislation",
        "bipartisan crypto bill", "treasury regulation"
    ]

    @classmethod
    def analyze_regulatory_climate(cls) -> dict:
        url = "https://news.google.com/rss/search?q=Clarity+Act+OR+FIT21+OR+SEC+crypto+legislation+when:2d&hl=en-US&gl=US&ceid=US:en"
        
        status = "NEUTRAL_REGULATORY_CLIMATE"
        multiplier = 1.0
        headline_match = "US Market Structure & Regulatory Climate Trading Normal."

        try:
            resp = requests.get(url, timeout=4)
            if resp.status_code == 200:
                text = resp.text.lower()
                
                bullish_triggers = ["passed", "approved", "bipartisan support", "clarity act advances", "fit21 passed", "regulatory clarity"]
                bearish_triggers = ["delayed", "vetoed", "sec lawsuit", "enforcement action", "crackdown", "rejected"]

                bull_score = sum(1 for w in bullish_triggers if w in text)
                bear_score = sum(1 for w in bearish_triggers if w in text)

                if bull_score > bear_score:
                    status = "BULLISH_REGULATORY_TAILWIND (Clarity Act / Policy Support)"
                    multiplier = 1.20
                    headline_match = "US Crypto & Market Structure Legislation advancing with bipartisan momentum."
                elif bear_score > bull_score:
                    status = "BEARISH_REGULATORY_HEADWIND (Enforcement / Delay)"
                    multiplier = 0.80
                    headline_match = "Regulatory enforcement or legislative delays creating short-term market headwind."
        except Exception as e:
            print(f"[!] Regulatory Engine RSS Fallback: {e}")

        return {
            "regulatory_status": status,
            "regulatory_multiplier": multiplier,
            "headline_match": headline_match
        }
