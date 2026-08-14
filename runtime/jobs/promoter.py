"""Task scheduler v2 `promoter` — promozione proattiva delle synth proposals.

Trigger `daily@04:45`, dopo `synt_suggest` (04:30) e
`proposals_eta_aggregate` (04:30). Per ogni proposta synth con
`final_state == "synthesized"` che NON ha gia' un record nel
`promoter.sqlite` (stato terminale), invoca
`proposal_evaluator.evaluate_proposal` (ADR 0122) e:

- verdict `accept` → `promote_to_catalog` + state='promoted_grace' (o
  'promoted_finalized' se `METNOS_PROMOTER_GRACE_HOURS=0`).
- verdict `gray`   → state='review_needed' + needs_human_review=1.
- verdict `reject` → state='archived' + sposta JSON in
  `~/.local/share/metnos/synth_archive/<id>/`.

Inoltre `expire_grace` promuove a `promoted_finalized` le proposte la cui
grace window e' scaduta.

Configurabilita' env (defaults conservativi):
- `METNOS_PROMOTER_GRACE_HOURS` (default 72; 0 = no grace = full auto)
- `METNOS_PROMOTER_MAX_PER_FIRE` (default 5)
- `METNOS_PROMOTER_DRY_RUN` (default false; true = decide ma non promuove)
- `METNOS_PROMOTER_NOTIFY_ADMIN` (default true; usato dal digest)
- `METNOS_SYNT_PROPOSALS_DIR` (override JSON dir per test)
- `METNOS_SYNTH_ARCHIVE_DIR` (override archive dir per test)

§7.9 deterministico ovunque (no LLM diretto, l'evaluator e' gia' §7.9).
§2.8 niente silent failure: ogni shape errore propagato in audit.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as _C  # §7.11

from .promoter_example import render_practical_example
from .promoter_promote import promote_to_catalog
from .promoter_state import (
    audit_append,
    ensure_schema,
    expire_grace,
    insert_pending,
    load_proposal_state,
    upsert_archived,
    upsert_promoted_grace,
    upsert_review_needed,
)

import logging
log = logging.getLogger("metnos.jobs.promoter")


# Default cap per fire — protezione contro flood al primo giro.
CAP_PER_FIRE_DEFAULT = 5
# Default grace window in ore.
GRACE_HOURS_DEFAULT = 72


_DEFAULT_PROPOSALS_DIR = _C.PATH_USER_DATA / "synt_proposals"
_DEFAULT_ARCHIVE_DIR = _C.PATH_USER_DATA / "synth_archive"


def _proposals_dir() -> Path:
    env = os.environ.get("METNOS_SYNT_PROPOSALS_DIR")
    return Path(env) if env else _DEFAULT_PROPOSALS_DIR


def _archive_dir() -> Path:
    env = os.environ.get("METNOS_SYNTH_ARCHIVE_DIR")
    return Path(env) if env else _DEFAULT_ARCHIVE_DIR


def _cap_per_fire() -> int:
    raw = os.environ.get("METNOS_PROMOTER_MAX_PER_FIRE")
    if not raw:
        return CAP_PER_FIRE_DEFAULT
    try:
        v = int(raw)
        return v if v > 0 else CAP_PER_FIRE_DEFAULT
    except ValueError:
        return CAP_PER_FIRE_DEFAULT


def _grace_hours() -> int:
    raw = os.environ.get("METNOS_PROMOTER_GRACE_HOURS")
    if raw is None or raw == "":
        return GRACE_HOURS_DEFAULT
    try:
        v = int(raw)
        return v if v >= 0 else GRACE_HOURS_DEFAULT
    except ValueError:
        return GRACE_HOURS_DEFAULT


def _dry_run() -> bool:
    return os.environ.get("METNOS_PROMOTER_DRY_RUN", "").lower() in (
        "1", "true", "yes",
    )


from timefmt import now_iso_z as _now_iso


def _load_proposal_json(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _is_already_processed(state_row: dict | None) -> bool:
    """True se la row esiste in uno stato terminale del promoter.

    Pending = non ancora processato (riprocessabile).
    Tutti gli altri stati = gia' decisi (no double promote/archive).
    """
    if state_row is None:
        return False
    state = state_row.get("state") or ""
    return state in (
        "promoted_grace", "promoted_finalized", "rolled_back",
        "archived", "review_needed",
    )


def _candidate_proposals(cap: int) -> list[Path]:
    """Itera i JSON synthesized non ancora processed (state pending o assente).

    Ordine: ts_start ascending (priorita' alle proposte piu' vecchie),
    cap stretto.
    """
    base = _proposals_dir()
    if not base.exists():
        return []
    out: list[tuple[float, Path]] = []
    for fp in base.glob("*.json"):
        if "_archived" in fp.parts:
            continue
        d = _load_proposal_json(fp)
        if d is None:
            continue
        if d.get("final_state") != "synthesized":
            continue
        proposal_id = d.get("id") or fp.stem
        state_row = load_proposal_state(proposal_id)
        if _is_already_processed(state_row):
            continue
        ts = d.get("ts_start")
        try:
            ts_f = float(ts) if ts is not None else fp.stat().st_mtime
        except (TypeError, ValueError):
            ts_f = fp.stat().st_mtime
        out.append((ts_f, fp))
    out.sort(key=lambda x: x[0])
    return [p for _, p in out[:cap]]


def _archive_proposal_json(proposal_id: str, json_path: Path) -> str | None:
    """Sposta il JSON in `<archive_dir>/<id>/` per traccia storica.

    Atomic via os.replace quando possibile. Ritorna il nuovo path o None se
    archive fallisce (non-blocking, audit comunque).
    """
    if not json_path.exists():
        return None
    dest_dir = _archive_dir() / proposal_id
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / json_path.name
        try:
            os.replace(str(json_path), str(dest))
        except OSError:
            shutil.copy2(str(json_path), str(dest))
            json_path.unlink()
        return str(dest)
    except OSError:
        return None


def _process_one(proposal: dict, dry_run: bool, grace_hours: int) -> dict:
    """Valuta + dispatch su una singola proposta. Ritorna evento audit."""
    proposal_id = proposal.get("id") or "?"
    name = proposal.get("name") or proposal.get("expected_name") or "?"
    base_ev: dict = {
        "ts": _now_iso(),
        "proposal_id": proposal_id,
        "name": name,
        "dry_run": dry_run,
    }

    # Inserisce row pending (idempotente) cosi' le query CLI vedono lo stato.
    if not dry_run:
        insert_pending(proposal_id, name)

    # Evaluator §ADR 0122.
    proposals_path = _proposals_dir()
    proposal_path = proposals_path / f"{proposal_id}.json"
    if not proposal_path.exists():
        return {
            **base_ev,
            "action": "skipped",
            "reason": "proposal_json_not_found",
        }
    try:
        from proposal_evaluator import evaluate_proposal
        result = evaluate_proposal(proposal_path, audit=True)
    except Exception as ex:  # noqa: BLE001
        return {
            **base_ev,
            "action": "evaluator_crash",
            "error": str(ex)[:300],
        }
    verdict_dict = result.to_dict()
    base_ev.update({
        "verdict": result.verdict,
        "evaluator_score": result.score,
        "killers": list(result.killers_triggered),
    })

    # Esempio pratico deterministico §7.9.
    try:
        example = render_practical_example(proposal, verdict_dict)
    except Exception as ex:  # noqa: BLE001
        # Fallback affermativo, mai fail-loud su rendering.
        example = f"(esempio non disponibile: {str(ex)[:80]})"
    base_ev["example"] = example

    # Dispatch.
    if result.verdict == "accept":
        if dry_run:
            base_ev["action"] = "would_promote"
            return base_ev
        promote_result = promote_to_catalog(proposal)
        if not promote_result.get("ok"):
            # Promote fallito (admission/sign/blob): NON marcare pending,
            # cosi' il prossimo fire ritenta (se il problema e' transient).
            # Marca review_needed se admission e' fail strutturale.
            err = promote_result.get("error") or ""
            if err.startswith("admission_failed_"):
                upsert_review_needed(
                    proposal_id=proposal_id, name=name,
                    verdict=verdict_dict, practical_example=example,
                )
                base_ev["action"] = "promote_failed_admission"
            else:
                base_ev["action"] = "promote_failed_transient"
            base_ev["error"] = err
            base_ev["error_detail"] = promote_result.get("reason") or ""
            return base_ev
        grace_iso = upsert_promoted_grace(
            proposal_id=proposal_id,
            name=name,
            blob_path=promote_result["blob_path"],
            verdict=verdict_dict,
            practical_example=example,
            grace_hours=grace_hours,
        )
        base_ev["action"] = (
            "promoted_finalized" if grace_hours == 0 else "promoted_grace"
        )
        base_ev["grace_until"] = grace_iso
        base_ev["blob_path"] = promote_result["blob_path"]
        base_ev["catalog_path"] = promote_result["path"]
        return base_ev

    if result.verdict == "gray":
        if dry_run:
            base_ev["action"] = "would_review_needed"
            return base_ev
        upsert_review_needed(
            proposal_id=proposal_id, name=name,
            verdict=verdict_dict, practical_example=example,
        )
        base_ev["action"] = "review_needed"
        return base_ev

    # reject
    if dry_run:
        base_ev["action"] = "would_archive"
        return base_ev
    upsert_archived(
        proposal_id=proposal_id, name=name, verdict=verdict_dict,
    )
    archived_path = _archive_proposal_json(proposal_id, proposal_path)
    base_ev["action"] = "archived"
    base_ev["archived_path"] = archived_path
    return base_ev


def _executor_grace_failures(name: str, since_epoch: float) -> tuple[int, int]:
    """Conta (uses, hard_fails) dell'executor `name` nei turni dal `since_epoch`
    (inizio grace). hard_fail = step con `error` o `scope_violation`. Scansione
    deterministica del turn-log (§7.9, nessun LLM). Per il kill-switch grace.
    """
    import glob
    tdir = _C.PATH_USER_DATA / "turns"
    uses = 0
    fails = 0
    for fp in glob.glob(str(tdir / "*.jsonl")):
        try:
            if os.path.getmtime(fp) < since_epoch - 86400:
                continue  # file interamente precedente alla finestra (slack 1g)
        except OSError:
            continue
        try:
            with open(fp, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = d.get("ts_start") or d.get("ts_end") or 0
                    try:
                        ts = float(ts)
                    except (TypeError, ValueError):
                        ts = 0.0
                    if ts and ts < since_epoch:
                        continue  # turno precedente alla promozione → ignora
                    for s in (d.get("steps") or []):
                        if isinstance(s, dict) and s.get("chosen_tool") == name:
                            uses += 1
                            if s.get("error") or s.get("scope_violation"):
                                fails += 1
        except OSError:
            continue
    return uses, fails


def _notify_killswitch(candidates: list, enforce: bool) -> None:
    """Notifica Telegram all'admin dei candidati kill-switch (osserva) o dei
    ritiri (enforce). Destinatario via `_resolve_admin_recipient` (users.db).
    Gated da METNOS_PROMOTER_NOTIFY_ADMIN. Best-effort: mai solleva.
    """
    if os.environ.get("METNOS_PROMOTER_NOTIFY_ADMIN", "true").strip().lower() \
            in ("0", "false", "no"):
        return
    try:
        from .promoter_digest import _resolve_admin_recipient
        rid, _err = _resolve_admin_recipient()
        if not rid:
            return
        body = "\n".join(
            f"• {c.get('name')} ({c.get('fails')}×)" for c in candidates)
        from messages import get as _msg
        key = "MSG_KILLSWITCH_ENFORCE" if enforce else "MSG_KILLSWITCH_OBSERVE"
        text = _msg(key, body=body)
        from backends.messages import telegram_bot
        telegram_bot.send({"messages": [{"recipient_id": rid, "body": text}]})
    except Exception as ex:
        log.warning("killswitch notify fallita: %r", ex)


def _grace_killswitch(dry_run: bool) -> dict:
    """Grazia probatoria a esito (L3.5, 30/5/2026): rileva gli executor in
    `promoted_grace` con segnale negativo (>= ROLLBACK_FAILS fallimenti duri nei
    turni dall'inizio grace) e li ritira via `rollback_promotion`.

    PRUDENZA — di default OSSERVA soltanto: logga "would_rollback" e lo riporta,
    ma il rollback REALE avviene solo con `METNOS_PROMOTER_KILLSWITCH_ENFORCE=1`.
    Cosi' il segnale si valida per qualche ciclo prima di armare l'auto-rollback.
    """
    import datetime as _dt
    rollback_fails = int(os.environ.get("METNOS_PROMOTER_ROLLBACK_FAILS", "2"))
    enforce = os.environ.get("METNOS_PROMOTER_KILLSWITCH_ENFORCE", "0") == "1"
    candidates: list[dict] = []
    try:
        from .promoter_state import list_by_state
        grace_rows = list_by_state(["promoted_grace"])
    except Exception as ex:  # difensivo: il kill-switch non deve mai rompere il promoter
        log.warning("killswitch: list_by_state fallito: %r", ex)
        return {"enforce": enforce, "error": repr(ex), "candidates": []}
    for rec in grace_rows:
        name = rec.get("name") or ""
        pid = rec.get("proposal_id") or ""
        if not name or not pid:
            continue
        promoted_at = rec.get("promoted_at") or ""
        try:
            since = _dt.datetime.fromisoformat(
                promoted_at.replace("Z", "+00:00")).timestamp()
        except (ValueError, AttributeError):
            since = 0.0
        uses, fails = _executor_grace_failures(name, since)
        if fails < rollback_fails:
            continue
        action = "would_rollback"
        if enforce and not dry_run:
            try:
                from .promoter_rollback import rollback_promotion
                rollback_promotion(pid)
                action = "rolled_back"
            except Exception as ex:
                action = f"rollback_error:{ex!r}"
        log.warning("promoter killswitch: %s %s (uses=%d fails=%d enforce=%s)",
                     name, action, uses, fails, enforce)
        candidates.append({"name": name, "proposal_id": pid,
                            "uses": uses, "fails": fails, "action": action})
    if candidates:
        _notify_killswitch(candidates, enforce)
    return {"enforce": enforce, "rollback_fails": rollback_fails,
            "candidates": candidates}


def task_promoter(payload: dict | None = None) -> dict:
    """Callback scheduler v2 `promoter` (daily@04:45).

    Payload ignorato (uniforme con gli altri callback v2). Ritorna RunResult:
        {ok, ok_count, error_count, metadata: {...}}
    """
    cap = _cap_per_fire()
    grace_hours = _grace_hours()
    dry_run = _dry_run()

    # Schema migration idempotente (eseguita lazy dalla prima query).
    import sqlite3 as _sql
    conn = _sql.connect(str(
        Path(os.environ.get("METNOS_PROMOTER_DB",
                            str(_C.PATH_USER_DATA / "promoter.sqlite")))
    ))
    try:
        cols_added = ensure_schema(conn)
    finally:
        conn.close()

    # 0. Kill-switch grace a esito (L3.5): ritira/segnala gli executor in grace
    #    con segnale negativo PRIMA del finalize, cosi' un cattivo non si
    #    consolida. Osserva-di-default (enforce via env).
    killswitch = _grace_killswitch(dry_run)

    # 1. Grace expiry (sempre prima, anche se cap=0 di candidates).
    finalized = expire_grace()
    for pid in finalized:
        audit_append({
            "ts": _now_iso(),
            "proposal_id": pid,
            "action": "promoted_finalized_via_grace_expiry",
        })

    # 2. Candidati: synthesized non ancora processed.
    candidates = _candidate_proposals(cap)

    ok_count = 0
    error_count = 0
    events: list[dict] = []
    for fp in candidates:
        d = _load_proposal_json(fp)
        if d is None:
            error_count += 1
            events.append({
                "ts": _now_iso(),
                "proposal_id": fp.stem,
                "action": "skipped",
                "reason": "json_decode_failed",
            })
            continue
        ev = _process_one(d, dry_run, grace_hours)
        events.append(ev)
        action = ev.get("action", "")
        if action.startswith("promoted") or action in (
            "review_needed", "archived",
            "would_promote", "would_review_needed", "would_archive",
        ):
            ok_count += 1
        elif action in (
            "evaluator_crash", "promote_failed_admission",
            "promote_failed_transient", "skipped",
        ):
            error_count += 1
        else:
            error_count += 1

    audit_path: Path | None = None
    if events:
        audit_path = audit_append(events[0])
        for ev in events[1:]:
            audit_append(ev)

    return {
        "ok": True,
        "ok_count": ok_count,
        "error_count": error_count,
        "metadata": {
            "cap": cap,
            "grace_hours": grace_hours,
            "dry_run": dry_run,
            "finalized_via_grace_expiry": len(finalized),
            "candidates_seen": len(candidates),
            "killswitch": killswitch,
            "schema_migration": cols_added,
            "audit_path": str(audit_path) if audit_path else None,
        },
    }


__all__ = ["task_promoter"]
