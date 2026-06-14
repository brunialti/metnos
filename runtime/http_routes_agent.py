"""http_routes_agent — endpoint /agent/* per la HTTP API.

- GET  /agent/health           liveness (anonymous)
- GET  /.well-known/metnos.json discovery (anonymous)
- POST /agent/turn             pianificatore (user/admin); JSON o SSE
- GET  /agent/devices/me       info device chiamante (user)
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import socket
import time
import urllib.parse
from pathlib import Path

from aiohttp import web

import devices
import config as _C  # §7.11
from html_sanitizer import to_safe_html_full
from http_render import _error, render_template
from http_auth import ADMIN_KEY_PATH
from logging_setup import get_logger

log = get_logger(__name__)

VERSION = "1.1"  # versione dell'HTTP API (ADR 0078), DISTINTA dalla product version

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
    sse_set = app.get("sse_responses") or set()
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

    Accessibile a user/admin. Anonymous viene dirottato al login admin
    (in LAN trusted il middleware promuove a user e si entra direttamente).
    """
    role = request.get("role", "anonymous")
    if role == "anonymous":
        raise web.HTTPFound("/admin/login")
    html = render_template("chat.html", role=role)
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
    started = request.app.get("started_at", time.time())
    return web.json_response(
        {"ok": True, "version": VERSION, "uptime_s": round(time.time() - started, 1),
         **_metnos_version_info()}
    )


