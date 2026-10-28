"""Declarative mapping from Metnos workloads to logical LLM requests.

Callers select a workload and retain only operation-owned constraints
(``max_tokens``, deadline, grammar, and tool schema).  Provider, model and
generation policy are resolved centrally.  The deterministic ``fast`` tier
also carries one router-owned level; it is never a per-call decoding override.
"""
from __future__ import annotations

from dataclasses import dataclass


class TierRequest(str):
    """An immutable tier name carrying its local workload and fast level.

    ``str`` compatibility keeps generic adapters simple.  ``LLMRouter`` reads
    ``level`` before resolving the provider, so a caller never manages model
    parameters itself.
    """

    level: str | None
    workload: str | None

    def __new__(cls, tier: str, level: str | None = None, *, workload: str | None = None):
        value = str.__new__(cls, tier)
        object.__setattr__(value, "level", level)
        object.__setattr__(value, "workload", workload)
        return value

    def __setattr__(self, name, value):
        raise AttributeError("TierRequest is immutable")

    def __delattr__(self, name):
        raise AttributeError("TierRequest is immutable")


@dataclass(frozen=True, slots=True)
class WorkloadContract:
    tier: str
    level: str | None
    family: str
    output_constraint: str


WORKLOADS: dict[str, WorkloadContract] = {
    # Micro decisions: bounded labels or tiny JSON.
    "intent.extract": WorkloadContract("fast", "micro", "micro_decision", "json"),
    "temporal.interpret": WorkloadContract("fast", "micro", "micro_decision", "json"),
    "intent.route_relation": WorkloadContract("fast", "micro", "micro_decision", "label"),
    "dialog.filler": WorkloadContract("fast", "micro", "micro_decision", "text"),
    "sites.goal_reduce": WorkloadContract("fast", "micro", "micro_decision", "json"),
    "sites.action_reduce": WorkloadContract("fast", "micro", "micro_decision", "json"),
    "sites.cookie_resolution": WorkloadContract("fast", "micro", "micro_decision", "json"),
    "sites.challenge_vision": WorkloadContract("frontier", None, "frontier", "json"),
    "images.search_rerank": WorkloadContract("fast", "micro", "micro_decision", "json"),
    "tutor.mode": WorkloadContract("fast", "micro", "micro_decision", "label"),
    "tutor.obligations": WorkloadContract("fast", "micro", "micro_decision", "json"),
    "entries.describe.map": WorkloadContract("fast", "micro", "micro_decision", "text"),
    "entries.describe.small": WorkloadContract("fast", "micro", "micro_decision", "text"),
    "entries.classify.small": WorkloadContract("fast", "micro", "micro_decision", "json"),
    "planner.routine": WorkloadContract("wise", None, "micro_decision", "json"),

    # Intermediate deterministic transforms and judgments. These are the
    # legacy ``middle`` workloads; they must not be collapsed into ``fast``.
    "entries.classify": WorkloadContract("middle", None, "procedural_exact", "json"),
    "entries.describe.medium": WorkloadContract("middle", None, "procedural_exact", "text"),
    "entries.extract": WorkloadContract("middle", None, "procedural_exact", "json"),
    "bills.extract": WorkloadContract("middle", None, "procedural_exact", "json"),
    "vaglio.judge": WorkloadContract("middle", None, "procedural_exact", "json"),
    "alignment.fit": WorkloadContract("middle", None, "procedural_exact", "json"),
    "admin.intent_translate": WorkloadContract("middle", None, "procedural_exact", "json"),
    "manifest.normalize": WorkloadContract("middle", None, "procedural_exact", "json"),
    "images.folder_classify": WorkloadContract("middle", None, "procedural_exact", "label"),
    "urls.rerank": WorkloadContract("middle", None, "procedural_exact", "json"),
    "synt.procedural": WorkloadContract("middle", None, "procedural_exact", "json"),
    "frontier.tool_loop": WorkloadContract("middle", None, "procedural_exact", "tools"),

    # Faithful high-context transforms without divergent decoding.
    "entries.describe": WorkloadContract("wise", None, "high_fidelity_exact", "text"),
    "translation.i18n": WorkloadContract("wise", None, "high_fidelity_exact", "text"),
    "translation.detection": WorkloadContract("wise", None, "high_fidelity_exact", "json"),
    "skill.description": WorkloadContract("wise", None, "high_fidelity_exact", "json"),
    "synt.semantic_verify": WorkloadContract("wise", None, "high_fidelity_exact", "json"),
    "executor.birth.semantic_review": WorkloadContract(
        "wise", None, "high_fidelity_exact", "json",
    ),
    "executor.birth.semantic_review.frontier": WorkloadContract(
        "frontier", None, "frontier", "json",
    ),
    "executor.birth.failure_review": WorkloadContract(
        "frontier", None, "frontier", "json",
    ),
    "tutor.compose": WorkloadContract("wise", None, "high_fidelity_exact", "text"),
    "planner.grammar": WorkloadContract("wise", None, "high_fidelity_exact", "grammar"),
    # Durable image preset: exact structured extraction and synthesis.  The
    # preset stores these workload names, never a tier or a provider binding.
    "durable.images.extract_questions": WorkloadContract("wise", None, "high_fidelity_exact", "json"),
    "durable.images.deduplicate": WorkloadContract("wise", None, "high_fidelity_exact", "json"),
    "durable.images.answer": WorkloadContract("wise", None, "high_fidelity_exact", "json"),
    "durable.images.validate": WorkloadContract("wise", None, "high_fidelity_exact", "json"),
    "durable.images.reduce_solutions": WorkloadContract("wise", None, "high_fidelity_exact", "json"),
    "durable.images.reduce_notes": WorkloadContract("wise", None, "high_fidelity_exact", "json"),
    "durable.images.reduce_formulae": WorkloadContract("wise", None, "high_fidelity_exact", "json"),
    "durable.images.assemble": WorkloadContract("wise", None, "high_fidelity_exact", "json"),

    # Deliberate planning and synthesis.
    "planner.deliberate": WorkloadContract("wise", None, "deliberate", "json"),
    "synt.generate": WorkloadContract("wise", None, "deliberate", "tools"),
    "synt.birth_tests": WorkloadContract("wise", None, "deliberate", "tools"),
    "synt.multistage": WorkloadContract("wise", None, "deliberate", "json"),

    # Divergent proposal and editorial generation.
    "telos.lens": WorkloadContract("creative", None, "divergent", "grammar_optional"),
    "promotion.commentary": WorkloadContract("creative", None, "divergent", "text"),
    "manifest.refactor": WorkloadContract("creative", None, "divergent", "json"),
    "synt.description": WorkloadContract("creative", None, "divergent", "json"),

    # Explicit maximum-capability escalation.
    "frontier.consult": WorkloadContract("frontier", None, "frontier", "caller_owned"),
    # Collection-only, when Frontier is configured and Sites has not opted
    # out. Route choice uses controls; observation also reads visible content.
    "sites.collection_route": WorkloadContract("frontier", None, "frontier", "json"),
    "sites.collection_observe": WorkloadContract("frontier", None, "frontier", "json"),
}


def tier_for(workload: str) -> TierRequest:
    """Return the registered request; unknown workloads fail closed."""

    try:
        contract = WORKLOADS[workload]
        return TierRequest(contract.tier, contract.level, workload=workload)
    except KeyError as exc:
        raise ValueError(f"unknown LLM workload: {workload!r}") from exc


def workload_for_request(request: str | None) -> str | None:
    """Read only registered, coherent local declarations; never infer a role."""
    if not isinstance(request, TierRequest) or request.workload is None:
        return None
    contract = WORKLOADS.get(request.workload) if isinstance(request.workload, str) else None
    if contract is None or (str(request), request.level) != (contract.tier, contract.level):
        raise ValueError("invalid LLM workload declaration")
    return request.workload


def contracts_by_tier() -> dict[str, tuple[str, ...]]:
    """Stable projection used by administration and documentation tests."""

    grouped: dict[str, list[str]] = {}
    for name, contract in WORKLOADS.items():
        grouped.setdefault(contract.tier, []).append(name)
    return {
        tier: tuple(sorted(names)) for tier, names in grouped.items()
    }
