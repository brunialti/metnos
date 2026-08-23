"""Closed, read-only F3 probes and per-principal observation capsules.

Probe IDs enter this module only through a validated, signed observation view.
There is no free-form dispatch and no executor invocation.  Each runner reads
the same typed registry used by the corresponding runtime/UI surface, then a
sanitizer reduces it to a bounded public schema before composition.
"""

from __future__ import annotations

from collections import Counter, OrderedDict
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Callable, Literal

from logging_setup import get_logger

from .models import TutorPrincipal

log = get_logger(__name__)

_AUDIENCE_RANK = {"user": 0, "instance_admin": 1}
_STATUSES = frozenset({"ok", "partial", "unavailable", "stale"})
_MAX_CACHE_ROWS = 128
_COMPOSITION_LIVE_MAX_CHARS = 10_000
try:
    _LOADED_IMPLEMENTATION_DIGEST = __import__("hashlib").sha256(
        Path(__file__).read_bytes()).hexdigest()[:16]
except OSError:
    _LOADED_IMPLEMENTATION_DIGEST = "unreadable"


def _probe_checkpoint(context: "ProbeContext") -> None:
    if context.deadline_at:
        from .deadline import remaining
        remaining(context.deadline_at)


@dataclass(frozen=True, slots=True)
class ProbeContext:
    principal: TutorPrincipal
    lang: str
    deadline_at: float = 0.0


@dataclass(frozen=True, slots=True)
class ProbePayload:
    facts: dict
    redactions: tuple[str, ...] = ()
    partial: bool = False
    status: Literal["ok", "partial", "unavailable"] | None = None


@dataclass(frozen=True, slots=True)
class ProbeSpec:
    probe_id: str
    audience: str
    bindings: tuple[str, ...]
    timeout_s: float
    ttl_s: float
    stale_ttl_s: float
    max_bytes: int
    source_version: str
    runner: Callable[[ProbeContext], ProbePayload]
    # Recursive closed schema.  Row fields are checked as strictly as the
    # outer mapping; a top-level key alone never attests arbitrary nested JSON.
    fact_schema: dict
    execution_policy: dict

    @property
    def name(self) -> str:
        return f"tutor_probe_{self.probe_id}"

    @property
    def fact_keys(self) -> frozenset[str]:
        return frozenset(self.fact_schema)


@dataclass(frozen=True, slots=True)
class ObservationCapsule:
    probe_id: str
    observed_at: str
    fresh_until: str
    status: Literal["ok", "partial", "unavailable", "stale"]
    facts: dict
    redactions: tuple[str, ...]
    source_version: str
    # Added only after a registered observation view has projected the probe.
    # Raw/cache capsules remain view-less and therefore cannot authorize use.
    view_id: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace(
        "+00:00", "Z")


def _executor_payload(context: ProbeContext) -> ProbePayload:
    from loader import load_catalog

    _probe_checkpoint(context)
    catalog = load_catalog()
    _probe_checkpoint(context)
    lifecycle = Counter()
    membership = Counter()
    rows = []
    for position, executor in enumerate(
            sorted(catalog, key=lambda item: item.name)):
        if position % 32 == 0:
            _probe_checkpoint(context)
        lifecycle[str(executor.lifecycle or "unknown")] += 1
        membership[str(getattr(executor, "membership", "builtin") or
                       "builtin")] += 1
        if len(rows) < 96:
            rows.append({
                "name": str(executor.name)[:96],
                "version": str(executor.version or "")[:48],
                "lifecycle": str(executor.lifecycle or "unknown")[:32],
                "membership": str(
                    getattr(executor, "membership", "builtin") or
                    "builtin")[:32],
                "standard_state": str(
                    getattr(executor, "standard_state", "legacy") or
                    "legacy")[:32],
            })
    rejected = tuple(getattr(catalog, "rejected", None) or ())
    return ProbePayload(
        facts={
            "total": len(catalog),
            "by_lifecycle": dict(sorted(lifecycle.items())),
            "by_membership": dict(sorted(membership.items())),
            "executors": rows,
            "rejected_total": len(rejected),
        },
        redactions=("rejected_paths_and_details",) if rejected else (),
        partial=len(catalog) > len(rows),
    )


