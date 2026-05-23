"""runtime.agent_server — HTTP server per metnos-client (executor remoti).

Endpoint MVP:
- POST /agent/register   -> consuma token + registra device, ritorna device_id.
- GET  /agent/health     -> liveness probe.

Tutti gli endpoint sono progettati a prova di doppia invocazione (idempotenza
delegata a devices.py, che usa SQLite con UNIQUE/atomic transactions). Il
server e' un componente passivo: rispende e basta, niente logica di iniziativa.

Modello di esecuzione:
- Funzione `run_in_thread(host, port)` lancia un asyncio loop in un thread
  daemon, in modo da convivere con il main loop sync del daemon Telegram.
- Funzione `run_standalone()` per dev: lancia il server come processo a se'.
- Lockfile a livello processo per evitare doppie istanze (single-instance gate).

Sicurezza MVP:
- Nessun TLS in questa stesura: bind 127.0.0.1 di default, e si aggiunge
  TLS in fase successiva (cert self-signed pin-by-fingerprint).
- Validazione input rigorosa, error responses generiche (no info leak).
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from aiohttp import web  # noqa: E402

import agent_mirror  # noqa: E402
import devices  # noqa: E402
import config as _C  # noqa: E402 — §7.11

from logging_setup import get_logger
log = get_logger(__name__)

log = logging.getLogger("metnos.agent_server")

DEFAULT_HOST = os.environ.get("METNOS_AGENT_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("METNOS_AGENT_PORT", "8765"))
LOCKFILE = Path(os.environ.get(
    "METNOS_AGENT_LOCKFILE",
    str(_C.PATH_USER_STATE / "agent_server.lock"),
))


# --- handlers -------------------------------------------------------------

async def health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def register(request: web.Request) -> web.Response:
    """POST /agent/register
    body JSON: { "token": "DEV.<...>.<...>",
                 "public_key": "<base64url ed25519 pub>",
                 "os_family": "linux"|"windows"|"macos" (optional),
                 "os_arch": "x86_64"|"aarch64"|... (optional) }
    """
    try:
        body = await request.json()
    except Exception:
        return _error(400, "invalid_json", "request body must be JSON")

    token = body.get("token")
    pub = body.get("public_key")
    if not isinstance(token, str) or not isinstance(pub, str):
        return _error(400, "missing_field", "token and public_key are required strings")

    os_family = body.get("os_family") if isinstance(body.get("os_family"), str) else None
    os_arch = body.get("os_arch") if isinstance(body.get("os_arch"), str) else None

    try:
        # consume_token e' sync su SQLite (operazione breve). Lo eseguiamo in
        # executor per non bloccare il loop asincrono.
        loop = asyncio.get_running_loop()
        device = await loop.run_in_executor(
            None,
            lambda: devices.consume_token(
                token, pub, os_family=os_family, os_arch=os_arch
            ),
        )
    except devices.ConsumedError:
        return _error(409, "token_already_used", "token already consumed by another key")
    except devices.TokenError as e:
        return _error(400, "invalid_token", str(e))
    except Exception:
        log.exception("register error")
        return _error(500, "internal_error", "registration failed")

    return web.json_response({
        "device_id": device.id,
        "name": device.name,
        "owner_user_id": device.owner_user_id,
        "fingerprint": device.public_key_fingerprint,
        "paired_at": device.paired_at,
    })


def _error(status: int, code: str, message: str) -> web.Response:
    return web.json_response({"error": code, "message": message}, status=status)


# --- app factory ----------------------------------------------------------

def make_app() -> web.Application:
    app = web.Application(client_max_size=64 * 1024)
    app.router.add_get("/agent/health", health)
    app.router.add_post("/agent/register", register)
    agent_mirror.register_routes(app)
    return app


# --- single-instance lock --------------------------------------------------

class ProcessLock:
    """Lockfile basato su flock (POSIX). Evita doppie istanze."""

    def __init__(self, path: Path, owner: str = "metnos"):
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
                f"{self.owner} gia' in esecuzione (lockfile {self.path}); "
                f"se sicuro che non lo sia, rimuovi il file."
            ) from e
        self._fh.seek(0)
        self._fh.truncate()
        self._fh.write(str(os.getpid()))
        self._fh.flush()

    def release(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            except Exception as _e:  # silent swallow (auto-fixed)
                log.warning("silent exception in %s: %s", __name__, _e)
            self._fh = None


# --- standalone runner ----------------------------------------------------

async def _serve(host: str, port: int, ready: asyncio.Event | None = None,
                 stop: asyncio.Event | None = None) -> None:
    app = make_app()
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    log.info("agent_server listening on %s:%d", host, port)
    if ready is not None:
        ready.set()
    try:
        if stop is not None:
            await stop.wait()
        else:
            # idle forever
            while True:
                await asyncio.sleep(3600)
    finally:
        await runner.cleanup()
        log.info("agent_server stopped")


def run_standalone(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    logging.basicConfig(
        level=os.environ.get("METNOS_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    lock = ProcessLock(LOCKFILE, owner="agent_server")
    lock.acquire()
    try:
        asyncio.run(_serve(host, port))
    except KeyboardInterrupt:
        log.info("KeyboardInterrupt, shutting down")
    finally:
        lock.release()


# --- in-thread runner (per integrazione nel daemon Telegram) -------------

class AgentServerThread:
    """Esecuzione del server in un thread daemon con event loop dedicato.

    Pensato per convivere col main loop sync del daemon Telegram. Il thread
    e' daemon=True: muore con il processo. Per stop pulito, chiamare stop().

    Auto-resume: se il loop interno crasha per eccezione non gestita,
    `_run_with_supervision` lo rilancia con backoff. Il main thread rimane
    inalterato.
    """

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
        self.host = host
        self.port = port
        self._thread: threading.Thread | None = None
        self._stop_flag = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_evt: asyncio.Event | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run_with_supervision, name="agent-server", daemon=True,
        )
        self._thread.start()

    def _run_with_supervision(self) -> None:
        backoff = 1.0
        while not self._stop_flag.is_set():
            try:
                self._run_once()
                # uscita pulita: stop voluto
                return
            except Exception:
                log.exception("agent_server thread crashed; restarting in %.1fs", backoff)
                if self._stop_flag.wait(backoff):
                    return
                backoff = min(backoff * 2, 30.0)

    def _run_once(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        try:
            self._stop_evt = asyncio.Event()
            ready = asyncio.Event()
            task = loop.create_task(
                _serve(self.host, self.port, ready=ready, stop=self._stop_evt)
            )
            loop.run_until_complete(task)
        finally:
            try:
                loop.close()
            finally:
                self._loop = None
                self._stop_evt = None

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_flag.set()
        loop = self._loop
        evt = self._stop_evt
        if loop is not None and evt is not None and loop.is_running():
            loop.call_soon_threadsafe(evt.set)
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Metnos agent_server")
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = p.parse_args()
    run_standalone(args.host, args.port)
