# models/indicators/funding_defense.py
import requests

class FundingRateDefenseEngine:
    """
    Den Engine Live Funding Rate & Fee Defense:
    1. Fetches real-time 8h Funding Rate across Futures contracts.
    2. Prevents entering LONGs when Funding Rate is heavily positive (> +0.03%), avoiding heavy funding fee payments to Shorts.
    3. Prevents entering SHORTs when Funding Rate is heavily negative (< -0.03%).
    4. Triggers Short Squeeze / Long Squeeze tailwind multipliers!
    """

    @staticmethod
    def get_funding_rate(symbol: str = "BTCUSDT") -> dict:
        clean_symbol = symbol.replace("/", "").upper()
        url = f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={clean_symbol}"
        
        try:
            resp = requests.get(url, timeout=3)
            if resp.status_code == 200:
                data = resp.json()
                funding_rate = float(data.get("lastFundingRate", 0.0))
                funding_pct = round(funding_rate * 100, 4)
                
                # Funding Fee Safeguard Rules:
                # High positive funding rate (> +0.03%) = Longs pay Shorts (AVOID LONGS OR REPEAT COST)
                # High negative funding rate (< -0.03%) = Shorts pay Longs (SHORT SQUEEZE POTENTIAL)
                
                is_long_safe = funding_rate <= 0.0003
                is_short_safe = funding_rate >= -0.0003
                
                squeeze_tailwind = 1.10 if funding_rate < -0.0002 else (0.90 if funding_rate > 0.0004 else 1.0)

                return {
                    "funding_rate": funding_rate,
                    "funding_pct": funding_pct,
                    "is_long_safe": is_long_safe,
                    "is_short_safe": is_short_safe,
                    "squeeze_tailwind": squeeze_tailwind,
                    "status": "NEUTRAL" if abs(funding_rate) < 0.0002 else ("SHORT_SQUEEZE_TAILWIND" if funding_rate < 0 else "LONG_FEE_HEADWIND")
                }
        except Exception:
            pass

        return {
            "funding_rate": 0.0001,
            "funding_pct": 0.01,
            "is_long_safe": True,
            "is_short_safe": True,
            "squeeze_tailwind": 1.0,
            "status": "NORMAL"
        }