def _service_payload(context: ProbeContext) -> ProbePayload:
    from services_registry import localized, snapshots
    from .deadline import remaining

    rows = []
    snapshots_rows = localized(
        snapshots(timeout_s=remaining(context.deadline_at)), context.lang,
    )
    technical_failures = 0
    for row in snapshots_rows:
        if row.get("observation_error"):
            technical_failures += 1
            continue
        label = row.get("label")
        description = row.get("description")
        rows.append({
            "key": str(row.get("key") or "")[:48],
            "label": str(label or "")[:96],
            "description": str(description or "")[:240],
            "status": str(row.get("status") or "unknown")[:32],
            "installed": bool(row.get("installed")),
            "healthy": row.get("healthy")
            if isinstance(row.get("healthy"), bool) else None,
            "scope": str(row.get("scope") or "")[:16],
        })
    status = (
        "unavailable" if snapshots_rows and not rows else
        "partial" if technical_failures else "ok"
    )
    return ProbePayload(facts={
        "total": len(snapshots_rows),
        "running": sum(row["status"] == "running" for row in rows),
        "degraded": sum(row["status"] in {"degraded", "failed"}
                        for row in rows),
        "services": rows,
    }, redactions=("units_endpoints_pids",),
        partial=bool(technical_failures), status=status)


def _device_payload(context: ProbeContext) -> ProbePayload:
    import devices
    import placement

    _probe_checkpoint(context)
    owned = devices.list_by_owner_readonly(context.principal.user_id)
    _probe_checkpoint(context)
    rows = []
    for position, device in enumerate(owned[:64]):
        if position % 16 == 0:
            _probe_checkpoint(context)
        client_version = ""
        try:
            profile = json.loads(device.profile_json or "{}")
            if isinstance(profile, dict):
                client_version = str(profile.get("client_version") or "")[:48]
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
        rows.append({
            "id": str(device.id)[:96],
            "name": str(device.name or "")[:96],
            "os_family": str(device.os_family or "")[:32],
            "os_arch": str(device.os_arch or "")[:32],
            "client_version": client_version,
            "last_heartbeat": str(device.last_heartbeat or "")[:48],
            "available": bool(placement.is_available(device)),
        })
    return ProbePayload(
        facts={
            "total": len(owned),
            "available": sum(row["available"] for row in rows),
            "devices": rows,
        },
        redactions=("public_keys_fingerprints_profiles",),
        partial=len(owned) > len(rows),
    )


def _task_payload(context: ProbeContext) -> ProbePayload:
    from recurring_tasks import list_user_tasks_readonly
    from scheduler_v2 import client as scheduler

    _probe_checkpoint(context)
    tasks = list_user_tasks_readonly(context.principal.user_id)
    _probe_checkpoint(context)
    admitted_names = tuple(
        str(task.get("scheduler_name") or "") for task in tasks
        if str(task.get("scheduler_name") or "")
    )
    runtime_rows = {
        row["name"]: row
        for row in scheduler.list_jobs_readonly(admitted_names)}
    _probe_checkpoint(context)
    rows = []
    for task in tasks[:64]:
        _probe_checkpoint(context)
        scheduler_name = str(task.get("scheduler_name") or "")
        runtime = runtime_rows.get(scheduler_name, {})
        history = scheduler.history_readonly(
            name=scheduler_name, limit=3)
        rows.append({
            "id": int(task.get("id") or 0),
            "name": str(task.get("name") or "")[:96],
            "label": str(task.get("label") or "")[:160],
            "schedule": str(task.get("schedule") or "")[:96],
            "enabled": bool(runtime.get("enabled", task.get("enabled"))),
            "next_fire_at": str(runtime.get("next_fire_at") or "")[:48],
            "last_run_at": str(runtime.get("last_run_at") or "")[:48],
            "last_status": str(runtime.get("last_status") or "")[:32],
            "recent_runs": [{
                "started_at": str(run.get("started_at") or "")[:48],
                "status": str(run.get("status") or "")[:32],
                "duration_ms": int(run.get("duration_ms") or 0),
            } for run in history],
        })
    return ProbePayload(
        facts={
            "total": len(tasks),
            "enabled": sum(row["enabled"] for row in rows),
            "tasks": rows,
        },
        redactions=("task_queries_run_outputs",),
        partial=len(tasks) > len(rows),
    )


