#!/usr/bin/env python3
"""get_inputs — engine UI dichiarativo per dialoghi strutturati (ADR 0090).

Apre un dialogo con l'utente per raccogliere uno o piu' valori. Il
PLANNER dichiara la lista delle domande in `dialog`; il runtime + il
channel adapter (Telegram, HTTP, voice) presentano le domande all'utente
secondo le capacita' del canale; le risposte avanzano lo stato fino a
completamento; al termine il PLANNER puo' rileggere i valori chiamando
nuovamente `get_inputs(dialog_id=<id>)` (cap-pending retrieval pattern,
TASK 4 dello sprint 4-5/5/2026).

Il primo turno (questa invocazione) NON aspetta l'utente: salva lo stato
iniziale, ritorna un descrittore (`decision="input_required"`,
`dialog_id`, `final_message_hint`) e termina. Il channel adapter prende
la palla e dialoga con l'utente. Codice deterministico (the design guide §7.9):
zero LLM nel critical path.

Schema kinds supportati (MVP 4-5/5/2026):
  - text:        stringa libera (default).
  - credentials: come text, ma `secret=true` mascherato in UI.
  - yes_no:      booleano si/no (parser tollerante: si/yes/y/ok ↔ no/n/cancel).
  - choice:      sceglie 1 fra `choices=[...]`.
  - multi_choice:sceglie N fra `choices=[...]` (stub validation, parsing CSV).
  - number:      intero o float.
  - date:        ISO YYYY-MM-DD.
  - file_path:   path letterale (no resolution qui).
  - location:    {lat, lon} (stub: oggi pass-through, da estendere).
"""
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ── Costanti ──────────────────────────────────────────────────────────

VALID_KINDS = (
    "text", "credentials", "yes_no", "choice", "multi_choice",
    "number", "date", "file_path", "location",
    "choice_with_preview",
)

MAX_STEPS = 30
MAX_PROMPT_LEN = 200
MAX_TIMEOUT_S = 3600
DEFAULT_TIMEOUT_S = 3600

# Identificatore snake_case per le var: lettera o `_` come primo char,
# poi alfanumerici/underscore. Niente trattini, spazi o accenti.
import re
_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Path del modulo dialog_pending (vive in runtime/). L'executor viene
# eseguito come subprocess: il runtime aumenta `PYTHONPATH` con la dir
# `runtime/` cosi' possiamo importare moduli condivisi.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "runtime"))

from messages import get as _msg  # noqa: E402


def _safe_sender(actor: str, channel: str | None) -> str:
    """Deriva un sender_id stabile per lo storage. Usa actor (multi-user)
    + channel. Default 'host'. Niente dipendenze esterne: il runtime
    inietta METNOS_ACTOR e METNOS_CHANNEL via env quando disponibili."""
    if not actor:
        actor = "host"
    if channel:
        return f"{channel}:{actor}"
    return actor


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


