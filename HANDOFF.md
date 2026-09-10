# Den engine — current handoff

Start with README.md and docs/ENGINE_AUDIT.md. The older handoff is preserved in docs/LEGACY_HANDOFF.md as historical context; its performance claims and implementation descriptions are not current evidence.

Current server address supplied by the user: http://13.140.188.62:10000/. Override via DEN_PUBLIC_HEALTH_URL. The old Render address is historical.

Current local version: v43.0-audited. Execution: v5-ordered-1m-cost-aware. Feature schema: v2-closed-bars-directional. Probability model: v2-purged-net-outcomes.

The active engine uses ordered complete 1m candles and the same deterministic trailing rules for shadow trades, monitoring and replay. A target arms as the trail on the next minute. Check the existing stop before a new target; account for gaps. Never use the entry minute, forming candles, merged high/low windows or missing-history paths to claim a known outcome.

The local book has 1,854 legacy outcomes, zero compatible new outcomes, and no demonstrated deployable edge. Preserve the old records; do not silently relabel them. Replay cannot reconstruct incompatible entry features. Model promotion requires separate purged chronological train/calibration/test periods and positive evidence after costs. Unvalidated forecasts remain paper-only.

Kelly consumes a probability lower bound and conservative net payoffs. No minimum bet. Planned risk cap: 1% per trade, 10% portfolio risk, 50% margin. Capital is explicitly configured, not read from an exchange. Candidate ranking considers expected account growth, not accuracy alone.

User-facing status is WAIT or SIGNAL. WAIT is a valid result. Do not restore score-as-probability, forced signal quotas, dry-spell threshold relaxation or certainty claims.

Verification: python3 -m unittest discover -s tests -v; python3 scripts/audit_engine.py --output docs/audit-evidence.json. These do not start the scanner or send messages. Test external boundaries with mocks. Do not start production integrations as an import or test side effect.

Changes are local, not deployed. Real exchange fills, contract specifications, account balance and production connectivity remain unverified. Outstanding credential rotation and distributed persistence limitations are documented in the audit.
