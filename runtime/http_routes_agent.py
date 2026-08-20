"""http_routes_agent — endpoint /agent/* per la HTTP API.

- GET  /agent/health           liveness (anonymous)
- GET  /.well-known/metnos.json discovery (anonymous)
- POST /agent/turn             pianificatore (user/admin); JSON o SSE
- GET  /agent/devices/me       info device chiamante (user)
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import os
import re
import socket
import time
import urllib.parse
from pathlib import Path

from aiohttp import web

import devices
import config as _C  # §7.11
import i18n as _i18n
from html_sanitizer import to_safe_html_full
from http_render import _error, render_template
from http_app_state import (
    ADMIN_KEY as APP_ADMIN_KEY, CATALOG_PROVIDER, SSE_RESPONSES, STARTED_AT,
    TURN_POOL, TUTOR_GATE, app_get, app_setdefault,
)
from http_turn_pool import TurnPoolBusy
from logging_setup import get_logger
from messages import get as _msg  # §11 i18n
from credential_intake import scrub_sensitive_text
from tutor_boundary import (
    answer as _tutor_boundary_answer,
    http_principal as _tutor_http_principal,
    unavailable_answer as _tutor_unavailable_answer,
)

log = get_logger(__name__)

VERSION = "1.1"  # versione dell'HTTP API (ADR 0078), DISTINTA dalla product version


class TutorBoundaryBusy(RuntimeError):
    """The bounded HTTP Tutor admission gate has no free worker."""


def _tutor_http_limit() -> int:
    try:
        return min(8, max(1, int(os.environ.get(
            "METNOS_TUTOR_HTTP_CONCURRENCY", "2"))))
    except (TypeError, ValueError):
        return 2


def _turn_pool(request: web.Request):
    """Return the central pool; legacy minimal test apps may omit it."""
    return app_get(request.app, TURN_POOL)


def _turn_busy_response() -> web.Response:
    return web.json_response(
        {"error": "turn_capacity_exhausted", "retry_after_s": 1},
        status=503,
        headers={"Retry-After": "1"},
    )


async def _reserve_turn(request: web.Request, actor: str):
    pool = _turn_pool(request)
    if pool is None:
        return None
    return await pool.reserve(actor)


async def _run_turn_reserved(request: web.Request, reservation, function):
    pool = _turn_pool(request)
    if pool is None:
        # `run_in_executor` non propaga automaticamente i ContextVar: senza
        # questo passaggio una app minimale o un test perderebbe la lingua
        # dell'utente proprio nel thread che genera la risposta.
        import contextvars
        context = contextvars.copy_context()
        return await asyncio.get_running_loop().run_in_executor(
            None, context.run, function,
        )
    return await pool.run_reserved(reservation, function)

# Product version (SemVer) — sorgente UNICA runtime/__version__.py (axis versioning).
try:
    from __version__ import version_info as _metnos_version_info
except Exception:  # pragma: no cover — fallback difensivo
    def _metnos_version_info():
        return {"metnos_version": "0.0.0", "ai_backend_api": 0}

# SSE keepalive: ogni N secondi il server emette un comment SSE (": keepalive\n\n")
# sulla connessione attiva. Comment = riga che inizia con `:` → il browser
# (EventSource standard E il parser custom in chat.html, regex
# `^event:\s*(\w+)\s*\n+data:\s*(.+)$`) lo ignora a livello di evento, ma il
# byte arriva a livello TCP e:
#   (a) tiene viva la connessione attraverso proxy/firewall idle-timeout,
#   (b) consente al watchdog client-side di resettare il timer "ultimo byte
#       ricevuto" e capire che il server e' ancora vivo.
# Esposto come modulo-level constant per i test (override via monkeypatch).
SSE_KEEPALIVE_INTERVAL_S = 8.0

# Capability corta per aprire un dialogo da un browser/device differente da
# quello che ha originato il turno. Non sostituisce l'owner binding: concede
# solo l'accesso a quel dialog_id e scade rapidamente.
_DIALOG_CAP_TTL_S = 15 * 60
_DIALOG_FORM_MARKER_RE = re.compile(
    r"INLINE_FORM:(/agent/dialog/([A-Za-z0-9][A-Za-z0-9_.-]{0,127})/form)"
    r"(?!\?cap=)"
)


def _safe_final_html(md: str | None) -> str:
    """Conversione markdown -> HTML completo (browser) per il campo
    `final_message_html` della HTTP API (ADR 0110).

    Il canale HTTP e' un browser e supporta HTML pieno: usa
    `to_safe_html_full` che rende `<table>` veri, heading `<hN>`, liste
    `<ul>/<ol>`, blockquote, hr — invece del subset Telegram. Telegram
    pipeline (`channels/telegram_format.format_for_telegram`) continua
    ad usare `to_safe_html` (subset compatibile parse_mode=HTML).

    Sicurezza: HTML escape iniziale di `<`, `>`, `&`; whitelist tag
    deliberatamente piccola (b, strong, i, em, u, code, pre, a, h1..h6,
    ul, ol, li, blockquote, hr, table, thead, tbody, tr, th, td, p, br).

    Fallback non-silente (§2.8): su eccezione interna log warning e
    ritorna l'input HTML-escaped al massimo (mai HTML iniettabile dal
    contenuto utente).
    """
    if not md:
        return ""
    try:
        return to_safe_html_full(md)
    except Exception:
        log.warning("to_safe_html_full failed", exc_info=True)
        try:
            import html as _h
            return _h.escape(md, quote=False)
        except Exception:
            return ""


# --- SSE keepalive + shutdown handler ---------------------------------------

async def _sse_keepalive_loop(response: web.StreamResponse) -> None:
    """Emette un comment SSE `: keepalive\\n\\n` ogni SSE_KEEPALIVE_INTERVAL_S
    secondi finche' non viene cancellato. Esce silenziosamente se la
    connessione e' chiusa (ConnectionError) o se il task viene cancellato.
    """
    try:
        while True:
            await asyncio.sleep(SSE_KEEPALIVE_INTERVAL_S)
            try:
                await response.write(b": keepalive\n\n")
            except (ConnectionError, ConnectionResetError, RuntimeError):
                # client disconnected o response gia' chiusa
                return
    except asyncio.CancelledError:
        return


async def close_active_sse(app: web.Application) -> None:
    """on_shutdown handler: chiude pulitamente le SSE attive registrate
    nell'app. Permette al client di vedere `done` invece di un reset TCP
    secco quando il daemon viene terminato (SIGTERM via systemctl restart).
    """
    sse_set = app_get(app, SSE_RESPONSES) or set()
    log.info("close_active_sse: %d connessioni SSE da chiudere", len(sse_set))
    for r in list(sse_set):
        try:
            await r.write_eof()
        except Exception as e:
            log.debug("close_active_sse: write_eof fallito: %s", e)
    sse_set.clear()


# --- Discovery ---------------------------------------------------------------

async def chat_root(request: web.Request) -> web.Response:
    """GET / — UI di chat (alternativa a Telegram).

    Accessibile a user/admin. Anonymous viene dirottato al login admin; la
    prossimità di rete, da sola, non identifica mai l'utente host.
    """
    role = request.get("role", "anonymous")
    if role == "anonymous":
        raise web.HTTPFound("/admin/login")
    # Anche lo storage del browser deve rispettare lo stesso principal del
    # server. Un hash stabile evita di esporre l'identificatore del registro
    # nel markup e impedisce che token, buffer comandi o puntatore alla
    # conversazione vengano riusati quando lo stesso profilo browser accede
    # come un altro utente.
    user_id = await _resolve_session_user_id(request)
    chat_user_scope = hashlib.sha256(
        f"metnos-chat-storage-v1:{user_id}".encode("utf-8")
    ).hexdigest()[:24]
    migrate_legacy_storage = role == "admin"
    try:
        import users as _users
        current_user = _users.get_user(user_id)
        migrate_legacy_storage = bool(
            current_user and current_user.get("role") == "host"
        )
    except Exception:
        # Un errore dello store non deve allargare la migrazione legacy a un
        # guest. L'admin autenticato resta il solo fallback compatibile con
        # la vecchia installazione single-user.
        migrate_legacy_storage = role == "admin"
    executor_intelligence = {}
    try:
        provider = app_get(request.app, CATALOG_PROVIDER)
        catalog = provider() if callable(provider) else []
        executor_intelligence = {
            ex.name: getattr(ex, "intelligence", "deterministic")
            for ex in catalog
        }
    except Exception as ex:
        log.warning("chat executor metadata unavailable: %s", ex)
    html = render_template(
        "chat.html", role=role, ui_lang=_i18n.current_lang(),
        chat_user_scope=chat_user_scope,
        chat_migrate_legacy_storage=migrate_legacy_storage,
        executor_intelligence=executor_intelligence,
    )
    return web.Response(
        text=html,
        content_type="text/html",
        # No-cache aggressivo: chat.html viene aggiornato spesso lato server
        # e il browser deve sempre prenderlo fresco — altrimenti vediamo
        # bug fantasma (es. "vecchio progress prompt sovrascrive i badge")
        # quando l'utente ha una versione cache.
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


async def health(request: web.Request) -> web.Response:
    """GET /agent/health"""
    started = app_get(request.app, STARTED_AT, time.time())
    return web.json_response(
        {"ok": True, "version": VERSION, "uptime_s": round(time.time() - started, 1),
         **_metnos_version_info()}
    )


async def well_known(request: web.Request) -> web.Response:
    """GET /.well-known/metnos.json — descrittore pubblico del nodo."""
    return web.json_response({
        "name": "metnos",
        "version": VERSION,
        **_metnos_version_info(),
        "channels": ["telegram", "http"],
        "capabilities": ["agent.turn", "admin.proposals", "admin.executors",
                         "admin.runs", "admin.safety", "admin.turns"],
        "pairing_url": "/agent/register",
    })


async def device_register(request: web.Request) -> web.Response:
    """Pair a remote device on the same public endpoint we advertise."""
    # Registration logic and one-time-token semantics retain a single SoT in
    # agent_server; the 8770 API merely exposes the same handler.
    import agent_server
    return await agent_server.register(request)


async def device_self(request: web.Request) -> web.Response:
    """GET /agent/devices/me"""
    device_id = request.get("device_id")
    if not device_id:
        # Caller LAN-trusted senza pairing: ritorna un descrittore minimale.
        return web.json_response({
            "device_id": None,
            "role": request.get("role", "anonymous"),
            "remote": request.remote,
        })
    d = devices.get_device(device_id)
    if d is None:
        return _error(404, "device_not_found", "device record missing")
    # Aggancio identificazione (predisposizione multi-utente): da owner_user_id
    # → utente reale del registro. Base per derivarne profili di sicurezza.
    _owner = devices.owner_user(d.owner_user_id)
    return web.json_response({
        "device_id": d.id,
        "name": d.name,
        "owner_user_id": d.owner_user_id,
        "owner": {"id": _owner["id"], "name": _owner["name"],
                  "display_name": _owner.get("display_name"),
                  "role": _owner.get("role"),
                  "autonomy_level": _owner.get("autonomy_level")}
                 if _owner else None,
        "fingerprint": d.public_key_fingerprint,
        "os_family": d.os_family,
        "os_arch": d.os_arch,
        "paired_at": d.paired_at,
        "last_heartbeat": d.last_heartbeat,
        "last_poll": d.last_poll,
    })


# --- /agent/turn -------------------------------------------------------------

class _SSEProgress:
    """Adapter Progress → eventi SSE.

    Implementa l'interfaccia di runtime.progress.Progress in modo
    canale-agnostico: ogni call schedula un evento sul loop asincrono.
    """

    def __init__(self, response: web.StreamResponse, loop: asyncio.AbstractEventLoop):
        self.response = response
        self.loop = loop

    def _emit_threadsafe(self, kind: str, payload: dict) -> None:
        coro = self._emit(kind, payload)
        try:
            asyncio.run_coroutine_threadsafe(coro, self.loop)
        except Exception as e:
            log.debug("sse emit failed: %s", e)

    async def _emit(self, kind: str, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str)
        chunk = f"event: {kind}\ndata: {body}\n\n".encode("utf-8")
        try:
            await self.response.write(chunk)
        except Exception as e:
            log.debug("sse write failed: %s", e)

    # Progress interface (sync, called from runtime thread).
    def start(self, header: str) -> None:
        self._emit_threadsafe("thinking", {"message": header})

    def update(self, stage: int, label: str | None = None) -> None:
        self._emit_threadsafe("progress", {"stage": stage, "label": label or ""})

    def update_free(self, label: str) -> None:
        self._emit_threadsafe("progress", {"label": label})

    def tool_call(self, tool: str, step_num: int,
                   path_so_far: list[str] | None = None,
                   args: dict | None = None,
                   predicted_remaining: list[str] | None = None) -> None:
        """Emette un evento `tool_call` con il path eseguito finora + il
        tool corrente + previsione step futuri. La chat HTML lo usa per
        disegnare il breadcrumb live (badge crescenti, corrente pulsante,
        futuri tratteggiati e muti).
        """
        self._emit_threadsafe("tool_call", {
            "tool": tool,
            "step_num": step_num,
            "path": list(path_so_far or []),
            "predicted_remaining": list(predicted_remaining or []),
            "args": args or {},
        })

    def finish(self, message: str) -> None:
        self._emit_threadsafe("final", {"message": message})


def _resolve_actor(request: web.Request, body: dict) -> str:
    """Resolve an actor without turning an unbound user into the host."""
    role = request.get("role", "anonymous")
    if role == "admin" and isinstance(body.get("actor"), str) and body["actor"]:
        return body["actor"]
    if role == "admin":
        return "host"
    return (request.get("device_id") or request.get("lan_principal")
            or "anonymous")


def _http_sender_id(principal_id: str, conv_id: str) -> str:
    """Chiave stabile per pending state HTTP.

    `principal_id` e' normalmente l'user_id logico, non il device actor:
    dialoghi e consensi seguono cosi' la conversazione durante il transfer.
    Un override actor esplicito dell'admin conserva invece il proprio scope.
    """
    return f"http:{principal_id}:{conv_id or '_'}"


def _http_has_pending(sender_id: str, actor: str,
                      owner_user_id: str) -> bool:
    """Read-only pending probe used only to annotate a tutor answer."""
    try:
        from dialog_pending import list_pending
        if (list_pending(sender_id, owner_user_id=owner_user_id)
                or list_pending(
                    f"http:{actor}", owner_user_id=owner_user_id)):
            return True
    except Exception:
        pass
    try:
        from channels.daemon import _cap_pending_load
        return bool(_cap_pending_load(
            sender_id, owner_user_id=owner_user_id))
    except Exception:
        return False


async def _apply_tutor_http(request: web.Request, *, query: str, actor: str,
                            user_id: str, conversation_id: str,
                            sender_id: str, turn_id_hint: str = ""):
    """Pure-help escape before pending consumers; never consumes state."""
    has_pending = _http_has_pending(sender_id, actor, user_id)
    app = getattr(request, "app", None)
    gate = (
        app_setdefault(app, TUTOR_GATE,
                       asyncio.Semaphore(_tutor_http_limit()))
        if app is not None else None
    )
    acquired = False
    worker = None
    try:
        if gate is not None:
            try:
                await asyncio.wait_for(gate.acquire(), timeout=0.05)
                acquired = True
            except asyncio.TimeoutError as exc:
                raise TutorBoundaryBusy("Tutor capacity exhausted") from exc
        from tutor.deadline import request_budget_s
        principal = _tutor_http_principal(
            role=str(request.get("role", "anonymous")),
            device_id=request.get("device_id"),
            actor=actor, user_id=user_id,
            conversation_id=conversation_id,
        )
        allow_handoff = str(request.get("role", "anonymous")) == "admin"
        if not allow_handoff and request.get("lan_principal"):
            # A trusted-LAN principal is intentionally synthetic and owns an
            # empty isolated scope.  It may receive grounded read-only help,
            # but it has no users.db authority from which to create a handoff.
            allow_handoff = False
        elif not allow_handoff:
            import users as _tutor_users
            owner = _tutor_users.get_user(user_id)
            if owner is None:
                raise RuntimeError("Tutor owner unavailable")
            allow_handoff = str(
                owner.get("autonomy_level") or "").lower() not in {
                    "read_only", "readonly",
                }
        worker = asyncio.create_task(asyncio.to_thread(
                _tutor_boundary_answer,
                query,
                principal,
                has_pending=has_pending,
                pending_sender_id=sender_id if allow_handoff else "",
                turn_id_hint=turn_id_hint,
            ))
        return await asyncio.wait_for(
            asyncio.shield(worker),
            # Safety net only. The same, slightly smaller monotonic budget is
            # propagated through scheduler and provider so the worker itself
            # releases resources before this outer bound fires.
            timeout=request_budget_s() + 2.0,
        )
    except TutorBoundaryBusy:
        raise
    except asyncio.TimeoutError:
        log.error("HTTP Tutor exceeded its propagated deadline")
        return _tutor_unavailable_answer(has_pending=has_pending)
    except Exception:
        log.warning("HTTP tutor boundary failed", exc_info=True)
        return _tutor_unavailable_answer(has_pending=has_pending)
    finally:
        if acquired:
            if worker is not None and not worker.done():
                worker.add_done_callback(lambda _done, _gate=gate: _gate.release())
            else:
                gate.release()


def _turn_id_for_preprocessed(data: dict) -> str:
    """Keep an already persisted Tutor identifier across the async boundary."""

    import uuid

    candidate = str(data.get("immediate_turn_id") or "")
    if (data.get("immediate_source") == "tutor"
            and re.fullmatch(r"[0-9a-f]{16}", candidate)):
        return candidate
    return uuid.uuid4().hex[:16]


def _persist_pending_http_turn(*, turn_id: str, query: str, message: str,
                               actor: str, owner_user_id: str,
                               conversation_id: str,
                               redacted_fields: int = 0) -> None:
    """Persist a short-circuited pending-dialog turn for history/recovery.

    Ordinary runtime turns persist through ``TurnLog.write`` and Tutor turns
    through Tutor telemetry.  Pending replies never enter either path, so the
    resumable event log used to be their only copy and they disappeared after
    its retention window.  Build the canonical TurnLog shape without running
    its operational post-processing; ``query`` is already the scrubbed HTTP
    boundary value.
    """

    try:
        import config as _config
        import users as _users
        from dataclasses import asdict
        import agent_runtime as _agent_runtime

        if _users.owner_deletion_started(owner_user_id):
            return
        now = time.time()
        record = asdict(_agent_runtime.TurnLog(
            ts_start=now,
            ts_end=now,
            user_query=query,
            turn_id=turn_id,
            mode="pending",
            final_message=message,
            final_kind="answer",
            actor=actor,
            owner_user_id=owner_user_id,
            channel="http",
            conversation_id=conversation_id,
            redacted=bool(redacted_fields),
            n_redacted_fields=max(0, int(redacted_fields or 0)),
        ))
        _config.ensure_private_dir(_config.PATH_TURNS)
        path = _config.PATH_TURNS / f"{time.strftime('%Y-%m-%d')}.jsonl"
        _config.append_private_bytes(
            path,
            (json.dumps(record, ensure_ascii=False, default=str) + "\n")
            .encode("utf-8"),
        )
    except Exception:
        # The response remains available in the resumable event log.  A
        # persistence outage must not turn a valid dialog response into 500.
        log.warning("pending HTTP turn persistence failed", exc_info=True)


def _apply_dialog_cancel(sender_id: str, query: str, *,
                         owner_user_id: str) -> str | None:
    """Intercetta "annulla" come abort di dialog pending (24/5/2026).

    Quando un dialog `get_inputs` (es. disambiguation) e' pending per il
    sender, l'executor istruisce l'utente con "Rispondi nel prossimo
    messaggio. `annulla` per abortire." Se l'utente poi scrive "annulla"
    come query libera (fuori dalla form UI), oggi va al fast_path UNDO
    e tenta `undo_last_turn` — che non trova nulla di mutante da
    revertire e risponde "Nessuna operazione recente da annullare".

    Soluzione §7.3: PRIMA del pipeline, se la query e' un undo pattern E
    ci sono dialog pending per il sender, cancella TUTTI i dialog pending
    e ritorna il messaggio di conferma. L'utente vede coerenza fra
    l'istruzione data (annulla abortisce il dialogo) e l'effetto osservato.

    Ritorna None se non c'e' nulla da fare (caller prosegue normale).
    """
    from fast_path import _normalize, _undo_prefix_match  # type: ignore
    from fast_path import _UNDO_PATTERNS  # type: ignore
    norm = _normalize(query)
    if not norm:
        return None
    if norm not in _UNDO_PATTERNS and not _undo_prefix_match(norm):
        return None
    try:
        from dialog_pending import cancel_pending, list_pending
    except Exception:
        return None
    pending = [
        item for item in list_pending(
            sender_id, owner_user_id=owner_user_id)
    ]
    if not pending:
        return None
    cancelled = 0
    for d in pending:
        dlg_id = d.get("dialog_id", "")
        if (dlg_id and str(d.get("owner_user_id") or "") == owner_user_id
                and cancel_pending(
                    sender_id, dlg_id, owner_user_id=owner_user_id)):
            cancelled += 1
    if cancelled == 0:
        return None
    if cancelled == 1:
        return _msg("MSG_DIALOG_CANCELLED")
    return _msg("MSG_DIALOG_CANCELLED_N", n=cancelled)


def _with_dialog_form_marker(message: str, dialog: dict, dialog_id: str,
                             *, channel: str) -> str:
    """Keep an HTTP form actionable when a pending step is re-presented."""

    if (str(channel or "").lower() != "http"
            or str(dialog.get("fmt") or "").lower() != "form"
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}",
                                str(dialog_id or ""))):
        return message
    marker = f"INLINE_FORM:/agent/dialog/{dialog_id}/form"
    if marker in message:
        return message
    return f"{message.rstrip()}\n\n{marker}"


def _is_repeated_defer_request(dialog: dict, query: str) -> bool:
    """True when a user repeats the request held by an offline-device offer.

    Repeating the literal request is an unambiguous retry, not an attempt to
    choose Approve/Reject.  This is deliberately limited to ``defer_turn``;
    security-sensitive handoff/approval dialogs continue to require an
    explicit declared choice.
    """

    on_complete = dialog.get("on_complete") or {}
    if str(on_complete.get("type") or "") != "defer_turn":
        return False
    original = str(on_complete.get("original_query") or "")
    normalized_query = " ".join(str(query or "").casefold().split())
    normalized_original = " ".join(original.casefold().split())
    return bool(original) and normalized_query == normalized_original


def _apply_dialog_pending(sender_id: str, query: str,
                            actor: str = "host",
                            channel: str = "http",
                            conversation_id: str = "",
                            owner_user_id: str = "",
                            admit_only_valid_closed: bool = False) -> str | None:
    """Universal §7.9: se sender ha un dialog pending (get_inputs aperto),
    intercetta il prossimo messaggio utente come risposta al dialog.

    Risolve gap critico: dialog strato3 (5 azioni) aperto, utente digita
    "1" / "ritenta" → senza questo handler, "1" viene trattato come nuova
    query (echo). Ora viene routed correttamente al dispatcher on_complete.

    Ritorna messaggio finale se dialog consumato, None altrimenti (passa
    a flusso normale run_turn).
    """
    try:
        from dialog_pending import (
            cancel_pending, consume_pending_step, list_pending,
        )
    except ImportError:
        return None
    # Universal §7.9: prova multipli sender_id formats per backward compat.
    # HTTP nuovo: _http_sender_id(actor, conv_id) = "http:host:xyz"
    # Strato3 legacy: "channel:actor" = "http:host"
    pending = list_pending(sender_id, owner_user_id=owner_user_id)
    sender_id_used = sender_id
    if not pending:
        alt_sender = f"{channel}:{actor}" if channel else actor
        if alt_sender != sender_id:
            pending = list_pending(
                alt_sender, owner_user_id=owner_user_id)
            if pending:
                sender_id_used = alt_sender
    if not pending:
        return None
    # Prendi il dialog piu' recente (last started)
    dlg = pending[-1]
    dialog_id = dlg.get("dialog_id", "")
    steps = dlg.get("dialog", []) or []
    # `step_index` è il nome canonical (dialog_pending.py consume usa questo)
    step_index = int(dlg.get("step_index") or 0)
    if step_index >= len(steps):
        return None
    current_var = steps[step_index].get("var", "")
    if not current_var:
        return None
    current_step = steps[step_index]
    schema = current_step.get("schema") or {}
    schema_kind = str(schema.get("kind") or "text")
    if admit_only_valid_closed and schema_kind in {
            "text", "credentials", "file_path", "location"}:
        return None
    from channels.daemon import parse_step_value
    valid, parsed_value, parse_error = parse_step_value(query, schema)
    if not valid:
        if _is_repeated_defer_request(dlg, query):
            # The original device request was submitted again (typically
            # after the device came back online).  Retire both halves of the
            # stale offer so Tutor/runtime can evaluate the request afresh.
            if cancel_pending(
                    sender_id_used, dialog_id,
                    owner_user_id=owner_user_id):
                try:
                    from channels.daemon import (
                        _cap_pending_clear, _cap_pending_load,
                    )
                    for cap_sender in dict.fromkeys(
                            (sender_id, sender_id_used)):
                        cap = _cap_pending_load(
                            cap_sender, owner_user_id=owner_user_id)
                        proposal = (cap or {}).get("proposal") or {}
                        if str(proposal.get("dialog_id") or "") == dialog_id:
                            _cap_pending_clear(cap_sender)
                except Exception:
                    log.debug("stale defer cap cleanup failed", exc_info=True)
                log.info("repeated deferred request retired dialog=%s",
                         dialog_id)
                return None
        if admit_only_valid_closed:
            return None
        return _with_dialog_form_marker(_msg(
            "MSG_DIALOG_STEP_REPROMPT",
            err=parse_error or "invalid_value",
            n=step_index + 1,
            total=len(steps),
            prompt=current_step.get("prompt") or "",
        ), dlg, dialog_id, channel=channel)
    # Avanza dialog con valore raccolto
    consume_res = consume_pending_step(
        sender_id_used, dialog_id, current_var, parsed_value,
        owner_user_id=owner_user_id)
    if not consume_res.get("ok"):
        # A value that does not satisfy a declared dialog schema is not a new
        # operational query.  Keep the pending interaction and reprompt just
        # as the Telegram adapter does; the user can explicitly cancel it.
        return _with_dialog_form_marker(_msg(
            "MSG_DIALOG_STEP_REPROMPT",
            err=consume_res.get("error") or "invalid_value",
            n=step_index + 1,
            total=len(steps),
            prompt=current_step.get("prompt") or "",
        ), dlg, dialog_id, channel=channel)
    # Se dialog completato → call canonical dispatcher process_completion_callback
    if consume_res.get("completed"):
        try:
            from orchestration import process_completion_callback
            return process_completion_callback(
                sender_id_used, dialog_id, actor=actor, channel=channel,
                owner_user_id=owner_user_id,
            ).text
        except Exception as ex:
            import logging
            logging.getLogger(__name__).warning(
                "dialog on_complete dispatch failed: %s", ex)
            return _msg("ERR_DIALOG_DISPATCH_FAILED", error=ex)
    next_index = int(consume_res.get("step_index") or step_index + 1)
    next_step = steps[next_index] if next_index < len(steps) else {}
    return _with_dialog_form_marker(_msg(
        "MSG_DIALOG_STEP_PROMPT",
        n=next_index + 1,
        total=len(steps),
        prompt=next_step.get("prompt") or "",
        hint="",
    ), dlg, dialog_id, channel=channel)


def _apply_cap_pending(
        sender_id: str, query: str, actor: str = "host", *,
        owner_user_id: str,
        decision: str = "",
        decision_turn_id: str = "") -> tuple[str, dict | None, str | None]:
    """Se c'e' un cap-expand pending e la query e' un sì, ritorna la
    query riscritta + il pending consumato. Altrimenti pulisce stato (su
    'no'/qualsiasi altro) e ritorna la query originale.

    Per pending di tipo `admin_approval` (ADR 0088): salta il PLANNER,
    invoca direttamente admin con il consent_token e ritorna nel terzo
    elemento della tupla la `final_message` da emettere subito (senza
    rilancio del turno).

    Per pending di tipo `get_inputs_response` (ADR 0090, 0091): NON
    intercettiamo qui, lo gestisce il submit del form HTTP standalone
    (/agent/dialog/<id>/submit) o, in modalita' dialogue, il prossimo
    turno via /agent/turn dopo il consume sequenziale (carry-over
    Telegram-style nel canale HTTP, oggi non implementato perche' il
    canale HTTP usa fmt='form' di default).

    Replica la logica di `channels/daemon.py:CAP EXPAND fase 2` per il
    canale HTTP che NON passa per ChannelDaemon.

    `decision` (17/8/2026): quando la chat presenta i due bottoni, la
    risposta non e' piu' una parola da interpretare ma una decisione
    tipizzata (`yes`/`no`) legata alla proposta da `decision_turn_id`.
    Nessuna classificazione di testo, quindi nessun fraintendimento
    possibile. E' lo stesso contratto dei bottoni Telegram
    (`cap:<turn_id>:yes|no`, `inline_ui`), su un altro trasporto — e passa
    per QUESTA funzione, cosi' il consumo della proposta resta uno solo.
    """
    from channels.daemon import (
        _cap_pending_load, _cap_pending_clear, _classify_yes_no,
    )
    # Il campo lo scrive il client: fuori dall'insieme chiuso non e' una
    # decisione, e ricade sul percorso normale invece di aprire una strada
    # propria. Normalizzato QUI, una volta, cosi' ogni ramo sotto legge la
    # stessa cosa.
    decision = decision if decision in ("yes", "no") else ""
    pending = _cap_pending_load(
        sender_id, owner_user_id=owner_user_id)
    if not pending:
        if decision:
            # Bottone premuto su una domanda che non c'e' piu' (scaduta,
            # gia' risposta altrove, sessione ripresa su un altro device).
            # Il bottone non deve far partire nulla al buio.
            return query, None, _msg("MSG_CAP_PROPOSAL_EXPIRED")
        return query, None, None
    p = pending["proposal"]

    if decision:
        _open = str(pending.get("turn_id") or "")
        if decision_turn_id and _open and decision_turn_id != _open:
            # Tap su una bolla vecchia mentre ne e' aperta un'altra: la
            # decisione vale per la domanda che l'utente stava guardando,
            # non per quella corrente. Stessa regola dei bottoni Telegram.
            return query, None, _msg("MSG_CAP_PROPOSAL_EXPIRED")

    # ── Branch get_inputs_response (ADR 0090, FIX 1 6/5/2026) ─────────
    # Il dialog persiste in dialog_pending (file 0600). Su HTTP +
    # 1 step il fmt e' 'dialogue', quindi la risposta dell'utente
    # arriva come query a /agent/turn — la consumiamo qui via
    # consume_pending_step + process_completion_callback (che a sua
    # volta dispatcha a expand_cap_and_resume e ri-invoca l'executor
    # con il cap esteso). Senza questo branch il "sì" finiva al PLANNER
    # come query nuova → rispondeva "Ciao!".
    if p.get("kind") == "get_inputs_response":
        return _consume_http_get_inputs_response(
            p, query, sender_id=sender_id, actor=actor,
            owner_user_id=owner_user_id,
        )

    # Una decisione tipizzata non si rilegge dal testo: e' gia' la risposta.
    ans = decision if decision in ("yes", "no") else _classify_yes_no(query)
    if ans == "yes":
        if p.get("kind") == "admin_approval":
            _cap_pending_clear(sender_id)
            from loader import invoke_verb_unique
            args = dict(p.get("args_suggested") or {})
            try:
                res = invoke_verb_unique(
                    "admin", caller="agent_runtime",
                    intent=args.get("intent", ""),
                    command_proposed=args.get("command_proposed", ""),
                    credentials_domain=args.get("credentials_domain"),
                    actor_consent_token=args.get("actor_consent_token"),
                    actor=actor,
                )
            except (PermissionError, KeyError, RuntimeError) as e:
                return query, pending, _msg(
                    "ERR_OP_FAILED", reason=f"{type(e).__name__}: {e}",
                )
            return query, pending, (res or {}).get("summary", json.dumps(res)[:500])
        # approval_required: direct invocation dell'executor con
        # args_suggested. Il rewrite verbale "(forza X=Y
        # su Z)" non regge sul PLANNER medium (modello locale): test live
        # 5/5/2026 ha mostrato che il modello locale ha interpretato il rewrite come
        # saluto e ha emesso "Ciao!" invece di rilanciare il tool.
        # 6/5/2026 (ADR 0091 generalizzato): il vecchio kind="cap_expand"
        # e' stato sostituito a monte da get_inputs_response (vedi
        # agent_runtime._orchestrate_cap_expand_dialog) — qui resta solo
        # `approval_required` (build pesante, signature size approval).
        if p.get("kind") == "approval_required":
            _cap_pending_clear(sender_id)
            executor_name = p.get("executor")
            args = dict(p.get("args_suggested") or {})
            try:
                from loader import load_catalog
                cat = load_catalog(verify=True, include_synth=True)
                ex = cat.executors.get(executor_name)
                if ex is None:
                    return query, pending, _msg(
                        "ERR_CHAT_EXECUTOR_NOT_IN_CATALOG",
                        executor=executor_name,
                    )
                import agent_runtime
                res = agent_runtime.invoke_executor(
                    ex, args, timeout_s=getattr(ex, "timeout_s", 30),
                    actor=actor, channel="http",
                )
            except Exception as e:
                return query, pending, _msg(
                    "ERR_OP_FAILED", reason=f"{type(e).__name__}: {e}",
                )
            # Format minimale del risultato. find_images_indices: entries=[{path,score}]
            entries = (res or {}).get("entries") or []
            n_entries = (res or {}).get("n_entries") or len(entries)
            if not res or not res.get("ok"):
                err = (res or {}).get("error") or _msg("MSG_ERR_UNKNOWN")
                return query, pending, _msg(
                    "MSG_CAP_EXPAND_FAILED", field=p["cap_field"],
                    value=p["cap_suggested"], err=err,
                )
            # Anteprima (nomi file + score) — il primo set significativo,
            # massimo 30 righe perche' oltre diventa illeggibile.
            preview = []
            for e in entries[:30]:
                p_path = e.get("path", "?")
                # solo basename per leggibilità
                bn = p_path.rsplit("/", 1)[-1] if isinstance(p_path, str) else str(p_path)
                score = e.get("score")
                if isinstance(score, (int, float)):
                    preview.append(f"  {score:+.3f}  {bn}")
                else:
                    preview.append(f"  {bn}")
            head = _msg(
                "MSG_CAP_EXPAND_RESULT", field=p["cap_field"],
                value=p["cap_suggested"], n=n_entries,
                label=_msg("MSG_CHAT_RESULTS_LABEL"),
            )
            if len(entries) > 30:
                tail = "\n" + _msg(
                    "MSG_RESUME_ENTRIES_OMITTED", n=len(entries) - 30,
                )
            else:
                tail = ""
            return query, pending, head + "\n\n" + "\n".join(preview) + tail
        # Kind sconosciuto al "sì" → scarta lo stato e procedi normalmente.
        log.warning("[http] cap_pending kind sconosciuto %r -> scarto", p.get("kind"))
        _cap_pending_clear(sender_id)
        return query, None, None
    if ans == "no":
        _cap_pending_clear(sender_id)
        return query, pending, _msg("MSG_CAP_PROPOSAL_DECLINED")
    _cap_pending_clear(sender_id)
    return query, None, None


def _consume_http_get_inputs_response(
    proposal: dict, query: str, *, sender_id: str, actor: str,
    owner_user_id: str,
) -> tuple[str, dict | None, str | None]:
    """Consumer HTTP per dialog get_inputs (mirror del Telegram daemon).

    Replica la logica di `channels/daemon.py:_consume_get_inputs_response`
    adattata al contratto di `_apply_cap_pending`: ritorna
    `(rewritten_query, consumed_pending, immediate_summary)`.

    Per cap-expand standard (1 step yes_no, fmt=dialogue su HTTP) il
    dialogo si completa al primo "sì" e il callback `expand_cap_and_resume`
    invoca direttamente l'executor con cap esteso. Niente PLANNER.
    """
    from channels.daemon import (
        _cap_pending_clear, parse_step_value,
    )
    # runtime/ già su sys.path (http_routes_agent VIVE in runtime/).
    import dialog_pending as _dp
    import orchestration as _orch

    dialog_id = proposal.get("dialog_id") or ""
    sender_for_state = proposal.get("sender_for_state") or sender_id
    text_norm = (query or "").strip().lower()

    if text_norm in ("annulla", "cancel", "abort", "stop"):
        _dp.cancel_pending(
            sender_for_state, dialog_id,
            owner_user_id=owner_user_id)
        _cap_pending_clear(sender_id)
        return query, proposal, _msg("MSG_DIALOG_CANCELLED")

    state = _dp.load_pending(
        sender_for_state, dialog_id, owner_user_id=owner_user_id)
    if state is None:
        # Dialog scaduto/cancellato/sconosciuto → tratta la query nuova
        # come turno fresco (no immediate_msg). 10/5/2026 fix: prima
        # ritornavamo un messaggio di errore che bloccava il PLANNER.
        _cap_pending_clear(sender_id)
        return query, None, None
    dialog = state.get("dialog") or []
    idx = int(state.get("step_index") or 0)
    if idx >= len(dialog) or state.get("completed") or state.get("cancelled"):
        # Dialog finito (completato dal form HTTP submit, o cancellato):
        # cap_pending e' rimasto stale. Pulisci e tratta la nuova query
        # come turno fresco (10/5/2026 fix: prima il messaggio Bob
        # con 13 foto veniva DROPPATO dopo Roberto enrollment).
        _cap_pending_clear(sender_id)
        return query, None, None
    cur_step = dialog[idx]
    var = cur_step.get("var")
    schema = cur_step.get("schema") or {}
    schema_kind = (schema or {}).get("kind")
    ok, value, err = parse_step_value(query or "", schema)
    if not ok:
        if _is_repeated_defer_request(state, query):
            _dp.cancel_pending(
                sender_for_state, dialog_id,
                owner_user_id=owner_user_id)
            _cap_pending_clear(sender_id)
            return query, None, None
        # Per dialog yes_no (caso tipico cap-expand): se l'utente NON
        # risponde sì/no ma scrive una query lunga (>10 char), interpretiamo
        # come "non era una risposta, ho cambiato idea, ecco una nuova
        # richiesta". Cancelliamo il dialog (equivalente a "no" sul
        # cap-expand) e lasciamo passare la query al PLANNER come turno
        # nuovo. Senza questa euristica l'utente resterebbe bloccato sul
        # re-prompt all'infinito ogni volta che scrive qualcosa di diverso.
        text_len = len((query or "").strip())
        if schema_kind == "yes_no" and text_len > 10:
            _dp.cancel_pending(
                sender_for_state, dialog_id,
                owner_user_id=owner_user_id)
            _cap_pending_clear(sender_id)
            # query torna invariata, pending=None → il flow normale
            # processa la query come turno nuovo.
            return query, None, None
        # Altrimenti: re-prompt dello stesso step (input troppo breve o
        # malformato — verosimilmente errore di battitura).
        return (query, None, _with_dialog_form_marker(_msg(
            "MSG_DIALOG_STEP_REPROMPT", err=err, n=idx + 1,
            total=len(dialog), prompt=cur_step.get("prompt") or "?",
        ), state, dialog_id, channel="http"))

    cres = _dp.consume_pending_step(
        sender_for_state, dialog_id, var, value,
        owner_user_id=owner_user_id)
    if not cres.get("ok"):
        _cap_pending_clear(sender_id)
        return (query, proposal, _msg(
            "MSG_DIALOG_STEP_ERROR", error=cres.get("error"),
        ))

    if cres.get("completed"):
        # Dialogo finito → applica callback. Per cap-expand questo
        # invoca direttamente l'executor con cap esteso e ritorna
        # un summary user-facing.
        msg = _orch.process_completion_callback(
            sender_for_state, dialog_id, actor=actor,
            owner_user_id=owner_user_id,
        ).text
        _cap_pending_clear(sender_id)
        return query, proposal, msg

    # Prossimo step (raro per cap-expand a 1 step).
    next_step = dialog[idx + 1]
    next_prompt = next_step.get("prompt") or "?"
    return (query, None, _with_dialog_form_marker(_msg(
        "MSG_DIALOG_STEP_PROMPT", n=idx + 2, total=len(dialog),
        prompt=next_prompt, hint="",
    ), state, dialog_id, channel="http"))


def _save_cap_pending_if_any(sender_id: str, original: str, turn_log, *,
                             owner_user_id: str) -> None:
    if getattr(turn_log, "expandable_caps", None):
        from channels.daemon import _cap_pending_save
        _cap_pending_save(sender_id, original,
                          turn_log.expandable_caps[0], turn_log.turn_id,
                          owner_user_id=owner_user_id)


# Proposte che si chiudono con un si' o un no, e nient'altro. I dialoghi
# strutturati (`get_inputs_response`) hanno gia' il loro modulo con i
# bottoni: iscriverli qui darebbe due comandi per la stessa domanda.
_DECIDABLE_PENDING_KINDS = frozenset({
    "admin_approval", "approval_required", "cap_expand",
})


def _pending_decision_payload(turn_log) -> dict | None:
    """Descrive la domanda aperta perche' la chat possa offrire due bottoni.

    Un bottone non manda la parola «si'»: manda una decisione tipizzata
    legata a QUESTA proposta (`turn_id`). E' il contratto dei bottoni
    Telegram portato sul canale HTTP — dove finora la stessa domanda
    arrivava come testo, e l'utente doveva scriverla."""
    caps = getattr(turn_log, "expandable_caps", None) or []
    if not caps:
        return None
    kind = str((caps[0] or {}).get("kind") or "")
    if kind not in _DECIDABLE_PENDING_KINDS:
        return None
    return {
        "turn_id": turn_log.turn_id,
        "kind": kind,
        "yes_label": _msg("MSG_BTN_APPROVE") if kind == "admin_approval"
        else _msg("MSG_BTN_YES"),
        "no_label": _msg("MSG_BTN_REJECT") if kind == "admin_approval"
        else _msg("MSG_BTN_NO"),
    }