def _scheduler_health_payload(context: ProbeContext) -> ProbePayload:
    """Observe the co-hosted loop itself, separately from individual jobs."""

    from scheduler_v2.health import snapshot

    _probe_checkpoint(context)
    observed = snapshot()
    _probe_checkpoint(context)
    facts = {
        "component": str(observed.get("component") or "scheduler_v2")[:48],
        "cohost": str(observed.get("cohost") or "http")[:32],
        "state": str(observed.get("state") or "unavailable")[:32],
        "healthy": observed.get("healthy") is True,
        "reason_code": str(observed.get("reason_code") or "")[:64],
        "started_at": str(observed.get("started_at") or "")[:48],
        "heartbeat_at": str(observed.get("heartbeat_at") or "")[:48],
        "heartbeat_age_s": observed.get("heartbeat_age_s"),
        "jobs_total": int(observed.get("jobs_total") or 0),
        "jobs_enabled": int(observed.get("jobs_enabled") or 0),
        "jobs_running": int(observed.get("jobs_running") or 0),
        "last_run_at": str(observed.get("last_run_at") or "")[:48],
        "last_run_status": str(
            observed.get("last_run_status") or "")[:32],
        "error_class": str(observed.get("error_class") or "")[:96],
        "error_summary": str(observed.get("error_summary") or "")[:240],
    }
    return ProbePayload(
        facts=facts,
        redactions=("job_payloads_and_outputs",),
        status=("unavailable" if facts["state"] == "unavailable" else None),
    )


_POLICY = {
    "effect": "read_only",
    "parallelism_class": 1,
    "resource_class": "local_io",
    "concurrency_key": "none",
    "equivalence_gate": "verified",
}

_REGISTRY: dict[str, ProbeSpec] = {
    "admitted_executor_state": ProbeSpec(
        "admitted_executor_state", "instance_admin", (), 2.0, 300.0, 120.0,
        24_000, "loader.catalog/v1", _executor_payload, {
            "total": "int",
            "by_lifecycle": {"*": "int"},
            "by_membership": {"*": "int"},
            "executors": [{
                "name": "str", "version": "str", "lifecycle": "str",
                "membership": "str", "standard_state": "str",
            }],
            "rejected_total": "int",
        }, dict(_POLICY)),
    "service_health": ProbeSpec(
        "service_health", "instance_admin", (), 10.0, 120.0, 60.0, 20_000,
        "services_registry.snapshots/v2", _service_payload, {
            "total": "int", "running": "int", "degraded": "int",
            "services": [{
                "key": "str", "label": "str", "description": "str",
                "status": "str", "installed": "bool",
                "healthy": "bool_or_none", "scope": "str",
            }],
        }, dict(_POLICY)),
    "owned_device_state": ProbeSpec(
        "owned_device_state", "user", (), 2.0, 120.0, 90.0, 16_000,
        "devices.list_by_owner_readonly+placement.is_available/v2",
        _device_payload, {
            "total": "int", "available": "int",
            "devices": [{
                "id": "str", "name": "str", "os_family": "str",
                "os_arch": "str", "client_version": "str",
                "last_heartbeat": "str", "available": "bool",
            }],
        }, dict(_POLICY)),
    "actor_task_state": ProbeSpec(
        "actor_task_state", "user", (), 2.0, 120.0, 90.0, 24_000,
        "recurring_tasks+scheduler_v2-readonly/v2", _task_payload, {
            "total": "int", "enabled": "int",
            "tasks": [{
                "id": "int", "name": "str", "label": "str",
                "schedule": "str", "enabled": "bool",
                "next_fire_at": "str", "last_run_at": "str",
                "last_status": "str",
                "recent_runs": [{
                    "started_at": "str", "status": "str",
                    "duration_ms": "int",
                }],
            }],
        }, dict(_POLICY)),
    "scheduler_health": ProbeSpec(
        # Health is a heartbeat, not configuration.  Do not extend a durable
        # snapshot's validity with another process-local cache interval.
        "scheduler_health", "instance_admin", (), 2.0, 0.0, 0.0, 8_000,
        "scheduler_v2.daemon_handle/v2", _scheduler_health_payload, {
            "component": "str", "cohost": "str", "state": "str",
            "healthy": "bool", "reason_code": "str",
            "started_at": "str", "heartbeat_at": "str",
            "heartbeat_age_s": "number_or_none", "jobs_total": "int",
            "jobs_enabled": "int", "jobs_running": "int",
            "last_run_at": "str", "last_run_status": "str",
            "error_class": "str", "error_summary": "str",
        }, dict(_POLICY)),
}

