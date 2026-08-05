# models/news/emergency_wire.py
import requests

class EmergencyMacroWireEngine:
    """
    Den Engine v15.1 Refined Emergency Wire Engine:
    Requires strict exact-match breaking wire conditions to prevent false positive multiplier spikes.
    """

    EXACT_EMERGENCY_TRIGGERS = [
        "breaking: emergency meeting", "white house emergency address",
        "fed emergency rate cut", "sec emergency halt order",
        "flash tariff announcement", "breaking: war declared"
    ]

    @classmethod
    def scan_emergency_wire(cls) -> dict:
        url = "https://news.google.com/rss/search?q=White+House+emergency+address+OR+Fed+emergency+rate+cut+when:1d&hl=en-US&gl=US&ceid=US:en"
        
        is_emergency = False
        emergency_status = "NORMAL_MARKET_WIRE"
        wire_multiplier = 1.0
        emergency_headline = "Normal market wire operating within standard parameters."

        try:
            resp = requests.get(url, timeout=3)
            if resp.status_code == 200:
                text = resp.text.lower()
                for keyword in cls.EXACT_EMERGENCY_TRIGGERS:
                    if keyword in text:
                        is_emergency = True
                        emergency_status = f"🚨 BREAKING EMERGENCY WIRE: {keyword.upper()}"
                        wire_multiplier = 1.20
                        emergency_headline = f"Breaking Wire: {keyword.title()} active."
                        break
        except Exception as e:
            print(f"[!] Emergency Wire Exception: {e}")

        return {
            "is_emergency": is_emergency,
            "emergency_status": emergency_status,
            "wire_multiplier": wire_multiplier,
            "emergency_headline": emergency_headline
        }
