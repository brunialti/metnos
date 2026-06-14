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
        return {"ok": False, "error": "title mancante per orchestrazione"}
    if not isinstance(dialog, list) or not dialog:
        return {"ok": False, "error": "dialog vuoto o non lista"}

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
        if channel == "http" and (has_preview_step or n_steps >= 2):
            resolved_fmt = "form"
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
        return {"ok": False, "error": f"save_pending fallito: {ex}"}

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
    title = _resolve_msg(state.get("title") or "Domanda")
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
        lines.append(f"({n} campi da compilare; rispondi `annulla` per abortire.)")
        return "\n".join(lines)
    # dialogue (default)
    first = dialog[0]
    prompt = _resolve_msg(first.get("prompt") or "?")
    lines = [title]
    if descr:
        lines.append(descr)
    lines.append("")
    lines.append(f"Step 1/{n} — {prompt}")
    schema = first.get("schema") or {}
    if schema.get("kind") == "credentials":
        lines.append("(la risposta sara' mascherata in registro)")
    elif schema.get("kind") == "choice":
        choices = schema.get("choices") or []
        if choices:
            lines.append("")
            for i, ch in enumerate(choices, 1):
                lines.append(f"  {i}. {ch}")
    lines.append("")
    lines.append("Rispondi nel prossimo messaggio. `annulla` per abortire.")
    return "\n".join(lines)


# ── process_completion_callback ───────────────────────────────────────