_CACHE: "OrderedDict[tuple[str, str, str, str, str, str], tuple[float, ObservationCapsule]]" = (
    OrderedDict())
_CACHE_LOCK = threading.RLock()


def registered_probe_ids() -> frozenset[str]:
    return frozenset(_REGISTRY)


def probe_contract(probe_id: str) -> tuple[str, dict] | None:
    """Return the immutable-by-convention public shape of a registered probe."""

    spec = _REGISTRY.get(str(probe_id or ""))
    if spec is None:
        return None
    return spec.audience, spec.fact_schema


def _implementation_version(spec: ProbeSpec) -> str:
    """Bind a capsule/cache entry to the exact installed probe code.

    The declared source names the upstream registry.  The file digest makes a
    code-only sanitizer or runner change invalidate old observations as well.
    """

    return f"{spec.source_version}@sha256:{_LOADED_IMPLEMENTATION_DIGEST}"


def _cache_key(spec: ProbeSpec, context: ProbeContext
               ) -> tuple[str, str, str, str, str, str]:
    return (
        spec.probe_id,
        context.principal.user_id,
        context.principal.actor,
        context.principal.audience,
        context.lang,
        _implementation_version(spec),
    )


def _cached(spec: ProbeSpec, context: ProbeContext,
            now: float) -> tuple[ObservationCapsule | None,
                                 ObservationCapsule | None]:
    key = _cache_key(spec, context)
    with _CACHE_LOCK:
        row = _CACHE.get(key)
        if row is None:
            return None, None
        _CACHE.move_to_end(key)
        fresh_until, capsule = row
        if fresh_until >= now:
            return capsule, None
        if now <= fresh_until + spec.stale_ttl_s:
            return None, capsule
        _CACHE.pop(key, None)
        return None, None


def _store(spec: ProbeSpec, context: ProbeContext, capsule: ObservationCapsule,
           fresh_until: float) -> None:
    key = _cache_key(spec, context)
    with _CACHE_LOCK:
        _CACHE[key] = (fresh_until, capsule)
        _CACHE.move_to_end(key)
        while len(_CACHE) > _MAX_CACHE_ROWS:
            _CACHE.popitem(last=False)


def purge_owner(owner_user_id: str) -> int:
    """Remove all live-observation cache entries for one principal."""

    owner = str(owner_user_id or "")
    if not owner:
        return 0
    with _CACHE_LOCK:
        keys = [key for key in _CACHE if key[1] == owner]
        for key in keys:
            _CACHE.pop(key, None)
    return len(keys)


def _validate_schema_value(value, schema, path: str) -> None:
    """Validate one value against the small recursive probe-schema language."""

    if isinstance(schema, str):
        valid = {
            "str": lambda item: type(item) is str,
            "int": lambda item: type(item) is int,
            "bool": lambda item: type(item) is bool,
            "bool_or_none": lambda item: item is None or type(item) is bool,
            "number_or_none": lambda item: (
                item is None or type(item) in (int, float)),
        }.get(schema)
        if valid is None:
            raise ValueError(f"unknown probe schema scalar at {path}")
        if not valid(value):
            raise TypeError(f"probe value has wrong type at {path}")
        return
    if isinstance(schema, list):
        if len(schema) != 1 or not isinstance(value, list):
            raise TypeError(f"probe value is not a schema list at {path}")
        for index, item in enumerate(value):
            _validate_schema_value(item, schema[0], f"{path}[{index}]")
        return
    if isinstance(schema, dict):
        if not isinstance(value, dict):
            raise TypeError(f"probe value is not a schema mapping at {path}")
        if set(schema) == {"*"}:
            if any(type(key) is not str for key in value):
                raise TypeError(f"probe dynamic keys are not strings at {path}")
            for key, item in value.items():
                _validate_schema_value(item, schema["*"], f"{path}.{key}")
            return
        if set(value) != set(schema):
            raise ValueError(f"probe mapping does not match schema at {path}")
        for key, child in schema.items():
            _validate_schema_value(value[key], child, f"{path}.{key}")
        return
    raise TypeError(f"invalid probe schema at {path}")


