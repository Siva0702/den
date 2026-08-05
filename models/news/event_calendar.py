# models/news/event_calendar.py
import json
import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import requests

CALENDAR_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "audit/event_calendar.json")

class ScheduledEventCalendar:
    """
    Den Engine v39.1 Rolling 30-Day Scheduled Event Calendar.

    Two free, keyless feeds, refreshed live:

      ForexFactory JSON  — macro calendar with impact tiers. FOMC, CPI, Non-Farm
                           Payrolls, rate decisions, PMIs. Affects every asset, though
                           by different amounts.
      Nasdaq earnings API— per-date earnings with consensus EPS. Maps directly onto the
                           equity half of the universe.

    The design point the user insisted on: this is NOT a blocklist. A hard block on any
    event keyword is what stopped NVDA outright and is exactly how an engine goes silent.
    Everything here is a GRADED, TIME-DECAYING risk value:

        risk = impact_weight x proximity_weight x relevance_weight

    72 hours before earnings the penalty is negligible. 90 minutes before, it is total.
    30 minutes after, the block lifts and the POST-EVENT playbook takes over — because
    that is where the tradeable move actually is (see event_volatility.py).

    Only a narrow blackout window around a high-impact event for THAT asset is an
    outright veto. Everything else is a score adjustment the setup can outweigh.
    """

    HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                             "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
               "Accept": "application/json"}

    FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    NASDAQ_URL = "https://api.nasdaq.com/api/calendar/earnings?date={date}"

    REFRESH_SECONDS = 6 * 3600
    EARNINGS_HORIZON_DAYS = 30
    MAX_WORKERS = 5

    # Blackout: no new risk this close to a high-impact event for the affected asset.
    BLACKOUT_BEFORE_MIN = 90
    BLACKOUT_AFTER_MIN = 30

    _lock = threading.Lock()
    _memory = {"fetched_at": 0.0, "events": []}

    # Impact weight per tier — how much a full-proximity event can cost.
    IMPACT_WEIGHT = {"CRITICAL": 1.0, "HIGH": 0.85, "MEDIUM": 0.45, "LOW": 0.15, "HOLIDAY": 0.05}

    # How exposed each asset class is to a USD macro print.
    MACRO_SENSITIVITY = {
        "Crypto Futures": 0.70, "Index Benchmark": 1.00, "Big Tech / AI": 0.85,
        "EV / Mobility": 0.80, "Financials / Fintech": 0.95, "Defense / Aerospace": 0.70,
        "Healthcare / BioPharma": 0.65, "Energy / Commodities": 0.90, "Consumer / Retail": 0.80,
    }

    # Macro titles that move everything hard, regardless of stated tier.
    CRITICAL_TITLES = (
        "fomc", "federal funds rate", "fed chair", "cpi ", "core cpi", "ppi ",
        "non-farm employment", "nonfarm", "unemployment rate", "gdp ", "pce ",
        "interest rate decision", "rate statement", "press conference",
    )

    # ------------------------------------------------------------------
    @staticmethod
    def _atomic_write(path, payload):
        d = os.path.dirname(path) or "."
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(payload, f, indent=2, default=str)
            os.replace(tmp, path)
        except Exception as e:
            print(f"[!] Calendar write failed: {e}")
            try:
                os.unlink(tmp)
            except Exception:
                pass

    @staticmethod
    def _parse_iso(text):
        try:
            dt = datetime.fromisoformat(text)
            return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # A scheduled official TALKING is not a scheduled data RELEASE. "FOMC Member Schmid
    # Speaks" was being tiered CRITICAL purely on the substring "fomc", which taxed every
    # asset in the universe for a routine speech and dragged live scores down by ~20
    # points. Speeches cap at MEDIUM; only decisions, statements and prints are CRITICAL.
    SPEECH_MARKERS = ("speaks", "speech", "remarks", "testifies", "testimony", "member")

    @classmethod
    def _classify_macro(cls, title: str, impact: str) -> str:
        low = title.lower()
        tier = {"High": "HIGH", "Medium": "MEDIUM", "Low": "LOW", "Holiday": "HOLIDAY"}.get(impact, "LOW")
        if any(k in low for k in cls.SPEECH_MARKERS):
            return "MEDIUM" if tier in ("CRITICAL", "HIGH") else tier
        if any(k in low for k in cls.CRITICAL_TITLES):
            return "CRITICAL"
        return tier

    @classmethod
    def _get_json(cls, url: str, attempts: int = 3):
        """
        GET with backoff. These public mirrors rate-limit hard (HTTP 429), and a
        transient 429 must never be mistaken for 'no events scheduled'.
        Returns None on failure so callers can distinguish empty from broken.
        """
        for i in range(attempts):
            try:
                resp = requests.get(url, headers=cls.HEADERS, timeout=12)
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code in (429, 503):
                    time.sleep(2 ** i)
                    continue
                return None
            except Exception:
                time.sleep(1 + i)
        return None

    @classmethod
    def _fetch_macro(cls):
        events = []
        payload = cls._get_json(cls.FF_URL)
        if payload is None:
            return None                     # signal failure, not emptiness
        try:
            for e in payload:
                dt = cls._parse_iso(e.get("date", ""))
                if not dt:
                    continue
                tier = cls._classify_macro(e.get("title", ""), e.get("impact", "Low"))
                if tier in ("LOW", "HOLIDAY"):
                    continue
                events.append({
                    "kind": "MACRO",
                    "title": e.get("title", "").strip(),
                    "country": e.get("country", "").strip(),
                    "when_utc": dt.isoformat(),
                    "tier": tier,
                    "forecast": e.get("forecast", ""),
                    "previous": e.get("previous", ""),
                    "symbol": None,
                })
        except Exception as e:
            print(f"[!] Macro calendar parse failed: {type(e).__name__}")
            return None
        return events

    # ------------------------------------------------------------------
    @classmethod
    def _fetch_earnings_for_date(cls, date_str: str) -> list:
        out = []
        try:
            payload = cls._get_json(cls.NASDAQ_URL.format(date=date_str), attempts=2)
            if payload is None:
                return out
            rows = ((payload or {}).get("data") or {}).get("rows") or []
            for r in rows:
                sym = (r.get("symbol") or "").strip().upper()
                if not sym:
                    continue
                # Nasdaq gives a session, not a clock time. Anchor to US market hours (UTC).
                slot = (r.get("time") or "").lower()
                if "pre-market" in slot:
                    hour, minute = 11, 0      # ~07:00 ET
                elif "after-hours" in slot:
                    hour, minute = 20, 30     # ~16:30 ET
                else:
                    hour, minute = 20, 0
                try:
                    base = datetime.strptime(date_str, "%Y-%m-%d").replace(
                        hour=hour, minute=minute, tzinfo=timezone.utc)
                except Exception:
                    continue
                out.append({
                    "kind": "EARNINGS",
                    "title": f"{sym} earnings ({slot or 'time TBD'})",
                    "country": "USD",
                    "when_utc": base.isoformat(),
                    "tier": "CRITICAL",
                    "forecast": r.get("epsForecast", ""),
                    "previous": r.get("lastYearEPS", ""),
                    "symbol": sym,
                    "company": r.get("name", ""),
                })
        except Exception:
            pass
        return out

    @classmethod
    def _fetch_earnings(cls, universe_symbols: set) -> list:
        today = datetime.now(timezone.utc).date()
        dates = [(today + timedelta(days=i)).strftime("%Y-%m-%d")
                 for i in range(cls.EARNINGS_HORIZON_DAYS)]
        collected = []
        with ThreadPoolExecutor(max_workers=cls.MAX_WORKERS) as pool:
            futures = {pool.submit(cls._fetch_earnings_for_date, d): d for d in dates}
            for fut in as_completed(futures):
                try:
                    for ev in fut.result():
                        # Only keep symbols we actually trade — 577/day otherwise.
                        if ev["symbol"] in universe_symbols:
                            collected.append(ev)
                except Exception:
                    continue
        return collected

    # ------------------------------------------------------------------
    @classmethod
    def equity_symbols(cls) -> set:
        """
        Only equity-class tickers may match an earnings row.

        Crypto tickers collide with real stock symbols — SEI/USDT is Sei the L1, but SEI
        is also Sei Investments Co; LTC is Litecoin and LTC Properties; APT is Aptos and
        Alpha Pro Tech. Matching on the bare symbol had the engine believing Litecoin
        reports quarterly earnings. Restricting the match to equity asset classes removes
        the whole collision class.
        """
        try:
            from news.market_universe import DynamicMarketUniverse
            return {
                i["ticker"].split("/")[0].upper()
                for i in DynamicMarketUniverse.get_full_hunting_universe()
                if i.get("asset_class") not in ("Crypto Futures", "Energy / Commodities", "Index Benchmark")
            }
        except Exception:
            return set()

    @classmethod
    def refresh(cls, universe_symbols: set = None, force: bool = False) -> list:
        now = time.time()
        with cls._lock:
            if not force and cls._memory["events"] and (now - cls._memory["fetched_at"]) < cls.REFRESH_SECONDS:
                return cls._memory["events"]

        if universe_symbols is None:
            universe_symbols = cls.equity_symbols()

        previous = cls._disk_events()
        macro = cls._fetch_macro()
        earnings = cls._fetch_earnings(universe_symbols)

        # A failed source must never blank the calendar — fall back to what we had.
        # Silently reporting "no events scheduled" because of a 429 would be the most
        # dangerous failure mode here: the engine would trade straight into an FOMC.
        degraded = []
        if macro is None:
            macro = [e for e in previous if e["kind"] == "MACRO"]
            degraded.append("macro")
        if not earnings:
            earnings = [e for e in previous if e["kind"] == "EARNINGS"]
            if earnings:
                degraded.append("earnings")

        events = sorted(macro + earnings, key=lambda e: e["when_utc"])

        with cls._lock:
            # Only advance the refresh clock on a clean pull, so a degraded fetch
            # retries on the next scan instead of sitting stale for six hours.
            cls._memory = {"fetched_at": now if not degraded else 0.0, "events": events}

        cls._atomic_write(CALENDAR_FILE, {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "horizon_days": cls.EARNINGS_HORIZON_DAYS,
            "event_count": len(events),
            "degraded_sources": degraded,
            "events": events,
        })
        flag = f" (degraded: {','.join(degraded)}, serving cached)" if degraded else ""
        print(f"[✓] Event calendar: {len(events)} events over {cls.EARNINGS_HORIZON_DAYS}d{flag}", flush=True)
        return events

    @classmethod
    def _disk_events(cls) -> list:
        if not os.path.exists(CALENDAR_FILE):
            return []
        try:
            with open(CALENDAR_FILE, "r") as f:
                return (json.load(f) or {}).get("events", [])
        except Exception:
            return []

    @classmethod
    def all_events(cls) -> list:
        with cls._lock:
            if cls._memory["events"]:
                return cls._memory["events"]
        if os.path.exists(CALENDAR_FILE):
            try:
                with open(CALENDAR_FILE, "r") as f:
                    data = json.load(f)
                    with cls._lock:
                        cls._memory = {"fetched_at": time.time(), "events": data.get("events", [])}
                    return cls._memory["events"]
            except Exception:
                pass
        return cls.refresh()

    # ------------------------------------------------------------------
    @classmethod
    def _relevance(cls, event: dict, ticker: str, asset_class: str) -> float:
        """How much does this event matter to THIS asset? 0.0 - 1.0."""
        base = ticker.split("/")[0].upper()
        if event["kind"] == "EARNINGS":
            return 1.0 if event.get("symbol") == base else 0.0
        if event["country"] not in ("USD", "All", ""):
            return 0.15          # foreign macro: real but secondary
        return cls.MACRO_SENSITIVITY.get(asset_class, 0.7)

    @staticmethod
    def _proximity(minutes_away: float) -> float:
        """
        Time decay. Peaks in the hour around the event and falls away smoothly,
        so risk is graded rather than binary.
        """
        m = abs(minutes_away)
        if m <= 60:
            return 1.0
        if m <= 180:
            return 0.85
        if m <= 480:
            return 0.60
        if m <= 1440:
            return 0.35
        if m <= 4320:            # 3 days
            return 0.15
        return 0.04

    # ------------------------------------------------------------------
    @classmethod
    def assess(cls, ticker: str, asset_class: str = "Crypto Futures") -> dict:
        """
        Dynamic event risk for one asset.

        Returns a graded 0-100 risk score, a bounded score penalty, and only a narrow
        hard veto for the blackout window around a directly relevant high-impact event.
        """
        events = cls.all_events()
        now = datetime.now(timezone.utc)

        risk = 0.0
        contributions = []
        blackout = False
        blackout_reason = ""
        next_event = None
        post_event = None

        for e in events:
            when = cls._parse_iso(e["when_utc"])
            if when is None:
                continue
            minutes = (when - now).total_seconds() / 60.0
            if minutes < -720 or minutes > 43200:      # older than 12h, further than 30d
                continue

            relevance = cls._relevance(e, ticker, asset_class)
            if relevance <= 0.0:
                continue

            weight = cls.IMPACT_WEIGHT.get(e["tier"], 0.2)
            contribution = weight * cls._proximity(minutes) * relevance * 100.0

            if minutes >= 0 and (next_event is None or minutes < next_event["minutes_away"]):
                next_event = {**e, "minutes_away": round(minutes, 1), "relevance": round(relevance, 2)}

            # Post-event window: the event has fired and the reaction is live.
            if -240 <= minutes < 0 and relevance >= 0.6 and e["tier"] in ("CRITICAL", "HIGH"):
                if post_event is None or minutes > post_event["minutes_since"] * -1:
                    post_event = {**e, "minutes_since": round(-minutes, 1)}

            # Hard veto only in the narrow blackout around a directly relevant event.
            if relevance >= 0.6 and e["tier"] in ("CRITICAL", "HIGH") and \
               -cls.BLACKOUT_AFTER_MIN <= minutes <= cls.BLACKOUT_BEFORE_MIN:
                blackout = True
                blackout_reason = (f"{e['title']} in {minutes:.0f} min" if minutes >= 0
                                   else f"{e['title']} {abs(minutes):.0f} min ago — reaction still forming")

            if contribution >= 1.0:
                contributions.append({
                    "title": e["title"], "tier": e["tier"], "kind": e["kind"],
                    "minutes_away": round(minutes, 1), "contribution": round(contribution, 1),
                })

        contributions.sort(key=lambda c: c["contribution"], reverse=True)

        # Dominant-event model rather than a plain sum. In any busy week half a dozen
        # macro prints land inside 48h, and summing them pinned every asset at 100 — a
        # flat penalty that discriminates between nothing. What actually matters is the
        # nearest big event; the rest are damped background.
        if contributions:
            head = contributions[0]["contribution"]
            tail = sum(c["contribution"] for c in contributions[1:])
            risk = min(head + 0.25 * tail, 100.0)
        else:
            risk = 0.0

        # Bounded penalty. Even maximum calendar risk costs at most 12 points, so a
        # genuinely exceptional setup can still clear the bar outside a blackout.
        penalty = round((risk / 100.0) * 12.0, 2)

        return {
            "event_risk_score": round(risk, 1),
            "score_penalty": penalty,
            "blackout": blackout,
            "blackout_reason": blackout_reason,
            "next_event": next_event,
            "post_event": post_event,
            "top_contributors": contributions[:4],
            "verdict": ("BLACKOUT" if blackout else
                        "HIGH_EVENT_RISK" if risk >= 55 else
                        "MODERATE_EVENT_RISK" if risk >= 25 else "CLEAR"),
        }

    # ------------------------------------------------------------------
    @classmethod
    def upcoming(cls, hours: int = 72, tiers=("CRITICAL", "HIGH")) -> list:
        """Calendar view for the digest."""
        now = datetime.now(timezone.utc)
        out = []
        for e in cls.all_events():
            when = cls._parse_iso(e["when_utc"])
            if when is None or e["tier"] not in tiers:
                continue
            mins = (when - now).total_seconds() / 60.0
            if 0 <= mins <= hours * 60:
                out.append({**e, "minutes_away": round(mins, 1)})
        return sorted(out, key=lambda e: e["minutes_away"])


if __name__ == "__main__":
    ScheduledEventCalendar.refresh(force=True)
    print("\nNext 72h high-impact:")
    for e in ScheduledEventCalendar.upcoming(72)[:15]:
        print(f"  {e['when_utc'][:16]}  [{e['tier']:8}] {e['country']:4} {e['title'][:60]}")
    for t, ac in [("NVDA/USDT", "Big Tech / AI"), ("BTC/USDT", "Crypto Futures"), ("AMD/USDT", "Big Tech / AI")]:
        a = ScheduledEventCalendar.assess(t, ac)
        print(f"\n{t}: {a['verdict']} risk={a['event_risk_score']} penalty=-{a['score_penalty']}pts")
        if a["next_event"]:
            print(f"   next: {a['next_event']['title'][:55]} in {a['next_event']['minutes_away']/60:.1f}h")
        for c in a["top_contributors"][:3]:
            print(f"   · {c['contribution']:5.1f} {c['tier']:8} {c['title'][:50]} ({c['minutes_away']/60:+.1f}h)")
