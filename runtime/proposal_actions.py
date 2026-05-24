# SPDX-License-Identifier: AGPL-3.0-only
"""proposal_actions.py — Effetto operativo dell'accept su proposte (C.8).

Quando l'utente accetta una proposta nella dashboard `/admin/proposals*`,
questo modulo trasforma il book-keeping in azione concreta:

- `new_valid` (nome nuovo + grammar §2.2 ok): scrive marker
  `synt_pending/<sig>.json` per richiesta sintesi nuovo executor.
- `existing_parametric` (target esiste, modulo args): marker
  `change_pending/<sig>.json` per estensione signature.
- `existing_pipeline` (target esiste, combinato con altri): marker
  `pipeline_pending/<sig>.json` come "proposta utente convalidata"
  (futuro: candidate per multi_tool_paths promote ad active).
- `existing_redundant` (target esiste, ricreazione): NESSUNA azione.
  L'UI ha gia' chiesto conferma esplicita (hx-confirm); accept comunque
  registrato come bookmark, ma niente synt request.

Idempotenza per signature (cluster-level): 28 varianti deadline→calendar
con stesso `signature_relaxed` producono 1 SOLO marker.

Determinismo §7.9. Storage append-only nei rispettivi dir; il consumer
(synth daemon o admin manual) li processa con i propri tempi.

API pubblica:
    on_accept(proposal_dict, decision_record) -> dict
    pending_markers(kind: str | None = None) -> list[dict]
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Optional

from logging_setup import get_logger
import config as _C  # §7.11
log = get_logger(__name__)


_DATA_DIR = _C.PATH_USER_DATA
SYNT_PENDING_DIR = _DATA_DIR / "proposal_accepts" / "synt_pending"
CHANGE_PENDING_DIR = _DATA_DIR / "proposal_accepts" / "change_pending"
PIPELINE_PENDING_DIR = _DATA_DIR / "proposal_accepts" / "pipeline_pending"
TELOS_FILTERED_LOG = _DATA_DIR / "telos_filtered.jsonl"


def _audit_filtered(proposal: dict, decision_record: dict,
                    expected_alignment: float, hard_gate: float) -> None:
    """Append-only audit log per proposte accept-ate ma filtrate dal hard
    gate. Usato per visibilita' admin (quante proposte vengono scartate
    dal gate, quali, perche')."""
    rec = {
        "ts": decision_record.get("ts", time.time()),
        "prop_id": proposal.get("prop_id", ""),
        "by": decision_record.get("by", "admin"),
        "executor_target": proposal.get("executor_target", ""),
        "expected_alignment": expected_alignment,
        "hard_gate": hard_gate,
        "name_status": proposal.get("name_status", "unknown"),
        "lens": proposal.get("lens", ""),
        "convergence_count": int(proposal.get("convergence_count", 1) or 1),
    }
    try:
        TELOS_FILTERED_LOG.parent.mkdir(parents=True, exist_ok=True)
        with TELOS_FILTERED_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:
        log.warning("audit_filtered write failed: %s", e)


def _signature(proposal: dict) -> str:
    """Signature cross-source per dedup: signature_relaxed se presente
    (telos), altrimenti hash di (target, parametric)."""
    sig = proposal.get("signature_relaxed")
    if sig:
        return sig
    target = proposal.get("executor_target", "") or ""
    parametric = 1 if proposal.get("is_parametric_extension") else 0
    key = f"{target}|{parametric}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _write_marker(dir_path: Path, sig: str, payload: dict) -> bool:
    """Scrive marker JSON idempotente per signature. Ritorna True se nuovo,
    False se gia' esistente (cluster-level dedup)."""
    dir_path.mkdir(parents=True, exist_ok=True)
    marker = dir_path / f"{sig}.json"
    if marker.exists():
        return False
    marker.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                       encoding="utf-8")
    return True


