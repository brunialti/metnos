#!/usr/bin/env python3
"""
undo_last_turn — executor di Metnos v1.1.

Annulla TUTTE le operazioni revertibili dell'ultimo turno utente che le
contiene. Best-effort: se in quel turno c'erano operazioni irreversibili
o esterne (mail send, ecc.), le altre vengono annullate comunque e
l'output segnala quali non sono state ribaltate.

Implementazione:
  1. Apre il log undo (~/.local/share/metnos/undo.jsonl).
  2. Estrae i record `done` non `undone` del turno piu' recente.
  3. In ordine inverso di esecuzione, importa il modulo Python
     dell'executor relativo e chiama la sua `reverse(plan, results)`.
  4. Append `undone` per ogni op ribaltata.
  5. Ritorna sommario.

Idempotenza: una seconda invocazione consecutiva non trova `done`
non-undone in quel turno → no-op con messaggio.
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

# Permette di importare runtime/undo.py, runtime/loader.py, runtime/reverse_patterns.py
sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from messages import get as _msg  # noqa: E402
from executor_helpers import run_stdio  # noqa: E402
from loader import load_catalog
from reverse_patterns import apply_patterns
from undo import UndoLog


def _load_module(code_path: Path):
    spec = importlib.util.spec_from_file_location("_undoable_executor", str(code_path))
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:
        return None
    return mod


def invoke(args):
    log_path_arg = args.get("log_path")
    log = UndoLog(Path(log_path_arg)) if log_path_arg else UndoLog()
    records = log.latest_turn_done()
    if not records:
        return {
            "ok": True,
            "undone_count": 0,
            "skipped_count": 0,
            "message": _msg("ERR_NO_UNDOABLE"),
            "details": [],
        }

    catalog = load_catalog()
    details = []
    undone = 0
    skipped = 0

    for rec in reversed(records):
        executor_name = rec["executor"]
        ex = catalog.get(executor_name)
        if ex is None:
            details.append({"op_id": rec["op_id"], "executor": executor_name, "status": "skipped", "reason": _msg("ERR_UNDO_NOT_IN_CATALOG")})
            skipped += 1
            continue
        if not ex.revertible:
            details.append({"op_id": rec["op_id"], "executor": executor_name, "status": "skipped", "reason": _msg("ERR_UNDO_NOT_REVERTIBLE")})
            skipped += 1
            continue
        rev_result = None
        # priority 1: catalogo deterministico (manifest.reverse_pattern)
        if ex.reverse_pattern:
            try:
                rev_result = apply_patterns(ex.reverse_pattern, rec.get("plan") or {}, rec.get("results") or {})
            except Exception as e:
                details.append({"op_id": rec["op_id"], "executor": executor_name, "status": "error", "reason": f"reverse_pattern exception: {e}"})
                skipped += 1
                continue
        else:
            # priority 2: fallback a reverse() custom del modulo (back-compat)
            mod = _load_module(ex.code_path)
            if mod is None or not hasattr(mod, "reverse"):
                details.append({"op_id": rec["op_id"], "executor": executor_name, "status": "skipped", "reason": _msg("ERR_UNDO_NO_REVERSE")})
                skipped += 1
                continue
            try:
                rev_result = mod.reverse(rec.get("plan") or {}, rec.get("results") or {})
            except Exception as e:
                details.append({"op_id": rec["op_id"], "executor": executor_name, "status": "error", "reason": f"reverse() exception: {e}"})
                skipped += 1
                continue
        # Status onesto: "undone" solo se il reverse ha effettivamente ribaltato
        # almeno qualcosa SENZA fallimenti; "partial" se mix; "failed" se zero
        # ribaltati ma alcuni falliti (es. mail destinazione gia' inesistente).
        # No "undone" generico mai con ok_count=0: viola feedback_no_silent_failure.
        rok = rev_result.get("ok_count") or 0
        rfail = rev_result.get("fail_count") or 0
        # IMPORTANTE: append_undone SOLO se il reverse ha SUCCESSO o partial.
        # Se completamente fallito, lasciamo l'op come "done not undone" per
        # poterla ritentare quando il bug viene fixato (caso live 29/4/2026:
        # schema mismatch ha marcato un move come "undone" silently impedendo
        # retry quando il fix arrivava).
        is_actually_undone = (rok > 0)  # almeno qualcosa ribaltato
        if is_actually_undone:
            try:
                log.append_undone(rec["op_id"], rev_result)
            except Exception as e:
                details.append({"op_id": rec["op_id"], "executor": executor_name, "status": "error", "reason": f"log undone failed: {e}", "reverse_result": rev_result})
                skipped += 1
                continue
        if rok > 0 and rfail == 0:
            ustatus = "undone"
        elif rok > 0 and rfail > 0:
            ustatus = "partial"
        elif rok == 0 and rfail > 0:
            ustatus = "failed"
        else:
            ustatus = "no_op"  # nessun ribaltamento eseguito (lista vuota?)
        details.append({
            "op_id": rec["op_id"],
            "executor": executor_name,
            "status": ustatus,
            "ok_count": rok,
            "fail_count": rfail,
            "reverse_error": rev_result.get("error") if rfail > 0 else None,
        })
        if ustatus == "undone":
            undone += 1
        else:
            # Non incrementiamo undone se il reverse non ha ribaltato veramente.
            skipped += 1

    return {
        "ok": skipped == 0,
        "undone_count": undone,
        "skipped_count": skipped,
        "details": details,
        "turn_id": records[0].get("turn_id") if records else None,
    }


def main():
    run_stdio(invoke)


if __name__ == "__main__":
    main()
