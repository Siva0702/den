# models/news/news_intelligence.py
import re
import time
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

class PerAssetNewsIntelligence:
    """
    Den Engine v39.0 Per-Asset Real-Time News Intelligence.

    Replaces the previous single global keyword sweep (which asked one crypto-policy
    question and applied the answer to all 87 assets) with a genuine per-symbol read.

    For every asset it resolves a real-world search entity (NVDA -> "NVIDIA stock",
    BTC/USDT -> "Bitcoin crypto"), pulls a fresh Google News RSS window, then scores
    each headline on four independent axes:

      1. Direction   — bullish / bearish lexicon, weighted by headline strength
      2. Recency     — a 2-hour-old headline outweighs a 5-day-old one
      3. Event risk  — earnings, FDA, guidance, halt, lawsuit: gap risk, block entry
      4. Manipulation— pump/dump, whale, wash-trading, unusual-move language

    Returns a bounded multiplier and explicit blocking flags. Everything degrades to
    neutral (1.0, no block) on failure — news must never fabricate conviction.
    """

    HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                             "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

    _cache = {}
    _lock = threading.Lock()
    TTL = 600.0          # 10 minutes — news is not a 15-second quantity
    TTL_EMPTY = 180.0

    # Real-world entity per symbol. Without this, a query for "W/USDT" or "V/USDT"
    # returns noise and the engine acts on garbage.
    ENTITY = {
        "BTC/USDT": "Bitcoin", "ETH/USDT": "Ethereum", "SOL/USDT": "Solana",
        "XRP/USDT": "XRP Ripple", "DOGE/USDT": "Dogecoin", "AVAX/USDT": "Avalanche crypto",
        "LINK/USDT": "Chainlink crypto", "NEAR/USDT": "NEAR Protocol", "SUI/USDT": "Sui blockchain",
        "PEPE/USDT": "Pepe coin", "WIF/USDT": "dogwifhat", "FET/USDT": "Fetch.ai",
        "RENDER/USDT": "Render Network crypto", "INJ/USDT": "Injective Protocol",
        "TIA/USDT": "Celestia crypto", "ARB/USDT": "Arbitrum crypto", "OP/USDT": "Optimism crypto",
        "APT/USDT": "Aptos crypto", "SEI/USDT": "Sei Network", "TAO/USDT": "Bittensor TAO",
        "PENDLE/USDT": "Pendle Finance", "RUNE/USDT": "THORChain", "BNB/USDT": "Binance Coin",
        "ADA/USDT": "Cardano", "DOT/USDT": "Polkadot", "LTC/USDT": "Litecoin",
        "MATIC/USDT": "Polygon POL crypto", "STX/USDT": "Stacks crypto", "ORDI/USDT": "ORDI Ordinals",
        "NOT/USDT": "Notcoin", "TON/USDT": "Toncoin", "JUP/USDT": "Jupiter Solana",
        "W/USDT": "Wormhole crypto", "ENA/USDT": "Ethena crypto", "BONK/USDT": "Bonk coin",
        "SPY/USDT": "S&P 500", "QQQ/USDT": "Nasdaq 100", "IWM/USDT": "Russell 2000",
        "PLTR/USDT": "Palantir stock", "NVDA/USDT": "NVIDIA stock", "TSLA/USDT": "Tesla stock",
        "INTC/USDT": "Intel stock", "SMCI/USDT": "Super Micro Computer stock", "AMD/USDT": "AMD stock",
        "AAPL/USDT": "Apple stock", "MSFT/USDT": "Microsoft stock", "GOOGL/USDT": "Alphabet Google stock",
        "AMZN/USDT": "Amazon stock", "META/USDT": "Meta Platforms stock", "NFLX/USDT": "Netflix stock",
        "CRM/USDT": "Salesforce stock", "ORCL/USDT": "Oracle stock", "CSCO/USDT": "Cisco stock",
        "IBM/USDT": "IBM stock", "UBER/USDT": "Uber stock", "ABNB/USDT": "Airbnb stock",
        "PANW/USDT": "Palo Alto Networks stock", "SNOW/USDT": "Snowflake stock",
        "RIVN/USDT": "Rivian stock", "NIO/USDT": "NIO stock",
        "JPM/USDT": "JPMorgan stock", "V/USDT": "Visa stock", "MA/USDT": "Mastercard stock",
        "BAC/USDT": "Bank of America stock", "PYPL/USDT": "PayPal stock", "HOOD/USDT": "Robinhood stock",
        "COIN/USDT": "Coinbase stock", "MSTR/USDT": "MicroStrategy Strategy stock",
        "BABA/USDT": "Alibaba stock", "GS/USDT": "Goldman Sachs stock", "MARA/USDT": "Marathon Digital stock",
        "CLSK/USDT": "CleanSpark stock",
        "LMT/USDT": "Lockheed Martin stock", "BA/USDT": "Boeing stock",
        "LLY/USDT": "Eli Lilly stock", "UNH/USDT": "UnitedHealth stock", "JNJ/USDT": "Johnson & Johnson stock",
        "XAU/USDT": "gold price", "XAG/USDT": "silver price", "COPPER/USDT": "copper price",
        "XLE/USDT": "energy sector stocks", "CL/USDT": "crude oil price",
        "WMT/USDT": "Walmart stock", "COST/USDT": "Costco stock", "DIS/USDT": "Disney stock",
        "NKE/USDT": "Nike stock", "MCD/USDT": "McDonald's stock",
    }

    # Weighted lexicons. Weight reflects how much the phrase actually moves price.
    BULLISH = {
        "beats estimates": 3.0, "beat expectations": 3.0, "raises guidance": 3.0, "record profit": 2.5,
        "upgrade": 2.0, "upgraded": 2.0, "price target raised": 2.0, "buyback": 2.0, "acquisition": 1.8,
        "approval": 2.0, "approved": 1.8, "partnership": 1.5, "etf approval": 3.0, "inflows": 1.8,
        "adoption": 1.5, "bullish": 1.5, "surges": 1.5, "soars": 1.8, "rally": 1.3, "breakout": 1.3,
        "outperform": 1.8, "strong demand": 2.0, "beats": 2.0, "record high": 1.8, "accumulation": 1.5,
        "institutional buying": 2.2, "whale accumulation": 1.8, "listing": 1.5, "burn": 1.2,
    }
    BEARISH = {
        "misses estimates": 3.0, "missed expectations": 3.0, "cuts guidance": 3.0, "lowers guidance": 3.0,
        "downgrade": 2.0, "downgraded": 2.0, "price target cut": 2.0, "lawsuit": 2.0, "sec charges": 2.8,
        "investigation": 2.2, "probe": 2.0, "hack": 3.0, "exploit": 3.0, "breach": 2.5, "bankruptcy": 3.5,
        "layoffs": 1.5, "recall": 2.0, "halt": 2.5, "delisting": 3.0, "fraud": 3.0, "plunges": 2.0,
        "crashes": 2.2, "tumbles": 1.8, "selloff": 1.8, "bearish": 1.5, "outflows": 1.8, "liquidation": 2.0,
        "warns": 1.8, "slashes": 2.0, "underperform": 1.8, "short seller": 2.2, "resignation": 1.5,
        "ban": 2.5, "crackdown": 2.2, "rug pull": 3.5, "insolvency": 3.5,
    }
    # Scheduled events that create gap/whipsaw risk. Split by asset class: "earnings"
    # is meaningless for Bitcoin, and a headline about AMD's earnings must not block
    # an NVDA trade — so these are additionally entity-gated in _event_hits().
    EVENT_RISK_EQUITY = {
        "earnings": 3, "quarterly results": 3, "q1 results": 3, "q2 results": 3,
        "q3 results": 3, "q4 results": 3, "earnings call": 3, "reports earnings": 3,
        "fda decision": 3, "guidance": 2, "investor day": 2, "trading halt": 3,
        "halted": 3, "stock split": 2, "merger vote": 2, "analyst day": 2,
    }
    EVENT_RISK_CRYPTO = {
        "token unlock": 3, "mainnet launch": 2, "hard fork": 2, "halving": 2,
        "etf decision": 3, "unlock event": 3, "airdrop": 2, "network upgrade": 2,
    }
    # Macro events hit every asset regardless of class — no entity gate needed.
    EVENT_RISK_MACRO = {
        "fomc": 3, "fed decision": 3, "rate decision": 3, "cpi report": 2,
        "jobs report": 2, "nonfarm payrolls": 2, "fed chair": 2,
    }
    MANIPULATION = {
        "pump and dump": 3.0, "pump-and-dump": 3.0, "manipulation": 2.5, "manipulated": 2.5,
        "wash trading": 2.5, "spoofing": 2.5, "insider trading": 2.5, "unusual options": 2.0,
        "unusual trading activity": 2.2, "suspicious": 1.8, "whale dumps": 2.0, "whale moves": 1.5,
        "short squeeze": 1.8, "gamma squeeze": 1.8, "meme stock": 1.5, "social media hype": 2.0,
        "coordinated buying": 2.5, "artificial": 1.8, "flash crash": 2.2, "liquidation cascade": 2.0,
    }

    # ------------------------------------------------------------------
    @classmethod
    def _entity_for(cls, ticker: str) -> str:
        if ticker in cls.ENTITY:
            return cls.ENTITY[ticker]
        base = ticker.split("/")[0]
        return f"{base} crypto"

    @staticmethod
    def _parse_pubdate(text: str):
        if not text:
            return None
        for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z"):
            try:
                dt = datetime.strptime(text.strip(), fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except Exception:
                continue
        return None

    @classmethod
    def _recency_weight(cls, pub_dt) -> float:
        """1.0 at publication, decaying to 0.25 at 72h. Old news is priced in."""
        if pub_dt is None:
            return 0.5
        age_h = (datetime.now(timezone.utc) - pub_dt).total_seconds() / 3600.0
        if age_h <= 2:
            return 1.0
        if age_h <= 6:
            return 0.9
        if age_h <= 24:
            return 0.7
        if age_h <= 48:
            return 0.45
        return 0.25

    @classmethod
    def _entity_tokens(cls, ticker: str) -> list:
        """Tokens that must appear for a headline to be ABOUT this asset."""
        base = ticker.split("/")[0].lower()
        toks = {base}
        for word in cls._entity_for(ticker).lower().split():
            word = re.sub(r"[^a-z0-9&.]", "", word)
            if len(word) > 2 and word not in ("stock", "crypto", "price", "coin", "the", "protocol", "network"):
                toks.add(word)
        return sorted(toks)

    @classmethod
    def _is_crypto(cls, ticker: str) -> bool:
        return ticker in cls.ENTITY and "stock" not in cls.ENTITY[ticker].lower() and ticker not in (
            "XAU/USDT", "XAG/USDT", "COPPER/USDT", "XLE/USDT", "CL/USDT",
            "SPY/USDT", "QQQ/USDT", "IWM/USDT",
        ) or ticker not in cls.ENTITY

    @classmethod
    def _event_hits(cls, text: str, ticker: str, entity_tokens: list) -> list:
        """
        Entity-gated event detection. A scheduled-event keyword only counts as risk
        for THIS asset if the headline is actually about this asset. Macro events
        (FOMC, CPI) apply to everything and skip the gate.
        """
        hits = []
        for phrase in cls.EVENT_RISK_MACRO:
            if phrase in text:
                hits.append(phrase)

        about_this_asset = any(tok in text for tok in entity_tokens)
        if about_this_asset:
            lexicon = cls.EVENT_RISK_CRYPTO if cls._is_crypto(ticker) else cls.EVENT_RISK_EQUITY
            for phrase in lexicon:
                if phrase in text:
                    hits.append(phrase)
        return hits

    @staticmethod
    def _match_score(text: str, lexicon: dict) -> tuple:
        total = 0.0
        hits = []
        for phrase, weight in lexicon.items():
            if phrase in text:
                total += weight
                hits.append(phrase)
        return total, hits

    # ------------------------------------------------------------------
    @classmethod
    def _fetch_headlines(cls, ticker: str) -> list:
        entity = cls._entity_for(ticker)
        query = requests.utils.quote(f"{entity} when:3d")
        url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"

        now = time.time()
        with cls._lock:
            hit = cls._cache.get(ticker)
            if hit and hit[1] > now:
                return hit[0]

        headlines = []
        try:
            resp = requests.get(url, headers=cls.HEADERS, timeout=6)
            if resp.status_code == 200:
                root = ET.fromstring(resp.content)
                for item in root.findall(".//item")[:20]:
                    t = item.find("title")
                    p = item.find("pubDate")
                    s = item.find("source")
                    if t is not None and t.text:
                        headlines.append({
                            "title": t.text,
                            "pub_dt": cls._parse_pubdate(p.text if p is not None else None),
                            "source": s.text if s is not None and s.text else "unknown",
                        })
        except Exception as e:
            print(f"[!] News fetch failed for {ticker}: {type(e).__name__}")

        with cls._lock:
            cls._cache[ticker] = (headlines, now + (cls.TTL if headlines else cls.TTL_EMPTY))
        return headlines

    # ------------------------------------------------------------------
    @classmethod
    def analyze(cls, ticker: str) -> dict:
        """
        Per-asset news verdict.

        news_multiplier is bounded to [0.80, 1.20] deliberately: news is a tilt on a
        technical setup, never the thesis. block_entry is the hard output — scheduled
        event risk and active manipulation chatter stop the trade outright.
        """
        neutral = {
            "available": False, "news_multiplier": 1.0, "news_bias": "NONE",
            "directional_score": 0.0, "block_entry": False, "block_reason": "",
            "event_risk": False, "manipulation_risk": False, "manipulation_score": 0.0,
            "headline_count": 0, "top_headlines": [], "drivers": [],
        }

        headlines = cls._fetch_headlines(ticker)
        if not headlines:
            return neutral

        bull_total = 0.0
        bear_total = 0.0
        manip_total = 0.0
        event_hits = []
        drivers = []
        top = []

        entity_tokens = cls._entity_tokens(ticker)

        for h in headlines:
            text = h["title"].lower()
            w = cls._recency_weight(h["pub_dt"])

            b_s, b_hits = cls._match_score(text, cls.BULLISH)
            r_s, r_hits = cls._match_score(text, cls.BEARISH)
            m_s, m_hits = cls._match_score(text, cls.MANIPULATION)
            e_hits = cls._event_hits(text, ticker, entity_tokens)

            # Directional lexicon only counts when the headline is about this asset.
            if not any(tok in text for tok in entity_tokens):
                b_s *= 0.35
                r_s *= 0.35

            bull_total += b_s * w
            bear_total += r_s * w
            manip_total += m_s * w
            if e_hits:
                event_hits.extend(e_hits)

            if b_s or r_s or m_s or e_hits:
                tag = "🟢" if b_s > r_s else "🔴" if r_s > b_s else "⚪"
                top.append(f"{tag} {h['title'][:110]}")
                for phrase in (b_hits + r_hits + m_hits)[:2]:
                    drivers.append(phrase)

        # LEARNED OVERLAY. The lexicon above is now only a cold-start prior; terms that
        # have accumulated real forward-return evidence override it, and terms nobody
        # hand-coded ("under wraps", "closed-door") get scored once they have history.
        learned = {"score": 0.0, "learned_fraction": 0.0, "terms": []}
        try:
            from news.learned_sentiment import LearnedNewsSentiment
            scored = [{"title": h["title"], "recency_weight": cls._recency_weight(h["pub_dt"])}
                      for h in headlines]
            learned = LearnedNewsSentiment.score_headlines(scored)
        except Exception as e:
            print(f"[!] Learned sentiment unavailable: {type(e).__name__}")

        net = bull_total - bear_total
        # Blend by how much of the learned score is actually measured rather than prior.
        lf = learned.get("learned_fraction", 0.0)
        if lf > 0:
            net = net * (1 - lf) + learned["score"] * 6.0 * lf
        magnitude = bull_total + bear_total

        # Normalise: the tilt saturates, so one loud day cannot dominate the setup.
        if magnitude > 0:
            normalised = net / max(magnitude, 1.0)
        else:
            normalised = 0.0
        confidence = min(magnitude / 12.0, 1.0)
        tilt = normalised * confidence * 0.20
        multiplier = round(min(max(1.0 + tilt, 0.80), 1.20), 4)

        if net > 1.5:
            bias = "BULLISH"
        elif net < -1.5:
            bias = "BEARISH"
        else:
            bias = "NONE"

        manipulation_risk = manip_total >= 3.0
        event_risk = len(set(event_hits)) >= 1

        block_entry = False
        block_reason = ""
        if event_risk:
            block_entry = True
            block_reason = f"Scheduled event risk in headlines ({', '.join(sorted(set(event_hits))[:3])}) — gap/whipsaw risk"
        elif manipulation_risk:
            block_entry = True
            block_reason = f"Manipulation chatter score {manip_total:.1f} — move may be engineered"

        return {
            "available": True,
            "news_multiplier": multiplier,
            "news_bias": bias,
            "directional_score": round(net, 2),
            "confidence": round(confidence, 2),
            "block_entry": block_entry,
            "block_reason": block_reason,
            "event_risk": event_risk,
            "event_tags": sorted(set(event_hits))[:5],
            "manipulation_risk": manipulation_risk,
            "manipulation_score": round(manip_total, 2),
            "headline_count": len(headlines),
            "top_headlines": top[:4],
            "drivers": sorted(set(drivers))[:6],
            "learned_score": learned.get("score", 0.0),
            "learned_fraction": learned.get("learned_fraction", 0.0),
            "learned_terms": learned.get("terms", [])[:5],
        }


if __name__ == "__main__":
    for t in ["NVDA/USDT", "BTC/USDT", "XAU/USDT", "MSTR/USDT"]:
        r = PerAssetNewsIntelligence.analyze(t)
        print(f"\n=== {t} ===")
        print(f"  bias={r['news_bias']} mult={r['news_multiplier']} block={r['block_entry']} {r.get('block_reason','')}")
        for h in r.get("top_headlines", []):
            print(f"   {h}")
