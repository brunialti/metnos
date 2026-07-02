"""runtime.agent_server — HTTP server per metnos-client (executor remoti).

Endpoint MVP:
- POST /agent/register   -> consuma token + registra device, ritorna device_id.
- GET  /agent/health     -> liveness probe.
- POST /agent/poll       -> long-poll: il client chiede la prossima invocazione.
- POST /agent/result     -> il client restituisce il risultato firmato.
- POST /agent/heartbeat  -> liveness device + profilo carico (placement L2).
- GET  /agent/executor/{name} -> bundle manifest+codice firmati (pull-on-miss).
- GET  /agent/shim       -> bundle firmato dei moduli runtime minimi
                            (executor_helpers, messages fallback) che gli
                            executor importano sul device.

Protocollo §6 di internal/design/remote-executors.html. Le richieste del
client sono firmate Ed25519 (header X-Metnos-Device-Sig sui bytes canonici
del body); le invocazioni sono firmate dal server (chiave 'author'). Firma
non valida = rifiuto + log, nessuna esecuzione (§12).

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
import json
import logging
import os
import re
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from aiohttp import web  # noqa: E402

import agent_mirror  # noqa: E402
import devices  # noqa: E402
import invocations  # noqa: E402
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
        # Pubkey del server: il client la pinna in state.json e con essa
        # verifica server_sig delle invocazioni + firma dei bundle (§6.2).
        "server_public_key": _server_public_key(),
    })


def _server_public_key() -> str | None:
    try:
        return invocations.server_public_key_b64()
    except Exception:
        log.warning("chiave server 'author' non disponibile: "
                    "le invocazioni non saranno firmabili")
        return None


def _error(status: int, code: str, message: str) -> web.Response:
    return web.json_response({"error": code, "message": message}, status=status)


# --- poll / result / heartbeat (protocollo §6) -----------------------------

POLL_BLOCK_MS_MAX = 30_000
POLL_CHECK_INTERVAL_S = 0.5
DEVICE_SIG_HEADER = "X-Metnos-Device-Sig"


async def _verified_device_body(request: web.Request):
    """Legge i bytes GREZZI del body, ne verifica la firma device (header
    X-Metnos-Device-Sig), e li parsa. Verifica sui bytes ESATTI ricevuti,
    non su una ri-serializzazione: contratto §6.3 (float-safe).

    Ritorna (device, body) oppure una web.Response di errore.
    """
    raw = await request.read()
    try:
        body = json.loads(raw)
    except Exception:
        return _error(400, "invalid_json", "request body must be JSON")
    if not isinstance(body, dict):
        return _error(400, "invalid_json", "request body must be a JSON object")
    device_id = body.get("device_id")
    if not isinstance(device_id, str):
        return _error(400, "missing_field", "device_id is required")
    sig = request.headers.get(DEVICE_SIG_HEADER, "")
    if not sig:
        return _error(401, "missing_signature",
                      f"{DEVICE_SIG_HEADER} header is required")
    loop = asyncio.get_running_loop()
    device = await loop.run_in_executor(None, devices.get_device, device_id)
    if device is None or device.revoked_at is not None:
        return _error(403, "unknown_device", "device not paired or revoked")
    if not invocations.verify_raw(device.public_key_b64, sig, raw):
        log.warning("firma device NON verificata per %s su %s: rifiuto",
                    device_id[:12], request.path)
        return _error(403, "bad_signature", "device signature not verified")
    return device, body


def _server_client_version() -> str | None:
    """Versione client corrente dal manifest del mirror (per self_update §5.5)."""
    try:
        import json as _json
        p = agent_mirror.MIRROR_CLIENT_DIR / "manifest.json"
        if p.is_file():
            return _json.loads(p.read_text()).get("latest")
    except Exception as _e:
        log.warning("client manifest illeggibile: %s", _e)
    return None


async def poll(request: web.Request) -> web.Response:
    """POST /agent/poll — long-poll §6.2.

    body: { device_id, cursor: <last-invocation-id-or-null>,
            capabilities: [...], block_ms: int }
    """
    out = await _verified_device_body(request)
    if isinstance(out, web.Response):
        return out
    device, body = out

    block_ms = body.get("block_ms")
    block_ms = min(int(block_ms), POLL_BLOCK_MS_MAX) if isinstance(block_ms, int) and block_ms > 0 else 0
    cursor = body.get("cursor") if isinstance(body.get("cursor"), str) else None

    loop = asyncio.get_running_loop()
    # Il poll e' anche liveness implicita: aggiorna last_heartbeat.
    await loop.run_in_executor(None, lambda: devices.heartbeat(device.id))

    deadline = loop.time() + block_ms / 1000.0
    while True:
        inv = await loop.run_in_executor(
            None, lambda: invocations.next_invocation(device.id, cursor=cursor))
        if inv is not None:
            return web.json_response({
                "invocation": inv,
                "server_client_version": _server_client_version(),
            })
        if loop.time() >= deadline:
            return web.json_response({
                "invocation": None,
                "server_client_version": _server_client_version(),
            })
        await asyncio.sleep(POLL_CHECK_INTERVAL_S)


async def result(request: web.Request) -> web.Response:
    """POST /agent/result — result firmato dal device §6.3.

    La firma (header X-Metnos-Device-Sig) copre i bytes ESATTI del body: la
    verifica e l'idempotenza (§6.4) vivono in complete_invocation, che riceve
    i bytes grezzi + la firma.
    """
    raw = await request.read()
    sig = request.headers.get(DEVICE_SIG_HEADER, "")
    if not sig:
        return _error(401, "missing_signature",
                      f"{DEVICE_SIG_HEADER} header is required")
    try:
        body = json.loads(raw)
    except Exception:
        return _error(400, "invalid_json", "request body must be JSON")
    if not isinstance(body, dict):
        return _error(400, "invalid_json", "request body must be a JSON object")
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(
            None, lambda: invocations.complete_invocation(
                body, raw_body=raw, sig_b64=sig))
    except invocations.SignatureError as e:
        return _error(403, "bad_signature", str(e))
    except invocations.InvocationError as e:
        return _error(400, "invalid_result", str(e))
    except Exception:
        log.exception("result error")
        return _error(500, "internal_error", "result processing failed")
    return web.json_response({"ok": True})


async def heartbeat(request: web.Request) -> web.Response:
    """POST /agent/heartbeat — liveness + profilo carico (placement §10 L2)."""
    out = await _verified_device_body(request)
    if isinstance(out, web.Response):
        return out
    device, body = out
    profile = body.get("profile") if isinstance(body.get("profile"), dict) else None
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None, lambda: devices.heartbeat(device.id, profile=profile))
    return web.json_response({"ok": True})


# --- executor + shim bundle (pull-on-miss §8) -------------------------------

_EXECUTOR_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{1,64}$")


async def executor_bundle(request: web.Request) -> web.Response:
    """GET /agent/executor/{name} — manifest+codice firmati per la cache device.

    Il server verifica l'executor PRIMA di servirlo (mai spedire un executor
    che il loader locale scarterebbe). Il client ri-verifica: firma manifest
    con la pubkey pinnata + digest sha256 del codice.
    """
    name = request.match_info["name"]
    if not _EXECUTOR_NAME_RE.match(name):
        return _error(400, "invalid_name", "invalid executor name")
    ex_dir = _C.PATH_EXECUTORS / name
    manifest_path = ex_dir / "manifest.toml"
    if not manifest_path.is_file():
        return _error(404, "unknown_executor", "executor not found")

    loop = asyncio.get_running_loop()

    def _load() -> dict:
        import base64
        import tomllib
        from sign import verify_executor
        ok, info = verify_executor(ex_dir)
        if not ok:
            raise RuntimeError(f"executor non verificato: {info.get('reason')}")
        manifest_bytes = manifest_path.read_bytes()
        sig_bytes = (ex_dir / "manifest.toml.sig").read_bytes()
        manifest = tomllib.loads(manifest_bytes.decode("utf-8"))
        files = {}
        for fname in manifest.get("code", {}).get("files", []):
            files[fname] = base64.b64encode((ex_dir / fname).read_bytes()).decode("ascii")
        return {
            "name": name,
            "manifest_toml": base64.b64encode(manifest_bytes).decode("ascii"),
            "manifest_sig": base64.b64encode(sig_bytes).decode("ascii"),
            "files": files,
        }

    try:
        bundle = await loop.run_in_executor(None, _load)
    except Exception as e:
        log.warning("executor bundle %s rifiutato: %s", name, e)
        return _error(409, "unverified_executor", "executor failed verification")
    return web.json_response(bundle)


async def shim_bundle(request: web.Request) -> web.Response:
    """GET /agent/shim — moduli runtime minimi per l'esecuzione sul device.

    Bundle: executor_helpers.py (il file REALE, zero drift) + messages.py
    (fallback senza DB i18n, da runtime/device_shim/). Firmato con la chiave
    server: il client verifica con la pubkey pinnata prima di scriverlo
    nella cache.
    """
    loop = asyncio.get_running_loop()

    def _load() -> dict:
        import base64
        runtime_dir = Path(__file__).resolve().parent
        sources = {
            "executor_helpers.py": runtime_dir / "executor_helpers.py",
            "messages.py": runtime_dir / "device_shim" / "messages.py",
        }
        files = {fname: base64.b64encode(p.read_bytes()).decode("ascii")
                 for fname, p in sources.items()}
        payload = {"files": files}
        return {"files": files, "sig": invocations.sign_payload(payload)}

    try:
        bundle = await loop.run_in_executor(None, _load)
    except Exception:
        log.exception("shim bundle error")
        return _error(500, "internal_error", "shim bundle failed")
    return web.json_response(bundle)


# --- app factory ----------------------------------------------------------

def make_app() -> web.Application:
    # 4 MB: gli entries di un result remoto possono essere corposi (§2.7 cap
    # a monte via max_total; qui e' solo il limite di trasporto).
    app = web.Application(client_max_size=4 * 1024 * 1024)
    app.router.add_get("/agent/health", health)
    app.router.add_post("/agent/register", register)
    app.router.add_post("/agent/poll", poll)
    app.router.add_post("/agent/result", result)
    app.router.add_post("/agent/heartbeat", heartbeat)
    app.router.add_get("/agent/executor/{name}", executor_bundle)
    app.router.add_get("/agent/shim", shim_bundle)
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