async def well_known(request: web.Request) -> web.Response:
    """GET /.well-known/metnos.json — descrittore pubblico del nodo."""
    fp = ""
    try:
        if ADMIN_KEY_PATH.exists():
            fp = "sha256:" + hashlib.sha256(
                ADMIN_KEY_PATH.read_text().strip().encode()
            ).hexdigest()[:16]
    except Exception as e:
        log.debug("admin key fingerprint unavailable: %s", e)

    return web.json_response({
        "name": "metnos",
        "version": VERSION,
        **_metnos_version_info(),
        "channels": ["telegram", "http"],
        "capabilities": ["agent.turn", "admin.proposals", "admin.executors",
                         "admin.runs", "admin.safety", "admin.turns"],
        "public_key_fingerprint": fp,
        "pairing_url": "/agent/register",
    })


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
    return web.json_response({
        "device_id": d.id,
        "name": d.name,
        "owner_user_id": d.owner_user_id,
        "fingerprint": d.public_key_fingerprint,
        "os_family": d.os_family,
        "os_arch": d.os_arch,
        "paired_at": d.paired_at,
        "last_heartbeat": d.last_heartbeat,
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
    """Determina actor: admin override > device_id > 'host'."""
    role = request.get("role", "anonymous")
    if role == "admin" and isinstance(body.get("actor"), str) and body["actor"]:
        return body["actor"]
    return request.get("device_id") or "host"


def _http_sender_id(actor: str, conv_id: str) -> str:
    """Chiave per pending state HTTP (cap-expand, future approval, ecc.)."""
    return f"http:{actor}:{conv_id or '_'}"


def _apply_dialog_cancel(sender_id: str, query: str) -> str | None:
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
    l'istruzione data (annulla aborta il dialogo) e l'effetto osservato.

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
    pending = list_pending(sender_id)
    if not pending:
        return None
    cancelled = 0
    for d in pending:
        dlg_id = d.get("dialog_id", "")
        if dlg_id and cancel_pending(sender_id, dlg_id):
            cancelled += 1
    if cancelled == 0:
        return None
    if cancelled == 1:
        return "Dialogo annullato."
    return f"Dialogo annullato ({cancelled} pending)."


def _apply_dialog_pending(sender_id: str, query: str,
                            actor: str = "host",
                            channel: str = "http",
                            conversation_id: str = "") -> str | None:
    """Universal §7.9: se sender ha un dialog pending (get_inputs aperto),
    intercetta il prossimo messaggio utente come risposta al dialog.

    Risolve gap critico: dialog strato3 (5 azioni) aperto, utente digita
    "1" / "ritenta" → senza questo handler, "1" viene trattato come nuova
    query (echo). Ora viene routed correttamente al dispatcher on_complete.

    Ritorna messaggio finale se dialog consumato, None altrimenti (passa
    a flusso normale run_turn).
    """
    try:
        from dialog_pending import list_pending, consume_pending_step
    except ImportError:
        return None
    # Universal §7.9: prova multipli sender_id formats per backward compat.
    # HTTP nuovo: _http_sender_id(actor, conv_id) = "http:host:xyz"
    # Strato3 legacy: "channel:actor" = "http:host"
    pending = list_pending(sender_id)
    sender_id_used = sender_id
    if not pending:
        alt_sender = f"{channel}:{actor}" if channel else actor
        if alt_sender != sender_id:
            pending = list_pending(alt_sender)
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
    # Avanza dialog con valore raccolto
    consume_res = consume_pending_step(
        sender_id_used, dialog_id, current_var, query.strip())
    if not consume_res.get("ok"):
        return None
    # Se dialog completato → call canonical dispatcher process_completion_callback
    if consume_res.get("completed"):
        try:
            from orchestration import process_completion_callback
            return process_completion_callback(
                sender_id_used, dialog_id, actor=actor, channel=channel,
            )
        except Exception as ex:
            import logging
            logging.getLogger(__name__).warning(
                "dialog on_complete dispatch failed: %s", ex)
            return f"Dialog dispatch fallito: {ex}"
    return None  # dialog ha più step, attendi prossimo input


def _apply_cap_pending(sender_id: str, query: str,
                        actor: str = "host") -> tuple[str, dict | None, str | None]:
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
    """
    from channels.daemon import (
        _cap_pending_load, _cap_pending_clear, _classify_yes_no,
    )
    pending = _cap_pending_load(sender_id)
    if not pending:
        return query, None, None
    p = pending["proposal"]

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
        )

    ans = _classify_yes_no(query)
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
                return query, pending, f"(esecuzione fallita: {type(e).__name__}: {e})"
            return query, pending, (res or {}).get("summary", json.dumps(res)[:500])
        # approval_required (find_images_indices build): direct invocation
        # dell'executor con args_suggested. Il rewrite verbale "(forza X=Y
        # su Z)" non regge sul PLANNER medium (Gemma 4 26B): test live
        # 5/5/2026 ha mostrato che Gemma ha interpretato il rewrite come
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
                    return query, pending, f"(executor {executor_name} non in catalog)"
                import agent_runtime
                res = agent_runtime.invoke_executor(
                    ex, args, timeout_s=getattr(ex, "timeout_s", 30),
                    actor=actor, channel="http",
                )
            except Exception as e:
                return query, pending, f"(esecuzione fallita: {type(e).__name__}: {e})"
            # Format minimale del risultato. find_images_indices: entries=[{path,score}]
            entries = (res or {}).get("entries") or []
            n_entries = (res or {}).get("n_entries") or len(entries)
            if not res or not res.get("ok"):
                err = (res or {}).get("error", "errore sconosciuto")
                return query, pending, f"Rilancio fallito: {err}"
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
            head = (
                f"Rilancio con {p['cap_field']}={p['cap_suggested']} → "
                f"{n_entries} risultati"
            )
            if len(entries) > 30:
                tail = f"\n…(altre {len(entries)-30} omesse)"
            else:
                tail = ""
            return query, pending, head + "\n\n" + "\n".join(preview) + tail
        # Kind sconosciuto al "sì" → scarta lo stato e procedi normalmente.
        log.warning("[http] cap_pending kind sconosciuto %r -> scarto", p.get("kind"))
        _cap_pending_clear(sender_id)
        return query, None, None
    _cap_pending_clear(sender_id)
    return query, None, None


def _consume_http_get_inputs_response(
    proposal: dict, query: str, *, sender_id: str, actor: str,
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
        _dp.cancel_pending(sender_for_state, dialog_id)
        _cap_pending_clear(sender_id)
        return query, proposal, "Dialogo annullato."

    state = _dp.load_pending(sender_for_state, dialog_id)
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
        # Per dialog yes_no (caso tipico cap-expand): se l'utente NON
        # risponde sì/no ma scrive una query lunga (>10 char), interpretiamo
        # come "non era una risposta, ho cambiato idea, ecco una nuova
        # richiesta". Cancelliamo il dialog (equivalente a "no" sul
        # cap-expand) e lasciamo passare la query al PLANNER come turno
        # nuovo. Senza questa euristica l'utente resterebbe bloccato sul
        # re-prompt all'infinito ogni volta che scrive qualcosa di diverso.
        text_len = len((query or "").strip())
        if schema_kind == "yes_no" and text_len > 10:
            _dp.cancel_pending(sender_for_state, dialog_id)
            _cap_pending_clear(sender_id)
            # query torna invariata, pending=None → il flow normale
            # processa la query come turno nuovo.
            return query, None, None
        # Altrimenti: re-prompt dello stesso step (input troppo breve o
        # malformato — verosimilmente errore di battitura).
        return (query, None,
                f"{err}\n\nStep {idx+1}/{len(dialog)} — {cur_step.get('prompt')}")

    cres = _dp.consume_pending_step(sender_for_state, dialog_id, var, value)
    if not cres.get("ok"):
        _cap_pending_clear(sender_id)
        return (query, proposal,
                f"(Errore stato dialogo: {cres.get('error')})")

    if cres.get("completed"):
        # Dialogo finito → applica callback. Per cap-expand questo
        # invoca direttamente l'executor con cap esteso e ritorna
        # un summary user-facing.
        msg = _orch.process_completion_callback(
            sender_for_state, dialog_id, actor=actor,
        )
        _cap_pending_clear(sender_id)
        return query, proposal, msg

    # Prossimo step (raro per cap-expand a 1 step).
    next_step = dialog[idx + 1]
    next_prompt = next_step.get("prompt") or "?"
    return (query, None,
            f"Step {idx+2}/{len(dialog)} — {next_prompt}")


def _save_cap_pending_if_any(sender_id: str, original: str, turn_log) -> None:
    if getattr(turn_log, "expandable_caps", None):
        from channels.daemon import _cap_pending_save
        _cap_pending_save(sender_id, original,
                          turn_log.expandable_caps[0], turn_log.turn_id)



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
            import urllib.parse as _up
            proxy = "/agent/photos/web?u=" + _up.quote(web_url, safe="")
            out.append({
                "kind": att.get("kind", "image"),
                "basename": att.get("basename"),
                "score": att.get("score"),
                "caption": att.get("caption"),
                "thumb_url": proxy,
                "full_url": proxy,
                "open_url": web_url,  # link a sorgente reale per click esterno
            })
            continue
        # Attachment local-sourced (path): URL signed via photo_endpoint.
        out.append({
            "kind": att.get("kind", "image"),
            "basename": att.get("basename"),
            "score": att.get("score"),
            "caption": att.get("caption"),
            "thumb_url": photo_endpoint.make_url(
                log_obj.turn_id, idx, "thumb", admin_key
            ),
            "full_url": photo_endpoint.make_url(
                log_obj.turn_id, idx, "full", admin_key
            ),
        })
    return out


def _gallery_url_for(log_obj) -> tuple[str | None, int]:
    """Ritorna (gallery_url, n_total). gallery_url None se 0 attachments."""
    n_total = len(getattr(log_obj, "attachments", []) or [])
    if n_total <= 0:
        return None, 0
    return f"/agent/gallery/{log_obj.turn_id}", n_total


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
    return {
        "turn_id": log_obj.turn_id,
        "final_message": log_obj.final_message,
        "final_message_html": _safe_final_html(log_obj.final_message),
        "final_kind": log_obj.final_kind,
        "total_ms": int((log_obj.ts_end - log_obj.ts_start) * 1000),
        "ts_end": float(log_obj.ts_end),
        "expandable_caps": getattr(log_obj, "expandable_caps", []) or [],
        "attachments": _enrich_attachments(log_obj, admin_key),
        "gallery_url": gallery_url,
        "n_total_matches": n_total,
        "path": path_summary,
    }


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
        }
        actor = _resolve_actor(request, body)
        conversation_id = body.get("conversation_id") or ""
        sender_id = _http_sender_id(actor, conversation_id)
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
        actor = _resolve_actor(request, body)
        conversation_id = body.get("conversation_id") or ""
        sender_id = _http_sender_id(actor, conversation_id)

    # Universal §7.3: drag&drop con immagini + query = NUOVA intenzione
    # esplicita, mai una risposta a dialog precedenti. Skip TUTTI gli
    # interceptor (cancel, dialog_pending, cap_pending).
    if reference_images:
        from channels.daemon import _cap_pending_clear
        _cap_pending_clear(sender_id)
        try:
            from dialog_pending import list_pending, cancel_pending
            for d in list_pending(sender_id):
                cancel_pending(sender_id, d.get("dialog_id", ""))
        except Exception:
            pass
        query_for_run = query
        immediate_msg = None
    else:
        # Dialog cancel intercept: se c'e' un dialog pending e l'utente scrive
        # "annulla"/"undo", cancella il dialog invece di routare a undo.
        _dialog_cancel_msg = _apply_dialog_cancel(sender_id, query)
        if _dialog_cancel_msg is not None:
            query_for_run = query
            immediate_msg = _dialog_cancel_msg
        else:
            # Universal §7.9: se sender ha dialog pending, intercetta come risposta.
            _dlg_resp = _apply_dialog_pending(
                sender_id, query, actor=actor, channel="http",
                conversation_id=conversation_id or "",
            )
            if _dlg_resp is not None:
                query_for_run = query
                immediate_msg = _dlg_resp
            else:
                query_for_run, _, immediate_msg = _apply_cap_pending(
                    sender_id, query, actor=actor)
    return None, {
        "query_for_run": query_for_run,
        "immediate_msg": immediate_msg,
        "actor": actor,
        "conversation_id": conversation_id,
        "sender_id": sender_id,
        "reference_images": reference_images,
        "original_query": query,
    }


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
    actor = data["actor"]
    conversation_id = data["conversation_id"]
    sender_id = data["sender_id"]
    reference_images = data["reference_images"]
    accept = request.headers.get("Accept", "")
    want_sse = "text/event-stream" in accept
    if immediate_msg is not None:
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
                "turn_id": "cap-expand",
                "final_message": immediate_msg,
                "final_message_html": _safe_final_html(immediate_msg),
                "final_kind": "answer",
                "total_ms": 0,
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
            "turn_id": "cap-expand",
            "final_message": immediate_msg,
            "final_message_html": _safe_final_html(immediate_msg),
            "final_kind": "answer",
            "total_ms": 0,
            "steps_summary": [],
            "conversation_id": conversation_id,
            "expandable_caps": [],
        })
    original_query_for_pending = query  # quello digitato adesso

    # Lazy import: evita di costruire il catalog ad app start (tempo CI).
    import agent_runtime

    if want_sse:
        return await _turn_sse(request, agent_runtime, query_for_run,
                               actor, conversation_id, sender_id,
                               original_query_for_pending,
                               reference_images)
    return await _turn_json(request, agent_runtime, query_for_run, actor,
                            conversation_id, sender_id,
                            original_query_for_pending,
                            reference_images)


async def _turn_json(request: web.Request, agent_runtime, query: str, actor: str, conv_id: str,
                     sender_id: str, original_for_pending: str,
                     reference_images: list[str] | None = None) -> web.Response:
    loop = asyncio.get_running_loop()
    refs = list(reference_images or [])
    log_obj = await loop.run_in_executor(
        None,
        lambda: agent_runtime.run_turn(
            query, actor=actor, channel="http",
            conversation_id=conv_id,
            reference_images=refs or None,
        ),
    )
    _save_cap_pending_if_any(sender_id, original_for_pending, log_obj)
    admin_key = request.app.get("admin_key", "")
    gallery_url, n_total = _gallery_url_for(log_obj)
    return web.json_response({
        "turn_id": log_obj.turn_id,
        "final_message": log_obj.final_message,
        "final_message_html": _safe_final_html(log_obj.final_message),
        "final_kind": log_obj.final_kind,
        "total_ms": int((log_obj.ts_end - log_obj.ts_start) * 1000),
        "ts_end": float(log_obj.ts_end),  # epoch seconds, per close-time UI
        "steps_summary": [
            {"step": s.step_num, "tool": s.chosen_tool,
             "ok": bool(s.result and s.result.get("ok", True)) if isinstance(s.result, dict) else None}
            for s in log_obj.steps
        ],
        "conversation_id": conv_id,
        "expandable_caps": getattr(log_obj, "expandable_caps", []) or [],
        "attachments": _enrich_attachments(log_obj, admin_key),
        "gallery_url": gallery_url,
        "n_total_matches": n_total,
    })


async def _turn_sse(request: web.Request, agent_runtime,
                    query: str, actor: str, conv_id: str,
                    sender_id: str, original_for_pending: str,
                    reference_images: list[str] | None = None) -> web.StreamResponse:
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
    sse_set = request.app.setdefault("sse_responses", set())
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
            conversation_id=conv_id,
            reference_images=refs or None,
        )

    try:
        log_obj = await loop.run_in_executor(None, _run_blocking)
        _save_cap_pending_if_any(sender_id, original_for_pending, log_obj)
        admin_key = request.app.get("admin_key", "")
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
                      f"dialogo {dialog_id} non trovato")
    if state.get("cancelled"):
        return web.Response(
            text=(
                "<!doctype html><meta charset=utf-8>"
                "<script>parent.postMessage("
                "{type:'metnos.dialog.cancelled'},'*');</script>"
                "<p style='font:14px sans-serif;color:#a00'>"
                "✗ Dialogo annullato.</p>"
            ),
            status=200, content_type="text/html",
        )
    if state.get("completed"):
        return web.Response(
            text=(
                "<!doctype html><meta charset=utf-8>"
                "<script>parent.postMessage("
                "{type:'metnos.dialog.done',completion_text:''},'*');"
                "</script>"
                "<p style='font:14px sans-serif;color:#0a7'>"
                "✓ Dialogo completato.</p>"
            ),
            status=200, content_type="text/html",
        )
    dialog_steps = [_resolve_i18n_step(s) for s in (state.get("dialog") or [])]
    html = render_template(
        "dialog_form.html",
        dialog_id=dialog_id,
        title=_resolve_i18n_str(state.get("title") or "Dialogo"),
        description=_resolve_i18n_str(state.get("description") or ""),
        dialog=dialog_steps,
        role=request.get("role", "user"),
    )
    return web.Response(text=html, content_type="text/html")


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
                      f"dialogo {dialog_id} non trovato")
    if state.get("cancelled") or state.get("completed"):
        return web.Response(text="Dialogo non piu' attivo.", status=410,
                            content_type="text/plain")
    try:
        form = await request.post()
    except Exception:
        return _error(400, "invalid_form", "form data non valido")

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
        msgs = "; ".join(f"{e['var']}: {e['error']}" for e in errors)
        return web.Response(
            text=f"<h2>Errori di validazione</h2><p>{msgs}</p>"
                 f"<p><a href=\"/agent/dialog/{dialog_id}/form\">Indietro</a></p>",
            status=400, content_type="text/html",
        )
    # Tutti i campi OK: marca completato applicando consume_pending_step
    # in sequenza (single source of truth: stesso storage dei dialoghi
    # incrementali, niente bypass).
    import dialog_pending
    sender_id = state.get("__sender_id") or "host"
    for step in dialog:
        var = step.get("var")
        if var in values and values[var] is not None:
            dialog_pending.consume_pending_step(
                sender_id, dialog_id, var, values[var],
            )
        else:
            # Optional skipped: avanza con None per coerenza idx.
            dialog_pending.consume_pending_step(
                sender_id, dialog_id, var, None,
            )

    # ADR 0091: dopo aver consumato tutti gli step, processa il callback
    # `on_complete` se presente nel state (es. save_credentials_and_resume).
    # process_completion_callback ritorna sempre un messaggio user-facing.
    final_state = dialog_pending.load_pending(sender_id, dialog_id) or {}
    on_complete = final_state.get("on_complete")
    actor = final_state.get("actor") or "host"
    # turn_id del turno che ha emesso il dialog → la bolla risultato in chat
    # riaggancia i badge feedback ✓/✗ (chat.html li mostra solo con turn_id).
    origin_turn_id = final_state.get("origin_turn_id") or ""
    completion_message = ""
    if on_complete:
        try:
            # Reverse proxy / Cloudflare tunnel: leggi X-Forwarded-Proto per
            # determinare il vero scheme del client (HTTPS al edge anche se
            # qui arriva HTTP plain). Fallback al request.scheme diretto.
            xfp = request.headers.get("X-Forwarded-Proto") or request.scheme
            origin_override = f"{xfp}://{request.host}"
            from orchestration import process_completion_callback
            completion_message = process_completion_callback(
                sender_id, dialog_id, actor=actor, channel="http",
                host_override=origin_override,
            )
        except (ImportError, RuntimeError) as ex:
            log.exception("dialog_submit: process_completion_callback fallito")
            completion_message = (
                f"(Callback fallito: {type(ex).__name__}: {ex})"
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
            body_html = (
                f"<div data-redirect-url=\"{esc_url}\">"
                "<h2>Setup avviato</h2>"
                "<p>Apertura della pagina di autorizzazione&hellip;</p>"
                f"<p><a href=\"{esc_url}\">Apri manualmente</a> se non parte "
                "automaticamente.</p>"
                "</div>"
            )
        else:
            # Errore o messaggio testuale dal callback: includi data-completion-text
            # cosi' il parent (chat.html) puo' mostrarlo come bolla regolare in
            # chat invece del laconico "Risposta dialog inviata".
            esc_text = _escape_html(msg_for_display)
            esc_tid = origin_turn_id.replace('"', "&quot;")
            body_html = (
                f"<div data-completion-text=\"{esc_text}\" "
                f"data-turn-id=\"{esc_tid}\">"
                "<h2>Dialogo completato</h2>"
                f"<pre>{esc_text}</pre>"
                "<p><a href=\"/\">Torna a Metnos</a></p>"
                "</div>"
            )
    else:
        body_html = (
            "<h2>Dialogo completato</h2>"
            "<p>I valori sono stati registrati. Puoi tornare alla chat: "
            "<a href=\"/\">Metnos</a></p>"
        )
    return web.Response(text=body_html, content_type="text/html")


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
                      f"dialogo {dialog_id} non trovato")
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
        headers={"Cache-Control": "private, max-age=300"},
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
                      f"dialogo {dialog_id} non trovato")
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
        headers={"Cache-Control": "private, max-age=300"},
    )


async def dialog_cancel(request: web.Request) -> web.Response:
    """GET /agent/dialog/<dialog_id>/cancel — cancella il dialogo."""
    dialog_id = request.match_info["dialog_id"]
    state = _resolve_dialog_state(request, dialog_id)
    if state is None:
        return _error(404, "dialog_not_found",
                      f"dialogo {dialog_id} non trovato")
    sender_id = state.get("__sender_id") or "host"
    import dialog_pending
    dialog_pending.cancel_pending(sender_id, dialog_id)
    return web.Response(
        text="<h2>Dialogo annullato</h2><p><a href=\"/\">Torna a Metnos</a></p>",
        content_type="text/html",
    )



# --- Photo thumbnail serving (Opzione 1, 5/5/2026) -------------------------
#
# GET /agent/photos/web?u=<url> — proxy fetch per immagini esterne (Vision API
# results). Bypassa hotlinking-block dei CDN (TikTok, Instagram, Facebook
# lookaside) usando UA Mozilla + Referer locale. Cache disk.
_WEB_PHOTO_CACHE_DIR = Path.home() / ".cache" / "metnos" / "web_photos"
_WEB_PHOTO_CACHE_TTL_S = 7 * 24 * 3600

# SSRF guard: l'endpoint e' anonimo (whitelist `/agent/photos/` in
# http_auth.py), quindi un URL fornito dall'utente NON deve poter colpire
# servizi interni. Rifiutiamo ogni host che risolve a IP loopback/privato/
# link-local/riservato (es. 127.0.0.1, 169.254.169.254 metadata, 10/8, ...).
_WEB_PHOTO_MAX_REDIRECTS = 4


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
    # SSRF guard (endpoint anonimo): rifiuta host che risolvono a IP non
    # pubblici (loopback / privati / link-local metadata / riservati).
    err, detail = _validate_fetch_url(raw_url)
    if err:
        status = 400 if err in ("invalid_url", "blocked") else 502
        return _error(status, err, detail or err)
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
                                     headers={"Cache-Control": "public, max-age=86400"})
        except OSError:
            pass
    # Fetch fresh. I redirect NON sono seguiti automaticamente: ogni hop e'
    # ri-validato (un CDN potrebbe redirigere verso un host interno → SSRF).
    import urllib.request as _ur
    import urllib.error as _ue

    class _NoRedirect(_ur.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            # Disabilita il follow automatico: lo gestiamo a mano sotto.
            return None

    opener = _ur.build_opener(_NoRedirect)
    cur_url = raw_url
    data = b""
    ctype = "image/jpeg"
    try:
        for _hop in range(_WEB_PHOTO_MAX_REDIRECTS + 1):
            req = _ur.Request(cur_url, headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
            })
            try:
                with opener.open(req, timeout=8) as r:
                    data = r.read()
                    ctype = r.headers.get("Content-Type", "image/jpeg")
                break
            except _ue.HTTPError as he:
                # 3xx senza follow → HTTPError con Location: valida e itera.
                if he.code in (301, 302, 303, 307, 308):
                    loc = he.headers.get("Location", "")
                    nxt = urllib.parse.urljoin(cur_url, loc)
                    verr, vdetail = _validate_fetch_url(nxt)
                    if verr:
                        return _error(400, "blocked_redirect",
                                      vdetail or "redirect to non-public host")
                    cur_url = nxt
                    continue
                raise
        else:
            return _error(400, "too_many_redirects", "redirect limit exceeded")
    except (_ue.HTTPError, _ue.URLError, OSError, TimeoutError) as ex:
        return _error(404, "fetch_failed", f"{type(ex).__name__}: {ex}")
    # Limit cache size: skip > 5MB
    if len(data) < 5 * 1024 * 1024:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(data)
        except OSError:
            pass
    if not ctype.startswith("image/"):
        # Non un'immagine reale (es. HTML login page)
        return _error(404, "not_image", f"content-type {ctype} not image")
    return web.Response(body=data, content_type=ctype,
                         headers={"Cache-Control": "public, max-age=86400"})


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
    admin_key = request.app.get("admin_key", "")
    if not admin_key:
        return _error(500, "no_admin_key", "server admin key not configured")
    if not photo_endpoint.verify(turn_id, idx, size, exp, token, admin_key):
        return _error(401, "invalid_token", "signed token invalid or expired")
    src_path = photo_endpoint.resolve_path(turn_id, idx)
    if not src_path:
        return _error(404, "not_found", "photo not found in recent turns")
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
    admin_key = request.app.get("admin_key", "")

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

    Ordine: device_id pairato → users.find_user_by_channel("http", device_id);
    altrimenti host singolo se esiste; altrimenti l'actor stesso come
    user_id "logico" (anonymous LAN trusted).

    Niente LLM, §7.9: lookup deterministico in users.db.
    """
    import users as _users
    actor = _resolve_actor(request, {})
    # Tenta lookup user_channels: device_id paired su channel='http'.
    try:
        device_id = request.get("device_id") or ""
        if device_id:
            # Cerca quale user ha device_id pairato come canale 'http'.
            for u in _users.list_users():
                ch = _users.get_channel(u["id"], "http")
                if ch and str(ch.get("recipient_id") or "") == str(device_id):
                    return u["id"]
    except Exception as e:
        log.debug("session user lookup: %s", e)
    # Fallback: host singolo se c'e' (LAN trusted bootstrap).
    try:
        hosts = _users.list_users(role="host")
        if len(hosts) == 1:
            return hosts[0]["id"]
    except Exception as e:
        log.debug("session host fallback: %s", e)
    # Ultima spiaggia: usa l'actor string come user_id logico (le sessioni
    # vivono nello stesso namespace ma sono isolate). Non e' un id valido
    # in users.db ma e' coerente per (user, channel) → 1 active session.
    return actor or "anonymous"


async def session_register(request: web.Request) -> web.Response:
    """POST /agent/session/register

    Body JSON: `{device_label?: str}`. Ritorna `{device_token}` su
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
    user_id = await _resolve_session_user_id(request)
    import active_sessions as _as
    try:
        res = _as.register_session(user_id, "http", device_label=device_label)
    except ValueError as e:
        return _error(400, "session_register_invalid", str(e))
    if res.get("conflict"):
        return web.json_response(res, status=409)
    return web.json_response(res)


async def session_takeover(request: web.Request) -> web.Response:
    """POST /agent/session/takeover

    Body JSON: `{takeover_token: str, device_label?: str}`. Atomic:
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
        return _error(400, "takeover_token_required", "takeover_token mancante")
    device_label = str(body.get("device_label") or "")[:200]
    import active_sessions as _as
    try:
        res = _as.confirm_takeover_with_notify(token, new_device_label=device_label)
    except ValueError as e:
        return _error(409, "takeover_invalid", str(e))
    return web.json_response(res)


async def session_ping(request: web.Request) -> web.Response:
    """POST /agent/session/ping

    Body JSON: `{device_token: str}`. Aggiorna last_seen_at. 200 se
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
        return _error(400, "device_token_required", "device_token mancante")
    import active_sessions as _as
    ok = _as.touch_session(token)
    if not ok:
        return web.json_response(
            {"ok": False, "revoked": True}, status=409,
        )
    return web.json_response({"ok": True})


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
        return _error(400, "device_token_required", "device_token mancante")
    reason = str(body.get("reason") or "manual")[:60]
    import active_sessions as _as
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
        return _error(400, "device_token_required", "device_token mancante")
    import active_sessions as _as

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
    sse_set = request.app.setdefault("sse_responses", set())
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
    actor = data["actor"]
    conv_id = data["conversation_id"]
    sender_id = data["sender_id"]
    original_query = data["original_query"]
    reference_images = data["reference_images"]
    admin_key = request.app.get("admin_key", "")

    from turn_events import TurnEventLog, TurnEventProgress
    import uuid as _uuid
    event_log = TurnEventLog.get()
    turn_id = _uuid.uuid4().hex[:16]
    # conversation_id/actor/query: consentono a turns_recent di ritrovare il
    # turn ancora running se il client ricarica la pagina (navigazione chat →
    # dashboard → chat). Il turn non è ancora nei JSONL persistiti.
    event_log.create(turn_id, conversation_id=conv_id, actor=actor,
                     query=original_query)

    # Risposta immediata (dialog/cap pending): nessun run_turn, append `final`
    # nel log e chiudi. Il client si attacca e riceve subito l'esito.
    if immediate_msg is not None:
        event_log.append(turn_id, "final", {
            "turn_id": turn_id,
            "final_message": immediate_msg,
            "final_message_html": _safe_final_html(immediate_msg),
            "final_kind": "answer",
            "total_ms": 0,
            "expandable_caps": [],
            "attachments": [],
            "gallery_url": None,
            "n_total_matches": 0,
            "path": [],
        })
        event_log.close(turn_id)
        return web.json_response({
            "turn_id": turn_id,
            "stream_url": f"/agent/turns/{turn_id}/stream",
        }, status=202)

    # Spawn task. Esegue run_turn in executor + scrive eventi nel log.
    loop = asyncio.get_running_loop()
    refs = list(reference_images or [])

    import agent_runtime as _agent_runtime
    async def _run_async():
        progress = TurnEventProgress(turn_id, log=event_log)
        try:
            log_obj = await loop.run_in_executor(
                None,
                lambda: _agent_runtime.run_turn(
                    query_for_run, actor=actor, channel="http",
                    conversation_id=conv_id,
                    progress=progress,
                    reference_images=refs or None,
                ),
            )
            _save_cap_pending_if_any(sender_id, original_query, log_obj)
            event_log.append(turn_id, "final",
                             _build_final_event_payload(log_obj, admin_key))
        except Exception as ex:
            log.exception("turn_submit run failed: %s", turn_id)
            event_log.append(turn_id, "error", {
                "message": str(ex),
                "type": type(ex).__name__,
            })
        finally:
            event_log.close(turn_id)

    asyncio.create_task(_run_async(), name=f"turn-{turn_id}")

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
    if not event_log.has(turn_id):
        return _error(404, "turn_not_found",
                       f"turn {turn_id!r} non in event log "
                       "(potrebbe essere troppo vecchio; usa "
                       "GET /agent/turns/{turn_id})")
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
    if event_log.has(turn_id):
        st = event_log._turns[turn_id]
        return web.json_response({
            "turn_id": turn_id,
            "state": "complete" if st.closed else "running",
            "events": [
                {"id": e.id, "event_type": e.event_type,
                 "payload": e.payload, "ts": e.ts}
                for e in st.events
                if e.event_type != "_heartbeat"
            ],
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
                        return web.json_response({
                            "turn_id": turn_id,
                            "state": "complete",
                            "persistent": True,
                            "final_message": d.get("final_message"),
                            "final_kind": d.get("final_kind"),
                            "steps_summary": [
                                {"step": s.get("step_num"),
                                 "tool": s.get("chosen_tool"),
                                 "ok": bool(
                                    (s.get("result") or {}).get("ok", True)
                                 ) if isinstance(s.get("result"), dict)
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
        return _error(400, "conversation_id_required", "conversation_id query param richiesto")
    # Multi-user segregation (8/5/2026): filtra ANCHE per actor, mai solo
    # per conversation_id. Senza questo filtro un utente B con stesso
    # conv_id (collisione UUID o brute force) vedrebbe la storia di A.
    actor = _resolve_actor(request, {})
    try:
        limit = min(200, max(1, int(request.query.get("limit", "50"))))
    except (ValueError, TypeError):
        limit = 50
    try:
        since_ts = float(request.query.get("since_ts", "0") or "0")
    except (ValueError, TypeError):
        since_ts = 0.0

    turns_dir = _C.PATH_USER_DATA / "turns"
    out: list[dict] = []
    if not turns_dir.exists():
        return web.json_response({"turns": []})

    # Scan ultimi 7 giorni di JSONL (ordine reverse per latest-first).
    files = sorted(turns_dir.glob("*.jsonl"), reverse=True)[:7]
    request.app.get("admin_key", "")
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
                    # SEGREGAZIONE MULTI-USER: actor del turn DEVE matchare
                    # l'actor della request corrente. Niente cross-user leak.
                    if t.get("actor") != actor:
                        continue
                    ts_start = float(t.get("ts_start") or 0)
                    if since_ts and ts_start <= since_ts:
                        continue
                    ts_end = float(t.get("ts_end") or 0)
                    in_flight = ts_end == 0 or not t.get("final_kind")
                    final_msg = t.get("final_message") or ""
                    out.append({
                        "turn_id": t.get("turn_id", ""),
                        "query": t.get("user_query", ""),
                        "final_message": final_msg,
                        "final_message_html": _safe_final_html(final_msg) if final_msg else "",
                        "final_kind": t.get("final_kind", ""),
                        "ts_start": ts_start,
                        "ts_end": ts_end if ts_end else None,
                        "total_ms": int((ts_end - ts_start) * 1000) if ts_end else None,
                        "in_flight": in_flight,
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
        for rt in TurnEventLog.get().running_turns(conv_id, actor):
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
                "in_flight": True,
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
    ".webmanifest": "application/manifest+json",
    ".txt":  "text/plain",
}


def _static_response(name: str) -> web.Response:
    """Serve un file statico da `runtime/static/`. 404 se non esiste o
    se il path tenta uscire dalla dir (path traversal)."""
    safe = (_STATIC_DIR / name).resolve()
    try:
        safe.relative_to(_STATIC_DIR.resolve())
    except ValueError:
        return web.Response(status=404, text="not found")
    if not safe.is_file():
        return web.Response(status=404, text="not found")
    ct = _STATIC_CT.get(safe.suffix.lower(), "application/octet-stream")
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
      2. L'admin invia il URL `https://chat.metnos.com/pair/<token>` al device target
         (cellulare, notebook fuori LAN) via canale fidato (Telegram, AirDrop, ...).
      3. Il device apre il URL UNA VOLTA → token consumato + binding device_id
         in user_channels.recipient_id + cookie USER_COOKIE firmato set.
      4. Future richieste dal device portano il cookie → ruolo `user`.
    """
    from http_auth import USER_COOKIE, USER_COOKIE_TTL_S, issue_user_cookie
    import users as _users

    token = request.match_info["token"]
    if not token or len(token) < 16:
        return web.Response(text="token non valido", status=400,
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

    admin_key = request.app.get("admin_key", "")
    cookie_val = issue_user_cookie(admin_key, device_id)
    resp = web.HTTPFound("/")
    resp.set_cookie(
        USER_COOKIE, cookie_val,
        max_age=USER_COOKIE_TTL_S,
        httponly=True,
        secure=True,
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
            ok=False, title="Scambio token fallito",
            body=f"<code>{html_escape(type(ex).__name__)}: {html_escape(str(ex))}</code>",
        )

    if not ok:
        return _oauth_result_page(
            ok=False, title="Scambio token fallito",
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
                resume_body = (
                    f"Token salvato. Executor <code>{html_escape(str(executor))}</code> non "
                    f"in catalog: rilancio annullato.")
            else:
                import agent_runtime as _ar
                res = _ar.invoke_executor(
                    ex, args_base, timeout_s=getattr(ex, "timeout_s", 30),
                    actor=entry.get("actor") or None,
                    channel=entry.get("channel") or None,
                )
                resume_body = _format_resume_result(res)
        except (PermissionError, KeyError, RuntimeError, TypeError) as ex:
            log.exception("oauth_callback: resume_call fallito")
            resume_body = (
                f"Token salvato, ma rilancio di <code>{html_escape(str(executor))}</code> "
                f"fallito: {html_escape(type(ex).__name__)}: {html_escape(str(ex))}")
    else:
        resume_body = "Token salvato. Nessun executor da ri-invocare."

    return _oauth_result_page(
        ok=True, title="Setup completato",
        body=resume_body,
    )


def _format_resume_result(res) -> str:
    """Markdown/HTML compatto per il risultato della ri-invocazione."""
    if not isinstance(res, dict):
        return f"<pre>{html_escape(str(res)[:600])}</pre>"
    if not res.get("ok"):
        err = res.get("error", "errore sconosciuto")
        return f"Executor ha risposto errore: <code>{html_escape(str(err))}</code>"
    summary = res.get("summary") or res.get("final_message_hint") or ""
    entries = res.get("entries") or []
    if summary and not entries:
        return f"<p>{summary}</p>"
    if entries:
        lines = [f"<p>Trovate <strong>{len(entries)}</strong> entries.</p><ul>"]
        for e in entries[:20]:
            if isinstance(e, dict):
                title = (e.get("summary") or e.get("title")
                         or e.get("subject") or e.get("name")
                         or str(e)[:80])
                lines.append(f"<li>{title}</li>")
            else:
                lines.append(f"<li>{str(e)[:80]}</li>")
        if len(entries) > 20:
            lines.append(f"<li>…(altre {len(entries) - 20} omesse)</li>")
        lines.append("</ul>")
        return "".join(lines)
    return "<p>Executor eseguito. Nessun output significativo.</p>"


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
    query = turn.get("user_query") or ""
    if not query:
        return _error(400, "no_query", "turn has no user_query to retry")

    # Cancellazione cache via cosine match: entries canonical_query_log il
    # cui canonical_query ha BGE similarity >= 0.7 con la user_query del
    # turno rifiutato → il retry non riusa pattern appena bocciati.
    deleted = 0
    try:
        from mnestoma import Mnestoma
        mn = Mnestoma()
        deleted = mn.delete_canonical_query_log_matching(query, cosine_threshold=0.7)
    except Exception as ex:
        log.warning("retry %s: cache cleanup failed: %r", turn_id, ex)
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