def process_completion_callback(sender_id: str, dialog_id: str,
                                  *, actor: str = "host",
                                  channel: Optional[str] = None,
                                  host_override: Optional[str] = None) -> str:
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
      str: messaggio user-facing da mandare nel canale. Mai None: il
      caller assume che ci sia sempre qualcosa da inviare.
    """
    state = dialog_pending.load_pending(sender_id, dialog_id)
    if state is None:
        return f"(Dialogo {dialog_id} non trovato. Riformula la richiesta.)"
    if not state.get("completed"):
        return ("(Dialogo non ancora completo. Compila tutti i campi prima "
                "di procedere.)")
    on_complete = state.get("on_complete")
    if not isinstance(on_complete, dict):
        # Niente callback dichiarato: solo conferma generica.
        return ("Dialogo completato. I valori sono stati registrati.")

    callback_type = on_complete.get("type")
    values = state.get("values_collected") or {}

    if callback_type == "save_credentials_and_resume":
        return _process_save_credentials_and_resume(
            on_complete, values, actor=actor,
        )

    if callback_type == "expand_cap_and_resume":
        return _process_expand_cap_and_resume(
            on_complete, values, actor=actor, channel=channel,
        )

    if callback_type == "resume_executor_with_values":
        return _process_resume_executor_with_values(
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

    if callback_type == "restart_turn_with_chosen_query":
        return _process_restart_turn_with_chosen_query(
            on_complete, values, actor=actor, channel=channel,
        )

    if callback_type == "strato3_choice_dispatch":
        return _process_strato3_choice_dispatch(
            on_complete, values, actor=actor, channel=channel,
        )

    # github_analyze / github_send_reply: RITIRATI (flusso watcher legacy →
    # executor write/read/find_issues + comandi schedulati).

    log.warning("on_complete type sconosciuto: %s", callback_type)
    return (f"Dialogo completato, ma il tipo callback "
            f"'{callback_type}' non e' implementato.")


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
        return ("(Dialogo completato ma username/password mancanti: "
                "non posso salvare le credenziali. Riformula la richiesta.)")

    if not domain:
        return ("(Dialogo completato ma il dominio target e' vuoto: "
                "non posso salvare le credenziali. Riformula la richiesta.)")

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
        return f"(Salvataggio credenziali fallito: {type(ex).__name__}: {ex})"

    # 2) resume_call
    if not resume_call:
        # Nessun resume previsto: solo conferma del save.
        return (f"Credenziali per {domain} salvate. Riformula la richiesta "
                f"originale per procedere.")

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
        return (f"Credenziali salvate ma rilancio di '{resume_call}' "
                f"fallito: {type(ex).__name__}: {ex}")

    if isinstance(res, dict):
        return (res.get("summary")
                or json.dumps(res, ensure_ascii=False)[:600])
    return str(res)


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
        return "(Cap-expand mal formato: nessun executor specificato.)"

    try:
        from loader import load_catalog
        cat = load_catalog(verify=True, include_synth=True)
        ex = cat.executors.get(executor)
        if ex is None:
            return f"(Executor {executor} non in catalog: rilancio annullato.)"
        import agent_runtime
        res = agent_runtime.invoke_executor(
            ex, args, timeout_s=getattr(ex, "timeout_s", 30),
            actor=actor, channel=channel,
        )
    except (PermissionError, KeyError, RuntimeError, TypeError) as ex:
        log.exception("orchestration: expand_cap invoke fallito")
        return (f"Rilancio fallito: {type(ex).__name__}: {ex}")

    if not isinstance(res, dict) or not res.get("ok"):
        err = (res or {}).get("error", "errore sconosciuto") if isinstance(res, dict) else "no result"
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
        body_blocks.append(_fmt_health_block(health))

    docs = res.get("discovered_documents") or []
    if isinstance(docs, list) and docs:
        body_blocks.append(_fmt_documents_block(docs))

    if entries:
        # Compatta: 1 linea per entry, max 20. Se health era reso, riduci a 8
        # (la sezione health gia' occupa righe).
        cap_preview = 8 if health else 20
        body_blocks.append(_fmt_entries_block(entries, cap_preview))

    if not body_blocks:
        # Output non-list-shaped (es. summary stringa).
        return head + "\n\n" + (res.get("summary") or
                                  json.dumps(res, ensure_ascii=False)[:600])

    return head + "\n\n" + "\n\n".join(body_blocks)


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
        return "(resume_executor_with_values: executor mancante.)"

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
            return f"(Executor {executor} non in catalog: rilancio annullato.)"
        import agent_runtime
        res = agent_runtime.invoke_executor(
            ex, args_base, timeout_s=getattr(ex, "timeout_s", 30),
            actor=actor, channel=channel,
        )
    except (PermissionError, KeyError, RuntimeError, TypeError) as ex:
        log.exception("orchestration: resume_executor_with_values fallito")
        return (f"Rilancio fallito: {type(ex).__name__}: {ex}")

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

    if isinstance(res, dict):
        msg = res.get("final_message_hint") or res.get("summary")
        if msg:
            return msg
        # Backstop universale (§ output formatter, no-raw-leak): MAI json.dumps
        # grezzo in chat (l'utente vedeva «{...}»). Sintesi pulita e i18n da
        # ok/error: l'executor che vuole testo ricco espone `summary`.
        if res.get("ok") is False:
            err = res.get("error") or res.get("error_class") or ""
            return f"✗ {err}" if err else _msg("ERR_GENERIC")
        return _msg("MSG_ACTION_DONE")
    return str(res)


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
        return ("Ok, mi fermo qui." if lang != "en"
                else "Ok, stopping here.")
    if action_key == "reformulate":
        return ("Riformula la richiesta nel prossimo messaggio."
                if lang != "en"
                else "Reformulate your request in the next message.")
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
            return f"Ritenta fallita: {type(ex).__name__}: {ex}"
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
        return ("Scelta non riconosciuta." if lang != "en"
                else "Unrecognized choice.")
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
        return f"Continuation fallita: {type(ex).__name__}: {ex}"
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
        return ("(Disambiguazione: scelta vuota, niente da rilanciare. "
                "Riformula la richiesta.)")
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
        return f"Continuation fallita: {type(ex).__name__}: {ex}"
    if new_log is None:
        return "(continuation: turno vuoto, nessuna final_message.)"
    return getattr(new_log, "final_message", "") or ""


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

    if not original_query:
        return ("(resume_planner_with_dialog_values: original_query "
                "mancante in on_complete, continuation impossibile.)")

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
        if isinstance(entry, dict) and entry.get("tool") == "get_inputs":
            entry["observation"] = dialog_obs
            _replaced = True
            break
    if not _replaced:
        prior_steps.append({
            "step": int(dialog_step_num),
            "tool": "get_inputs",
            "args": {"dialog": "<elided>"},
            "observation": dialog_obs,
        })

    # Orchestratore deterministico post-dialog (ADR 0129, 14/5/2026):
    # esegue gli `implicit_actions` (dall'intent del turno originale)
    # piu' un eventuale notify finale (send_messages) quando la query
    # contiene un notify-hint. Il PLANNER LLM medium su pipeline
    # multi-pipeline si e' rivelato fragile (loop / dimentica step).
    # Determinismo §7.9, §7.3 (generale, no hardcoded).
    implicit_actions = on_complete.get("implicit_actions") or []
    det = _orchestrate_implicit_actions(
        original_query, values, prior_steps,
        implicit_actions=implicit_actions,
        actor=actor, channel=channel,
    )
    if det is not None:
        return det

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
        return (f"Continuation fallita: {type(ex).__name__}: {ex}")

    if new_log is None:
        return "(continuation: turno vuoto, nessuna final_message.)"
    msg_out = getattr(new_log, "final_message", "") or ""
    if not msg_out:
        return "(continuation completata.)"
    return msg_out


# --- Notify-hint canonical (ADR 0129) ---------------------------------
# IT + EN, usato per detectare la richiesta di notifica esplicita post-
# dialog. Da estendere quando si supportano nuove lingue (cfr. ADR 0092).
_NOTIFY_HINTS = (
    "mandami", "manda", "inviami", "invia", "notificami",
    "scrivimi", "avvisami", "informami", "rispondimi",
    "send me", "email me", "notify me", "let me know",
)

# Hint linguistici per disambiguare il canale di notifica preferito.
_CHANNEL_HINTS = {
    "email":    ("email", "e-mail", "mail", "posta"),
    "telegram": ("telegram", "telegrami", "chat", "messaggio telegram"),
}


def _resolve_actor_to_user(actor: str) -> dict | None:
    """Risolve `actor` (es. 'host', 'roberto', user_id) a una row utente.
    Generalizzato §7.3: prima exact match per name/id, poi role match.
    Ritorna None se nessun match. Determinismo §7.9.
    """
    try:
        import sqlite3
        import config as _C  # §7.11
        db = sqlite3.connect(str(_C.PATH_USER_DATA / "users.db"))
        cur = db.cursor()
        cur.row_factory = sqlite3.Row
        rows = cur.execute(
            "SELECT id, name, role, email FROM users"
        ).fetchall()
        db.close()
    except Exception:
        return None
    if not rows:
        return None
    a = (actor or "").strip().lower()
    # 1) exact match by id or name
    for r in rows:
        if a in (str(r["id"]).lower(), str(r["name"]).lower()):
            return {"id": r["id"], "name": r["name"], "role": r["role"], "email": r["email"]}
    # 2) role match (a='host' → primo host)
    for r in rows:
        if str(r["role"]).lower() == a:
            return {"id": r["id"], "name": r["name"], "role": r["role"], "email": r["email"]}
    # 3) default fallback: primo host
    for r in rows:
        if str(r["role"]).lower() == "host":
            return {"id": r["id"], "name": r["name"], "role": r["role"], "email": r["email"]}
    return None


def _extract_chosen_from_values(values: dict) -> tuple[str | None, str]:
    """Estrai (chosen_value, chosen_label) dai dialog values (qualsiasi var)."""
    for _var, val in (values or {}).items():
        if isinstance(val, dict) and isinstance(val.get("value"), str):
            return val["value"], val.get("label", "")
        if isinstance(val, str):
            return val, ""
    return None, ""


def _match_entry_in_prior_steps(prior_steps: list, chosen_value: str,
                                  *, match_field: str = "start"
                                  ) -> dict | None:
    """Trova in prior_steps l'entry il cui `match_field` matcha `chosen_value`."""
    for s in prior_steps:
        if not isinstance(s, dict):
            continue
        obs = s.get("observation") or {}
        if not isinstance(obs, dict) or not obs.get("ok"):
            continue
        for e in (obs.get("entries") or []):
            if isinstance(e, dict) and e.get(match_field) == chosen_value:
                return e
    return None