def _validate_dialog(dialog) -> tuple[bool, str | None]:
    """Validazione deterministica dello schema dialog. Ritorna (ok, error).

    Il PLANNER e' un LLM medium: errori puntuali aiutano a riprovare.
    """
    if not isinstance(dialog, list):
        return False, "dialog deve essere una lista"
    if len(dialog) == 0:
        return False, "dialog deve contenere almeno uno step"
    if len(dialog) > MAX_STEPS:
        return False, f"dialog troppo lungo: {len(dialog)} step, max {MAX_STEPS}"
    seen_vars: set[str] = set()
    for i, step in enumerate(dialog):
        if not isinstance(step, dict):
            return False, f"step {i}: deve essere un dict"
        var = step.get("var")
        if not isinstance(var, str) or not var:
            return False, f"step {i}: campo 'var' mancante o non stringa"
        if not _VAR_NAME_RE.match(var):
            return False, (f"step {i}: 'var'={var!r} non e' snake_case "
                            "(usa solo lettere, numeri, underscore; deve "
                            "iniziare con lettera o '_')")
        if var in seen_vars:
            return False, f"step {i}: 'var'={var!r} duplicate (gia' definita)"
        seen_vars.add(var)
        prompt = step.get("prompt")
        if not isinstance(prompt, str) or not prompt:
            return False, f"step {i} ({var}): 'prompt' mancante o non stringa"
        if len(prompt) > MAX_PROMPT_LEN:
            return False, (f"step {i} ({var}): 'prompt' troppo lungo "
                            f"({len(prompt)} char, max {MAX_PROMPT_LEN})")
        schema = step.get("schema")
        if not isinstance(schema, dict):
            return False, f"step {i} ({var}): 'schema' mancante o non dict"
        kind = schema.get("kind")
        if kind not in VALID_KINDS:
            return False, (f"step {i} ({var}): 'schema.kind'={kind!r} "
                            f"non valido. Ammessi: {', '.join(VALID_KINDS)}")
        # choice / multi_choice richiedono `choices` esplicite OPPURE
        # derivazione da entries (ADR 0127 propose-and-fire): se lo step
        # ha `display_template` (o flag `from_entries=true`), `choices`
        # verra' popolato a runtime in `_derive_choices_from_entries`.
        if kind in ("choice", "multi_choice"):
            has_template = isinstance(schema.get("display_template"), str) \
                and schema.get("display_template")
            from_entries = bool(schema.get("from_entries"))
            choices = schema.get("choices")
            choices_explicit = isinstance(choices, list) and len(choices) >= 1
            if not (choices_explicit or has_template or from_entries):
                return False, (f"step {i} ({var}): kind={kind!r} richiede "
                                "'choices' (lista esplicita >=2) OPPURE "
                                "'display_template' (derivazione da entries "
                                "passate via from_step a livello top di args)")
            if choices_explicit and len(choices) < 2 \
                    and not (has_template or from_entries):
                return False, (f"step {i} ({var}): kind={kind!r} 'choices' "
                                "esplicite richiedono >=2 elementi")
            if "value_field" in schema and not isinstance(
                    schema.get("value_field"), str):
                return False, (f"step {i} ({var}): 'value_field' deve essere "
                                "stringa (nome campo dell'entry)")
            if has_template:
                tpl = schema["display_template"]
                if len(tpl) > 400:
                    return False, (f"step {i} ({var}): 'display_template' "
                                    f"troppo lungo ({len(tpl)} char, max 400)")
        # choice_with_preview (PR5): options con value+label+preview path.
        # Path validato lato callers e re-validato lato server preview
        # endpoint (defense in depth, anti path-traversal).
        if kind == "choice_with_preview":
            err_pv = _validate_options_with_preview(schema.get("options"))
            if err_pv is not None:
                return False, f"step {i} ({var}): {err_pv}"
    return True, None


def _format_template_safe(template: str, entry: dict) -> str:
    """Applica `template.format(**entry)` in modo difensivo (§7.9).

    DEVI: passare placeholder che corrispondono a campi del dict entry.
    NON DEVI: usare conversioni complesse — solo substitution puro.
    OK: '{start} - {end}' su entry {start, end, duration_min, ...}.
    ERRORE: '{path[0]}' (indici complessi non supportati: fallback al
    raw string entry).

    Campi mancanti producono `<missing:campo>` per non rompere il
    flow ma rendere visibile l'errore. Niente eccezioni.
    """
    try:
        return template.format(**entry)
    except (KeyError, IndexError) as ex:
        return f"{template} <missing:{ex}>"
    except (ValueError, TypeError):
        # Format spec invalido o type non format-able: fallback raw.
        return json.dumps(entry, ensure_ascii=False, default=str)[:200]


