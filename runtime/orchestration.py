"""orchestration — auto-orchestrazione di get_inputs per `decision='needs_inputs'`
(ADR 0091, 5/5/2026).

Il pattern Strato 2 di ADR 0089 (admin emette stringa testuale «mi servono
credenziali») viene rimpiazzato da una orchestrazione lato runtime: quando
admin (o un altro tool) ritorna `decision="needs_inputs"`, il runtime invoca
sinteticamente `get_inputs(fmt="auto")` con il payload dichiarato e salva un
callback `on_complete` insieme allo stato del dialogo. Quando il dialogo si
completa (HTTP form submit oppure conversazione sequenziale Telegram), il
runtime applica il callback: salva le credenziali cifrate e ri-invoca admin
con gli args originali (resume_call).

Architettura:

  +----------------+         +-------------------+         +---------------+
  | admin (PLANNER)| -- needs_inputs ----------> | agent_runtime |
  +----------------+         +-------------------+         +-------+-------+
                                                                   |
                                                                   v
                                                  invoke_get_inputs_internal()
                                                                   |
                                                                   v
                                                         +-------------------+
                                                         | dialog_pending    |
                                                         | (state + on_complete)
                                                         +---------+---------+
                                                                   |
                       <-------- final_answer carta UI -------------+
                                                                   |
        (utente compila form HTTP o sequenza dialog Telegram)      |
                                                                   v
                                                       process_completion_callback()
                                                                   |
                              +------------------------------------+
                              v                                    v
                   credentials.store(domain, ...)        invoke_verb_unique("admin", ...)
                                                                   |
                                                                   v
                                                         risposta utente nel canale

the design guide §7.9 (deterministico > LLM): tutta la pipeline di orchestrazione
e' codice deterministico. L'unico LLM nella catena e' il PLANNER iniziale
che chiama admin la prima volta; il resume e' una chiamata diretta al verb.
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))

import dialog_pending
from logging_setup import get_logger
from messages import get as _msg

log = get_logger(__name__)


def _shape_result_for_chat(res) -> str:
    """Backstop universale no-raw-leak (§output formatter): MAI `json.dumps`
    grezzo in chat (l'utente vedrebbe «{...}», bypassando l'i18n). Testo pulito
    da final_message_hint/summary → `✗ <err>` → entries (reader §2.6) →
    MSG_ACTION_DONE. Single source per tutti i result-shaper di orchestration."""
    if isinstance(res, dict):
        msg = res.get("final_message_hint") or res.get("summary")
        if msg:
            return msg
        if res.get("ok") is False:
            err = res.get("error") or res.get("error_class") or ""
            return f"✗ {err}" if err else _msg("ERR_GENERIC")
        # READER (§2.6: `entries` = dati letti, `results` = mutazione): i dati
        # SONO la risposta — un resume che legge un foglio e risponde solo
        # «✓ Operazione completata» butta il contenuto (turn 1e895534→«1»).
        # Render compatto deterministico; i mutanti (results) restano al ✓.
        entries = res.get("entries")
        if isinstance(entries, list) and entries:
            return _fmt_reader_entries(entries)
        return _msg("MSG_ACTION_DONE")
    return str(res)


_READER_PREVIEW_CAP = 20


def _fmt_reader_entries(entries: list) -> str:
    """Render compatto delle entries di un reader per la chat (§7.9, no LLM).
    RIGHE di foglio (list[list], read_spreadsheet §2.6) → tabella markdown
    (riga-0 = header se tutte etichette non-numeriche, come
    `extract_entries._looks_like_header_row`); altrimenti lista compatta via
    `_fmt_entries_block`. Cap §2.7 con nota MSG_TOP_OF."""
    rows = [e for e in entries if isinstance(e, (list, tuple))]
    if rows and len(rows) == len(entries):
        from extract_entries import _looks_like_header_row
        from output_format import format_table
        out: list[str] = []
        body = [list(map(lambda c: "" if c is None else str(c), r))
                for r in rows]
        headers = None
        if _looks_like_header_row(body[0]):
            headers, body = body[0], body[1:]
        if len(body) > _READER_PREVIEW_CAP:
            out.append(_msg("MSG_TOP_OF", top=_READER_PREVIEW_CAP,
                            total=len(body)))
            body = body[:_READER_PREVIEW_CAP]
        if not headers:
            width = max((len(r) for r in body), default=0)
            headers = [f"c{i+1}" for i in range(width)]
        width = len(headers)
        body = [(r + [""] * (width - len(r)))[:width] for r in body]
        out.append(format_table(headers=headers, rows=body))
        return "\n".join(out)
    return _fmt_entries_block(entries, _READER_PREVIEW_CAP)


# ── Helper: sender_id stabile per lo storage ──────────────────────────

def _safe_sender(actor: str, channel: Optional[str]) -> str:
    """Deriva un sender_id stabile per lo storage del dialogo. Coerente con
    `executors/get_inputs/get_inputs.py:_safe_sender` (single source of
    truth: stessa shape `{channel}:{actor}` o `actor`)."""
    if not actor:
        actor = "host"
    if channel:
        return f"{channel}:{actor}"
    return actor


from timefmt import now_iso_offset as _utc_now_iso


# ── invoke_get_inputs_internal ────────────────────────────────────────

def invoke_get_inputs_internal(*,
                                sender_id: str,
                                title: str,
                                description: Optional[str],
                                dialog: list[dict],
                                fmt: str = "auto",
                                on_complete: Optional[dict] = None,
                                actor: str = "host",
                                channel: Optional[str] = None,
                                timeout_s: Optional[int] = None,
                                origin_turn_id: str = "") -> dict:
    """Orchestrazione runtime-side di `get_inputs` (ADR 0091).

    Replica il comportamento dell'executor `get_inputs.invoke()` ma vive nel
    runtime cosi' puo' iniettare il campo `on_complete` (non visibile al
    PLANNER) nello stato persisto. Il runtime intercetta il completamento
    via `process_completion_callback` ed esegue il callback dichiarativo.

    Args:
      sender_id: chiave di storage (`{channel}:{actor}` oppure `actor`).
      title: titolo della carta UX (max 80 char).
      description: secondo stringa opzionale.
      dialog: lista di step `{var, prompt, schema, ...}` (validata dall'
              executor get_inputs lato esecuzione, qui assumiamo OK; viene
              comunque ri-validata dalla forma JSON sul disco).
      fmt: 'auto' | 'dialogue' | 'form' | 'voice'.
      on_complete: dict callback dichiarativo (vedi process_completion_callback).
      actor: identita' user (multi-user, ADR 0035).
      channel: 'http' | 'telegram' | None (auto-detect lato fmt='auto').
      timeout_s: TTL del dialogo. Se None, default per forma via
                 `dialog_pending.default_timeout_for` (60s semplici, 600s form/credenziali).

    Returns:
      dict con la stessa shape di get_inputs.invoke():
      {ok, decision="input_required", dialog_id, step_total, fmt,
       final_message_hint, expandable_caps: [{kind: "get_inputs_response", ...,
       sender_for_state: sender_id}]}
    """
    if not isinstance(title, str) or not title.strip():
        return {"ok": False, "error": _msg("MSG_ORCH_TITLE_MISSING")}
    if not isinstance(dialog, list) or not dialog:
        return {"ok": False, "error": _msg("MSG_ORCH_DIALOG_EMPTY")}

    n_steps = len(dialog)
    # Risolvi fmt='auto' lato runtime. Su HTTP:
    # - se almeno uno step ha kind=choice_with_preview → form (radio +
    #   thumbnail face crops + foto intera via context_image_path: il
    #   testo non puo' rendere immagini, sarebbe inutilizzabile)
    # - se n_steps >= 2 → form (allineato con get_inputs.py:_decide_fmt
    #   8/5/2026, soglia abbassata 3→2)
    # Su Telegram (10/6/2026, parita' con get_inputs.py:_decide_fmt):
    # - tutti gli step yes_no/choice/choice_with_preview → telegram_inline
    #   (inline keyboard: un bottone per alternativa; vale per TUTTI i
    #   flussi di autorizzazione orchestrati — strato3/frontier, cap-expand,
    #   approva/edita/rifiuta — non solo per i dialog aperti dal PLANNER).
    # - altrimenti dialogue (sequenza testuale, degrado onesto §2.8 per
    #   kind non rappresentabili a bottoni: text/credentials/number/...).
    has_preview_step = any(
        (s.get("schema") or {}).get("kind") == "choice_with_preview"
        for s in dialog
    )
    if fmt == "auto":
        if channel == "http":
            # form anche a 1 SOLO step se tutto e' cliccabile (choice/yes_no):
            # una scelta si clicca, non si trascrive — parita' con la regola
            # Telegram (inline keyboard), turn 1e895534. Kind testuali
            # (text/credentials/...) mono-step restano dialogue.
            from channels.inline_ui import all_choice_like
            resolved_fmt = ("form"
                            if (has_preview_step or n_steps >= 2
                                or all_choice_like(dialog))
                            else "dialogue")
        elif channel == "telegram":
            from channels.inline_ui import all_inline_compatible
            resolved_fmt = ("telegram_inline"
                            if all_inline_compatible(dialog) else "dialogue")
        else:
            resolved_fmt = "dialogue"
    elif fmt == "voice":
        resolved_fmt = "dialogue"  # stub: voice degrada a dialogue
    else:
        resolved_fmt = fmt
    # `form` e' un concetto della chat HTTP (iframe inline / pagina standalone).
    # Su canali non-HTTP (Telegram, voce) non c'e' modo di renderizzare un form
    # con campi editabili → degrada a dialogue (sequenza testuale). Regola
    # generale, channel-aware: un executor puo' chiedere fmt='form' senza dover
    # conoscere il canale; il runtime fa il downgrade dove serve.
    if resolved_fmt == "form" and channel and channel != "http":
        resolved_fmt = "dialogue"

    if timeout_s is None:
        timeout_s = dialog_pending.default_timeout_for(dialog, on_complete)

    dialog_id = uuid.uuid4().hex[:16]
    state = {
        "dialog_id": dialog_id,
        "title": title,
        "description": description,
        "dialog": dialog,
        "fmt": resolved_fmt,
        "fmt_arg": fmt,
        "values_collected": {},
        "step_index": 0,
        "started_at": _utc_now_iso(),
        "actor": actor,
        "channel": channel or "",
        "timeout_s": int(timeout_s),
        "completed": False,
        "cancelled": False,
        # ADR 0091: callback dichiarativo. Persiste insieme allo stato
        # cosi' il completamento (sequenziale o form) puo' processarlo
        # senza rebuild lato runtime.
        "on_complete": on_complete,
        # Comodita' per l'orchestratore: tieni traccia del sender per il
        # cap-pending registry, cosi' il daemon trova lo state al posto
        # giusto senza dover ricalcolarlo.
        "sender_id": sender_id,
        # turn_id del turno che ha EMESSO il dialog (es. "crea calendario" →
        # needs_inputs): persisterlo permette al completamento-form di
        # agganciare i badge feedback ✓/✗ alla bolla risultato (chat.html) su
        # un turn REALE gia' nel JSONL. Senza, il form-completion non e' un
        # turn → niente badge (regressione 3/6 passaggio dialogue→form).
        "origin_turn_id": origin_turn_id,
    }
    try:
        dialog_pending.save_pending(sender_id, dialog_id, state)
    except (OSError, ValueError, TypeError) as ex:
        return {"ok": False, "error": _msg("MSG_ORCH_SAVE_PENDING_FAILED", detail=str(ex))}

    final_message_hint = _build_final_message_hint(state, resolved_fmt)
    return {
        "ok": True,
        "decision": "input_required",
        "dialog_id": dialog_id,
        "step_index": 0,
        "step_total": n_steps,
        "values": {},
        "fmt": resolved_fmt,
        "final_message_hint": final_message_hint,
        "expandable_caps": [{
            "kind": "get_inputs_response",
            "dialog_id": dialog_id,
            "step_total": n_steps,
            "fmt": resolved_fmt,
            "sender_for_state": sender_id,
        }],
        "metadata": {
            "title": title,
            "n_steps": n_steps,
            "fmt": resolved_fmt,
            "actor": actor,
            "channel": channel or "",
            "orchestrated": True,  # flag: emesso dall'orchestratore, non dal PLANNER
        },
    }


def _build_final_message_hint(state: dict, fmt: str) -> str:
    """Genera il testo da mostrare all'utente per il primo step.

    Coerente con `executors/get_inputs/get_inputs.py:_build_final_message_hint`:
    duplichiamo la logica qui per evitare un import del modulo executor (che
    vive in `executors/`, non in PYTHONPATH del runtime).
    """
    def _resolve_msg(s):
        if isinstance(s, str) and s.startswith("MSG_"):
            return _msg(s)
        return s
    title = _resolve_msg(state.get("title") or _msg("MSG_ORCH_DEFAULT_QUESTION_TITLE"))
    dialog = state.get("dialog") or []
    n = len(dialog)
    dialog_id = state.get("dialog_id") or ""
    descr = _resolve_msg(state.get("description") or "")
    if fmt == "form":
        # URL relativo: il browser usa lo stesso host della chat. Risolve
        # multi-LAN/multi-device senza dover indovinare l'IP server.
        # `inline_form_path` e' il segnale per chat.html: se presente,
        # renderizza un iframe inline invece di un link cliccabile.
        url = f"/agent/dialog/{dialog_id}/form"
        lines = [title]
        if descr:
            lines.append(descr)
        lines.append("")
        lines.append(f"INLINE_FORM:{url}")
        lines.append(_msg("MSG_ORCH_FORM_FIELDS_HINT", n=n))
        return "\n".join(lines)
    # dialogue (default)
    first = dialog[0]
    prompt = _resolve_msg(first.get("prompt") or "?")
    lines = [title]
    if descr:
        lines.append(descr)
    lines.append("")
    lines.append(_msg("MSG_ORCH_STEP_PROMPT", n=n, prompt=prompt))
    schema = first.get("schema") or {}
    if schema.get("kind") == "credentials":
        lines.append(_msg("MSG_ORCH_MASKED_HINT"))
    elif schema.get("kind") == "choice":
        choices = schema.get("choices") or []
        if choices:
            lines.append("")
            for i, ch in enumerate(choices, 1):
                _lbl = (ch.get("label", ch.get("value", ch))
                        if isinstance(ch, dict) else ch)
                lines.append(f"  {i}. {_lbl}")
    lines.append("")
    lines.append(_msg("MSG_ORCH_REPLY_NEXT_HINT"))
    return "\n".join(lines)


# ── process_completion_callback ───────────────────────────────────────

from dataclasses import dataclass as _dc, field as _dcfield


@_dc
class CompletionResult:
    """Esito STRUTTURATO di un dialog completato (5/7/2026, bug zip-line).

    Prima i consumer ritornavano solo str: un resume che ri-eseguiva un TURNO
    INTERO (disambiguazione foto) buttava attachments/gallery e la meta del
    turno — 74 foto rese come testo, senza badge né dati. `text` resta il
    contratto minimo (i canali senza media lo usano tal quale); attachments
    e meta viaggiano quando il dispatch li ha."""
    text: str = ""
    attachments: list = _dcfield(default_factory=list)
    turn_id: str = ""
    total_ms: int = 0
    target_device: str = ""
    # Stessa forma del payload `final` dei turni normali (SoT shape:
    # http_routes_agent._build_final_event_payload) — la bolla del resume
    # deve avere gallery-link e breadcrumb executor come ogni turno.
    gallery_url: str = ""
    n_total_matches: int = 0
    path: list = _dcfield(default_factory=list)


def _completion_from_turnlog(new_log) -> CompletionResult:
    """CompletionResult da un TurnLog di run_turn (resume full-turn)."""
    try:
        total_ms = int((getattr(new_log, "ts_end", 0)
                        - getattr(new_log, "ts_start", 0)) * 1000)
    except Exception:
        total_ms = 0
    atts = list(getattr(new_log, "attachments", None) or [])
    turn_id = getattr(new_log, "turn_id", "") or ""
    n_total = sum(1 for a in atts
                  if isinstance(a, dict) and a.get("kind") != "file")
    path_summary = []
    for st in getattr(new_log, "steps", None) or []:
        tool = getattr(st, "chosen_tool", "") or ""
        if not tool or getattr(st, "error", None) == "auto_final_on_duplicate":
            continue
        res = st.result if isinstance(getattr(st, "result", None), dict) else {}
        path_summary.append({"tool": tool, "ok": bool(res.get("ok", True))})
    return CompletionResult(
        text=getattr(new_log, "final_message", "") or "",
        attachments=atts,
        turn_id=turn_id,
        total_ms=max(0, total_ms),
        target_device=getattr(new_log, "target_device", None) or "",
        gallery_url=(f"/agent/gallery/{turn_id}" if (n_total and turn_id) else ""),
        n_total_matches=n_total,
        path=path_summary,
    )


def process_completion_callback(sender_id: str, dialog_id: str,
                                  *, actor: str = "host",
                                  channel: Optional[str] = None,
                                  host_override: Optional[str] = None
                                  ) -> "CompletionResult":
    """Wrapper pubblico: normalizza l'esito dei dispatch a CompletionResult
    (i dispatch legacy ritornano str; quelli full-turn CompletionResult)."""
    out = _dispatch_completion(
        sender_id, dialog_id, actor=actor, channel=channel,
        host_override=host_override)
    if isinstance(out, CompletionResult):
        return out
    return CompletionResult(text=str(out) if out is not None else "")


def _dispatch_completion(sender_id: str, dialog_id: str,
                                  *, actor: str = "host",
                                  channel: Optional[str] = None,
                                  host_override: Optional[str] = None):
    """Esegue il callback dichiarativo `on_complete` di un dialogo completato.

    Chiamato:
      - dal Telegram daemon quando l'ultimo step e' stato consumato in
        sequenza dialogue;
      - dal POST /agent/dialog/<id>/submit quando il form HTTP e' stato
        inviato con tutti i campi validi.

    Pre-condizione: il dialogo `<sender_id>/<dialog_id>` deve essere in
    stato `completed=True` (i values sono stati raccolti). Se non lo e',
    ritorna messaggio diagnostico.

    Comportamento per type:
      - `save_credentials_and_resume`:
         1. credentials.store(credentials_domain, {username, password, context})
         2. invoke_verb_unique(resume_call, **resume_args) → ottieni il
            risultato dal verb.
         3. Ritorna la summary per il canale (carta vaglio approvazione,
            esito esecuzione, eccetera).

    Per type non riconosciuti: ritorna messaggio di errore.

    Returns:
      CompletionResult: `.text` = messaggio user-facing (mai vuoto per i
      canali testuali); attachments/turn-meta presenti quando il dispatch
      ri-esegue un turno completo (resume/disambiguazione). I dispatch
      legacy che ritornano str vengono normalizzati qui.
    """
    state = dialog_pending.load_pending(sender_id, dialog_id)
    if state is None:
        return _msg("MSG_ORCH_DIALOG_NOT_FOUND", dialog_id=dialog_id)
    if not state.get("completed"):
        return _msg("MSG_ORCH_DIALOG_INCOMPLETE")
    on_complete = state.get("on_complete")
    if not isinstance(on_complete, dict):
        # Niente callback dichiarato: solo conferma generica.
        return _msg("MSG_ORCH_DIALOG_DONE")

    callback_type = on_complete.get("type")
    values = state.get("values_collected") or {}

    if callback_type == "save_credentials_and_resume":
        return _process_save_credentials_and_resume(
            on_complete, values, actor=actor,
        )

    if callback_type == "set_credential_mandates_and_resume":
        return _process_set_credential_mandates_and_resume(
            on_complete, values, actor=actor, channel=channel,
        )

    if callback_type == "expand_cap_and_resume":
        return _process_expand_cap_and_resume(
            on_complete, values, actor=actor, channel=channel,
        )

    if callback_type == "resume_executor_with_values":
        return _process_resume_executor_with_values(
            on_complete, values, actor=actor, channel=channel,
        )

    if callback_type == "resume_executor_values_tail":
        return _process_resume_executor_values_tail(
            on_complete, values, actor=actor, channel=channel,
        )

    if callback_type == "start_oauth_redirect_flow":
        return _process_start_oauth_redirect_flow(
            on_complete, values, sender_id=sender_id,
            dialog_id=dialog_id, channel=channel, actor=actor,
            host_override=host_override,
        )

    if callback_type == "resume_planner_with_dialog_values":
        return _process_resume_planner_with_dialog_values(
            on_complete, values, actor=actor, channel=channel,
        )

    if callback_type == "rerun_query_disambiguated":
        return _process_rerun_query_disambiguated(
            on_complete, values, actor=actor, channel=channel)
    if callback_type == "restart_turn_with_chosen_query":
        return _process_restart_turn_with_chosen_query(
            on_complete, values, actor=actor, channel=channel,
        )

    if callback_type == "strato3_choice_dispatch":
        return _process_strato3_choice_dispatch(
            on_complete, values, actor=actor, channel=channel,
        )

    if callback_type == "gate_dispatch":
        return _process_gate_dispatch(
            on_complete, values, actor=actor, channel=channel,
        )

    if callback_type == "resume_engine_gate":
        return _process_resume_engine_gate(
            on_complete, values, actor=actor, channel=channel,
        )

    if callback_type == "resume_executor_gate_tail":
        return _process_resume_executor_gate_tail(
            on_complete, values, actor=actor, channel=channel,
        )

    if callback_type == "defer_turn":
        # Fase 7 A.1: consenso al DIFFERIMENTO di un turno su device offline.
        # Sì → accoda (deferred_turns) e chiudi; il re-run parte al primo
        # poll del device (agent_server) e l'esito arriva via user_notices.
        decision = next(iter((values or {}).values()), None) if values else None
        if decision != "approve":
            return _msg("MSG_GATE_NO_ACTION")
        import deferred_turns as _dt
        rid = _dt.add(
            device_id=on_complete.get("device_id") or "",
            device_name=on_complete.get("device_name") or "?",
            query=on_complete.get("original_query") or "",
            actor=actor or "host", channel=channel or "",
            conversation_id=on_complete.get("conversation_id") or "")
        log.info("A.1 defer_turn accodato %s per device %s", rid,
                 (on_complete.get("device_name") or "?"))
        return _msg("MSG_DEFER_QUEUED",
                    device=on_complete.get("device_name") or "?",
                    hours=int(float(__import__("os").environ.get(
                        "METNOS_DEFER_TTL_H", "24"))))

    # github_analyze / github_send_reply: RITIRATI (flusso watcher legacy →
    # executor write/read/find_issues + comandi schedulati).

    log.warning("on_complete type sconosciuto: %s", callback_type)
    return _msg("MSG_ORCH_CALLBACK_UNKNOWN", callback_type=callback_type)


def _process_save_credentials_and_resume(on_complete: dict, values: dict,
                                          *, actor: str = "host") -> str:
    """Salva credenziali cifrate e ri-invoca il verb dichiarato (admin).

    Pattern callback `save_credentials_and_resume`:
      payload = {
        "type": "save_credentials_and_resume",
        "credentials_domain": "cifs_192.0.2.20",
        "credentials_context": {binding, host, share, ...},
        "resume_call": "admin",
        "resume_args": {intent, command_proposed, credentials_domain},
      }
    """
    domain = on_complete.get("credentials_domain") or ""
    ctx = on_complete.get("credentials_context") or {}
    resume_call = on_complete.get("resume_call") or ""
    resume_args = dict(on_complete.get("resume_args") or {})

    username = values.get("username") or values.get("user")
    password = values.get("password") or values.get("pwd")

    if not username or not password:
        return _msg("MSG_ORCH_CREDS_MISSING_USERPASS")

    if not domain:
        return _msg("MSG_ORCH_CREDS_MISSING_DOMAIN")

    # 1) save_credentials
    try:
        import credentials as _cred
        payload = {
            "username": username,
            "password": password,
            "context": dict(ctx),
        }
        _cred.store(domain, payload)
        log.info("orchestration: credentials saved per dominio %s "
                  "(user=%s)", domain, username)
    except (ImportError, OSError, RuntimeError) as ex:
        log.exception("orchestration: credentials.store fallito")
        return _msg("MSG_ORCH_CREDS_SAVE_FAILED", detail=f"{type(ex).__name__}: {ex}")

    # 2) resume_call
    if not resume_call:
        # Nessun resume previsto: solo conferma del save.
        return _msg("MSG_ORCH_CREDS_SAVED", domain=domain)

    try:
        from loader import invoke_verb_unique
        # Caller "agent_runtime" cosi' admin riconosce un'invocazione legittima
        # dal runtime (ADR 0088 AUTHORISED_CALLERS).
        res = invoke_verb_unique(
            resume_call, caller="agent_runtime",
            actor=actor, **resume_args,
        )
    except (PermissionError, KeyError, RuntimeError, TypeError) as ex:
        log.exception("orchestration: resume_call fallito")
        return _msg("MSG_ORCH_CREDS_SAVED_RESUME_FAILED", resume_call=resume_call, detail=f"{type(ex).__name__}: {ex}")

    return _shape_result_for_chat(res)


def _process_set_credential_mandates_and_resume(
        on_complete: dict, values: dict, *, actor: str = "host",
        channel: str | None = None):
    """Apply a secret-free credential policy, then optionally resume a turn."""
    bindings = [str(item) for item in (on_complete.get("bindings") or [])
                if isinstance(item, str) and item]
    profile = str(values.get("credential_mandate") or "")
    if not bindings or not profile:
        return _msg("MSG_ORCH_DIALOG_INCOMPLETE")
    try:
        import credential_mandates
        import credentials
        for binding in bindings:
            payload = credentials.load(binding)
            if not isinstance(payload, dict):
                return _msg("MSG_ORCH_CREDS_MISSING_DOMAIN")
            payload["scopes"] = credential_mandates.apply_profile(
                payload.get("scopes"), profile)
            credentials.store(binding, payload)
    except (OSError, RuntimeError, TypeError, ValueError) as ex:
        log.exception("orchestration: credential mandate update failed")
        return _msg("MSG_ORCH_CREDS_SAVE_FAILED",
                    detail=f"{type(ex).__name__}: {ex}")

    resume_query = str(on_complete.get("resume_query") or "").strip()
    if not resume_query:
        return _msg("MSG_CREDENTIAL_MANDATE_SAVED",
                    binding=", ".join(bindings))
    try:
        import agent_runtime
        new_log = agent_runtime.run_turn(
            resume_query, actor=actor or "host", channel=channel or "",
            conversation_id=str(on_complete.get("conversation_id") or ""),
            allow_disambig_synth=False,
        )
    except (ImportError, RuntimeError, TypeError) as ex:
        log.exception("orchestration: credential mandate resume failed")
        return _msg("MSG_ORCH_RELAUNCH_FAILED",
                    detail=f"{type(ex).__name__}: {ex}")
    if new_log is None:
        return _msg("MSG_ORCH_CONTINUATION_EMPTY")
    out = _completion_from_turnlog(new_log)
    if not out.text:
        out.text = _msg("MSG_CREDENTIAL_MANDATE_SAVED",
                        binding=", ".join(bindings))
    return out


def _process_expand_cap_and_resume(on_complete: dict, values: dict,
                                     *, actor: str = "host",
                                     channel: str | None = None) -> str:
    """Allarga il cap di un executor e lo ri-invoca direttamente (no PLANNER).

    Pattern callback `expand_cap_and_resume`:
      payload = {
        "type": "expand_cap_and_resume",
        "executor": "get_processes",
        "cap_field": "top",
        "cap_suggested": 1000,
        "args_suggested": {...},     # args completi gia' patchati col cap nuovo
        "preview_label": "processi", # 'processi', 'foto', 'risultati', ...
      }

    Contratto get_inputs (1 step yes_no): `values["confirm"]` = True/False.
    True → invoke_executor(executor, args_suggested) e formatta summary.
    False → no-op con messaggio neutro.

    Direct invoke senza PLANNER (the design guide §2.11 fase 2 + commento storico
    in http_routes_agent.py: il PLANNER medium interpretava il rewrite
    "(forza X=Y)" come saluto). Coerente con la logica di
    `_apply_cap_pending` HTTP (kind=cap_expand) pre-migrazione.
    """
    confirm = values.get("confirm")
    # Tolerant: yes_no parser ritorna bool; difensivamente, accetta
    # stringhe ("si"/"no") nel caso il caller bypassi parse_step_value.
    if isinstance(confirm, str):
        confirm = confirm.strip().lower() in ("si", "sì", "yes", "y", "ok", "true", "1")
    if not confirm:
        return _msg("MSG_CAP_EXPAND_DECLINED")

    executor = on_complete.get("executor") or ""
    args = dict(on_complete.get("args_suggested") or {})
    cap_field = on_complete.get("cap_field") or ""
    cap_suggested = on_complete.get("cap_suggested")
    label = on_complete.get("preview_label") or "risultati"

    if not executor:
        return _msg("MSG_ORCH_CAPEXPAND_MALFORMED")

    try:
        from loader import load_catalog
        cat = load_catalog(verify=True, include_synth=True)
        ex = cat.executors.get(executor)
        if ex is None:
            return _msg("MSG_ORCH_EXECUTOR_NOT_IN_CATALOG", executor=executor)
        import agent_runtime
        res = agent_runtime.invoke_executor(
            ex, args, timeout_s=getattr(ex, "timeout_s", 30),
            actor=actor, channel=channel,
        )
    except (PermissionError, KeyError, RuntimeError, TypeError) as ex:
        log.exception("orchestration: expand_cap invoke fallito")
        return _msg("MSG_ORCH_RELAUNCH_FAILED", detail=f"{type(ex).__name__}: {ex}")

    if not isinstance(res, dict) or not res.get("ok"):
        err = (res or {}).get("error", _msg("MSG_ORCH_UNKNOWN_ERROR")) if isinstance(res, dict) else _msg("MSG_ORCH_NO_RESULT")
        return _msg("MSG_CAP_EXPAND_FAILED",
                    field=cap_field, value=cap_suggested, err=err)

    entries = res.get("entries") or []
    n_entries = res.get("n_entries") or len(entries)
    head = _msg("MSG_CAP_EXPAND_RESULT",
                field=cap_field, value=cap_suggested,
                n=n_entries, label=label)

    # Render schema-aware: scegliamo il formatter in base ai campi top-level
    # del result. Tre famiglie distinte:
    #   1. health presente → rendering "stato server" (load + memoria + dischi
    #      + servizi + top processi compatti).
    #   2. discovered_documents non vuoto → rendering "documenti trovati"
    #      (lista doc con anchor_text + ext, poi entries non-doc compatte).
    #   3. fallback: lista entries compatta (basename + score).

    body_blocks: list[str] = []

    health = res.get("health") if isinstance(res.get("health"), dict) else None
    if health:
        body_blocks.append(_fmt_health_block(
            health, host=str(res.get("_ran_on_device") or "")))

    docs = res.get("discovered_documents") or []
    if isinstance(docs, list) and docs:
        body_blocks.append(_fmt_documents_block(docs))

    if entries:
        # Compatta: 1 linea per entry, max 20. Se health era reso, riduci a 8
        # (la sezione health gia' occupa righe).
        cap_preview = 8 if health else 20
        body_blocks.append(_fmt_entries_block(entries, cap_preview))

    if not body_blocks:
        # Output non-list-shaped (es. summary stringa). No raw-leak: shaper unico.
        return head + "\n\n" + _shape_result_for_chat(res)

    return head + "\n\n" + "\n\n".join(body_blocks)


def _process_gate_dispatch(on_complete: dict, values: dict,
                           *, actor: str = "host",
                           channel: str | None = None) -> str:
    """Gate di consenso (executor `get_approval`): esegue il branch scelto.

    `values` = {'<var>': '<scelta>'} (es. {'decision': 'approve'}). Se la
    scelta == `approve_value` esegue `on_approve`, altrimenti `on_reject` (se
    dichiarato). Annulla NON arriva qui (dialog cancelled → nessun on_complete).
    Ogni branch = {tool: <executor>, args: <dict>}. Deterministico §7.9: nessun
    LLM, solo dispatch dell'executor indicato dall'autore della gate.
    """
    approve_value = on_complete.get("approve_value", "approve")
    decision = next(iter(values.values()), None) if values else None
    branch = on_complete.get("on_approve") if decision == approve_value \
        else on_complete.get("on_reject")
    if not isinstance(branch, dict):
        # Rifiuto (o scelta non mappata) senza azione dichiarata: onesto, no-op.
        return _msg("MSG_GATE_NO_ACTION")

    executor = branch.get("tool") or branch.get("executor") or ""
    args_base = dict(branch.get("args") or {})
    if not executor:
        return _msg("MSG_ORCH_RESUME_EXEC_MISSING")
    try:
        from loader import load_catalog
        cat = load_catalog(verify=True, include_synth=True)
        ex = cat.executors.get(executor)
        if ex is None:
            return _msg("MSG_ORCH_EXECUTOR_NOT_IN_CATALOG", executor=executor)
        import agent_runtime
        res = agent_runtime.invoke_executor(
            ex, args_base, timeout_s=getattr(ex, "timeout_s", 30),
            actor=actor, channel=channel,
        )
    except (PermissionError, KeyError, RuntimeError, TypeError) as ex:
        log.exception("orchestration: gate_dispatch fallito")
        return _msg("MSG_ORCH_RELAUNCH_FAILED", detail=f"{type(ex).__name__}: {ex}")

    if isinstance(res, dict):
        msg = res.get("final_message_hint") or res.get("summary")
        if msg:
            return msg
        if res.get("ok") is False:
            err = res.get("error") or res.get("error_class") or ""
            return f"✗ {err}" if err else _msg("ERR_GENERIC")
        return _msg("MSG_ACTION_DONE")
    return str(res)


def _invoke_gate_branch_result(branch: dict | None, *, actor: str,
                               channel: str | None):
    """Esegue un branch dichiarativo e conserva il result strutturato."""
    if not isinstance(branch, dict):
        return {"ok": False, "error": _msg("MSG_GATE_NO_ACTION")}
    executor = branch.get("tool") or branch.get("executor") or ""
    args_base = dict(branch.get("args") or {})
    if not executor:
        return {"ok": False, "error": _msg("MSG_ORCH_RESUME_EXEC_MISSING")}
    try:
        from loader import load_catalog
        cat = load_catalog(verify=True, include_synth=True)
        ex = cat.executors.get(executor)
        if ex is None:
            return {"ok": False, "error": _msg(
                "MSG_ORCH_EXECUTOR_NOT_IN_CATALOG", executor=executor)}
        import agent_runtime
        return agent_runtime.invoke_executor(
            ex, args_base, timeout_s=getattr(ex, "timeout_s", 30),
            actor=actor, channel=channel)
    except (PermissionError, KeyError, RuntimeError, TypeError) as ex:
        log.exception("orchestration: executor gate branch fallito")
        return {"ok": False, "error": _msg(
            "MSG_ORCH_RELAUNCH_FAILED", detail=f"{type(ex).__name__}: {ex}")}


def _carry_executor_tail_to_nested_gate(
        branch_result: dict, parent_callback: dict, *,
        actor: str, channel: str | None) -> bool:
    """Trasferisce una coda residua a un gate emesso dal branch approvato.

    Un executor auto-riprendibile puo' incontrare piu' transizioni protette in
    sequenza. Ogni branch va quindi completato prima di usare il suo risultato
    come seed: se ritorna ancora ``input_required``, il nuovo dialogo eredita la
    stessa coda e il runtime si ferma nuovamente. Il criterio e' interamente
    strutturale (gate_dispatch + dialog_id), senza nomi di dominio/executor.
    """
    dialog_id = branch_result.get("dialog_id")
    if (branch_result.get("decision") != "input_required"
            or not isinstance(dialog_id, str) or not dialog_id):
        return False
    raw_tail = parent_callback.get("tail_steps")
    if not isinstance(raw_tail, list) or not raw_tail:
        return False
    sender = f"{channel}:{actor}" if channel else actor
    state = dialog_pending.load_pending(sender, dialog_id)
    if not isinstance(state, dict):
        return False
    nested = state.get("on_complete") or {}
    if not isinstance(nested, dict) or nested.get("type") != "gate_dispatch":
        return False
    approve_branch = nested.get("on_approve")
    if not isinstance(approve_branch, dict):
        return False
    approve_tool = approve_branch.get("tool") or approve_branch.get("executor")
    if not isinstance(approve_tool, str) or not approve_tool:
        return False

    state["on_complete"] = {
        "type": "resume_executor_gate_tail",
        "gate_approve_value": nested.get("approve_value", "approve"),
        "gate_on_approve": approve_branch,
        "gate_on_reject": nested.get("on_reject"),
        "tail_steps": raw_tail,
        "tail_final_message": parent_callback.get("tail_final_message") or "",
        "original_query": parent_callback.get("original_query") or "",
        "conversation_id": parent_callback.get("conversation_id") or "",
    }
    dialog_pending.save_pending(sender, dialog_id, state)
    log.info("orchestration: coda executor trasferita al gate annidato %s "
             "(%s, %d step)", dialog_id, approve_tool, len(raw_tail))
    return True


def _process_resume_executor_gate_tail(on_complete: dict, values: dict, *,
                                       actor: str = "host",
                                       channel: str | None = None):
    """Riprende una pipeline dopo un gate creato dentro un executor.

    Prima ripresenta al broker il token opaco del branch approvato, poi esegue
    soltanto gli step residui usando quel result come seed. In questo modo la
    risorsa osservata resta quella mostrata all'utente e nessuna azione
    pre-gate viene ripetuta. Se il branch incontra un altro gate, trasferisce la
    coda a quel dialogo e si sospende ancora, per un numero arbitrario di gate.
    """
    approve = on_complete.get("gate_approve_value", "approve")
    decision = next(iter((values or {}).values()), None) if values else None
    if decision != approve:
        rejected = _invoke_gate_branch_result(
            on_complete.get("gate_on_reject"), actor=actor, channel=channel)
        return _shape_result_for_chat(rejected)

    branch_result = _invoke_gate_branch_result(
        on_complete.get("gate_on_approve"), actor=actor, channel=channel)
    if not isinstance(branch_result, dict) or not branch_result.get("ok"):
        return _shape_result_for_chat(branch_result)

    raw_tail = on_complete.get("tail_steps") or []
    if branch_result.get("decision") == "needs_inputs":
        payload = branch_result.get("needs_inputs") or {}
        nested_callback = payload.get("on_complete") or {}
        if (raw_tail and isinstance(nested_callback, dict)
                and nested_callback.get("type") ==
                    "resume_executor_with_values"):
            nested_callback.update({
                "type": "resume_executor_values_tail",
                "tail_steps": raw_tail,
                "tail_final_message": (
                    on_complete.get("tail_final_message") or ""),
                "original_query": on_complete.get("original_query") or "",
                "conversation_id": on_complete.get("conversation_id") or "",
            })
            payload["on_complete"] = nested_callback
        conversation_id = str(on_complete.get("conversation_id") or "")
        sender = f"{channel or 'http'}:{actor or 'host'}"
        if conversation_id:
            sender = f"{sender}:{conversation_id}"
        dialog = orchestrate_needs_inputs(
            branch_result, sender_id=sender,
            actor=actor or "host", channel=channel or "http")
        if not isinstance(dialog, dict) or not dialog.get("ok"):
            return _shape_result_for_chat(dialog)
        return CompletionResult(
            text=(dialog.get("final_message_hint")
                  or branch_result.get("final_message_hint")
                  or _msg("MSG_ORCH_DIALOG_DONE")),
            attachments=list(branch_result.get("attachments") or ()),
            n_total_matches=len(branch_result.get("attachments") or ()),
            path=[{"tool": str((on_complete.get("gate_on_approve") or {}).get(
                "tool") or ""), "ok": True}],
        )
    if (branch_result.get("decision") == "input_required"
            and branch_result.get("dialog_id")):
        if not raw_tail:
            return _shape_result_for_chat(branch_result)
        if not _carry_executor_tail_to_nested_gate(
                branch_result, on_complete, actor=actor, channel=channel):
            return _msg(
                "MSG_ORCH_CONTINUATION_FAILED",
                detail="nested approval gate could not inherit executor tail",
            )
        attachments = list(branch_result.get("attachments") or [])
        branch = on_complete.get("gate_on_approve") or {}
        branch_tool = (
            (branch.get("tool") or branch.get("executor") or "")
            if isinstance(branch, dict) else ""
        )
        return CompletionResult(
            text=_shape_result_for_chat(branch_result),
            attachments=attachments,
            n_total_matches=len(attachments),
            path=([{"tool": branch_tool, "ok": True}]
                  if branch_tool else []),
        )
    if not isinstance(raw_tail, list) or not raw_tail:
        return _shape_result_for_chat(branch_result)
    try:
        from engine.executor import Executor
        from engine.types import Framework, StepRun, StepSpec
        from loader import load_catalog
        import agent_runtime
        cat = load_catalog(verify=True, include_synth=True)
        # Il catalog della coda deve conoscere anche i builtin in-process
        # (describe_entries/classify_entries/...): un tail post-gate li usa come
        # nel loop principale. Riuso l'augmenter universale, cosi' il Validator
        # dell'engine non li scarta come `tool_unknown`.
        catalog = agent_runtime._engine_v2_catalog_with_builtins(
            list(cat.executors.values()))
        steps = [StepSpec(
            tool=str(item.get("tool") or ""),
            args=dict(item.get("args") or {}),
            if_prev_entries_nonempty=bool(item.get("if_prev_entries_nonempty")),
        ) for item in raw_tail if isinstance(item, dict) and item.get("tool")]
        if not steps:
            return _shape_result_for_chat(branch_result)
        framework = Framework(
            steps=steps,
            final_message=str(on_complete.get("tail_final_message") or ""))

        def _invoke(tool_name: str, args: dict) -> dict:
            # Dispatch canonico: builtin-first, poi executor firmato. Stesso
            # percorso del loop principale, cosi' un helper universale nella
            # coda non e' mai un falso `tool_unknown` (§7.3).
            return agent_runtime.invoke_tool_by_name(
                tool_name, args, catalog=catalog, actor=actor, channel=channel)

        seed = StepRun(
            step_idx=1, tool="@approved_executor_gate", args={},
            result=branch_result, ok=True, latency_ms=0, kind="input")
        run = Executor(
            invoke_executor=_invoke, seed_steps=[seed], catalog=catalog).run(
                framework, query=on_complete.get("original_query") or "",
                runtime_ctx={
                    "actor": actor or "host", "channel": channel or "",
                    "user_query_raw": on_complete.get("original_query") or "",
                    "conversation_id": on_complete.get("conversation_id") or "",
                })
        if getattr(run, "gate_dialog_id", ""):
            from engine.dispatch import _inject_gate_resume_if_paused
            _inject_gate_resume_if_paused(
                run, on_complete.get("original_query") or "",
                {"actor": actor or "host", "channel": channel or "",
                 "user_query_raw": on_complete.get("original_query") or "",
                 "conversation_id": on_complete.get("conversation_id") or ""},
                framework=framework)

        attachments = []
        path = []
        for step in run.steps:
            if step.tool == "@approved_executor_gate":
                continue
            result = step.result if isinstance(step.result, dict) else {}
            path.append({"tool": step.tool, "ok": bool(result.get("ok"))})
            if isinstance(result.get("attachments"), list):
                attachments.extend(result["attachments"])
        text = run.final_text
        last_result = (run.steps[-1].result
                       if run.steps and isinstance(run.steps[-1].result, dict)
                       else {})
        # La presentazione dichiarata dall'executor e' piu' informativa del
        # bullet generico di una entry tecnica sites (solo session_id/stato).
        if last_result.get("final_message_hint"):
            text = last_result["final_message_hint"]
        elif not text and run.steps:
            text = _shape_result_for_chat(last_result)
        return CompletionResult(
            text=text or _msg("MSG_ORCH_CONTINUATION_DONE"),
            attachments=attachments,
            n_total_matches=len(attachments), path=path)
    except (ImportError, KeyError, RuntimeError, TypeError, ValueError) as ex:
        log.exception("orchestration: resume executor gate tail fallito")
        return _msg("MSG_ORCH_CONTINUATION_FAILED",
                    detail=f"{type(ex).__name__}: {ex}")


def _process_resume_engine_gate(on_complete: dict, values: dict, *,
                                actor: str = "host",
                                channel: str | None = None) -> str:
    """gate-resume engine (20/6/2026): un gate get_approval ha messo in PAUSA
    una pipeline compound (find → get_approval → send → write). All'APPROVAZIONE
    riesegue il turno con `pre_approved_gate=True`: il gate auto-passa e gli step
    a valle (send/write) girano con lo stato corrente dello store (re-query
    idempotente; i pre-gate sono read-only per convenzione). Al RIFIUTO: stop
    onesto (§2.8), nessuna azione a valle.

    Universale §7.9: nessun LLM nella decisione; `pre_approved_gate` rende il
    gate trasparente, la ricomposizione resta deterministica (v3, seed fisso).
    """
    approve = on_complete.get("gate_approve_value", "approve")
    decision = next(iter((values or {}).values()), None) if values else None
    if decision != approve:
        # Rifiuto (o scelta non mappata): niente pubblicazione. Onesto, no-op.
        return _msg("MSG_GATE_NO_ACTION")
    query = on_complete.get("original_query") or ""
    conversation_id = on_complete.get("conversation_id") or ""
    if not query:
        return _msg("MSG_ORCH_RESUME_PLANNER_NO_QUERY")
    try:
        import agent_runtime
        new_log = agent_runtime.run_turn(
            query,
            actor=actor or "host",
            channel=channel or "",
            conversation_id=conversation_id,
            pre_approved_gate=True,
        )
    except (RuntimeError, TypeError, ImportError) as ex:
        log.exception("orchestration: resume_engine_gate fallito")
        return _msg("MSG_ORCH_CONTINUATION_FAILED",
                    detail=f"{type(ex).__name__}: {ex}")
    if new_log is None:
        return _msg("MSG_ORCH_CONTINUATION_EMPTY")
    # CompletionResult PIENO (6/7, Roberto: «si perdono le info sul turno»):
    # il resume post-approvazione e' un TURNO INTERO — turn_id/tempo/device/
    # breadcrumb devono arrivare alla chat come per ogni turno (stessa via
    # del resume full-turn foto, bug zip-line 5/7).
    out = _completion_from_turnlog(new_log)
    if not out.text:
        out.text = _msg("MSG_ORCH_CONTINUATION_DONE")
    return out


def _process_resume_executor_with_values(on_complete: dict, values: dict,
                                          *, actor: str = "host",
                                          channel: str | None = None) -> str:
    """Ri-invoca un executor con args originali patchati con i values raccolti.

    Pattern callback `resume_executor_with_values` (PR2 persons registry,
    ADR 0090): usato per disambiguazione face/name multi-candidate.

      payload = {
        "type": "resume_executor_with_values",
        "executor": "set_persons",
        "args_base": {...},     # args originali della prima invocazione
        "merge_into": "face_choices",   # opzionale: chiave dict in cui i
                                        # values vanno annidati. Se omesso,
                                        # values e' fuso a top-level di args.
      }

    Il merge e' deterministico (§7.9): values del dialogo override
    args_base (lo scopo della disambiguation e' aggiungere campi).
    """
    executor = on_complete.get("executor") or ""
    args_base = dict(on_complete.get("args_base") or {})
    merge_into = on_complete.get("merge_into")

    if not executor:
        return _msg("MSG_ORCH_RESUME_EXEC_MISSING")

    if merge_into:
        nested = dict(args_base.get(merge_into) or {})
        nested.update(values)
        args_base[merge_into] = nested
    else:
        args_base.update(values)

    try:
        from loader import load_catalog
        cat = load_catalog(verify=True, include_synth=True)
        ex = cat.executors.get(executor)
        if ex is None:
            return _msg("MSG_ORCH_EXECUTOR_NOT_IN_CATALOG", executor=executor)
        import agent_runtime
        res = agent_runtime.invoke_executor(
            ex, args_base, timeout_s=getattr(ex, "timeout_s", 30),
            actor=actor, channel=channel,
        )
    except (PermissionError, KeyError, RuntimeError, TypeError) as ex:
        log.exception("orchestration: resume_executor_with_values fallito")
        return _msg("MSG_ORCH_RELAUNCH_FAILED", detail=f"{type(ex).__name__}: {ex}")

    # Cattura scope-arg dal form: il valore confermato/inserito diventa default
    # per il giro dopo (§7.9). Resume bypassa Executor.run → cattura esplicita qui.
    if isinstance(res, dict) and res.get("ok"):
        try:
            from args_resolver import remember_scope_args
            remember_scope_args(
                executor, args_base,
                actor=args_base.get("_actor") or actor or "host")
        except Exception:
            pass

    return _shape_result_for_chat(res)


def _process_resume_executor_values_tail(on_complete: dict, values: dict, *,
                                         actor: str = "host",
                                         channel: str | None = None):
    """Rilancia un executor con input raccolti e continua la coda stateful.

    Riusa lo stesso motore delle approvazioni executor: cambia soltanto il modo
    in cui viene costruito il branch iniziale (merge dei valori del dialogo).
    I valori restano in memoria e non entrano nella query del planner.
    """
    executor = str(on_complete.get("executor") or "")
    if not executor:
        return _msg("MSG_ORCH_RESUME_EXEC_MISSING")
    args = dict(on_complete.get("args_base") or {})
    merge_into = on_complete.get("merge_into")
    if merge_into:
        nested = dict(args.get(merge_into) or {})
        nested.update(values or {})
        args[merge_into] = nested
    else:
        args.update(values or {})
    callback = {
        "gate_approve_value": "approve",
        "gate_on_approve": {"tool": executor, "args": args},
        "gate_on_reject": None,
        "tail_steps": list(on_complete.get("tail_steps") or ()),
        "tail_final_message": on_complete.get("tail_final_message") or "",
        "original_query": on_complete.get("original_query") or "",
        "conversation_id": on_complete.get("conversation_id") or "",
    }
    return _process_resume_executor_gate_tail(
        callback, {"decision": "approve"},
        actor=actor, channel=channel)


def _process_strato3_choice_dispatch(
    on_complete: dict, values: dict, *,
    actor: str = "host", channel: str | None = None,
) -> str:
    """Dispatcher strato 3 (task #30, 24/5/2026): la scelta utente fra 4
    azioni viene mappata in una nuova query e si rilancia il turno.

    `values["chosen_action"]` puo' essere index 0-3 oppure prefix string.
    """
    raw = (values or {}).get("chosen_action") or ""
    original_query = on_complete.get("original_query") or ""
    lang = on_complete.get("lang") or "it"
    conversation_id = on_complete.get("conversation_id") or ""
    # Normalizza scelta. Accetta:
    #  - 1-indexed integer "1".."5" (user-facing label)
    #  - 0-indexed integer "0".."4" (programmatic)
    #  - prefix string ("retry"/"synth"/"frontier"/"reformulate"/"abandon")
    #  - IT prefix ("ritent"/"sintetiz"/"riformul"/"abbandon")
    txt = str(raw).strip().lower().rstrip(".")
    # Map ordinato: index 0-based corrisponde a action_key
    ACTIONS_ORDER = ["retry", "synth", "frontier", "reformulate", "abandon"]
    PREFIX_MAP = {
        "retry": "retry", "ritent": "retry",
        "synth": "synth", "sintetiz": "synth",
        "frontier": "frontier",
        "reformul": "reformulate", "riformul": "reformulate",
        "abandon": "abandon", "abbandon": "abandon",
    }
    action_key = "abandon"
    # Try numeric (1-indexed primary, 0-indexed fallback)
    if txt.isdigit():
        n = int(txt)
        if 1 <= n <= len(ACTIONS_ORDER):
            action_key = ACTIONS_ORDER[n - 1]  # user-facing 1-indexed
        elif 0 <= n < len(ACTIONS_ORDER):
            action_key = ACTIONS_ORDER[n]
    else:
        # Prefix match
        for prefix, key in PREFIX_MAP.items():
            if txt.startswith(prefix):
                action_key = key
                break

    if action_key == "abandon":
        return _msg("MSG_ORCH_STOPPING")
    if action_key == "reformulate":
        return _msg("MSG_ORCH_REFORMULATE_NEXT")
    if action_key == "retry":
        # Ritenta query originale bypassando anti_skill demote del
        # turn_feedback. Universal §7.9: se il motore o lo stato sono
        # cambiati (training, manifest update, ecc.), la pipeline che
        # prima falliva può ora funzionare. Bypass viene segnalato via
        # env temporanea + flag su run_turn.
        try:
            import agent_runtime
            new_log = agent_runtime.run_turn(
                original_query,
                actor=actor or "host",
                channel=channel or "",
                conversation_id=conversation_id,
                allow_disambig_synth=False,
                bypass_rejected_pipelines=True,
            )
            final = getattr(new_log, "final_message", "") or ""
            # Se ritenta SUCCESS (no error, no escalation), reset anti_skill
            # per questa query (compensa il demote precedente).
            try:
                if new_log and getattr(new_log, "final_kind", "") == "answer":
                    from turn_feedback import reset_rejected_for_query
                    reset_rejected_for_query(original_query)
            except Exception as _ex:
                log.warning("reset_rejected_for_query failed: %s", _ex)
            return final
        except (RuntimeError, TypeError, ImportError) as ex:
            log.exception("strato3 retry failed")
            return _msg("MSG_ORCH_RETRY_FAILED", detail=f"{type(ex).__name__}: {ex}")
    if action_key == "synth":
        new_query = (
            f"request_new_executor per: {original_query}"
            if lang != "en"
            else f"request_new_executor for: {original_query}"
        )
    elif action_key == "frontier":
        new_query = (
            f"consult_frontier su: {original_query}"
            if lang != "en"
            else f"consult_frontier on: {original_query}"
        )
    else:
        return _msg("MSG_ORCH_CHOICE_UNKNOWN")
    try:
        import agent_runtime
        new_log = agent_runtime.run_turn(
            new_query,
            actor=actor or "host",
            channel=channel or "",
            conversation_id=conversation_id,
            allow_disambig_synth=False,
        )
    except (RuntimeError, TypeError, ImportError) as ex:
        log.exception("strato3 dispatch failed")
        return _msg("MSG_ORCH_CONTINUATION_FAILED", detail=f"{type(ex).__name__}: {ex}")
    return getattr(new_log, "final_message", "") or ""


def _process_restart_turn_with_chosen_query(
    on_complete: dict, values: dict, *,
    actor: str = "host", channel: str | None = None,
) -> str:
    """Riprende un turno disambiguato: la scelta dell'utente diventa
    direttamente la nuova `user_query` (Test 6 fix sistemico, 16/5/2026).

    Pattern callback `restart_turn_with_chosen_query`:
      payload = {
        "type": "restart_turn_with_chosen_query",
        "original_query": str,        # query originale (per audit/log)
        "options": list[str],         # le opzioni proposte (audit)
        "conversation_id": str,       # opzionale
      }

    `values["chosen_query"]` viene riusato come nuova user_query del
    turno successivo. PLANNER vede una query gia' disambiguata e procede
    senza ambiguita'. Niente scratchpad (la disambiguazione cambia
    interpretazione, non e' un resume di pipeline).

    Determinismo §7.9, language-agnostic.
    """
    chosen = (values or {}).get("chosen_query") or ""
    if not isinstance(chosen, str) or not chosen.strip():
        return (_msg("MSG_ORCH_DISAMB_EMPTY_CHOICE"))
    conversation_id = on_complete.get("conversation_id") or ""
    try:
        import agent_runtime
        # Anti-loop: il PLANNER al restart NON deve ri-disambiguare la
        # query gia' disambiguata dall'utente (anche se semanticamente
        # potrebbe sembrare ancora ambigua per il PLANNER). Pattern
        # §7.3 generale: passare allow_disambig_synth=False sul restart.
        new_log = agent_runtime.run_turn(
            chosen.strip(),
            actor=actor or "host",
            channel=channel or "",
            conversation_id=conversation_id,
            allow_disambig_synth=False,
        )
    except (RuntimeError, TypeError, ImportError) as ex:
        log.exception("orchestration: restart_turn_with_chosen_query fallito")
        return _msg("MSG_ORCH_CONTINUATION_FAILED", detail=f"{type(ex).__name__}: {ex}")
    if new_log is None:
        return _msg("MSG_ORCH_CONTINUATION_EMPTY")
    # Bug zip-line (5/7): il resume È un turno completo — porta su
    # attachments/gallery e meta, non solo il testo.
    return _completion_from_turnlog(new_log)


def _process_rerun_query_disambiguated(
    on_complete: dict, values: dict, *,
    actor: str = "host", channel: str | None = None,
) -> str:
    """Riprende dopo la scelta nel form di DISAMBIGUAZIONE ROUTING (§2.11): la
    query ORIGINALE viene ri-eseguita con l'OGGETTO fissato (forced_object), cosi'
    il routing punta deterministicamente all'oggetto scelto, senza ri-chiedere.

    Pattern callback `rerun_query_disambiguated`:
      payload = {"type": "rerun_query_disambiguated", "query": str,
                 "conversation_id": str?}
    `values["object"]` = l'oggetto scelto (value dell'opzione choice).

    §2.11 errore-runtime→form (25/6): se on_complete porta `inject_arg`, la scelta
    NON è un oggetto-routing ma un ARG concreto (es. base_path) che l'executor
    aveva chiesto. Si ri-esegue iniettando `forced_args={inject_arg: scelta}` —
    generale per qualsiasi arg, no hardcoding del caso path."""
    query = (on_complete or {}).get("query") or ""
    inject_arg = (on_complete or {}).get("inject_arg")
    conversation_id = on_complete.get("conversation_id") or ""
    if inject_arg:
        chosen = (values or {}).get(inject_arg) or (values or {}).get("choice") or ""
        if not isinstance(query, str) or not query.strip() or not chosen:
            return _msg("MSG_ORCH_DISAMB_EMPTY_CHOICE")
        try:
            import agent_runtime
            new_log = agent_runtime.run_turn(
                query.strip(), actor=actor or "host", channel=channel or "",
                conversation_id=conversation_id,
                forced_args={inject_arg: str(chosen)})
        except (RuntimeError, TypeError, ImportError) as ex:
            log.exception("orchestration: rerun inject_arg fallito")
            return _msg("MSG_ORCH_CONTINUATION_FAILED",
                        detail=f"{type(ex).__name__}: {ex}")
        if new_log is None:
            return _msg("MSG_ORCH_CONTINUATION_EMPTY")
        return _completion_from_turnlog(new_log)
    chosen_obj = (values or {}).get("object") or ""
    if not isinstance(query, str) or not query.strip() or not chosen_obj:
        return _msg("MSG_ORCH_DISAMB_EMPTY_CHOICE")
    try:
        import agent_runtime
        new_log = agent_runtime.run_turn(
            query.strip(), actor=actor or "host", channel=channel or "",
            conversation_id=conversation_id, forced_object=str(chosen_obj))
    except (RuntimeError, TypeError, ImportError) as ex:
        log.exception("orchestration: rerun_query_disambiguated fallito")
        return _msg("MSG_ORCH_CONTINUATION_FAILED",
                    detail=f"{type(ex).__name__}: {ex}")
    if new_log is None:
        return _msg("MSG_ORCH_CONTINUATION_EMPTY")
    return _completion_from_turnlog(new_log)


def _process_resume_planner_with_dialog_values(
    on_complete: dict, values: dict, *,
    actor: str = "host", channel: str | None = None,
) -> str:
    """Riprende un turno PLANNER multi-pipeline dopo che get_inputs ha
    raccolto le scelte dell'utente (bug residuo 12/5/2026 pipeline
    propose+notify ferma al "Dialogo completato").

    Pattern callback `resume_planner_with_dialog_values`:
      payload = {
        "type": "resume_planner_with_dialog_values",
        "original_query": str,           # query utente del turno originale
        "prior_steps": [                  # scratchpad pre-dialog snapshot
          {"step": 1, "tool": "find_events_empty",
           "args": {...}, "observation": {...}},
          ...
        ],
        "dialog_step_num": int,           # step in cui get_inputs e' stato emesso
        "dialog_var_name": str,           # var raccolta nel dialog (opzionale)
        "conversation_id": str,           # opzionale, fallback ""
      }

    Comportamento:
      1. Ricostruisce lo scratchpad: prior_steps + 1 step extra "get_inputs"
         (decision="completed" + values raccolti) cosi' il PLANNER al primo
         loop iter vede il dialogo completato + le scelte utente.
      2. Re-invoca `agent_runtime.run_turn(..., resume_with_scratchpad=...)`.
      3. Il PLANNER al secondo turno prosegue dal punto in cui era (es. emette
         send_messages con i valori scelti).

    Determinismo §7.9: nessun LLM nella ricostruzione; il PLANNER decide
    autonomamente i prossimi step vedendo scratchpad + values.

    Returns:
      str: final_message del nuovo turno (continuation completata) oppure
      messaggio diagnostico se ricostruzione fallita.
    """
    original_query = on_complete.get("original_query") or ""
    prior_steps = list(on_complete.get("prior_steps") or [])
    dialog_step_num = (on_complete.get("dialog_step_num")
                        or (len(prior_steps) + 1))
    dialog_var = on_complete.get("dialog_var_name") or "values"
    conversation_id = on_complete.get("conversation_id") or ""
    # Tool che ha emesso la pausa: get_inputs (default) o get_approval
    # (gate-resume, 20/6/2026). Generalizza il match nello scratchpad sotto.
    _dialog_tool = on_complete.get("dialog_tool") or "get_inputs"

    # Gate di consenso (get_approval): se il resume e' un GATE, riprendi il
    # piano SOLO all'approvazione; il rifiuto e' uno stop onesto (§2.8), senza
    # eseguire le azioni a valle (niente pubblicazione). gate_approve_value
    # assente = path get_inputs normale (sempre resume, nessun gate).
    _gate_approve = on_complete.get("gate_approve_value")
    if _gate_approve is not None:
        _decision = next(iter((values or {}).values()), None) if values else None
        if _decision != _gate_approve:
            return _msg("MSG_GATE_NO_ACTION")

    if not original_query:
        return _msg("MSG_ORCH_RESUME_PLANNER_NO_QUERY")

    # Costruisci uno step "get_inputs" completed e PROIETTALO nello scratchpad.
    # Caso normale: lo snapshot del turno originale gia' contiene lo step
    # get_inputs(decision="input_required") (cf. agent_runtime ~r.4881 — lo
    # snapshot e' preso DOPO che get_inputs ha emesso il dialogo). Se appendessimo
    # un secondo step con stesso step_num+tool, il PLANNER continuation vedrebbe
    # due step identici e auto_final_on_duplicate scatterebbe sul tentativo
    # successivo (bug live turn ef7e19cc6c8e435f, 14/5/2026). Sostituiamo
    # l'observation dell'ultimo get_inputs presente; fallback: append solo se
    # non esiste alcun get_inputs (snapshot pre-dialog).
    dialog_obs = {
        "ok": True,
        "decision": "completed",
        "values": dict(values or {}),
        "_resumed": True,
        "_dialog_var": dialog_var,
    }
    _replaced = False
    for entry in reversed(prior_steps):
        if isinstance(entry, dict) and entry.get("tool") == _dialog_tool:
            entry["observation"] = dialog_obs
            _replaced = True
            break
    if not _replaced:
        prior_steps.append({
            "step": int(dialog_step_num),
            "tool": _dialog_tool,
            "args": {"dialog": "<elided>"},
            "observation": dialog_obs,
        })

    # Continuazione → ENGINE v3 (ADR 0177 M1, «semina di turno»). Lo shortcut
    # deterministico `_orchestrate_implicit_actions` (ADR 0129) è stato RITIRATO:
    # copriva solo `(create, events)` (tabella `_ACTION_TEMPLATES`) e cadeva nel
    # PLANNER legacy per ogni altra azione. L'engine ora gestisce il caso
    # GENERALE — gli `prior_steps` (produttori già eseguiti) diventano seed
    # kind="done": il proposer (consapevole via «FATTO FINORA») pianifica solo il
    # resto, la guardia dedup salta le ri-emissioni. e2e: create_events + notify
    # compound (il caso ADR 0129) e send_messages (che lo shortcut NON copriva).
    # `run_turn` instrada `resume_with_scratchpad` all'engine (gate
    # METNOS_ENGINE_RESUME, fallback legacy su engine-None).
    try:
        import agent_runtime
        new_log = agent_runtime.run_turn(
            original_query,
            actor=actor or "host",
            channel=channel or "",
            conversation_id=conversation_id,
            resume_with_scratchpad=prior_steps,
        )
    except (RuntimeError, TypeError, ImportError) as ex:
        log.exception("orchestration: resume_planner_with_dialog_values fallito")
        return _msg("MSG_ORCH_CONTINUATION_FAILED", detail=f"{type(ex).__name__}: {ex}")

    if new_log is None:
        return _msg("MSG_ORCH_CONTINUATION_EMPTY")
    msg_out = getattr(new_log, "final_message", "") or ""
    if not msg_out:
        return _msg("MSG_ORCH_CONTINUATION_DONE")
    return msg_out


def _fmt_health_block(h: dict, host: str = "", sections: set | None = None) -> str:
    """Rende la sezione health in 4-6 righe leggibili.

    `sections` (9/7, Roberto): focus per DOMANDA SPECIFICA («qual è l'ip», «che
    gpu ha») — rende SOLO le sezioni richieste, in forma DETTAGLIATA (rete con
    MAC, gpu con VRAM used/total, cpu con core+freq+uso, periferiche usb/block).
    None = blocco-status completo (comportamento storico, riga Sistema sintetica).

    Stile per ADR 0095 (output deterministico): KV con label espliciti,
    no slash ambigui per gruppi correlati (load 1m/5m/15m), unita' inline.
    `host`: nome del DEVICE quando i dati vengono da lì (5/7: il titolo
    diceva «Stato server» anche per i processi del PC — disonesto §2.8).
    """
    out = [_msg("MSG_HEALTH_TITLE_HOST", host=host) if host
           else _msg("MSG_HEALTH_TITLE")]
    if sections:
        # ── FOCUS: solo le sezioni richieste, dettagliate ─────────────────
        if "system" in sections:
            sd = h.get("system") or {}
            bits = [str(sd[k]) for k in ("hostname", "distro", "os_release", "arch")
                    if sd.get(k)]
            if bits:
                out.append(_msg("MSG_HEALTH_SYSTEM", body=" · ".join(bits)))
        if "cpu" in sections:
            cd = h.get("cpu") or {}
            bits = []
            if cd.get("model"):
                bits.append(str(cd["model"]))
            if cd.get("physical_cores"):
                bits.append(f"{cd['physical_cores']}c/"
                            f"{cd.get('logical_cores') or '?'}t")
            elif cd.get("logical_cores"):
                bits.append(f"{cd['logical_cores']} thread")
            if cd.get("freq_mhz"):
                fm = f"{cd['freq_mhz']}MHz"
                if cd.get("freq_max_mhz"):
                    fm += f" (max {cd['freq_max_mhz']}MHz)"
                bits.append(fm)
            if cd.get("usage_pct") is not None:
                bits.append(f"{cd['usage_pct']}% in uso")
            if bits:
                out.append(_msg("MSG_HEALTH_CPU", body=" · ".join(bits)))
        if "gpu" in sections:
            for g in (h.get("gpu") or []):
                bits = [str(g.get("vendor") or g.get("device_id") or "?")]
                if g.get("vram_total_mb"):
                    used = g.get("vram_used_mb")
                    bits.append(f"VRAM {used if used is not None else '?'}/"
                                f"{g['vram_total_mb']} MB")
                if g.get("busy_pct") is not None:
                    bits.append(f"busy {g['busy_pct']}%")
                out.append(_msg("MSG_HEALTH_GPU", body=" · ".join(bits)))
        if "network" in sections:
            bits = []
            for n in (h.get("network") or []):
                addrs = (n.get("ipv4") or []) + [a for a in (n.get("ipv6") or [])
                                                  if not a.startswith("fe80")]
                if not addrs:
                    continue
                s = f"{n.get('iface','?')}{'' if n.get('up') else ' ✗'} " \
                    f"{', '.join(addrs)}"
                if n.get("mac"):
                    s += f" (MAC {n['mac']})"
                bits.append(s)
            if bits:
                out.append(_msg("MSG_HEALTH_NETWORK", body=" · ".join(bits)))
        if "peripherals" in sections:
            per = h.get("peripherals") or {}
            usb = [f"{u.get('manufacturer','')} {u.get('product','')}".strip()
                   for u in per.get("usb", []) if u.get("product")]
            blk = [f"{b['name']} {b.get('size_gb','?')}GB"
                   + (f" ({b['model']})" if b.get("model") else "")
                   for b in per.get("block", []) if b.get("name")]
            if usb or blk:
                out.append(_msg("MSG_HEALTH_PERIPHERALS",
                                body=" · ".join(blk + usb)))
        # §2.8 (10/7, turn 6dce715f: «ip del pc-roberto» col client senza
        # psutil → health.network=[] → blocco = SOLO titolo): se il focus non
        # ha prodotto NULLA e nessuna sezione dinamica seguirà, dillo.
        _dynamic = sections & {"load", "memory", "disk", "thermal", "power",
                               "services"}
        if len(out) == 1 and not _dynamic:
            out.append(_msg("MSG_HEALTH_SECTION_EMPTY",
                            sections=", ".join(sorted(sections))))
        # sezioni dinamiche riusano il render standard sotto (load/memory/
        # disk/thermal/power/services filtrate dal set).
        _keep = sections
    else:
        _keep = None
    # Riga descrittiva SISTEMA (9/7, Roberto): hostname · distro/os · CPU · GPU.
    # Sintetica nel blocco-status; le sezioni COMPLETE (health.cpu/gpu/system/
    # peripherals) restano nei dati per le domande specifiche (ramo focus sopra).
    sysd = (h.get("system") or {}) if _keep is None else {}
    cpud = (h.get("cpu") or {}) if _keep is None else {}
    gpus = (h.get("gpu") or []) if _keep is None else []
    sys_bits = []
    if sysd.get("hostname"):
        sys_bits.append(str(sysd["hostname"]))
    if sysd.get("distro") or sysd.get("os"):
        osname = sysd.get("distro") or sysd.get("os")
        rel = sysd.get("os_release") or ""
        sys_bits.append(f"{osname}" + (f" ({rel})" if rel and not sysd.get("distro") else ""))
    if cpud.get("model"):
        cores = cpud.get("physical_cores") or cpud.get("logical_cores")
        sys_bits.append(str(cpud["model"])
                        + (f" {cores}c" if cores else "")
                        + (f" @{cpud['freq_mhz']}MHz" if cpud.get("freq_mhz") else ""))
    for g in gpus[:2]:
        gb = f"GPU {g.get('vendor') or g.get('device_id') or '?'}"
        if g.get("vram_total_mb"):
            gb += f" {g['vram_total_mb']//1024}GB VRAM"
        if g.get("busy_pct") is not None:
            gb += f" ({g['busy_pct']}%)"
        sys_bits.append(gb)
    if sys_bits:
        out.append(_msg("MSG_HEALTH_SYSTEM", body=" · ".join(sys_bits)))
    load = h.get("load") or {}
    if (_keep is None or "load" in _keep) and load.get("available"):
        up_h = (load.get("uptime_s") or 0) // 3600
        if load.get("1m") is None:
            # Windows: niente load avg → uptime + uso CPU (10/7).
            _cpu = load.get("cpu_pct")
            out.append(_msg("MSG_HEALTH_LOAD_WIN", uph=up_h,
                            cpu_pct=(f"{_cpu}" if _cpu is not None else "?")))
        else:
            out.append(_msg(
                "MSG_HEALTH_LOAD",
                l1=load.get("1m", "?"), l5=load.get("5m", "?"),
                l15=load.get("15m", "?"), uph=up_h,
            ))
    mem = h.get("memory") or {}
    if (_keep is None or "memory" in _keep) and mem.get("available"):
        used_gb = (mem.get("used_mb", 0)) // 1024
        tot_gb = (mem.get("total_mb", 0)) // 1024
        swap_pct = mem.get("swap_pct", 0) or 0
        swap_str = (_msg("MSG_HEALTH_RAM_SWAP", swap_pct=f"{swap_pct:.0f}")
                    if swap_pct > 0 else "")
        out.append(_msg(
            "MSG_HEALTH_RAM",
            pct=mem.get("pct", "?"), used_gb=used_gb,
            tot_gb=tot_gb, swap=swap_str,
        ))
    disks = h.get("disk") or []
    if (_keep is None or "disk" in _keep) and disks:
        disk_strs = []
        for d in disks[:5]:
            mount = d.get("mount", "?")
            pct = d.get("pct", "?")
            free_gb = d.get("free_gb")
            if free_gb is not None:
                disk_strs.append(f"{mount} {pct}% (free {free_gb} GB)")
            else:
                disk_strs.append(f"{mount} {pct}%")
        out.append(_msg("MSG_HEALTH_DISKS", body=" · ".join(disk_strs)))
    thermal = h.get("thermal") or {}
    if (_keep is None or "thermal" in _keep) and thermal.get("available"):
        therm_strs = []
        for label_key, kind in (("cpu_c", "CPU"), ("gpu_c", "GPU"), ("nvme_c", "NVMe")):
            v = thermal.get(label_key)
            if v is not None:
                therm_strs.append(f"{kind} {v}°C")
        if therm_strs:
            out.append(_msg("MSG_HEALTH_THERMAL", body=" · ".join(therm_strs)))
    elif (_keep is None or "thermal" in _keep):
        # Il device può essere raggiungibile ma non esporre sensori termici
        # (caso comune su Windows senza API/driver HW disponibili). Non
        # lasciare una risposta apparentemente vuota.
        out.append(_msg("MSG_HEALTH_SECTION_EMPTY", sections="thermal"))
    power = h.get("power") or {}
    if (_keep is None or "power" in _keep) and (
            power.get("available_cpu") or power.get("available_gpu")):
        pwr_strs = []
        cw = power.get("cpu_watts")
        if cw is not None:
            pwr_strs.append(f"CPU {cw} W")
        gw = power.get("gpu_watts")
        if gw is not None:
            vendor = (power.get("vendor") or "").upper()
            label = f"GPU{f' [{vendor}]' if vendor else ''}"
            pwr_strs.append(f"{label} {gw} W")
        if pwr_strs:
            out.append(_msg("MSG_HEALTH_POWER", body=" · ".join(pwr_strs)))
    network = h.get("network") or []
    if _keep is None and network:  # nel focus la riga rete (con MAC) è sopra
        net_strs = []
        for n in network:
            if not isinstance(n, dict):
                continue
            iface = n.get("iface", "?")
            ipv4 = n.get("ipv4") or []
            ipv6 = n.get("ipv6") or []
            addrs = ipv4 + [a for a in ipv6 if not a.startswith("fe80")]  # skip link-local
            if not addrs:
                continue
            up_mark = "" if n.get("up") else " ✗"
            net_strs.append(f"{iface}{up_mark} {', '.join(addrs)}")
        if net_strs:
            out.append(_msg("MSG_HEALTH_NETWORK", body=" · ".join(net_strs)))
    services = h.get("services") or []
    if (_keep is None or "services" in _keep) and services:
        svc_strs = []
        for s in services:
            # Strip prefisso `metnos-` E suffisso `.timer` per leggibilita':
            # "metnos-i18n-translator.timer" → "i18n-translator".
            name = (s.get("name") or "").replace("metnos-", "")
            if name.endswith(".timer"):
                name = name[: -len(".timer")]
            mark = "✓" if s.get("status") == "active" else "✗"
            svc_strs.append(f"{name} {mark}")
        out.append(_msg("MSG_HEALTH_SERVICES", body=" · ".join(svc_strs)))
    # Separator \n: a single block of consecutive lines → un solo <p>
    # con <br> interni su HTTP (GFM soft line break, vedi html_sanitizer
    # `_flush_para`); su Telegram restano newline naturali. Niente
    # paragrafi separati: evita la riga vuota visiva fra Carico/RAM/...
    return "\n".join(out)


def _fmt_documents_block(docs: list) -> str:
    """Lista compatta dei documenti scoperti."""
    out = [_msg("MSG_DOCS_DISCOVERED", n=len(docs))]
    for d in docs[:15]:
        if not isinstance(d, dict):
            continue
        anchor = (d.get("anchor_text") or "").strip() or d.get("url", "")
        ext = (d.get("ext") or "").lstrip(".")
        score = d.get("score", 0)
        out.append(f"  [{ext}] {anchor[:60]}  ({score:.1f})")
    if len(docs) > 15:
        out.append(_msg("MSG_OMITTED_OTHERS", n=len(docs) - 15))
    return "\n".join(out)


def _fmt_entries_block(entries: list, cap: int) -> str:
    """Lista compatta delle entries (process names, file paths, ...).

    Per get_processes (records con cpu_pct + mem_pct) usa una tabella
    markdown (ADR 0095): formato strutturato leggibile su HTTP/Telegram.
    """
    out: list[str] = []
    if cap < len(entries):
        out.append(_msg("MSG_TOP_OF", top=cap, total=len(entries)))
    # Special case: get_processes records → tabella markdown.
    # Il campo nome è `comm` (ps/tasklist) o `name`: il match solo-`name`
    # rendeva la tabella MORTA da sempre (dict grezzi in chat, visto 5/7).
    proc_records = [
        e for e in entries[:cap]
        if isinstance(e, dict) and "cpu_pct" in e
        and ("name" in e or "comm" in e)
    ]
    if proc_records and len(proc_records) == len([
        e for e in entries[:cap] if isinstance(e, dict)
    ]):
        from output_format import format_table
        # Windows (tasklist) non ha mem_pct: usa mem_kb→MB come colonna RAM.
        use_pct = any(e.get("mem_pct") for e in proc_records)
        rows = [
            [
                str(e.get("name") or e.get("comm") or "?")[:24],
                f"{e.get('cpu_pct', 0):.1f}",
                (f"{e.get('mem_pct', 0):.1f}" if use_pct
                 else f"{(e.get('mem_kb') or 0) / 1024:.0f}"),
            ]
            for e in proc_records
        ]
        out.append(format_table(
            headers=[_msg("MSG_PROCESS_HEADER_NAME"), "CPU%",
                     "MEM%" if use_pct else "RAM MB"],
            rows=rows,
            align=["left", "right", "right"],
        ))
        return "\n".join(out)
    for e in entries[:cap]:
        if not isinstance(e, dict):
            out.append(f"  {str(e)[:80]}")
            continue
        # Per get_processes mostra anche cpu/mem.
        if "cpu_pct" in e and "name" in e:
            out.append(
                f"  {e.get('name','?')[:24]:24s} cpu={e.get('cpu_pct',0):4.1f}%  "
                f"mem={e.get('mem_pct',0):4.1f}%"
            )
            continue
        # Fallback: primo campo testuale.
        label_e = ""
        for k in ("path", "name", "title", "query", "url"):
            v = e.get(k)
            if isinstance(v, str) and v:
                label_e = v.rsplit("/", 1)[-1] if k == "path" else v
                break
        if not label_e:
            label_e = json.dumps(e, ensure_ascii=False)[:80]
        score = e.get("score")
        if isinstance(score, (int, float)):
            out.append(f"  {score:+.3f}  {label_e}")
        else:
            out.append(f"  {label_e}")
    if len(entries) > cap:
        out.append(_msg("MSG_OMITTED_OTHERS_F", n=len(entries) - cap))
    return "\n".join(out)


def _process_start_oauth_redirect_flow(on_complete: dict, values: dict, *,
                                        sender_id: str = "",
                                        dialog_id: str = "",
                                        channel: Optional[str] = None,
                                        actor: str = "host",
                                        host_override: Optional[str] = None) -> str:
    """Avvia un flow OAuth 2.0 (Authorization Code) generico con redirect
    HTTP callback. Niente conoscenza di provider specifici: tutti i
    parametri provider-dependent arrivano dal caller via `on_complete`.

    Pattern callback:
      payload = {
        "type": "start_oauth_redirect_flow",
        "binding": str,                      # chiave credentials (es. 'google-workspace')
        "executor": str,                     # nome executor da ri-invocare
        "args_base": dict,                   # args originali
        "scopes_options": [                  # opzioni esposte all'utente
          {"label": "calendar", "scopes": [URL_1, URL_2, ...]},
          ...
        ],
        "mirror_paths": [str, ...]           # opt: path plain per legacy compat
        "client_secret_install_path": str    # opt: path dove copiare client_secret
      }

    Mapping `values["services"]` (scelta utente) -> `scopes`:
    si cerca la entry con `label == services` in `scopes_options`.
    """
    client_secret_path = values.get("client_secret_path") or ""
    services = values.get("services") or ""
    executor = on_complete.get("executor") or ""
    args_base = dict(on_complete.get("args_base") or {})
    binding = on_complete.get("binding") or ""
    scopes_options = on_complete.get("scopes_options") or []
    mirror_paths = list(on_complete.get("mirror_paths") or [])
    client_secret_install_path = on_complete.get("client_secret_install_path")

    if not client_secret_path:
        return _msg("MSG_ORCH_OAUTH_NO_SECRET_PATH")
    if not executor:
        return _msg("MSG_ORCH_OAUTH_NO_EXECUTOR")
    if not binding:
        return _msg("MSG_ORCH_OAUTH_NO_BINDING")

    scopes = _resolve_scopes_from_options(scopes_options, services)
    if not scopes:
        return _msg("MSG_ORCH_OAUTH_NO_SCOPE")

    redirect_uri = _resolve_oauth_redirect_uri(host_override=host_override)

    try:
        import oauth_flow
        import oauth_pending
        auth_url, flow_state = oauth_flow.start_flow(
            client_secret_path=client_secret_path,
            scopes=scopes,
            redirect_uri=redirect_uri,
            state="",
            client_secret_install_path=client_secret_install_path,
        )
    except FileNotFoundError as ex:
        return _msg("MSG_ORCH_OAUTH_SECRET_NOT_FOUND", detail=str(ex))
    except (ImportError, OSError, RuntimeError, ValueError) as ex:
        return _msg("MSG_ORCH_OAUTH_START_FAILED", detail=f"{type(ex).__name__}: {ex}")

    state_token = oauth_pending.put({
        "flow_state": flow_state,
        "executor": executor,
        "args_base": args_base,
        "binding": binding,
        "mirror_paths": mirror_paths,
        "sender_id": sender_id,
        "channel": channel or "",
        "dialog_id": dialog_id,
        "services": services,
    })
    auth_url_with_state = _inject_state_param(auth_url, state_token)
    flow_state["state"] = state_token

    # Marker strutturato __REDIRECT__: il caller HTTP (dialog_submit)
    # riconosce il prefix e invece di mostrare il messaggio fa navigation
    # diretta del browser al URL OAuth. Niente intermediate "clicca qui"
    # — il consent screen di Google si apre subito. La parte testuale dopo
    # il newline e' fallback per canali che non possono fare redirect
    # (es. Telegram: l'utente apre il link manualmente).
    msg = _msg("MSG_ORCH_OAUTH_LINK_PROMPT", url=auth_url_with_state)
    return f"__REDIRECT__:{auth_url_with_state}\n{msg}"


def _resolve_scopes_from_options(scopes_options: list, selection: str) -> list:
    """`scopes_options` = [{label, scopes}, ...]. Ritorna gli scope della
    entry con `label == selection`. Se selection vuota o non trovata,
    ritorna gli scope della prima entry (fallback)."""
    if not scopes_options:
        return []
    for opt in scopes_options:
        if isinstance(opt, dict) and opt.get("label") == selection:
            return list(opt.get("scopes") or [])
    first = scopes_options[0]
    if isinstance(first, dict):
        return list(first.get("scopes") or [])
    return []


def _resolve_oauth_redirect_uri(host_override: Optional[str] = None) -> str:
    """URL di callback OAuth lato Metnos.

    Ordine: env METNOS_OAUTH_REDIRECT_URI > host_override (origin completo
    `scheme://host` o solo `host`) > config DEFAULT_OAUTH_REDIRECT_URI >
    derivato da http_port localhost.

    `host_override` puo' essere:
      - URL prefix completo (`https://chat.metnos.com`) → usato as-is.
      - Solo host (`192.0.2.10:8770`) → prefisso `http://` (LAN).

    Necessario per reverse proxy / tunnel HTTPS (Cloudflare, nginx, ecc.):
    Metnos riceve HTTP plain ma il client originale ha usato HTTPS. Il
    chiamante (`dialog_submit`) legge `X-Forwarded-Proto` per costruire
    l'origin corretto."""
    import os as _os
    env_url = _os.environ.get("METNOS_OAUTH_REDIRECT_URI")
    if env_url:
        return env_url
    if host_override:
        if host_override.startswith("http://") or host_override.startswith("https://"):
            return f"{host_override.rstrip('/')}/oauth/callback"
        return f"http://{host_override}/oauth/callback"
    try:
        from config import DEFAULT_OAUTH_REDIRECT_URI as _u
        if _u:
            return _u
    except Exception:
        pass
    try:
        from config import HTTP_PORT as _port
    except Exception:
        _port = 8770
    return f"http://localhost:{_port}/oauth/callback"


