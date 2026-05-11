# 0112 — Scheduler v2: asyncio-native, co-host HTTP, single table, materialized next_fire_at

Status: accepted, 2026-05-08

## Context

v1 scheduler (`runtime/scheduler.py`, 1112 LOC) had a critical architectural bug. The daemon's `tick()` iterated `self.tasks` (in-memory dict), never re-reading from SQLite. Eight call sites in `recurring_tasks.py` plus `admin/scheduler_cli.py` and `http_routes_admin.py` did `from scheduler import Scheduler; sched = Scheduler(); sched.register(...)`, which wrote to DB and updated *that* instance's dict, then died. The live daemon never noticed.

Surfaced 8/5/2026 morning: two user tasks at `daily@08:00`. Only `user_verifica_mail_importanti_mattutine` (registered 1/5, present in daemon's dict at its 7/5 boot) fired. `user_controllo_versione_amd_rocm` (registered 7/5 after daemon boot) had never fired despite living in the DB. The bug also explained why `proposals_cleanup` and `lifecycle_summary` (both added to source after daemon boot) had `last_run_at NULL`.

Other v1 deficits: serial fire (a 134s LLM-heavy task blocked the entire loop), `thread.join(timeout)` that doesn't actually cancel the worker (only the daemon's wait), 60 s polling regardless of when the next event was due, two separate SQLite DBs (`recurring_tasks.db` + `state.sqlite`) with overlapping data and divergent semantics.

## Decision

Rewrite as `runtime/scheduler_v2/` package, asyncio-native, **co-hosted in `metnos-http.service`** (single asyncio.Task in the aiohttp loop). No standalone daemon. No SIGHUP. No Unix socket.

### Concurrency model

- One `asyncio.Task` runs `_loop_main`. It `await asyncio.gather(*[fire(e) for e in due])` so N entries due at the same instant fire in parallel.
- Sync callbacks run on `ThreadPoolExecutor` via `loop.run_in_executor` (pool size `min(32, cpu*4)`). The 134s blocking call no longer freezes the loop.
- Async callbacks awaited directly. Both branches wrap in `asyncio.wait_for(timeout=entry.timeout_s)` for real cooperative cancellation.
- Per-entry re-entrancy lock honors `max_concurrent`.

### Data model

Single SQLite table `schedule_entries` replaces v1's separate jobs/timers split. Recurring jobs and one-shot timers are the same row, distinguished by `recurring: bool` and `remaining_runs: int`. Companion table `runs` for history with crash trail (`status='running'` rows updated on completion; stale `running` rows marked `crashed` at boot).

DB path: `~/.local/state/metnos/scheduler_v2.sqlite` (WAL, atomic writes).

### Algorithm: materialized `next_fire_at`

`next_fire_at` is a column. Computed once at registration (and after each fire) via `schedule_parser.next_fire_at(trigger, after_epoch, tz_name)`. The loop wakes at exactly `MIN(next_fire_at)` (capped at 60s as DST/safety). No fixed-tick polling. Apscheduler/Quartz/Sidekiq pattern.

For 1000 entries, the decision is one indexed query (`enabled=1, next_fire_at <= ?`), O(log N).

### Trigger grammar

- `daily@HH:MM` — local TZ (Europe/Rome by default). DST gap policy: zoneinfo `fold=0` (non-existent wall clock interpreted with pre-gap offset; round-trips to first existing wall clock after the gap).
- `every_Ns / Nm / Nh` — drift-allowing (re-anchored at fire, not fixed-rate).
- `at:<ISO8601>` — absolute UTC, one-shot.
- `cron:<5-field>` — optional, requires `croniter`. Tests `pytest.importorskip` so absence is non-blocking.

### Cross-process IPC: deliberately none

Every mutation (add/cancel/toggle/run-now) is a SQLite INSERT/UPDATE/DELETE via `scheduler_v2.client`. Same-process callers (any code running in the aiohttp event loop or its thread pool) call `daemon_handle.get_active().kick()` to wake the loop synchronously. Out-of-process callers (e.g. CLI) just write — the daemon picks up the change at the next loop iteration (max 60 s wait if it was sleeping idle). No SIGHUP, no socket protocol, no PID file dance, no race window between write and signal.

This is the inverse of the v1 bug: there is no "register on a dead instance" path, because no client ever holds a `Scheduler()` instance. There is exactly one daemon, in exactly one process.

### Migration

