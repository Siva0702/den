# Engine audit — 11 September 2026 (IST)

## Result

The local engine has been repaired around one execution definition, one validated probability model and one cost-aware Kelly policy. **This is a correctness and decision-quality improvement, not evidence of a profitable trading edge or 90% accuracy.** No production deployment, real trade, Telegram message or remote state write was performed by this audit.

The audit covers the active path from candle adapters through technical features, enrichment, model fitting, calibration, sizing, gates, shadow sampling, position monitoring, replay, dispatch accounting and user status. Persistence and messaging boundaries received targeted checks. Archived strategy modules were not rehabilitated or promoted. Production uptime, exchange fills and real-account balances remain unverified. The user supplied the new server address http://13.140.188.62:10000/. A direct health GET timed out after 15 seconds with no response; this is not proof that the server is down. Local self-health configuration and operating documentation now use the new address, overridable through DEN_PUBLIC_HEALTH_URL. The former Render address is historical.

## Measured local evidence

The saved book contains **1,854** records from **4–11 August 2026 UTC**, all stamped with the old `v4-trail-one-behind-1m` logic. Three missing-data timeouts are excluded from modelling. There are **zero records with both the new execution and feature versions**.

The reproducible audit uses fixed settings and expanding chronological windows, rather than searching for a flattering threshold. Its latest split has 995 training records, 300 calibration records and 371 test records; 185 trades are purged because their outcome windows cross a boundary. The numerical results are in `audit-evidence.json`.

These legacy outcomes are diagnostically useful but **unverified**. They must not be read as achievable returns. The revised model failed promotion on them: insufficient time coverage, stale evidence, incompatible feature/execution versions and insufficient reliable net-return evidence. Even replaying prices cannot reconstruct missing historical features or establish an executable entry at an old cached quote. New feature snapshots must be collected prospectively.

## Confirmed defects and changes

| Area | Defect | Correction |
|---|---|---|
| Candle intervals | Requests for 1m and 5m silently became 15m on all three providers. | Explicit interval maps; unsupported intervals rejected. |
| Data integrity | Provider order, timestamps, OHLC geometry and finite values were trusted. | Sorted, contiguous, correctly spaced candles with finite, valid prices are required. |
| Price freshness | Cached forming candles were treated as live entry prices for an entire timeframe. | Execution quotes refresh within ten seconds; closed technical snapshots retain candle-aligned caching. |
| Entry timing | Missing 5m data bypassed the timing gate. | Missing timing data means WAIT; timing indicators use closed bars. |
| Late paper entries | Candidates waited for all assets' enrichment before being opened at older prices. | Refresh the quote after enrichment and open each paper trade immediately. |
| Resolution | Several minutes were collapsed into a single high/low range. | Preserve each timestamp and replay the ordered one-minute path. |
| Trail order | A new target could supersede an earlier stop; repeated scans could arm a trail on the same candle. | Check the already armed stop before a new rung. Process each complete minute once. |
| Consistent exits | Live positions closed at TP1; shadow and recovery used different trailing rules. | One pure execution routine is used by all three paths. |
| Gap risk | A worse observed stop fill was improved back to the planned stop in the ledger. | Opening gaps fill at the worse open; no artificial stop-price guarantee. |
| Realized price | Monitoring reported a later sampled close instead of the actual exit event. | Persist the exact event, fill estimate, reason and timestamp. |
| Missing history | Missing, pre-entry or forming candles could fabricate outcomes. | No label from incomplete paths; historical catch-up is bounded and replay rejects gaps. |
| Training leakage | The probability calibrator used the classifier's training predictions. | Chronologically separate training, calibration and test sets; purge overlapping outcomes. |
| Evidence compatibility | Old 15m backtests, stale outcomes and incompatible feature versions could support live confidence. | Separate versions, freshness checks and explicit abstention; historical backtests are research only. |
| Historical research | Backtest timestamps used the wall clock; HTF groups used row offsets; current learned weights could leak into history. | Use candle timestamps and calendar-aligned groups, disable live learned adjustments, and explicitly mark coarse research ineligible for live calibration. |
| Selection bias | Low model scores could be excluded from shadow learning. | Shadow sampling uses the fixed technical candidate floor, independent of the learned probability. |
| Probability meaning | A high displayed score could masquerade as a measured win probability. | Distinguish probability, uncertainty bound and display score. Only a validated model authorizes dispatch. |
| Payoff mismatch | Gross win labels and planned TP reward inflated Kelly's edge. | Learn net-positive outcomes; use conservative observed winning payoffs and loss tails as well as planned costs. |
| Kelly sizing | A dollar floor could override a small positive Kelly allocation and the stated cap. | No floor; round allocation down; zero size for missing, invalid or negative-edge inputs. |
| Portfolio exposure | Same-scan peers were checked without all existing positions. | Check open and pending positions, overlapping correlation groups, aggregate risk and margin. |
| Threshold drift | A dry spell automatically lowered the dispatch threshold. | Fixed threshold; inactivity does not justify a weaker trade. |
| Stability | Repeated cached scans counted as independent confirmations. | Distinct minute observations over at least two minutes; direction changes reset confirmation. |
| Structure | BOS used the window extreme despite claiming to use the most recent swing. | Use the latest confirmed swing. |
| HTF alignment | Alignment counted the HTF majority even when the selected trade opposed it. | Count agreement with the actual selected direction. |
| RSI / EMA | Flat prices appeared oversold; default history was too short for EMA200. | Flat RSI is 50; fetch 260 execution-timeframe bars. |
| BTC relationship | Correlation could align row indexes rather than timestamps and include forming bars. | Join closed returns by timestamp; handle constant-return correlations safely. |
| Learned adjustments | Legacy efficiency data could influence current direction; regime keys differed between fitting and lookup. | Remove legacy per-ticker tilt; align regime keys and use current-version net outcomes. |
| Calendar / headlines | Stale calendar data could appear clear; generic event headlines could create persistent blanket vetoes. | Expose stale/unavailable calendar state; calendar timestamps determine scheduled blackouts. |
| Dispatch delivery | Failed sends still consumed quota and created audit records. | Count only successful delivery; track dispatch identity and propagate audit-write failures. |
| Accounting identity | Same ticker/direction could close the wrong dispatch. | Prefer exact dispatch identity; retries retain the event and avoid duplicate efficiency outcomes. |
| Equity display | A bankrupt simulated account could later resume making money. | Stop the simulated curve at ruin. |
| Storage | Milestone paths depended on working directory; remote restore could write malformed JSON or shrink historical ledgers. | Stable paths, atomic writes, type checks and history-size guards. |
| Messaging | Hardcoded credentials and unrestricted incoming chat messages. | Explicit credentials only; filter replies to the configured chat. |
| User experience | Conflicting scores, twenty-row digests and certainty labels obscured the decision. | Short WAIT / SIGNAL status and one entry/risk/exit explanation. |

