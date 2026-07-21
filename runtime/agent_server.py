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

    # Join session (§5.3): se il token veniva da un flusso UI, avanza lo
    # stato a 'registered'. Best-effort: il register resta valido comunque.
    try:
        await loop.run_in_executor(
            None, lambda: devices.mark_join_registered_by_token(token, device.id))
    except Exception as _e:
        log.warning("join session non aggiornata su register: %s", _e)

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
# Questo loop è il wake-up della coda long-poll. Il precedente 0.5s
# aggiungeva fino a mezzo secondo a ogni executor remoto; il probe SQLite è
# read-only e può essere più frequente. Override per installazioni con molti
# device, con limiti per evitare busy-loop o configurazioni troppo lente.
try:
    POLL_CHECK_INTERVAL_S = min(1.0, max(
        0.05, float(os.environ.get("METNOS_AGENT_POLL_CHECK_S", "0.1"))))
except (TypeError, ValueError):
    POLL_CHECK_INTERVAL_S = 0.1
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
        # Log esplicito: il client ritenta con backoff e senza questa riga
        # il rifiuto e' invisibile lato server (diagnosi live 3/7: device
        # revocato che pollava nel silenzio totale).
        log.info("device %s respinto su %s: %s", device_id[:12], request.path,
                 "revocato" if device is not None else "sconosciuto")
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


_DEFER_RUN: set = set()


