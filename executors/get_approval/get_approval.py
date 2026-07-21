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
non sceglie). Deterministico (CLAUDE.md §7.9): zero LLM nel critical path.
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


def _failure(error_code: str, error: str,
             *, error_class: str = "invalid_input") -> dict:
    return {
        "ok": False,
        "error": error,
        "error_class": error_class,
        "error_code": error_code,
    }


def _validate_branch(branch, name: str) -> tuple[str, str] | None:
    """Un branch (`on_approve`/`on_reject`) deve essere {tool: str, args: dict?}."""
    if not isinstance(branch, dict):
        return _msg("ERR_ARG_NOT_DICT", arg=name), f"{name}_not_object"
    tool = branch.get("tool") or branch.get("executor")
    if not isinstance(tool, str) or not tool:
        return (_msg("ERR_ARG_NOT_NONEMPTY_STRING", arg=f"{name}.tool"),
                f"{name}_tool_invalid")
    args = branch.get("args")
    if args is not None and not isinstance(args, dict):
        return (_msg("ERR_ARG_NOT_DICT", arg=f"{name}.args"),
                f"{name}_args_not_object")
    return None


def invoke(args: dict) -> dict:
    if not isinstance(args, dict):
        return _failure("args_not_object", _msg("ERR_ARGS_NOT_OBJECT"))

    # gate-resume re-run (20/6/2026): l'utente ha GIA' approvato in un turno
    # precedente; questa e' la RIPRESA della pipeline col gate auto-passato
    # (engine inietta `_pre_approved` quando runtime_ctx._gate_approved). Passa
    # trasparente — ok senza nuovo dialog — cosi' gli step a valle (send/write)
    # proseguono. Deterministico §7.9.
    if args.get("_pre_approved") is True:
        return {"ok": True, "decision": "approved", "final_message_hint": ""}

    # §2.11/§2.8 (gate-vuoto 22/6 + soglia mutazioni-di-massa 6/7): `guard_count`
    # (iniettato dal gate runtime, risolto da ${stepN.@count}) ≤ `guard_threshold`
    # → passa TRASPARENTE senza dialog. threshold=0 (default) = comportamento
    # storico del consent-gate outbound (passa solo su 0 elementi: niente «approvo
    # 0?»). threshold=N>0 = gate mutazioni-di-massa: chiede SOLO oltre N item (una
    # delete/move di pochi file non disturba). Non-numerico/non risolto → gate
    # normale (fail-safe: nel dubbio chiedi).
    gc = args.get("guard_count")
    if gc is not None:
        try:
            threshold = int(args.get("guard_threshold") or 0)
            if int(gc) <= threshold:
                return {"ok": True, "decision": "approved",
                        "final_message_hint": ""}
        except (TypeError, ValueError):
            pass

    prompt = args.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return _failure(
            "prompt_missing", _msg("ERR_ARG_MISSING", arg="prompt"))
    # Prompt troppo lungo → TRONCA invece di fallire (§2.8 degrado onesto): il
    # prompt puo' inglobare un riassunto data-driven (es. ${stepN.@brief} con
    # i titoli delle issue) la cui lunghezza non e' nota a monte; un hard-fail
    # romperebbe l'intero gate. Tronca con ellissi, preservando la domanda.
    if len(prompt) > MAX_PROMPT_LEN:
        prompt = prompt[:MAX_PROMPT_LEN - 1].rstrip() + "…"

    on_approve = args.get("on_approve")
    err = _validate_branch(on_approve, "on_approve")
    if err:
        return _failure(err[1], err[0])

    on_reject = args.get("on_reject")
    if on_reject is not None:
        err = _validate_branch(on_reject, "on_reject")
        if err:
            return _failure(err[1], err[0])

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
    except ImportError:
        return _failure(
            "dialog_dependency_missing",
            _msg("ERR_DEPENDENCY_MISSING", what="dialog_pending"),
            error_class="dependency_unavailable",
        )

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
    elif channel == "http":
        # Web (bug live 3db55063, 6/7): 'dialogue' lasciava l'utente SENZA UI
        # di risposta (doveva indovinare e digitare). 'form' → il hint porta il
        # marker INLINE_FORM e chat.html monta l'iframe del form (bottoni).
        fmt = "form"
    else:
        fmt = "dialogue"

    timeout_s = args.get("timeout_s")
    if timeout_s is not None and (not isinstance(timeout_s, int)
                                  or isinstance(timeout_s, bool)
                                  or timeout_s < 1 or timeout_s > MAX_TIMEOUT_S):
        return _failure(
            "timeout_invalid", _msg("ERR_TIMEOUT_RANGE", max=MAX_TIMEOUT_S))
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
    except (OSError, ValueError, TypeError):
        return _failure(
            "dialog_save_failed",
            _msg("ERR_OP_FAILED", reason="dialog state"),
            error_class="io_error",
        )

    # fmt=form (web): il marker INLINE_FORM viene sostituito da chat.html con
    # l'iframe di /agent/dialog/<id>/form (stesso contratto di get_inputs).
    hint = prompt
    if fmt == "form":
        hint = f"{prompt}\n\nINLINE_FORM:/agent/dialog/{dialog_id}/form"

    return {
        "ok": True,
        "decision": "input_required",
        "dialog_id": dialog_id,
        "step_index": 0,
        "step_total": 1,
        "values": {},
        "fmt": fmt,
        "final_message_hint": hint,
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
