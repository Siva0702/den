# Den Engine — Session Handoff

Paste this whole file into a new Claude Code session as your first message, prefixed with:
*"Read HANDOFF.md in /Users/siva/Desktop/Stocks/den and continue from there."*

---

## 1. What this is

Multi-asset quant signal engine. Scans 87 assets across 5 timeframes, scores setups
0–100, and dispatches high-conviction signals to Telegram. Deployed on Render, state
persisted in Upstash Redis.

- **Repo:** `/Users/siva/Desktop/Stocks/den` — GitHub `Siva0702/den`, branch `main`
- **Live:** https://den-quant-scanner.onrender.com/
- **Last commit:** `4c23134`
- **Telegram bot:** commands `/ledger`, `/kelly`, `/veto`, `/calendar`, reply `positioned`

## 2. Current state (verified)

```
imports        0 failures across 33 live modules
integrity      clean, 0 duplicates
version        v4-trail-one-behind-1m, 0 stale records
ledger         446 resolved · 203W · 243L · accuracy 45.5%
equity         $1,000 -> $854.76 · -4.84R · max drawdown 103%
scan speed     ~70s cold, ~1s warm between candle closes
signals sent   0 (dispatch floor 78; almost nothing reaches it)
```

**The engine has NO demonstrated edge. Do not fund it.**

## 3. Architecture — the scan loop

```
every ~15s:
  FETCH    87 assets x 5 TFs (1m/5m/15m/1h/4h/1d), 6 threads
           candle-aligned cache: a 15m candle is fetched ONCE per candle
  MONITOR  open positions first, then advance shadow ledger at 1m resolution
  SETTLE   event reactions, news term weights
  SCREEN   cheap technical score on all 87 (reuses cache if candle unchanged)
  ENRICH   top 30 only: derivatives, news, calendar, event-volatility
  SCORE    5 pillars, true 0-100:
             trend 20 | htf 20 | orderflow 25 | structure 20 | defense 15
           + bounded additive tilt (news, regime-learned weights, BTC beta)
  LEVELS   stop beyond liquidity pool, volatility-scaled floor
           TP ladder off liquidity pools (R:R varies per signal)
  SIZE     calibrated win rate -> Kelly -> conviction-scaled risk -> leverage
  GATES    score >= 78 | stability (3 consecutive scans, sigma<9, not decaying)
           | calibrated WR >= 50% | R:R >= 1.2 | event blackout | hunt risk
           | 5m timing | Kelly veto | correlation
  DISPATCH max 3/scan, 8/day
  LEARN    every candidate >= 40 becomes a shadow trade
```

## 4. Key modules

| File | Role |
|---|---|
| `models/auto_scanner.py` | main loop, gates, dispatch, Telegram |
| `models/indicators/confluence_engine.py` | 5-pillar scoring |
| `models/indicators/regime_engine.py` | two-axis regime (BULL/BEAR x EXPANSION/COMPRESSION) |
| `models/indicators/liquidity_map.py` | stop placement, TP ladder |
| `models/audit/shadow_ledger.py` | virtual trades, trailing, resolution |
| `models/audit/regime_performance.py` | learned score adjustments |
| `models/audit/calibration.py` | Wilson-bounded win rates, FDR |
| `models/audit/ledger_recovery.py` | replay records against 1m candles |
| `models/data/exchange_feed.py` | klines, sticky routing, candle cache |

`models/_archive/` = 29 superseded modules. `_archive/unvalidated/` = tested, found
useless, kept so the same idea is not rebuilt.

## 5. RULES — violated repeatedly, cost days of bad data

1. **Resolve trades at 1m, always** — live AND replay. Never 15m.
2. **Trail moves TO the rung just hit.** TP1 hit -> SL becomes TP1. If TP1 is hit
   before TP2, result is `TP1_HIT`. Same for TP2, TP3, TP4.
3. **A rung arms one bar AFTER it is tagged** — price reaches a target from below, so
   that bar's low sits under it by definition.
4. **Only bars strictly AFTER `opened_epoch` count.** A lookback window credited
   trades with moves that predated them (10 fake wins).
5. **Never mix logic versions.** Bump `LOGIC_VERSION` on any resolution change; stale
   records auto-replay on boot.
6. **Cohort membership is frozen at trade-open.** Never recompute from current
   calibration — records migrate between cohorts and the comparison dies.
7. **Verify the OUTPUT, not the mechanism.** "Scan got faster" proved nothing about
   what the ledger recorded.
8. **When a defect is found, sweep for its twin.** Every bug this session had one.
9. **Test new signals against resolved outcomes BEFORE shipping.** If winners and
   losers score the same, archive it. Do not ship plausible-looking noise.

