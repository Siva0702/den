# models/news/learned_sentiment.py
import json
import math
import os
import re
import tempfile
import threading
import time
from datetime import datetime, timezone

LEXICON_FILE = "audit/learned_lexicon.json"
PENDING_FILE = "audit/news_pending.json"

class LearnedNewsSentiment:
    """
    Den Engine v39.4 Self-Learning News Sentiment.

    The previous news engine scored headlines against a lexicon I hand-wrote: "beats
    estimates" = +3.0, "downgrade" = -2.0, and so on. That is guesswork wearing a
    number, and it fails in the two ways the user was worried about:

      - it only sees terms I happened to think of, so a phrase like "under wraps" or
        "closed-door briefing" scores exactly zero no matter how much it moves price
      - my weights are opinions. If "partnership" is actually bearish for a given
        sector, the hardcoded +1.5 will keep being wrong forever.

    This replaces the guessing with measurement. Every headline is stored with the
    price at observation. Some hours later the engine looks at what price ACTUALLY did
    and attributes that move back to the terms in the headline. Over time each term
    accumulates an empirical impact score: the mean forward return of the asset when
    that term appears, with a Wilson-style shrink so rare terms cannot dominate.

    The hand-written lexicon is retained ONLY as a cold-start prior, and every observed
    term progressively overrides it as evidence accumulates:

        weight = (prior * prior_strength + observed_mean * n) / (prior_strength + n)

    So the engine begins with a sensible opinion and ends with a measured one. No term
    is privileged, no vocabulary is fixed, and nothing needs me to have guessed it.
    """

    HORIZON_HOURS = 4.0          # how long after a headline we measure the reaction
    PRIOR_STRENGTH = 6.0         # pseudo-observations backing the seed lexicon
    MIN_TERM_COUNT = 4           # below this a term reports its prior only
    MAX_PENDING = 4000
    MAX_TERMS = 6000
    MAX_LEXICON_KB = 600.0      # Upstash free-tier REST requests fail near 1MB

    STOPWORDS = {
        "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with",
        "is", "are", "was", "were", "be", "been", "as", "by", "from", "that", "this",
        "it", "its", "has", "have", "had", "will", "would", "can", "could", "may",
        "says", "say", "said", "new", "after", "before", "than", "then", "into", "over",
        "amid", "about", "more", "most", "you", "your", "we", "us", "how", "why", "what",
        "here", "there", "not", "no", "yes", "up", "down", "out", "off", "just", "now",
    }

    _lock = threading.Lock()
    _cache = {"lexicon": None, "loaded_at": 0.0}

    # ------------------------------------------------------------------
    @staticmethod
    def _atomic_write(path, payload):
        d = os.path.dirname(path) or "."
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(payload, f, default=str)
            os.replace(tmp, path)
        except Exception as e:
            print(f"[!] Lexicon write failed: {e}")
            try:
                os.unlink(tmp)
            except Exception:
                pass

    @staticmethod
    def _load(path, default):
        if not os.path.exists(path):
            return default
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            return default

    # ------------------------------------------------------------------
    @classmethod
    def tokenize(cls, title: str) -> list:
        """
        Unigrams and bigrams, stopwords removed. Bigrams matter because 'beats' and
        'estimates' individually are weak, while 'beats estimates' is decisive — and
        the engine has no way to know that in advance, so it learns both.
        """
        words = re.findall(r"[a-z0-9$%\.\-]+", title.lower())
        words = [w.strip(".-") for w in words if len(w) > 2 and w not in cls.STOPWORDS]
        terms = list(words)
        terms += [f"{words[i]} {words[i + 1]}" for i in range(len(words) - 1)]
        return list(dict.fromkeys(terms))

    # ------------------------------------------------------------------
    @classmethod
    def _seed_prior(cls) -> dict:
        """Cold-start opinions only. Every one of these is overridable by evidence."""
        from news.news_intelligence import PerAssetNewsIntelligence as P
        prior = {}
        for phrase, w in P.BULLISH.items():
            prior[phrase] = min(w / 3.0, 1.0) * 0.6
        for phrase, w in P.BEARISH.items():
            prior[phrase] = -min(w / 3.5, 1.0) * 0.6
        for phrase, w in P.MANIPULATION.items():
            prior[phrase] = -min(w / 3.5, 1.0) * 0.3
        return prior

    @classmethod
    def lexicon(cls, force: bool = False) -> dict:
        now = time.time()
        with cls._lock:
            if not force and cls._cache["lexicon"] is not None and (now - cls._cache["loaded_at"]) < 300:
                return cls._cache["lexicon"]
        data = cls._load(LEXICON_FILE, {"terms": {}, "updated": None})
        with cls._lock:
            cls._cache = {"lexicon": data, "loaded_at": now}
        return data

    # ------------------------------------------------------------------
    @classmethod
    def observe(cls, ticker: str, headlines: list, price_now: float):
        """
        Record headlines with the current price so their outcome can be measured later.
        Called by the news layer on every fresh fetch.
        """
        if not headlines or not price_now:
            return
        with cls._lock:
            pending = cls._load(PENDING_FILE, [])
            seen = {(p["ticker"], p["title"]) for p in pending}
            now = time.time()
            for h in headlines:
                title = h["title"] if isinstance(h, dict) else str(h)
                if (ticker, title) in seen:
                    continue
                pending.append({
                    "ticker": ticker, "title": title,
                    "observed_epoch": now, "price_at_observation": float(price_now),
                })
            if len(pending) > cls.MAX_PENDING:
                pending = pending[-cls.MAX_PENDING:]
            cls._atomic_write(PENDING_FILE, pending)

    # ------------------------------------------------------------------
    @classmethod
    def settle(cls, price_map: dict) -> int:
        """
        Attribute realised forward returns back to headline terms.
        price_map: {ticker: price} (or {"close": ...} dicts). Returns terms updated.
        """
        now = time.time()
        horizon = cls.HORIZON_HOURS * 3600

        with cls._lock:
            pending = cls._load(PENDING_FILE, [])
            if not pending:
                return 0
            lex = cls._load(LEXICON_FILE, {"terms": {}, "updated": None})
            terms = lex.get("terms", {})

            still = []
            updated = 0
            for item in pending:
                if now - item["observed_epoch"] < horizon:
                    still.append(item)
                    continue

                px = price_map.get(item["ticker"])
                if px is None:
                    # Give it one more cycle, then drop it — an unmeasured headline
                    # must never be scored as if it were neutral evidence.
                    if now - item["observed_epoch"] < horizon * 3:
                        still.append(item)
                    continue
                if isinstance(px, dict):
                    px = px.get("close")
                p0 = float(item["price_at_observation"])
                if not p0 or not px:
                    continue

                ret_pct = (float(px) - p0) / p0 * 100.0
                # Squash so one violent move cannot swamp a term's history.
                signal = math.tanh(ret_pct / 2.0)

                for term in cls.tokenize(item["title"]):
                    slot = terms.setdefault(term, {"n": 0, "sum": 0.0, "mean": 0.0})
                    slot["n"] += 1
                    slot["sum"] += signal
                    slot["mean"] = slot["sum"] / slot["n"]
                    updated += 1

            # PRUNE. The lexicon accrues a term for every word in every headline, so it
            # grows without bound — it hit 491KB in a day, and Upstash free-tier REST
            # requests fail near 1MB. That failure is SILENT: sync just stops working and
            # the engine keeps reporting healthy. Terms below MIN_TERM_COUNT carry no
            # weight anyway (term_weight returns the prior for them), so dropping the
            # thinnest ones costs nothing and keeps the payload syncable.
            if len(terms) > cls.MAX_TERMS:
                terms = dict(sorted(terms.items(), key=lambda kv: kv[1]["n"], reverse=True)[:cls.MAX_TERMS])
            approx_kb = len(json.dumps(terms)) / 1024.0
            if approx_kb > cls.MAX_LEXICON_KB:
                # Keep only terms with real evidence, highest-count first.
                mature = {k: v for k, v in terms.items() if v["n"] >= cls.MIN_TERM_COUNT}
                ranked = sorted(mature.items(), key=lambda kv: kv[1]["n"], reverse=True)
                kept, size = {}, 0
                for k, v in ranked:
                    entry = len(k) + 60
                    if (size + entry) / 1024.0 > cls.MAX_LEXICON_KB:
                        break
                    kept[k] = v
                    size += entry
                print(f"[news] lexicon pruned {approx_kb:.0f}KB -> "
                      f"{len(kept)} mature terms (from {len(terms)})", flush=True)
                terms = kept

            lex["terms"] = terms
            lex["updated"] = datetime.now(timezone.utc).isoformat()
            cls._atomic_write(LEXICON_FILE, lex)
            cls._atomic_write(PENDING_FILE, still)
            cls._cache = {"lexicon": lex, "loaded_at": now}
        return updated

    # ------------------------------------------------------------------
    @classmethod
    def term_weight(cls, term: str, prior: dict) -> tuple:
        """Blended weight for one term, plus the evidence count behind it."""
        lex = cls.lexicon()
        slot = (lex.get("terms") or {}).get(term)
        p = prior.get(term, 0.0)
        if not slot or slot["n"] < cls.MIN_TERM_COUNT:
            return p, (slot or {}).get("n", 0)
        n = slot["n"]
        blended = (p * cls.PRIOR_STRENGTH + slot["mean"] * n) / (cls.PRIOR_STRENGTH + n)
        return blended, n

    # ------------------------------------------------------------------
    @classmethod
    def score_headlines(cls, headlines: list) -> dict:
        """
        Score a set of headlines using learned weights where evidence exists and priors
        where it does not. Reports how much of the score came from measurement, so the
        caller can discount an opinion-driven read.
        """
        if not headlines:
            return {"score": 0.0, "learned_fraction": 0.0, "terms": [], "n_headlines": 0}

        prior = cls._seed_prior()
        total = 0.0
        learned_mass = 0.0
        total_mass = 0.0
        contributors = []

        for h in headlines:
            title = h["title"] if isinstance(h, dict) else str(h)
            weight = h.get("recency_weight", 1.0) if isinstance(h, dict) else 1.0
            for term in cls.tokenize(title):
                w, n = cls.term_weight(term, prior)
                if abs(w) < 0.05:
                    continue
                total += w * weight
                total_mass += abs(w)
                if n >= cls.MIN_TERM_COUNT:
                    learned_mass += abs(w)
                    contributors.append((term, round(w, 3), n))

        contributors.sort(key=lambda x: abs(x[1]), reverse=True)
        return {
            "score": round(total, 3),
            "learned_fraction": round(learned_mass / total_mass, 3) if total_mass else 0.0,
            "terms": contributors[:8],
            "n_headlines": len(headlines),
        }

    # ------------------------------------------------------------------
    @classmethod
    def stats(cls) -> dict:
        lex = cls.lexicon(force=True)
        terms = lex.get("terms", {})
        mature = {k: v for k, v in terms.items() if v["n"] >= cls.MIN_TERM_COUNT}
        pending = cls._load(PENDING_FILE, [])
        top = sorted(mature.items(), key=lambda kv: abs(kv[1]["mean"]), reverse=True)[:10]
        return {
            "terms_tracked": len(terms),
            "terms_mature": len(mature),
            "pending_headlines": len(pending),
            "updated": lex.get("updated"),
            "strongest": [(k, round(v["mean"], 3), v["n"]) for k, v in top],
        }