def _inject_state_param(url: str, state: str) -> str:
    """Sostituisce/aggiunge `state=<state>` in un URL OAuth.

    `google_auth_oauthlib` mette state="" nell'URL quando passiamo state=""
    a `authorization_url`. Qui sostituiamo col token vero (non possiamo
    passarlo a start_flow perche' il token e' generato DA oauth_pending.put,
    che vuole il flow_state per memorizzarlo)."""
    import urllib.parse as _up
    parts = list(_up.urlparse(url))
    q = dict(_up.parse_qsl(parts[4], keep_blank_values=True))
    q["state"] = state
    parts[4] = _up.urlencode(q)
    return _up.urlunparse(parts)


# ── Esposizione del mapping needs_inputs → orchestrazione ────────────

def orchestrate_needs_inputs(obs: dict, *,
                              sender_id: str,
                              actor: str = "host",
                              channel: Optional[str] = None,
                              origin_turn_id: str = "") -> dict:
    """Helper di alto livello: dato l'observation di un tool che ha emesso
    `decision="needs_inputs"`, costruisce il payload e chiama
    `invoke_get_inputs_internal`. Ritorna il dict di get_inputs.

    Idempotente: se l'observation e' malformato (manca needs_inputs payload),
    ritorna `{ok: False, error: ...}`.
    """
    if not isinstance(obs, dict):
        return {"ok": False, "error": _msg("MSG_ORCH_OBS_NOT_DICT")}
    if obs.get("decision") != "needs_inputs":
        return {"ok": False,
                "error": _msg("MSG_ORCH_DECISION_NOT_NEEDS_INPUTS", decision=repr(obs.get('decision')))}
    payload = obs.get("needs_inputs") or {}
    if not isinstance(payload, dict):
        return {"ok": False, "error": _msg("MSG_ORCH_PAYLOAD_NOT_DICT")}

    title = payload.get("title") or _msg("MSG_ORCH_DEFAULT_INPUTS_TITLE")
    description = payload.get("description")
    dialog = payload.get("dialog") or []
    fmt = payload.get("fmt") or "auto"
    on_complete = payload.get("on_complete")
    timeout_s = int(payload.get("timeout_s") or 3600)

    return invoke_get_inputs_internal(
        sender_id=sender_id,
        title=title,
        description=description,
        dialog=dialog,
        fmt=fmt,
        on_complete=on_complete,
        actor=actor,
        channel=channel,
        timeout_s=timeout_s,
        origin_turn_id=origin_turn_id,
    )