CHAT_INLINE_ATT_CAP = 20


def _enrich_attachments(log_obj, admin_key: str, *, cap: int = CHAT_INLINE_ATT_CAP) -> list:
    """Sostituisce path crudi con URL signed (thumb+full). Ritorna lista
    di dict {kind, basename, score, caption, thumb_url, full_url}.
    Niente path crudo verso il client (ADR 0082-style separation).

    Cap default = 20: la chat compatta mostra le prime 20 inline; le
    eventuali extra restano accessibili dalla gallery (`gallery_url`).
    Cap=0 → nessun limite (placeholder convention §2.4).

    Universal §7.3 — supporto dual-source:
      (1) attachment con `path` (local file) → URL signed via photo_endpoint
      (2) attachment con `url` (web URL, es. da find_images_web) → URL diretto

    Universal §7.3 — input + results coexist (drag&drop reverse search):
    quando ci sono input photos (`caption=='input'`), mostra TUTTI gli
    input + i primi `cap` risultati. Gli input sono significativi per
    l'utente (cosa ha caricato), non vanno troncati per stare nel cap.
    """
    import photo_endpoint
    atts = getattr(log_obj, "attachments", []) or []
    if cap and cap > 0:
        inputs = [a for a in atts if isinstance(a, dict) and a.get("caption") == "input"]
        others = [a for a in atts if isinstance(a, dict) and a.get("caption") != "input"]
        if inputs and others:
            # Tutti gli input + cap risultati. Ordine: input prima, results dopo.
            atts = inputs + others[:cap]
        else:
            atts = atts[:cap]
    out = []
    for idx, att in enumerate(atts):
        if not isinstance(att, dict):
            continue
        # Attachment web-sourced (url): proxy via /agent/photos/web per
        # bypassare hotlinking-block dei CDN (TikTok, Instagram, FB).
        web_url = att.get("url")
        if isinstance(web_url, str) and (web_url.startswith("http://") or web_url.startswith("https://")):
            preview_url = att.get("thumbnail_url")
            if not isinstance(preview_url, str) or not preview_url.startswith(("http://", "https://")):
                preview_url = web_url
            proxy = _web_photo_proxy_url(preview_url, admin_key)
            full_proxy = _web_photo_proxy_url(web_url, admin_key)
            out.append({
                "kind": att.get("kind", "image"),
                "basename": att.get("basename"),
                "score": att.get("score"),
                "caption": att.get("caption"),
                "date": att.get("date"),
                "thumb_url": proxy,
                "full_url": full_proxy,
                "open_url": web_url,  # link a sorgente reale per click esterno
                "fallback_url": preview_url if preview_url != web_url else web_url,
            })
            continue
        # Attachment file non-immagine (xlsx/doc/zip/pdf): download signed,
        # niente thumbnail. La chat lo rende come chip di download.
        if att.get("kind") == "file":
            out.append({
                "kind": "file",
                "basename": att.get("basename"),
                "caption": att.get("caption"),
                "mime": att.get("mime"),
                "download_url": photo_endpoint.make_url(
                    log_obj.turn_id, idx, "file", admin_key),
            })
            continue
        # Attachment local-sourced (path): URL signed via photo_endpoint.
        out.append({
            "kind": att.get("kind", "image"),
            "basename": att.get("basename"),
            "score": att.get("score"),
            "caption": att.get("caption"),
            "date": att.get("date"),
            "thumb_url": photo_endpoint.make_url(
                log_obj.turn_id, idx, "thumb", admin_key
            ),
            "full_url": photo_endpoint.make_url(
                log_obj.turn_id, idx, "full", admin_key
            ),
        })
    return out


