# Den — evidence first, Kelly second

Den scans markets and produces **WAIT** or **SIGNAL**. It does not place exchange orders.

**Current finding: a profitable edge has not been demonstrated.** The saved local history uses an older, defective execution path. The corrected engine keeps collecting paper trades and refuses to size an unvalidated model.

## What you need to read

| Message | Meaning | Action |
|---|---|---|
| **WAIT — LEARNING** | There is not enough trustworthy, current evidence. | No trade. Let the paper book collect outcomes. |
| **WAIT — VALIDATED** | The model passed its tests, but this setup failed an entry or risk check. | No trade. Read the stated reason. |
| **SIGNAL** | This particular setup passed model, execution and portfolio checks. | Review entry, stop, targets and dollar risk; confirm your exchange's actual prices and limits. |
| **WIN / LOSS** | The paper execution model resolved a dispatched signal. | Treat the net result as an estimate until reconciled to your fills. |

Telegram commands: `/status` for the current decision, `/signals` for dispatched paper outcomes, `/calendar` for scheduled events. `/kelly` and `/ledger` also show the simple status. Reply `positioned` to a signal you entered.

## How money management works

The engine estimates net profitability, checks its forecast on later trades, and uses a conservative probability bound for half-Kelly sizing. It considers the size of wins and losses as well as their frequency. A 90% win rate with tiny wins can still be a losing strategy.

There is no minimum bet. An unknown or negative edge gets **$0**. Risk is capped at **1% of configured capital per trade**, **10% across open positions**, and **50% margin usage**. These are planning limits; gaps and actual execution costs can exceed them. Candidates are ranked by conservative expected account growth.

`DEN_ACCOUNT_BALANCE` is your explicitly configured sizing balance (default $1,000). It is **not** a connected exchange balance. Update it to match the capital you intend to allocate; paper P&L does not automatically change real-account sizing.

## Deployment endpoint

The current server address provided by the owner is [13.140.188.62:10000](http://13.140.188.62:10000/). The self-health request uses this address by default; override it with `DEN_PUBLIC_HEALTH_URL` when moving the server. `render.yaml` remains a legacy Render deployment option. Local code changes do not update the server automatically.

## Local checks

Run from the repository root:

```sh
python3 -m unittest discover -s tests -v
python3 scripts/audit_engine.py --output docs/audit-evidence.json
python3 models/audit/decision_report.py
```

These commands do not start the scanner or send messages. The status command needs a completed scanner run to show a live decision.

The scanner entry point remains `python3 models/auto_scanner.py`. Starting it enables its configured Telegram and state-sync integrations. Credentials must be supplied through `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`; there are no built-in fallback credentials. The audit did not start or deploy that process.

Read [the audit](docs/ENGINE_AUDIT.md) for findings, evidence, execution assumptions and remaining limits. The numerical diagnostics are in [audit-evidence.json](docs/audit-evidence.json).
