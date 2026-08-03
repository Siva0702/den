# models/news/emergency_wire.py
import requests

class EmergencyMacroWireEngine:
    """
    Den Engine v14.0 Quantum Unscheduled Emergency News & Flash Wire Engine:
    Monitors unscheduled breaking news, emergency White House / President addresses,
    unscheduled Fed emergency meetings, flash tariffs, geopolitical shocks, SEC emergency orders.
    Refreshes continuously in sub-minute scan loops.
    """

    EMERGENCY_KEYWORDS = [
        "emergency meeting", "white house address", "trump announcement", 
        "flash tariff", "emergency fed", "sec emergency order", "geopolitical shock",
        "unscheduled address", "breaking wire", "national emergency", "executive order"
    ]

    @classmethod
    def scan_emergency_wire(cls) -> dict:
        url = "https://news.google.com/rss/search?q=emergency+meeting+OR+Trump+announcement+OR+White+House+address+OR+Fed+emergency+when:1d&hl=en-US&gl=US&ceid=US:en"
        
        is_emergency = False
        emergency_status = "NORMAL_MARKET_WIRE"
        wire_multiplier = 1.0
        emergency_headline = "No unscheduled emergency White House or Fed meetings detected."

        try:
            resp = requests.get(url, timeout=3)
            if resp.status_code == 200:
                text = resp.text.lower()
                for keyword in cls.EMERGENCY_KEYWORDS:
                    if keyword in text:
                        is_emergency = True
                        emergency_status = f"🚨 EMERGENCY UNSECURED EVENT DETECTED: {keyword.upper()}"
                        wire_multiplier = 1.30 # High volatility shock multiplier
                        emergency_headline = f"Breaking Wire: Unscheduled {keyword.title()} in progress. High volatility impulse active."
                        break
        except Exception as e:
            print(f"[!] Emergency Wire Scraper Exception: {e}")

        return {
            "is_emergency": is_emergency,
            "emergency_status": emergency_status,
            "wire_multiplier": wire_multiplier,
            "emergency_headline": emergency_headline
        }
