"""Migrate v1 scheduler state into scheduler v2.

Reads two v1 SQLite DBs:

  - `recurring_tasks.db` (table `recurring_tasks`): user-defined recurring
    tasks (`create_tasks` from chat, formerly `schedule_recurring`). Each row maps to a v2 entry
    with `callback_key="run_user_query"`, `origin="user"`, and a payload
    carrying the fields the v1 callback closure used to capture (query,
    actor, channel, chat_id, name, label).

  - `state.sqlite` (table `tasks`): v1 builtin scheduler state, including
    `last_run_at` / `last_status`. We map the 7 known builtin names to v2
    entries with `origin="system"` and `payload={}`.

Output: `scheduler_v2.sqlite` — the v2 DB created (or extended) via
`SchedulerStorage.upsert`. Idempotent: re-running on an already-migrated
DB is a no-op (we skip names already present in v2).

`--dry-run` prints what would change without writing.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .builtin_callbacks import _BUILTIN_JOBS
from .models import ScheduleEntry
from .schedule_parser import next_fire_at as compute_next_fire
from .storage import SchedulerStorage


# v1 builtin names -> v2 callback_key. Values mirror what
# install_default_callbacks registers; trigger comes from _BUILTIN_JOBS.
_BUILTIN_NAME_TO_KEY: dict[str, str] = {
    j["name"]: j["callback_key"] for j in _BUILTIN_JOBS
}
_BUILTIN_NAME_TO_TRIGGER: dict[str, str] = {
    j["name"]: j["trigger"] for j in _BUILTIN_JOBS
}


def _open_ro(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    )
    return cur.fetchone() is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _to_iso(s: Any) -> str | None:
    """Normalize v1 ISO timestamps to v2 UTC ISO. None -> None."""
    if s in (None, ""):
        return None
    if isinstance(s, str):
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat(timespec="seconds")
        except Exception:
            return s  # keep as-is rather than drop info
    return None


def _migrate_user_tasks(
    src: sqlite3.Connection, dst: SchedulerStorage, *, dry_run: bool, tz_name: str
) -> tuple[int, int, int]:
    """Returns (migrated, skipped, errors)."""
    if not _table_exists(src, "recurring_tasks"):
        return (0, 0, 0)
    cols = _columns(src, "recurring_tasks")
    rows = src.execute("SELECT * FROM recurring_tasks").fetchall()
    migrated = skipped = errors = 0
    now = time.time()
    for row in rows:
        rec = {k: row[k] for k in row.keys()}
        v2_name = f"user_{rec['name']}"
        if dst.get_by_name(v2_name) is not None:
            skipped += 1
            continue
        try:
            trigger = rec["schedule"]
            # Anchor: per never-fired daily tasks, partiamo dall'inizio della
            # giornata locale corrente cosi' un target gia' passato OGGI viene
            # scelto come prossimo fire (recover_missed lo fira al boot).
            # Per task gia' firati, anchor = now (target sarebbe domani: ok).
            fired_count_col = rec.get("fired_count") if "fired_count" in cols else 0
            if not fired_count_col:
                from datetime import datetime
                from zoneinfo import ZoneInfo
                local_now = datetime.now(ZoneInfo(tz_name))
                day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
                anchor = day_start.timestamp()
            else:
                anchor = now
            try:
                nxt = compute_next_fire(trigger, anchor, tz_name)
            except ValueError:
                errors += 1
                continue
            payload = {
                "query": rec.get("query"),
                "channel": rec.get("channel"),
                "actor": rec.get("actor"),
                "chat_id": rec.get("chat_id"),
                "name": rec.get("name"),
                "label": rec.get("label"),
            }
            times = rec.get("times") if "times" in cols else None
            fired = rec.get("fired_count") if "fired_count" in cols else 0
            remaining = 0
            if times and int(times) > 0:
                remaining = max(0, int(times) - int(fired or 0))
            grace_min = rec.get("grace_window_minutes") if "grace_window_minutes" in cols else None
            grace_s = int(grace_min) * 60 if grace_min else None
            entry = ScheduleEntry(
                name=v2_name,
                trigger=trigger,
                next_fire_at=nxt,
                recurring=True,
                callback_key=rec.get("callback_key") or "run_user_query",
                payload=payload,
                enabled=bool(rec.get("enabled", 1)),
                grace_window_s=grace_s,
                remaining_runs=remaining,
                origin="user",
                label=rec.get("label") or "",
                source_command=f"schedule_recurring(label={rec.get('label')!r})",
                description=f"user task: {rec.get('label') or rec.get('name')} (actor={rec.get('actor')})",
                created_at=_to_iso(rec.get("created_at")) or "",
            )
            if dry_run:
                print(f"[dry-run] +user {v2_name} trigger={trigger} actor={rec.get('actor')}")
            else:
                dst.upsert(entry)
            migrated += 1
        except Exception as exc:  # noqa: BLE001
            print(f"[error] user task {rec.get('name')!r}: {exc}", file=sys.stderr)
            errors += 1
    return (migrated, skipped, errors)


def _migrate_builtin(
    src: sqlite3.Connection, dst: SchedulerStorage, *, dry_run: bool, tz_name: str
) -> tuple[int, int, int]:
    """Returns (migrated, skipped, errors)."""
    if not _table_exists(src, "tasks"):
        return (0, 0, 0)
    rows = src.execute("SELECT * FROM tasks").fetchall()
    migrated = skipped = errors = 0
    now = time.time()
    for row in rows:
        rec = {k: row[k] for k in row.keys()}
        name = rec["name"]
        # User tasks live in scheduler.tasks too (prefix "user_") — those are
        # the source of truth in recurring_tasks.db; skip the v1 mirror here
        # to avoid double-migration.
        if name.startswith("user_"):
            skipped += 1
            continue
        callback_key = _BUILTIN_NAME_TO_KEY.get(name)
        if callback_key is None:
            # Unknown builtin (perhaps an old/dead name): skip rather than
            # invent a callback key.
            skipped += 1
            continue
        if dst.get_by_name(name) is not None:
            skipped += 1
            continue
        try:
            trigger = rec.get("schedule") or _BUILTIN_NAME_TO_TRIGGER.get(name) or "daily@04:00"
            try:
                nxt = compute_next_fire(trigger, now, tz_name)
            except ValueError:
                errors += 1
                continue
            grace_min = rec.get("grace_window_minutes")
            grace_s = int(grace_min) * 60 if grace_min else None
            entry = ScheduleEntry(
                name=name,
                trigger=trigger,
                next_fire_at=nxt,
                recurring=True,
                callback_key=callback_key,
                payload={},
                enabled=bool(rec.get("enabled", 1)),
                grace_window_s=grace_s,
                origin="system",
                last_run_at=_to_iso(rec.get("last_run_at")),
                last_status=rec.get("last_status"),
                created_at=_to_iso(rec.get("created_at")) or "",
            )
            if dry_run:
                print(f"[dry-run] +system {name} trigger={trigger}")
            else:
                dst.upsert(entry)
            migrated += 1
        except Exception as exc:  # noqa: BLE001
            print(f"[error] builtin task {name!r}: {exc}", file=sys.stderr)
            errors += 1
    return (migrated, skipped, errors)


def migrate(
    *,
    recurring_db: Path,
    state_db: Path,
    target_db: Path,
    dry_run: bool = False,
    tz_name: str = "Europe/Rome",
    include_user: bool = True,
    include_builtin: bool = True,
) -> dict[str, int]:
    """Programmatic entry point. Returns counts dict."""
    target_db.parent.mkdir(parents=True, exist_ok=True)
    dst = SchedulerStorage(target_db)
    user_mig = user_skip = user_err = 0
    sys_mig = sys_skip = sys_err = 0
    try:
        if include_user:
            rec = _open_ro(recurring_db)
            if rec is not None:
                try:
                    user_mig, user_skip, user_err = _migrate_user_tasks(
                        rec, dst, dry_run=dry_run, tz_name=tz_name
                    )
                finally:
                    rec.close()
        if include_builtin:
            st = _open_ro(state_db)
            if st is not None:
                try:
                    sys_mig, sys_skip, sys_err = _migrate_builtin(
                        st, dst, dry_run=dry_run, tz_name=tz_name
                    )
                finally:
                    st.close()
    finally:
        dst.close()
    summary = {
        "migrated_user": user_mig,
        "migrated_builtin": sys_mig,
        "skipped": user_skip + sys_skip,
        "errors": user_err + sys_err,
    }
    return summary


def _cli(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Migrate v1 scheduler state to v2")
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import config as _C  # §7.11
    ap.add_argument(
        "--recurring-db",
        type=Path,
        default=_C.DB_RECURRING_TASKS,
    )
    # ADR 0148 rename-resilient: state.sqlite default derived from
    # PATH_WORKSPACE (this file lives at runtime/scheduler_v2/migrate_v1.py).
    _default_state_db = (Path(__file__).resolve().parents[2]
                         / "workspace" / ".scheduler" / "state.sqlite")
    ap.add_argument(
        "--state-db",
        type=Path,
        default=_default_state_db,
    )
    ap.add_argument(
        "--target-db",
        type=Path,
        default=_C.PATH_USER_STATE / "scheduler_v2.sqlite",
    )
    ap.add_argument("--tz", default="Europe/Rome")
    ap.add_argument("--dry-run", action="store_true")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--user-only", action="store_true",
                   help="Skip builtin (system) tasks; migrate user tasks only")
    g.add_argument("--builtin-only", action="store_true",
                   help="Skip user tasks; migrate builtin (system) tasks only")
    ns = ap.parse_args(argv)
    summary = migrate(
        recurring_db=ns.recurring_db,
        state_db=ns.state_db,
        target_db=ns.target_db,
        dry_run=ns.dry_run,
        tz_name=ns.tz,
        include_user=not ns.builtin_only,
        include_builtin=not ns.user_only,
    )
    print(
        f"migrated_user={summary['migrated_user']} "
        f"migrated_builtin={summary['migrated_builtin']} "
        f"skipped={summary['skipped']} errors={summary['errors']}"
    )
    return 0 if summary["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