`runtime/scheduler_v2/migrate_v1.py` reads v1's `recurring_tasks.db` (user task definitions) and `state.sqlite` (last_run_at + builtin metadata), maps to v2 `schedule_entries` rows. Idempotent (UPSERT on name; skip if present). Flags `--user-only`/`--builtin-only` for staged cutover. Invoked automatically at HTTP boot in `make_app`.

For never-fired daily tasks the migration anchors `next_fire_at` computation at the start of the local day (not `now`), so a missed-target-today is recovered on first loop iteration via the in-grace branch of `_recover_missed`.

### Callback registry

Explicit registration in `runtime/scheduler_v2/builtin_callbacks.py::install_default_callbacks`. No pkgutil auto-discovery. Adding a callback = one line in this file + the implementation.

The 7 v1 task implementations (`task_apply_ager`, `task_apply_executor_ager`, `task_synt_suggest`, `task_introvertiva_propose`, `task_introvertiva_apply`, `task_proposals_cleanup`, `task_lifecycle_summary`) are loaded from `runtime/scheduler_v2/_v1_tasks.pyc` (bytecode preserved when `runtime/scheduler.py` was deleted) via `importlib.machinery.SourcelessFileLoader`. **Follow-up**: extract these to proper Python source under `runtime/jobs/<name>.py`. Tracked, not blocking.

## Consequences

- `runtime/scheduler.py` deleted (1112 LOC). `~/.config/systemd/user/metnos-scheduler.service` deleted. One fewer process to monitor.
- 245 lines of v1 test fixtures in `runtime/testing/populate_cases.py` deleted (the entire `("scheduler", ...)` block + `_sched_env_setup` helper + `CLUSTER_PYTHON_CASES_SCHEDULER`).
- `runtime/scheduler_v2/` is ~1500 LOC prod + ~1300 LOC tests for 115 passing tests.
- `runtime/observability.py` updated: `SCHEDULER_DB` repointed to v2 path, query reshaped for `schedule_entries` schema.
- `runtime/testing/seed_modules.py` updated: module entry `scheduler` → `scheduler_v2`, edges `(scheduler, mnestoma)` etc. renamed.
- v1's `state.sqlite` (`/opt/myclaw/workspace/.scheduler/state.sqlite`) left in place for forensic reference. Can be deleted by hand later.
- `recurring_tasks.db` retained — it's the user-task feature DB (label/query/actor/channel/chat_id/times/weekdays), distinct from the scheduler's runtime state. The dual-DB write at `handle_schedule_recurring` time is the documented pattern, not legacy.

## Rollout (8/5/2026)

- PR1+PR2: new `runtime/scheduler_v2/` package (parser, storage, models, callbacks, daemon, tests). Isolated, no production impact.
- PR3+PR4: builtin callbacks + migration tool. Adds `kick()` thread-safety (loop_id capture + `call_soon_threadsafe`).
- PR5: rewire `recurring_tasks.py` (8 call sites), `admin/scheduler_cli.py` (rewritten), `http_routes_admin.py` (`_summary_runs`/`admin_runs`).
- PR6: integrate `SchedulerDaemon` in `make_app` as `app["scheduler_v2"]`, lifecycle on `on_startup`/`on_shutdown`, daemon_handle `set_active`/`clear`. Migration runs at boot.
- PR7 (this PR): stop+disable+rm v1 systemd unit, delete `runtime/scheduler.py`, delete v1 test fixtures, update observability + seed_modules, write this ADR.

## Verified

- 115 pytest tests + 1 skipped (croniter optional) green throughout PR1→PR7.
- HTTP server lifecycle: `app["scheduler_v2"]` set, daemon `start()` invoked at startup, `stop()` at shutdown. Smoke test with `aiohttp.web.AppRunner`.
- Migration at boot: 11 entries (7 system + 2 user + 2 placeholder skipped), idempotent (re-run = 0 new).
- `user_controllo_versione_amd_rocm` (the task that "never fired" under v1, root cause of this rewrite) — first fire 8/5 07:54:07 UTC via v2 `run_now` + loop wake, completed 07:55:33 status=success duration=86s.
- Concurrent fire: 50 entries firing at the same `next_fire_at` complete within 2s in `test_concurrent_fire.py`.

## Future

- Extract `_v1_tasks.pyc` content to source modules in `runtime/jobs/`.
- Add admin endpoints `/admin/scheduler/*` for inspection/control via UI (currently CLI-only).
- Consider min-heap in-memory ordering when entry count exceeds ~1k (current: SQL+index, sufficient).
- Phase 7 multi-OS: scheduler stays single-host. Distributed scheduling requires leader election + fencing — separate ADR when needed.
