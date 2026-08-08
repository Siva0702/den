#!/usr/bin/env python3
"""
Detailed dashboard for DISPATCHED signals — the trades actually sent to Telegram.

Run from the repo:   python3 models/audit/dispatch_report.py

Reads LIVE state from Redis first, falling back to local disk only if Redis is
unreachable. That order matters: the local files lag the running engine by up to
15 minutes, and reporting from them produced confidently wrong numbers all through
this project's history.

All P&L is NET of taker fees on both legs and of any funding settlements actually
crossed. Gross is shown alongside so the cost of trading is visible rather than
silently absorbed.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load():
    """Redis first — local disk is a stale snapshot, not the source of truth."""
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))), ".env"))
    except Exception:
        pass
    reachable = False
    try:
        from audit.redis_state_sync import UpstashRedisStateSync as R
        ok, v = R._redis_cmd(["GET", "den:dispatch_ledger"])
        reachable = bool(ok)
        if ok and v:
            rows = json.loads(v) if isinstance(v, str) else v
            if isinstance(rows, list):
                return rows, "redis (live)"
    except Exception:
        pass
    from audit.dispatch_ledger import DispatchLedger
    # Distinguish "Redis is down" from "the key does not exist yet". Conflating them
    # sends you hunting a connection problem that is not there.
    note = ("redis reachable, key not written yet — local disk"
            if reachable else "local disk (REDIS UNREACHABLE — may be stale)")
    return DispatchLedger.load(), note


def main(capital=1000.0, risk=30.0):
    rows, src = _load()
    print(f"\n{'=' * 78}\n  DISPATCHED SIGNAL DASHBOARD   source: {src}\n{'=' * 78}")
    if not rows:
        print("\n  No dispatched signals recorded yet.")
        print("  The ledger begins at the first dispatch after it was deployed;")
        print("  signals sent before that were never persisted and are unrecoverable.\n")
        return

    closed = [r for r in rows if r.get("status") == "CLOSED"]
    live = [r for r in rows if r.get("status") != "CLOSED"]

    if live:
        print(f"\n  OPEN / DISPATCHING ({len(live)})")
        print(f"  {'dispatched':<18}{'ticker':<12}{'dir':<6}{'score':>6}{'prob':>7}"
              f"{'entry':>11}{'stop':>11}{'lev':>5}{'margin':>9}")
        for r in sorted(live, key=lambda x: x.get("dispatched_epoch") or 0):
            print(f"  {str(r.get('dispatched_time'))[:16]:<18}{str(r.get('ticker')):<12}"
                  f"{str(r.get('direction')):<6}{(r.get('model_score') or 0):>6.1f}"
                  f"{(r.get('model_prob') or 0):>7.3f}{(r.get('entry') or 0):>11.6g}"
                  f"{(r.get('stop_loss') or 0):>11.6g}{str(r.get('leverage') or '-'):>5}"
                  f"{(r.get('margin') or 0):>9.2f}")

    if not closed:
        print(f"\n  No dispatched signal has closed yet ({len(live)} still running).\n")
        return

    print(f"\n  CLOSED ({len(closed)})")
    print(f"  {'closed':<18}{'ticker':<12}{'dir':<6}{'score':>6}{'reason':<22}"
          f"{'grossR':>8}{'netR':>8}{'fee$':>8}{'net$':>9}")
    tot_net = tot_fee = 0.0
    for r in sorted(closed, key=lambda x: x.get("closed_epoch") or 0):
        notional = r.get("notional") or 0.0
        net_usd = (r.get("pnl_pct") or 0.0) / 100.0 * notional
        fee_usd = r.get("fee_usd") or 0.0
        tot_net += net_usd
        tot_fee += fee_usd
        print(f"  {str(r.get('closed_time'))[:16]:<18}{str(r.get('ticker')):<12}"
              f"{str(r.get('direction')):<6}{(r.get('model_score') or 0):>6.1f}"
              f"{str(r.get('exit_reason')):<22}{(r.get('gross_r') or 0):>+8.2f}"
              f"{(r.get('r_multiple') or 0):>+8.2f}{fee_usd:>8.2f}{net_usd:>+9.2f}")

    Rs = [float(r.get("r_multiple") or 0.0) for r in closed]
    gRs = [float(r.get("gross_r") or 0.0) for r in closed]
    wins = sum(1 for r in closed if r.get("is_win"))
    gw = sum(x for x in Rs if x > 0)
    gl = -sum(x for x in Rs if x < 0)
    scratched = sum(1 for r in closed if r.get("exit_reason") == "SCRATCHED_BREAKEVEN")

    print(f"\n{'-' * 78}\n  PERFORMANCE (net of all costs)")
    print(f"    signals dispatched   {len(rows)}   ({len(closed)} closed, {len(live)} open)")
    print(f"    wins / losses        {wins}W / {len(closed) - wins}L   "
          f"accuracy {wins / len(closed) * 100:.1f}%")
    print(f"    scratched breakeven  {scratched}")
    print(f"    total R  gross       {sum(gRs):+.3f}")
    print(f"    total R  NET         {sum(Rs):+.3f}   (avg {sum(Rs) / len(Rs):+.3f}/trade)")
    print(f"    profit factor        {round(gw / gl, 3) if gl else '— (no losses yet)'}")
    print(f"\n  ACCOUNT  (${capital:,.0f} capital, ${risk:.0f} risk/trade)")
    print(f"    fees paid            ${tot_fee:,.2f}")
    print(f"    net P&L              ${tot_net:+,.2f}")
    print(f"    ending equity        ${capital + tot_net:,.2f}   "
          f"({tot_net / capital * 100:+.2f}%)")
    if len(closed) < 20:
        print(f"\n  NOTE: {len(closed)} closed trades is too few to conclude anything. "
              f"Treat this as a\n        record of what happened, not evidence of edge.")
    print(f"{'=' * 78}\n")


if __name__ == "__main__":
    cap = float(sys.argv[1]) if len(sys.argv) > 1 else 1000.0
    rsk = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0
    main(cap, rsk)