# --- Action templates: lookup (verb, object) → args builder -----------------
# Ogni entry e' una funzione che riceve un context dict e ritorna gli args
# da passare a `invoke_executor`. Il context contiene:
#   chosen_value: str (es. ISO datetime), chosen_label: str,
#   matched_entry: dict | None (entry del prior step che matcha chosen_value),
#   actor: str, original_query: str, user_row: dict | None.
# Ritorna None se non puo' costruire args validi (es. match_field mancante).

def _args_create_events(ctx: dict) -> dict | None:
    e = ctx.get("matched_entry") or {}
    start = ctx.get("chosen_value")
    end = e.get("end")
    if not start or not end:
        return None
    return {"summary": "Appuntamento", "start": start, "end": end}


# Tabella canonica (verb, object) -> tool_name + args_builder.
# Estendere via PR quando si aggiungono nuovi pattern propose+fire.
_ACTION_TEMPLATES: dict[tuple[str, str], dict] = {
    ("create", "events"): {
        "tool":  "create_events",
        "args":  _args_create_events,
        "label": "Appuntamento",
    },
    # Posto per pattern futuri:
    # ("set",    "messages"): {...},   # propose-label + apply
    # ("create", "dirs"):     {...},   # propose-name + mkdir
    # ("share",  "files"):    {...},   # propose-target + grant
}


