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
from reverse_patterns import PATTERNS, apply_patterns, build_remote_reverse_calls
from undo import UndoLog


def _reverse_on_device(patterns, rec) -> dict:
    """C7 CP4: ribalta sull O STESSO device (§2.9) un'op che ha girato lì.

    Traduce i pattern in chiamate executor (build_remote_reverse_calls) e le
    accoda al device via invocations, attesa SINCRONA bounded. Aggregato con
    la stessa shape di apply_patterns (ok/ok_count/fail_count/stages).
    Device offline/timeout → fallito ONESTO (l'op resta ritentabile)."""
    import time as _time
    import invocations as _inv
    device_id = rec.get("device") or ""
    built = build_remote_reverse_calls(
        patterns, rec.get("plan") or {}, rec.get("results") or {})
    stages, total_ok, total_fail = [], 0, 0
    overall_ok = True
    for pat in built["unsupported"]:
        stages.append({"pattern": pat, "result": {
            "ok": False, "ok_count": 0, "fail_count": 1,
            "error": _msg("ERR_UNDO_REMOTE_UNSUPPORTED", pattern=pat)}})
        total_fail += 1
        overall_ok = False
    for call in built["calls"]:
        # Deadline SCALATA con gli item (stessa politica A.0 di remote_exec,
        # 6/7): un batch-restore da 100 copie non sta nei 30s di default — il
        # job-object ucciderebbe il restore a metà (failure-mode 1ba8e2c4,
        # ma sull'UNDO). Attesa server allineata alla deadline device.
        try:
            from remote_exec import _scaled_timeout_s
            _scaled_s = _scaled_timeout_s(30, call["args"], "revertible")
        except Exception:
            _scaled_s = 30
        try:
            inv_id = _inv.enqueue_invocation(
                device_id, call["executor"], call["args"], scope="device",
                deadline_ms=_scaled_s * 1000)
        except Exception as e:
            stages.append({"pattern": call["executor"], "result": {
                "ok": False, "ok_count": 0, "fail_count": 1,
                "error": f"enqueue failed: {e}"}})
            total_fail += 1
            overall_ok = False
            continue
        deadline = _time.time() + max(
            float(os.environ.get("METNOS_UNDO_DEVICE_TIMEOUT_S", "25")),
            _scaled_s + 15)
        state, res = "", None
        while _time.time() < deadline:
            i = _inv.get_invocation(inv_id)
            state = (i or {}).get("state") or ""
            if state in ("done", "failed", "error", "denied", "expired"):
                res = (i or {}).get("result")
                break
            _time.sleep(0.4)
        if state == "done" and isinstance(res, dict) and res.get("ok"):
            # ok_count vive nel PAYLOAD del result wire (§2.6 round-trip):
            # leggerlo dal body dava 1-per-chiamata → l'undo batch di 455
            # file diceva «5 elementi» (le stage, 6/7). Fallback onesti:
            # n_processed (body wire) → len(results).
            _pl = res.get("payload") if isinstance(res.get("payload"), dict) else {}
            rok = _pl.get("ok_count")
            if not isinstance(rok, int):
                rok = res.get("ok_count") if isinstance(res.get("ok_count"), int) else None
            if not isinstance(rok, int):
                rok = res.get("n_processed") if isinstance(res.get("n_processed"), int) else None
            if not isinstance(rok, int):
                rok = max(1, len(_pl.get("results") or res.get("results") or []))
            stages.append({"pattern": call["executor"],
                           "result": {"ok": True, "ok_count": rok,
                                      "fail_count": res.get("fail_count") or 0,
                                      "device": device_id,
                                      "invocation_id": inv_id}})
            total_ok += rok
            total_fail += (_pl.get("fail_count") or res.get("fail_count") or 0)
        else:
            # §2.8: distingui «eseguito ma fallito» (state=done, ok:false) da
            # «mai arrivato» (timeout/error/denied) — e porta l'evidenza.
            _err = (res or {}).get("error") if isinstance(res, dict) else None
            stages.append({"pattern": call["executor"], "result": {
                "ok": False, "ok_count": 0, "fail_count": 1,
                "error": (_err or _msg("ERR_UNDO_DEVICE_UNREACHABLE",
                                       device=device_id,
                                       state=state or "timeout")),
                "state": state or "timeout",
                "device_result": (json.dumps(res, default=str)[:300]
                                   if res is not None else None),
                "invocation_id": inv_id}})
            total_fail += 1
            overall_ok = False
    return {"ok": overall_ok and total_fail == 0, "ok_count": total_ok,
            "fail_count": total_fail, "stages": stages}


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