def _derive_choices_from_entries(dialog: list, entries: list) -> list:
    """Per ogni step `choice`/`multi_choice` con `display_template` o
    `from_entries=true` e senza `choices` esplicite, popola `choices` a
    partire dalle entries.

    `value_field` (opzionale) estrae il valore da ogni entry; default
    JSON-serializza l'entry intera (fallback robusto per record con
    schema non noto).

    Ritorna una NUOVA lista dialog (immutabilita' del parametro PLANNER).

    Determinismo §7.9: solo string formatting, no LLM.
    """
    if not isinstance(entries, list) or len(entries) == 0:
        return list(dialog)
    out_dialog = []
    for step in dialog:
        if not isinstance(step, dict):
            out_dialog.append(step)
            continue
        schema = step.get("schema") or {}
        kind = schema.get("kind")
        if kind not in ("choice", "multi_choice"):
            out_dialog.append(step)
            continue
        existing = schema.get("choices")
        if isinstance(existing, list) and len(existing) >= 2:
            # Esplicite: priorita' sopra entries-derivation.
            out_dialog.append(step)
            continue
        tpl = schema.get("display_template")
        from_entries = bool(schema.get("from_entries"))
        if not (tpl or from_entries):
            out_dialog.append(step)
            continue
        value_field = schema.get("value_field")
        derived = []
        for ent in entries:
            if not isinstance(ent, dict):
                # Entry scalare: usa la stringa come label e value.
                lab = str(ent)
                derived.append({"label": lab, "value": lab})
                continue
            if tpl:
                label = _format_template_safe(tpl, ent)
            else:
                # from_entries=true ma senza template: fallback a
                # JSON compatto come label (rimane azione utile).
                label = json.dumps(ent, ensure_ascii=False, default=str)[:200]
            if value_field and value_field in ent:
                val = ent[value_field]
            else:
                # Nessun value_field: usiamo l'entry serializzata come
                # value (preserva tutti i campi per lo step successivo).
                val = json.dumps(ent, ensure_ascii=False, default=str)
            derived.append({"label": label, "value": val})
        new_schema = dict(schema)
        new_schema["choices"] = derived
        # Sentinel per i layer downstream (canale Telegram callback_data
        # usa indice — non serve modifica li').
        new_schema["_derived_from_entries"] = True
        new_step = dict(step)
        new_step["schema"] = new_schema
        out_dialog.append(new_step)
    return out_dialog


def _validate_options_with_preview(options) -> str | None:
    """Valida la lista `options` di un kind=choice_with_preview.

    Ogni option deve essere un dict con `value` (string|int) + `label`
    (string non vuota) + `preview_image_path` (string non vuota). Path
    parsabile come `<path>` o `<path>#bbox=x,y,w,h`. Non risolviamo
    qui l'esistenza del file: il caller (callers persons_*) costruisce
    da dati persistenti del registry, e il preview endpoint HTTP
    ricontrolla a runtime quando lo serve.

    Ritorna None se ok, stringa di errore altrimenti.
    """
    if not isinstance(options, list) or len(options) < 2:
        return ("kind=choice_with_preview richiede 'options' lista con "
                ">=2 elementi")
    if len(options) > 50:
        return f"kind=choice_with_preview: troppe opzioni ({len(options)}, max 50)"
    seen_values: set[str] = set()
    for j, opt in enumerate(options):
        if not isinstance(opt, dict):
            return f"option {j}: deve essere un dict"
        val = opt.get("value")
        if not isinstance(val, (str, int)):
            return f"option {j}: 'value' string|int richiesto"
        sval = str(val)
        if sval in seen_values:
            return f"option {j}: 'value'={sval!r} duplicato"
        seen_values.add(sval)
        label = opt.get("label")
        if not isinstance(label, str) or not label:
            return f"option {j}: 'label' string non vuota richiesta"
        pv = opt.get("preview_image_path")
        if not isinstance(pv, str) or not pv:
            return (f"option {j}: 'preview_image_path' string non vuota "
                    "richiesta (path assoluto, opzionale '#bbox=x,y,w,h')")
        # Check shape parseable. Safety/exists check rimandato al server.
        try:
            from pathlib import Path as _P
            import sys as _s
            _s.path.insert(0, str(_P(__file__).resolve().parent.parent.parent / "runtime"))
            import dialog_preview as _dp
            _dp.parse_preview_path(pv)
        except (ImportError, ValueError) as ex:
            return f"option {j}: preview_image_path non valido: {ex}"
    return None


