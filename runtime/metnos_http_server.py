"""metnos_http_server — HTTP API Phase 1.

Espone:
- /agent/health, /.well-known/metnos.json, /agent/turn, /agent/devices/me
- /admin (dashboard), /admin/changes (+actions, ADR 0158), /admin/executors
  (+stats), /admin/runs, /admin/safety, /admin/turns

Stile uniforme col `runtime.agent_server` (porta 8765 pairing): aiohttp
bare, niente decorator-routing, helper `_error`, ROUTES come tuple list,
middleware funzionali. Auth tramite admin key file + device pairing
token + bypass LAN trusted (vedi `http_auth.auth_middleware`).

Avvio standalone:
    python3 -m metnos_http_server [--host 0.0.0.0 --port 8770]
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# The daemon is the central execution-policy startup point. Operators can
# still lower or disable the pool explicitly; absent overrides, only signed
# and equivalence-verified executor classes are eligible.
os.environ.setdefault("METNOS_EXECUTOR_PARALLEL", "1")
os.environ.setdefault("METNOS_EXECUTOR_MAX_CLASS", "3")
# Conservative cross-step waves are independently switchable.  Admission is
# still read-only + signed class > 0 + verified equivalence, and any explicit
# deployment value (notably ``0`` for rollback) wins over this default.
os.environ.setdefault("METNOS_ENGINE_PARALLEL_STEPS", "1")

# Resolve once before importing routes/runtime workers. Child executors inherit
# the same capability profile and never need to inspect GPUs or LLM frameworks.
from llm_concurrency import initialize_environment as _init_llm_concurrency
_LLM_CONCURRENCY = _init_llm_concurrency()

from aiohttp import web

import http_async_tasks
import http_routes_admin
import http_routes_agent
import http_routes_stack
from http_auth import auth_middleware, get_or_create_admin_key
from http_app_state import (
    ADMIN_KEY, CATALOG_PROVIDER, SCHEDULER_V2, SSE_RESPONSES, STARTED_AT,
)
from logging_setup import get_logger

log = get_logger(__name__)
log.info(
    "llm_concurrency framework=%s gpu_count=%d batching=%s "
    "parallelism_class=%d max_in_flight=%d executor_parallel=%s "
    "engine_parallel_steps=%s",
    _LLM_CONCURRENCY.framework, _LLM_CONCURRENCY.gpu_count,
    _LLM_CONCURRENCY.batching, _LLM_CONCURRENCY.parallelism_class,
    _LLM_CONCURRENCY.max_in_flight,
    os.environ.get("METNOS_EXECUTOR_PARALLEL", "0"),
    os.environ.get("METNOS_ENGINE_PARALLEL_STEPS", "0"),
)

DEFAULT_HOST = os.environ.get("METNOS_HTTP_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("METNOS_HTTP_PORT", "8770"))
# Lockfile sotto PATH_USER_STATE (rispetta METNOS_USER_STATE per
# storage isolato test/e2e). Override esplicito via METNOS_HTTP_LOCKFILE.
from runtime import config as _C  # noqa: E402
LOCKFILE = Path(os.environ.get(
    "METNOS_HTTP_LOCKFILE",
    str(_C.PATH_USER_STATE / "http_server.lock"),
))


def _build_catalog_provider():
    """Carica il catalog una sola volta e ne fornisce un getter pigro.

    Il catalog viene caricato al primo access (non al boot) cosi' i test
    HTTP che non lo usano restano leggeri.
    """
    cache: dict = {}

    def get():
        if "catalog" not in cache:
            try:
                from loader import load_catalog
                cache["catalog"] = load_catalog()
            except Exception as e:
                log.warning("catalog load failed: %s", e)
                cache["catalog"] = []
        cat = cache["catalog"]
        if hasattr(cat, "executors"):
            return list(cat.executors.values())
        return list(cat)
    return get


# --- app factory -------------------------------------------------------------

def make_app(*, admin_key: str | None = None) -> web.Application:
    """Costruisce l'app aiohttp con middleware auth + routes registrate."""
    # ADR 0092: invariante "stesso set di file" enforced al boot. Boot fail
    # se una lingua secondaria (en/, xx/) ha un file mancante rispetto a it/.
    import prompt_loader
    prompt_loader.validate_invariant()

    # client_max_size: cap del body request. 50 MB per accomodare upload
    # multipart di foto reference (drag&drop ADR 0092). Foto JPEG/HEIC/PNG
    # tipiche 2-15 MB ognuna, max 10 file = ~100-150 MB worst case; 50 MB
    # copre il caso comune (≤ 5 foto media risoluzione).
    app = web.Application(client_max_size=50 * 1024 * 1024, middlewares=[auth_middleware])
    app[STARTED_AT] = time.time()
    app[ADMIN_KEY] = (
        admin_key if admin_key is not None else get_or_create_admin_key())
    app[CATALOG_PROVIDER] = _build_catalog_provider()

    for method, path, handler in (
        list(http_routes_agent.ROUTES)
        + list(http_routes_admin.ROUTES)
        + list(http_routes_stack.ROUTES)
    ):
        app.router.add_route(method, path, handler)

    # Set di SSE pending: usato da _turn_sse per registrarsi e da
    # close_active_sse per chiudere pulitamente al shutdown del server.
    app[SSE_RESPONSES] = set()
    app.on_shutdown.append(http_routes_agent.close_active_sse)

    # TurnEventLog: bind del loop asyncio per le notifiche cross-thread
    # (run_turn gira in executor, scrive eventi via call_soon_threadsafe).
    async def _bind_turn_events_loop(app):
        import asyncio as _a
        from turn_events import TurnEventLog
        TurnEventLog.get().bind_loop(_a.get_running_loop())
    app.on_startup.append(_bind_turn_events_loop)

    # ADR 0093: task async per build asincrona indici immagine.
    # 3 task: healthcheck stale + notification dispatch + tmp sweeper.
    # Cancellati on_shutdown; restartati al boot del daemon.
    if os.environ.get("METNOS_HTTP_DISABLE_BUILD_TASKS") != "1":
        http_async_tasks.register_async_tasks(app)

    # ADR 0112: scheduler v2 co-host. SchedulerDaemon vive come asyncio.Task
    # nel loop dell'HTTP server (no daemon standalone). Cutover staged:
    # in questa fase v2 gestisce SOLO i task user (la cui v1 era rotta per
    # il drift in-memory cross-process). I 5 builtin ager continuano via
    # metnos-scheduler.service v1 finche' PR7 non promuove tutto a v2.
    try:
        from scheduler_v2 import SchedulerDaemon, DEFAULT_DB_PATH
        from scheduler_v2 import builtin_callbacks as _bcb
        from scheduler_v2 import daemon_handle as _dh
        from scheduler_v2 import migrate_v1 as _mig
        # Migrazione user-only al primo boot. Idempotente: se i nomi sono
        # gia' in v2, skip. Best-effort: errori warning, non bloccano boot.
        try:
            import config as _C  # ADR 0148 rename-resilient
            import recurring_tasks as _recurring_tasks
            # The v1 registry remains the authority source for task mandates.
            # Upgrade it before scheduler_v2 reads or executes existing rows.
            _recurring_tasks.init_db()
            recurring_db = _C.DB_RECURRING_TASKS
            state_db = _C.DB_SCHEDULER
            summary = _mig.migrate(
                recurring_db=recurring_db, state_db=state_db,
                target_db=Path(DEFAULT_DB_PATH).expanduser(),
                include_user=True, include_builtin=True,
            )
            log.info("scheduler_v2 migrate: %s", summary)
        except Exception as ex:
            log.warning("scheduler_v2 migration at boot failed: %s", ex)
        sched = SchedulerDaemon(Path(DEFAULT_DB_PATH).expanduser())
        _bcb.install_default_callbacks(sched)
        # Idempotente: seed delle 7 entries di sistema se non gia' presenti
        # (post-migration di solito gia' ci sono; questo copre il caso DB pulito).
        try:
            _bcb.install_default_jobs(sched)
        except Exception as ex:
            log.warning("scheduler_v2 install_default_jobs: %s", ex)
        app[SCHEDULER_V2] = sched

        async def _start_sched(_app):
            await sched.start()
            _dh.set_active(sched)
            log.info("scheduler_v2 started, db=%s pool=%d",
                     sched.db_path, sched.pool_size)

        async def _stop_sched(_app):
            _dh.clear()
            await sched.stop()
            log.info("scheduler_v2 stopped")

        app.on_startup.append(_start_sched)
        app.on_shutdown.append(_stop_sched)
    except Exception as ex:
        log.warning("scheduler_v2 init failed (non-fatal): %s", ex)

    # PR4 (8/5/2026): upgrade incrementale schema indici v1 -> v2 al boot.
    # Async di default (non blocca boot); arricchisce entries esistenti
    # con i campi di `index_schema.ENRICHMENTS` mancanti, riusando
    # vectors.npy. Idempotente: se gia' a INDEX_SCHEMA_VERSION skip.
    try:
        import index_schema_upgrade as _isu
        _u_stats = _isu.upgrade_existing_indices_at_boot()
        log.info("index_schema_upgrade at boot: %s", _u_stats)
    except Exception as ex:
        log.warning("index_schema_upgrade at boot failed: %s", ex)

    # Sync pairings.db → users.user_channels: riconcilia bootstrap pair
    # (Telegram default_chat_id, /pair PAIR.<token>) coi binding identita'
    # multi-user (ADR 0083). Idempotente, deterministico (§7.9).
    # Senza questo, /admin/users/<id> mostra channels=[] anche per host
    # bootstrappato. Best-effort: errori loggano warning, non bloccano boot.
    try:
        import users_pairings_sync as _ups
        _stats = _ups.sync_pairings_to_user_channels()
        log.info("users_pairings_sync at boot: %s", _stats)
    except Exception as ex:
        log.warning("users_pairings_sync at boot failed: %s", ex)

    # Sync users.email → user_channels(channel="mail"): popola il pairing
    # email implicito dai dati anagrafici. Senza, send_messages(via_channel=
    # "mail") fallisce con channel_not_paired:email per utenti con email
    # nota in users ma assente da user_channels (14/5/2026). Idempotente §7.9.
    try:
        import users_email_sync as _ues
        _e_stats = _ues.sync_users_email_to_user_channels()
        log.info("users_email_sync at boot: %s", _e_stats)
    except Exception as ex:
        log.warning("users_email_sync at boot failed: %s", ex)

    # Active sessions schema (Phase 7 Phase 1, 12/5/2026): garantisce
    # `active_sessions` table in users.db. Idempotente.
    try:
        import active_sessions as _as
        _as.init_db()
        log.info("active_sessions schema ready")
    except Exception as ex:
        log.warning("active_sessions init at boot failed: %s", ex)

    return app