def _unknown_patterns(patterns) -> list[str]:
    """Restituisce le etichette non appartenenti al catalogo chiuso."""
    names = [patterns] if isinstance(patterns, str) else patterns
    if not isinstance(names, list):
        return [str(patterns)]
    return [name for name in names if name not in PATTERNS]


def _effective_reverse_pattern(ex, rec):
    """Select the executed branch's pattern within the manifest ceiling."""
    declared = ex.reverse_pattern
    declared_names = ([declared] if isinstance(declared, str)
                      else list(declared or []))
    results = rec.get("results") or {}
    undo_meta = results.get("_undo") if isinstance(results, dict) else None
    selected = (undo_meta.get("reverse_pattern")
                if isinstance(undo_meta, dict) else None)
    if selected is None:
        return declared, None
    selected_names = ([selected] if isinstance(selected, str)
                      else list(selected) if isinstance(selected, list) else [])
    if (not selected_names
            or any(not isinstance(name, str) or name not in declared_names
                   for name in selected_names)):
        return None, "runtime reverse pattern is outside manifest ceiling"
    return selected, None


def _reverse_with_module(ex, rec):
    """Fallback custom per executor manuali con pattern legacy/assente."""
    mod = _load_module(ex.code_path)
    if mod is None or not hasattr(mod, "reverse"):
        return None, _msg("ERR_UNDO_NO_REVERSE")
    try:
        return mod.reverse(
            rec.get("plan") or {}, rec.get("results") or {}), None
    except Exception as exc:
        return None, f"reverse() exception: {exc}"


def invoke(args):
    log_path_arg = args.get("log_path")
    log = UndoLog(Path(log_path_arg)) if log_path_arg else UndoLog()
    # Isolamento multi-utente (7/7/2026): annulli SOLO le TUE operazioni.
    # `_actor` e' garantito al choke-point invoke_executor (copre anche il
    # fast-path «annulla», che non passa dall'injection dell'engine).
    records = log.latest_turn_done(actor=args.get("_actor") or "host")
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
        reverse_pattern, selection_error = _effective_reverse_pattern(ex, rec)
        if selection_error:
            details.append({"op_id": rec["op_id"], "executor": executor_name,
                            "status": "error", "reason": selection_error})
            skipped += 1
            continue
        # Priority 1: catalogo deterministico. Un'etichetta ignota non viene
        # eseguita parzialmente: per gli executor manuali cade sul reverse()
        # del modulo. Un pattern CONOSCIUTO che fallisce non usa il fallback,
        # evitando doppi effetti o un reverse diverso da quello dichiarato.
        unknown = _unknown_patterns(reverse_pattern) if reverse_pattern else []
        if reverse_pattern and not unknown:
            try:
                if rec.get("device"):
                    # C7 CP4: op eseguita su un DEVICE → il reverse gira LI'
                    # (§2.9), mai sul filesystem del server.
                    rev_result = _reverse_on_device(reverse_pattern, rec)
                else:
                    rev_result = apply_patterns(reverse_pattern, rec.get("plan") or {}, rec.get("results") or {})
            except Exception as e:
                details.append({"op_id": rec["op_id"], "executor": executor_name, "status": "error", "reason": f"reverse_pattern exception: {e}"})
                skipped += 1
                continue
        else:
            # Priority 2: fallback custom. Un reverse custom non puo' essere
            # spostato implicitamente sul server per un effetto nato sul device.
            if rec.get("device"):
                rev_result = _reverse_on_device(reverse_pattern, rec)
            else:
                rev_result, reverse_error = _reverse_with_module(ex, rec)
            if rev_result is None:
                details.append({"op_id": rec["op_id"], "executor": executor_name,
                                "status": "skipped", "reason": reverse_error})
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
            "stages": rev_result.get("stages"),
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