def _validate_payload(spec: ProbeSpec, payload: ProbePayload) -> None:
    if not isinstance(payload.facts, dict):
        raise TypeError("probe facts must be a mapping")
    if set(payload.facts) != set(spec.fact_schema):
        raise ValueError("probe facts do not match the closed schema")
    _validate_schema_value(payload.facts, spec.fact_schema, "facts")
    if payload.status not in {None, "ok", "partial", "unavailable"}:
        raise ValueError("invalid probe payload status")
    encoded = json.dumps(
        payload.facts, ensure_ascii=False, separators=(",", ":"),
        sort_keys=True).encode("utf-8")
    if len(encoded) > spec.max_bytes:
        raise ValueError("probe facts exceed the declared maximum")


def _encoded_facts_size(facts: dict) -> int:
    return len(json.dumps(
        facts, ensure_ascii=False, separators=(",", ":"),
        sort_keys=True).encode("utf-8"))


def bound_payload(spec: ProbeSpec, payload: ProbePayload) -> ProbePayload:
    """Fit list-valued facts by dropping complete trailing rows only.

    Probe runners observe registries whose cardinality can grow after release.
    A fixed row count is therefore not a byte bound.  This generic structural
    pass preserves every scalar and every admitted row in full, and marks the
    resulting capsule partial instead of turning a large healthy registry into
    an unavailable probe or slicing JSON.
    """

    if _encoded_facts_size(payload.facts) <= spec.max_bytes:
        return payload
    facts = {
        key: list(value) if isinstance(value, list) else value
        for key, value in payload.facts.items()
    }
    trimmed = False
    while _encoded_facts_size(facts) > spec.max_bytes:
        candidates = [
            key for key, value in facts.items()
            if isinstance(value, list) and value
        ]
        if not candidates:
            raise ValueError("probe scalar facts exceed the declared maximum")
        # Remove from the largest encoded list first.  Ties use the schema key,
        # so identical input always yields identical prefixes.
        key = max(candidates, key=lambda item: (
            _encoded_facts_size({item: facts[item]}), item))
        facts[key].pop()
        trimmed = True
    redactions = tuple(payload.redactions)
    if trimmed and "bounded_registry_tail" not in redactions:
        redactions = (*redactions, "bounded_registry_tail")
    return ProbePayload(
        facts=facts,
        redactions=redactions,
        partial=bool(payload.partial or trimmed),
        status=("partial" if trimmed and payload.status in {None, "ok"}
                else payload.status),
    )


