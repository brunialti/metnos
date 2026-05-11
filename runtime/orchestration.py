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

CLAUDE.md §7.9 (deterministico > LLM): tutta la pipeline di orchestrazione
e' codice deterministico. L'unico LLM nella catena e' il PLANNER iniziale
che chiama admin la prima volta; il resume e' una chiamata diretta al verb.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

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


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


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
                                timeout_s: int = 3600) -> dict:
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
      timeout_s: TTL del dialogo (default 1h).

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
    # - altrimenti dialogue (1 step yes_no o text inline tipico di
    #   cap-expand)
    has_preview_step = any(
        (s.get("schema") or {}).get("kind") == "choice_with_preview"
        for s in dialog
    )
    if fmt == "auto":
        if channel == "http" and (has_preview_step or n_steps >= 2):
            resolved_fmt = "form"
        else:
            resolved_fmt = "dialogue"
    elif fmt == "voice":
        resolved_fmt = "dialogue"  # stub: voice degrada a dialogue
    else:
        resolved_fmt = fmt

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
    if (first.get("schema") or {}).get("kind") == "credentials":
        lines.append("(la risposta sara' mascherata in registro)")
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
            on_complete, values, actor=actor,
        )

    if callback_type == "resume_executor_with_values":
        return _process_resume_executor_with_values(
            on_complete, values, actor=actor,
        )

    if callback_type == "start_oauth_redirect_flow":
        return _process_start_oauth_redirect_flow(
            on_complete, values, sender_id=sender_id,
            dialog_id=dialog_id, channel=channel, actor=actor,
            host_override=host_override,
        )

    log.warning("on_complete type sconosciuto: %s", callback_type)
    return (f"Dialogo completato, ma il tipo callback "
            f"'{callback_type}' non e' implementato.")


def _process_save_credentials_and_resume(on_complete: dict, values: dict,
                                          *, actor: str = "host") -> str:
    """Salva credenziali cifrate e ri-invoca il verb dichiarato (admin).

    Pattern callback `save_credentials_and_resume`:
      payload = {
        "type": "save_credentials_and_resume",
        "credentials_domain": "cifs_192.168.1.20",
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
                                     *, actor: str = "host") -> str:
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

    Direct invoke senza PLANNER (CLAUDE.md §2.11 fase 2 + commento storico
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
                                          *, actor: str = "host") -> str:
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
        )
    except (PermissionError, KeyError, RuntimeError, TypeError) as ex:
        log.exception("orchestration: resume_executor_with_values fallito")
        return (f"Rilancio fallito: {type(ex).__name__}: {ex}")

    if isinstance(res, dict):
        return (res.get("final_message_hint")
                or res.get("summary")
                or json.dumps(res, ensure_ascii=False)[:600])
    return str(res)


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
        disk_strs = [f"{d.get('mount','?')} {d.get('pct','?')}%"
                      for d in disks[:5]]
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
      - Solo host (`192.168.1.33:8770`) → prefisso `http://` (LAN).

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
                              channel: Optional[str] = None) -> dict:
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
    )
