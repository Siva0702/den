# models/news/regulatory_events.py
import requests
import xml.etree.ElementTree as ET

class USRegulatoryPolicyEngine:
    """
    Den Engine v38.0 US Legislative, Recess & Regulatory Policy Tracker:
    Monitors high-impact political, legislative, and regulatory catalysts in real-time:
    - Congressional Recess / Legislative Delays (e.g., Clarity Act delayed until Sept)
    - FIT21 / Digital Asset Market Structure Acts
    - SEC / CFTC Rulemaking & Enforcement Actions
    - Historical impact precedent tracking (e.g. Recess/Delay causing BTC $97K -> $61K -38% crash)
    """

    @classmethod
    def analyze_regulatory_climate(cls) -> dict:
        url = "https://news.google.com/rss/search?q=Congress+recess+OR+Clarity+Act+OR+FIT21+crypto+when:7d&hl=en-US&gl=US&ceid=US:en"
        
        status = "NEUTRAL_REGULATORY_CLIMATE"
        multiplier = 1.0
        headline_match = "US Market Structure & Regulatory Climate Operating Normally."
        is_recess_delay = False
        warning_msg = ""

        try:
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                root = ET.fromstring(resp.content)
                items = root.findall(".//item")
                
                titles = []
                for item in items[:15]:
                    t_elem = item.find("title")
                    if t_elem is not None and t_elem.text:
                        titles.append(t_elem.text.lower())

                full_text = " ".join(titles)

                # Specific Recess & Delay Triggers
                recess_keywords = ["recess", "recess countdown", "summer recess", "congress leaves", "deadline looms", "hopes fade", "delayed", "postponed", "uncertainty"]
                recess_matches = [k for k in recess_keywords if k in full_text]

                bullish_triggers = ["passed", "approved", "bipartisan support", "clarity act advances", "fit21 passed", "regulatory clarity", "signed into law"]
                bearish_triggers = ["delayed", "hopes fade", "recess", "vetoed", "sec lawsuit", "enforcement action", "crackdown", "rejected", "uncertainty holding crypto back"]

                bull_score = sum(1 for w in bullish_triggers if w in full_text)
                bear_score = sum(1 for w in bearish_triggers if w in full_text)

                if len(recess_matches) >= 2 or ("clarity act" in full_text and ("recess" in full_text or "fades" in full_text or "delayed" in full_text or "uncertainty" in full_text)):
                    is_recess_delay = True
                    status = "🚨 BEARISH REGULATORY HEADWIND: Congressional Recess & Clarity Act Delay"
                    multiplier = 0.65  # Heavy 35% penalty for LONGs / Boost for SHORTs
                    warning_msg = "⚠️ MACRO HEADWIND: Congress Recess & Clarity Act Delay (Historical Precedent: BTC $97K → $61K -38% Drop)"
                    
                    # Find best headline match
                    for title in titles:
                        if "recess" in title or "clarity" in title or "delay" in title or "fade" in title:
                            headline_match = title.title()
                            break

                elif bull_score > bear_score:
                    status = "🟢 BULLISH REGULATORY TAILWIND: Clarity Act Advancement"
                    multiplier = 1.30
                    headline_match = "US Crypto & Market Structure Legislation advancing with bipartisan momentum."
                elif bear_score > bull_score:
                    status = "🔴 BEARISH REGULATORY HEADWIND: Enforcement & Policy Delays"
                    multiplier = 0.75
                    headline_match = "Regulatory enforcement actions or legislative friction creating market resistance."

        except Exception as e:
            print(f"[!] USRegulatoryPolicyEngine RSS Error: {e}")

        return {
            "regulatory_status": status,
            "regulatory_multiplier": multiplier,
            "headline_match": headline_match,
            "is_recess_delay": is_recess_delay,
            "warning_msg": warning_msg
        }

if __name__ == "__main__":
    res = USRegulatoryPolicyEngine.analyze_regulatory_climate()
    print("=" * 60)
    print("      US REGULATORY & LEGISLATIVE CLIMATE AUDIT      ")
    print("=" * 60)
    print(f"Status: {res['regulatory_status']}")
    print(f"Multiplier: {res['regulatory_multiplier']}")
    print(f"Matched Headline: {res['headline_match']}")
    if res['warning_msg']:
        print(f"Warning: {res['warning_msg']}")
