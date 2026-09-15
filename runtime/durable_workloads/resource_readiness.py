"""Host-owned readiness for an already admitted local model resource.

The execution bridge calls this only after acquiring a lease and verifying the
runner. Invocation arguments select neither an executable nor model artifacts;
the normal Virt lifecycle reads the administrator-owned startup environment.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from .schema import MAX_SNAPSHOT_JSON_BYTES, digest_json


class ModelResourceChanged(ValueError):
    """The available model configuration differs from the admitted binding."""


class ModelResourceUnavailable(RuntimeError):
    """The normal host lifecycle could not make its configured resource ready."""


def ensure_model_resource(executor, args, contract, context, device_id, *, deadline_at):
    """Prepare local vision only when this invocation may actually call it.

    A multi-phase executor can share one frozen model contract while granting
    model access only to some phases. Zero-call phases must not start a model.
    Remote devices remain responsible for their own local resource lifecycle.
    """
    if device_id not in {None, "server"} or contract.model_kind != "vision":
        return
    from capabilities import effective_capabilities

    capabilities = effective_capabilities(
        getattr(executor, "capabilities", ()) or (),
        getattr(executor, "args_schema", {}) or {}, args,
    )
    if not any(cap.get("name") == "llm:local" for cap in capabilities):
        return
    if dict(context.resource_claims).get("vlm", 0) < 1:
        raise ModelResourceChanged("vision resource was not claimed")

    import vlm_client
    from virt import ensure_vlm_up

    def verified_binding():
        # The call limits belong to the frozen runner policy (OCR may use a
        # different output limit from captions). Endpoint, model, provider,
        # image limit and timeout still come from the current configuration.
        try:
            vlm_client.reload_configuration()
            binding = vlm_client.model_binding_facts(max_tokens=contract.model_max_output_tokens)
        except (OSError, TypeError, ValueError) as exc:
            raise ModelResourceChanged("local vision configuration is invalid") from exc
        binding["max_calls_per_attempt"] = contract.model_max_calls
        digest = digest_json("durable-executor-model-binding", binding,
                             max_bytes=MAX_SNAPSHOT_JSON_BYTES)
        if digest != contract.model_binding_digest:
            raise ModelResourceChanged("local vision binding changed")
        endpoint = urlsplit(binding["endpoint"])
        if (binding["provider"] != "llamacpp"
                or endpoint.scheme not in {"http", "https"}
                or endpoint.hostname not in {"localhost", "127.0.0.1", "::1"}
                or endpoint.username is not None or endpoint.password is not None):
            raise ModelResourceUnavailable("local vision lifecycle is not configured")
        return binding

    binding = verified_binding()
    try:
        ready = ensure_vlm_up(binding["role"], deadline_at=deadline_at)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ModelResourceUnavailable("local vision startup is unavailable") from exc
    if not ready:
        raise ModelResourceUnavailable("local vision model is not ready")
    verified_binding()
