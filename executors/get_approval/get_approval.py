#!/usr/bin/env python3
"""get_approval — gate di consenso umano general-purpose (cross-skill).

Presenta all'utente una scelta a 2 bottoni (default Approva/Disapprova;
Annulla automatico dal canale) e, alla risposta, fa eseguire al runtime
l'executor indicato: `on_approve` se approvato, `on_reject` se rifiutato,
niente se annullato.

Riusa la macchina di `get_inputs` (ADR 0090): salva un dialog `choice` in
`dialog_pending` con `on_complete={type:'gate_dispatch', ...}` e ritorna il
descrittore `decision='input_required'` + `expandable_caps`. Il channel
adapter (Telegram inline / HTTP / dialogue) presenta i bottoni; alla scelta
il runtime esegue il branch via `orchestration._process_gate_dispatch`.

Funziona INTERATTIVO e SCHEDULATO (il dialog resta pending finche' l'utente
non sceglie). Deterministico (the design guide §7.9): zero LLM nel critical path.
"""
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# L'executor gira come subprocess: il runtime aggiunge runtime/ al PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "runtime"))

from messages import get as _msg  # noqa: E402
from executor_helpers import run_stdio  # noqa: E402

MAX_PROMPT_LEN = 200
MAX_TIMEOUT_S = 3600


def _safe_sender(actor: str, channel: str | None) -> str:
    if not actor:
        actor = "host"
    return f"{channel}:{actor}" if channel else actor


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


def _validate_branch(branch, name: str) -> str | None:
    """Un branch (`on_approve`/`on_reject`) deve essere {tool: str, args: dict?}."""
    if not isinstance(branch, dict):
        return _msg("ERR_ARG_NOT_DICT", arg=name)
    tool = branch.get("tool") or branch.get("executor")
    if not isinstance(tool, str) or not tool:
        return _msg("ERR_ARG_NOT_NONEMPTY_STRING", arg=f"{name}.tool")
    args = branch.get("args")
    if args is not None and not isinstance(args, dict):
        return _msg("ERR_ARG_NOT_DICT", arg=f"{name}.args")
    return None


