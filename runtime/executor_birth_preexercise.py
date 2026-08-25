"""Inactive, fail-closed RM-0008 F5 preexercise eligibility policy.

The policy is intentionally a pure decision function.  It is not connected to
the loader, router, publisher, or durable workloads and therefore cannot make
preexercise productive before the real-admission threshold is certified.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping


class PreexerciseError(ValueError):
    __slots__ = ("code", "detail")

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


class PreexerciseDenial(str, Enum):
    NOT_SYNTHESIZED = "preexercise_not_synthesized"
    NOT_READ_ONLY = "preexercise_not_read_only"
    CAPABILITY_UNKNOWN = "preexercise_capability_unknown"
    CAPABILITY_FORBIDDEN = "preexercise_capability_forbidden"
    REAL_NETWORK = "preexercise_real_network_forbidden"
    SECRET_ACCESS = "preexercise_secret_access_forbidden"
    GENERIC_EXECUTION = "preexercise_generic_execution_forbidden"
    USER_DIALOG = "preexercise_user_dialog_forbidden"
    PERSONAL_PATH = "preexercise_personal_path_forbidden"
    UNBOUNDED_SCOPE = "preexercise_unbounded_scope_forbidden"
    SECRET_EGRESS = "preexercise_secret_egress_forbidden"


@dataclass(frozen=True, slots=True)
class PreexerciseFacts:
    """Core-derived facts; none of these values grants authority by itself."""

    synthesized_origin: bool
    read_only: bool
    capabilities: tuple[str, ...]
    real_network: bool = False
    secret_access: bool = False
    generic_execution: bool = False
    user_dialog: bool = False
    personal_or_sensitive_paths: bool = False
    unbounded_scope: bool = False
    secret_egress_possible: bool = False

    def __post_init__(self) -> None:
        for field in (
            "synthesized_origin", "read_only", "real_network", "secret_access",
            "generic_execution", "user_dialog", "personal_or_sensitive_paths",
            "unbounded_scope", "secret_egress_possible",
        ):
            if type(getattr(self, field)) is not bool:
                raise PreexerciseError("preexercise_facts_invalid", field)
        if not isinstance(self.capabilities, tuple) or any(
            not isinstance(item, str) or not item or item != item.strip()
            or "\x00" in item or len(item.encode("utf-8")) > 128
            for item in self.capabilities
        ):
            raise PreexerciseError("preexercise_facts_invalid", "capabilities")
        if len(self.capabilities) != len(set(self.capabilities)):
            raise PreexerciseError("preexercise_facts_invalid", "duplicate capability")


@dataclass(frozen=True, slots=True)
class PreexercisePolicy:
    """Versioned core allowlist: absence means ineligible, including new names."""

    version: str
    capability_eligibility: Mapping[str, bool]

    def __post_init__(self) -> None:
        if (not isinstance(self.version, str) or not self.version
                or self.version != self.version.strip() or "\x00" in self.version):
            raise PreexerciseError("preexercise_policy_invalid", "version")
        if not isinstance(self.capability_eligibility, Mapping):
            raise PreexerciseError("preexercise_policy_invalid", "capabilities")
        normalized: dict[str, bool] = {}
        for name, eligible in self.capability_eligibility.items():
            if (not isinstance(name, str) or not name or name != name.strip()
                    or "\x00" in name or type(eligible) is not bool):
                raise PreexerciseError("preexercise_policy_invalid", "capability entry")
            normalized[name] = eligible
        object.__setattr__(self, "capability_eligibility", MappingProxyType(normalized))


@dataclass(frozen=True, slots=True)
class PreexerciseDecision:
    eligible: bool
    policy_version: str
    denial: PreexerciseDenial | None
    detail: str | None


def decide_preexercise(
    facts: PreexerciseFacts, *, policy: PreexercisePolicy,
) -> PreexerciseDecision:
    """Return one deterministic first-denial decision under a closed policy."""
    if not isinstance(facts, PreexerciseFacts):
        raise PreexerciseError("preexercise_facts_invalid")
    if not isinstance(policy, PreexercisePolicy):
        raise PreexerciseError("preexercise_policy_invalid")

    scalar_denials = (
        (not facts.synthesized_origin, PreexerciseDenial.NOT_SYNTHESIZED),
        (not facts.read_only, PreexerciseDenial.NOT_READ_ONLY),
        (facts.real_network, PreexerciseDenial.REAL_NETWORK),
        (facts.secret_access, PreexerciseDenial.SECRET_ACCESS),
        (facts.generic_execution, PreexerciseDenial.GENERIC_EXECUTION),
        (facts.user_dialog, PreexerciseDenial.USER_DIALOG),
        (facts.personal_or_sensitive_paths, PreexerciseDenial.PERSONAL_PATH),
        (facts.unbounded_scope, PreexerciseDenial.UNBOUNDED_SCOPE),
        (facts.secret_egress_possible, PreexerciseDenial.SECRET_EGRESS),
    )
    for denied, reason in scalar_denials:
        if denied:
            return PreexerciseDecision(False, policy.version, reason, None)

    for capability in sorted(facts.capabilities, key=str.encode):
        eligible = policy.capability_eligibility.get(capability)
        if eligible is None:
            return PreexerciseDecision(
                False, policy.version, PreexerciseDenial.CAPABILITY_UNKNOWN, capability,
            )
        if not eligible:
            return PreexerciseDecision(
                False, policy.version, PreexerciseDenial.CAPABILITY_FORBIDDEN, capability,
            )
    return PreexerciseDecision(True, policy.version, None, None)