## 6. Bugs found and fixed (do not reintroduce)

- Open positions never monitored (`continue` before the monitor call)
- Score ceiling 82 then multiplied by a regulatory multiplier -> 78 unreachable
- Shadow ledger sampled close only, missing intrabar moves
- Win defined as reaching TP4, so TP1+TP2 trades booked as losses
- Target leakage: post-mortem tags fed into factor lift as predictors
- Factor labels embedded live numbers -> thousands of one-off "factors"
- Invalidation was long-biased, firing on winning shorts
- Crypto tickers collided with equity symbols on the earnings calendar
- 429 from calendar mirror blanked the calendar silently
- Churn loop: one setup logged 21 times (IBM, entry 224.61)
- Trail triggered on the tagging bar, killing every runner at TP1
- Stopped-out trades booked at live price instead of the stop (-1% shown as +4.4%)
- State paths relative to cwd -> Redis restore wrote where nothing read
- 39 assets defaulted to 50x leverage; no liquidation-distance check at all
- Breakeven counted as loss (twice: `engine_efficiency` and `shadow_ledger`)
- `RISK_FLOOR_USD` overrode Kelly's negative-edge veto
- 1m refetch used a 20-min lookback -> 10 fake instant wins
- Live and replay used DIFFERENT trailing rules, both stamped v4
- Price-scale: 1000BONK fetched without the /1000 divisor -> MAE -100,115%

## 7. The open question

**Score bins were inverted** — 70+ won 0% while 50-60 won 62.7%. Root cause: the
structure pillar awarded the same +40% for bullish and bearish BOS, but measured:

```
BEARISH BOS + SHORT   n=103   71.8%   +0.758R
BULLISH BOS + LONG    n= 46   26.1%   -0.501R
```

Fixed via `regime_performance.py` — adjustments learned from outcomes with Bayesian
shrinkage `n/(n+25)`, minimum n=15. Validated out-of-sample (fit on first 282 trades,
tested on 122 unseen):

```
30-40  n=36  acc 41.7%  avgR -0.188
40-50  n=44  acc 70.5%  avgR +0.515
50-60  n=32  acc 71.9%  avgR +0.617
60-70  n=10  acc 90.0%  avgR +1.280      monotonic: TRUE
```

**BUT the extreme tail may still invert:** setups above 78 went 0-for-3 (-3.00R) while
70-78 went 11-for-14 (+9.35R, +0.668R avg). n=3 — could be variance, could be
"maximum confluence means you are late." Unresolved.

Tested and REJECTED: 1m entry-quality (VWAP extension, EMA5 decay, bar maturity).
Flagged 34.2% of winners vs 33.3% of losers — no discrimination. Archived.

## 8. First things to do in the new session

1. **Verify state:**
   ```bash
   curl -s https://den-quant-scanner.onrender.com/ | grep -E "total_scans|shadow_open|scan_duration"
   cd /Users/siva/Desktop/Stocks/den/models && python3 -c "
   import sys; sys.path.insert(0,'.')
   from audit.shadow_ledger import ShadowTradeLedger as S
   print(S.summary()); print(S.audit_integrity()); print(S.version_report())"
   ```

2. **Check whether the 78+ inversion is real** now that more records exist. If 78+ is
   still ~0% at n>=15, it is a real effect. If it is ~70%, there was never an inversion.

3. **Decide the dispatch floor.** Currently 78, which yields ~1 signal/15h. The
   validated band is 70-78 (78.6% accuracy, +0.668R). This is a real-money decision.

4. **Rebuild regime weights** — they refresh every 15 min automatically, but force with:
   `python3 -c "import sys;sys.path.insert(0,'.');from audit.regime_performance import RegimePerformance as R;print(R.report())"`

## 9. Still outstanding

- 4 modules never logic-reviewed: `telegram_bot.py`, `portable_store.py`,
  `redis_state_sync.py` internals, `regulatory_events.py`
- Upstash token was pasted in plaintext in an earlier session — rotate it
- New two-axis regime labels (`BULL_EXPANSION` etc.) not yet validated; learned weights
  currently key off the legacy label because that is what historical records carry
- `max drawdown 103%` in the equity curve means the modelled account went below zero
  at one point — the R-based curve does not floor at bankruptcy. Cosmetic, but worth
  fixing so the number is not misleading.

## 10. Working agreement

The user does not read Python but reasons precisely about trading semantics, and has
caught more real defects than I have — the churn duplicates, the trail semantics, the
lower-timeframe insight, the erased records, the breakeven double-count. Show measured
evidence, not assurances. When something cannot be verified, say so plainly.
