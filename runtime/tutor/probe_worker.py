"""Disposable execution boundary for one closed Tutor live probe."""

from __future__ import annotations

import json
import math
import sys
import time

from .models import TutorPrincipal
from .probes import ProbeContext, _REGISTRY, _validate_payload, bound_payload


def _request() -> tuple[object, ProbeContext]:
    raw = json.load(sys.stdin)
    probe_id = str(raw.get("probe_id") or "")
    spec = _REGISTRY.get(probe_id)
    if spec is None:
        raise ValueError("unknown Tutor probe")
    principal_data = raw.get("principal")
    if not isinstance(principal_data, dict):
        raise ValueError("missing Tutor probe principal")
    timeout_s = float(raw.get("timeout_s") or 0.0)
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("invalid Tutor probe timeout")
    principal = TutorPrincipal(
        user_id=str(principal_data.get("user_id") or ""),
        actor=str(principal_data.get("actor") or ""),
        audience=str(principal_data.get("audience") or ""),
        channel=str(principal_data.get("channel") or ""),
        conversation_id=str(
            principal_data.get("conversation_id") or ""),
    )
    if not principal.user_id:
        raise ValueError("missing Tutor probe owner")
    expected_rank = {"user": 0, "instance_admin": 1}
    if (expected_rank.get(principal.audience, -1)
            < expected_rank.get(spec.audience, 99)):
        raise PermissionError("Tutor probe audience mismatch")
    context = ProbeContext(
        principal=principal,
        lang=str(raw.get("lang") or ""),
        deadline_at=time.monotonic() + timeout_s,
    )
    return spec, context


def main() -> int:
    try:
        spec, context = _request()
        # The subprocess is an independent reader.  It must hold its own
        # cross-process lease rather than inheriting safety accidentally from
        # a parent that may be killed while this worker is still alive.
        from user_lifecycle import owner_session
        with owner_session(context.principal.user_id):
            payload = bound_payload(spec, spec.runner(context))
        _validate_payload(spec, payload)
        json.dump({
            "facts": payload.facts,
            "redactions": list(payload.redactions),
            "partial": bool(payload.partial),
            "status": payload.status,
        }, sys.stdout, ensure_ascii=False, separators=(",", ":"))
        return 0
    except Exception as exc:
        # The parent exposes only a closed unavailable reason.  Stderr keeps
        # the error class for local diagnostics and never includes payloads.
        sys.stderr.write(type(exc).__name__ + "\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
