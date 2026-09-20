"""Planner-visible, domain-neutral admission boundary for LRE."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import contextmanager
import hashlib
import json
import logging
import os
import re

from durable_runtime_registry import ADMISSION_NAMES, default_runtime_registry
from durable_workloads.admission import (
    submit_candidate,
    submit_registered_local_sources,
)
from durable_workloads.direct_invocation import (
    DirectInvocationUnsupported,
    build_direct_candidate,
    is_intrinsically_long,
)
from durable_workloads.schema import MAX_PLAN_JSON_BYTES, digest_json
from durable_workloads.source_authority import SourceAuthority
from durable_workloads.storage import (
    DurableWorkloadStore,
    IdempotencyConflictError,
)
from lre_config import (
    feature_configuration_lock,
    read_feature_configuration,
)
from messages import get as _msg


log = logging.getLogger("metnos.lre_submission")
_TURN_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_MAX_PATH_BYTES = 32_768
_MAX_REQUEST_ID_BYTES = 512
_READINESS_ERROR_CODES = {
    "configuration_invalid": "ERR_LRE_CONFIG_INVALID",
    "feature_disabled": "ERR_LRE_DISABLED",
    "worker_unavailable": "ERR_LRE_WORKER_UNAVAILABLE",
}


START_LRE_TOOL = {
    "type": "function",
    "function": {
        "name": "start_lre",
        "description": (
            "Ingresso tecnico di compatibilità per avviare un piano LRE già "
            "registrato su sorgenti locali. Il normale motore affida invece "
            "automaticamente a LRE le azioni lunghe ammissibili. Non usare "
            "questo strumento per scegliere se un’attività debba usare LRE."
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


def _receipt(store: DurableWorkloadStore, result) -> dict:
    try:
        summary = store.execution_summary(
            result.workload.owner_user_id,
            result.workload.workload_id,
        )
    except Exception as exc:
        # Admission is already durable. A projection failure must never turn
        # its receipt into the false claim that nothing started.
        log.warning(
            "lre_submission_summary_unavailable type=%s",
            type(exc).__name__,
        )
        summary = None

    revision = result.revision
    workload = result.workload
    status_url = "/admin/lre"
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


def _catalog_by_name(catalog: object) -> dict[str, object]:
    owned = getattr(catalog, "executors", None)
    if isinstance(owned, Mapping):
        values = owned.values()
    elif isinstance(catalog, Mapping):
        values = catalog.values()
    else:
        values = (
            catalog
            if isinstance(catalog, Sequence)
            and not isinstance(catalog, (str, bytes))
            else ()
        )
    return {
        str(getattr(executor, "name")): executor
        for executor in values
        if isinstance(getattr(executor, "name", None), str)
    }


def _automatic_error(code: str, *, error_class: str) -> dict:
    result = _failure(code, error_class=error_class)
    result["decision"] = "rejected"
    result["final_message_hint"] = result["error"]
    return result


def submit_automatic_lre(
    framework: object,
    *,
    catalog: object,
    owner_user_id: str,
    turn_id: str,
    source_request_id: str = "",
    target_device: str | None = None,
) -> dict | None:
    """Submit one finalized long step, or return ``None`` when none exists."""

    steps = tuple(getattr(framework, "steps", ()) or ())
    executors = _catalog_by_name(catalog)
    long_steps: list[tuple[int, object, object]] = []
    for index, step in enumerate(steps):
        tool = str(getattr(step, "tool", "") or "")
        if tool == "final_answer":
            continue
        executor = executors.get(tool)
        if executor is not None and is_intrinsically_long(executor):
            long_steps.append((index, step, executor))
    if not long_steps:
        return None

    # A finalized approval gate is allowed to stop the interactive leg.  The
    # approved resume is finalized and classified again before its long step.
    first_long_index = long_steps[0][0]
    if any(
        str(getattr(step, "tool", "") or "") == "get_approval"
        for step in steps[:first_long_index]
    ):
        return None

    try:
        executable = tuple(
            step for step in steps
            if str(getattr(step, "tool", "") or "") != "final_answer"
        )
        if (
            len(long_steps) != 1
            or len(executable) != 1
            or bool(getattr(framework, "fillers", {}) or {})
            or bool(getattr(long_steps[0][1], "if_prev_entries_nonempty", False))
        ):
            return _automatic_error(
                "ERR_LRE_PLAN_NOT_ADMISSIBLE",
                error_class="plan_not_admissible",
            )
        _index, step, executor = long_steps[0]
        args = getattr(step, "args", None)
        if not isinstance(args, dict):
            return _automatic_error(
                "ERR_LRE_PLAN_NOT_ADMISSIBLE",
                error_class="plan_not_admissible",
            )
        if not isinstance(owner_user_id, str) or not owner_user_id:
            return _automatic_error(
                "ERR_LRE_SUBMISSION_FAILED",
                error_class="operation_failed",
            )
        if (
            not source_request_id
            and (
                not isinstance(turn_id, str)
                or _TURN_ID_RE.fullmatch(turn_id) is None
            )
        ):
            return _automatic_error(
                "ERR_LRE_SUBMISSION_FAILED",
                error_class="operation_failed",
            )
        request_key = _request_key(source_request_id, turn_id)
        candidate, inventory = build_direct_candidate(
            executor, args, target_device,
        )
        args_digest = digest_json(
            "lre-direct-arguments",
            args,
            max_bytes=MAX_PLAN_JSON_BYTES,
        )
        placement = candidate["stages"][1]["placement"]
        placement_digest = digest_json(
            "lre-direct-placement",
            placement,
            max_bytes=MAX_PLAN_JSON_BYTES,
        )
        redacted = {
            "runner_name": str(executor.name),
            "arguments_digest": args_digest,
            "placement_target": placement["target"],
            "device_present": placement["target"] == "device",
            "placement_digest": placement_digest,
        }
        _require_ready()
        registry = default_runtime_registry()
        with DurableWorkloadStore.open() as store:
            result = submit_candidate(
                store,
                registry,
                owner_user_id,
                request_key,
                candidate,
                inventory,
                redacted_request=redacted,
                admission_boundary=_admission_boundary,
            )
            return _receipt(store, result)
    except DirectInvocationUnsupported:
        return _automatic_error(
            "ERR_LRE_EXECUTOR_CONTRACT_UNSUPPORTED",
            error_class="contract_unsupported",
        )
    except IdempotencyConflictError:
        return _automatic_error(
            "ERR_LRE_IDEMPOTENCY_CONFLICT",
            error_class="idempotency_conflict",
        )
    except RuntimeError as exc:
        reason = str(exc)
        code = _READINESS_ERROR_CODES.get(reason, "ERR_LRE_SUBMISSION_FAILED")
        return _automatic_error(code, error_class="dependency_unavailable")
    except Exception as exc:
        # A long step was already recognized. Never fall through to inline
        # execution, even when admission itself has an unexpected failure.
        log.warning("lre_automatic_submission_failed type=%s", type(exc).__name__)
        return _automatic_error(
            "ERR_LRE_SUBMISSION_FAILED",
            error_class="operation_failed",
        )


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
                response = _receipt(store, result)
    except IdempotencyConflictError:
        return _failure(
            "ERR_LRE_IDEMPOTENCY_CONFLICT",
            error_class="idempotency_conflict",
        )
    except RuntimeError as exc:
        reason = str(exc)
        code = _READINESS_ERROR_CODES.get(reason)
        if code is not None:
            log.info("lre_submission_rejected reason=%s", reason)
            return _failure(code, error_class="dependency_unavailable")
        log.warning("lre_submission_failed type=%s", type(exc).__name__)
        return _failure("ERR_LRE_SUBMISSION_FAILED", error_class="operation_failed")
    except Exception as exc:
        log.warning("lre_submission_failed type=%s", type(exc).__name__)
        return _failure("ERR_LRE_SUBMISSION_FAILED", error_class="operation_failed")

    return response


BUILTIN_INPROC_SPECS = [{
    "name": "start_lre",
    "tool_spec": START_LRE_TOOL,
    "affinity": [
        "start_lre", "profilo lre registrato", "registered lre profile",
    ],
}]


__all__ = [
    "BUILTIN_INPROC_SPECS",
    "START_LRE_TOOL",
    "handle_start_lre",
    "submit_automatic_lre",
]
