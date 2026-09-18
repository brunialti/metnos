"""Host-owned model lifecycle adapters, independent of workload scheduling.

LRE verifies frozen binding facts and admission; Virt resolves the provider and
owns readiness. Remote and externally supervised providers retain their existing
transport lifecycle. A managed service may keep a stable public endpoint while
changing its internal replicas, without changing the admitted model identity.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ModelResource:
    """A resource claim plus host-owned binding and lifecycle operations."""

    claim: str
    binding_facts: Callable[[], dict]
    ensure_ready: Callable[..., bool]


def resolve_model_resource(kind: str, *, max_output_tokens: int,
                           max_calls: int) -> ModelResource | None:
    """Resolve a lifecycle from trusted configuration, never from job commands.

    Only vision currently has an on-demand host launcher. Other providers are
    already externally supervised or remote; this function must not fabricate
    startup authority for them. Adding a lifecycle here requires no LRE change.
    Resolution is fresh for every admission, so idle stops and config changes
    cannot leave a process-local ready latch behind.
    """
    if kind != "vision":
        return None

    def binding_facts():
        import vlm_client

        vlm_client.reload_configuration()
        binding = vlm_client.model_binding_facts(max_tokens=max_output_tokens)
        binding["max_calls_per_attempt"] = max_calls
        return binding

    def ensure_ready(binding, *, deadline_at):
        import virt

        endpoint = urlsplit(binding["endpoint"])
        if (binding["provider"] != "llamacpp"
                or endpoint.scheme not in {"http", "https"}
                or endpoint.hostname not in {"localhost", "127.0.0.1", "::1"}
                or endpoint.username is not None or endpoint.password is not None):
            return False
        return virt.ensure_vlm_up(binding["role"], deadline_at=deadline_at)

    return ModelResource("vlm", binding_facts, ensure_ready)
