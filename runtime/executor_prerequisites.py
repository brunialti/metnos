"""Resolve prerequisite requests from signed executor declarations.

An observation may provide data, never the authority to choose another tool.
This module only resolves a declaration; ordinary admission, placement and
capability checks still govern the resulting invocation.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import logging
import re
from typing import Any


_FIELD = re.compile(r"[a-z][a-z0-9_]{0,63}")
_EXECUTOR = re.compile(r"[a-z][a-z0-9_]{1,127}")
_PLAN = re.compile(r"[a-z][a-z0-9_.-]{2,63}")
_LOG = logging.getLogger("metnos.executor_prerequisites")


def normalize_lre_plan(value: object) -> str:
    """Read an optional signed reference to an approved plan adapter."""
    if value is None:
        return ""
    if not isinstance(value, str) or not _PLAN.fullmatch(value):
        raise ValueError("lre_plan_registration_invalid")
    return value


@dataclass(frozen=True, slots=True)
class ExecutorPrerequisite:
    on_error: str
    executor: str
    # Destination argument, source object (args/result), source field.
    argument_bindings: tuple[tuple[str, str, str], ...]


def normalize_prerequisites(
    value: object, *, owner: str,
) -> tuple[ExecutorPrerequisite, ...]:
    """Reject malformed authority instead of silently ignoring a declaration."""
    if value is None:
        return ()
    if not isinstance(value, list) or not 1 <= len(value) <= 8:
        raise ValueError("prerequisites_must_be_bounded_array")
    rules = []
    seen = set()
    for raw in value:
        if not isinstance(raw, dict) or set(raw) != {
            "on_error", "executor", "arguments",
        }:
            raise ValueError("prerequisite_fields_invalid")
        error = raw["on_error"]
        target = raw["executor"]
        arguments = raw["arguments"]
        if (
            not isinstance(error, str) or not _FIELD.fullmatch(error)
            or error in seen
            or not isinstance(target, str) or not _EXECUTOR.fullmatch(target)
            or target == owner
            or not isinstance(arguments, dict) or not 1 <= len(arguments) <= 16
        ):
            raise ValueError("prerequisite_identity_invalid")
        bindings = []
        for destination, binding in arguments.items():
            if (
                not isinstance(destination, str) or not _FIELD.fullmatch(destination)
                or not isinstance(binding, dict) or set(binding) != {"source", "field"}
                or binding["source"] not in ("args", "result")
                or not isinstance(binding["field"], str)
                or not _FIELD.fullmatch(binding["field"])
            ):
                raise ValueError("prerequisite_argument_binding_invalid")
            bindings.append((destination, binding["source"], binding["field"]))
        seen.add(error)
        rules.append(ExecutorPrerequisite(error, target, tuple(sorted(bindings))))
    return tuple(rules)


def resolve_prerequisite(
    executor: object,
    arguments: Mapping[str, Any],
    observation: object,
    catalog: Mapping[str, object],
) -> tuple[object, dict[str, Any]] | None:
    """Resolve one exact prerequisite without following result-supplied commands."""
    if not isinstance(observation, Mapping) or observation.get("ok") is not False:
        return None
    signed_by = getattr(executor, "signed_by", "")
    if not isinstance(signed_by, str) or not signed_by or signed_by.startswith("("):
        return None
    for rule in getattr(executor, "prerequisites", ()):
        if not isinstance(rule, ExecutorPrerequisite):
            raise ValueError("prerequisite_declaration_not_normalized")
        if observation.get("error_class") != rule.on_error:
            continue
        target = catalog.get(rule.executor)
        signer = getattr(target, "signed_by", "")
        if (
            target is None or getattr(target, "name", None) != rule.executor
            or not isinstance(signer, str) or not signer or signer.startswith("(")
            or getattr(target, "lifecycle", None) != "active"
            or bool(getattr(target, "dormant", False))
        ):
            raise ValueError("prerequisite_executor_unavailable")
        resolved = {}
        for destination, source, field in rule.argument_bindings:
            selected = arguments if source == "args" else observation
            if field not in selected or selected[field] is None:
                raise ValueError("prerequisite_argument_unresolved")
            resolved[destination] = selected[field]
        return target, resolved
    return None


def _server_only(executor: object) -> bool:
    """Mirror the dispatch default; never infer placement from tool output."""
    placement = getattr(executor, "placement", None) or {}
    return (isinstance(placement, Mapping)
            and placement.get("scope", "server") == "server"
            and not placement.get("device_ok", False))


def admit_prerequisite(executor, arguments, observation, *, catalog_loader,
                       validate_args, guard, owner_user_id, turn_id,
                       target_device=None):
    """Admit only a signed, non-critical, registered preparation operation.

    A prerequisite cannot authorize a send, deletion, credential change or
    arbitrary nested plan. Such actions keep their ordinary explicit gates.
    Worker invocations never call this boundary, so chains cannot recurse.
    """
    if not isinstance(observation, Mapping) or observation.get("ok") is not False:
        return observation
    from capabilities import effective_capabilities
    from durable_runtime_registry import invocation_plan_adapter
    from engine.types import Framework, StepSpec
    from lre_submission import _automatic_error, _catalog_by_name, submit_automatic_lre
    from policy import CAPABILITY_REGISTRY
    from vaglio import check_executor_guard

    boundary = "catalog"
    try:
        catalog = _catalog_by_name(catalog_loader(verify=True))
        boundary = "declaration"
        resolved = resolve_prerequisite(executor, arguments, observation, catalog)
        if resolved is None:
            return observation
        target, args = resolved
        boundary = "registered_plan"
        if invocation_plan_adapter(target) is None:
            raise ValueError("prerequisite_has_no_registered_plan")
        boundary = "arguments_and_guard"
        if validate_args(args, target.args_schema) or not check_executor_guard(
                guard, target.name, args, executor=target)[0]:
            raise ValueError("prerequisite_arguments_not_admissible")
        boundary = "capabilities"
        for capability in effective_capabilities(target.capabilities, target.args_schema, args):
            spec = CAPABILITY_REGISTRY.get(capability.get("name"))
            if spec is None or spec.critical or spec.default_approval == "always":
                raise ValueError("prerequisite_needs_explicit_approval")
        # Dispatch already runs server-only readers on the server, regardless
        # of the conversational device. A server-only prerequisite must keep
        # that same location. Both contracts have passed signature checks;
        # remote-capable/device-only operations retain their original target.
        prerequisite_device = (
            "server" if _server_only(executor) and _server_only(target)
            else target_device
        )
        boundary = "submission"
        admitted = submit_automatic_lre(
            Framework(steps=[StepSpec(target.name, args)]), catalog=catalog,
            owner_user_id=owner_user_id, turn_id=turn_id, target_device=prerequisite_device,
        )
        if admitted is None:
            raise ValueError("prerequisite_not_admitted")
        return admitted
    except (ValueError, TypeError, OSError, RuntimeError) as exc:
        # No arguments, paths or exception text: diagnostic boundaries are
        # fixed identifiers, not executor-controlled or sensitive content.
        _LOG.warning("prerequisite_rejected boundary=%s exception_type=%s",
                     boundary, type(exc).__name__)
        return _automatic_error("ERR_LRE_EXECUTOR_CONTRACT_UNSUPPORTED",
                                error_class="contract_unsupported")


__all__ = [
    "ExecutorPrerequisite", "normalize_prerequisites", "resolve_prerequisite",
    "normalize_lre_plan", "admit_prerequisite",
]
