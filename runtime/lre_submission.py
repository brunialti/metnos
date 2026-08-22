"""Planner-visible, domain-neutral admission boundary for LRE."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import logging
import os
import re

from durable_runtime_registry import ADMISSION_NAMES, default_runtime_registry
from durable_workloads.admission import submit_registered_local_sources
from durable_workloads.source_authority import SourceAuthority
from durable_workloads.storage import DurableWorkloadStore
from lre_config import (
    feature_configuration_lock,
    read_feature_configuration,
)
from messages import get as _msg


log = logging.getLogger("metnos.lre_submission")
_TURN_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_MAX_PATH_BYTES = 32_768
_MAX_REQUEST_ID_BYTES = 512


START_LRE_TOOL = {
    "type": "function",
    "function": {
        "name": "start_lre",
        "description": (
            "Affida a LRE una richiesta esplicitamente lunga su un insieme "
            "di sorgenti locali. Usa soltanto un profilo registrato e passa "
            "percorsi assoluti. Non usarlo per una singola operazione breve, "
            "per sorgenti remote o per inventare un nuovo flusso."
        ),
        "parameters": {
            "type": "object",
            "required": ["profile", "paths"],
            "properties": {
                "profile": {
                    "type": "string",
                    "enum": list(ADMISSION_NAMES),
                    "description": "Profilo LRE registrato adatto al risultato richiesto.",
                },
                "paths": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 1024,
                    "items": {"type": "string"},
                    "description": (
                        "File o cartelle locali, espressi con percorsi assoluti."
                    ),
                },
            },
        },
    },
}


def _failure(code: str, *, error_class: str) -> dict:
    return {
        "ok": False,
        "error": _msg(code),
        "error_code": code,
        "error_class": error_class,
    }


def _require_ready() -> None:
    configuration = read_feature_configuration()
    if not configuration.valid:
        raise RuntimeError("configuration_invalid")
    if not configuration.enabled:
        raise RuntimeError("feature_disabled")
    from durable_workloads.service import health_snapshot

    health = health_snapshot()
    if not (
        health.get("state") == "ready"
        and health.get("enabled") is True
        and health.get("worker_available") is True
    ):
        raise RuntimeError("worker_unavailable")


@contextmanager
def _admission_boundary():
    with feature_configuration_lock():
        _require_ready()
        yield


def _paths(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= 1024:
        raise ValueError("paths must contain 1..1024 entries")
    selected: list[str] = []
    for raw in value:
        if not isinstance(raw, str) or not raw or "\x00" in raw:
            raise ValueError("path is invalid")
        if not os.path.isabs(raw):
            raise ValueError("path must be absolute")
        path = os.path.abspath(raw)
        if len(os.fsencode(path)) > _MAX_PATH_BYTES:
            raise ValueError("path is outside the admission boundary")
        selected.append(path)
    return tuple(selected)


def _roots_digest(paths: tuple[str, ...]) -> str:
    payload = json.dumps(paths, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(
        b"metnos:lre-source-roots:1\x00" + payload.encode("utf-8")
    ).hexdigest()


def _request_key(source_request_id: object, turn_id: str) -> str:
    value = source_request_id or f"turn:{turn_id}"
    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or len(value.encode("utf-8")) > _MAX_REQUEST_ID_BYTES
    ):
        raise ValueError("source request identity is invalid")
    digest = hashlib.sha256(
        b"metnos:lre-submission:1\x00" + value.encode("utf-8")
    ).hexdigest()
    return f"lre:{digest}"


def handle_start_lre(
    args: dict,
    *,
    owner_user_id: str = "",
    turn_id: str = "",
    source_request_id: str = "",
    **_unused,
) -> dict:
    """Admit one registered local-source request without domain branches."""

    payload = args if isinstance(args, dict) else {}
    profile = payload.get("profile")
    turn_valid = (
        isinstance(turn_id, str) and bool(_TURN_ID_RE.fullmatch(turn_id))
    )
    if (
        not isinstance(owner_user_id, str)
        or not owner_user_id
        or (not turn_valid and not source_request_id)
        or profile not in ADMISSION_NAMES
    ):
        return _failure("ERR_LRE_REQUEST_INVALID", error_class="invalid_args")
    try:
        paths = _paths(payload.get("paths"))
        request_key = _request_key(source_request_id, turn_id)
    except (TypeError, ValueError):
        log.warning("lre_submission_invalid type=%s", type(profile).__name__)
        return _failure("ERR_LRE_REQUEST_INVALID", error_class="invalid_args")

    try:
        _require_ready()
        registry = default_runtime_registry()
        if tuple(registry.admission_names) != ADMISSION_NAMES:
            raise RuntimeError("admission_registry_mismatch")
        redacted = {
            "profile": profile,
            "source_root_count": len(paths),
            "source_roots_digest": _roots_digest(paths),
        }
        with DurableWorkloadStore.open() as store:
            with SourceAuthority.open() as authority:
                result = submit_registered_local_sources(
                    store,
                    authority,
                    registry,
                    profile,
                    owner_user_id,
                    request_key,
                    paths,
                    redacted_request=redacted,
                    admission_boundary=_admission_boundary,
                )
                try:
                    summary = store.execution_summary(
                        owner_user_id,
                        result.workload.workload_id,
                    )
                except Exception as exc:
                    # Admission is already durable. A projection failure must
                    # never turn its receipt into the false claim that nothing
                    # started; the console remains the authoritative view.
                    log.warning(
                        "lre_submission_summary_unavailable type=%s",
                        type(exc).__name__,
                    )
                    summary = None
    except RuntimeError as exc:
        reason = str(exc)
        code = {
            "configuration_invalid": "ERR_LRE_CONFIG_INVALID",
            "feature_disabled": "ERR_LRE_DISABLED",
            "worker_unavailable": "ERR_LRE_WORKER_UNAVAILABLE",
        }.get(reason)
        if code is not None:
            log.info("lre_submission_rejected reason=%s", reason)
            return _failure(code, error_class="dependency_unavailable")
        log.warning("lre_submission_failed type=%s", type(exc).__name__)
        return _failure("ERR_LRE_SUBMISSION_FAILED", error_class="operation_failed")
    except Exception as exc:
        log.warning("lre_submission_failed type=%s", type(exc).__name__)
        return _failure("ERR_LRE_SUBMISSION_FAILED", error_class="operation_failed")

    revision = result.revision
    workload = result.workload
    status_url = "/agent/workloads"
    stages = summary["stages"] if summary is not None else []
    limits = summary["budget"] if summary is not None else {}
    response = {
        "ok": True,
        "decision": "accepted",
        "workload_id": workload.workload_id,
        "revision_id": revision.revision_id if revision is not None else None,
        "state": workload.state.value,
        "expected_sources": (
            revision.expected_source_count if revision is not None else 0
        ),
        "status_url": status_url,
        "final_message_hint": _msg(
            (
                "MSG_LRE_SUBMITTED_WITH_SUMMARY"
                if summary is not None else "MSG_LRE_SUBMITTED"
            ),
            workload_id=workload.workload_id,
            source_count=(
                revision.expected_source_count if revision is not None else 0
            ),
            stage_count=len(stages),
            max_concurrency=limits.get("max_concurrency", 0),
            status_url=status_url,
        ),
    }
    if summary is not None:
        response.update({
            "plan_summary": {
                "stage_count": len(stages),
                "required_stage_count": sum(
                    1 for stage in stages if stage.get("required") is True
                ),
            },
            "limits": limits,
        })
    return response


BUILTIN_INPROC_SPECS = [{
    "name": "start_lre",
    "tool_spec": START_LRE_TOOL,
    "affinity": [
        "lre", "long run", "lavoro lungo", "in background",
        "riprendi dopo riavvio", "intero corpus", "tutta la cartella",
        "attività prolungata", "resume after restart", "whole folder",
        "large corpus", "molti file", "many files",
    ],
}]


__all__ = ["BUILTIN_INPROC_SPECS", "START_LRE_TOOL", "handle_start_lre"]