def _orchestrate_implicit_actions(
    original_query: str, values: dict, prior_steps: list,
    *, implicit_actions: list, actor: str = "host", channel: str = "",
) -> str | None:
    """Esegue deterministicamente gli `implicit_actions` post-dialog +
    eventuale notify (ADR 0129). Ritorna None se nessun match (caller
    delega al PLANNER LLM).

    Pipeline:
      1. Per ogni `implicit_action`: lookup `_ACTION_TEMPLATES[(verb, object)]`.
         Se presente, costruisce args via builder + invoca via catalog.
      2. Se `original_query` contiene un notify-hint, invoca `send_messages`
         all'utente (mail di conferma con riepilogo delle azioni eseguite).

    Generale §7.3: nessun pattern hardcoded, lookup tabellare estendibile.
    """
    if not isinstance(implicit_actions, list) or not implicit_actions:
        # Senza implicit_actions, non c'e' nulla da orchestrare deterministica-
        # mente: lascia al PLANNER.
        return None

    chosen_value, chosen_label = _extract_chosen_from_values(values)
    if not chosen_value:
        return None
    matched_entry = _match_entry_in_prior_steps(
        prior_steps, chosen_value, match_field="start",
    )
    user_row = _resolve_actor_to_user(actor)

    ctx = {
        "chosen_value": chosen_value,
        "chosen_label": chosen_label,
        "matched_entry": matched_entry,
        "actor": actor,
        "original_query": original_query,
        "user_row": user_row,
    }

    # Catalog load shared
    try:
        from loader import load_catalog
        import agent_runtime as _ar
        cat = load_catalog(verify=True, include_synth=True)
    except Exception as ex:
        log.exception("orchestrate_implicit_actions: catalog load fallito")
        return f"Catalog load fallito: {type(ex).__name__}: {ex}"

    def _run(name: str, args: dict) -> dict:
        ex = cat.executors.get(name)
        if ex is None:
            return {"ok": False, "error": f"executor {name} non in catalog"}
        try:
            return _ar.invoke_executor(
                ex, args, timeout_s=getattr(ex, "timeout_s", 30),
                actor=actor, channel=channel or "http",
            )
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def _handle_needs_inputs(r: dict) -> str | None:
        """Generale §7.3: qualunque backend (gw/local/imap/...) puo' chiedere
        OAuth setup o altri input. Registriamo dialog_pending + ritorniamo
        msg user-facing. Idempotente: se gia' registrato, ritorna comunque.
        """
        sender_id = (f"{channel}:{actor}" if channel else (actor or "host"))
        try:
            gi = orchestrate_needs_inputs(
                r, sender_id=sender_id, actor=actor, channel=channel,
            )
        except Exception as ex:
            log.exception("orchestrate_implicit_actions: needs_inputs dispatch")
            return f"OAuth setup fallito: {type(ex).__name__}: {ex}"
        return (gi or {}).get("final_message_hint") or (
            (r.get("needs_inputs") or {}).get("title")
            or "Servono credenziali per completare l'azione."
        )

    out_lines: list[str] = []
    actions_executed: list[dict] = []
    for ia in implicit_actions:
        if not isinstance(ia, dict):
            continue
        if ia.get("strategy") not in ("auto", "ask"):
            continue
        v = (ia.get("verb_canonical") or "").lower()
        o = (ia.get("object") or "").lower()
        tpl = _ACTION_TEMPLATES.get((v, o))
        if tpl is None:
            # Pattern non in tabella: lascia al PLANNER (return None).
            return None
        args = tpl["args"](ctx) if callable(tpl.get("args")) else None
        if not args:
            return None
        r = _run(tpl["tool"], args)
        # Backend richiede credenziali / input → dialog OAuth flow.
        if isinstance(r, dict) and r.get("decision") == "needs_inputs":
            msg_oauth = _handle_needs_inputs(r)
            return msg_oauth or "Servono credenziali per completare l'azione."
        if not (r or {}).get("ok"):
            return (f"{tpl['tool']} fallito: "
                    f"{(r or {}).get('error','errore sconosciuto')}")
        rec = {"tool": tpl["tool"], "args": args, "result": r,
               "label": tpl.get("label") or tpl["tool"]}
        actions_executed.append(rec)
        out_lines.append(
            f"{rec['label']} creato per {chosen_label or chosen_value}."
        )

    if not actions_executed:
        return None

    # Notify finale (send_messages) se la query lo richiede esplicitamente.
    q_low = (original_query or "").lower()
    has_notify = any(h in q_low for h in _NOTIFY_HINTS)
    if has_notify:
        # Canale preferito da hint linguistici; default email per «email» o
        # in assenza di hint specifici.
        via = "email"
        for ch, hints in _CHANNEL_HINTS.items():
            if any(h in q_low for h in hints):
                via = ch
                break

        # Subject + body generati dal riepilogo delle azioni eseguite
        subject_label = actions_executed[0]["label"]
        subject = f"Conferma {subject_label.lower()} {chosen_label or chosen_value}"
        body_lines = [
            f"Riepilogo delle azioni eseguite per: «{original_query.strip()}»",
            "",
        ]
        for rec in actions_executed:
            body_lines.append(f"  • {rec['label']}:")
            for k, v in (rec["args"] or {}).items():
                body_lines.append(f"      - {k}: {v}")
        body = "\n".join(body_lines)

        # Target user via resolution: actor → user.email; fallback to_user=actor.
        send_args: dict = {
            "messages": [{"subject": subject, "body": body}],
            "via_channel": via,
        }
        if user_row and user_row.get("email") and via == "email":
            send_args["messages"][0]["to"] = user_row["email"]
        elif user_row and user_row.get("name"):
            send_args["to_user"] = user_row["name"]
        else:
            send_args["to_user"] = actor

        sm = _run("send_messages", send_args)
        # Anche send_messages backend (es. google_workspace gmail) puo'
        # richiedere OAuth setup: stesso handler generale §7.3.
        if isinstance(sm, dict) and sm.get("decision") == "needs_inputs":
            msg_oauth = _handle_needs_inputs(sm)
            out_lines.append(
                msg_oauth or "Servono credenziali per inviare la notifica."
            )
        elif (sm or {}).get("ok"):
            channel_label = "Email" if via == "email" else via.capitalize()
            out_lines.append(f"{channel_label} di conferma inviata.")
        else:
            err = (sm or {}).get("error") or "errore sconosciuto"
            failed = (sm or {}).get("failed") or []
            if failed and isinstance(failed[0], dict):
                err = failed[0].get("error") or err
            out_lines.append(f"Notifica NON inviata: {err}")

    return "\n".join(out_lines)



