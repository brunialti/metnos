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
la palla e dialoga con l'utente. Codice deterministico (CLAUDE.md §7.9):
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
        # choice / multi_choice richiedono `choices`
        if kind in ("choice", "multi_choice"):
            choices = schema.get("choices")
            if not isinstance(choices, list) or len(choices) < 2:
                return False, (f"step {i} ({var}): kind={kind!r} richiede "
                                "'choices' lista con >=2 elementi")
        # choice_with_preview (PR5): options con value+label+preview path.
        # Path validato lato callers e re-validato lato server preview
        # endpoint (defense in depth, anti path-traversal).
        if kind == "choice_with_preview":
            err_pv = _validate_options_with_preview(schema.get("options"))
            if err_pv is not None:
                return False, f"step {i} ({var}): {err_pv}"
    return True, None


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


# multi_choice escluso da MVP inline keyboard: richiede toggle ✓ con
# editMessageReplyMarkup ad ogni click + button "Conferma" finale, scope
# 2× rispetto a yes_no/choice. Se qualcuno lo chiede via use case reale
# si rilascia dopo. yes_no e choice coprono >90% dei casi (cap-expand,
# admin approval, scelta dst_folder).
_INLINE_COMPATIBLE_KINDS = frozenset({"yes_no", "choice", "choice_with_preview"})


def _all_inline_compatible(dialog: list) -> bool:
    """True se tutti gli step del dialog hanno `schema.kind` che si rende
    naturalmente come InlineKeyboardButton di Telegram. Caso misto (es.
    yes_no + text) → False: il text richiede sequenza dialogue, quindi
    tanto vale fare tutto in dialogue per coerenza UX."""
    for s in dialog:
        kind = (s.get("schema") or {}).get("kind")
        if kind not in _INLINE_COMPATIBLE_KINDS:
            return False
    return True


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
    lines = [title]
    if descr:
        lines.append(descr)
    lines.append("")
    lines.append(f"Step 1/{n} — {prompt}")
    if first.get("schema", {}).get("kind") == "credentials":
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
        return {"ok": False, "error": "missing required arg 'title'"}
    if len(title) > 80:
        return {"ok": False, "error": "title troppo lungo (max 80 char)"}

    description = args.get("description")
    if description is not None and not isinstance(description, str):
        return {"ok": False, "error": "'description' deve essere stringa"}

    dialog = args.get("dialog")
    ok, err = _validate_dialog(dialog)
    if not ok:
        return {"ok": False, "error": f"dialog non valido: {err}"}

    fmt_arg = args.get("fmt") or "auto"
    if fmt_arg not in ("auto", "dialogue", "form", "voice"):
        return {"ok": False, "error": f"fmt non valido: {fmt_arg!r}"}

    timeout_s = args.get("timeout_s")
    if timeout_s is not None:
        if not isinstance(timeout_s, int) or timeout_s < 1 or timeout_s > MAX_TIMEOUT_S:
            return {"ok": False,
                    "error": f"timeout_s deve essere int 1..{MAX_TIMEOUT_S}"}
    else:
        timeout_s = DEFAULT_TIMEOUT_S

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
        sys.stdout.write(json.dumps({"ok": False, "error": f"invalid input json: {e}"}))
        return
    result = invoke(args)
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