# TODO: per restart pulito aggiungere a /etc/systemd/system/metnos-http.service:
#   KillMode=mixed
#   TimeoutStopSec=5
# poi `sudo systemctl daemon-reload`. Combinato con on_shutdown handler sopra,
# il restart dara' 5s alle SSE attive per chiudersi prima di SIGKILL.


# --- single-instance lock ----------------------------------------------------

class ProcessLock:
    """Lockfile flock POSIX (single-instance gate)."""

    def __init__(self, path: Path, owner: str = "metnos_http_server"):
        self.path = path
        self.owner = owner
        self._fh = None

    def acquire(self) -> None:
        import fcntl
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a+")
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            self._fh.close()
            self._fh = None
            raise RuntimeError(
                f"{self.owner} gia' in esecuzione (lockfile {self.path})"
            ) from e
        self._fh.seek(0); self._fh.truncate()
        self._fh.write(str(os.getpid())); self._fh.flush()

    def release(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            except Exception as e:
                log.warning("lock release: %s", e)
            self._fh = None


# --- runners -----------------------------------------------------------------

async def _serve(host: str, port: int,
                 ready: asyncio.Event | None = None,
                 stop: asyncio.Event | None = None) -> None:
    app = make_app()
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    log.info("metnos_http_server listening on %s:%d", host, port)
    if ready is not None:
        ready.set()
    try:
        if stop is not None:
            await stop.wait()
        else:
            while True:
                await asyncio.sleep(3600)
    finally:
        await runner.cleanup()
        log.info("metnos_http_server stopped")


def run_standalone(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    logging.basicConfig(
        level=os.environ.get("METNOS_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    lock = ProcessLock(LOCKFILE)
    lock.acquire()
    try:
        asyncio.run(_serve(host, port))
    except KeyboardInterrupt:
        log.info("KeyboardInterrupt, shutting down")
    finally:
        lock.release()


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Metnos HTTP API server")
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = p.parse_args()
    run_standalone(args.host, args.port)