def invoke(args: dict) -> dict:
    # gate-resume re-run (20/6/2026): l'utente ha GIA' approvato in un turno
    # precedente; questa e' la RIPRESA della pipeline col gate auto-passato
    # (engine inietta `_pre_approved` quando runtime_ctx._gate_approved). Passa
    # trasparente — ok senza nuovo dialog — cosi' gli step a valle (send/write)
    # proseguono. Deterministico §7.9.
    if args.get("_pre_approved"):
        return {"ok": True, "decision": "approved", "final_message_hint": ""}

    # §2.11/§2.8 (gate-vuoto, 22/6): un consenso su un outbound di 0 elementi NON
    # ha nulla da approvare. `guard_count` (iniettato dal consent-gate runtime,
    # risolto da ${stepN.@count}) == 0 → passa TRASPARENTE senza dialog: l'utente
    # non viene disturbato con «approvo 0 elementi?», e il send a valle resta un
    # no-op onesto. Non-numerico / non risolto → gate normale.
    gc = args.get("guard_count")
    if gc is not None:
        try:
            if int(gc) == 0:
                return {"ok": True, "decision": "approved",
                        "final_message_hint": ""}
        except (TypeError, ValueError):
            pass

    prompt = args.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return {"ok": False, "error": _msg("ERR_ARG_MISSING", arg="prompt"),
                "error_class": "invalid_args"}
    # Prompt troppo lungo → TRONCA invece di fallire (§2.8 degrado onesto): il
    # prompt puo' inglobare un riassunto data-driven (es. ${stepN.@brief} con
    # i titoli delle issue) la cui lunghezza non e' nota a monte; un hard-fail
    # romperebbe l'intero gate. Tronca con ellissi, preservando la domanda.
    if len(prompt) > MAX_PROMPT_LEN:
        prompt = prompt[:MAX_PROMPT_LEN - 1].rstrip() + "…"

    on_approve = args.get("on_approve")
    err = _validate_branch(on_approve, "on_approve")
    if err:
        return {"ok": False, "error": err, "error_class": "invalid_args"}

    on_reject = args.get("on_reject")
    if on_reject is not None:
        err = _validate_branch(on_reject, "on_reject")
        if err:
            return {"ok": False, "error": err, "error_class": "invalid_args"}

    # Etichette bottoni: default i18n (MSG_BTN_*), override esplicito a 2 voci.
    options = args.get("options")
    if isinstance(options, list) and len(options) == 2 \
            and all(isinstance(o, str) and o for o in options):
        label_ok, label_no = options[0], options[1]
    else:
        label_ok, label_no = _msg("MSG_BTN_APPROVE"), _msg("MSG_BTN_REJECT")

    title = args.get("title")
    if not isinstance(title, str) or not title.strip():
        title = _msg("MSG_APPROVAL_TITLE")

    actor = args.get("actor") or os.environ.get("METNOS_ACTOR") or "host"
    channel = args.get("channel") or os.environ.get("METNOS_CHANNEL") or ""
    sender_id = _safe_sender(actor, channel)

    try:
        import dialog_pending as _dp
    except ImportError as ex:
        return {"ok": False, "error": f"dialog_pending non disponibile: {ex}"}

    # Dialog a 1 step `choice`: i value sono canonici (approve/reject), le label
    # sono user-facing. on_complete cabla il dispatch (deterministico).
    dialog = [{
        "var": "decision",
        "prompt": prompt,
        "schema": {"kind": "choice", "choices": [
            {"label": label_ok, "value": "approve"},
            {"label": label_no, "value": "reject"},
        ]},
    }]

    # fmt: riusa la fonte unica di compatibilita' inline (channels.inline_ui).
    try:
        from channels.inline_ui import all_inline_compatible as _inline_ok
    except ImportError:
        _inline_ok = lambda _d: False  # noqa: E731
    if channel == "telegram" and _inline_ok(dialog):
        fmt = "telegram_inline"
    else:
        fmt = "dialogue"

    timeout_s = args.get("timeout_s")
    if timeout_s is not None and (not isinstance(timeout_s, int)
                                  or timeout_s < 1 or timeout_s > MAX_TIMEOUT_S):
        return {"ok": False, "error": _msg("ERR_TIMEOUT_RANGE", max=MAX_TIMEOUT_S)}
    if timeout_s is None:
        timeout_s = _dp.default_timeout_for(dialog)

    dialog_id = uuid.uuid4().hex[:16]
    branches = {"type": "gate_dispatch", "approve_value": "approve",
                "on_approve": on_approve}
    if on_reject is not None:
        branches["on_reject"] = on_reject

    state = {
        "dialog_id": dialog_id,
        "title": title,
        "dialog": dialog,
        "fmt": fmt,
        "fmt_arg": "auto",
        "values_collected": {},
        "step_index": 0,
        "started_at": _utc_now_iso(),
        "actor": actor,
        "channel": channel,
        "timeout_s": timeout_s,
        "completed": False,
        "cancelled": False,
        "on_complete": branches,
    }
    try:
        _dp.save_pending(sender_id, dialog_id, state)
    except (OSError, ValueError, TypeError) as ex:
        return {"ok": False, "error": f"save_pending fallito: {ex}"}

    return {
        "ok": True,
        "decision": "input_required",
        "dialog_id": dialog_id,
        "step_index": 0,
        "step_total": 1,
        "values": {},
        "fmt": fmt,
        "final_message_hint": prompt,
        "expandable_caps": [{
            "kind": "get_inputs_response",
            "dialog_id": dialog_id,
            "step_total": 1,
            "fmt": fmt,
            # sender SOTTO CUI e' salvato il pending (es. "telegram:<actor>").
            # Il tap inline risolve il chat_id a un actor che puo' DIFFERIRE
            # (<chat_id>→host) → senza questo bridge il lookup multi-candidato
            # non trova lo stato e risponde «dialogo scaduto». `sender_for_state`
            # e' il PRIMO candidato in sender_state_candidates → match garantito.
            "sender_for_state": sender_id,
        }],
        "metadata": {"title": title, "n_steps": 1, "fmt": fmt,
                     "actor": actor, "channel": channel},
    }


def main():
    run_stdio(invoke, allow_empty=True)


if __name__ == "__main__":
    main()