def _run_deferred_for_device(device_id: str) -> None:
    """A.1: ri-esegue i turni differiti del device appena torna a pollare.
    Best-effort, MAI blocca il poll (gira nel thread pool). Il re-run è un
    run_turn PIENO (planning fresco, gate, undo standard); esito → notice."""
    try:
        import deferred_turns as _dt
        import user_notices as _un
        from messages import get as _m
        for rec in _dt.pending_for_device(device_id):
            rid = rec.get("id")
            if not rid or rid in _DEFER_RUN:
                continue
            if rec.get("state") == "expired":
                _un.append(rec.get("channel") or "", rec.get("actor") or "host",
                           _m("MSG_DEFER_EXPIRED",
                              device=rec.get("device_name") or "?",
                              query=(rec.get("query") or "")[:80]))
                continue
            _DEFER_RUN.add(rid)
            _dt.mark(rid, "running")
            try:
                import agent_runtime as _ar
                nl = _ar.run_turn(
                    rec.get("query") or "",
                    actor=rec.get("actor") or "host",
                    channel=rec.get("channel") or "",
                    conversation_id=rec.get("conversation_id") or "")
                ok = bool(nl is not None
                          and getattr(nl, "final_kind", "") == "answer")
                _dt.mark(rid, "done" if ok else "failed")
                # turn:id NELLA notice (10/7, rilievo Roberto): il run
                # differito gira fuori-sessione — senza l'id l'utente non
                # può citarlo per segnalare un esito errato.
                _tid = (getattr(nl, "turn_id", "") or "")[:8]
                _outcome = (getattr(nl, "final_message", "") or "")[:200]
                if _tid:
                    _outcome = f"[turn:{_tid}] {_outcome}"
                _un.append(
                    rec.get("channel") or "", rec.get("actor") or "host",
                    _m("MSG_DEFER_DONE",
                       device=rec.get("device_name") or "?",
                       outcome=_outcome))
            except Exception as ex:
                _dt.mark(rid, "failed", note=repr(ex)[:200])
                log.warning("A.1 deferred %s fallito: %r", rid, ex)
            finally:
                _DEFER_RUN.discard(rid)
    except Exception as ex:  # noqa: BLE001 — mai rompere il poll
        log.warning("A.1 run_deferred noop: %r", ex)


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
    # Distingui la vita del PROCESSO dalla capacita' del WORKER. Il heartbeat
    # dedicato continua anche mentre un executor e' bloccato; solo un poll
    # prova che il loop sequenziale e' pronto a ricevere altro lavoro.
    await loop.run_in_executor(None, lambda: devices.poll_seen(device.id))
    # Fase 7 A.1: il device è TORNATO (sta pollando) → esegui i turni
    # DIFFERITI col suo consenso. Fire-and-forget nel thread pool; l'esito
    # arriva all'utente via user_notices (A.2). Dedup in-process (_DEFER_RUN).
    loop.run_in_executor(None, _run_deferred_for_device, device.id)

    deadline = loop.time() + block_ms / 1000.0
    while True:
        inv = await loop.run_in_executor(
            None, lambda: invocations.next_invocation(device.id, cursor=cursor))
        # shim_sha256 nell'ENVELOPE (content-addressing 6/7): metadato di
        # trasporto, NON nel payload firmato per-invocazione (i client 0.2.14
        # ricostruiscono i bytes firmati da lista fissa: un campo nuovo lì
        # invaliderebbe la firma). 0.2.14 ignora il campo (serde default).
        import shim_manifest as _shm
        if inv is not None:
            return web.json_response({
                "invocation": inv,
                "server_client_version": _server_client_version(),
                "shim_sha256": _shm.current_sha(),
            })
        if loop.time() >= deadline:
            return web.json_response({
                "invocation": None,
                "server_client_version": _server_client_version(),
                "shim_sha256": _shm.current_sha(),
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
        import shim_manifest
        # SoT dei sorgenti spostata in shim_manifest (content-addressing
        # 6/7): stessa lista usata per lo sha nel poll. Razionale storico dei
        # moduli inclusi (C7 CP1, chiusura ad albero, import lazy xlsx/google,
        # validazione segmenti wire) nel docstring di shim_manifest.
        sources = shim_manifest.shim_sources()
        files = {fname: base64.b64encode(p.read_bytes()).decode("ascii")
                 for fname, p in sources.items()}
        payload = {"files": files}
        return {"files": files, "sig": invocations.sign_payload(payload),
                # sha corrente del bundle: il client ≥0.2.15 lo persiste e lo
                # confronta con quello annunciato dal poll (re-pull su drift).
                "sha256": shim_manifest.current_sha()}

    try:
        bundle = await loop.run_in_executor(None, _load)
    except Exception:
        log.exception("shim bundle error")
        return _error(500, "internal_error", "shim bundle failed")
    return web.json_response(bundle)


async def client_update_descriptor(request: web.Request) -> web.Response:
    """GET /agent/client/update/{target} — descrittore di self-update FIRMATO.

    W4 (5/7/2026): il client confronta `server_client_version` dal poll con la
    propria; su mismatch chiama QUESTO endpoint, verifica la firma con la
    pubkey server PINNATA (stessa ancora di fiducia di shim/invocazioni — a
    differenza dello sha-solo-integrità di install.ps1) e scarica il binario
    dal mirror. Idempotenza lato client: sha del proprio exe == sha del
    descrittore ⇒ già aggiornato, nessun loop."""
    target = request.match_info.get("target") or ""
    if not re.fullmatch(r"[a-z0-9_\-]+", target):
        return _error(400, "bad_target", "target non valido")
    import json as _json
    p = agent_mirror.MIRROR_CLIENT_DIR / "manifest.json"
    if not p.is_file():
        return _error(404, "no_manifest", "mirror client assente")
    try:
        man = _json.loads(p.read_text())
        version = man.get("latest") or ""
        entry = ((man.get("versions") or {}).get(version) or {}).get(target)
    except Exception:
        return _error(500, "bad_manifest", "manifest illeggibile")
    if not version or not entry:
        return _error(404, "no_binary", f"nessun binario {target} in {version!r}")
    payload = {"version": version, "target": target,
               "sha256": entry.get("sha256") or ""}
    return web.json_response({
        **payload,
        "url_path": f"/agent/client/{version}/{target}/"
                    + ("metnos-client.exe" if "windows" in target
                       else "metnos-client"),
        "sig": invocations.sign_payload(payload),
    })


# --- join flow (§5.4-5.7 design doc: install-at-the-fly dalla UI) ----------
#
# La pagina join NON richiede auth admin: il segreto e' il join_id effimero,
# che punta a un token DEV. one-shot (TTL 30', DEFAULT_JOIN_TTL_S). La pagina
# marca 'opened',
# rileva l'OS dal browser, scarica l'installer PERSONALIZZATO (server+token
# baked) e segue lo stato fino a 'heartbeat'.

_JOIN_ID_RE = re.compile(r"^[0-9a-f]{8,32}$")

_JOIN_PAGE = """<!DOCTYPE html>
<html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Metnos — installa il client</title>
<style>
 body{font-family:system-ui,sans-serif;max-width:640px;margin:8vh auto;padding:0 20px;color:#1a2733}
 h1{font-size:1.4rem} .muted{color:#68788a;font-size:.92rem}
 .btn{display:inline-block;background:#1A477A;color:#fff;padding:14px 26px;border-radius:8px;
      font-size:1.05rem;text-decoration:none;margin:18px 0}
 ol.steps{list-style:none;padding:0} ol.steps li{padding:6px 0 6px 28px;position:relative;color:#68788a}
 ol.steps li::before{content:'○';position:absolute;left:4px}
 ol.steps li.done{color:#1a2733} ol.steps li.done::before{content:'●';color:#2e7d32}
 ol.steps li.now{color:#1a2733;font-weight:600} ol.steps li.now::before{content:'◐';color:#1A477A}
 .ok{color:#2e7d32;font-weight:600} .err{color:#b3261e;font-weight:600}
 code{background:#eef2f6;padding:2px 6px;border-radius:4px}
</style></head><body>
<h1>Installa Metnos Client su questo PC</h1>
<p class="muted">Device: <code>__DEVICE_NAME__</code> · il link scade insieme al
token (30 minuti dall'emissione).</p>
<div id="expired" class="err" style="display:none">Sessione scaduta: genera un
nuovo link dalla console <code>/admin/devices</code>.</div>
<div id="main">
  <p>Sistema rilevato: <strong id="os-label">…</strong></p>
  <a id="dl" class="btn" href="#">Scarica installer Metnos Client</a>
  <p class="muted" id="hint"></p>
  <ol class="steps" id="steps">
    <li data-s="created">link generato</li>
    <li data-s="opened">pagina aperta su questo PC</li>
    <li data-s="downloaded">installer scaricato — <em>aprilo per proseguire</em></li>
    <li data-s="registered">device registrato (chiave Ed25519)</li>
    <li data-s="heartbeat">client attivo — installazione completata</li>
  </ol>
  <p id="done" class="ok" style="display:none">Fatto: il device è appaiato e
  raggiungibile. Puoi chiudere questa pagina.</p>
</div>
<script>
(function () {
  var joinId = "__JOIN_ID__";
  var isWin = /Windows/i.test(navigator.userAgent);
  var platform = "__PLATFORM__";
  if (platform === "auto") platform = isWin ? "windows" : "linux";
  document.getElementById("os-label").textContent =
    platform === "windows" ? "Windows" : "Linux";
  document.getElementById("hint").textContent = platform === "windows"
    ? "Dopo il download: clicca sul file scaricato (barra dei download del " +
      "browser). Se Windows mostra un avviso di sicurezza, scegli «Esegui». " +
      "Si apre una finestra che mostra l'avanzamento e l'esito."
    : "Dopo il download: apri un terminale ed esegui  sh metnos-client-install.sh";
  var dlUrl = "/agent/client/join/" + joinId + "/installer?platform=" + platform;
  var dl = document.getElementById("dl");
  dl.href = dlUrl;
  var ORDER = ["created","opened","downloaded","registered","heartbeat"];
  function render(state) {
    if (state === "expired") {
      document.getElementById("expired").style.display = "block";
      document.getElementById("main").style.display = "none";
      return true;
    }
    var idx = ORDER.indexOf(state);
    var lis = document.querySelectorAll("#steps li");
    lis.forEach(function (li) {
      var i = ORDER.indexOf(li.getAttribute("data-s"));
      li.className = i < idx ? "done" : (i === idx ? "now" : "");
      if (i <= idx) li.classList.add("done");
      if (i === idx) li.classList.add("now");
    });
    if (state === "heartbeat") {
      document.getElementById("done").style.display = "block";
      return true;
    }
    return false;
  }
  render("__STATE__");
  // Auto-download dopo un breve delay (il browser puo' solo scaricare:
  // l'esecuzione resta un gesto manuale dell'utente).
  if ("__STATE__" === "created" || "__STATE__" === "opened") {
    setTimeout(function () { window.location.href = dlUrl; }, 1200);
  }
  var t = setInterval(function () {
    fetch("/agent/client/join/" + joinId + "/status")
      .then(function (r) { return r.json(); })
      .then(function (s) { if (render(s.state)) clearInterval(t); })
      .catch(function () {});
  }, 2000);
})();
</script></body></html>"""


def _join_session_or_none(join_id: str):
    if not _JOIN_ID_RE.match(join_id):
        return None
    return devices.get_join_session(join_id)


async def client_join_page(request: web.Request) -> web.Response:
    """GET /agent/client/join/{join_id} — pagina join sul PC target (§5.5)."""
    join_id = request.match_info["join_id"]
    loop = asyncio.get_running_loop()
    sess = await loop.run_in_executor(None, _join_session_or_none, join_id)
    if sess is None:
        return web.Response(
            text="<h1>404</h1><p>Sessione di join inesistente.</p>",
            status=404, content_type="text/html")
    hint = {"ip": request.remote,
            "user_agent": request.headers.get("User-Agent", "")[:300]}
    await loop.run_in_executor(
        None, lambda: devices.mark_join_state(join_id, "opened", client_hint=hint))
    state = "opened" if sess["state"] == "created" else sess["state"]
    # Escape SEMPRE (XSS): il nome e' slug-validato alla sorgente, ma la
    # pagina e' no-auth e i dati vengono dal DB — secondo strato qui.
    import html as _html
    platform = sess["platform"] if sess["platform"] in ("auto", "linux", "windows") else "auto"
    state = state if state in devices.JOIN_STATES or state == "expired" else "created"
    html = (_JOIN_PAGE
            .replace("__JOIN_ID__", join_id)
            .replace("__DEVICE_NAME__", _html.escape(sess["device_name"]))
            .replace("__PLATFORM__", platform)
            .replace("__STATE__", state))
    return web.Response(text=html, content_type="text/html",
                        headers={"Cache-Control": "no-store"})


async def client_join_status(request: web.Request) -> web.Response:
    """GET /agent/client/join/{join_id}/status — stato per pagina join e UI."""
    join_id = request.match_info["join_id"]
    loop = asyncio.get_running_loop()
    sess = await loop.run_in_executor(None, _join_session_or_none, join_id)
    if sess is None:
        return _error(404, "unknown_join", "join session not found")
    out = {
        "join_id": join_id,
        "state": sess["state"],
        "device_name": sess["device_name"],
        "platform": sess["platform"],
        "expires_at": sess["expires_at"],
    }
    if sess.get("device_id"):
        dev = await loop.run_in_executor(None, devices.get_device, sess["device_id"])
        if dev is not None:
            out["device"] = {
                "id": dev.id, "name": dev.name,
                "fingerprint": dev.public_key_fingerprint,
                "os_family": dev.os_family, "os_arch": dev.os_arch,
                "last_heartbeat": dev.last_heartbeat,
                "last_poll": dev.last_poll,
            }
    return web.json_response(out, headers={"Cache-Control": "no-store"})


def _sh_squote(s: str) -> str:
    """Quoting robusto per stringa dentro apici singoli POSIX: chiudi
    l'apice, inseriscine uno escapato, riapri. Sicuro anche se `s` contiene
    apici, spazi, `$`, `;` (server_url viene dall'header Host)."""
    return "'" + s.replace("'", "'\\''") + "'"


# --- installer Windows .cmd (§5.7, one-click) -------------------------------
#
# Un .ps1 scaricato NON si esegue col doppio click (Windows apre il selettore
# app — attrito osservato live 3/7: cartella → tasto destro → menu legacy →
# execution policy → finestra rossa che si chiude). Un .cmd invece SI esegue
# con un click dalla barra download del browser. Il file servito e' un
# poliglotta batch+PowerShell: testa batch ASCII (env baked + bootstrap),
# marker, poi install.ps1 INTATTO come coda — un solo file, un solo click,
# e la finestra resta APERTA con l'esito leggibile (pause), mai piu' un
# errore che sparisce.

_CMD_MARKER = "#::METNOS-PS1::#"

# Charset fail-closed per i valori baked nella testa batch: URL http,
# token DEV. (base64url + punti), versione semver, sha256 hex, nome
# tarball python-build-standalone (contiene `+`). `%`, `"`, `^`, `!`,
# spazi romperebbero il parsing cmd.exe o aprirebbero injection:
# meglio un 503 onesto che un installer malformato.
_CMD_SAFE_RE = re.compile(r"^[A-Za-z0-9._:/+\[\]\-]+$")


def _windows_python_runtime_pin() -> tuple[str, str | None] | None:
    """(nome_tarball, sha256|None) del python-build-standalone Windows piu'
    recente nel mirror (`MIRROR_RUNTIME_DIR`), o None se non ospitato. Il pin
    viene baked nell'installer: il client lo scarica robusto (chunk Range +
    consenso) da /agent/runtime/ alla prima invocazione (pyenv.rs) — senza
    pin gli executor falliscono onesti con «nessun interprete Python». Lo
    sha256 abilita la verifica end-to-end lato client; letto da un sidecar
    `<tarball>.sha256` se presente (evita di ri-hashare 46 MB a ogni
    installer), altrimenti calcolato e memoizzato su quel sidecar."""
    try:
        names = sorted(
            p.name for p in agent_mirror.MIRROR_RUNTIME_DIR.glob(
                "cpython-*-x86_64-pc-windows-msvc-install_only.tar.gz"))
    except OSError:
        return None
    if not names:
        return None
    tarball = names[-1]
    path = agent_mirror.MIRROR_RUNTIME_DIR / tarball
    side = path.with_suffix(path.suffix + ".sha256")
    sha: str | None = None
    try:
        if side.is_file():
            sha = side.read_text().split()[0].strip().lower() or None
        else:
            import hashlib
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for block in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(block)
            sha = h.hexdigest()
            try:
                side.write_text(sha + "\n")
            except OSError:
                pass  # memoizzazione best-effort
    except OSError:
        sha = None
    return tarball, sha


def _cmd_env_line(name: str, value: str) -> str:
    """Riga `set "NAME=value"` batch-safe; ValueError se il valore esce
    dal charset chiuso (mai emettere un .cmd col parsing compromesso)."""
    if not _CMD_SAFE_RE.match(value or ""):
        raise ValueError(f"valore non batch-safe per {name}: {value!r}")
    return f'set "{name}={value}"'


def _windows_cmd_installer(env: dict, ps_body: str) -> bytes:
    """Costruisce il .cmd poliglotta: testa batch (CRLF, solo ASCII) che
    estrae ed esegue la coda PowerShell dopo il marker. Il marker nella
    testa e' SPEZZATO ('#::METNOS'+'-PS1::#') cosi' IndexOf trova solo
    quello vero; il path del file passa via env (METNOS_SELF), mai
    interpolato nel comando: robusto a spazi/apostrofi nel percorso."""
    m_head, m_tail = _CMD_MARKER[:9], _CMD_MARKER[9:]
    boot = (
        'powershell -NoProfile -ExecutionPolicy Bypass -Command '
        f'"$m=\'{m_head}\'+\'{m_tail}\'; '
        '$t=[IO.File]::ReadAllText($env:METNOS_SELF); '
        '$i=$t.IndexOf($m); if ($i -lt 0) { exit 9 }; '
        'Invoke-Expression $t.Substring($i+$m.Length)"'
    )
    head = [
        "@echo off",
        "setlocal",
        "title Metnos Client Setup",
        "rem Installer Metnos: doppio click per eseguire. La finestra resta",
        "rem aperta a fine corsa con l'esito (mai un errore che sparisce).",
        *(_cmd_env_line(k, v) for k, v in env.items()),
        'set "METNOS_SELF=%~f0"',
        boot,
        'set "EC=%ERRORLEVEL%"',
        "echo.",
        'if "%EC%"=="0" (echo [OK] Installazione completata. Puoi chiudere '
        "questa finestra.) else (echo [ERRORE] Installazione NON riuscita: "
        "leggi il messaggio qui sopra.)",
        "pause",
        "exit /b %EC%",
        _CMD_MARKER,
    ]
    return ("\r\n".join(head) + "\r\n" + ps_body).encode("utf-8")


async def client_join_installer(request: web.Request) -> web.Response:
    """GET /agent/client/join/{join_id}/installer?platform=linux|windows —
    installer PERSONALIZZATO (§5.6/5.7): server URL + token baked, cosi' il
    file scaricato si esegue senza incollare variabili. Marca 'downloaded'."""
    join_id = request.match_info["join_id"]
    loop = asyncio.get_running_loop()
    sess = await loop.run_in_executor(None, _join_session_or_none, join_id)
    if sess is None:
        return _error(404, "unknown_join", "join session not found")
    if sess["state"] == "expired":
        return _error(410, "expired", "join session expired; generate a new link")

    platform = request.query.get("platform") or sess["platform"] or "auto"
    if platform == "auto":
        platform = "windows" if "Windows" in request.headers.get("User-Agent", "") else "linux"
    if platform not in ("linux", "windows"):
        return _error(400, "invalid_platform", "platform must be linux|windows")

    # Il target ha raggiunto QUESTO host:porta: e' l'URL server giusto per lui.
    host = request.headers.get("Host") or f"127.0.0.1:{DEFAULT_PORT}"
    server_url = sess.get("server_url") or f"http://{host}"
    if request.headers.get("Host"):
        server_url = f"http://{request.headers['Host']}"

    src = agent_mirror.MIRROR_CLIENT_DIR / ("install.ps1" if platform == "windows"
                                            else "install.sh")
    if not src.is_file():
        return _error(503, "installer_missing",
                      "installer non presente nel mirror (scripts/build-client.sh)")
    body = src.read_text(encoding="utf-8")
    token = sess["token"]
    if platform == "windows":
        # Windows non puo' verificare Ed25519 in PowerShell (niente supporto
        # CNG/.NET Framework): il pin di versione+sha256 DENTRO l'installer
        # personalizzato toglie la fiducia dal manifest scaricato — un MITM
        # deve manomettere QUESTO file, stesso livello del pin-pubkey Linux.
        # Nel flusso join il pin e' OBBLIGATORIO: se non generabile, fail-closed
        # 503 (mai un installer Windows senza pin che ricada sul manifest).
        try:
            m = json.loads(
                (agent_mirror.MIRROR_CLIENT_DIR / "manifest.json").read_text())
            entry = m["versions"][m["latest"]]["x86_64-pc-windows-gnu"]
            env = {
                "METNOS_SERVER": server_url,
                "METNOS_TOKEN": token,
                "METNOS_CLIENT_VERSION": m["latest"],
                "METNOS_CLIENT_SHA256": entry["sha256"],
            }
            runtime_pin = _windows_python_runtime_pin()
            if runtime_pin:
                tarball_name, runtime_sha = runtime_pin
                env["METNOS_PYTHON_RUNTIME_WIN"] = tarball_name
                if runtime_sha:
                    env["METNOS_PYTHON_RUNTIME_WIN_SHA256"] = runtime_sha
        except Exception as e:
            log.error("pin versione/sha256 non generabile per l'installer "
                      "windows (manifest mirror illeggibile): %s", e)
            return _error(503, "pin_unavailable",
                          "impossibile generare l'installer Windows con pin "
                          "sha256 (manifest mirror illeggibile); rigenera con "
                          "scripts/build-client.sh e riprova")
        # .cmd poliglotta: eseguibile con un click dal browser, install.ps1
        # intatto in coda (stesso corpo del one-liner manuale, §5.7).
        try:
            raw = _windows_cmd_installer(env, body)
        except ValueError as e:
            log.error("installer windows non generabile: %s", e)
            return _error(503, "unsafe_value", str(e))
        await loop.run_in_executor(
            None, lambda: devices.mark_join_state(join_id, "downloaded"))
        return web.Response(
            body=raw,
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Disposition":
                    'attachment; filename="MetnosClientSetup.cmd"',
                "Cache-Control": "no-store",
            })
    prelude = (f"METNOS_SERVER={_sh_squote(server_url)}; export METNOS_SERVER\n"
               f"METNOS_TOKEN={_sh_squote(token)}; export METNOS_TOKEN\n")
    lines = body.split("\n", 1)
    text = (lines[0] + "\n" + prelude + (lines[1] if len(lines) > 1 else "")
            ) if lines[0].startswith("#!") else prelude + body
    await loop.run_in_executor(
        None, lambda: devices.mark_join_state(join_id, "downloaded"))
    return web.Response(
        body=text.encode("utf-8"),
        headers={
            "Content-Type": "text/x-shellscript; charset=utf-8",
            "Content-Disposition":
                'attachment; filename="metnos-client-install.sh"',
            "Cache-Control": "no-store",
        })


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
    # Join flow (§5): PRIMA del mirror, che ha la route catch-all
    # /agent/client/{filename}.
    app.router.add_get("/agent/client/update/{target}", client_update_descriptor)
    app.router.add_get("/agent/client/join/{join_id}", client_join_page)
    app.router.add_get("/agent/client/join/{join_id}/status", client_join_status)
    app.router.add_get("/agent/client/join/{join_id}/installer", client_join_installer)
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