def _decide_fmt(fmt_arg: str, n_steps: int, channel: str | None,
                 dialog: list | None = None) -> str:
    """Risolve `fmt='auto'` secondo canale, numero step e shape del dialog.

    Pattern:
    - HTTP + ≥2 step → `form` (form HTML standalone con widget nativi).
    - Telegram + tutti kind in {yes_no, choice, multi_choice} → `telegram_inline`
      (inline keyboard nativa, no browser, no context-switch).
    - Altrimenti → `dialogue` (sequenza messaggi, universale).

    `dialog` puo' essere None: in quel caso skippiamo il check telegram_inline
    e cadiamo su dialogue.
    """
    if fmt_arg in ("dialogue", "form", "voice", "telegram_inline"):
        # voice non implementato: stub → degrada a dialogue.
        return "dialogue" if fmt_arg == "voice" else fmt_arg
    # auto
    if channel == "http" and n_steps >= 2:
        return "form"
    if channel == "telegram" and dialog and _all_inline_compatible(dialog):
        return "telegram_inline"
    return "dialogue"


# Compatibilita' inline keyboard: fonte unica `channels.inline_ui`
# (condivisa con l'orchestratore runtime e il daemon Telegram). Kind
# ammessi: yes_no, choice, choice_with_preview; multi_choice escluso
# (toggle ✓ via editMessageReplyMarkup = scope 2×, si rilascia su use
# case reale); cap alternative per step = INLINE_MAX_CHOICES.
from channels.inline_ui import all_inline_compatible as _all_inline_compatible  # noqa: E402


def _build_final_message_hint(state: dict, fmt: str) -> str:
    """Genera il testo da mostrare all'utente per il primo step.

    - dialogue:        prompt sequenziale (vedere step 1 di N).
    - form:            URL del form HTTP standalone.
    - telegram_inline: prompt step 1/N (la inline keyboard e' allegata
                       dal daemon Telegram via reply_markup, non e' nel
                       testo). Stesso scheletro di dialogue.
    - voice:           placeholder finche' il canale voice non e' wired.
    """
    title = state.get("title") or "Domanda"
    dialog = state.get("dialog") or []
    n = len(dialog)
    dialog_id = state.get("dialog_id") or ""
    if fmt == "form":
        host = os.environ.get("METNOS_HTTP_HOST", "127.0.0.1")
        port = os.environ.get("METNOS_HTTP_PORT", "8770")
        url = f"http://{host}:{port}/agent/dialog/{dialog_id}/form"
        descr = state.get("description") or ""
        lines = [title]
        if descr:
            lines.append(descr)
        lines.append("")
        lines.append(f"Apri il form: {url}")
        lines.append(f"({n} campi da compilare; rispondi `annulla` per abortire.)")
        return "\n".join(lines)
    # dialogue + telegram_inline (stesso testo; il daemon TG aggancia keyboard)
    first = dialog[0]
    prompt = first.get("prompt") or "?"
    descr = state.get("description") or ""
    schema_first = first.get("schema") or {}
    kind_first = schema_first.get("kind")
    lines = [title]
    if descr:
        lines.append(descr)
    lines.append("")
    lines.append(f"Step 1/{n} — {prompt}")
    # Per kind=choice/multi_choice in fmt=dialogue (no inline keyboard),
    # ENUMERA le opzioni numerate cosi' l'utente sa cosa rispondere.
    # Bug live turn 518878ff (12/5/2026): prompt mostrava «Scegli uno degli
    # orari» ma le 3 opzioni non erano visibili → utente disorientato.
    if kind_first in ("choice", "multi_choice"):
        choices = schema_first.get("choices") or []
        if choices:
            for idx, ch in enumerate(choices, start=1):
                if isinstance(ch, dict):
                    label = ch.get("label") or ch.get("value") or str(ch)
                else:
                    label = str(ch)
                lines.append(f"  {idx}) {label}")
            if kind_first == "choice":
                lines.append("")
                lines.append("Rispondi con il numero (1, 2, ...) della tua scelta.")
            else:
                lines.append("")
                lines.append("Rispondi con i numeri separati da virgola (es. 1,3).")
            lines.append("`annulla` per abortire.")
            return "\n".join(lines)
    if kind_first == "credentials":
        lines.append("(la risposta sara' mascherata in registro)")
    lines.append("")
    lines.append("Rispondi nel prossimo messaggio. `annulla` per abortire.")
    return "\n".join(lines)