def _fmt_health_block(h: dict) -> str:
    """Rende la sezione health in 4-6 righe leggibili.

    Stile per ADR 0095 (output deterministico): KV con label espliciti,
    no slash ambigui per gruppi correlati (load 1m/5m/15m), unita' inline.
    """
    out = [_msg("MSG_HEALTH_TITLE")]
    load = h.get("load") or {}
    if load.get("available"):
        up_h = (load.get("uptime_s") or 0) // 3600
        out.append(_msg(
            "MSG_HEALTH_LOAD",
            l1=load.get("1m", "?"), l5=load.get("5m", "?"),
            l15=load.get("15m", "?"), uph=up_h,
        ))
    mem = h.get("memory") or {}
    if mem.get("available"):
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
    if disks:
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
    if thermal.get("available"):
        therm_strs = []
        for label_key, kind in (("cpu_c", "CPU"), ("gpu_c", "GPU"), ("nvme_c", "NVMe")):
            v = thermal.get(label_key)
            if v is not None:
                therm_strs.append(f"{kind} {v}°C")
        if therm_strs:
            out.append(_msg("MSG_HEALTH_THERMAL", body=" · ".join(therm_strs)))
    power = h.get("power") or {}
    if power.get("available_cpu") or power.get("available_gpu"):
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
    if network:
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
    if services:
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
    proc_records = [
        e for e in entries[:cap]
        if isinstance(e, dict) and "cpu_pct" in e and "name" in e
    ]
    if proc_records and len(proc_records) == len([
        e for e in entries[:cap] if isinstance(e, dict)
    ]):
        from output_format import format_table
        rows = [
            [
                str(e.get("name", "?"))[:24],
                f"{e.get('cpu_pct', 0):.1f}",
                f"{e.get('mem_pct', 0):.1f}",
            ]
            for e in proc_records
        ]
        out.append(format_table(
            headers=[_msg("MSG_PROCESS_HEADER_NAME"), "CPU%", "MEM%"],
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
        return "(client_secret_path mancante: form OAuth non puo' partire.)"
    if not executor:
        return "(executor mancante in on_complete: OAuth non riavviabile.)"
    if not binding:
        return "(binding mancante in on_complete: token non salvabile.)"

    scopes = _resolve_scopes_from_options(scopes_options, services)
    if not scopes:
        return ("(Nessuno scope risolto per la scelta utente: il caller "
                "deve fornire scopes_options non vuoto e services valido.)")

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
        return f"(File client_secret non trovato: {ex})"
    except (ImportError, OSError, RuntimeError, ValueError) as ex:
        return f"(Avvio OAuth fallito: {type(ex).__name__}: {ex})"

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
    msg = (
        f"Apri questo link per autorizzare Metnos:\n\n"
        f"{auth_url_with_state}\n\n"
        f"Dopo l'autorizzazione il browser ti riporta qui e il setup si "
        f"completa in automatico. Subito dopo Metnos rilancia la "
        f"richiesta originale."
    )
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
        return {"ok": False, "error": "observation non e' dict"}
    if obs.get("decision") != "needs_inputs":
        return {"ok": False,
                "error": f"decision non e' needs_inputs: {obs.get('decision')!r}"}
    payload = obs.get("needs_inputs") or {}
    if not isinstance(payload, dict):
        return {"ok": False, "error": "needs_inputs payload non e' dict"}

    title = payload.get("title") or "Servono alcuni dati"
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
