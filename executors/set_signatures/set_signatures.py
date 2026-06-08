"""set_signatures — unified curation tool for the safety store (ADR 0071).

Single executor for add/remove. The kind argument determines the target
state:
  - 'blacklist' / 'whitelist': insert with source='user'.
  - 'unknown':                 delete the row (entries severity='forbidden'
                               are refused — Law 1).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "runtime"))

from messages import get as _msg  # noqa: E402
from safety.canonicalize import Signature
from safety.storage import SafetyStore


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

    actor = (ctx or {}).get("actor", "host")
    store = SafetyStore()
    try:
        if kind == "blacklist":
            return _set_blacklist(store, signature, reason, severity, actor)
        if kind == "whitelist":
            return _set_whitelist(store, signature, reason, actor)
        return _set_unknown(store, signature)
    finally:
        store.close()



if __name__ == "__main__":  # pragma: no cover
    import json, sys
    raw = sys.stdin.read() or "{}"
    args = json.loads(raw)
    print(json.dumps(invoke(args), default=str, ensure_ascii=False))
