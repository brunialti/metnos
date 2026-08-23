"""set_signatures — unified curation tool for the safety store (ADR 0071).

Single executor for add/remove. The kind argument determines the target
state:
  - 'blacklist' / 'whitelist': insert with source='user'.
  - 'unknown':                 delete the row (entries severity='forbidden'
                               are refused — Law 1).
"""
from __future__ import annotations

import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "runtime"))

from messages import get as _msg  # noqa: E402
from executor_helpers import run_stdio  # noqa: E402
from safety.canonicalize import Signature
from safety.storage import SafetyStore
from state_receipts import state_to_restore, state_transition


def _set_blacklist(
    store: SafetyStore, sig: str, reason: str, severity: str, actor: str
) -> dict:
    if severity not in ("forbidden", "irreversible", "dangerous", "reversible"):
        return {"ok": False, "error": _msg("ERR_ARG_INVALID", arg="severity", reason=str(severity))}
    row = store.upsert_user(
        sig, "blacklist",
        severity=severity, reason=reason, created_by=actor,
    )
    return {
        "ok": True,
        "signature": row.signature,
        "kind": row.kind,
        "severity": row.severity,
        "source": row.source,
        "message": _msg("MSG_SIG_BLACKLISTED", signature=row.signature, severity=severity),
    }


def _set_whitelist(
    store: SafetyStore, sig: str, reason: str, actor: str
) -> dict:
    row = store.upsert_user(
        sig, "whitelist",
        severity="reversible", reason=reason, created_by=actor,
    )
    return {
        "ok": True,
        "signature": row.signature,
        "kind": row.kind,
        "source": row.source,
        "message": _msg("MSG_SIG_WHITELISTED", signature=row.signature),
    }


def _set_unknown(store: SafetyStore, sig: str) -> dict:
    existing = store.find_by_signature(sig)
    if existing is None:
        return {
            "ok": True,
            "removed": False,
            "message": _msg("MSG_SIG_ALREADY_UNKNOWN", signature=sig),
        }
    if existing.severity == "forbidden":
        return {
            "ok": False,
            "error": (
                f"Signature '{sig}' has severity='forbidden' "
                "(Law 1, non-derogable). Cannot delete."
            ),
        }
    ok = store.delete(sig)
    return {
        "ok": True,
        "removed": ok,
        "previous_kind": existing.kind,
        "message": _msg("MSG_SIG_REMOVED", signature=sig, kind=existing.kind),
    }


def invoke(args: dict, ctx: dict | None = None) -> dict:
    kind = args.get("kind")
    signature = args.get("signature")
    reason = args.get("reason", "")
    severity = args.get("severity", "dangerous")

    if kind not in ("blacklist", "whitelist", "unknown"):
        return {"ok": False, "error": _msg("ERR_ARG_ENUM", arg="kind", allowed="blacklist | whitelist | unknown")}
    if not signature or not isinstance(signature, str):
        return {"ok": False, "error": _msg("ERR_ARG_MISSING", arg="signature")}

    try:
        Signature.parse(signature)
    except ValueError as e:
        return {"ok": False, "error": _msg("ERR_ARG_INVALID", arg="signature", reason=str(e))}

    if kind != "unknown" and not reason:
        return {"ok": False, "error": _msg("ERR_ARG_MISSING", arg="reason")}

    actor = (ctx or {}).get("actor") or os.environ.get("METNOS_ACTOR") or "host"
    store = SafetyStore()
    try:
        store.conn.execute("BEGIN IMMEDIATE")
        before = store.snapshot(signature)
        if before is not None and before.get("severity") == "forbidden":
            store.conn.execute("COMMIT")
            return {
                "ok": False,
                "error_code": "ERR_PERMISSION_DENIED",
                "error": _msg("ERR_PERMISSION_DENIED"),
                "_undo": {"outcome": "no_effect"},
            }
        if kind == "blacklist":
            result = _set_blacklist(
                store, signature, reason, severity, actor)
        elif kind == "whitelist":
            result = _set_whitelist(store, signature, reason, actor)
        else:
            result = _set_unknown(store, signature)
        after = store.snapshot(signature)
        store.conn.execute("COMMIT")
        if before == after:
            result["_undo"] = {"outcome": "no_effect"}
        elif after is not None and after.get("severity") == "forbidden":
            result["_undo"] = {"outcome": "irreversible"}
        else:
            result["_undo"] = {
                "outcome": "reversible",
                "state_receipt": state_transition(before, after),
            }
        return result
    except Exception:
        if store.conn.in_transaction:
            store.conn.execute("ROLLBACK")
        raise
    finally:
        store.close()


def reverse(_plan: dict, results: dict) -> dict:
    metadata = results.get("_undo") if isinstance(results, dict) else None
    receipt = metadata.get("state_receipt") if isinstance(metadata, dict) else None
    after = receipt.get("state_after") if isinstance(receipt, dict) else None
    before = receipt.get("state_before") if isinstance(receipt, dict) else None
    signature = None
    if isinstance(after, dict):
        signature = after.get("signature")
    elif isinstance(before, dict):
        signature = before.get("signature")
    if not isinstance(signature, str) or not signature:
        return {"ok": False, "ok_count": 0, "fail_count": 1,
                "error_class": "invalid_receipt"}

    store = SafetyStore()
    try:
        current = store.snapshot(signature)
        try:
            restore = state_to_restore(receipt, current)
        except ValueError:
            return {"ok": False, "ok_count": 0, "fail_count": 1,
                    "error_class": "state_conflict"}
        restored, reason = store.restore_snapshot_if_current(
            signature, expected=after, restore=restore)
        return {
            "ok": restored,
            "ok_count": 1 if restored else 0,
            "fail_count": 0 if restored else 1,
            **({"error_class": reason} if not restored else {}),
        }
    finally:
        store.close()



def main():
    run_stdio(invoke, default=str, allow_empty=True)


if __name__ == "__main__":
    main()