def invoke(args: dict) -> dict:
    """Entrypoint chiamato dal runtime.

    Comportamento del PRIMO turno (creazione dialogo):
      - valida args
      - genera dialog_id
      - persiste lo stato in dialog_pending
      - ritorna `{decision: "input_required", dialog_id, final_message_hint}`

    Lookup di dialogo esistente (cap-pending retrieval pattern):
      - se viene passato `dialog_id` esistente E lo stato e' completed,
        ritorna `{decision: "completed", values: {...}}` cosi' il PLANNER
        puo' procedere col turno successivo.
      - se cancelled: `{decision: "cancelled"}`.
      - se ancora pending: `{decision: "input_required", ...}`.
    """
    # Import lazy: dialog_pending vive in runtime/, riferito via PYTHONPATH.
    try:
        import dialog_pending as _dp
    except ImportError as ex:
        return {"ok": False, "error": f"dialog_pending non disponibile: {ex}"}

    # Lookup di dialogo esistente (TASK 4 / pattern A: PLANNER ri-chiama
    # get_inputs con dialog_id per recuperare i values raccolti).
    explicit_dialog_id = args.get("dialog_id")
    actor = args.get("actor") or os.environ.get("METNOS_ACTOR") or "host"
    channel = args.get("channel") or os.environ.get("METNOS_CHANNEL") or ""
    sender_id = _safe_sender(actor, channel)

    if explicit_dialog_id:
        existing = _dp.load_pending(sender_id, explicit_dialog_id)
        if existing is None:
            return {
                "ok": False,
                "error": "dialog_not_found",
                "dialog_id": explicit_dialog_id,
                "actor": actor, "channel": channel,
            }
        if existing.get("cancelled"):
            return {
                "ok": True,
                "decision": "cancelled",
                "dialog_id": explicit_dialog_id,
                "values": existing.get("values_collected") or {},
            }
        if existing.get("completed"):
            return {
                "ok": True,
                "decision": "completed",
                "dialog_id": explicit_dialog_id,
                "values": existing.get("values_collected") or {},
                "step_index": existing.get("step_index", 0),
                "step_total": len(existing.get("dialog") or []),
            }
        # Ancora in attesa: ritorna lo stato corrente, niente UX prompt
        # (il channel daemon e' gia' a chiedere).
        return {
            "ok": True,
            "decision": "input_required",
            "dialog_id": explicit_dialog_id,
            "step_index": existing.get("step_index", 0),
            "step_total": len(existing.get("dialog") or []),
            "values": existing.get("values_collected") or {},
        }

    # Creazione di un nuovo dialogo: valida tutto.
    title = args.get("title")
    if not isinstance(title, str) or not title.strip():
        return {"ok": False, "error": _msg("ERR_ARG_MISSING", arg="title")}
    if len(title) > 80:
        return {"ok": False, "error": _msg("ERR_TITLE_TOO_LONG", max=80)}

    description = args.get("description")
    if description is not None and not isinstance(description, str):
        return {"ok": False, "error": _msg("ERR_ARG_NOT_STRING", arg="description")}

    dialog = args.get("dialog")
    # §2.4 robustezza NL→determinismo: il proposer emette talvolta un SINGOLO
    # step dict invece della lista di uno → wrap deterministico in [dialog].
    # (Una stringa NON è uno step valido: resta errore onesto via _validate.)
    if isinstance(dialog, dict):
        dialog = [dialog]
    # ADR 0127 + 15/5/2026: auto-inject `from_entries=true` su step
    # `kind=choice`/`multi_choice` SE `from_step` top-level presente E
    # nessun campo (choices/display_template/from_entries) e' specificato.
    # Bug live (turn 0a4b6a59): LLM emette `schema={kind:'choice'}` senza
    # null'altro → validation fallisce. Fallback robusto: `from_entries`
    # con JSON compatto come label (vedi `_derive_choices_from_entries`).
    if isinstance(dialog, list) and (
            args.get("from_step") is not None
            or isinstance(args.get("entries"), list)):
        for _step in dialog:
            if not isinstance(_step, dict):
                continue
            _sch = _step.get("schema")
            if not isinstance(_sch, dict):
                continue
            if _sch.get("kind") not in ("choice", "multi_choice"):
                continue
            _has_choices = isinstance(_sch.get("choices"), list) \
                and len(_sch["choices"]) >= 1
            _has_template = isinstance(_sch.get("display_template"), str) \
                and _sch["display_template"]
            _has_from_entries = bool(_sch.get("from_entries"))
            if not (_has_choices or _has_template or _has_from_entries):
                _sch["from_entries"] = True

    ok, err = _validate_dialog(dialog)
    if not ok:
        # error_class strutturato (§7.3): un dialog malformato dal planner è
        # un arg invalido RECUPERABILE → l'engine fa recovery/re-propose, non
        # un dead-end con stringa grezza in faccia all'utente.
        return {"ok": False, "error": _msg("ERR_DIALOG_INVALID", detail=err),
                "error_class": "invalid_args"}

    # Pattern propose-and-fire (ADR 0127): se l'arg `entries` e' presente
    # (popolato a runtime quando il PLANNER chiama get_inputs con
    # `from_step: N` top-level), deriva `choices` per gli step
    # `choice`/`multi_choice` con `display_template`/`from_entries`.
    # Determinismo §7.9.
    entries_for_choices = args.get("entries")

    # 15/5/2026: auto-infer `display_template` se il LLM non l'ha fornito
    # ma le entries hanno campi noti (when_human, name, subject, path,
    # title). Migliora UX: invece di JSON raw come label, mostra "lun 18
    # mag, 09:00-10:00". Determinismo §7.9, lookup table cross-domain.
    if isinstance(entries_for_choices, list) and entries_for_choices:
        _first = entries_for_choices[0] if isinstance(
            entries_for_choices[0], dict) else None
        if _first:
            _LABEL_HEURISTIC = (
                ("when_human", "{when_human}"),
                ("subject", "{subject}"),
                ("name", "{name}"),
                ("title", "{title}"),
                ("path", "{path}"),
                ("start_human", "{start_human} → {end_human}"),
                ("start", "{start} → {end}"),
            )
            _VALUE_HEURISTIC = ("id", "start", "path", "url", "value")
            _inferred_tpl = next(
                (t for f, t in _LABEL_HEURISTIC if f in _first), None)
            _inferred_vf = next(
                (f for f in _VALUE_HEURISTIC if f in _first), None)
            if _inferred_tpl:
                for _step in dialog:
                    if not isinstance(_step, dict):
                        continue
                    _sch = _step.get("schema")
                    if not isinstance(_sch, dict):
                        continue
                    if _sch.get("kind") not in ("choice", "multi_choice"):
                        continue
                    if _sch.get("display_template") or _sch.get("choices"):
                        continue
                    _sch["display_template"] = _inferred_tpl
                    if _inferred_vf and not _sch.get("value_field"):
                        _sch["value_field"] = _inferred_vf
    needs_derivation = any(
        (s.get("schema") or {}).get("kind") in ("choice", "multi_choice")
        and (
            (s.get("schema") or {}).get("display_template")
            or (s.get("schema") or {}).get("from_entries")
        )
        and not (s.get("schema") or {}).get("choices")
        for s in dialog
    )
    if needs_derivation:
        if not isinstance(entries_for_choices, list):
            return {
                "ok": False,
                "error": (
                    "step kind=choice/multi_choice con display_template "
                    "richiede `from_step=N` top-level di args (il runtime "
                    "espande from_step in entries). Nessuna `entries` ricevuta."
                ),
                "error_class": "invalid_args",
            }
        if len(entries_for_choices) == 0:
            return {
                "ok": False,
                "error": (
                    "step kind=choice/multi_choice derivato da `entries` "
                    "VUOTE — niente scelte disponibili. Verifica che lo step "
                    "from_step abbia prodotto >=1 entry, o passa choices "
                    "esplicite."
                ),
                "error_class": "invalid_args",
            }
        dialog = _derive_choices_from_entries(dialog, entries_for_choices)

    fmt_arg = args.get("fmt") or "auto"
    if fmt_arg not in ("auto", "dialogue", "form", "voice"):
        return {"ok": False, "error": _msg("ERR_FMT_INVALID", value=repr(fmt_arg))}

    timeout_s = args.get("timeout_s")
    if timeout_s is not None:
        if not isinstance(timeout_s, int) or timeout_s < 1 or timeout_s > MAX_TIMEOUT_S:
            return {"ok": False,
                    "error": _msg("ERR_TIMEOUT_RANGE", max=MAX_TIMEOUT_S)}
    else:
        # Default per FORMA del dialogo (§7.3): 60s per i dialoghi semplici
        # (1 step si/no/scelta), 600s per form (>=2 step) e credenziali, cosi'
        # i dialoghi abbandonati si chiudono in fretta ma quelli da compilare
        # hanno tempo. Override esplicito via arg `timeout_s` resta sovrano.
        import dialog_pending as _dp_ttl
        timeout_s = _dp_ttl.default_timeout_for(dialog)

    fmt = _decide_fmt(fmt_arg, len(dialog), channel, dialog)

    dialog_id = uuid.uuid4().hex[:16]
    state = {
        "dialog_id": dialog_id,
        "title": title,
        "description": description,
        "dialog": dialog,
        "fmt": fmt,
        "fmt_arg": fmt_arg,        # originale (per debugging)
        "values_collected": {},
        "step_index": 0,
        "started_at": _utc_now_iso(),
        "actor": actor,
        "channel": channel,
        "timeout_s": timeout_s,
        "completed": False,
        "cancelled": False,
    }
    try:
        _dp.save_pending(sender_id, dialog_id, state)
    except (OSError, ValueError, TypeError) as ex:
        return {"ok": False, "error": f"save_pending fallito: {ex}"}

    final_message_hint = _build_final_message_hint(state, fmt)
    return {
        "ok": True,
        "decision": "input_required",
        "dialog_id": dialog_id,
        "step_index": 0,
        "step_total": len(dialog),
        "values": {},
        "fmt": fmt,
        "final_message_hint": final_message_hint,
        # Cap-pending pattern: il channel daemon riconosce questo kind e
        # consuma le risposte utente avanzando lo stato del dialogo
        # senza coinvolgere il PLANNER finche' non e' completato.
        "expandable_caps": [{
            "kind": "get_inputs_response",
            "dialog_id": dialog_id,
            "step_total": len(dialog),
            "fmt": fmt,
        }],
        "metadata": {
            "title": title,
            "n_steps": len(dialog),
            "fmt": fmt,
            "actor": actor,
            "channel": channel,
        },
    }


def main():
    raw = sys.stdin.read()
    try:
        args = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({"ok": False, "error": _msg("ERR_JSON_INVALID")}))
        return
    result = invoke(args)
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