def _gallery_url_for(log_obj) -> tuple[str | None, int]:
    """Ritorna (gallery_url, n_total). La gallery e' IMAGE-only: i file
    deliverable (kind='file', xlsx/doc/zip) NON contano e non generano un
    link gallery (bug 7d2f734f: "gallery 1 foto" vuota su turno con solo
    uno spreadsheet)."""
    atts = getattr(log_obj, "attachments", []) or []
    n_total = sum(1 for a in atts
                  if isinstance(a, dict) and a.get("kind") != "file")
    if n_total <= 0:
        return None, 0
    return f"/agent/gallery/{log_obj.turn_id}", n_total


def _dialog_cap_sign(dialog_id: str, admin_key: str, *,
                     now: int | None = None) -> str:
    """Firma una capability HTTP limitata a un singolo dialogo."""
    if not admin_key:
        return ""
    exp = int(now if now is not None else time.time()) + _DIALOG_CAP_TTL_S
    payload = f"dialog-v1:{dialog_id}:{exp}"
    sig = hmac.new(admin_key.encode("utf-8"), payload.encode("utf-8"),
                   hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def _dialog_cap_verify(dialog_id: str, token: str, admin_key: str, *,
                       now: int | None = None) -> bool:
    """Verifica binding, firma e scadenza senza sollevare su input ostile."""
    if not token or not admin_key or len(token) > 160:
        return False
    try:
        exp_s, sig = token.split(".", 1)
        exp = int(exp_s)
    except (TypeError, ValueError):
        return False
    current = int(now if now is not None else time.time())
    if exp < current or exp > current + _DIALOG_CAP_TTL_S + 60:
        return False
    payload = f"dialog-v1:{dialog_id}:{exp}"
    expected = hmac.new(admin_key.encode("utf-8"), payload.encode("utf-8"),
                        hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


def _decorate_dialog_markers(message: str | None, admin_key: str) -> str:
    """Aggiunge JIT la capability ai marker dei form nel solo boundary HTTP."""
    text = message or ""
    if not text or not admin_key:
        return text

    def _replace(match: re.Match) -> str:
        path, dialog_id = match.group(1), match.group(2)
        cap = _dialog_cap_sign(dialog_id, admin_key)
        return f"INLINE_FORM:{path}?cap={cap}" if cap else match.group(0)

    return _DIALOG_FORM_MARKER_RE.sub(_replace, text)


def _build_final_event_payload(log_obj, admin_key: str) -> dict:
    """Payload unico dell'evento `final` (SSE inline + event-log resumable).
    Condiviso da `_turn_sse` e `turn_submit`: ogni path espone gli stessi
    campi — inclusi attachments/gallery per i turni con immagini. Salta gli
    step senza tool e i phantom `auto_final_on_duplicate` nel path badge."""
    gallery_url, n_total = _gallery_url_for(log_obj)
    path_summary = []
    for s in getattr(log_obj, "steps", []) or []:
        tool = getattr(s, "chosen_tool", "") or ""
        if not tool:
            continue
        if getattr(s, "error", None) == "auto_final_on_duplicate":
            continue
        res = s.result if isinstance(s.result, dict) else {}
        path_summary.append({"tool": tool, "ok": bool(res.get("ok", True))})
    final_message = _decorate_dialog_markers(log_obj.final_message, admin_key)
    return {
        "turn_id": log_obj.turn_id,
        "final_message": final_message,
        "final_message_html": _safe_final_html(final_message),
        "final_kind": log_obj.final_kind,
        # Destinazione risolta (ADR 0034): None = server, altrimenti nome device.
        "target_device": getattr(log_obj, "target_device", None),
        "total_ms": int((log_obj.ts_end - log_obj.ts_start) * 1000),
        "ts_end": float(log_obj.ts_end),
        "expandable_caps": getattr(log_obj, "expandable_caps", []) or [],
        "pending_decision": _pending_decision_payload(log_obj),
        "attachments": _enrich_attachments(log_obj, admin_key),
        "gallery_url": gallery_url,
        "n_total_matches": n_total,
        "path": path_summary,
    }


def _preprocessed_turn_data(
        *, query_for_run: str, immediate_msg: str | None,
        immediate_source: str, immediate_turn_id: str, actor: str,
        user_id: str, conversation_id: str, sender_id: str,
        reference_images: list[str], original_query: str,
        credential_meta: list[dict] | None = None,
        redacted_fields: int = 0, immediate_elapsed_ms: int = 0,
        tutor_deferred: bool = False,
        deferred_query: str = "") -> dict:
    """Build the one internal hand-off shape used by both HTTP turn paths.

    ``deferred_query`` exists only in the in-memory task hand-off for the
    resumable endpoint.  It is never copied to the event log or a TurnLog;
    credentials are never deferred because their structural redaction skips
    Tutor and follows the synchronous preparation path.
    """

    data = {
        "query_for_run": query_for_run,
        "immediate_msg": immediate_msg,
        "immediate_source": immediate_source,
        "immediate_turn_id": immediate_turn_id,
        "immediate_elapsed_ms": max(0, int(immediate_elapsed_ms or 0)),
        "actor": actor,
        "user_id": user_id,
        "conversation_id": conversation_id,
        "sender_id": sender_id,
        "reference_images": reference_images,
        "original_query": original_query,
        "credential_meta": list(credential_meta or []),
        "redacted_fields": int(redacted_fields or 0),
    }
    if tutor_deferred:
        data["_tutor_deferred"] = True
        data["_deferred_query"] = deferred_query
    return data


async def _resolve_open_http_turn(
        request: web.Request, *, query: str, safe_original_query: str,
        sensitive_fields: int, actor: str, user_id: str,
        conversation_id: str, sender_id: str,
        reference_images: list[str], turn_id_hint: str = "") -> dict:
    """Resolve Tutor and open pending state after deterministic pre-passes.

    The resumable endpoint runs this phase in its background turn task, after
    returning ``202``.  The legacy inline endpoint awaits the same function.
    Keeping one implementation preserves identical semantic authority,
    per-user pending isolation and credential preparation across transports.
    """

    tutor_error = None
    tutor_busy = False
    try:
        tutor = (
            None if sensitive_fields else
            await _apply_tutor_http(
                request, query=query, actor=actor, user_id=user_id,
                conversation_id=conversation_id, sender_id=sender_id,
                turn_id_hint=turn_id_hint,
            )
        )
    except TutorBoundaryBusy:
        tutor = None
        tutor_busy = True

    if tutor is not None and tutor.esito != "tutor_error":
        return _preprocessed_turn_data(
            query_for_run=safe_original_query,
            immediate_msg=tutor.answer_md,
            immediate_source="tutor",
            immediate_turn_id=tutor.turn_id or turn_id_hint or "tutor",
            immediate_elapsed_ms=tutor.elapsed_ms,
            actor=actor, user_id=user_id,
            conversation_id=conversation_id, sender_id=sender_id,
            reference_images=reference_images,
            original_query=safe_original_query,
            redacted_fields=sensitive_fields,
        )
    if tutor is not None:
        tutor_error = tutor

    query_for_run = query
    immediate_msg = None
    boundary_failed = tutor_error is not None or tutor_busy
    if boundary_failed:
        # The deterministic closed-schema pre-pass has already accepted every
        # valid reply.  During a Tutor outage only an exact yes/no twin may
        # reach the capability consumer; arbitrary prose must preserve an
        # open dialog instead of being swallowed by it.
        # `_classify_yes_no` returns "yes" | "no" | "other", never None: the
        # comparison against None was always true and let arbitrary prose
        # through to the capability consumer — the opposite of what the
        # comment above states. The exact twin is a yes or a no, nothing else.
        from channels.daemon import _classify_yes_no
        if _classify_yes_no(query) in ("yes", "no"):
            query_for_run, _consumed_pending, immediate_msg = (
                _apply_cap_pending(
                    sender_id, query, actor=actor,
                    owner_user_id=user_id)
            )
        if immediate_msg is None and tutor_error is not None:
            return _preprocessed_turn_data(
                query_for_run=safe_original_query,
                immediate_msg=tutor_error.answer_md,
                immediate_source="tutor",
                immediate_turn_id=(
                    tutor_error.turn_id or turn_id_hint or "tutor"),
                immediate_elapsed_ms=tutor_error.elapsed_ms,
                actor=actor, user_id=user_id,
                conversation_id=conversation_id, sender_id=sender_id,
                reference_images=reference_images,
                original_query=safe_original_query,
                redacted_fields=sensitive_fields,
            )
        if tutor_busy:
            # Tutor is an optional semantic pre-gate. Saturating its bounded
            # workers preserves the pending interaction and falls through to
            # the ordinary runtime.
            log.info("HTTP Tutor busy; falling through to runtime")
    else:
        immediate_msg = _apply_dialog_pending(
            sender_id, query, actor=actor, channel="http",
            conversation_id=conversation_id or "",
            owner_user_id=user_id,
        )
        if immediate_msg is None:
            query_for_run, _consumed_pending, immediate_msg = (
                _apply_cap_pending(
                    sender_id, query, actor=actor,
                    owner_user_id=user_id)
            )

    credential_meta: list[dict] = []
    prepared_fields = sensitive_fields
    if immediate_msg is None:
        # Pending consumers have seen the in-memory raw reply. From here on,
        # both planner and persistence receive only the prepared form.
        import agent_runtime as _credential_runtime
        query_for_run, credential_meta, prepared_fields = (
            _credential_runtime.prepare_credentials_for_routing(query_for_run)
        )
    return _preprocessed_turn_data(
        query_for_run=query_for_run,
        immediate_msg=immediate_msg,
        immediate_source="pending" if immediate_msg is not None else "",
        immediate_turn_id="",
        actor=actor, user_id=user_id,
        conversation_id=conversation_id, sender_id=sender_id,
        reference_images=reference_images,
        original_query=safe_original_query,
        credential_meta=credential_meta,
        redacted_fields=prepared_fields,
    )


async def _preprocess_turn(request: web.Request):
    """Pre-elabora una richiesta di turno (JSON o multipart immagini) in modo
    condiviso fra `turn()` (streaming inline legacy) e `turn_submit()`
    (resumable EventSource). Esegue: parse body, salvataggio campi `image_*`
    in upload dir → `reference_images`, e interception dialog/cap pending.

    Ritorna `(err_response, data)`:
      - `err_response` = web.Response su validazione fallita (`data` None);
      - altrimenti `data` = dict con `query_for_run`, `immediate_msg`,
        `actor`, `conversation_id`, `sender_id`, `reference_images`,
        `original_query`.
    """
    ctype = (request.content_type or "").lower()
    reference_images: list[str] = []
    if ctype.startswith("multipart/"):
        try:
            form = await request.post()
        except Exception as ex:
            log.exception("multipart form parse failed: ctype=%r len=%s",
                          ctype, request.content_length)
            return _error(400, "invalid_form",
                          f"multipart form data non valido: "
                          f"{type(ex).__name__}: {ex}"), None
        query = form.get("query") or form.get("text") or ""
        if not isinstance(query, str):
            query = str(query) if query is not None else ""
        if not query.strip():
            return _error(400, "missing_field", "query (string) required"), None
        body = {
            "query": query,
            "conversation_id": form.get("conversation_id") or "",
            "actor": form.get("actor") or None,
            "device_token": form.get("device_token") or "",
        }
        raw_actor = _resolve_actor(request, body)
        user_id = await _resolve_session_user_id(request)
        # `actor` resta l'identita' di autorizzazione storica (host/device):
        # sostituirla con l'UUID utente romperebbe gate admin, undo e ownership
        # degli executor. Solo lo state conversazionale usa l'owner stabile,
        # cosi' puo' seguire la conversazione su un device differente.
        actor = raw_actor
        state_owner = (raw_actor if request.get("role") == "admin"
                       and body.get("actor") else user_id)
        conversation_id = body.get("conversation_id") or ""
        sender_id = _http_sender_id(state_owner, conversation_id)
        if request.path == "/agent/turn/submit":
            import active_sessions as _as
            writable, reason = _as.validate_writer(
                str(body.get("device_token") or ""), user_id=user_id,
                channel="http", conversation_id=conversation_id,
            )
            if not writable:
                status = 400 if reason in {
                    "session_token_required", "conversation_required",
                    "invalid_conversation",
                } else 409
                return _error(
                    status, reason, _msg("ERR_CHAT_SESSION_WRITE_DENIED"),
                ), None
        from upload_cleanup import UPLOAD_DIR as _UP
        safe_sender = sender_id.replace("/", "_")[:64]
        out_dir = Path(_UP) / safe_sender
        out_dir.mkdir(parents=True, exist_ok=True)
        turn_pre = hashlib.sha256(
            f"{time.time()}_{sender_id}".encode()
        ).hexdigest()[:12]
        idx = 0
        items = form.items() if hasattr(form, "items") else []
        for key, val in items:
            if not (isinstance(key, str) and key.startswith("image")):
                continue
            file_field = val
            if not hasattr(file_field, "file"):
                continue  # non un upload (es. testo passato in image_*)
            mime = (getattr(file_field, "content_type", "") or "").lower()
            if not mime.startswith("image/"):
                continue
            ext = ".jpg"
            fname = getattr(file_field, "filename", "") or ""
            if "." in fname:
                _e = "." + fname.rsplit(".", 1)[-1].lower()
                if _e in (".jpg", ".jpeg", ".png", ".webp", ".heic"):
                    ext = _e
            out_path = out_dir / f"{turn_pre}_{idx}{ext}"
            try:
                with out_path.open("wb") as fh:
                    while True:
                        chunk = file_field.file.read(64 * 1024)
                        if not chunk:
                            break
                        fh.write(chunk)
                reference_images.append(str(out_path))
                idx += 1
            except OSError as ex:
                log.warning("upload save failed for %s: %s", fname, ex)
    else:
        try:
            body = await request.json()
        except Exception:
            return _error(400, "invalid_json", "request body must be JSON"), None
        query = body.get("query")
        if not isinstance(query, str) or not query.strip():
            return _error(400, "missing_field", "query (string) required"), None
        raw_actor = _resolve_actor(request, body)
        user_id = await _resolve_session_user_id(request)
        actor = raw_actor
        state_owner = (raw_actor if request.get("role") == "admin"
                       and body.get("actor") else user_id)
        conversation_id = body.get("conversation_id") or ""
        sender_id = _http_sender_id(state_owner, conversation_id)
        if request.path == "/agent/turn/submit":
            import active_sessions as _as
            writable, reason = _as.validate_writer(
                str(body.get("device_token") or ""), user_id=user_id,
                channel="http", conversation_id=conversation_id,
            )
            if not writable:
                status = 400 if reason in {
                    "session_token_required", "conversation_required",
                    "invalid_conversation",
                } else 409
                return _error(
                    status, reason, _msg("ERR_CHAT_SESSION_WRITE_DENIED"),
                ), None

    # One canonical structural pass before any LLM. Raw values may still be
    # consumed by a trusted pending dialog below, but Tutor never receives
    # them and no returned/prepared payload retains them.
    safe_original_query, sensitive_fields = scrub_sensitive_text(query)

    # ── Decisione da bottone (17/8/2026) ─────────────────────────────
    # Un tap non e' una richiesta nuova: e' la risposta a una domanda
    # aperta. Entra prima del Tutor e degli altri intercettori, che
    # esistono per interpretare del testo — e qui non c'e' testo da
    # interpretare. Il valore arriva tipizzato dal client e viene
    # comunque validato contro l'insieme chiuso: un campo arbitrario non
    # apre questa strada.
    # Solo dal corpo JSON: un turno multipart porta immagini, cioe' una
    # intenzione nuova, e piu' sotto azzera comunque lo stato pendente.
    _body = body if not ctype.startswith("multipart/") else {}
    _decision = str((_body or {}).get("decision") or "").strip().lower()
    if _decision in ("yes", "no"):
        _q_run, _consumed, _immediate = _apply_cap_pending(
            sender_id, query, actor=actor, owner_user_id=user_id,
            decision=_decision,
            decision_turn_id=str((_body or {}).get("decision_turn_id") or ""),
        )
        return None, _preprocessed_turn_data(
            query_for_run=_q_run,
            immediate_msg=_immediate,
            immediate_source="pending" if _immediate is not None else "",
            immediate_turn_id="",
            actor=actor, user_id=user_id,
            conversation_id=conversation_id, sender_id=sender_id,
            reference_images=reference_images,
            original_query=safe_original_query,
            redacted_fields=sensitive_fields,
        )

    # Universal §7.3: drag&drop con immagini + query = NUOVA intenzione
    # esplicita, mai una risposta a dialog precedenti. Skip TUTTI gli
    # interceptor (cancel, dialog_pending, cap_pending).
    if reference_images:
        from channels.daemon import _cap_pending_clear
        _cap_pending_clear(sender_id)
        try:
            from dialog_pending import list_pending, cancel_pending
            for d in list_pending(sender_id, owner_user_id=user_id):
                cancel_pending(
                    sender_id, d.get("dialog_id", ""),
                    owner_user_id=user_id)
        except Exception:
            pass
        import agent_runtime as _credential_runtime
        query_for_run, credential_meta, prepared_fields = (
            _credential_runtime.prepare_credentials_for_routing(query)
        )
        return None, _preprocessed_turn_data(
            query_for_run=query_for_run, immediate_msg=None,
            immediate_source="", immediate_turn_id="",
            actor=actor, user_id=user_id,
            conversation_id=conversation_id, sender_id=sender_id,
            reference_images=reference_images,
            original_query=safe_original_query,
            credential_meta=credential_meta,
            redacted_fields=prepared_fields,
        )

    # Dialog cancel intercept: se c'e' un dialog pending e l'utente scrive
    # "annulla"/"undo", cancella il dialog invece di routare a undo.
    _dialog_cancel_msg = _apply_dialog_cancel(
        sender_id, query, owner_user_id=user_id)
    if _dialog_cancel_msg is not None:
        return None, _preprocessed_turn_data(
            query_for_run=query, immediate_msg=_dialog_cancel_msg,
            immediate_source="pending", immediate_turn_id="",
            actor=actor, user_id=user_id,
            conversation_id=conversation_id, sender_id=sender_id,
            reference_images=reference_images,
            original_query=safe_original_query,
            redacted_fields=sensitive_fields,
        )

    # Closed-schema replies are unambiguous and must remain usable even when
    # the Tutor provider is unavailable. Open text gets the semantic help
    # chance first and therefore preserves its pending interaction.
    _closed_reply = _apply_dialog_pending(
        sender_id, query, actor=actor, channel="http",
        conversation_id=conversation_id or "",
        owner_user_id=user_id,
        admit_only_valid_closed=True,
    )
    if _closed_reply is not None:
        return None, _preprocessed_turn_data(
            query_for_run=query, immediate_msg=_closed_reply,
            immediate_source="pending", immediate_turn_id="",
            actor=actor, user_id=user_id,
            conversation_id=conversation_id, sender_id=sender_id,
            reference_images=reference_images,
            original_query=safe_original_query,
            redacted_fields=sensitive_fields,
        )

    if request.path == "/agent/turn/submit" and not sensitive_fields:
        # The chat endpoint is resumable: semantic admission and any slow
        # Tutor composition belong behind its 202 boundary.  EventSource can
        # then keep the connection alive and a proxy timeout cannot replace a
        # valid turn with an HTML 524 page.
        return None, _preprocessed_turn_data(
            query_for_run=safe_original_query, immediate_msg=None,
            immediate_source="", immediate_turn_id="",
            actor=actor, user_id=user_id,
            conversation_id=conversation_id, sender_id=sender_id,
            reference_images=reference_images,
            original_query=safe_original_query,
            redacted_fields=sensitive_fields,
            tutor_deferred=True, deferred_query=query,
        )

    return None, await _resolve_open_http_turn(
        request,
        query=query,
        safe_original_query=safe_original_query,
        sensitive_fields=sensitive_fields,
        actor=actor,
        user_id=user_id,
        conversation_id=conversation_id,
        sender_id=sender_id,
        reference_images=reference_images,
    )


async def turn(request: web.Request) -> web.Response:
    """POST /agent/turn

    Body shapes (alternativi, NON shim retro-compat — the design guide §7.1):
      - JSON `application/json`: `{ query: str, conversation_id?, actor? }`
      - Multipart `multipart/form-data` (ADR 0092): campo `query` (text) +
        N campi `image_<i>` (FileField, image/*) + opzionale
        `conversation_id`. Le immagini vengono salvate in
        `/tmp/metnos_uploads/<sender>/<turn-pre>_<idx>.jpg` e propagate a
        run_turn come `reference_images=[paths]`.

    Header Accept: text/event-stream → SSE; default → JSON.
    """
    err, data = await _preprocess_turn(request)
    if err is not None:
        return err
    query = data["original_query"]
    query_for_run = data["query_for_run"]
    immediate_msg = data["immediate_msg"]
    immediate_elapsed_ms = int(data.get("immediate_elapsed_ms") or 0)
    immediate_source = data.get("immediate_source") or "pending"
    immediate_turn_id = data.get("immediate_turn_id") or immediate_source
    actor = data["actor"]
    user_id = data["user_id"]
    conversation_id = data["conversation_id"]
    sender_id = data["sender_id"]
    reference_images = data["reference_images"]
    credential_meta = data.get("credential_meta") or []
    redacted_fields = int(data.get("redacted_fields") or 0)
    accept = request.headers.get("Accept", "")
    want_sse = "text/event-stream" in accept
    if immediate_msg is not None:
        admin_key = app_get(request.app, APP_ADMIN_KEY, "")
        immediate_http = _decorate_dialog_markers(immediate_msg, admin_key)
        if want_sse:
            # Stream un singolo evento `final` SSE-formattato cosi' il
            # client chat.html chiude pulito (niente "connessione interrotta").
            response = web.StreamResponse(
                status=200,
                headers={
                    "Content-Type": "text/event-stream",
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )
            await response.prepare(request)
            payload = {
                "turn_id": immediate_turn_id,
                "final_message": immediate_http,
                "final_message_html": _safe_final_html(immediate_http),
                "final_kind": "answer",
                "total_ms": immediate_elapsed_ms,
                "expandable_caps": [],
                "attachments": [],
                "gallery_url": None,
                "n_total_matches": 0,
                "path": [],
            }
            await response.write(
                f"event: final\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")
            )
            await response.write_eof()
            return response
        return web.json_response({
            "turn_id": immediate_turn_id,
            "final_message": immediate_http,
            "final_message_html": _safe_final_html(immediate_http),
            "final_kind": "answer",
            "total_ms": immediate_elapsed_ms,
            "steps_summary": [],
            "conversation_id": conversation_id,
            "expandable_caps": [],
        })
    original_query_for_pending = query  # quello digitato adesso

    # Lazy import: evita di costruire il catalog ad app start (tempo CI).
    import agent_runtime

    if want_sse:
        return await _turn_sse(request, agent_runtime, query_for_run,
                               actor, user_id, conversation_id, sender_id,
                               original_query_for_pending,
                               reference_images, credential_meta,
                               redacted_fields)
    return await _turn_json(request, agent_runtime, query_for_run, actor, user_id,
                            conversation_id, sender_id,
                            original_query_for_pending,
                            reference_images, credential_meta,
                            redacted_fields)


async def _turn_json(request: web.Request, agent_runtime, query: str, actor: str,
                     user_id: str, conv_id: str,
                     sender_id: str, original_for_pending: str,
                     reference_images: list[str] | None = None,
                     credential_meta: list[dict] | None = None,
                     redacted_fields: int = 0) -> web.Response:
    try:
        reservation = await _reserve_turn(request, actor)
    except TurnPoolBusy:
        return _turn_busy_response()
    refs = list(reference_images or [])
    log_obj = await _run_turn_reserved(
        request, reservation,
        lambda: agent_runtime.run_turn(
            query, actor=actor, channel="http",
            owner_user_id=user_id,
            conversation_id=conv_id,
            reference_images=refs or None,
            credential_meta=credential_meta,
            credentials_prepared=True,
            redacted_fields=redacted_fields,
        ),
    )
    _save_cap_pending_if_any(
        sender_id, original_for_pending, log_obj,
        owner_user_id=user_id)
    admin_key = app_get(request.app, APP_ADMIN_KEY, "")
    gallery_url, n_total = _gallery_url_for(log_obj)
    final_message = _decorate_dialog_markers(log_obj.final_message, admin_key)
    return web.json_response({
        "turn_id": log_obj.turn_id,
        "final_message": final_message,
        "final_message_html": _safe_final_html(final_message),
        "final_kind": log_obj.final_kind,
        # Destinazione risolta (ADR 0034): None = server, altrimenti nome device.
        "target_device": getattr(log_obj, "target_device", None),
        "total_ms": int((log_obj.ts_end - log_obj.ts_start) * 1000),
        "ts_end": float(log_obj.ts_end),  # epoch seconds, per close-time UI
        "steps_summary": [
            {"step": s.step_num, "tool": s.chosen_tool,
             "ok": bool(s.result and s.result.get("ok", True)) if isinstance(s.result, dict) else None,
             "error_class": s.result.get("error_class")
             if isinstance(s.result, dict) else None}
            for s in log_obj.steps
        ],
        "conversation_id": conv_id,
        "expandable_caps": getattr(log_obj, "expandable_caps", []) or [],
        "pending_decision": _pending_decision_payload(log_obj),
        "attachments": _enrich_attachments(log_obj, admin_key),
        "gallery_url": gallery_url,
        "n_total_matches": n_total,
    })


async def _turn_sse(request: web.Request, agent_runtime,
                    query: str, actor: str, user_id: str, conv_id: str,
                    sender_id: str, original_for_pending: str,
                    reference_images: list[str] | None = None,
                    credential_meta: list[dict] | None = None,
                    redacted_fields: int = 0) -> web.StreamResponse:
    try:
        reservation = await _reserve_turn(request, actor)
    except TurnPoolBusy:
        return _turn_busy_response()
    response = web.StreamResponse(
        status=200,
        reason="OK",
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
    await response.prepare(request)

    loop = asyncio.get_running_loop()
    progress = _SSEProgress(response, loop)

    # Registra la response per la chiusura pulita on_shutdown (Fix C).
    sse_set = app_setdefault(request.app, SSE_RESPONSES, set())
    sse_set.add(response)

    # Keepalive task: tiene viva la connessione TCP e fornisce al client
    # un segnale di "server vivo" (Fix A).
    ka_task = asyncio.create_task(_sse_keepalive_loop(response))

    # run_turn e' sync e blocca. Lo eseguiamo in executor; gli eventi
    # scorrono via il callback Progress.
    refs = list(reference_images or [])
    def _run_blocking():
        return agent_runtime.run_turn(
            query, actor=actor, channel="http", progress=progress,
            owner_user_id=user_id,
            conversation_id=conv_id,
            reference_images=refs or None,
            credential_meta=credential_meta,
            credentials_prepared=True,
            redacted_fields=redacted_fields,
        )

    try:
        log_obj = await _run_turn_reserved(request, reservation, _run_blocking)
        _save_cap_pending_if_any(
            sender_id, original_for_pending, log_obj,
            owner_user_id=user_id)
        admin_key = app_get(request.app, APP_ADMIN_KEY, "")
        await progress._emit(
            "final", _build_final_event_payload(log_obj, admin_key))
    except Exception as e:
        log.exception("turn SSE error")
        await progress._emit("error", {"message": str(e)})
    finally:
        ka_task.cancel()
        try:
            await ka_task
        except (asyncio.CancelledError, Exception):
            pass
        sse_set.discard(response)

    await response.write_eof()
    return response


# --- Dialog form routes (ADR 0090, get_inputs) -------------------------------
#
# Tre route per il rendering form unico HTTP di un dialogo `get_inputs`:
#   GET  /agent/dialog/<dialog_id>/form    — render dialogo come form HTML
#   POST /agent/dialog/<dialog_id>/submit  — riceve i campi, valida, marca
#                                             completed, ritorna conferma
#   GET  /agent/dialog/<dialog_id>/cancel  — cancella il dialogo, redirect /

def _resolve_dialog_state(request: web.Request, dialog_id: str) -> dict | None:
    """Cerca il dialogo in tutti i sender_dir noti. Il form HTTP non
    necessariamente conosce il sender al momento del GET (URL puo' essere
    aperto da un browser con sessione differente da quella che ha emesso
    get_inputs). Cerchiamo per dialog_id univoco."""
    import dialog_pending
    if not dialog_pending.valid_dialog_id(dialog_id):
        return None
    base = dialog_pending.DIALOG_DIR
    if not base.exists():
        return None
    for sender_dir in base.iterdir():
        if not sender_dir.is_dir():
            continue
        p = sender_dir / f"{dialog_id}.json"
        if p.exists():
            try:
                state = json.loads(p.read_text(encoding="utf-8"))
                state["__sender_id"] = sender_dir.name
                return state
            except (OSError, json.JSONDecodeError):
                continue
    return None


def _dialog_access_error(request: web.Request, dialog_id: str,
                         state: dict) -> web.Response | None:
    """Enforce immutable ownership or an explicit one-dialog capability."""
    admin_key = app_get(request.app, APP_ADMIN_KEY, "")
    cap = request.query.get("cap", "")
    if _dialog_cap_verify(dialog_id, cap, admin_key):
        return None

    owner_id = str(state.get("owner_user_id") or "")
    current_id = str(request.get("authenticated_user_id") or "")
    if owner_id and current_id and hmac.compare_digest(owner_id, current_id):
        return None

    return web.json_response(
        {"ok": False, "error": "dialog_forbidden"}, status=403,
        headers={"Cache-Control": "no-store",
                 "Referrer-Policy": "no-referrer"},
    )


def _dialog_cap_query(request: web.Request, dialog_id: str) -> str:
    """Propaga soltanto una capability valida nelle URL interne al form."""
    token = request.query.get("cap", "")
    admin_key = app_get(request.app, APP_ADMIN_KEY, "")
    return f"?cap={token}" if _dialog_cap_verify(
        dialog_id, token, admin_key) else ""


def _dialog_lifecycle(state: dict | None) -> str:
    """Stato canonico condiviso dalle route HTTP del dialogo."""
    if state is None:
        return "missing"
    if state.get("cancelled"):
        return "cancelled"
    if state.get("completed"):
        return "completed"
    import dialog_pending
    if dialog_pending.is_expired(state):
        return "expired"
    return "active"


def _dialog_terminal_response(dialog_id: str, state: str) -> web.Response:
    """Risposta HTML leggibile e strutturata per uno stato non azionabile."""
    if state == "cancelled":
        message = _msg("MSG_DIALOG_CANCELLED")
    elif state == "completed":
        message = _msg("MSG_ORCH_DIALOG_DONE")
    else:
        message = _msg("MSG_DIALOG_EXPIRED")
    import html as _html
    ui_lang = _html.escape(_i18n.current_lang(), quote=True)
    dialog_label = _html.escape(
        _msg("MSG_CHAT_DIALOG_ID_LABEL"), quote=False,
    )
    terminal_event = json.dumps({
        "type": "metnos.dialog.terminal",
        "state": state,
        "dialog_id": dialog_id,
    }, ensure_ascii=False)
    body = (
        f"<!doctype html><html lang=\"{ui_lang}\"><meta charset=utf-8>"
        f"<div data-dialog-state=\"{state}\">"
        f"<p>{_html.escape(message)}</p>"
        f"<p>{dialog_label} <code>{_html.escape(dialog_id)}</code></p>"
        "</div>"
        f"<script>parent.postMessage({terminal_event},'*');</script></html>"
    )
    return web.Response(
        text=body, status=410, content_type="text/html",
        headers={"Cache-Control": "no-store",
                 "Referrer-Policy": "no-referrer",
                 "X-Metnos-Dialog-State": state},
    )


def _resolve_i18n_str(s):
    """Risolve un codice MSG_* tramite messages.get; passthrough se non e' una key.
    Inserito perche' `_needs_inputs_oauth_setup` e altri builder lasciano
    le chiavi grezze nel payload (ADR 0090 + ADR 0123): la risoluzione
    avviene al boundary del canale, mantenendo lo state lingua-agnostico."""
    if isinstance(s, str) and s.startswith("MSG_"):
        try:
            from messages import get as _msg
            return _msg(s)
        except Exception:
            return s
    return s


def _resolve_i18n_step(step):
    """Risolve `prompt` (e `description` se presente) di un dialog step."""
    if not isinstance(step, dict):
        return step
    out = dict(step)
    if "prompt" in out:
        out["prompt"] = _resolve_i18n_str(out.get("prompt"))
    if "description" in out:
        out["description"] = _resolve_i18n_str(out.get("description"))
    return out


async def dialog_form(request: web.Request) -> web.Response:
    """GET /agent/dialog/<dialog_id>/form — render del form HTML.

    Per dialog gia' completati o cancellati (caso live 18/5/2026: utente
    torna alla chat dopo OAuth → iframe ricarica il form → dialog finito),
    emette un HTML minimal che fa postMessage al parent perche' rimuova
    l'iframe dalla bubble. Tipo coerente con dialog_form.html (events
    `metnos.dialog.done` / `metnos.dialog.cancelled`).
    """
    dialog_id = request.match_info["dialog_id"]
    state = _resolve_dialog_state(request, dialog_id)
    if state is None:
        return _error(404, "dialog_not_found",
                      _msg("ERR_DIALOG_NOT_FOUND"))
    access_error = _dialog_access_error(request, dialog_id, state)
    if access_error is not None:
        return access_error
    lifecycle = _dialog_lifecycle(state)
    if lifecycle != "active":
        return _dialog_terminal_response(dialog_id, lifecycle)
    dialog_steps = [_resolve_i18n_step(s) for s in (state.get("dialog") or [])]
    html = render_template(
        "dialog_form.html",
        dialog_id=dialog_id,
        origin_turn_id=state.get("origin_turn_id") or "",
        title=_resolve_i18n_str(
            state.get("title") or _msg("MSG_CHAT_DIALOG_DEFAULT_TITLE")
        ),
        description=_resolve_i18n_str(state.get("description") or ""),
        expired_message=_resolve_i18n_str("MSG_DIALOG_EXPIRED"),
        dialog=dialog_steps,
        dialog_cap_query=_dialog_cap_query(request, dialog_id),
        role=request.get("role", "user"),
    )
    return web.Response(
        text=html, content_type="text/html",
        headers={"Cache-Control": "no-store",
                 "Referrer-Policy": "no-referrer"},
    )


async def dialog_submit(request: web.Request) -> web.Response:
    """POST /agent/dialog/<dialog_id>/submit — riceve i form fields.

    Validation deterministica: chiama il parser per ogni step, accumula
    errori, marca tutti i values e completa il dialogo se OK. Se ci sono
    errori, ri-render il form con i messaggi.
    """
    dialog_id = request.match_info["dialog_id"]
    state = _resolve_dialog_state(request, dialog_id)
    if state is None:
        return _error(404, "dialog_not_found",
                      _msg("ERR_DIALOG_NOT_FOUND"))
    access_error = _dialog_access_error(request, dialog_id, state)
    if access_error is not None:
        return access_error
    lifecycle = _dialog_lifecycle(state)
    if lifecycle != "active":
        return web.json_response(
            {"ok": False, "error": "dialog_not_active",
             "message": _msg("MSG_DIALOG_EXPIRED"),
             "dialog_id": dialog_id, "state": lifecycle},
            status=410,
            headers={"Cache-Control": "no-store",
                     "X-Metnos-Dialog-State": lifecycle},
        )
    try:
        form = await request.post()
    except Exception:
        return _error(
            400, "invalid_form", _msg("ERR_CHAT_DIALOG_INVALID_FORM"),
        )

    # Single source of truth: stesso parser del channel daemon.
    # Rename-resilient (ADR 0148): risolve la runtime dir da __file__.
    import sys as _sys
    _runtime_dir = str(Path(__file__).resolve().parent)
    if _runtime_dir not in _sys.path:
        _sys.path.insert(0, _runtime_dir)
    from channels.daemon import parse_step_value
    dialog = state.get("dialog") or []
    values = {}
    errors = []
    for step in dialog:
        var = step.get("var")
        kind = (step.get("schema") or {}).get("kind")
        if kind == "yes_no":
            raw = form.get(var) or ""
        elif kind == "multi_choice":
            picks = form.getall(var) if hasattr(form, "getall") else []
            raw = ",".join(picks)
        elif kind == "location":
            lat = form.get(f"{var}__lat") or ""
            lon = form.get(f"{var}__lon") or ""
            raw = f"{lat},{lon}".strip(",")
        else:
            raw = form.get(var) or ""
        if not raw and step.get("optional"):
            values[var] = None
            continue
        ok, value, err = parse_step_value(raw, step.get("schema") or {})
        if not ok:
            errors.append({"var": var, "error": err})
            continue
        values[var] = value
    if errors:
        # Re-render con errori in topbar (template minimal: usiamo plain
        # HTML response per non complicare il template).
        msgs = _escape_html(
            "; ".join(f"{e['var']}: {e['error']}" for e in errors)
        )
        cap_query = _dialog_cap_query(request, dialog_id)
        validation_title = _escape_html(
            _msg("MSG_CHAT_DIALOG_VALIDATION_TITLE")
        )
        back_label = _escape_html(_msg("MSG_CHAT_DIALOG_BACK"))
        return web.Response(
            text=f"<h2>{validation_title}</h2><p>{msgs}</p>"
                 f"<p><a href=\"/agent/dialog/{dialog_id}/form{cap_query}\">"
                 f"{back_label}</a></p>",
            status=400, content_type="text/html",
            headers={"Cache-Control": "no-store",
                     "Referrer-Policy": "no-referrer"},
        )
    # Tutti i campi OK: marca completato applicando consume_pending_step
    # in sequenza (single source of truth: stesso storage dei dialoghi
    # incrementali, niente bypass).
    import dialog_pending
    sender_id = state.get("__sender_id") or "host"
    for step in dialog:
        var = step.get("var")
        if var in values and values[var] is not None:
            consumed = dialog_pending.consume_pending_step(
                sender_id, dialog_id, var, values[var],
                owner_user_id=str(state.get("owner_user_id") or ""),
            )
        else:
            # Optional skipped: avanza con None per coerenza idx.
            consumed = dialog_pending.consume_pending_step(
                sender_id, dialog_id, var, None,
                owner_user_id=str(state.get("owner_user_id") or ""),
            )
        if not consumed.get("ok"):
            return web.json_response(
                {"ok": False, "error": consumed.get("error") or "dialog_conflict",
                 "dialog_id": dialog_id, "state": "conflict"},
                status=409,
                headers={"Cache-Control": "no-store",
                         "X-Metnos-Dialog-State": "conflict"},
            )

    # ADR 0091: dopo aver consumato tutti gli step, processa il callback
    # `on_complete` se presente nel state (es. save_credentials_and_resume).
    # process_completion_callback ritorna sempre un messaggio user-facing.
    final_state = dialog_pending.load_pending(
        sender_id, dialog_id,
        owner_user_id=str(state.get("owner_user_id") or "")) or {}
    on_complete = final_state.get("on_complete")
    actor = final_state.get("actor") or "host"
    # turn_id del turno che ha emesso il dialog → la bolla risultato in chat
    # riaggancia i badge feedback ✓/✗ (chat.html li mostra solo con turn_id).
    origin_turn_id = final_state.get("origin_turn_id") or ""
    completion_message = ""
    completion_attachments = []
    completion_meta = {}
    if on_complete:
        try:
            # Usa lo scheme attestato dal collegamento diretto o da un reverse
            # proxy fidato per costruire l'origine restituita al browser.
            from http_auth import external_request_scheme
            xfp = external_request_scheme(request)
            origin_override = f"{xfp}://{request.host}"
            from orchestration import process_completion_callback
            _cr = process_completion_callback(
                sender_id, dialog_id, actor=actor, channel="http",
                owner_user_id=str(final_state.get("owner_user_id") or ""),
                host_override=origin_override,
            )
            completion_message = _cr.text
            # Bug zip-line (5/7): il resume full-turn porta attachments e meta
            # del NUOVO turno — la bolla in chat deve avere gallery + status
            # line + badge sul turno REALE (non solo testo nudo).
            completion_attachments = []
            if _cr.attachments:
                try:
                    from types import SimpleNamespace as _SN
                    completion_attachments = _enrich_attachments(
                        _SN(attachments=_cr.attachments,
                            turn_id=_cr.turn_id or origin_turn_id or ""),
                        app_get(request.app, APP_ADMIN_KEY, ""))
                except Exception as _ea:
                    log.warning("dialog_submit: enrich attachments noop: %r", _ea)
            completion_meta = {
                "turn_id": _cr.turn_id or "",
                "total_ms": _cr.total_ms or 0,
                "target_device": _cr.target_device or "",
                "gallery_url": _cr.gallery_url or "",
                "n_total_matches": _cr.n_total_matches or 0,
                "path": _cr.path or [],
            }
        except (ImportError, RuntimeError) as ex:
            log.exception("dialog_submit: process_completion_callback fallito")
            completion_message = _msg(
                "ERR_CHAT_DIALOG_CALLBACK_FAILED",
                error=f"{type(ex).__name__}: {ex}",
            )

    if completion_message:
        # Marker strutturato `__REDIRECT__:<url>\n<msg>` (core, general):
        # il callback chiede una browser navigation diretta. Estraiamo
        # l'URL, includiamo `data-redirect-url` cosi' che lo script JS
        # dell'iframe possa propagarlo al parent (chat.html) per top-level
        # navigation. Fallback link cliccabile per canali senza JS.
        redirect_url = ""
        msg_for_display = completion_message
        if completion_message.startswith("__REDIRECT__:"):
            head, _, rest = completion_message.partition("\n")
            redirect_url = head[len("__REDIRECT__:"):].strip()
            msg_for_display = rest or msg_for_display
        if redirect_url:
            esc_url = redirect_url.replace('"', "&quot;")
            setup_started = _escape_html(
                _msg("MSG_CHAT_DIALOG_SETUP_STARTED")
            )
            auth_opening = _escape_html(
                _msg("MSG_CHAT_DIALOG_AUTH_OPENING")
            )
            open_manually = _escape_html(
                _msg("MSG_CHAT_DIALOG_OPEN_MANUALLY")
            )
            auth_fallback = _escape_html(
                _msg("MSG_CHAT_DIALOG_AUTH_FALLBACK_SUFFIX")
            )
            body_html = (
                f"<div data-redirect-url=\"{esc_url}\">"
                f"<h2>{setup_started}</h2>"
                f"<p>{auth_opening}</p>"
                f"<p><a href=\"{esc_url}\">{open_manually}</a>"
                f"{auth_fallback}</p>"
                "</div>"
            )
        else:
            # Errore o messaggio testuale dal callback: includi data-completion-text
            # cosi' il parent (chat.html) puo' mostrarlo come bolla regolare in
            # chat invece del laconico "Risposta dialog inviata".
            esc_text = _escape_html(msg_for_display)
            # Badge sul turno REALE del resume quando c'è (feedback ✓/✗ sul
            # risultato); fallback al turno che ha emesso il form.
            _tid = (completion_meta.get("turn_id") or origin_turn_id or "")
            esc_tid = _tid.replace('"', "&quot;")
            import base64 as _b64
            import json as _json
            _att_b64 = ""
            if completion_attachments:
                _att_b64 = _b64.b64encode(_json.dumps(
                    completion_attachments, ensure_ascii=False,
                    default=str).encode("utf-8")).decode("ascii")
            _meta_b64 = _b64.b64encode(_json.dumps(
                completion_meta, ensure_ascii=False).encode("utf-8")
                ).decode("ascii") if completion_meta else ""
            completed_title = _escape_html(
                _msg("MSG_CHAT_DIALOG_COMPLETED_TITLE")
            )
            return_label = _escape_html(_msg("MSG_CHAT_DIALOG_RETURN"))
            body_html = (
                f"<div data-completion-text=\"{esc_text}\" "
                f"data-turn-id=\"{esc_tid}\" "
                f"data-attachments-b64=\"{_att_b64}\" "
                f"data-turn-meta-b64=\"{_meta_b64}\">"
                f"<h2>{completed_title}</h2>"
                f"<pre>{esc_text}</pre>"
                f"<p><a href=\"/\">{return_label}</a></p>"
                "</div>"
            )
    else:
        completed_title = _escape_html(
            _msg("MSG_CHAT_DIALOG_COMPLETED_TITLE")
        )
        values_saved = _escape_html(_msg("MSG_ORCH_DIALOG_DONE"))
        return_label = _escape_html(_msg("MSG_CHAT_DIALOG_RETURN"))
        body_html = (
            f"<h2>{completed_title}</h2>"
            f"<p>{values_saved} "
            f"<a href=\"/\">{return_label}</a></p>"
        )
    return web.Response(
        text=body_html, content_type="text/html",
        headers={"Cache-Control": "no-store",
                 "Referrer-Policy": "no-referrer"},
    )


def _escape_html(text: str) -> str:
    """Escape minimo per output testuale in completion page."""
    if not text:
        return ""
    return (text.replace("&", "&amp;")
                 .replace("<", "&lt;")
                 .replace(">", "&gt;"))


async def dialog_preview(request: web.Request) -> web.Response:
    """GET /agent/dialog/<dialog_id>/preview/<step_idx>/<option_idx> —
    JPEG miniatura di una opzione `choice_with_preview` (PR5).

    Identifica lo step ESPLICITAMENTE via URL (`step_idx`) anziche' via
    `state.step_index`: il form HTTP renderizza tutti gli step in una
    sola pagina, quindi `state.step_index` (corrente) sarebbe sempre 0
    e tutti gli step mostrerebbero le opzioni dello step 0 (10/5/2026
    bug live). Backwards-compat: percorso senza step_idx assume step 0.

    Sicurezza: il path della miniatura e' validato contro un set chiuso
    di root consentiti (ADR 0090 estensione, anti path-traversal §2.8).
    Risposta cached 5 min (`max-age=300`).
    """
    dialog_id = request.match_info["dialog_id"]
    try:
        opt_idx = int(request.match_info["option_idx"])
    except (TypeError, ValueError):
        return _error(400, "invalid_idx", "option_idx must be integer")
    # step_idx opzionale: se mancante, assumiamo step 0 (single-step legacy).
    step_idx_raw = request.match_info.get("step_idx")
    if step_idx_raw is None:
        step_idx = 0
    else:
        try:
            step_idx = int(step_idx_raw)
        except (TypeError, ValueError):
            return _error(400, "invalid_step_idx",
                          "step_idx must be integer")
    state = _resolve_dialog_state(request, dialog_id)
    if state is None:
        return _error(404, "dialog_not_found",
                      _msg("ERR_DIALOG_NOT_FOUND"))
    access_error = _dialog_access_error(request, dialog_id, state)
    if access_error is not None:
        return access_error
    dialog = state.get("dialog") or []
    if step_idx < 0 or step_idx >= len(dialog):
        return _error(404, "step_out_of_range",
                      f"step {step_idx} not in 0..{len(dialog)-1}")
    cur_step = dialog[step_idx]
    if (cur_step.get("schema") or {}).get("kind") != "choice_with_preview":
        return _error(404, "not_preview_step",
                      f"step {step_idx} is not choice_with_preview")
    options = (cur_step.get("schema") or {}).get("options") or []
    if opt_idx < 0 or opt_idx >= len(options):
        return _error(404, "option_out_of_range",
                      f"option {opt_idx} not in 0..{len(options)-1}")
    spec = options[opt_idx].get("preview_image_path") or ""
    # runtime/ già su sys.path (http_routes_agent VIVE in runtime/).
    import dialog_preview as _dpv
    try:
        path, bbox = _dpv.validate_preview_spec(spec, require_exists=True)
    except ValueError as ex:
        # Path-traversal o file mancante: 403 sul traversal, 404 altrimenti.
        msg = str(ex)
        if "fuori dai root" in msg:
            return _error(403, "preview_forbidden", msg)
        return _error(404, "preview_not_found", msg)
    try:
        body = _dpv.crop_image_bytes(path, bbox)
    except (OSError, ValueError) as ex:
        return _error(500, "preview_render_failed", str(ex))
    return web.Response(
        body=body,
        content_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=300",
                 "Referrer-Policy": "no-referrer"},
    )


async def dialog_context(request: web.Request) -> web.Response:
    """GET /agent/dialog/<dialog_id>/context/<step_idx> — JPEG dell'intera
    foto riferita allo step `step_idx`.

    Identifica lo step ESPLICITAMENTE via URL: il form HTTP renderizza
    tutti gli step in una sola pagina, quindi `state.step_index`
    sarebbe sempre 0 e tutti gli step mostrerebbero la stessa foto
    (10/5/2026 bug live). Backwards-compat: percorso senza step_idx
    assume step 0.

    Sicurezza: stesso validatore di `dialog_preview`. Cached 5min.
    """
    dialog_id = request.match_info["dialog_id"]
    step_idx_raw = request.match_info.get("step_idx")
    if step_idx_raw is None:
        step_idx = 0
    else:
        try:
            step_idx = int(step_idx_raw)
        except (TypeError, ValueError):
            return _error(400, "invalid_step_idx",
                          "step_idx must be integer")
    state = _resolve_dialog_state(request, dialog_id)
    if state is None:
        return _error(404, "dialog_not_found",
                      _msg("ERR_DIALOG_NOT_FOUND"))
    access_error = _dialog_access_error(request, dialog_id, state)
    if access_error is not None:
        return access_error
    dialog = state.get("dialog") or []
    if step_idx < 0 or step_idx >= len(dialog):
        return _error(404, "step_out_of_range",
                      f"step {step_idx} not in 0..{len(dialog)-1}")
    cur_step = dialog[step_idx]
    spec = (cur_step.get("schema") or {}).get("context_image_path") or ""
    if not spec:
        return _error(404, "no_context_image",
                      "step has no context_image_path")
    # runtime/ già su sys.path (http_routes_agent VIVE in runtime/).
    import dialog_preview as _dpv
    try:
        path, _ = _dpv.validate_preview_spec(spec, require_exists=True)
    except ValueError as ex:
        msg = str(ex)
        if "fuori dai root" in msg:
            return _error(403, "context_forbidden", msg)
        return _error(404, "context_not_found", msg)
    try:
        body = _dpv.crop_image_bytes(path, None, max_dim=480)
    except (OSError, ValueError) as ex:
        return _error(500, "context_render_failed", str(ex))
    return web.Response(
        body=body,
        content_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=300",
                 "Referrer-Policy": "no-referrer"},
    )


async def dialog_cancel(request: web.Request) -> web.Response:
    """GET /agent/dialog/<dialog_id>/cancel — cancella il dialogo."""
    dialog_id = request.match_info["dialog_id"]
    state = _resolve_dialog_state(request, dialog_id)
    if state is None:
        return _error(404, "dialog_not_found",
                      _msg("ERR_DIALOG_NOT_FOUND"))
    access_error = _dialog_access_error(request, dialog_id, state)
    if access_error is not None:
        return access_error
    lifecycle = _dialog_lifecycle(state)
    if lifecycle != "active":
        return _dialog_terminal_response(dialog_id, lifecycle)
    sender_id = state.get("__sender_id") or "host"
    import dialog_pending
    dialog_pending.cancel_pending(
        sender_id, dialog_id,
        owner_user_id=str(state.get("owner_user_id") or ""))
    cancelled = _escape_html(_msg("MSG_DIALOG_CANCELLED"))
    return_label = _escape_html(_msg("MSG_CHAT_DIALOG_RETURN"))
    return web.Response(
        text=(f"<h2>{cancelled}</h2>"
              f"<p><a href=\"/\">{return_label}</a></p>"),
        content_type="text/html",
        headers={"Cache-Control": "no-store",
                 "Referrer-Policy": "no-referrer",
                 "X-Metnos-Dialog-State": "cancelled"},
    )



# --- Photo thumbnail serving (Opzione 1, 5/5/2026) -------------------------
#
# GET /agent/photos/web?u=<url> — proxy fetch per immagini esterne (Vision API
# results). Bypassa hotlinking-block dei CDN (TikTok, Instagram, Facebook
# lookaside) usando UA Mozilla + Referer locale. Cache disk.
_WEB_PHOTO_CACHE_DIR = Path.home() / ".cache" / "metnos" / "web_photos"
_WEB_PHOTO_CACHE_TTL_S = 7 * 24 * 3600
_WEB_PHOTO_CAP_TTL_S = 24 * 3600
_WEB_PHOTO_MAX_BYTES = 5 * 1024 * 1024

# SSRF guard: l'endpoint e' anonimo (whitelist `/agent/photos/` in
# http_auth.py), quindi un URL fornito dall'utente NON deve poter colpire
# servizi interni. Rifiutiamo ogni host che risolve a IP loopback/privato/
# link-local/riservato (es. 127.0.0.1, 169.254.169.254 metadata, 10/8, ...).
_WEB_PHOTO_MAX_REDIRECTS = 4


def _web_photo_sign(raw_url: str, exp: int, admin_key: str) -> str:
    payload = f"web-photo\n{int(exp)}\n{raw_url}".encode("utf-8")
    return hmac.new(admin_key.encode("utf-8"), payload,
                    hashlib.sha256).hexdigest()


def _web_photo_verify(raw_url: str, exp: int, token: str,
                      admin_key: str) -> bool:
    if not admin_key or not token or int(exp) < int(time.time()):
        return False
    expected = _web_photo_sign(raw_url, exp, admin_key)
    return hmac.compare_digest(token, expected)


def _web_photo_proxy_url(raw_url: str, admin_key: str) -> str:
    """Capability URL automatica: nessun prompt aggiuntivo per l'utente."""
    if not admin_key:
        return raw_url
    exp = int(time.time()) + _WEB_PHOTO_CAP_TTL_S
    return "/agent/photos/web?" + urllib.parse.urlencode({
        "u": raw_url,
        "exp": str(exp),
        "t": _web_photo_sign(raw_url, exp, admin_key),
    })


def _ip_is_blocked(ip_str: str) -> bool:
    """True se l'IP NON e' un indirizzo pubblico instradabile.

    Blocca loopback, link-local (incl. 169.254.169.254 cloud-metadata),
    privati (RFC1918 / ULA), multicast, riservati, unspecified. Consente
    solo IP global-scope. Su parse-error → blocca (fail-closed)."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_multicast or ip.is_reserved or ip.is_unspecified
        or not ip.is_global
    )


def _resolve_public_host(host: str) -> tuple[str | None, str | None]:
    """Risolve `host` e ritorna `(error, error_detail)`.

    Ritorna `(None, None)` se TUTTI gli indirizzi risolti sono pubblici.
    Ritorna `("blocked"|"resolve_failed", detail)` altrimenti. Controlliamo
    OGNI record (un host puo' risolvere a piu' IP, alcuni interni)."""
    if not host:
        return ("blocked", "empty host")
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError, OSError) as ex:
        return ("resolve_failed", f"{type(ex).__name__}: {ex}")
    if not infos:
        return ("resolve_failed", "no address")
    for info in infos:
        ip_str = info[4][0]
        if _ip_is_blocked(ip_str):
            return ("blocked", f"{host} -> {ip_str} not public")
    return (None, None)


def _resolve_public_addresses(host: str, port: int) -> tuple[
        str | None, str | None, list[tuple[str, int]]]:
    """Resolve una volta e restituisce gli IP che la connessione deve usare."""
    if not host:
        return "blocked", "empty host", []
    try:
        infos = socket.getaddrinfo(
            host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError) as ex:
        return "resolve_failed", f"{type(ex).__name__}: {ex}", []
    addresses: list[tuple[str, int]] = []
    seen = set()
    for family, _socktype, _proto, _canon, sockaddr in infos:
        ip_str = sockaddr[0]
        if _ip_is_blocked(ip_str):
            return "blocked", f"{host} -> {ip_str} not public", []
        key = (ip_str, family)
        if key not in seen:
            seen.add(key)
            addresses.append(key)
    if not addresses:
        return "resolve_failed", "no address", []
    return None, None, addresses


class _PinnedResolver:
    """aiohttp resolver che impedisce una seconda risoluzione DNS."""

    def __init__(self, addresses: list[tuple[str, int]]):
        self._addresses = list(addresses)

    async def resolve(self, host, port=0, family=socket.AF_UNSPEC):
        return [
            {"hostname": host, "host": ip, "port": port,
             "family": address_family, "proto": 0, "flags": 0}
            for ip, address_family in self._addresses
            if family in (socket.AF_UNSPEC, address_family)
        ]

    async def close(self):
        return None


class _ImageTooLarge(ValueError):
    pass


async def _read_web_photo_limited(content) -> bytes:
    body = bytearray()
    async for chunk in content.iter_chunked(64 * 1024):
        if len(body) + len(chunk) > _WEB_PHOTO_MAX_BYTES:
            raise _ImageTooLarge
        body.extend(chunk)
    return bytes(body)


def _validate_fetch_url(raw_url: str) -> tuple[str | None, str | None]:
    """Valida schema (http/https) + host pubblico per un URL da proxare.

    Ritorna `(error_code, detail)`; `(None, None)` se l'URL e' fetchabile."""
    try:
        parsed = urllib.parse.urlsplit(raw_url)
    except ValueError as ex:
        return ("invalid_url", str(ex))
    if parsed.scheme not in ("http", "https"):
        return ("invalid_url", "scheme must be http or https")
    if not parsed.hostname:
        return ("invalid_url", "missing host")
    return _resolve_public_host(parsed.hostname)


async def photo_web_proxy(request: web.Request) -> web.Response:
    """GET /agent/photos/web?u=<url> — fetch + cache + serve.

    §7.3 universal: rimuove hotlinking-block per attachment con URL esterni
    (Vision similar_images). Header UA Mozilla; timeout 8s; cache disk 7d.
    """
    raw_url = request.query.get("u", "")
    if not raw_url or not (raw_url.startswith("http://") or raw_url.startswith("https://")):
        return _error(400, "invalid_url", "u must be http(s) URL")
    try:
        exp = int(request.query.get("exp", "0"))
    except (TypeError, ValueError):
        return _error(401, "invalid_token", "invalid capability expiry")
    admin_key = app_get(request.app, APP_ADMIN_KEY, "")
    if not _web_photo_verify(
            raw_url, exp, request.query.get("t", ""), admin_key):
        return _error(401, "invalid_token", "capability invalid or expired")
    import hashlib as _hl
    key = _hl.sha256(raw_url.encode()).hexdigest()
    _WEB_PHOTO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = _WEB_PHOTO_CACHE_DIR / f"{key[:2]}" / f"{key}.bin"
    if cache_path.is_file():
        try:
            age = time.time() - cache_path.stat().st_mtime
            if age < _WEB_PHOTO_CACHE_TTL_S:
                data = cache_path.read_bytes()
                ctype = "image/jpeg"
                if data[:8].startswith(b"\x89PNG"):
                    ctype = "image/png"
                elif data[:6] in (b"GIF87a", b"GIF89a"):
                    ctype = "image/gif"
                elif data[:4] == b"RIFF":
                    ctype = "image/webp"
                return web.Response(body=data, content_type=ctype,
                                     headers={"Cache-Control": "public, max-age=86400",
                                              "Referrer-Policy": "no-referrer"})
        except OSError:
            pass
    # Fetch fresh. Ogni hop viene risolto una volta e quegli IP sono iniettati
    # nel connector: hostname, Host e TLS SNI restano quelli originali, ma un
    # DNS rebinding fra validazione e connect non puo' cambiare destinazione.
    import aiohttp
    cur_url = raw_url
    data = b""
    ctype = "image/jpeg"
    try:
        for _hop in range(_WEB_PHOTO_MAX_REDIRECTS + 1):
            parsed = urllib.parse.urlsplit(cur_url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                return _error(400, "invalid_url", "invalid redirect URL")
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            rerr, rdetail, addresses = await asyncio.to_thread(
                _resolve_public_addresses, parsed.hostname, port)
            if rerr:
                code = "blocked_redirect" if _hop else rerr
                status = 400 if rerr in ("invalid_url", "blocked") else 502
                return _error(status, code, rdetail or rerr)
            connector = aiohttp.TCPConnector(
                resolver=_PinnedResolver(addresses), use_dns_cache=False)
            timeout = aiohttp.ClientTimeout(total=8)
            async with aiohttp.ClientSession(
                    connector=connector, timeout=timeout,
                    headers={
                        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) "
                                      "AppleWebKit/537.36",
                        "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
                    }) as session:
                async with session.get(cur_url, allow_redirects=False) as resp:
                    if resp.status in (301, 302, 303, 307, 308):
                        loc = resp.headers.get("Location", "")
                        cur_url = urllib.parse.urljoin(cur_url, loc)
                        continue
                    if resp.status < 200 or resp.status >= 300:
                        return _error(404, "fetch_failed",
                                      f"upstream HTTP {resp.status}")
                    ctype = resp.headers.get(
                        "Content-Type", "image/jpeg").split(";", 1)[0].strip()
                    if not ctype.startswith("image/"):
                        return _error(404, "not_image",
                                      f"content-type {ctype} not image")
                    try:
                        content_length = int(
                            resp.headers.get("Content-Length", "0") or 0)
                    except ValueError:
                        content_length = 0
                    if content_length > _WEB_PHOTO_MAX_BYTES:
                        return _error(413, "image_too_large",
                                      "image exceeds proxy limit")
                    try:
                        data = await _read_web_photo_limited(resp.content)
                    except _ImageTooLarge:
                        return _error(413, "image_too_large",
                                      "image exceeds proxy limit")
                    break
        else:
            return _error(400, "too_many_redirects", "redirect limit exceeded")
    except (aiohttp.ClientError, OSError, TimeoutError, asyncio.TimeoutError) as ex:
        return _error(404, "fetch_failed", f"{type(ex).__name__}: {ex}")
    if len(data) <= _WEB_PHOTO_MAX_BYTES:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(data)
        except OSError:
            pass
    if not ctype.startswith("image/"):
        # Non un'immagine reale (es. HTML login page)
        return _error(404, "not_image", f"content-type {ctype} not image")
    return web.Response(body=data, content_type=ctype,
                         headers={"Cache-Control": "public, max-age=86400",
                                  "Referrer-Policy": "no-referrer"})


# GET /agent/photos/<turn_id>/<idx>?size=thumb|full&exp=<ts>&t=<sig>
# Auth: signed HMAC token nel querystring (TTL 24h). Whitelist anonymous
# in http_auth.py: l URL stesso fa da capability.

async def photo_serve(request: web.Request) -> web.Response:
    """GET /agent/photos/<turn_id>/<idx> — thumbnail JPEG signed-URL."""
    import photo_endpoint
    turn_id = request.match_info["turn_id"]
    try:
        idx = int(request.match_info["idx"])
    except (TypeError, ValueError):
        return _error(400, "invalid_idx", "idx must be integer")
    size = request.query.get("size", "thumb")
    try:
        exp = int(request.query.get("exp", "0"))
    except (TypeError, ValueError):
        return _error(400, "invalid_exp", "exp must be integer")
    token = request.query.get("t", "")
    admin_key = app_get(request.app, APP_ADMIN_KEY, "")
    if not admin_key:
        return _error(500, "no_admin_key", "server admin key not configured")
    if not photo_endpoint.verify(turn_id, idx, size, exp, token, admin_key):
        return _error(401, "invalid_token", "signed token invalid or expired")
    src_path = photo_endpoint.resolve_path(turn_id, idx)
    if not src_path:
        return _error(404, "not_found", "photo not found in recent turns")
    # size="file": consegna RAW del deliverable (xlsx/doc/zip/pdf) come
    # download, niente thumbnail. Content-Disposition: attachment. Bug 5303699e.
    if size == "file":
        import mimetypes
        from pathlib import Path as _P
        fp = _P(src_path)
        if not fp.is_file():
            return _error(404, "not_found", "file not found")
        try:
            body = fp.read_bytes()
        except OSError as e:
            return _error(500, "read_error", f"file read failed: {e}")
        ctype = mimetypes.guess_type(src_path)[0] or "application/octet-stream"
        safe_name = fp.name.replace('"', "")
        return web.Response(body=body, content_type=ctype, headers={
            "Content-Disposition": f'attachment; filename="{safe_name}"',
            "Cache-Control": "private, max-age=86400"})
    thumb = photo_endpoint.get_or_make_thumb(src_path, size)
    if not thumb:
        return _error(415, "not_an_image", "source path is not a readable image")
    try:
        body = thumb.read_bytes()
    except OSError as e:
        return _error(500, "read_error", f"cache read failed: {e}")
    return web.Response(
        body=body,
        content_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )


GALLERY_PAGE_SIZE = 60


def _attachments_from_record(rec: dict) -> list[dict]:
    """Estrae la lista cumulativa di attachments dal record di un turno.
    L ultimo step che ha emesso `attachments` vince (use case realistico:
    un solo `find_images_indices` per turno). Specchio della logica in
    agent_runtime.py:TurnLog.finalize per non dover deserializzare un
    TurnLog completo qui."""
    if not isinstance(rec, dict):
        return []
    for step in reversed(rec.get("steps") or []):
        result = step.get("result") if isinstance(step, dict) else None
        if isinstance(result, dict):
            atts = result.get("attachments")
            if isinstance(atts, list) and atts:
                return atts
    return []


def _user_query_from_record(rec: dict) -> str:
    """Restituisce la query utente del turno (per header gallery). Vuota
    se non disponibile o redatta."""
    if not isinstance(rec, dict):
        return ""
    q = rec.get("user_query")
    return q if isinstance(q, str) else ""


async def gallery(request: web.Request) -> web.Response:
    """GET /agent/gallery/<turn_id>?from=<int>

    Pagina HTML dedicata che mostra TUTTE le foto matched per il turno.
    Auth: middleware http_auth (user/admin); anonymous redirige al login
    perche' il path NON e' in ANON_WHITELIST_PREFIXES.
    """
    role = request.get("role", "anonymous")
    if role == "anonymous":
        raise web.HTTPFound("/admin/login")
    turn_id = request.match_info["turn_id"]
    import photo_endpoint
    rec = photo_endpoint.resolve_turn_record(turn_id)
    if rec is None:
        return _error(404, "turn_not_found",
                      f"turn {turn_id} non trovato negli ultimi giorni")
    access_error = await _turn_access_error(request, rec)
    if access_error is not None:
        return access_error
    atts = _attachments_from_record(rec)
    n_total = len(atts)
    try:
        from_idx = int(request.query.get("from", "0"))
    except (TypeError, ValueError):
        from_idx = 0
    if from_idx < 0:
        from_idx = 0
    if from_idx >= n_total and n_total > 0:
        from_idx = max(0, ((n_total - 1) // GALLERY_PAGE_SIZE) * GALLERY_PAGE_SIZE)
    end_idx = min(from_idx + GALLERY_PAGE_SIZE, n_total)
    admin_key = app_get(request.app, APP_ADMIN_KEY, "")

    items = []
    for offset in range(from_idx, end_idx):
        att = atts[offset] if offset < n_total else None
        if not isinstance(att, dict):
            continue
        score = att.get("score")
        items.append({
            "thumb_url": photo_endpoint.make_url(turn_id, offset, "thumb", admin_key),
            "full_url":  photo_endpoint.make_url(turn_id, offset, "full",  admin_key),
            "basename":  att.get("basename") or "",
            "score":     score if isinstance(score, (int, float)) else None,
            "caption":   att.get("caption") or "",
            # Data foto (Roberto 6/7): EXIF taken_at dall'indice, solo la
            # parte giorno per l'header compatto della card.
            "date":      (str(att.get("date"))[:10] if att.get("date") else ""),
        })

    has_prev = from_idx > 0
    has_next = end_idx < n_total
    html = render_template(
        "gallery.html",
        turn_id=turn_id,
        user_query=_user_query_from_record(rec),
        n_total=n_total,
        items=items,
        start_one_indexed=(from_idx + 1) if n_total else 0,
        end_one_indexed=end_idx,
        has_prev=has_prev,
        prev_from=max(0, from_idx - GALLERY_PAGE_SIZE),
        has_next=has_next,
        next_from=end_idx,
    )
    return web.Response(text=html, content_type="text/html")


async def _resolve_session_user_id(request: web.Request) -> str:
    """Risolve l'user_id logico per la session registry partendo
    dall'actor HTTP (Phase 7 Phase 1, 12/5/2026).

    Ordine: device_id pairato → owner del binding HTTP; device autenticato ma
    non risolvibile → principal sintetico fail-closed; admin autenticato → host
    singolo; qualsiasi altro principal senza binding resta sintetico.

    Niente LLM, §7.9: lookup deterministico in users.db.
    """
    import users as _users
    actor = _resolve_actor(request, {})
    # Un device autenticato non deve MAI cadere nel fallback host: se il
    # binding sparisce fra middleware e handler, o lo store ha un guasto, un
    # fallback permissivo trasformerebbe una sessione guest in sessione host.
    # Il principal sintetico è stabile ma isolato; non concede accesso ai dati
    # di alcun utente registrato.
    authenticated_user_id = str(
        request.get("authenticated_user_id") or "")
    if authenticated_user_id:
        return authenticated_user_id
    device_id = str(request.get("device_id") or "")
    if device_id:
        # Middleware authenticated a device-shaped credential but could not
        # retain a live owner.  Never move that request into a fresh synthetic
        # scope while deletion/revocation may be in progress.
        raise web.HTTPUnauthorized(
            text="authenticated device owner is no longer available")

    if request.get("role") == "admin":
        # The auth middleware resolves the unique live host and acquires its
        # lease before dispatch.  Missing state here is an authorization
        # failure, never a signal to invent a shared synthetic owner.
        raise web.HTTPServiceUnavailable(
            text="authenticated host identity is unavailable")

    synthetic = str(request.get("lan_principal") or actor or "anonymous")
    digest = hashlib.sha256(
        f"metnos-unbound-http-principal-v1:{synthetic}".encode("utf-8")
    ).hexdigest()[:24]
    return f"http_principal_{digest}"


def _turn_record_belongs_to_user(record: dict, *, user_id: str,
                                 actor: str) -> bool:
    """Deterministic ownership check shared by every turn-id endpoint.

    New records carry ``owner_user_id`` and use it as the authority.  Older
    records may only carry a conversation or actor; those compatibility
    paths are deliberately narrower and never override a present owner.
    """
    if not isinstance(record, dict):
        return False
    owner_id = str(record.get("owner_user_id") or "")
    if owner_id:
        return hmac.compare_digest(owner_id, str(user_id or ""))

    conversation_id = str(record.get("conversation_id") or "")
    if conversation_id:
        try:
            import active_sessions as _active_sessions
            conversation = _active_sessions.get_conversation(conversation_id)
        except Exception:
            # Identity state is an authorization dependency: failure is a
            # denial, never permission to fall through to a weaker signal.
            return False
        if conversation is not None:
            return (
                str(conversation.get("channel") or "") == "http"
                and hmac.compare_digest(
                    str(conversation.get("user_id") or ""),
                    str(user_id or ""),
                )
            )

    legacy_actor = str(record.get("actor") or "")
    candidates = {str(user_id or ""), str(actor or "")}
    candidates.discard("")
    return any(hmac.compare_digest(legacy_actor, candidate)
               for candidate in candidates)


async def _turn_access_error(request: web.Request,
                             record: dict) -> web.Response | None:
    """Return an HTTP denial for a foreign turn, or ``None`` if admitted."""
    if request.get("role", "anonymous") == "admin":
        return None
    user_id = await _resolve_session_user_id(request)
    actor = _resolve_actor(request, {})
    if _turn_record_belongs_to_user(record, user_id=user_id, actor=actor):
        return None
    return web.json_response(
        {"error": "turn_forbidden", "message": "turn belongs to another user"},
        status=403,
        headers={"Cache-Control": "no-store"},
    )


async def session_register(request: web.Request) -> web.Response:
    """POST /agent/session/register

    Body JSON: `{device_label?: str, conversation_id?: str}`. Ritorna
    `{device_token, conversation_id}` su
    success (HTTP 200) o `{conflict: true, existing, takeover_token}` su
    409 quando un altro device tiene gia' la sessione attiva per lo
    stesso (user_id, channel='http').
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    device_label = str(body.get("device_label") or "")[:200]
    conversation_id = str(body.get("conversation_id") or "")
    user_id = await _resolve_session_user_id(request)
    legacy_actor = _resolve_actor(request, {})
    import active_sessions as _as
    try:
        res = _as.register_session(
            user_id, "http", device_label=device_label,
            conversation_id=conversation_id, legacy_actor=legacy_actor,
        )
    except ValueError:
        return _error(400, "session_register_invalid",
                      _msg("ERR_CHAT_SESSION_REGISTER"))
    if res.get("conflict"):
        return web.json_response(res, status=409)
    return web.json_response(res)


async def session_takeover(request: web.Request) -> web.Response:
    """POST /agent/session/takeover

    Body JSON: `{takeover_token: str, device_label?: str, mode: str}`. Atomic:
    revoca la sessione vecchia + crea la nuova in singola transazione.
    Notifica il device sloggato via SSE (`/agent/session/events`) se
    sottoscritto. Ritorna `{device_token, revoked_device_token}`.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    token = str(body.get("takeover_token") or "").strip()
    if not token:
        return _error(400, "takeover_token_required",
                      _msg("ERR_CHAT_SESSION_RESOLUTION_TOKEN"))
    device_label = str(body.get("device_label") or "")[:200]
    mode = str(body.get("mode") or "activate_current")
    user_id = await _resolve_session_user_id(request)
    import active_sessions as _as
    try:
        res = _as.confirm_takeover_with_notify(
            token, new_device_label=device_label, mode=mode,
            expected_user_id=user_id,
        )
    except ValueError:
        return _error(409, "takeover_invalid",
                      _msg("ERR_CHAT_SESSION_RESOLUTION_CHANGED"))
    return web.json_response(res)


async def session_ping(request: web.Request) -> web.Response:
    """POST /agent/session/ping

    Body JSON: `{device_token: str, conversation_id?: str}`. Aggiorna
    last_seen_at e associa una conversazione legacy una sola volta. 200 se
    sessione attiva, 409 se revocata (client deve ri-registrare).
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    token = str(body.get("device_token") or "").strip()
    if not token:
        return _error(400, "device_token_required",
                      _msg("ERR_CHAT_SESSION_TOKEN_REQUIRED"))
    user_id = await _resolve_session_user_id(request)
    conversation_id = str(body.get("conversation_id") or "")
    legacy_actor = _resolve_actor(request, {})
    import active_sessions as _as
    try:
        session = _as.touch_session(
            token, user_id=user_id, conversation_id=conversation_id,
            legacy_actor=legacy_actor,
        )
    except ValueError:
        return _error(409, "session_conversation_invalid",
                      _msg("ERR_CHAT_SESSION_WRITE_DENIED"))
    if not session:
        return web.json_response(
            {"ok": False, "revoked": True}, status=409,
        )
    return web.json_response({
        "ok": True,
        "conversation_id": session.get("conversation_id") or "",
    })


async def session_revoke(request: web.Request) -> web.Response:
    """POST /agent/session/revoke

    Body JSON: `{device_token: str, reason?: str}`. Marca la sessione
    come revocata (uscita esplicita dell'utente). Idempotente.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    token = str(body.get("device_token") or "").strip()
    if not token:
        return _error(400, "device_token_required",
                      _msg("ERR_CHAT_SESSION_TOKEN_REQUIRED"))
    reason = str(body.get("reason") or "manual")[:60]
    import active_sessions as _as
    session = _as.get_session(token)
    user_id = await _resolve_session_user_id(request)
    if session and session.get("user_id") != user_id:
        return _error(403, "session_forbidden",
                      _msg("ERR_CHAT_SESSION_WRITE_DENIED"))
    changed = _as.revoke_session(token, reason=reason)
    return web.json_response({"ok": True, "changed": changed})


async def session_events(request: web.Request) -> web.StreamResponse:
    """GET /agent/session/events?device_token=X

    SSE stream: il client si sottoscrive agli eventi della propria
    sessione. Eventi possibili:
    - `session_revoked`: la sessione e' stata revocata (typ. via takeover
      di un altro device). Il client mostra banner + disabilita input.

    Connessione long-poll: 15s keepalive comment, chiusura su disconnect.
    """
    token = (request.query.get("device_token") or "").strip()
    if not token:
        return _error(400, "device_token_required",
                      _msg("ERR_CHAT_SESSION_TOKEN_REQUIRED"))
    import active_sessions as _as

    current_user_id = await _resolve_session_user_id(request)
    token_session = _as.get_session(token)
    if token_session and token_session.get("user_id") != current_user_id:
        return _error(403, "session_forbidden",
                      _msg("ERR_CHAT_SESSION_WRITE_DENIED"))

    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache, no-store",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    await resp.prepare(request)
    # Snapshot: se gia' revocata al subscribe, emetti subito e chiudi.
    sess = _as.get_session(token)
    if sess is None or sess.get("revoked_at"):
        payload = json.dumps({
            "reason": (sess or {}).get("revoke_reason") or "unknown",
            "ts": (sess or {}).get("revoked_at") or "",
        }, ensure_ascii=False)
        await resp.write(f"event: session_revoked\ndata: {payload}\n\n".encode())
        await resp.write_eof()
        return resp

    queue: asyncio.Queue = asyncio.Queue(maxsize=32)
    _as.subscribe(token, queue)
    # Registra come SSE attiva per il graceful shutdown.
    sse_set = app_setdefault(request.app, SSE_RESPONSES, set())
    sse_set.add(resp)
    keepalive_task = asyncio.create_task(_sse_keepalive_loop(resp))
    try:
        while True:
            try:
                ev = await asyncio.wait_for(queue.get(), timeout=60.0)
            except asyncio.TimeoutError:
                # nessun evento da 60s, continua (il keepalive task scrive
                # i comment SSE). Esce solo su disconnect o eccezione write.
                continue
            kind = ev.get("kind") or "message"
            data = {k: v for k, v in ev.items() if k != "kind"}
            body = json.dumps(data, ensure_ascii=False, default=str)
            try:
                await resp.write(f"event: {kind}\ndata: {body}\n\n".encode())
            except (ConnectionError, ConnectionResetError, RuntimeError):
                break
            if kind == "session_revoked":
                # Dopo aver notificato il client, chiudi la connessione.
                break
    except asyncio.CancelledError:
        pass
    finally:
        keepalive_task.cancel()
        _as.unsubscribe(token, queue)
        sse_set.discard(resp)
        try:
            await resp.write_eof()
        except Exception:
            pass
    return resp


async def turn_submit(request: web.Request) -> web.Response:
    """POST /agent/turn/submit (ADR pending — turn esecuzione async).

    Ritorna 202 Accepted con `{turn_id, stream_url}` immediatamente.
    Spawn una asyncio.Task che esegue `run_turn()` in executor, scrivendo
    eventi durabili in `TurnEventLog`. Il client si attacca via
    `GET /agent/turns/{turn_id}/stream` (SSE resumable con
    Last-Event-ID). Disaccoppia esecuzione da connessione: refresh,
    tab hidden, network drop non interrompono il turn.

    Body: JSON `{ query, conversation_id?, actor? }` OPPURE multipart
    `multipart/form-data` con campo `query` + N campi `image_<i>` (turni
    image-to-image). Stesso pre-processing di `POST /agent/turn`
    (`_preprocess_turn`): salvataggio immagini + interception dialog/cap.

    Errori 400/401 come `turn()`. Niente fallback Telegram-style: e' un
    endpoint asincrono dedicato al client HTTP dashboard.
    """
    err, data = await _preprocess_turn(request)
    if err is not None:
        return err
    query_for_run = data["query_for_run"]
    immediate_msg = data["immediate_msg"]
    immediate_elapsed_ms = int(data.get("immediate_elapsed_ms") or 0)
    actor = data["actor"]
    user_id = data["user_id"]
    conv_id = data["conversation_id"]
    sender_id = data["sender_id"]
    original_query = data["original_query"]
    reference_images = data["reference_images"]
    credential_meta = data.get("credential_meta") or []
    redacted_fields = int(data.get("redacted_fields") or 0)
    admin_key = app_get(request.app, APP_ADMIN_KEY, "")

    from turn_events import TurnEventLog, TurnEventProgress
    event_log = TurnEventLog.get()
    turn_id = _turn_id_for_preprocessed(data)
    # conversation_id/actor/query: consentono a turns_recent di ritrovare il
    # turn ancora running se il client ricarica la pagina (navigazione chat →
    # dashboard → chat). Il turn non è ancora nei JSONL persistiti.
    event_log.create(turn_id, conversation_id=conv_id, actor=actor,
                     owner_user_id=user_id,
                     query=original_query)

    # Risposta immediata (dialog/cap pending): nessun run_turn, append `final`
    # nel log e chiudi. Il client si attacca e riceve subito l'esito.
    if immediate_msg is not None:
        immediate_http = _decorate_dialog_markers(immediate_msg, admin_key)
        event_log.append(turn_id, "final", {
            "turn_id": turn_id,
            "final_message": immediate_http,
            "final_message_html": _safe_final_html(immediate_http),
            "final_kind": "answer",
            "total_ms": immediate_elapsed_ms,
            "expandable_caps": [],
            "attachments": [],
            "gallery_url": None,
            "n_total_matches": 0,
            "path": [],
        })
        event_log.close(turn_id)
        if data.get("immediate_source") == "pending":
            _persist_pending_http_turn(
                turn_id=turn_id,
                query=original_query,
                message=immediate_msg,
                actor=actor,
                owner_user_id=user_id,
                conversation_id=conv_id,
                redacted_fields=redacted_fields,
            )
        return web.json_response({
            "turn_id": turn_id,
            "stream_url": f"/agent/turns/{turn_id}/stream",
        }, status=202)

    # Reserve before returning 202: accepted means there is bounded execution
    # capacity, not merely an unbounded task queued in the default executor.
    try:
        reservation = await _reserve_turn(request, actor)
    except TurnPoolBusy:
        event_log.close(turn_id)
        return _turn_busy_response()

    # Spawn task. Esegue run_turn nel pool dedicato + scrive eventi nel log.
    refs = list(reference_images or [])

    import agent_runtime as _agent_runtime
    async def _run_async():
        progress = TurnEventProgress(turn_id, log=event_log)
        reservation_handed_off = False
        try:
            runtime_data = data
            if data.get("_tutor_deferred"):
                deferred_query = str(data.pop("_deferred_query", ""))
                runtime_data = await _resolve_open_http_turn(
                    request,
                    query=deferred_query,
                    safe_original_query=original_query,
                    sensitive_fields=0,
                    actor=actor,
                    user_id=user_id,
                    conversation_id=conv_id,
                    sender_id=sender_id,
                    reference_images=refs,
                    turn_id_hint=turn_id,
                )
                deferred_immediate = runtime_data["immediate_msg"]
                if deferred_immediate is not None:
                    immediate_http = _decorate_dialog_markers(
                        deferred_immediate, admin_key)
                    event_log.append(turn_id, "final", {
                        "turn_id": turn_id,
                        "final_message": immediate_http,
                        "final_message_html": _safe_final_html(immediate_http),
                        "final_kind": "answer",
                        "total_ms": int(
                            runtime_data.get("immediate_elapsed_ms") or 0),
                        "expandable_caps": [],
                        "attachments": [],
                        "gallery_url": None,
                        "n_total_matches": 0,
                        "path": [],
                    })
                    if runtime_data.get("immediate_source") == "pending":
                        _persist_pending_http_turn(
                            turn_id=turn_id,
                            query=runtime_data["original_query"],
                            message=deferred_immediate,
                            actor=actor,
                            owner_user_id=user_id,
                            conversation_id=conv_id,
                            redacted_fields=int(
                                runtime_data.get("redacted_fields") or 0),
                        )
                    pool = _turn_pool(request)
                    if pool is not None:
                        pool.release(reservation, completed=True)
                    reservation_handed_off = True
                    return

            query_to_run = runtime_data["query_for_run"]
            runtime_credentials = runtime_data.get("credential_meta") or []
            runtime_redacted_fields = int(
                runtime_data.get("redacted_fields") or 0)
            # From this point the dedicated pool owns release semantics,
            # including cancellation while its worker thread is still alive.
            reservation_handed_off = True
            log_obj = await _run_turn_reserved(
                request, reservation,
                lambda: _agent_runtime.run_turn(
                    query_to_run, actor=actor, channel="http",
                    owner_user_id=user_id,
                    conversation_id=conv_id,
                    progress=progress,
                    reference_images=refs or None,
                    credential_meta=runtime_credentials,
                    credentials_prepared=True,
                    redacted_fields=runtime_redacted_fields,
                ),
            )
            _save_cap_pending_if_any(
                sender_id, original_query, log_obj,
                owner_user_id=user_id)
            event_log.append(turn_id, "final",
                             _build_final_event_payload(log_obj, admin_key))
        except Exception as ex:
            log.exception("turn_submit run failed: %s", turn_id)
            event_log.append(turn_id, "error", {
                "message": str(ex),
                "type": type(ex).__name__,
            })
        finally:
            if not reservation_handed_off:
                pool = _turn_pool(request)
                if pool is not None:
                    pool.release(reservation, completed=True)
            event_log.close(turn_id)

    try:
        asyncio.create_task(_run_async(), name=f"turn-{turn_id}")
    except BaseException:
        pool = _turn_pool(request)
        if pool is not None:
            pool.release(reservation)
        event_log.close(turn_id)
        raise

    return web.json_response({
        "turn_id": turn_id,
        "stream_url": f"/agent/turns/{turn_id}/stream",
    }, status=202)


async def turn_stream(request: web.Request) -> web.Response:
    """GET /agent/turns/{turn_id}/stream (SSE resumable).

    Subscribe al `TurnEventLog` per il turn_id. Honora il header
    `Last-Event-ID` (EventSource standard): replay degli eventi dal next
    in poi. Heartbeat ogni 15s (comment SSE `: keepalive`).

    404 se turn_id sconosciuto al log (turn troppo vecchio, > 5 min
    dopo close, o turn_id mai esistito). Il client puo' fallback a
    `GET /agent/turns/{turn_id}` per il risultato persistente da
    TurnLog jsonl.
    """
    from turn_events import TurnEventLog, format_sse
    turn_id = request.match_info["turn_id"]
    event_log = TurnEventLog.get()
    snapshot = event_log.snapshot(turn_id)
    if snapshot is None:
        return _error(404, "turn_not_found",
                       f"turn {turn_id!r} non in event log "
                       "(potrebbe essere troppo vecchio; usa "
                       "GET /agent/turns/{turn_id})")
    access_error = await _turn_access_error(request, snapshot)
    if access_error is not None:
        return access_error
    last_id = 0
    raw_lid = request.headers.get("Last-Event-ID")
    if raw_lid:
        try:
            last_id = int(raw_lid)
        except (ValueError, TypeError):
            last_id = 0

    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable nginx buffering
        },
    )
    await response.prepare(request)

    try:
        async for ev in event_log.subscribe(turn_id, last_event_id=last_id):
            try:
                await response.write(format_sse(ev))
            except (ConnectionResetError, asyncio.CancelledError):
                # Client disconnect: stop senza interrompere il turn.
                # Il run_turn continua su executor e scrive nel log;
                # il client si ri-attacca via reconnect.
                break
    except Exception as ex:
        log.warning("turn_stream %s error: %r", turn_id, ex)
    finally:
        try:
            await response.write_eof()
        except Exception:
            pass
    return response


async def turn_status(request: web.Request) -> web.Response:
    """GET /agent/turns/{turn_id} — stato del turn.

    Polling fallback per quando lo stream SSE non e' disponibile. Ritorna:
    - Se turn ancora vivo in event log: `{state: "running"|"complete",
      events: [...]}` con tutti gli eventi finora.
    - Se turn chiuso e gc-ed: legge `turns/<date>.jsonl` per il risultato
      finale persistente.

    404 se turn_id non trovato ne' in log ne' su disco.
    """
    from turn_events import TurnEventLog
    turn_id = request.match_info["turn_id"]
    event_log = TurnEventLog.get()
    snapshot = event_log.snapshot(turn_id)
    if snapshot is not None:
        access_error = await _turn_access_error(request, snapshot)
        if access_error is not None:
            return access_error
        return web.json_response({
            "turn_id": turn_id,
            "state": "complete" if snapshot["closed"] else "running",
            "events": snapshot["events"],
        })
    # Fallback: cerca su disco (TurnLog jsonl).
    import json as _json
    from pathlib import Path as _Path
    import config as _C
    turns_dir = _C.PATH_TURNS
    if turns_dir.is_dir():
        for f in sorted(turns_dir.glob("*.jsonl"), reverse=True)[:7]:
            try:
                for line in _Path(f).read_text(
                        encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        d = _json.loads(line)
                    except _json.JSONDecodeError:
                        continue
                    if d.get("turn_id") == turn_id:
                        access_error = await _turn_access_error(request, d)
                        if access_error is not None:
                            return access_error
                        ts_start = float(d.get("ts_start") or 0)
                        ts_end = float(d.get("ts_end") or 0)
                        final_msg = _decorate_dialog_markers(
                            d.get("final_message") or "",
                            app_get(request.app, APP_ADMIN_KEY, ""),
                        )
                        return web.json_response({
                            "turn_id": turn_id,
                            "state": "complete",
                            "persistent": True,
                            "final_message": final_msg,
                            "final_message_html": _safe_final_html(final_msg)
                            if final_msg else "",
                            "final_kind": d.get("final_kind"),
                            "ts_end": ts_end if ts_end else None,
                            "total_ms": int((ts_end - ts_start) * 1000)
                            if ts_end else None,
                            "target_device": d.get("target_device"),
                            "steps_summary": [
                                {"step": s.get("step_num"),
                                 "tool": s.get("chosen_tool"),
                                 "ok": bool(
                                    (s.get("result") or {}).get("ok", True)
                                 ) if isinstance(s.get("result"), dict)
                                       else None,
                                 "error_class": (s.get("result") or {}).get(
                                     "error_class")
                                 if isinstance(s.get("result"), dict)
                                 else None}
                                for s in d.get("steps", [])
                            ],
                        })
            except Exception:
                continue
    return _error(404, "turn_not_found", f"turn {turn_id!r} non trovato")


async def turns_recent(request: web.Request) -> web.Response:
    """GET /agent/turns/recent?conversation_id=X&limit=N&since_ts=T

    Ritorna i turn HTTP della conversation_id specificata, ordinati per
    ts_start desc. Usato dal chat HTML per ricaricare la storia dopo
    tab close (8/5/2026).

    Filtri: conversation_id (required), limit (default 50, max 200),
    since_ts (epoch sec, ritorna solo turn con ts_start > since_ts).

    Output JSON: {turns: [{turn_id, query, final_message, final_message_html,
    final_kind, ts_start, ts_end, in_flight: bool, expandable_caps,
    attachments, gallery_url}]}.

    `in_flight: true` per turn senza ts_end o final_kind (la query e' ancora
    in elaborazione server-side). Il client puo' polling questo endpoint.
    """
    conv_id = request.query.get("conversation_id", "").strip()
    if not conv_id:
        return _error(400, "conversation_id_required",
                      _msg("ERR_CHAT_CONVERSATION_REQUIRED"))
    user_id = await _resolve_session_user_id(request)
    import active_sessions as _as
    conversation = _as.conversation_for_user(conv_id, user_id, "http")
    if conversation is None:
        return _error(403, "conversation_forbidden",
                      _msg("ERR_CHAT_CONVERSATION_FORBIDDEN"))
    # I record nuovi portano owner_user_id. `legacy_actor` consente di leggere
    # soltanto i turni della stessa conversazione anteriori alla migrazione.
    allowed_legacy_actors = {
        user_id,
        str(conversation.get("legacy_actor") or ""),
    }
    allowed_legacy_actors.discard("")
    try:
        limit = min(200, max(1, int(request.query.get("limit", "50"))))
    except (ValueError, TypeError):
        limit = 50
    try:
        since_ts = float(request.query.get("since_ts", "0") or "0")
    except (ValueError, TypeError):
        since_ts = 0.0

    turns_dir = _C.PATH_TURNS
    out: list[dict] = []
    if not turns_dir.exists():
        return web.json_response({"turns": []})

    # Una sessione ancora attiva puo' essere stata lasciata ferma per piu' di
    # sette giorni: scansiona i file dal piu' recente finche' il limite e'
    # raggiunto, invece di troncare arbitrariamente la conversazione.
    files = sorted(turns_dir.glob("*.jsonl"), reverse=True)
    for f in files:
        try:
            with f.open() as fh:
                for ln in fh:
                    try:
                        t = json.loads(ln)
                    except Exception:
                        continue
                    if t.get("conversation_id") != conv_id:
                        continue
                    record_owner = str(t.get("owner_user_id") or "")
                    if record_owner:
                        if record_owner != user_id:
                            continue
                    elif str(t.get("actor") or "") not in allowed_legacy_actors:
                        continue
                    ts_start = float(t.get("ts_start") or 0)
                    if since_ts and ts_start <= since_ts:
                        continue
                    ts_end = float(t.get("ts_end") or 0)
                    in_flight = ts_end == 0 or not t.get("final_kind")
                    final_msg = _decorate_dialog_markers(
                        t.get("final_message") or "",
                        app_get(request.app, APP_ADMIN_KEY, ""),
                    )
                    steps_summary = [
                        {"step": s.get("step_num"),
                         "tool": s.get("chosen_tool"),
                         "ok": bool((s.get("result") or {}).get("ok", True))
                         if isinstance(s.get("result"), dict) else None,
                         "error_class": (s.get("result") or {}).get(
                             "error_class")
                         if isinstance(s.get("result"), dict) else None}
                        for s in (t.get("steps") or [])
                    ]
                    out.append({
                        "turn_id": t.get("turn_id", ""),
                        "query": t.get("user_query", ""),
                        "final_message": final_msg,
                        "final_message_html": _safe_final_html(final_msg) if final_msg else "",
                        "final_kind": t.get("final_kind", ""),
                        "ts_start": ts_start,
                        "ts_end": ts_end if ts_end else None,
                        "total_ms": int((ts_end - ts_start) * 1000) if ts_end else None,
                        "target_device": t.get("target_device"),
                        "in_flight": in_flight,
                        "steps_summary": steps_summary,
                        "expandable_caps": t.get("expandable_caps") or [],
                        "attachments": t.get("attachments") or [],
                    })
                    if len(out) >= limit * 2:  # cap scan, sort below
                        break
        except Exception as e:
            log.warning("turns_recent scan %s failed: %s", f, e)
        if len(out) >= limit * 2:
            break

    # Merge turn IN-FLIGHT dall'event log: girano ancora e NON sono nei JSONL
    # (scritti solo a fine turno). Senza questo, ricaricare la chat mentre un
    # turn gira lo perde (navigazione chat→dashboard→chat su Android) → il
    # client non riaggancia lo stream → ⏳ infinito o falso errore.
    try:
        from turn_events import TurnEventLog
        seen_ids = {t["turn_id"] for t in out}
        for rt in TurnEventLog.get().running_turns(conv_id, user_id):
            if rt["turn_id"] in seen_ids:
                continue
            ts_start = float(rt.get("ts_start") or 0)
            if since_ts and ts_start <= since_ts:
                continue
            out.append({
                "turn_id": rt["turn_id"],
                "query": rt.get("query", ""),
                "final_message": "",
                "final_message_html": "",
                "final_kind": "",
                "ts_start": ts_start,
                "ts_end": None,
                "total_ms": None,
                "target_device": None,
                "in_flight": True,
                "steps_summary": [],
                "expandable_caps": [],
                "attachments": [],
            })
    except Exception as e:
        log.warning("turns_recent in-flight merge failed: %s", e)

    out.sort(key=lambda x: x["ts_start"], reverse=True)
    return web.json_response({"turns": out[:limit]})


_STATIC_DIR = Path(__file__).parent / "static"

_STATIC_CT = {
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg":  "image/svg+xml",
    ".ico":  "image/x-icon",
    ".js":   "application/javascript",
    ".css":  "text/css",
    ".json": "application/json",
    ".html": "text/html",
    ".webmanifest": "application/manifest+json",
    ".txt":  "text/plain",
}


def _static_response(name: str) -> web.Response:
    """Serve un file statico da `runtime/static/`. 404 se non esiste, se il
    path tenta uscire dalla dir (path traversal), o se il tipo non e' un
    asset web.

    `_STATIC_CT` is the ALLOWLIST, not a lookup table with a fallback. The
    route is anonymous by design (`/static/` is exempt in `http_auth`), so
    the directory publishes whatever it holds: a file whose type is not a web
    asset is a document that landed in an asset directory, and answering
    `application/octet-stream` would hand it out. Keeping the two facts in
    one place means a new asset type is added deliberately, and a stray
    document is a 404 rather than a disclosure.
    """
    safe = (_STATIC_DIR / name).resolve()
    try:
        safe.relative_to(_STATIC_DIR.resolve())
    except ValueError:
        return web.Response(status=404, text="not found")
    if not safe.is_file():
        return web.Response(status=404, text="not found")
    ct = _STATIC_CT.get(safe.suffix.lower())
    if ct is None:
        return web.Response(status=404, text="not found")
    body = safe.read_bytes()
    headers = {"Cache-Control": "public, max-age=3600"}
    return web.Response(body=body, content_type=ct, headers=headers)


async def static_asset(request: web.Request) -> web.Response:
    """GET /static/<name> — serve file da `runtime/static/`."""
    return _static_response(request.match_info["name"])


async def manifest_webmanifest(request: web.Request) -> web.Response:
    """GET /manifest.webmanifest — PWA web app manifest."""
    return _static_response("manifest.webmanifest")


async def service_worker(request: web.Request) -> web.Response:
    """GET /sw.js — service worker (deve essere alla root per scope='/')."""
    return _static_response("sw.js")


async def pair_consume(request: web.Request) -> web.Response:
    """GET /pair/<token> — device web consuma un pair token e ottiene
    cookie pair persistente.

    Flusso (ADR 0083 multi-user, 11/5/2026 estensione channel='http'):
      1. L'admin (Roberto) emette il token via `/admin/users/<id>/channels/http/pair`
         o via comando Telegram (TODO). Il token vive in `users.user_channels.pairing_token`.
      2. L'admin invia il URL `https://metnos.example/pair/<token>` al device target
         (cellulare, notebook fuori LAN) via canale fidato (Telegram, AirDrop, ...).
      3. Il device apre il URL UNA VOLTA → token consumato + binding device_id
         in user_channels.recipient_id + cookie USER_COOKIE firmato set.
      4. Future richieste dal device portano il cookie → ruolo `user`.
    """
    from http_auth import (
        USER_COOKIE, USER_COOKIE_TTL_S, external_request_scheme,
        issue_user_cookie,
    )
    import users as _users

    token = request.match_info["token"]
    if not token or len(token) < 16:
        return web.Response(text=_msg("ERR_PAIRING_TOKEN_INVALID"), status=400,
                            content_type="text/plain")

    # device_id stabile per questo specifico device: hash di User-Agent +
    # token (token rende unico questo binding, UA classifica il device).
    ua = request.headers.get("User-Agent", "")[:200]
    device_id = hashlib.sha256(f"http:{token}:{ua}".encode()).hexdigest()[:32]

    try:
        user = _users.consume_pairing_token("http", device_id, token)
    except ValueError as ex:
        return web.Response(
            text=f"<h2>Pair fallito</h2><p>{html_escape(str(ex))}</p>"
                 f"<p>Il token potrebbe essere gia' stato usato o scaduto. "
                 f"Chiedi all'admin di generarne uno nuovo.</p>"
                 f"<p><a href=\"/\">Torna</a></p>",
            status=410, content_type="text/html",
        )

    admin_key = app_get(request.app, APP_ADMIN_KEY, "")
    cookie_val = issue_user_cookie(admin_key, device_id)
    resp = web.HTTPFound("/")
    resp.set_cookie(
        USER_COOKIE, cookie_val,
        max_age=USER_COOKIE_TTL_S,
        httponly=True,
        secure=external_request_scheme(request) == "https",
        samesite="Lax",
        path="/",
    )
    log.info("[pair] device bound user_id=%s name=%s device_id=%s",
              user.get("id"), user.get("name"), device_id[:12])
    return resp


def html_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


async def oauth_callback(request: web.Request) -> web.Response:
    """GET /oauth/callback — Google reindirizza qui dopo l'autorizzazione.

    Query param: `code` (token di scambio) + `state` (anti-CSRF + lookup
    nel pending store). Bypass auth middleware via path pubblico
    (Google non puo' propagare il cookie sessione admin Metnos).

    Flusso:
      1. Lookup state in oauth_pending. Se assente/scaduto: 410 Gone.
      2. Chiama gworkspace_oauth.finish_flow(code) per ottenere token.
      3. Ri-invoca executor con args_base via invoke_executor.
      4. Rende pagina HTML con esito + risultato + bottone «torna alla chat».
    """
    code = request.query.get("code") or ""
    state = request.query.get("state") or ""
    error = request.query.get("error") or ""

    if error:
        return _oauth_result_page(
            ok=False,
            title="Autorizzazione rifiutata",
            body=(f"Google ha riportato: <code>{html_escape(error)}</code>. "
                  f"Riprova dalla chat se vuoi rifare il setup."),
        )

    if not code or not state:
        return _oauth_result_page(
            ok=False,
            title="Callback OAuth incompleto",
            body="Parametri <code>code</code> o <code>state</code> mancanti.",
        )

    try:
        import oauth_pending
        entry = oauth_pending.pop(state)
    except ImportError:
        return _oauth_result_page(
            ok=False, title="OAuth non disponibile",
            body="oauth_pending non importabile sul server.",
        )

    if entry is None:
        return _oauth_result_page(
            ok=False, title="State scaduto",
            body=("Il flow OAuth e' scaduto (TTL 10 min) o e' stato gia' "
                  "consumato. Rifai la richiesta dalla chat per ripartire."),
        )

    owner_user_id = str(entry.get("owner_user_id") or "").strip()
    try:
        from user_lifecycle import OwnerUnavailable, owner_session
        import users as _users
        with owner_session(owner_user_id):
            owner = _users.get_user(owner_user_id)
            if (owner is None or str(owner.get("id") or "")
                    != owner_user_id):
                raise OwnerUnavailable("OAuth owner unavailable")
            return _finish_oauth_callback_for_owner(
                entry, code, owner=owner)
    except OwnerUnavailable:
        return _oauth_result_page(
            ok=False, title="State scaduto",
            body=("Il flow OAuth non appartiene piu' a un utente attivo. "
                  "Rifai la richiesta dalla chat per ripartire."),
        )


def _finish_oauth_callback_for_owner(entry: dict, code: str, *,
                                     owner: dict) -> web.Response:
    """Finish and resume OAuth while holding the logical owner's lease."""

    try:
        import oauth_flow
        ok, err = oauth_flow.finish_flow(
            flow_state=entry.get("flow_state") or {},
            code=code,
            binding=entry.get("binding") or "",
            mirror_paths=entry.get("mirror_paths") or [],
        )
    except (ImportError, OSError, RuntimeError, ValueError) as ex:
        return _oauth_result_page(
            ok=False, title=_msg("MSG_TOKEN_EXCHANGE_FAILED"),
            body=f"<code>{html_escape(type(ex).__name__)}: {html_escape(str(ex))}</code>",
        )

    if not ok:
        return _oauth_result_page(
            ok=False, title=_msg("MSG_TOKEN_EXCHANGE_FAILED"),
            body=f"<code>{html_escape(str(err))}</code>",
        )

    executor = entry.get("executor") or ""
    args_base = dict(entry.get("args_base") or {})
    resume_body = ""
    if executor:
        try:
            from loader import load_catalog
            cat = load_catalog(verify=True, include_synth=True)
            ex = cat.executors.get(executor)
            if ex is None:
                resume_body = _msg("MSG_TOKEN_SAVED_NO_CATALOG",
                                   executor=html_escape(str(executor)))
            else:
                import agent_runtime as _ar
                res = _ar.invoke_executor(
                    ex, args_base, timeout_s=getattr(ex, "timeout_s", 30),
                    actor=owner.get("name") or None,
                    channel=entry.get("channel") or None,
                    owner_user_id=owner.get("id") or "",
                )
                resume_body = _format_resume_result(res)
        except (PermissionError, KeyError, RuntimeError, TypeError) as ex:
            log.exception("oauth_callback: resume_call fallito")
            resume_body = _msg(
                "ERR_TOKEN_SAVED_RESUME_FAILED",
                executor=html_escape(str(executor)),
                detail=f"{html_escape(type(ex).__name__)}: {html_escape(str(ex))}")
    else:
        resume_body = _msg("MSG_TOKEN_SAVED_NO_RESUME")

    return _oauth_result_page(
        ok=True, title=_msg("MSG_SETUP_COMPLETED"),
        body=resume_body,
    )


def _format_resume_result(res) -> str:
    """Markdown/HTML compatto per il risultato della ri-invocazione."""
    if not isinstance(res, dict):
        return f"<pre>{html_escape(str(res)[:600])}</pre>"
    if not res.get("ok"):
        err = res.get("error") or _msg("MSG_ERR_UNKNOWN")
        return _msg("ERR_EXECUTOR_RETURNED_ERROR", error=html_escape(str(err)))
    summary = res.get("summary") or res.get("final_message_hint") or ""
    entries = res.get("entries") or []
    if summary and not entries:
        return f"<p>{summary}</p>"
    if entries:
        lines = [f"<p>{_msg('MSG_RESUME_ENTRIES_FOUND', n=len(entries))}</p><ul>"]
        for e in entries[:20]:
            if isinstance(e, dict):
                title = (e.get("summary") or e.get("title")
                         or e.get("subject") or e.get("name")
                         or str(e)[:80])
                lines.append(f"<li>{title}</li>")
            else:
                lines.append(f"<li>{str(e)[:80]}</li>")
        if len(entries) > 20:
            lines.append(f"<li>{_msg('MSG_RESUME_ENTRIES_OMITTED', n=len(entries) - 20)}</li>")
        lines.append("</ul>")
        return "".join(lines)
    return f"<p>{_msg('MSG_RESUME_NO_OUTPUT')}</p>"


def _oauth_result_page(*, ok: bool, title: str, body: str) -> web.Response:
    """Render pagina HTML autostante per OAuth callback (success o failure)."""
    color = "#2a7c2a" if ok else "#a02020"
    icon = "✓" if ok else "✗"
    html = f"""<!doctype html>
<html lang="it"><head><meta charset="utf-8">
<title>Metnos - OAuth</title>
<style>
body{{font:14px system-ui,-apple-system,sans-serif;background:#fafafa;
     color:#222;max-width:640px;margin:2rem auto;padding:1rem;}}
h1{{color:{color};font-size:1.4em;margin:0 0 .6rem 0}}
.card{{background:#fff;border:1px solid #e0e0e0;border-radius:.4rem;
       padding:1rem 1.2rem;}}
code{{background:#f4f4f4;padding:.1em .3em;border-radius:.2em}}
pre{{background:#f4f4f4;padding:.8rem;border-radius:.3rem;overflow-x:auto}}
a.btn{{display:inline-block;padding:.4rem 1rem;border:1px solid #aaa;
       border-radius:.3rem;background:#f8f8f8;text-decoration:none;color:#333;
       margin-top:1rem;}}
a.btn:hover{{background:#eef}}
</style></head>
<body><div class="card">
<h1>{icon} {title}</h1>
{body}
<p><a class="btn" href="/">Torna alla chat</a></p>
</div></body></html>"""
    return web.Response(text=html, content_type="text/html")


async def turn_retry_handler(request: web.Request) -> web.Response:
    """POST /agent/turns/{turn_id}/retry — rilancia la query del turno.

    Pre-step di pulizia (richiesta utente 22/5/2026): cancella le entries
    `canonical_query_log` con BGE similarity alta vs la query del turno
    rifiutato, cosi' il retry non riusa pattern appena bocciati.
    (11/6/2026: rimosso il cleanup L2 multi_tool_paths — ADR 0150 ritirato.)

    Risposta JSON: {"query": <str>, "submit_url": "/agent/turn/submit",
                    "deleted_cache_entries": <int>}.
    """
    turn_id = request.match_info["turn_id"]
    try:
        from turn_feedback import _load_turn
        turn = _load_turn(turn_id)
    except Exception as ex:
        log.exception("retry: cannot load turn %s", turn_id)
        return _error(500, "internal_error", str(ex))
    if turn is None:
        return _error(404, "not_found", f"turn {turn_id} not found")
    access_error = await _turn_access_error(request, turn)
    if access_error is not None:
        return access_error
    query = turn.get("user_query") or ""
    if not query:
        return _error(400, "no_query", "turn has no user_query to retry")

    # Cancellazione cache via cosine match: entries canonical_query_log il
    # cui canonical_query ha BGE similarity >= 0.7 con la user_query del
    # turno rifiutato → il retry non riusa pattern appena bocciati.
    deleted = 0
    mn = None
    try:
        from mnestoma import Mnestoma
        mn = Mnestoma()
        deleted = mn.delete_canonical_query_log_matching(query, cosine_threshold=0.7)
    except Exception as ex:
        log.warning("retry %s: cache cleanup failed: %r", turn_id, ex)
    finally:
        if mn is not None:
            try:
                mn.close()
            except Exception:
                log.debug("retry %s: mnestoma close failed", turn_id,
                          exc_info=True)
    log.info("retry %s: deleted %d canonical cache entries vs query %r",
             turn_id, deleted, query[:60])

    return web.json_response({
        "ok": True, "query": query,
        "submit_url": "/agent/turn/submit",
        "conversation_id": turn.get("conversation_id"),
        "deleted_cache_entries": deleted,
    })


async def turn_feedback_handler(request: web.Request) -> web.Response:
    """POST /agent/turns/{turn_id}/feedback — user feedback OK|error.

    Body JSON: {"action": "ok"|"error"}.
    OK/Error propagano il verdict a engine.autopath; error marca il turno
    negativo in audit log (rejected pipelines LWW + E12 demote executor).

    Risposta HTML (htmx HX-Request) o JSON.
    """
    turn_id = request.match_info["turn_id"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    action = (body.get("action") or "").strip().lower()
    if action not in ("ok", "error", "repeat"):
        return _error(400, "invalid_action",
                      "action must be 'ok', 'error', or 'repeat'")
    try:
        from turn_feedback import _load_turn
        turn = _load_turn(turn_id)
    except Exception as ex:
        log.exception("turn_feedback: cannot load turn %s", turn_id)
        return _error(500, "internal_error", str(ex))
    if turn is None:
        return _error(404, "not_found", f"turn {turn_id} not found")
    access_error = await _turn_access_error(request, turn)
    if access_error is not None:
        return access_error
    actor = _resolve_actor(request, body)
    try:
        from turn_feedback import apply_feedback
        rec = apply_feedback(turn_id, action, by=actor or "user")
    except ValueError as ex:
        return _error(400, "invalid_request", str(ex))
    except Exception as ex:
        log.exception("turn_feedback failed for %s/%s", turn_id, action)
        return _error(500, "internal_error", str(ex))

    is_htmx = request.headers.get("HX-Request", "").lower() == "true"
    if is_htmx:
        # Risposta minimal: badge che sostituisce i 2 button. Label da i18n
        # (no hardcoded user-facing text, ADR 0104).
        from messages import get as _msg
        emoji = "✓" if action == "ok" else "✗"
        label_key = "MSG_CHAT_FB_OK_DONE" if action == "ok" else "MSG_CHAT_FB_ERR_DONE"
        label = _msg(label_key)
        effects = rec.get("effects", [])
        eff_summary = ", ".join(
            e.get("action", e.get("type", "?")) for e in effects
        ) or "noted"
        # Classe semantica per stile colorato: ok verde, err rosso.
        done_class = "ok" if action == "ok" else "err"
        # E.2: dopo action=error aggiungo button "↻ riprova" inline. Il
        # client intercetta il click e chiama POST /agent/turns/{id}/retry.
        retry_btn = ""
        if action == "error":
            retry_label = _msg("MSG_CHAT_FB_RETRY")
            retry_hint = _msg("MSG_CHAT_FB_RETRY_HINT")
            # Event delegation lato client (no inline onclick: rischio CSP).
            retry_btn = (
                f' <button class="msg-fb-retry" type="button" '
                f'title="{retry_hint}" data-turn-id="{turn_id}" '
                f'data-action="retry-turn">↻ {retry_label}</button>'
            )
        html = (
            f'<span class="msg-fb-done {done_class}" title="{eff_summary}">'
            f'{emoji} {label}{retry_btn}</span>'
        )
        return web.Response(text=html, content_type="text/html")
    return web.json_response({"ok": True, "feedback": rec})


ROUTES = (
    ("GET",  "/",                      chat_root),
    ("GET",  "/agent/health",          health),
    ("GET",  "/.well-known/metnos.json", well_known),
    ("POST", "/agent/register",         device_register),
    ("POST", "/agent/turn",            turn),
    ("POST", "/agent/turn/submit",     turn_submit),
    ("GET",  "/agent/turns/{turn_id}/stream", turn_stream),
    ("GET",  "/agent/turns/{turn_id}",  turn_status),
    ("POST", r"/agent/turns/{turn_id}/feedback", turn_feedback_handler),
    ("POST", r"/agent/turns/{turn_id}/retry",    turn_retry_handler),
    ("GET",  "/agent/turns/recent",    turns_recent),
    ("POST", "/agent/session/register", session_register),
    ("POST", "/agent/session/takeover", session_takeover),
    ("POST", "/agent/session/ping",    session_ping),
    ("POST", "/agent/session/revoke",  session_revoke),
    ("GET",  "/agent/session/events",  session_events),
    ("GET",  "/agent/devices/me",      device_self),
    ("GET",  "/agent/dialog/{dialog_id}/form",   dialog_form),
    ("POST", "/agent/dialog/{dialog_id}/submit", dialog_submit),
    ("GET",  "/agent/dialog/{dialog_id}/cancel", dialog_cancel),
    ("GET",  "/agent/dialog/{dialog_id}/preview/{step_idx}/{option_idx}", dialog_preview),
    ("GET",  "/agent/dialog/{dialog_id}/preview/{option_idx}",            dialog_preview),
    ("GET",  "/agent/dialog/{dialog_id}/context/{step_idx}",              dialog_context),
    ("GET",  "/agent/dialog/{dialog_id}/context",                         dialog_context),
    ("GET",  "/agent/photos/web",                 photo_web_proxy),
    ("GET",  "/agent/photos/{turn_id}/{idx}",     photo_serve),
    ("GET",  "/agent/gallery/{turn_id}",          gallery),
    ("GET",  "/oauth/callback",                   oauth_callback),
    ("GET",  "/pair/{token}",                     pair_consume),
    ("GET",  "/static/{name}",                    static_asset),
    ("GET",  "/manifest.webmanifest",             manifest_webmanifest),
    ("GET",  "/sw.js",                            service_worker),
)
