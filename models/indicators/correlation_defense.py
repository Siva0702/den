# models/indicators/correlation_defense.py
import json
import os

POSITIONS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "portfolio/active_positions.json")

class CorrelationDefenseEngine:
    """
    Den Engine v15.0 Correlation & Portfolio Overlap Defense:
    Prevents taking multiple highly-correlated positions simultaneously (e.g., ETH + SOL + AVAX)
    to eliminate hidden portfolio leverage exposure.
    """

    CORRELATED_GROUPS = {
        "CRYPTO_L1": ["BTC/USDT", "ETH/USDT", "SOL/USDT", "AVAX/USDT", "NEAR/USDT", "SUI/USDT", "ADA/USDT"],
        "CRYPTO_MEME": ["DOGE/USDT", "PEPE/USDT", "WIF/USDT"],
        "US_SEMIS": ["NVDA/USDT", "AMD/USDT", "AVGO/USDT", "QCOM/USDT", "MU/USDT", "ARM/USDT", "SMCI/USDT"],
        "BIG_TECH": ["AAPL/USDT", "MSFT/USDT", "GOOGL/USDT", "AMZN/USDT", "META/USDT"]
    }

    @classmethod
    def check_pending(cls, new_ticker: str, direction: str, pending: list) -> tuple:
        """
        Correlation guard across signals dispatched IN THE SAME SCAN.

        The engine may fire up to 3 signals per scan, and nothing stopped all three
        being BTC, ETH and SOL long — one directional bet at triple size, dressed as
        diversification. This checks the new candidate against both open positions and
        the signals already queued this cycle.
        """
        group = None
        for g, members in cls.CORRELATED_GROUPS.items():
            if new_ticker in members:
                group = g
                break
        if not group:
            return True, "uncorrelated"
        for p in pending:
            if p.get("direction") != direction:
                continue          # opposite directions are a hedge, not a doubling
            if p.get("ticker") in cls.CORRELATED_GROUPS[group]:
                return False, f"correlated with {p['ticker']} in {group} (same direction)"
        return True, f"clear in {group}"

    @classmethod
    def check_correlation_overlap(cls, new_ticker: str) -> bool:
        if not os.path.exists(POSITIONS_FILE):
            return False

        try:
            with open(POSITIONS_FILE, "r") as f:
                positions = json.load(f)
                active_tickers = [p.get("ticker") for p in positions]

                for group_name, members in cls.CORRELATED_GROUPS.items():
                    if new_ticker in members:
                        # Count active positions in same correlation group
                        group_active_count = sum(1 for t in active_tickers if t in members)
                        if group_active_count >= 2:
                            print(f"[🛡️] Correlation Defense Blocked {new_ticker}: Already 2 active positions in {group_name}")
                            return True # Overlap detected! Block trade
        except Exception:
            pass

        return False