def _run_isolated(spec: ProbeSpec, context: ProbeContext,
                  timeout_s: float) -> ProbePayload:
    """Run a registered probe in a disposable, killable interpreter.

    Scheduler admission controls concurrency; this process boundary controls
    execution time.  A blocked SQLite/filesystem call therefore cannot retain
    a Telegram daemon thread or an HTTP Tutor gate after the declared probe
    deadline.  Only the closed probe identifier and authenticated principal
    fields cross the local stdin pipe.
    """

    timeout = float(timeout_s)
    if timeout <= 0:
        raise TimeoutError("Tutor probe deadline exhausted")
    request = {
        "probe_id": spec.probe_id,
        "principal": {
            "user_id": context.principal.user_id,
            "actor": context.principal.actor,
            "audience": context.principal.audience,
            "channel": context.principal.channel,
            "conversation_id": context.principal.conversation_id,
        },
        "lang": context.lang,
        "timeout_s": timeout,
    }
    worker_env = dict(os.environ)
    package_root = str(Path(__file__).resolve().parent.parent)
    inherited_path = worker_env.get("PYTHONPATH", "")
    worker_env["PYTHONPATH"] = (
        package_root if not inherited_path else
        package_root + os.pathsep + inherited_path
    )
    try:
        completed = subprocess.run(
            [sys.executable, "-m", f"{__package__}.probe_worker"],
            input=json.dumps(request, ensure_ascii=False),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=worker_env,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("Tutor probe execution timed out") from exc
    if completed.returncode != 0:
        raise RuntimeError("Tutor probe worker failed")
    try:
        response = json.loads(completed.stdout)
        payload = ProbePayload(
            facts=dict(response["facts"]),
            redactions=tuple(response.get("redactions") or ()),
            partial=bool(response.get("partial")),
            status=response.get("status"),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("Tutor probe worker returned invalid data") from exc
    _validate_payload(spec, payload)
    return payload


def _capsule(spec: ProbeSpec, payload: ProbePayload, now: float,
             *, status: str | None = None) -> ObservationCapsule:
    resolved = status or payload.status or (
        "partial" if payload.partial else "ok")
    if resolved not in _STATUSES:
        raise ValueError("invalid probe status")
    return ObservationCapsule(
        probe_id=spec.probe_id,
        observed_at=_iso(now),
        fresh_until=_iso(now + spec.ttl_s),
        status=resolved,  # type: ignore[arg-type]
        facts=payload.facts,
        redactions=tuple(payload.redactions),
        source_version=_implementation_version(spec),
    )


def _unavailable(spec: ProbeSpec, now: float, reason: str,
                 stale: ObservationCapsule | None = None
                 ) -> ObservationCapsule:
    if stale is not None:
        return ObservationCapsule(
            probe_id=stale.probe_id,
            observed_at=stale.observed_at,
            fresh_until=stale.fresh_until,
            status="stale",
            facts=stale.facts,
            redactions=tuple((*stale.redactions, reason)),
            source_version=stale.source_version,
        )
    return ObservationCapsule(
        probe_id=spec.probe_id,
        observed_at=_iso(now),
        fresh_until=_iso(now),
        status="unavailable",
        facts={},
        redactions=(reason,),
        source_version=_implementation_version(spec),
    )


def execute_probe_refs(
        refs: tuple[str, ...], *, principal: TutorPrincipal, lang: str,
        injected: dict[str, object] | None = None,
        deadline_at: float = 0.0,
        ) -> tuple[ObservationCapsule, ...]:
    """Run a bounded set of source-attested probes through the scheduler."""

    from .deadline import new_deadline, phase_deadline, remaining

    outer_deadline = deadline_at or new_deadline()
    context = ProbeContext(
        principal=principal, lang=lang, deadline_at=outer_deadline)
    unique = tuple(dict.fromkeys(str(ref) for ref in refs if ref))[:4]
    out: list[ObservationCapsule] = []
    from executor_scheduler import invoke_scheduled

    for probe_id in unique:
        remaining(outer_deadline)
        now = time.time()
        spec = _REGISTRY.get(probe_id)
        if spec is None:
            out.append(ObservationCapsule(
                probe_id=probe_id,
                observed_at=_iso(now),
                fresh_until=_iso(now),
                status="unavailable",
                facts={},
                redactions=("unknown_probe_ref",),
                source_version="unregistered",
            ))
            continue
        if (_AUDIENCE_RANK.get(principal.audience, -1)
                < _AUDIENCE_RANK.get(spec.audience, 99)):
            out.append(_unavailable(spec, now, "audience_mismatch"))
            continue
        fresh, stale = _cached(spec, context, now)
        if fresh is not None:
            out.append(fresh)
            continue

        call_deadline = phase_deadline(outer_deadline, spec.timeout_s)

        def _run(_spec=spec, _deadline=call_deadline):
            probe_context = ProbeContext(
                principal=principal, lang=lang, deadline_at=_deadline)
            payload = _run_isolated(
                _spec, probe_context, remaining(_deadline))
            return {"ok": True, "payload": payload}

        try:
            # The injection hook is accepted only by exact registered ID and
            # cannot add a probe or bypass audience checks.
            if injected and probe_id in injected:
                injected_value = injected[probe_id]
                if isinstance(injected_value, ProbePayload):
                    payload = injected_value
                elif isinstance(injected_value, dict):
                    payload = ProbePayload(dict(injected_value))
                else:
                    raise TypeError("invalid injected probe payload")
                payload = bound_payload(spec, payload)
                _validate_payload(spec, payload)
            else:
                result = invoke_scheduled(
                    spec, _run,
                    admission_timeout_s=remaining(call_deadline),
                )
                if not isinstance(result, dict) or not result.get("ok"):
                    raise RuntimeError("probe scheduler rejected the result")
                payload = result["payload"]
            remaining(call_deadline)
            observed = time.time()
            capsule = _capsule(spec, payload, observed)
            _store(spec, context, capsule, observed + spec.ttl_s)
            out.append(capsule)
        except Exception as exc:  # fail-soft, but never silently healthy
            log.warning("Tutor probe %s unavailable: %s", spec.probe_id,
                        type(exc).__name__)
            reason = (
                "timeout" if isinstance(exc, TimeoutError) else
                "invalid_payload" if isinstance(exc, (TypeError, ValueError)) else
                "runner_unavailable"
            )
            out.append(_unavailable(
                spec, time.time(), reason, stale))
    order = {probe_id: index for index, probe_id in enumerate(unique)}
    out.sort(key=lambda capsule: order.get(capsule.probe_id, len(order)))
    return tuple(out)


def render_capsules(capsules: tuple[ObservationCapsule, ...]) -> str:
    """Bounded evidence block for the final composer, never for planning."""

    return "\n\n".join(
        "[LIVE_CAPSULE] " + json.dumps(
            capsule.as_dict(), ensure_ascii=False, separators=(",", ":"),
            sort_keys=True) + " [/LIVE_CAPSULE]"
        for capsule in capsules
    )


def compact_for_composition(
        capsules: tuple[ObservationCapsule, ...],
        max_chars: int = _COMPOSITION_LIVE_MAX_CHARS,
        ) -> tuple[ObservationCapsule, ...]:
    """Bound the complete live block without ever cutting a JSON value."""

    limit = max(1000, int(max_chars))
    working = [replace(capsule, facts={
        key: list(value) if isinstance(value, list) else value
        for key, value in capsule.facts.items()
    }) for capsule in capsules]
    changed: set[int] = set()
    while len(render_capsules(tuple(working))) > limit:
        choices: list[tuple[int, int, str]] = []
        for index, capsule in enumerate(working):
            for key, value in capsule.facts.items():
                if isinstance(value, list) and value:
                    choices.append((
                        len(json.dumps(value, ensure_ascii=False,
                                       separators=(",", ":"))),
                        index,
                        key,
                    ))
        if not choices:
            break
        _size, index, key = max(choices, key=lambda row: (row[0], row[2]))
        facts = dict(working[index].facts)
        rows = list(facts[key])
        rows.pop()
        facts[key] = rows
        working[index] = replace(working[index], facts=facts)
        changed.add(index)
    for index in changed:
        capsule = working[index]
        redactions = tuple(capsule.redactions)
        if "composition_budget_tail" not in redactions:
            redactions = (*redactions, "composition_budget_tail")
        working[index] = replace(
            capsule,
            status=("partial" if capsule.status == "ok" else capsule.status),
            redactions=redactions,
        )
    return tuple(working)


def capsules_are_fresh(capsules: tuple[ObservationCapsule, ...], *,
                       now: float | None = None) -> bool:
    """True only while every usable point-in-time observation is unexpired."""

    epoch = time.time() if now is None else float(now)
    for capsule in capsules:
        if capsule.status not in {"ok", "partial"}:
            continue
        try:
            expires = datetime.fromisoformat(
                capsule.fresh_until.replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError):
            return False
        if expires < epoch:
            return False
    return True


def _clear_cache_for_tests() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()
