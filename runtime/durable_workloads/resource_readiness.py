"""Host-owned readiness for an already admitted local model resource.

The execution bridge calls this only after acquiring a lease and verifying the
runner. Invocation arguments select neither an executable nor model artifacts;
the normal Virt lifecycle reads the administrator-owned startup environment.
"""
from __future__ import annotations

from .schema import MAX_SNAPSHOT_JSON_BYTES, digest_json


class ModelResourceChanged(ValueError):
    """The available model configuration differs from the admitted binding."""


class ModelResourceUnavailable(RuntimeError):
    """The normal host lifecycle could not make its configured resource ready."""


def ensure_model_resource(executor, args, contract, context, device_id, *, deadline_at):
    """Prepare a host-managed resource only when this invocation may call it.

    A multi-phase executor can share one frozen model contract while granting
    model access only to some phases. Zero-call phases must not start a model.
    Remote devices remain responsible for their own local resource lifecycle.
    """
    if device_id not in {None, "server"} or contract.model_kind is None:
        return
    from capabilities import effective_capabilities

    capabilities = effective_capabilities(
        getattr(executor, "capabilities", ()) or (),
        getattr(executor, "args_schema", {}) or {}, args,
    )
    if not any(cap.get("name") == "llm:local" for cap in capabilities):
        return
    from virt.resources import resolve_model_resource

    resource = resolve_model_resource(
        contract.model_kind, max_output_tokens=contract.model_max_output_tokens,
        max_calls=contract.model_max_calls,
    )
    if resource is None:
        return
    if dict(context.resource_claims).get(resource.claim, 0) < 1:
        raise ModelResourceChanged("model resource was not claimed")

    def verify():
        try:
            binding = resource.binding_facts()
        except (OSError, TypeError, ValueError) as exc:
            raise ModelResourceChanged("model configuration is invalid") from exc
        digest = digest_json("durable-executor-model-binding", binding,
                             max_bytes=MAX_SNAPSHOT_JSON_BYTES)
        if digest != contract.model_binding_digest:
            raise ModelResourceChanged("model binding changed")
        return binding

    binding = verify()
    try:
        ready = resource.ensure_ready(binding, deadline_at=deadline_at)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ModelResourceUnavailable("model startup is unavailable") from exc
    if not ready:
        raise ModelResourceUnavailable("model is not ready")
    verify()