def on_accept(proposal: dict, decision_record: dict) -> dict:
    """Trasforma accept in azione operativa per la proposta `proposal`.

    Args:
      proposal: dict UnifiedProposal-shape (vedi telos_proposals_store).
      decision_record: dict ritornato da apply_decision.

    Ritorna dict {kind, sig, created, marker_path | reason}.
    """
    if decision_record.get("action") != "accept":
        return {"kind": "noop", "reason": "not_an_accept"}

    # Hard gate alignment (C.8 fase 2, 24/5/2026): anche se l'utente accetta
    # una proposta vista in dashboard con filtri allargati, il gate qui
    # blocca la propagazione operativa se sotto soglia. Self-correcting:
    # proposte meritevoli riemergono con score piu' alto nel tempo.
    try:
        from runtime_settings import get as _setting
        hard_gate = float(_setting("telos.accept_hard_gate"))
    except Exception:
        hard_gate = 0.45
    expected_alignment = float(proposal.get("expected_alignment") or 0.0)
    if expected_alignment < hard_gate:
        _audit_filtered(proposal, decision_record, expected_alignment, hard_gate)
        return {"kind": "noop",
                "reason": f"below_hard_gate={hard_gate}",
                "expected_alignment": expected_alignment}

    name_status = proposal.get("name_status", "unknown")
    target = proposal.get("executor_target", "") or ""
    sig = _signature(proposal)
    base_payload = {
        "sig": sig,
        "prop_id": proposal.get("prop_id", ""),
        "source": proposal.get("source", "telos"),
        "executor_target": target,
        "proposed_action": proposal.get("proposed_action", ""),
        "rationale": proposal.get("rationale", ""),
        "name_status": name_status,
        "convergence_count": int(proposal.get("convergence_count", 1) or 1),
        "convergence_lenses": list(proposal.get("convergence_lenses") or []),
        "ts": decision_record.get("ts", time.time()),
        "by": decision_record.get("by", "admin"),
    }

    if name_status == "new_valid":
        created = _write_marker(SYNT_PENDING_DIR, sig, {
            **base_payload,
            "kind": "synt_request",
            "expected_name": target,
            "intent": proposal.get("proposed_action", ""),
        })
        return {"kind": "synt_pending", "sig": sig, "created": created,
                "marker_path": str(SYNT_PENDING_DIR / f"{sig}.json")}

    if name_status == "existing_parametric":
        created = _write_marker(CHANGE_PENDING_DIR, sig, {
            **base_payload,
            "kind": "change_executor",
            "args_extension_hint": proposal.get("proposed_action", ""),
        })
        return {"kind": "change_pending", "sig": sig, "created": created,
                "marker_path": str(CHANGE_PENDING_DIR / f"{sig}.json")}

    if name_status == "existing_pipeline":
        created = _write_marker(PIPELINE_PENDING_DIR, sig, {
            **base_payload,
            "kind": "pipeline_validated",
            "tools_mentioned": list(proposal.get("pipeline_tools_mentioned") or []),
        })
        return {"kind": "pipeline_pending", "sig": sig, "created": created,
                "marker_path": str(PIPELINE_PENDING_DIR / f"{sig}.json")}

    if name_status == "existing_redundant":
        return {"kind": "noop", "reason": "existing_redundant_skipped",
                "note": "UI hx-confirm gia' chiesto, ma no synth automatico"}

    # name_status `new_invalid` o `unknown`: skip silenzioso.
    return {"kind": "noop", "reason": f"name_status={name_status}"}


def pending_markers(kind: Optional[str] = None) -> list[dict]:
    """Lista marker pending di tutti i tipi (o un kind specifico).

    Usato da dashboard admin per mostrare azioni in pipeline post-accept.
    """
    dirs = {
        "synt_pending": SYNT_PENDING_DIR,
        "change_pending": CHANGE_PENDING_DIR,
        "pipeline_pending": PIPELINE_PENDING_DIR,
    }
    out: list[dict] = []
    for k, d in dirs.items():
        if kind and kind != k:
            continue
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.json")):
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
                rec["_marker_kind"] = k
                rec["_marker_path"] = str(f)
                out.append(rec)
            except (OSError, json.JSONDecodeError):
                continue
    out.sort(key=lambda r: -r.get("ts", 0))
    return out
