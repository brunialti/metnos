#!/usr/bin/env python3
"""metnos-sched — admin CLI per scheduler v2 + recurring user tasks.

Usage:
    python3 -m admin.scheduler_cli list                    # tutti i job
    python3 -m admin.scheduler_cli list --user             # solo recurring user
    python3 -m admin.scheduler_cli list --system           # solo job di sistema
    python3 -m admin.scheduler_cli show <name>             # dettaglio + last fire
    python3 -m admin.scheduler_cli history <name> [--limit N]
    python3 -m admin.scheduler_cli enable <name>
    python3 -m admin.scheduler_cli disable <name>
    python3 -m admin.scheduler_cli cancel <name>           # cancella user job
    python3 -m admin.scheduler_cli run-now <name>          # avanza next_fire a now
    python3 -m admin.scheduler_cli daemon-status           # health del service

PR5: il loop di scheduling gira in-process all'HTTP server (scheduler v2).
Le sottocomandi `enable/disable/cancel/run-now` sono scritture sullo storage
v2 + kick best-effort del daemon co-locato; l'esito reale (status, output)
viene osservato via `history` dopo il prossimo tick.
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scheduler_v2 import client as sched_client  # noqa: E402
from recurring_tasks import (  # noqa: E402
    list_user_tasks, cancel_user_task,
)


def _fmt_ts(ts: str | None) -> str:
    if not ts:
        return "-"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%d/%m %H:%M")
    except (ValueError, TypeError):
        return ts[:16]


def cmd_list(args):
    rows = sched_client.list_jobs()
    user_recs = {f"user_{r['name']}": r for r in list_user_tasks()}
    print(f"  {'NAME':22s} {'TRIGGER':14s} {'EN':2s} {'LAST_FIRE':12s} {'STATUS':8s} {'KIND':5s}")
    print(f"  {'-'*22} {'-'*14} {'-'*2} {'-'*12} {'-'*8} {'-'*5}")
    n = 0
    for r in rows:
        name = r["name"]
        is_user = name.startswith("user_")
        if args.user and not is_user:
            continue
        if args.system and is_user:
            continue
        kind = "USER" if is_user else "SYS"
        print(f"  {name:22s} {(r['trigger'] or '-'):14s} "
              f"{('y' if r.get('enabled') else 'n'):2s} "
              f"{_fmt_ts(r.get('last_run_at')):12s} "
              f"{(r.get('last_status') or '-'):8s} "
              f"{kind:5s}")
        if is_user and name in user_recs:
            ur = user_recs[name]
            print(f"    └ label: {ur.get('label') or '-'}")
            print(f"    └ query: {ur['query'][:80]!r}")
            print(f"    └ actor={ur['actor']} channel={ur['channel']} chat_id={ur.get('chat_id') or '-'}")
        n += 1
    print(f"\n({n} jobs)")


def cmd_show(args):
    rows = [r for r in sched_client.list_jobs() if r["name"] == args.name]
    if not rows:
        print(f"task '{args.name}' non trovato", file=sys.stderr)
        sys.exit(1)
    r = rows[0]
    print(f"name:         {r['name']}")
    print(f"trigger:      {r['trigger']}")
    print(f"callback_key: {r.get('callback_key')}")
    print(f"enabled:      {bool(r.get('enabled'))}")
    print(f"last_run:     {_fmt_ts(r.get('last_run_at'))}")
    print(f"last_status:  {r.get('last_status')}")
    print(f"last_error:   {(r.get('last_error') or '')[:300]}")
    print(f"total_runs:   {r.get('total_runs')}  failures: {r.get('total_failures')}")
    if args.name.startswith("user_"):
        user_name = args.name[len("user_"):]
        urs = [u for u in list_user_tasks() if u["name"] == user_name]
        if urs:
            ur = urs[0]
            print()
            print(f"--- recurring user record ---")
            for k in ("label", "query", "actor", "channel", "chat_id", "callback_key", "created_at"):
                print(f"  {k}: {ur.get(k)}")


def cmd_history(args):
    name = None if args.name == "all" else args.name
    rows = sched_client.history(name=name, limit=args.limit)
    print(f"  {'STARTED':12s} {'TASK':22s} {'STATUS':8s} {'DUR':>6s}  OUTPUT[:60]")
    print(f"  {'-'*12} {'-'*22} {'-'*8} {'-'*6}  {'-'*60}")
    for r in rows:
        dur = r.get('duration_ms')
        dur_s = f"{dur:>5}ms" if dur is not None else "    -"
        print(f"  {_fmt_ts(r['started_at']):12s} {r['entry_name']:22s} "
              f"{r['status']:8s} {dur_s}  {(r.get('output') or '')[:60]}")
    print(f"\n({len(rows)} entries)")


def cmd_enable(args):
    ok = sched_client.toggle_job(args.name, True)
    if not ok:
        print(f"task '{args.name}' non trovato", file=sys.stderr)
        sys.exit(1)
    print(f"enabled: {args.name}")


def cmd_disable(args):
    ok = sched_client.toggle_job(args.name, False)
    if not ok:
        print(f"task '{args.name}' non trovato", file=sys.stderr)
        sys.exit(1)
    print(f"disabled: {args.name}")


def cmd_cancel(args):
    if not args.name.startswith("user_"):
        print(f"ERR: cancel funziona solo su recurring user tasks (prefisso 'user_'). "
              f"Per disabilitare task di sistema usa `disable`.", file=sys.stderr)
        sys.exit(2)
    user_name = args.name[len("user_"):]
    ok_db = cancel_user_task(user_name)  # admin cancel: no actor restrict
    if not ok_db:
        print(f"task '{args.name}' non trovato in DB user", file=sys.stderr)
        sys.exit(1)
    sched_client.cancel_job(args.name)
    print(f"cancelled: {args.name}")


def cmd_run_now(args):
    """Avanza next_fire_at a now() + kick. Il fire effettivo avviene al prossimo
    tick del daemon co-locato in-process all'HTTP server. L'esito (status,
    output) si osserva con `history`.
    """
    out = sched_client.run_now(args.name)
    if not out.get("ok"):
        print(f"run_now failed: {out.get('error')}", file=sys.stderr)
        sys.exit(1)
    print(f"scheduled: {args.name} (next_fire_at advanced; check `history` after tick)")


def cmd_daemon_status(_args):
    import subprocess
    try:
        # In v2 lo scheduler vive nel processo HTTP server (no service dedicato).
        r = subprocess.run(
            ["systemctl", "--user", "is-active", "metnos-http.service"],
            capture_output=True, text=True, timeout=5,
        )
        print(f"http service active: {r.stdout.strip()}")
        r2 = subprocess.run(
            ["systemctl", "--user", "show", "metnos-http.service",
              "--property=MainPID,ActiveEnterTimestamp,NRestarts"],
            capture_output=True, text=True, timeout=5,
        )
        print(r2.stdout)
    except Exception as e:
        print(f"check fallito: {e}", file=sys.stderr)


def main():
    p = argparse.ArgumentParser(prog="metnos-sched")
    sub = p.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("list")
    pl.add_argument("--user", action="store_true")
    pl.add_argument("--system", action="store_true")
    pl.set_defaults(fn=cmd_list)
    ps = sub.add_parser("show"); ps.add_argument("name")
    ps.set_defaults(fn=cmd_show)
    ph = sub.add_parser("history")
    ph.add_argument("name", nargs="?", default="all")
    ph.add_argument("--limit", type=int, default=20)
    ph.set_defaults(fn=cmd_history)
    pe = sub.add_parser("enable"); pe.add_argument("name")
    pe.set_defaults(fn=cmd_enable)
    pd = sub.add_parser("disable"); pd.add_argument("name")
    pd.set_defaults(fn=cmd_disable)
    pc = sub.add_parser("cancel"); pc.add_argument("name")
    pc.set_defaults(fn=cmd_cancel)
    pr = sub.add_parser("run-now"); pr.add_argument("name")
    pr.set_defaults(fn=cmd_run_now)
    pds = sub.add_parser("daemon-status")
    pds.set_defaults(fn=cmd_daemon_status)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