## Model promotion policy

The base model remains small, regularized logistic regression. More model complexity is not justified by the local data. The base classifier uses the oldest 60%, the calibrator the next 20%, and evaluation the newest 20%, after purging outcomes that were not known before the next period began. The deployed fitted objects are the same ones evaluated; they are not silently refitted on the test set.

At least 100 training, 40 calibration and 40 test records are required, with both outcomes present in the first two blocks. Further checks require at least seven days of compatible history, evidence no older than seven days, probability error better than a constant base-rate forecast on both Brier score and log loss, at least 30 test candidates over the fixed 70% probability threshold, and positive net expectancy under a daily-block uncertainty check. Three daily blocks are a minimum for that diagnostic, not proof of robustness across regimes.

Local probability support discounts clustered observations to distinct hours and requires at least 20 such observations. Sizing uses the smaller of the model probability and a Wilson lower bound. Missing core features, nonfinite inputs, incompatible feature schemas and extreme out-of-distribution inputs cause abstention. Correlation across hours/days can still make uncertainty greater than these diagnostics estimate.

Model validation and the candidate's entry/risk checks are separate requirements. A fitted but unvalidated model can score paper candidates, but cannot authorize dispatch. No threshold is lowered to satisfy a signal quota. Candidate ranking uses conservative expected log growth.

## Execution and cost assumptions

- Complete 1m OHLC is the finest resolution used. The entry minute is excluded because its pre-entry high/low cannot be separated. This is an explicit blind spot; it is not tick-level or actual-fill validation.
- Stop first when an already armed stop and a target both occur within one minute. A reached target becomes the trail from the next minute. The final target exits immediately. Opening gaps use the worse open, including gaps through an armed profitable trail.
- The maximum holding horizon is 36 hours. A timeout exits at the observed complete candle's close; an incomplete path is not labelled as a known trade outcome.
- Cost assumptions: 0.06% taker fee per side, two basis points slippage per side, and a conservative funding allowance. Fees use entry and exit notional. These are configurable-in-code modelling assumptions, not verified current Bitunix/WEEX fees or execution guarantees.
- Kelly uses net-positive labels. Planned winning payoff is capped by a conservative observed winner payoff; loss estimates incorporate the observed tail. Dollar allocation has no floor and is capped at 1% of configured capital. Notional sizing uses the modelled loss amount, rather than treating leverage as risk.
- Paper signals use public market feeds. There is no connected exchange order/fill/position reconciliation. Contract availability, tick/lot sizes, funding schedules, margin tiers and exact liquidation rules must be verified against the intended execution venue before automated order placement is added.

## Validation performed

**57 offline regression and integration tests pass.** The full scanner tests exercise a valid simulated dispatch, failed Telegram delivery, rejected model, negative edge and failed execution feed, with network/storage boundaries mocked. Other tests cover provider interval maps, stale/wrong candles, live/replay/monitor parity, long and short trailing, stop/target collisions, gaps, repeated bars, missing history, purged fitting, synthetic known signal versus noise, capital caps, costs, correlated exposure, state restore and status clarity. All 42 active Python modules imported with network access blocked. Python compilation and diff whitespace checks also pass.

Tests establish those behaviours, not future investment performance. No production Telegram send or real trade was used as a test. The historical evidence audit is read-only and does not modify the production ledgers or model cache.

## Remaining work before claiming an edge

1. Run the corrected engine prospectively in paper mode, collect compatible outcomes across changing market conditions, and retain the frozen features/probability at each entry.
2. Require independent validation and monitor realised net returns and calibration drift. A claimed 90% accuracy needs its own adequately sized, unseen sample and confidence interval; it is not a configuration setting.
3. Reconcile estimates to the intended venue's actual fills, costs and account balance before adding order execution or scaling real capital.
4. Rotate the previously embedded Telegram credential through the account owner. Removing defaults does not revoke a token or remove it from Git history. The old handoff also records an earlier exposed Redis token; this audit did not access credential-management systems.
5. The JSON/SQLite/Redis store remains a single-process architecture, not a transactional distributed trading ledger. Atomic writes and restore guards reduce defects but do not provide distributed exactly-once execution. Remote deployment and connectivity have not been certified.

## References

The classifier/calibrator separation follows the [scikit-learn probability calibration documentation](https://scikit-learn.org/stable/modules/calibration.html). Provider interval and reverse chronological ordering are documented in [Bybit Get Kline](https://bybit-exchange.github.io/docs/v5/market/kline). The fixes themselves are verified against local code and the offline tests; those references do not establish a trading edge.
