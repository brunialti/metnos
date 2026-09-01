"""Portable RM-0008 service-catalog schema and single topology source.

This module is deliberately a leaf: importing it performs no filesystem or
platform operation.  Productive loading imports the distribution verifier
locally so the distribution -> preflight -> maintenance inventory import path
cannot form a cycle.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Iterable, Mapping, NamedTuple


CATALOG_PATH_V1 = "deployment/executor-birth-service-catalog-v1.json"
CATALOG_ID_DOMAIN = b"metnos.executor-birth.service-catalog/v1\0"
SERVICE_COVERAGE_DOMAIN = b"metnos.executor-birth.service-coverage/v1\0"
SYSTEMD_FRAGMENT_DOMAIN = b"metnos.executor-birth.systemd-fragment/v1\0"
TARGET_EXECUTABLE_DOMAIN = b"metnos.executor-birth.target-executable/v1\0"
MAX_CATALOG_BYTES = 256 * 1024
MAX_UNIT_FRAGMENT_BYTES = 256 * 1024
MAX_RELATIVE_PATH_COMPONENTS_V1 = 32
ADMINISTRATIVE_ADAPTER_PATH_V1 = (
    "/usr/libexec/metnos/executor-birth-v1/preflight.py"
)
# The product's private runtime root, mirrored from the administrative
# preflight, which owns it. Every gated unit must declare it writable: the gate
# each unit runs before its payload verifies signatures through openssl, which
# needs a temporary directory there, and a hardened unit mounts the hierarchy
# read-only.
RUNTIME_ROOT_TEXT_V1 = "/run/metnos-executor-birth-v1"

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_ENTRY_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")
_UNIT_RE = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9_.@-]*\.(?:service|timer|target)\Z"
)
_MODULE_RE = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*){0,31}\Z"
)
_ENVIRONMENT_NAME_RE = re.compile(r"[A-Z_][A-Z0-9_]{0,127}\Z")
_ACCOUNT_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}\Z")
_INTEGER_RE = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_DURATION_RE = re.compile(r"(?:0|[1-9][0-9]*)(?:us|ms|s|min|h|d|w)\Z")
_SAFE_TOKEN_RE = re.compile(r"!?[A-Za-z0-9_./:@+=,-]+\Z")

_CATALOG_KEYS = frozenset({
    "schema_version", "catalog_id", "entries", "legacy_bindings",
})
_ENTRY_KEYS = frozenset({
    "entry_id", "unit_name", "external_unit_name", "adapter_path", "class",
    "scope", "execution_kind", "target_executable", "target_executable_hash",
    "python_module", "target_args", "target_working_directory",
    "target_environment", "timer_target", "unit_spec", "requires_preflight",
    "readiness_owner",
})
_LEGACY_KEYS = frozenset({
    "legacy_id", "entry_id", "kind", "scope", "locator", "disposition",
})
_ENVIRONMENT_KEYS = frozenset({"name", "value"})
_UNIT_SPEC_KEYS = frozenset({"fragment_hash", "directives"})
_DIRECTIVE_KEYS = frozenset({"section", "name", "value_type", "values"})

_CLASSES = frozenset({
    "gated_service", "gated_timer", "stop_only", "target",
    "external_dependency", "gated_entrypoint",
})
_EXECUTION_KINDS = frozenset({
    "none", "python_module", "native_executable", "systemctl_stop",
})
_LEGACY_KINDS = frozenset({
    "user_unit", "system_unit", "script", "python_module", "powershell",
})
_LEGACY_SCOPES = frozenset({"user", "system", "repository", "installed"})
_SECTION_ORDER = MappingProxyType({
    "Unit": 0, "Service": 1, "Timer": 2, "Install": 3,
})
_DIRECTIVE_TYPES = MappingProxyType({
    ("Unit", "Description"): "scalar",
    ("Unit", "Documentation"): "scalar",
    ("Unit", "DefaultDependencies"): "boolean",
    ("Unit", "Requires"): "unit_list",
    ("Unit", "Wants"): "unit_list",
    ("Unit", "BindsTo"): "unit_list",
    ("Unit", "After"): "unit_list",
    ("Unit", "Before"): "unit_list",
    ("Unit", "PartOf"): "unit_list",
    ("Unit", "OnFailure"): "unit_list",
    ("Unit", "StartLimitIntervalSec"): "duration",
    ("Unit", "StartLimitBurst"): "integer",
    ("Service", "Type"): "scalar",
    ("Service", "User"): "scalar",
    ("Service", "Group"): "scalar",
    ("Service", "SupplementaryGroups"): "scalar",
    ("Service", "ExecStartPre"): "argv",
    ("Service", "ExecStart"): "argv",
    ("Service", "ExecStop"): "argv",
    ("Service", "Restart"): "scalar",
    ("Service", "RestartSec"): "duration",
    ("Service", "TimeoutStartSec"): "duration",
    ("Service", "TimeoutStopSec"): "duration",
    ("Service", "WorkingDirectory"): "path_list",
    ("Service", "Environment"): "environment",
    ("Service", "NoNewPrivileges"): "boolean",
    ("Service", "PrivateTmp"): "boolean",
    ("Service", "ProtectSystem"): "scalar",
    ("Service", "ProtectHome"): "scalar",
    ("Service", "ReadWritePaths"): "path_list",
    ("Service", "CapabilityBoundingSet"): "scalar",
    ("Service", "AmbientCapabilities"): "scalar",
    ("Service", "KillMode"): "scalar",
    ("Service", "KillSignal"): "scalar",
    ("Service", "SuccessExitStatus"): "scalar",
    ("Service", "RemainAfterExit"): "boolean",
    ("Service", "UMask"): "scalar",
    ("Service", "NotifyAccess"): "scalar",
    ("Service", "WatchdogSec"): "duration",
    ("Service", "Delegate"): "boolean",
    ("Service", "DelegateSubgroup"): "scalar",
    ("Service", "ProtectKernelTunables"): "boolean",
    ("Service", "ProtectKernelModules"): "boolean",
    ("Service", "ProtectControlGroups"): "boolean",
    ("Service", "RestrictNamespaces"): "boolean",
    ("Service", "RestrictRealtime"): "boolean",
    ("Service", "RestrictAddressFamilies"): "scalar",
    ("Service", "LockPersonality"): "boolean",
    ("Service", "MemoryDenyWriteExecute"): "boolean",
    ("Service", "SystemCallArchitectures"): "scalar",
    ("Service", "MemoryAccounting"): "boolean",
    ("Service", "MemoryHigh"): "scalar",
    ("Service", "MemoryMax"): "scalar",
    ("Service", "TasksAccounting"): "boolean",
    ("Service", "TasksMax"): "integer",
    ("Service", "Nice"): "integer",
    ("Service", "LimitNOFILE"): "integer",
    ("Service", "StandardOutput"): "scalar",
    ("Service", "StandardError"): "scalar",
    ("Service", "SyslogIdentifier"): "scalar",
    ("Timer", "OnBootSec"): "duration",
    ("Timer", "OnActiveSec"): "duration",
    ("Timer", "OnUnitActiveSec"): "duration",
    ("Timer", "OnCalendar"): "scalar",
    ("Timer", "RandomizedDelaySec"): "duration",
    ("Timer", "Persistent"): "boolean",
    ("Timer", "AccuracySec"): "duration",
    ("Timer", "Unit"): "unit_list",
    ("Install", "WantedBy"): "unit_list",
    ("Install", "RequiredBy"): "unit_list",
})


class ServiceCatalogError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


class LegacySourceBindingV1(NamedTuple):
    legacy_id: str
    kind: str
    scope: str
    locator: str


class SourceDirectiveRecipeV1(NamedTuple):
    section: str
    name: str
    values: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SourceTargetRecipeV1:
    execution_kind: str
    target_executable: str | None
    python_module: str | None
    target_args: tuple[str, ...] = ()
    target_working_directory: str | None = None
    target_environment: tuple[tuple[str, str], ...] = ()


_NO_TARGET_RECIPE = SourceTargetRecipeV1("none", None, None)


@dataclass(frozen=True, slots=True)
class _SourceCompileContextV1:
    """Typed values resolved by B3 from one authenticated descriptor."""

    installation_root: str
    python_executable: str
    service_user: str
    service_gid: int
    service_supplementary_gids: tuple[int, ...]
    service_home: str
    systemctl_executable: str
    target_hashes: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class ServiceSourceEntryV1:
    """Complete static recipe; B3 may only resolve typed placeholders/hashes."""

    entry_id: str
    class_name: str
    unit_name: str | None = None
    external_unit_name: str | None = None
    timer_target: str | None = None
    readiness_owner: bool = False
    legacy_bindings: tuple[LegacySourceBindingV1, ...] = ()
    target_recipe: SourceTargetRecipeV1 = _NO_TARGET_RECIPE
    unit_recipe: tuple[SourceDirectiveRecipeV1, ...] = ()


@dataclass(frozen=True, slots=True)
class ServiceDirectiveV1:
    section: str
    name: str
    value_type: str
    values: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ServiceUnitSpecV1:
    fragment_hash: str
    directives: tuple[ServiceDirectiveV1, ...]


@dataclass(frozen=True, slots=True)
class ServiceEnvironmentV1:
    name: str
    value: str


@dataclass(frozen=True, slots=True)
class ServiceCatalogEntryV1:
    entry_id: str
    unit_name: str | None
    external_unit_name: str | None
    adapter_path: str | None
    class_name: str
    scope: str
    execution_kind: str
    target_executable: str | None
    target_executable_hash: str | None
    python_module: str | None
    target_args: tuple[str, ...]
    target_working_directory: str | None
    target_environment: tuple[ServiceEnvironmentV1, ...]
    timer_target: str | None
    unit_spec: ServiceUnitSpecV1 | None
    requires_preflight: bool
    readiness_owner: bool


@dataclass(frozen=True, slots=True)
class ServiceLegacyBindingV1:
    legacy_id: str
    entry_id: str
    kind: str
    scope: str
    locator: str
    disposition: str


@dataclass(frozen=True, slots=True)
class DecodedServiceCatalogV1:
    """Pure, observational decode result; it grants no launch authority."""

    catalog_id: str
    entries: tuple[ServiceCatalogEntryV1, ...]
    legacy_bindings: tuple[ServiceLegacyBindingV1, ...]
    encoded: bytes
    service_coverage_hash: str


_LOADED_CATALOG_SEAL = object()


@dataclass(frozen=True, slots=True)
class LoadedServiceCatalogV1:
    """Live reread result; still observational until G6-C verification."""

    catalog: DecodedServiceCatalogV1
    unit_fragments: tuple[tuple[str, bytes], ...]
    _seal: object

    def __post_init__(self) -> None:
        if self._seal is not _LOADED_CATALOG_SEAL:
            raise ServiceCatalogError(
                "birth_ownership_service_catalog_invalid", "loaded catalog",
            )


@dataclass(frozen=True, slots=True)
class _BuiltServiceCatalogV1:
    """Canonical catalog bytes and the unit fragments derived from them."""

    encoded: bytes
    catalog_id: str
    service_coverage_hash: str
    unit_fragments: tuple[tuple[str, bytes], ...]


def _user_unit(legacy_id: str, locator: str) -> LegacySourceBindingV1:
    return LegacySourceBindingV1(legacy_id, "user_unit", "user", locator)


def _system_unit(legacy_id: str, locator: str) -> LegacySourceBindingV1:
    return LegacySourceBindingV1(legacy_id, "system_unit", "system", locator)


def _repository_entry(
    legacy_id: str, kind: str, locator: str,
) -> LegacySourceBindingV1:
    return LegacySourceBindingV1(legacy_id, kind, "repository", locator)


def _python_target(
    module: str, *args: str, working_directory: str = "@installation_root@",
    environment: tuple[tuple[str, str], ...] = (),
) -> SourceTargetRecipeV1:
    return SourceTargetRecipeV1(
        "python_module", "@python@", module, tuple(args), working_directory,
        environment,
    )


def _native_target(
    executable: str, *args: str, working_directory: str = "@installation_root@",
    environment: tuple[tuple[str, str], ...] = (),
) -> SourceTargetRecipeV1:
    return SourceTargetRecipeV1(
        "native_executable", executable, None, tuple(args), working_directory,
        environment,
    )


def _administrative_operation(operation: str) -> SourceTargetRecipeV1:
    """Sign a closed operation name without exposing a historical installer."""

    return _python_target(
        "runtime.executor_birth_admin_operations", operation,
    )


def _target_environment(
    *items: tuple[str, str],
) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(items, key=lambda item: item[0].encode("utf-8")))


_TARGET_DATA_ENVIRONMENT_V1 = _target_environment(
    ("METNOS_USER_CONFIG", "@service_config@"),
    ("METNOS_USER_DATA", "@service_data@"),
    ("METNOS_USER_STATE", "@service_state@"),
    ("METNOS_WORKSPACE", "@service_data@/workspace"),
)


def _source_directive(
    section: str, name: str, *values: str,
) -> SourceDirectiveRecipeV1:
    return SourceDirectiveRecipeV1(section, name, tuple(values))


def _service_unit_recipe(
    entry_id: str, *, relations: tuple[SourceDirectiveRecipeV1, ...] = (),
    settings: tuple[SourceDirectiveRecipeV1, ...] = (),
    writable_paths: tuple[str, ...] = (),
) -> tuple[SourceDirectiveRecipeV1, ...]:
    return (
        _source_directive("Unit", "Description", f"Metnos controlled {entry_id}"),
        *relations,
        _source_directive(
            "Service", "ExecStartPre", "!@administrative_python@", "-I", "-S",
            ADMINISTRATIVE_ADAPTER_PATH_V1, "check", "--entry-id", entry_id,
        ),
        _source_directive(
            "Service", "ExecStart", "!@administrative_python@", "-I", "-S",
            ADMINISTRATIVE_ADAPTER_PATH_V1, "launch", "--entry-id", entry_id,
        ),
        _source_directive(
            "Service", "CapabilityBoundingSet",
            "CAP_SETGID CAP_SETPCAP CAP_SETUID",
        ),
        _source_directive("Service", "Group", "@service_gid@"),
        _source_directive("Service", "KillMode", "control-group"),
        _source_directive("Service", "NoNewPrivileges", "yes"),
        # Imposed here, with the gate itself, and never left to each unit:
        # every gated unit runs the same `check --entry-id` before its payload,
        # and that program verifies signatures through openssl in a temporary
        # directory under the product's runtime root. Without this the gate
        # dies with the generic recovery code — measured on the live G6-C cell
        # (roadmap §23.23). It is a consequence of the shape, not a per-unit
        # choice, and it grants the demoted payload nothing: the root stays
        # `0700` root-owned, so discretionary permissions still apply.
        _source_directive(
            "Service", "ReadWritePaths",
            RUNTIME_ROOT_TEXT_V1, *writable_paths,
        ),
        *settings,
        _source_directive(
            "Service", "SupplementaryGroups",
            "@service_supplementary_gids@",
        ),
        _source_directive("Service", "User", "@service_user@"),
        _source_directive("Service", "WorkingDirectory", "/"),
    )


def _service(
    entry_id: str, unit_name: str, *, readiness_owner: bool = False,
    extra_bindings: tuple[LegacySourceBindingV1, ...] = (),
    user_binding: bool = True,
    target_recipe: SourceTargetRecipeV1,
    relations: tuple[SourceDirectiveRecipeV1, ...] = (),
    settings: tuple[SourceDirectiveRecipeV1, ...] = (),
    writable_paths: tuple[str, ...] = (),
) -> ServiceSourceEntryV1:
    return ServiceSourceEntryV1(
        entry_id, "gated_service", unit_name=unit_name,
        readiness_owner=readiness_owner,
        legacy_bindings=(
            (_user_unit(f"legacy-{entry_id}-user", unit_name),)
            if user_binding else ()
        ) + extra_bindings,
        target_recipe=target_recipe,
        unit_recipe=_service_unit_recipe(
            entry_id, relations=relations, settings=settings,
            writable_paths=writable_paths,
        ),
    )


def _unit_relation(name: str, *entry_ids: str) -> SourceDirectiveRecipeV1:
    return _source_directive(
        "Unit", name, *(f"@unit:{entry_id}@" for entry_id in entry_ids),
    )


def _install_relation(name: str, *entry_ids: str) -> SourceDirectiveRecipeV1:
    return _source_directive(
        "Install", name, *(f"@unit:{entry_id}@" for entry_id in entry_ids),
    )


def _timer(
    entry_id: str, unit_name: str, target: str, *,
    schedule: tuple[SourceDirectiveRecipeV1, ...],
) -> ServiceSourceEntryV1:
    return ServiceSourceEntryV1(
        entry_id, "gated_timer", unit_name=unit_name, timer_target=target,
        legacy_bindings=(
            _user_unit(f"legacy-{entry_id}-user", unit_name),
        ),
        unit_recipe=(
            _source_directive("Unit", "Description", f"Metnos controlled {entry_id}"),
            _unit_relation("PartOf", "target-stack"),
            *schedule,
            _source_directive("Timer", "Unit", f"@unit:{target}@"),
            _install_relation("WantedBy", "target-stack"),
        ),
    )


def _entrypoint(
    entry_id: str, target_recipe: SourceTargetRecipeV1,
    *bindings: LegacySourceBindingV1,
) -> ServiceSourceEntryV1:
    return ServiceSourceEntryV1(
        entry_id, "gated_entrypoint", legacy_bindings=tuple(bindings),
        target_recipe=target_recipe,
    )


# The tuple below is the sole static topology source.  It is intentionally not
# generated from host state or optional-file presence.  B3 will add authenticated
# executable paths, hashes and per-release target values to these same entries;
# it must not introduce another inventory.
SERVICE_SOURCE_V1 = tuple(sorted((
    _service(
        "service-http", "metnos-http.service",
        target_recipe=_python_target(
            "runtime.metnos_http_server", "--host", "127.0.0.1", "--port", "8770",
            environment=_target_environment(
                *_TARGET_DATA_ENVIRONMENT_V1,
                ("METNOS_ENGINE", "metis"),
                ("METNOS_EXECUTOR_MAX_CLASS", "3"),
                ("METNOS_EXECUTOR_PARALLEL", "1"),
                ("METNOS_INTENT_CLASSIFIER", "1"),
                ("METNOS_PREFILTER_RULES", "1"),
                ("METNOS_PROPOSER_FAST_CONFIDENCE", "0.70"),
                ("METNOS_PROPOSER_GRAMMAR", "1"),
                ("METNOS_PROPOSER_VERB_FILTER", "1"),
            ),
        ),
        relations=(
            _unit_relation("After", "external-network"),
            _unit_relation("Before", "service-stack-ready"),
            _unit_relation("PartOf", "target-stack"),
            _source_directive("Unit", "StartLimitBurst", "5"),
            _source_directive("Unit", "StartLimitIntervalSec", "120s"),
            _install_relation("RequiredBy", "target-stack"),
        ),
        settings=(
            _source_directive("Service", "Delegate", "yes"),
            _source_directive(
                "Service", "DelegateSubgroup", "metnos-birth-host",
            ),
            _source_directive("Service", "MemoryAccounting", "yes"),
            _source_directive("Service", "Restart", "on-failure"),
            _source_directive("Service", "RestartSec", "10s"),
            _source_directive("Service", "TasksAccounting", "yes"),
            _source_directive("Service", "Type", "simple"),
        ),
        extra_bindings=(
            _system_unit("legacy-service-http-system", "metnos-http.service"),
        ),
    ),
    _service(
        "service-durable-worker", "metnos-durable-worker.service",
        target_recipe=_python_target(
            "durable_workloads.service",
            environment=_TARGET_DATA_ENVIRONMENT_V1,
        ),
        relations=(
            _unit_relation("After", "external-network-online"),
            _unit_relation("Before", "service-stack-ready"),
            _unit_relation("PartOf", "target-stack"),
            _source_directive("Unit", "StartLimitBurst", "3"),
            _source_directive("Unit", "StartLimitIntervalSec", "300s"),
            _install_relation("WantedBy", "target-stack"),
        ),
        settings=(
            _source_directive("Service", "KillSignal", "SIGTERM"),
            _source_directive("Service", "Restart", "on-failure"),
            _source_directive("Service", "RestartSec", "5s"),
            _source_directive(
                "Service", "SyslogIdentifier", "metnos-durable-worker",
            ),
            _source_directive("Service", "TimeoutStopSec", "45s"),
            _source_directive("Service", "Type", "simple"),
        ),
    ),
    _service(
        "service-i18n-translator", "metnos-i18n-translator.service",
        target_recipe=_python_target(
            "admin.i18n_cli", "translate-pending",
            working_directory="@installation_root@/runtime",
            environment=_TARGET_DATA_ENVIRONMENT_V1,
        ),
        relations=(
            _unit_relation("After", "external-network", "service-llm"),
            _unit_relation("PartOf", "target-stack"),
        ),
        settings=(
            _source_directive(
                "Service", "SyslogIdentifier", "metnos-i18n-translator",
            ),
            _source_directive("Service", "TimeoutStartSec", "10min"),
            _source_directive("Service", "Type", "oneshot"),
        ),
    ),
    _service(
        "service-stack-ready", "metnos-stack-ready.service",
        target_recipe=_python_target(
            "stack_reconcile", "wait-ready", "--require-sidecar", "auto",
            "--timeout", "120", working_directory="@installation_root@/runtime",
            environment=_TARGET_DATA_ENVIRONMENT_V1,
        ),
        relations=(
            _unit_relation("Requires", "service-http", "timer-i18n-translator"),
            _unit_relation(
                "After", "external-network-online", "service-http",
                "service-side-display", "service-playwright",
                "service-telegram-daemon", "service-llm", "service-searxng",
                "service-photon", "service-durable-worker",
                "timer-i18n-translator",
            ),
            _unit_relation("Before", "target-stack"),
            _unit_relation("PartOf", "target-stack"),
            _unit_relation("OnFailure", "stop-stack-quarantine"),
        ),
        settings=(
            _source_directive("Service", "RemainAfterExit", "yes"),
            _source_directive(
                "Service", "SyslogIdentifier", "metnos-stack-ready",
            ),
            _source_directive("Service", "TimeoutStartSec", "150s"),
            _source_directive("Service", "Type", "oneshot"),
        ),
        readiness_owner=True,
    ),
    _service(
        "service-stack-watchdog", "metnos-stack-watchdog.service",
        target_recipe=_python_target(
            "stack_reconcile", "watchdog", "--require-sidecar", "auto",
            working_directory="@installation_root@/runtime",
            environment=_TARGET_DATA_ENVIRONMENT_V1,
        ),
        relations=(_unit_relation("After", "target-stack"),),
        settings=(
            _source_directive(
                "Service", "SyslogIdentifier", "metnos-stack-watchdog",
            ),
            _source_directive("Service", "TimeoutStartSec", "180s"),
            _source_directive("Service", "Type", "oneshot"),
        ),
    ),
    _service(
        "service-telegram-daemon", "metnos-telegram-daemon.service",
        # Carried over from the legacy unit, which declared
        # `%h/.local/state/metnos %h/.local/share/metnos`. The signed source
        # forbids systemd specifiers, so the same two directories are named
        # through the bindings the compiler resolves from the service home.
        writable_paths=("@service_state@", "@service_data@"),
        target_recipe=_python_target(
            "runtime.channels.daemon",
            environment=_TARGET_DATA_ENVIRONMENT_V1,
        ),
        relations=(
            _unit_relation("After", "external-network-online", "service-http"),
            _unit_relation("Before", "service-stack-ready"),
            _unit_relation("PartOf", "target-stack"),
            _unit_relation("Wants", "external-network-online"),
            _install_relation("WantedBy", "target-stack"),
        ),
        settings=(
            _source_directive("Service", "LockPersonality", "yes"),
            _source_directive("Service", "MemoryDenyWriteExecute", "no"),
            _source_directive("Service", "PrivateTmp", "yes"),
            _source_directive("Service", "ProtectControlGroups", "yes"),
            _source_directive("Service", "ProtectKernelModules", "yes"),
            _source_directive("Service", "ProtectKernelTunables", "yes"),
            _source_directive("Service", "ProtectSystem", "strict"),
            _source_directive("Service", "Restart", "on-failure"),
            _source_directive("Service", "RestartSec", "10s"),
            _source_directive("Service", "RestrictNamespaces", "yes"),
            _source_directive("Service", "RestrictRealtime", "yes"),
            _source_directive("Service", "StandardError", "journal"),
            _source_directive("Service", "StandardOutput", "journal"),
            _source_directive("Service", "SystemCallArchitectures", "native"),
            _source_directive(
                "Service", "SyslogIdentifier", "metnos-telegram",
            ),
            _source_directive("Service", "TimeoutStopSec", "10s"),
            _source_directive("Service", "Type", "simple"),
        ),
    ),
    _service(
        "service-playwright", "metnos-playwright.service",
        target_recipe=_python_target(
            "playwright_sidecar.server", "--host", "127.0.0.1", "--port", "8771",
            working_directory="@installation_root@/runtime",
            environment=_target_environment(
                *_TARGET_DATA_ENVIRONMENT_V1,
                ("DISPLAY", ":99"),
                (
                    "PLAYWRIGHT_BROWSERS_PATH",
                    "@service_data@/playwright-browsers",
                ),
            ),
        ),
        relations=(
            _unit_relation("After", "external-network-online", "service-side-display"),
            _unit_relation("Wants", "external-network-online"),
            _unit_relation("Requires", "service-side-display"),
            _unit_relation("Before", "service-stack-ready"),
            _unit_relation("PartOf", "target-stack"),
            _source_directive("Unit", "StartLimitBurst", "10"),
            _source_directive("Unit", "StartLimitIntervalSec", "300s"),
            _install_relation("WantedBy", "target-stack"),
        ),
        settings=(
            _source_directive("Service", "LimitNOFILE", "8192"),
            _source_directive("Service", "LockPersonality", "yes"),
            _source_directive("Service", "MemoryDenyWriteExecute", "no"),
            _source_directive("Service", "MemoryHigh", "900M"),
            _source_directive("Service", "MemoryMax", "1200M"),
            _source_directive("Service", "NotifyAccess", "main"),
            _source_directive("Service", "PrivateTmp", "yes"),
            _source_directive("Service", "ProtectControlGroups", "yes"),
            _source_directive("Service", "ProtectKernelModules", "yes"),
            _source_directive("Service", "ProtectKernelTunables", "yes"),
            _source_directive("Service", "Restart", "always"),
            _source_directive("Service", "RestartSec", "5s"),
            _source_directive("Service", "RestrictRealtime", "yes"),
            _source_directive("Service", "StandardError", "journal"),
            _source_directive("Service", "StandardOutput", "journal"),
            _source_directive(
                "Service", "SyslogIdentifier", "metnos-playwright",
            ),
            _source_directive("Service", "SystemCallArchitectures", "native"),
            _source_directive("Service", "TasksMax", "512"),
            _source_directive("Service", "TimeoutStartSec", "90s"),
            _source_directive("Service", "TimeoutStopSec", "20s"),
            _source_directive("Service", "Type", "notify"),
            _source_directive("Service", "WatchdogSec", "45s"),
        ),
    ),
    _service(
        "service-side-display", "metnos-side-display.service",
        target_recipe=_native_target(
            "/usr/bin/Xvfb", ":99", "-screen", "0", "1920x1080x24", "-ac",
            "-nolisten", "tcp", "-dpi", "96",
        ),
        relations=(
            _unit_relation("After", "external-network-online"),
            _unit_relation("Wants", "external-network-online"),
            _unit_relation("Before", "service-playwright", "service-stack-ready"),
            _unit_relation("PartOf", "target-stack"),
            _install_relation("WantedBy", "target-stack"),
        ),
        settings=(
            _source_directive("Service", "PrivateTmp", "yes"),
            _source_directive("Service", "ProtectHome", "no"),
            _source_directive("Service", "ProtectSystem", "strict"),
            _source_directive("Service", "Restart", "always"),
            _source_directive("Service", "RestartSec", "2s"),
            _source_directive(
                "Service", "RestrictAddressFamilies", "AF_UNIX",
            ),
            _source_directive("Service", "StandardError", "journal"),
            _source_directive("Service", "StandardOutput", "journal"),
            _source_directive(
                "Service", "SyslogIdentifier", "metnos-side-display",
            ),
            _source_directive("Service", "Type", "simple"),
        ),
    ),
    _service(
        "service-llm", "metnos-llm.service",
        target_recipe=_native_target(
            "@installation_root@/runtime/bin/llama-server", "-m",
            "@service_data@/models/llm.gguf", "--host", "127.0.0.1",
            "--port", "8080", "-ngl", "0", "-c", "8192",
        ),
        relations=(
            _unit_relation("After", "external-network-online"),
            _unit_relation("Before", "service-stack-ready"),
            _unit_relation("PartOf", "target-stack"),
            _install_relation("WantedBy", "target-stack"),
        ),
        settings=(
            _source_directive("Service", "Nice", "5"),
            _source_directive("Service", "Restart", "on-failure"),
            _source_directive("Service", "Type", "simple"),
        ),
    ),
    _service(
        "service-searxng", "metnos-searxng.service",
        target_recipe=_python_target(
            "searx.webapp",
            working_directory="@installation_root@/runtime/vendor/searxng",
            environment=_target_environment(
                ("SEARXNG_SETTINGS_PATH", "@service_config@/searxng/settings.yml"),
                ("TMPDIR", "@service_data@/sidecars/searxng/cache"),
            ),
        ),
        relations=(
            _unit_relation("After", "external-network"),
            _unit_relation("Before", "service-stack-ready"),
            _unit_relation("PartOf", "target-stack"),
            _install_relation("WantedBy", "target-stack"),
        ),
        settings=(
            _source_directive("Service", "Restart", "on-failure"),
            _source_directive("Service", "RestartSec", "10s"),
            _source_directive("Service", "Type", "simple"),
        ),
    ),
    _service(
        "service-photon", "metnos-photon.service",
        target_recipe=_native_target(
            "/usr/bin/java", "-jar", "@installation_root@/runtime/vendor/photon.jar",
            "serve", "-data-dir", "@service_data@/sidecars/photon/current",
            "-listen-ip", "127.0.0.1", "-listen-port", "2322", "-j", "4",
            environment=_target_environment(
                ("PHOTON_DATA_DIR", "@service_data@/sidecars/photon/current"),
            ),
        ),
        relations=(
            _unit_relation("After", "external-network"),
            _unit_relation("Before", "service-stack-ready"),
            _unit_relation("PartOf", "target-stack"),
            _install_relation("WantedBy", "target-stack"),
        ),
        settings=(
            _source_directive("Service", "Restart", "on-failure"),
            _source_directive("Service", "RestartSec", "10s"),
            _source_directive("Service", "Type", "simple"),
        ),
    ),
    _timer(
        "timer-i18n-translator", "metnos-i18n-translator.timer",
        "service-i18n-translator", schedule=(
            _source_directive("Timer", "AccuracySec", "30s"),
            _source_directive("Timer", "OnActiveSec", "30s"),
            _source_directive("Timer", "OnUnitActiveSec", "5min"),
            _source_directive("Timer", "Persistent", "yes"),
        ),
    ),
    _timer(
        "timer-stack-watchdog", "metnos-stack-watchdog.timer",
        "service-stack-watchdog", schedule=(
            _source_directive("Timer", "AccuracySec", "15s"),
            _source_directive("Timer", "OnActiveSec", "3min"),
            _source_directive("Timer", "OnUnitActiveSec", "2min"),
        ),
    ),
    ServiceSourceEntryV1(
        "stop-stack-quarantine", "stop_only",
        unit_name="metnos-stack-quarantine.service",
        legacy_bindings=(
            _user_unit(
                "legacy-stop-stack-quarantine-user",
                "metnos-stack-quarantine.service",
            ),
        ),
        target_recipe=SourceTargetRecipeV1(
            "systemctl_stop", "@systemctl@", None, ("stop", "@stop_units@"), "/",
        ),
        unit_recipe=(
            _source_directive("Unit", "Description", "Metnos closed stack stop"),
            _source_directive(
                "Service", "ExecStart", "@systemctl@", "stop", "@stop_units@",
            ),
            _source_directive(
                "Service", "SyslogIdentifier", "metnos-stack-quarantine",
            ),
            _source_directive("Service", "TimeoutStartSec", "30s"),
            _source_directive("Service", "Type", "oneshot"),
        ),
    ),
    ServiceSourceEntryV1(
        "target-stack", "target", unit_name="metnos.target",
        legacy_bindings=(
            _user_unit("legacy-target-stack-user", "metnos.target"),
        ),
        unit_recipe=(
            _source_directive("Unit", "Description", "Metnos integrated local stack"),
            _unit_relation(
                "Requires", "service-http", "timer-i18n-translator",
                "service-stack-ready",
            ),
            _unit_relation("BindsTo", "service-stack-ready"),
            _unit_relation(
                "Wants", "service-side-display", "service-playwright",
                "service-telegram-daemon", "timer-stack-watchdog", "service-llm",
                "service-searxng", "service-photon", "service-durable-worker",
            ),
            _unit_relation("After", "service-stack-ready"),
            _install_relation("WantedBy", "external-default"),
        ),
    ),
    ServiceSourceEntryV1(
        "external-network", "external_dependency",
        external_unit_name="network.target",
    ),
    ServiceSourceEntryV1(
        "external-network-online", "external_dependency",
        external_unit_name="network-online.target",
    ),
    ServiceSourceEntryV1(
        "external-default", "external_dependency",
        external_unit_name="default.target",
    ),
    ServiceSourceEntryV1(
        "external-timers", "external_dependency",
        external_unit_name="timers.target",
    ),
    _entrypoint(
        "entry-installer", _administrative_operation("install-metnos"),
            _repository_entry("legacy-install-bootstrap", "script", "install/bootstrap.sh"),
            _repository_entry("legacy-install-setup", "script", "install/setup.sh"),
            _repository_entry(
                "legacy-install-module", "python_module", "install/__main__.py",
            ),
    ),
    _entrypoint(
        "entry-install-git-hooks",
        _administrative_operation("install-git-hooks"),
            _repository_entry(
                "legacy-install-git-hooks", "script",
                "scripts/install_git_hooks.sh",
            ),
    ),
    _entrypoint(
        "entry-download-models",
        _administrative_operation("download-models"),
            _repository_entry(
                "legacy-install-download-models", "script",
                "install/download_models.sh",
            ),
    ),
    _entrypoint(
        "entry-playwright-installer",
        _administrative_operation("install-playwright"),
            _repository_entry(
                "legacy-install-playwright", "python_module",
                "install/playwright_sidecar.py",
            ),
            _repository_entry(
                "legacy-playwright-install-script", "script",
                "runtime/playwright_sidecar/install.sh",
            ),
    ),
    _entrypoint(
        "entry-sidecar-photon",
        _administrative_operation("install-sidecar-photon"),
            _repository_entry(
                "legacy-install-sidecars-photon", "python_module",
                "install/sidecar.py",
            ),
    ),
    _entrypoint(
        "entry-sidecar-searxng",
        _administrative_operation("install-sidecar-searxng"),
            _repository_entry(
                "legacy-install-sidecars-searxng", "python_module",
                "install/sidecar.py",
            ),
    ),
    _entrypoint(
        "entry-sidecar-vlm",
        _administrative_operation("install-sidecar-vlm"),
            _repository_entry(
                "legacy-install-sidecars-vlm", "python_module",
                "install/sidecar.py",
            ),
    ),
    _entrypoint(
        "entry-llm-installer", _administrative_operation("install-llm"),
            _repository_entry(
                "legacy-install-llm", "python_module", "install/llm_manager.py",
            ),
    ),
    _entrypoint(
        "entry-service-policy",
        _administrative_operation("install-service-policy"),
            _repository_entry(
                "legacy-service-policy", "python_module",
                "install/service_control_policy.py",
            ),
    ),
    _entrypoint(
        "entry-backup", _administrative_operation("backup"),
            _repository_entry("legacy-deploy-backup", "script", "deploy/backup_nas.sh"),
            _system_unit("legacy-service-backup-system", "metnos-backup.service"),
            _system_unit("legacy-timer-backup-system", "metnos-backup.timer"),
    ),
    _entrypoint(
        "entry-prompts-translator",
        _administrative_operation("prompts-translator"),
            _repository_entry(
                "legacy-deploy-prompts-translator", "script",
                "deploy/run_prompts_translator.sh",
            ),
            _system_unit(
                "legacy-service-prompts-translator-system",
                "metnos-prompts-translator.service",
            ),
            _system_unit(
                "legacy-timer-prompts-translator-system",
                "metnos-prompts-translator.timer",
            ),
    ),
    _entrypoint(
        "entry-migrate-syspath",
        _administrative_operation("migrate-syspath"),
            _repository_entry(
                "legacy-migrate-syspath", "python_module",
                "scripts/migrate-syspath-to-package.py",
            ),
    ),
    _entrypoint(
        "entry-normalize-installed-executors",
        _administrative_operation("normalize-installed-executors"),
            _repository_entry(
                "legacy-normalize-installed-executors", "python_module",
                "scripts/normalize_installed_github_executors.py",
            ),
    ),
    _entrypoint(
        "entry-rename-myclaw",
        _administrative_operation("rename-myclaw"),
            _repository_entry(
                "legacy-rename-myclaw", "script",
                "scripts/rename-myclaw-to-metnos.sh",
            ),
    ),
    _entrypoint(
        "entry-post-rename-baseline",
        _administrative_operation("post-rename-baseline"),
            _repository_entry(
                "legacy-post-rename-baseline", "script",
                "scripts/post-rename-verify.sh",
            ),
    ),
    _entrypoint(
        "entry-post-rename-verify",
        _administrative_operation("post-rename-verify"),
            _repository_entry(
                "legacy-post-rename-verify", "script",
                "scripts/post-rename-verify.sh",
            ),
    ),
), key=lambda item: item.entry_id.encode("utf-8")))


# Current directives absent from the candidate recipe may disappear only by
# one of these reviewed migrations.  The mechanical census test requires an
# exact match, so a new legacy directive cannot vanish silently.
#
# One entry was retired here rather than migrated, and the reason must not be
# lost with it.  `metnos-telegram-daemon.service` used to record
# `Service/ReadWritePaths` as absent from the candidate, pending
# "replace_with_verified_service_data_permissions".  The candidate now declares
# that directive for every gated unit, because the birth gate needs the runtime
# root writable, so the key is no longer absent and this register can no longer
# express a partial migration.  The migration is now COMPLETE for that unit:
# the legacy `%h/.local/state/metnos %h/.local/share/metnos` is carried over
# through the `@service_state@` and `@service_data@` bindings, because the
# signed source forbids systemd specifiers (roadmap §23.33).
_CURRENT_UNIT_DIRECTIVE_DISPOSITIONS_V1 = MappingProxyType({
    ("metnos-durable-worker.service", "Service", "Environment"):
        "move_to_signed_target_or_minimum_environment",
    ("metnos-http.service", "Install", "WantedBy"):
        "replace_weak_enablement_with_signed_requiredby",
    ("metnos-http.service", "Service", "Environment"):
        "move_to_signed_target_or_minimum_environment",
    ("metnos-i18n-translator.service", "Service", "Environment"):
        "move_to_signed_target_or_minimum_environment",
    ("metnos-photon.service", "Service", "Environment"):
        "move_to_signed_target_environment",
    ("metnos-playwright.service", "Service", "Environment"):
        "move_to_signed_target_environment",
    ("metnos-playwright.service", "Unit", "Documentation"):
        "drop_nonoperational_legacy_metadata",
    ("metnos-searxng.service", "Service", "Environment"):
        "move_to_signed_target_environment",
    ("metnos-side-display.service", "Service", "Environment"):
        "drop_unused_xvfb_display_environment",
    ("metnos-side-display.service", "Unit", "Documentation"):
        "drop_nonoperational_legacy_metadata",
    ("metnos-stack-ready.service", "Service", "Environment"):
        "move_to_signed_target_or_minimum_environment",
    ("metnos-stack-watchdog.service", "Service", "Environment"):
        "move_to_signed_target_or_minimum_environment",
    ("metnos-telegram-daemon.service", "Service", "Environment"):
        "move_to_signed_target_or_minimum_environment",
    ("metnos-telegram-daemon.service", "Unit", "Documentation"):
        "drop_nonoperational_legacy_metadata",
    ("metnos.target", "Unit", "Documentation"):
        "drop_nonoperational_legacy_metadata",
})


def _validate_service_source_v1() -> None:
    entry_ids = [item.entry_id for item in SERVICE_SOURCE_V1]
    if (
        entry_ids != sorted(entry_ids, key=lambda value: value.encode("utf-8"))
        or len(entry_ids) != len(set(entry_ids))
        or any(_ENTRY_ID_RE.fullmatch(value) is None for value in entry_ids)
    ):
        raise ServiceCatalogError("birth_ownership_service_catalog_invalid", "source entries")
    by_id = {item.entry_id: item for item in SERVICE_SOURCE_V1}
    units: list[str] = []
    legacy_ids: list[str] = []
    for item in SERVICE_SOURCE_V1:
        if item.class_name not in _CLASSES:
            raise ServiceCatalogError("birth_ownership_service_catalog_invalid", "source class")
        if item.unit_name is not None:
            if _UNIT_RE.fullmatch(item.unit_name) is None:
                raise ServiceCatalogError("birth_ownership_service_catalog_invalid", "source unit")
            units.append(item.unit_name)
        if item.external_unit_name is not None:
            if _UNIT_RE.fullmatch(item.external_unit_name) is None:
                raise ServiceCatalogError("birth_ownership_service_catalog_invalid", "source external unit")
            units.append(item.external_unit_name)
        if item.class_name == "gated_timer":
            target = by_id.get(item.timer_target or "")
            if target is None or target.class_name != "gated_service":
                raise ServiceCatalogError("birth_ownership_service_catalog_invalid", "source timer")
        elif item.timer_target is not None:
            raise ServiceCatalogError("birth_ownership_service_catalog_invalid", "source timer")
        for binding in item.legacy_bindings:
            if _ENTRY_ID_RE.fullmatch(binding.legacy_id) is None:
                raise ServiceCatalogError("birth_ownership_service_catalog_invalid", "source legacy id")
            if binding.kind not in _LEGACY_KINDS or binding.scope not in _LEGACY_SCOPES:
                raise ServiceCatalogError("birth_ownership_service_catalog_invalid", "source legacy kind")
            _validate_legacy_locator(binding.kind, binding.locator)
            legacy_ids.append(binding.legacy_id)
    if len(units) != len(set(units)):
        raise ServiceCatalogError("birth_ownership_service_catalog_invalid", "source unit duplicate")
    if len(legacy_ids) != len(set(legacy_ids)):
        raise ServiceCatalogError("birth_ownership_service_catalog_invalid", "source legacy duplicate")
    if sum(item.readiness_owner for item in SERVICE_SOURCE_V1) != 1:
        raise ServiceCatalogError("birth_ownership_service_catalog_invalid", "source readiness")
    hashes = tuple(
        (item.entry_id, "sha256:" + "0" * 64)
        for item in SERVICE_SOURCE_V1
        if item.target_recipe.execution_kind != "none"
    )
    _compile_service_source_v1(_SourceCompileContextV1(
        "/opt/metnos", "/usr/bin/python3", "metnos", 1000,
        (1001,), "/srv/metnos", "/usr/bin/systemctl", hashes,
    ))


def legacy_bindings_from_source_v1() -> tuple[dict[str, object], ...]:
    bindings = [
        {
            "legacy_id": binding.legacy_id,
            "entry_id": item.entry_id,
            "kind": binding.kind,
            "scope": binding.scope,
            "locator": binding.locator,
            "disposition": "retire_in_group7",
        }
        for item in SERVICE_SOURCE_V1
        for binding in item.legacy_bindings
    ]
    return tuple(sorted(bindings, key=lambda value: str(value["legacy_id"]).encode("utf-8")))


def maintenance_targets_from_source_v1() -> tuple[tuple[str, str], ...]:
    return tuple(sorted({
        (str(binding["scope"]), str(binding["locator"]))
        for binding in legacy_bindings_from_source_v1()
        if binding["kind"] in {"user_unit", "system_unit"}
    }))


def contract_cutover_units_from_source_v1() -> tuple[str, ...]:
    return tuple(sorted({
        locator for scope, locator in maintenance_targets_from_source_v1()
        if scope == "user"
    }))


def _validate_legacy_locator(kind: str, value: object) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ServiceCatalogError("birth_ownership_service_catalog_invalid", "legacy locator")
    if kind in {"user_unit", "system_unit"}:
        if _UNIT_RE.fullmatch(value) is None:
            raise ServiceCatalogError("birth_ownership_service_catalog_invalid", "legacy unit")
        return value
    if value.startswith("/"):
        return _absolute_path(value, "legacy locator")
    return _relative_path(value, "legacy locator")


def _relative_path(value: object, detail: str = "path") -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise ServiceCatalogError("birth_ownership_service_catalog_invalid", detail)
    path = PurePosixPath(value)
    if (
        path.is_absolute() or value != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
        or len(path.parts) > MAX_RELATIVE_PATH_COMPONENTS_V1
    ):
        raise ServiceCatalogError("birth_ownership_service_catalog_invalid", detail)
    return value


def _absolute_path(value: object, detail: str = "path") -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise ServiceCatalogError("birth_ownership_service_catalog_invalid", detail)
    path = PurePosixPath(value)
    if not path.is_absolute() or value != path.as_posix() or any(part in {".", ".."} for part in path.parts):
        raise ServiceCatalogError("birth_ownership_service_catalog_invalid", detail)
    return value


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "json",
        ) from exc


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in items:
        if key in value:
            raise ServiceCatalogError(
                "birth_ownership_service_catalog_invalid", "duplicate key",
            )
        value[key] = item
    return value


def _digest(value: object, detail: str) -> str:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", detail,
        )
    return value


def _u64be(value: int) -> bytes:
    return value.to_bytes(8, "big", signed=False)


def target_executable_hash_v1(path: str, content: bytes) -> str:
    canonical = _absolute_path(path, "target executable")
    if not isinstance(content, bytes):
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "target bytes",
        )
    encoded_path = canonical.encode("utf-8")
    return "sha256:" + hashlib.sha256(
        TARGET_EXECUTABLE_DOMAIN
        + _u64be(len(encoded_path)) + encoded_path
        + _u64be(len(content)) + content
    ).hexdigest()


def service_coverage_hash_v1(encoded: bytes) -> str:
    if not isinstance(encoded, bytes):
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "catalog bytes",
        )
    return "sha256:" + hashlib.sha256(
        SERVICE_COVERAGE_DOMAIN + encoded
    ).hexdigest()


def _fragment_hash(unit_name: str, fragment: bytes) -> str:
    if _UNIT_RE.fullmatch(unit_name or "") is None or not isinstance(fragment, bytes):
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "unit fragment",
        )
    name = unit_name.encode("utf-8")
    return "sha256:" + hashlib.sha256(
        SYSTEMD_FRAGMENT_DOMAIN
        + _u64be(len(name)) + name
        + _u64be(len(fragment)) + fragment
    ).hexdigest()


def _safe_scalar(value: object, detail: str) -> str:
    if (
        not isinstance(value, str) or not value
        or value != value.strip() or "\x00" in value
        or "\n" in value or "\r" in value or "%" in value
        or '"' in value or "'" in value
        or value.startswith(("#", ";")) or "\\" in value
        or len(value.encode("utf-8")) > 4096
    ):
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", detail,
        )
    return value


def _validate_environment_name(name: object, *, target: bool) -> str:
    if not isinstance(name, str) or _ENVIRONMENT_NAME_RE.fullmatch(name) is None:
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "environment name",
        )
    if target and (
        name in {"PATH", "HOME", "SHELL", "VIRTUAL_ENV"}
        or name.startswith(("PYTHON", "LD_", "DYLD_", "OPENSSL_"))
        or name in {
            "METNOS_INSTALL_ROOT", "METNOS_VENV", "METNOS_CONFIG",
            "METNOS_OWNERSHIP_ROOT", "METNOS_EXECUTOR_BIRTH_ROOT",
        }
    ):
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "forbidden environment",
        )
    return name


def _validated_directive(
    section: object, name: object, value_type: object, values: object,
) -> ServiceDirectiveV1:
    if not isinstance(section, str) or not isinstance(name, str):
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "directive",
        )
    expected_type = _DIRECTIVE_TYPES.get((section, name))
    if expected_type is None or value_type != expected_type:
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "directive type",
        )
    if not isinstance(values, list) or not values or len(values) > 128:
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "directive values",
        )
    parsed = tuple(values)
    if any(not isinstance(value, str) for value in parsed):
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "directive value",
        )
    if expected_type == "scalar":
        if len(parsed) != 1:
            raise ServiceCatalogError(
                "birth_ownership_service_catalog_invalid", "scalar cardinality",
            )
        _safe_scalar(parsed[0], "scalar")
    elif expected_type == "boolean":
        if len(parsed) != 1 or parsed[0] not in {"yes", "no"}:
            raise ServiceCatalogError(
                "birth_ownership_service_catalog_invalid", "boolean",
            )
    elif expected_type == "duration":
        if len(parsed) != 1 or _DURATION_RE.fullmatch(parsed[0]) is None:
            raise ServiceCatalogError(
                "birth_ownership_service_catalog_invalid", "duration",
            )
    elif expected_type == "integer":
        if len(parsed) != 1 or re.fullmatch(r"(?:0|-?[1-9][0-9]*)", parsed[0]) is None:
            raise ServiceCatalogError(
                "birth_ownership_service_catalog_invalid", "integer",
            )
    elif expected_type == "argv":
        if len(parsed) > 32 or any(
            _SAFE_TOKEN_RE.fullmatch(value) is None or "%" in value
            or len(value.encode("utf-8")) > 4096
            for value in parsed
        ):
            raise ServiceCatalogError(
                "birth_ownership_service_catalog_invalid", "argv",
            )
        if any(value.startswith("!") for value in parsed[1:]):
            raise ServiceCatalogError(
                "birth_ownership_service_catalog_invalid", "argv prefix",
            )
    elif expected_type == "environment":
        names: list[str] = []
        for value in parsed:
            _safe_scalar(value, "environment")
            if "=" not in value or any(character.isspace() for character in value):
                raise ServiceCatalogError(
                    "birth_ownership_service_catalog_invalid", "environment",
                )
            env_name, _env_value = value.split("=", 1)
            names.append(_validate_environment_name(env_name, target=False))
        if names != sorted(names, key=lambda item: item.encode("utf-8")) or len(names) != len(set(names)):
            raise ServiceCatalogError(
                "birth_ownership_service_catalog_invalid", "environment order",
            )
    elif expected_type == "unit_list":
        if any(_UNIT_RE.fullmatch(value) is None for value in parsed):
            raise ServiceCatalogError(
                "birth_ownership_service_catalog_invalid", "unit list",
            )
        if parsed != tuple(sorted(set(parsed), key=lambda item: item.encode("utf-8"))):
            raise ServiceCatalogError(
                "birth_ownership_service_catalog_invalid", "unit list order",
            )
    elif expected_type == "path_list":
        for value in parsed:
            _absolute_path(value, "path list")
            if (
                "%" in value or '"' in value or "'" in value
                or any(character.isspace() for character in value)
            ):
                raise ServiceCatalogError(
                    "birth_ownership_service_catalog_invalid", "specifier",
                )
        if parsed != tuple(sorted(set(parsed), key=lambda item: item.encode("utf-8"))):
            raise ServiceCatalogError(
                "birth_ownership_service_catalog_invalid", "path list order",
            )
    return ServiceDirectiveV1(section, name, str(value_type), parsed)


def _directive_document(directive: ServiceDirectiveV1) -> dict[str, object]:
    return {
        "section": directive.section,
        "name": directive.name,
        "value_type": directive.value_type,
        "values": list(directive.values),
    }


def _directive_sort_key(directive: ServiceDirectiveV1) -> tuple[int, bytes]:
    return (_SECTION_ORDER[directive.section], directive.name.encode("utf-8"))


def _render_directives(directives: tuple[ServiceDirectiveV1, ...]) -> bytes:
    lines: list[str] = []
    current_section: str | None = None
    for directive in directives:
        if directive.section != current_section:
            if current_section is not None:
                lines.append("")
            current_section = directive.section
            lines.append(f"[{current_section}]")
        if directive.value_type in {
            "argv", "environment", "unit_list", "path_list",
        }:
            rendered_value = " ".join(directive.values)
        else:
            rendered_value = directive.values[0]
        lines.append(f"{directive.name}={rendered_value}")
    if not lines:
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "empty unit",
        )
    return ("\n".join(lines) + "\n").encode("utf-8")


def make_unit_spec_v1(
    unit_name: str, directives: Iterable[Mapping[str, object] | ServiceDirectiveV1],
) -> ServiceUnitSpecV1:
    if _UNIT_RE.fullmatch(unit_name or "") is None:
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "unit name",
        )
    parsed: list[ServiceDirectiveV1] = []
    for raw in directives:
        if isinstance(raw, ServiceDirectiveV1):
            directive = _validated_directive(
                raw.section, raw.name, raw.value_type, list(raw.values),
            )
        elif isinstance(raw, Mapping) and set(raw) == _DIRECTIVE_KEYS:
            directive = _validated_directive(
                raw.get("section"), raw.get("name"), raw.get("value_type"),
                raw.get("values"),
            )
        else:
            raise ServiceCatalogError(
                "birth_ownership_service_catalog_invalid", "directive schema",
            )
        parsed.append(directive)
    expected = sorted(parsed, key=_directive_sort_key)
    keys = [(item.section, item.name) for item in parsed]
    if parsed != expected or len(keys) != len(set(keys)):
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "directive order",
        )
    fragment = _render_directives(tuple(parsed))
    if len(fragment) > MAX_UNIT_FRAGMENT_BYTES:
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "unit size",
        )
    return ServiceUnitSpecV1(_fragment_hash(unit_name, fragment), tuple(parsed))


def render_unit_spec_v1(unit_name: str, unit_spec: ServiceUnitSpecV1) -> bytes:
    if type(unit_spec) is not ServiceUnitSpecV1:
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "unit spec",
        )
    rebuilt = make_unit_spec_v1(unit_name, unit_spec.directives)
    if rebuilt.fragment_hash != unit_spec.fragment_hash:
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "fragment hash",
        )
    return _render_directives(rebuilt.directives)


def parse_unit_fragment_v1(unit_name: str, fragment: bytes) -> ServiceUnitSpecV1:
    """Independently scan only the exact subset emitted by the renderer."""
    if not isinstance(fragment, bytes) or not fragment or len(fragment) > MAX_UNIT_FRAGMENT_BYTES:
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "unit size",
        )
    try:
        text = fragment.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "unit encoding",
        ) from exc
    if not text.endswith("\n") or text.endswith("\n\n") or "\r" in text or "\\\n" in text:
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "unit framing",
        )
    section: str | None = None
    documents: list[dict[str, object]] = []
    seen_sections: set[str] = set()
    last_section_order = -1
    for line in text[:-1].split("\n"):
        if not line:
            section = None
            continue
        if line.startswith(("#", ";")) or "%" in line or line != line.strip():
            raise ServiceCatalogError(
                "birth_ownership_service_catalog_invalid", "unit line",
            )
        if line.startswith("["):
            if not line.endswith("]") or line[1:-1] not in _SECTION_ORDER:
                raise ServiceCatalogError(
                    "birth_ownership_service_catalog_invalid", "unit section",
                )
            candidate = line[1:-1]
            candidate_order = _SECTION_ORDER[candidate]
            if candidate in seen_sections or candidate_order <= last_section_order:
                raise ServiceCatalogError(
                    "birth_ownership_service_catalog_invalid", "section order",
                )
            seen_sections.add(candidate)
            last_section_order = candidate_order
            section = candidate
            continue
        if section is None or line.count("=") < 1:
            raise ServiceCatalogError(
                "birth_ownership_service_catalog_invalid", "unit syntax",
            )
        name, raw_value = line.split("=", 1)
        value_type = _DIRECTIVE_TYPES.get((section, name))
        if value_type is None or not raw_value:
            raise ServiceCatalogError(
                "birth_ownership_service_catalog_invalid", "unit directive",
            )
        values = (
            raw_value.split(" ")
            if value_type in {"argv", "environment", "unit_list", "path_list"}
            else [raw_value]
        )
        if any(not value for value in values):
            raise ServiceCatalogError(
                "birth_ownership_service_catalog_invalid", "unit spacing",
            )
        documents.append({
            "section": section, "name": name,
            "value_type": value_type, "values": values,
        })
    parsed = make_unit_spec_v1(unit_name, documents)
    if _render_directives(parsed.directives) != fragment:
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "unit canonical",
        )
    return parsed


def _entry_scope(class_name: str) -> str:
    if class_name in {
        "gated_service", "gated_timer", "stop_only", "target",
    }:
        return "system"
    if class_name == "external_dependency":
        return "external"
    return "administrative"


def _resolve_recipe_value_v1(
    value: str, context: _SourceCompileContextV1,
    by_id: Mapping[str, ServiceSourceEntryV1],
) -> str:
    fixed = {
        "@installation_root@": context.installation_root,
        "@python@": context.python_executable,
        "@administrative_python@": context.python_executable,
        "!@administrative_python@": "!" + context.python_executable,
        "@service_user@": context.service_user,
        "@service_gid@": str(context.service_gid),
        "@service_home@": context.service_home,
        "@service_data@": context.service_home + "/.local/share/metnos",
        "@service_config@": context.service_home + "/.config/metnos",
        "@service_state@": context.service_home + "/.local/state/metnos",
        "@systemctl@": context.systemctl_executable,
    }
    if value in fixed:
        return fixed[value]
    unit_match = re.fullmatch(r"@unit:([a-z0-9][a-z0-9-]{0,63})@", value)
    if unit_match is not None:
        item = by_id.get(unit_match.group(1))
        unit = None if item is None else item.unit_name or item.external_unit_name
        if unit is None:
            raise ServiceCatalogError(
                "birth_ownership_service_catalog_invalid", "source unit placeholder",
            )
        return unit
    for placeholder, replacement in fixed.items():
        prefix = placeholder + "/"
        if value.startswith(prefix):
            return replacement + value[len(placeholder):]
    if "@" in value:
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "source placeholder",
        )
    return value


def _validate_compile_context_v1(
    context: _SourceCompileContextV1,
) -> dict[str, str]:
    if type(context) is not _SourceCompileContextV1:
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "source context",
        )
    _absolute_path(context.installation_root, "installation root")
    _absolute_path(context.python_executable, "python executable")
    _absolute_path(context.service_home, "service home")
    _absolute_path(context.systemctl_executable, "systemctl executable")
    if (
        _ACCOUNT_RE.fullmatch(context.service_user) is None
        or type(context.service_gid) is not int
        or not 0 < context.service_gid < 2 ** 31
        or any(
            type(item) is not int or not 0 < item < 2 ** 31
            for item in context.service_supplementary_gids
        )
        or context.service_supplementary_gids != tuple(sorted(
            set(context.service_supplementary_gids),
        ))
    ):
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "service identity",
        )
    target_hashes = dict(context.target_hashes)
    executable_ids = {
        item.entry_id for item in SERVICE_SOURCE_V1
        if item.target_recipe.execution_kind != "none"
    }
    if (
        len(target_hashes) != len(context.target_hashes)
        or set(target_hashes) != executable_ids
    ):
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "target hash coverage",
        )
    for digest in target_hashes.values():
        _digest(digest, "target executable hash")
    return target_hashes


def _compile_service_source_v1(
    context: _SourceCompileContextV1,
) -> tuple[ServiceCatalogEntryV1, ...]:
    """Compile the sole source; no caller may add entries or directives."""

    target_hashes = _validate_compile_context_v1(context)
    by_id = {item.entry_id: item for item in SERVICE_SOURCE_V1}
    stop_units = tuple(sorted(
        str(item.unit_name) for item in SERVICE_SOURCE_V1
        if item.class_name in {"gated_service", "gated_timer"}
    ))
    entries: list[ServiceCatalogEntryV1] = []
    for source in SERVICE_SOURCE_V1:
        recipe = source.target_recipe

        def resolve_values(values: Iterable[str]) -> tuple[str, ...]:
            resolved: list[str] = []
            for value in values:
                if value == "@stop_units@":
                    resolved.extend(stop_units)
                else:
                    resolved.append(
                        _resolve_recipe_value_v1(value, context, by_id)
                    )
            return tuple(resolved)

        executable = (
            None if recipe.target_executable is None
            else _resolve_recipe_value_v1(
                recipe.target_executable, context, by_id,
            )
        )
        working_directory = (
            None if recipe.target_working_directory is None
            else _resolve_recipe_value_v1(
                recipe.target_working_directory, context, by_id,
            )
        )
        environment = tuple(sorted((
            ServiceEnvironmentV1(
                _validate_environment_name(name, target=True),
                _resolve_recipe_value_v1(value, context, by_id),
            )
            for name, value in recipe.target_environment
        ), key=lambda item: item.name.encode("utf-8")))
        unit_spec = None
        if source.unit_name is not None:
            directives: list[ServiceDirectiveV1] = []
            for directive in source.unit_recipe:
                if directive.values == ("@service_supplementary_gids@",):
                    if not context.service_supplementary_gids:
                        continue
                    values = (" ".join(
                        str(item) for item in context.service_supplementary_gids
                    ),)
                else:
                    values = resolve_values(directive.values)
                value_type = _DIRECTIVE_TYPES.get(
                    (directive.section, directive.name)
                )
                if value_type in {"environment", "path_list", "unit_list"}:
                    values = tuple(sorted(
                        set(values), key=lambda item: item.encode("utf-8"),
                    ))
                directives.append(ServiceDirectiveV1(
                    directive.section, directive.name, str(value_type), values,
                ))
            directives.sort(key=_directive_sort_key)
            unit_spec = make_unit_spec_v1(source.unit_name, directives)
        entries.append(ServiceCatalogEntryV1(
            source.entry_id, source.unit_name, source.external_unit_name,
            (
                ADMINISTRATIVE_ADAPTER_PATH_V1
                if source.class_name == "gated_entrypoint" else None
            ),
            source.class_name, _entry_scope(source.class_name),
            recipe.execution_kind, executable,
            (
                target_hashes[source.entry_id]
                if recipe.execution_kind != "none" else None
            ),
            recipe.python_module, resolve_values(recipe.target_args),
            working_directory, environment, source.timer_target, unit_spec,
            source.class_name in {"gated_service", "gated_entrypoint"},
            source.readiness_owner,
        ))
    return tuple(entries)


def _unit_suffix_for_class(class_name: str) -> str | None:
    return {
        "gated_service": ".service",
        "gated_timer": ".timer",
        "stop_only": ".service",
        "target": ".target",
    }.get(class_name)


def _parse_target_environment(value: object) -> tuple[ServiceEnvironmentV1, ...]:
    if not isinstance(value, list) or len(value) > 256:
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "target environment",
        )
    result: list[ServiceEnvironmentV1] = []
    for raw in value:
        if not isinstance(raw, dict) or set(raw) != _ENVIRONMENT_KEYS:
            raise ServiceCatalogError(
                "birth_ownership_service_catalog_invalid", "target environment schema",
            )
        name = _validate_environment_name(raw.get("name"), target=True)
        env_value = raw.get("value")
        if (
            not isinstance(env_value, str) or "\x00" in env_value
            or len(env_value.encode("utf-8")) > 16 * 1024
        ):
            raise ServiceCatalogError(
                "birth_ownership_service_catalog_invalid", "target environment value",
            )
        result.append(ServiceEnvironmentV1(name, env_value))
    names = [item.name for item in result]
    if names != sorted(names, key=lambda item: item.encode("utf-8")) or len(names) != len(set(names)):
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "target environment order",
        )
    return tuple(result)


def _parse_unit_spec(unit_name: str, value: object) -> ServiceUnitSpecV1:
    if not isinstance(value, dict) or set(value) != _UNIT_SPEC_KEYS:
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "unit spec schema",
        )
    raw_directives = value.get("directives")
    if not isinstance(raw_directives, list):
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "directives",
        )
    spec = make_unit_spec_v1(unit_name, raw_directives)
    declared = _digest(value.get("fragment_hash"), "fragment hash")
    if declared != spec.fragment_hash:
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "fragment hash",
        )
    return spec


def _directive_index(
    unit_spec: ServiceUnitSpecV1 | None,
) -> dict[tuple[str, str], ServiceDirectiveV1]:
    return {
        (item.section, item.name): item
        for item in (() if unit_spec is None else unit_spec.directives)
    }


def _require_gated_service_unit_shape(entry: ServiceCatalogEntryV1) -> None:
    directives = _directive_index(entry.unit_spec)
    required = {
        ("Service", "User"), ("Service", "Group"),
        ("Service", "ExecStartPre"), ("Service", "ExecStart"),
        ("Service", "WorkingDirectory"), ("Service", "KillMode"),
        ("Service", "CapabilityBoundingSet"),
        ("Service", "NoNewPrivileges"),
    }
    if (
        not required.issubset(directives)
        or ("Service", "Environment") in directives
        or ("Service", "ExecStop") in directives
    ):
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "gated service directives",
        )
    if directives[("Service", "WorkingDirectory")].values != ("/",):
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "unit working directory",
        )
    if directives[("Service", "KillMode")].values != ("control-group",):
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "kill mode",
        )
    if (
        directives[("Service", "CapabilityBoundingSet")].values
        != ("CAP_SETGID CAP_SETPCAP CAP_SETUID",)
        or directives[("Service", "NoNewPrivileges")].values != ("yes",)
    ):
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "launcher capabilities",
        )
    group = directives[("Service", "Group")].values[0]
    if _INTEGER_RE.fullmatch(group) is None or group == "0":
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "service gid",
        )
    supplementary = directives.get(("Service", "SupplementaryGroups"))
    if supplementary is not None:
        groups = supplementary.values[0].split(" ")
        if (
            any(_INTEGER_RE.fullmatch(item) is None or item == "0" for item in groups)
            or groups != sorted(set(groups), key=int)
        ):
            raise ServiceCatalogError(
                "birth_ownership_service_catalog_invalid",
                "supplementary groups",
            )
    check = directives[("Service", "ExecStartPre")].values
    launch = directives[("Service", "ExecStart")].values
    expected_tail = (
        "-I", "-S", ADMINISTRATIVE_ADAPTER_PATH_V1,
    )
    if (
        len(check) != 7 or len(launch) != 7
        or not check[0].startswith("!/") or launch[0] != check[0]
        or check[1:4] != expected_tail or launch[1:4] != expected_tail
        or check[4:] != ("check", "--entry-id", entry.entry_id)
        or launch[4:] != ("launch", "--entry-id", entry.entry_id)
    ):
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "administrative command",
        )


def _parse_entry(value: object) -> ServiceCatalogEntryV1:
    if not isinstance(value, dict) or set(value) != _ENTRY_KEYS:
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "entry schema",
        )
    entry_id = value.get("entry_id")
    class_name = value.get("class")
    if not isinstance(entry_id, str) or _ENTRY_ID_RE.fullmatch(entry_id) is None:
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "entry id",
        )
    if (
        not isinstance(class_name, str) or class_name not in _CLASSES
        or value.get("scope") != _entry_scope(class_name)
    ):
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "entry class",
        )
    suffix = _unit_suffix_for_class(str(class_name))
    unit_name = value.get("unit_name")
    if suffix is None:
        if unit_name is not None:
            raise ServiceCatalogError(
                "birth_ownership_service_catalog_invalid", "unit nullability",
            )
    elif (
        not isinstance(unit_name, str) or _UNIT_RE.fullmatch(unit_name) is None
        or not unit_name.endswith(suffix) or len(unit_name.encode("utf-8")) > 192
    ):
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "unit name",
        )
    external_unit_name = value.get("external_unit_name")
    if class_name == "external_dependency":
        if not isinstance(external_unit_name, str) or _UNIT_RE.fullmatch(external_unit_name) is None:
            raise ServiceCatalogError(
                "birth_ownership_service_catalog_invalid", "external unit",
            )
    elif external_unit_name is not None:
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "external unit nullability",
        )
    adapter_path = value.get("adapter_path")
    if class_name == "gated_entrypoint":
        if adapter_path != ADMINISTRATIVE_ADAPTER_PATH_V1:
            raise ServiceCatalogError(
                "birth_ownership_service_catalog_invalid", "adapter path",
            )
    elif adapter_path is not None:
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "adapter nullability",
        )
    execution_kind = value.get("execution_kind")
    if not isinstance(execution_kind, str) or execution_kind not in _EXECUTION_KINDS:
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "execution kind",
        )
    if class_name in {"gated_service", "gated_entrypoint"}:
        if execution_kind not in {"python_module", "native_executable"}:
            raise ServiceCatalogError(
                "birth_ownership_service_catalog_invalid", "executable class",
            )
    elif class_name == "stop_only":
        if execution_kind != "systemctl_stop":
            raise ServiceCatalogError(
                "birth_ownership_service_catalog_invalid", "stop execution",
            )
    elif execution_kind != "none":
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "non executable class",
        )
    target_executable = value.get("target_executable")
    target_hash = value.get("target_executable_hash")
    if execution_kind == "none":
        if target_executable is not None or target_hash is not None:
            raise ServiceCatalogError(
                "birth_ownership_service_catalog_invalid", "target nullability",
            )
    else:
        target_executable = _absolute_path(target_executable, "target executable")
        target_hash = _digest(target_hash, "target executable hash")
    python_module = value.get("python_module")
    if execution_kind == "python_module":
        if (
            not isinstance(python_module, str)
            or _MODULE_RE.fullmatch(python_module) is None
            or len(python_module.encode("utf-8")) > 255
        ):
            raise ServiceCatalogError(
                "birth_ownership_service_catalog_invalid", "python module",
            )
    elif python_module is not None:
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "python module nullability",
        )
    target_args = value.get("target_args")
    if not isinstance(target_args, list) or len(target_args) > 28 or any(
        not isinstance(item, str) or "\x00" in item
        or len(item.encode("utf-8")) > 4096
        for item in target_args
    ):
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "target args",
        )
    target_environment = _parse_target_environment(value.get("target_environment"))
    working_directory = value.get("target_working_directory")
    if execution_kind == "none":
        if target_args or target_environment or working_directory is not None:
            raise ServiceCatalogError(
                "birth_ownership_service_catalog_invalid", "none target fields",
            )
    else:
        working_directory = _absolute_path(working_directory, "target working directory")
    if execution_kind == "systemctl_stop":
        if working_directory != "/" or len(target_args) < 2 or target_args[0] != "stop":
            raise ServiceCatalogError(
                "birth_ownership_service_catalog_invalid", "stop command",
            )
        stop_units = target_args[1:]
        if (
            any(_UNIT_RE.fullmatch(item) is None for item in stop_units)
            or stop_units != sorted(set(stop_units), key=lambda item: item.encode("utf-8"))
            or target_environment
        ):
            raise ServiceCatalogError(
                "birth_ownership_service_catalog_invalid", "stop units",
            )
    timer_target = value.get("timer_target")
    if class_name == "gated_timer":
        if not isinstance(timer_target, str) or _ENTRY_ID_RE.fullmatch(timer_target) is None:
            raise ServiceCatalogError(
                "birth_ownership_service_catalog_invalid", "timer target",
            )
    elif timer_target is not None:
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "timer nullability",
        )
    raw_spec = value.get("unit_spec")
    if class_name in {"gated_entrypoint", "external_dependency"}:
        if raw_spec is not None:
            raise ServiceCatalogError(
                "birth_ownership_service_catalog_invalid", "unit spec nullability",
            )
        unit_spec = None
    else:
        unit_spec = _parse_unit_spec(str(unit_name), raw_spec)
    requires_preflight = value.get("requires_preflight")
    readiness_owner = value.get("readiness_owner")
    if type(requires_preflight) is not bool or requires_preflight != (
        class_name in {"gated_service", "gated_entrypoint"}
    ):
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "preflight flag",
        )
    if type(readiness_owner) is not bool or (readiness_owner and class_name != "gated_service"):
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "readiness flag",
        )
    entry = ServiceCatalogEntryV1(
        entry_id, unit_name, external_unit_name, adapter_path, str(class_name),
        str(value["scope"]), str(execution_kind), target_executable, target_hash,
        python_module, tuple(target_args), working_directory, target_environment,
        timer_target, unit_spec, requires_preflight, readiness_owner,
    )
    directives = _directive_index(unit_spec)
    if class_name == "gated_service":
        _require_gated_service_unit_shape(entry)
    elif ("Service", "ExecStartPre") in directives:
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "unexpected preflight command",
        )
    if class_name == "stop_only":
        start = directives.get(("Service", "ExecStart"))
        allowed = {
            ("Unit", "Description"),
            ("Service", "ExecStart"),
            ("Service", "SyslogIdentifier"),
            ("Service", "TimeoutStartSec"),
            ("Service", "Type"),
        }
        if (
            start is None
            or start.values != (str(target_executable), *tuple(target_args))
            or ("Service", "ExecStop") in directives
            or not set(directives).issubset(allowed)
        ):
            raise ServiceCatalogError(
                "birth_ownership_service_catalog_invalid", "stop unit command",
            )
    elif class_name in {"gated_timer", "target"} and any(
        name in {"ExecStart", "ExecStop"} for _section, name in directives
    ):
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "unexpected command",
        )
    return entry


def _parse_legacy_binding(value: object, entry_ids: frozenset[str]) -> ServiceLegacyBindingV1:
    if not isinstance(value, dict) or set(value) != _LEGACY_KEYS:
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "legacy schema",
        )
    legacy_id = value.get("legacy_id")
    entry_id = value.get("entry_id")
    kind = value.get("kind")
    scope = value.get("scope")
    if (
        not isinstance(legacy_id, str) or _ENTRY_ID_RE.fullmatch(legacy_id) is None
        or not isinstance(entry_id, str) or entry_id not in entry_ids
        or not isinstance(kind, str) or kind not in _LEGACY_KINDS
        or not isinstance(scope, str) or scope not in _LEGACY_SCOPES
        or value.get("disposition") != "retire_in_group7"
    ):
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "legacy binding",
        )
    if kind == "user_unit" and scope != "user" or kind == "system_unit" and scope != "system":
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "legacy scope",
        )
    locator = _validate_legacy_locator(str(kind), value.get("locator"))
    return ServiceLegacyBindingV1(
        legacy_id, str(entry_id), str(kind), str(scope), locator,
        "retire_in_group7",
    )


def _catalog_id(value: Mapping[str, object]) -> str:
    unsigned = dict(value)
    unsigned.pop("catalog_id", None)
    return "sha256:" + hashlib.sha256(
        CATALOG_ID_DOMAIN + _canonical(unsigned)
    ).hexdigest()


def decode_service_catalog_v1(encoded: bytes) -> DecodedServiceCatalogV1:
    if (
        not isinstance(encoded, bytes) or not encoded
        or len(encoded) > MAX_CATALOG_BYTES
    ):
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "catalog size",
        )
    try:
        value = json.loads(encoded.decode("ascii"), object_pairs_hook=_pairs)
    except ServiceCatalogError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "catalog json",
        ) from exc
    if (
        not isinstance(value, dict) or set(value) != _CATALOG_KEYS
        or _canonical(value) != encoded
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
    ):
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "catalog schema",
        )
    declared_id = _digest(value.get("catalog_id"), "catalog id")
    if declared_id != _catalog_id(value):
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "catalog id",
        )
    raw_entries = value.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "entries",
        )
    entries = tuple(_parse_entry(item) for item in raw_entries)
    entry_ids = [item.entry_id for item in entries]
    if (
        entry_ids != sorted(entry_ids, key=lambda item: item.encode("utf-8"))
        or len(entry_ids) != len(set(entry_ids))
    ):
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "entry order",
        )
    unit_names = [
        item.unit_name for item in entries if item.unit_name is not None
    ] + [
        item.external_unit_name for item in entries
        if item.external_unit_name is not None
    ]
    if len(unit_names) != len(set(unit_names)):
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "unit duplicate",
        )
    by_id = {item.entry_id: item for item in entries}
    for item in entries:
        if item.class_name == "gated_timer":
            target = by_id.get(item.timer_target or "")
            if target is None or target.class_name != "gated_service":
                raise ServiceCatalogError(
                    "birth_ownership_service_catalog_invalid", "timer relation",
                )
            directive = _directive_index(item.unit_spec).get(("Timer", "Unit"))
            if directive is None or directive.values != (target.unit_name,):
                raise ServiceCatalogError(
                    "birth_ownership_service_catalog_invalid", "timer unit relation",
                )
    if sum(item.readiness_owner for item in entries) != 1:
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "readiness owner",
        )
    raw_legacy = value.get("legacy_bindings")
    if not isinstance(raw_legacy, list):
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "legacy bindings",
        )
    legacy = tuple(
        _parse_legacy_binding(item, frozenset(entry_ids)) for item in raw_legacy
    )
    legacy_ids = [item.legacy_id for item in legacy]
    if (
        legacy_ids != sorted(legacy_ids, key=lambda item: item.encode("utf-8"))
        or len(legacy_ids) != len(set(legacy_ids))
    ):
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "legacy order",
        )
    return DecodedServiceCatalogV1(
        declared_id, entries, legacy, bytes(encoded),
        service_coverage_hash_v1(encoded),
    )


def _environment_document(value: ServiceEnvironmentV1) -> dict[str, str]:
    return {"name": value.name, "value": value.value}


def _unit_spec_document(value: ServiceUnitSpecV1 | None) -> dict[str, object] | None:
    if value is None:
        return None
    return {
        "fragment_hash": value.fragment_hash,
        "directives": [_directive_document(item) for item in value.directives],
    }


def _entry_document(value: ServiceCatalogEntryV1) -> dict[str, object]:
    return {
        "entry_id": value.entry_id,
        "unit_name": value.unit_name,
        "external_unit_name": value.external_unit_name,
        "adapter_path": value.adapter_path,
        "class": value.class_name,
        "scope": value.scope,
        "execution_kind": value.execution_kind,
        "target_executable": value.target_executable,
        "target_executable_hash": value.target_executable_hash,
        "python_module": value.python_module,
        "target_args": list(value.target_args),
        "target_working_directory": value.target_working_directory,
        "target_environment": [
            _environment_document(item) for item in value.target_environment
        ],
        "timer_target": value.timer_target,
        "unit_spec": _unit_spec_document(value.unit_spec),
        "requires_preflight": value.requires_preflight,
        "readiness_owner": value.readiness_owner,
    }


def _legacy_document(value: ServiceLegacyBindingV1) -> dict[str, object]:
    return {
        "legacy_id": value.legacy_id,
        "entry_id": value.entry_id,
        "kind": value.kind,
        "scope": value.scope,
        "locator": value.locator,
        "disposition": value.disposition,
    }


def _encode_service_catalog_v1(
    entries: Iterable[ServiceCatalogEntryV1],
    legacy_bindings: Iterable[ServiceLegacyBindingV1],
) -> bytes:
    document: dict[str, object] = {
        "schema_version": 1,
        "catalog_id": None,
        "entries": [_entry_document(item) for item in entries],
        "legacy_bindings": [_legacy_document(item) for item in legacy_bindings],
    }
    document["catalog_id"] = _catalog_id(document)
    encoded = _canonical(document)
    decode_service_catalog_v1(encoded)
    return encoded


def _source_identity(
    catalog: DecodedServiceCatalogV1, installation_root: str,
) -> None:
    _absolute_path(installation_root, "installation root")
    if len(catalog.entries) != len(SERVICE_SOURCE_V1):
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "source coverage",
        )
    for observed, source in zip(catalog.entries, SERVICE_SOURCE_V1, strict=True):
        if (
            observed.entry_id != source.entry_id
            or observed.class_name != source.class_name
            or observed.unit_name != source.unit_name
            or observed.external_unit_name != source.external_unit_name
            or observed.timer_target != source.timer_target
            or observed.readiness_owner is not source.readiness_owner
        ):
            raise ServiceCatalogError(
                "birth_ownership_service_catalog_invalid", "source coverage",
            )
    observed_by_id = {item.entry_id: item for item in catalog.entries}
    python_paths = {
        observed_by_id[source.entry_id].target_executable
        for source in SERVICE_SOURCE_V1
        if source.target_recipe.target_executable == "@python@"
    }
    administrative_python: set[str] = set()
    service_users: set[str] = set()
    service_gids: set[int] = set()
    supplementary_gids: set[tuple[int, ...]] = set()
    for source in SERVICE_SOURCE_V1:
        if source.class_name != "gated_service":
            continue
        directives = _directive_index(observed_by_id[source.entry_id].unit_spec)
        check = directives[("Service", "ExecStartPre")].values[0]
        administrative_python.add(check[1:])
        service_users.add(directives[("Service", "User")].values[0])
        service_gids.add(int(directives[("Service", "Group")].values[0]))
        supplementary = directives.get(("Service", "SupplementaryGroups"))
        supplementary_gids.add(
            () if supplementary is None
            else tuple(int(item) for item in supplementary.values[0].split(" "))
        )
    if (
        len(python_paths) != 1
        or python_paths != administrative_python
        or len(service_users) != 1
        or len(service_gids) != 1
        or len(supplementary_gids) != 1
    ):
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "source context",
        )
    data_paths = {
        environment.value
        for source in SERVICE_SOURCE_V1
        for environment in observed_by_id[source.entry_id].target_environment
        if environment.name == "METNOS_USER_DATA"
    }
    workspace_paths = {
        environment.value
        for source in SERVICE_SOURCE_V1
        for environment in observed_by_id[source.entry_id].target_environment
        if environment.name == "METNOS_WORKSPACE"
    }
    data_suffix = "/.local/share/metnos"
    if (
        len(data_paths) != 1
        or not next(iter(data_paths)).endswith(data_suffix)
        or next(iter(data_paths)) == data_suffix
        or workspace_paths
        != {next(iter(data_paths)) + "/workspace"}
    ):
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "service home binding",
        )
    service_home = next(iter(data_paths))[:-len(data_suffix)]
    stop_entry = observed_by_id["stop-stack-quarantine"]
    target_hashes = tuple(
        (item.entry_id, str(item.target_executable_hash))
        for item in catalog.entries if item.execution_kind != "none"
    )
    hashes_by_executable: dict[str, str] = {}
    for item in catalog.entries:
        if item.target_executable is None:
            continue
        previous = hashes_by_executable.setdefault(
            item.target_executable, str(item.target_executable_hash),
        )
        if previous != item.target_executable_hash:
            raise ServiceCatalogError(
                "birth_ownership_service_catalog_invalid", "target hash alias",
            )
    expected_entries = _compile_service_source_v1(_SourceCompileContextV1(
        installation_root, str(next(iter(python_paths))),
        next(iter(service_users)), next(iter(service_gids)),
        next(iter(supplementary_gids)), service_home,
        str(stop_entry.target_executable), target_hashes,
    ))
    if catalog.entries != expected_entries:
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "source recipe",
        )
    expected_legacy = tuple(
        ServiceLegacyBindingV1(
            str(item["legacy_id"]), str(item["entry_id"]), str(item["kind"]),
            str(item["scope"]), str(item["locator"]), str(item["disposition"]),
        )
        for item in legacy_bindings_from_source_v1()
    )
    if catalog.legacy_bindings != expected_legacy:
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "legacy coverage",
        )


def _build_service_catalog_v1(
    *, installation_root: str, python_executable: str, service_user: str,
    service_gid: int, service_supplementary_gids: tuple[int, ...],
    service_home: str, systemctl_executable: str,
    target_executables: tuple[tuple[str, bytes], ...],
) -> _BuiltServiceCatalogV1:
    """Compile the fixed service source against exact executable bytes.

    Callers supply only the closed runtime facts.  Entry coverage, legacy
    bindings, unit fragments and target identities remain derived here from
    ``SERVICE_SOURCE_V1``; no caller can add or remove a catalog entry.
    """
    if type(target_executables) is not tuple or any(
        type(item) is not tuple or len(item) != 2
        or type(item[0]) is not str or type(item[1]) is not bytes
        for item in target_executables
    ):
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "target bytes",
        )
    target_content = dict(target_executables)
    if len(target_content) != len(target_executables):
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "target coverage",
        )
    base_context = _SourceCompileContextV1(
        installation_root, python_executable, service_user, service_gid,
        service_supplementary_gids, service_home, systemctl_executable, (),
    )
    by_id = {item.entry_id: item for item in SERVICE_SOURCE_V1}
    resolved_targets: list[tuple[str, str]] = []
    expected_paths: set[str] = set()
    for source in SERVICE_SOURCE_V1:
        recipe = source.target_recipe
        if recipe.execution_kind == "none":
            continue
        if recipe.target_executable is None:
            raise ServiceCatalogError(
                "birth_ownership_service_catalog_invalid", "source target",
            )
        executable = _resolve_recipe_value_v1(
            recipe.target_executable, base_context, by_id,
        )
        expected_paths.add(executable)
        try:
            content = target_content[executable]
        except KeyError as exc:
            raise ServiceCatalogError(
                "birth_ownership_service_catalog_invalid", "target coverage",
            ) from exc
        resolved_targets.append((
            source.entry_id, target_executable_hash_v1(executable, content),
        ))
    if set(target_content) != expected_paths:
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "target coverage",
        )
    entries = _compile_service_source_v1(_SourceCompileContextV1(
        installation_root, python_executable, service_user, service_gid,
        service_supplementary_gids, service_home, systemctl_executable,
        tuple(resolved_targets),
    ))
    legacy = tuple(ServiceLegacyBindingV1(
        str(item["legacy_id"]), str(item["entry_id"]), str(item["kind"]),
        str(item["scope"]), str(item["locator"]), str(item["disposition"]),
    ) for item in legacy_bindings_from_source_v1())
    encoded = _encode_service_catalog_v1(entries, legacy)
    decoded = decode_service_catalog_v1(encoded)
    _source_identity(decoded, installation_root)
    fragments = tuple(sorted((
        (str(item.unit_name), render_unit_spec_v1(
            str(item.unit_name), item.unit_spec,
        ))
        for item in decoded.entries if item.unit_spec is not None
    ), key=lambda item: item[0].encode("utf-8")))
    return _BuiltServiceCatalogV1(
        encoded, decoded.catalog_id, decoded.service_coverage_hash, fragments,
    )


def _load_verified_service_catalog_v1(verified: object) -> LoadedServiceCatalogV1:
    """Reread the catalog and every fragment from one verified fixed release."""
    from executor_birth_distribution_manifest import (
        DistributionFile,
        VerifiedDistribution,
        _secure_read,
        file_content_hash,
    )

    if type(verified) is not VerifiedDistribution:
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "verified distribution",
        )
    root = PurePosixPath(verified.installation_root)
    catalog_files = [
        item for item in verified.files
        if item.path == CATALOG_PATH_V1 and item.role == "service_catalog"
    ]
    if len(catalog_files) != 1:
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "catalog binding",
        )
    # Path is derived exclusively from the verified fixed release.
    from pathlib import Path

    live_root = Path(root.as_posix())

    def reread(item: DistributionFile) -> bytes:
        content = _secure_read(live_root, item, administrative=True)
        if file_content_hash(item.path, content) != item.content_hash:
            raise ServiceCatalogError(
                "birth_ownership_service_catalog_invalid", "artifact hash",
            )
        return content

    catalog = decode_service_catalog_v1(reread(catalog_files[0]))
    _source_identity(catalog, root.as_posix())
    expected_paths = {
        f"deployment/systemd/{item.unit_name}"
        for item in catalog.entries if item.unit_spec is not None
    }
    service_files = [item for item in verified.files if item.role == "service_unit"]
    observed_paths = [item.path for item in service_files]
    if len(observed_paths) != len(set(observed_paths)) or set(observed_paths) != expected_paths:
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "unit file coverage",
        )
    by_path = {item.path: item for item in service_files}
    fragments: list[tuple[str, bytes]] = []
    for entry in catalog.entries:
        if entry.unit_spec is None:
            continue
        path = f"deployment/systemd/{entry.unit_name}"
        fragment = reread(by_path[path])
        parsed = parse_unit_fragment_v1(str(entry.unit_name), fragment)
        if parsed != entry.unit_spec or render_unit_spec_v1(
            str(entry.unit_name), entry.unit_spec,
        ) != fragment:
            raise ServiceCatalogError(
                "birth_ownership_service_catalog_invalid", "unit fragment binding",
            )
        fragments.append((str(entry.unit_name), fragment))
    return LoadedServiceCatalogV1(
        catalog, tuple(sorted(fragments)), _LOADED_CATALOG_SEAL,
    )


def load_service_catalog_v1(record: object) -> LoadedServiceCatalogV1:
    """Reattest an authenticated record and reread its catalog and units."""
    import sys

    if not sys.platform.startswith("linux"):
        raise ServiceCatalogError("birth_ownership_platform_unsupported")
    from executor_birth_distribution_manifest import (
        AuthenticatedDistributionRecordV1, VerifiedDistribution,
        verify_installed_distribution_record_v1,
    )

    if type(record) is not AuthenticatedDistributionRecordV1:
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "authenticated record",
        )
    verified = verify_installed_distribution_record_v1(record)
    if (
        type(verified) is not VerifiedDistribution
        or verified.installation_root != record.installation_root
    ):
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "installation root",
        )
    return _load_verified_service_catalog_v1(verified)


def capture_current_service_catalog_v1(
    distribution: object,
) -> LoadedServiceCatalogV1:
    """Reverify one sealed current release around an exact catalog capture."""
    import sys

    if not sys.platform.startswith("linux"):
        raise ServiceCatalogError("birth_ownership_platform_unsupported")
    from executor_birth_distribution_manifest import (
        is_verified_distribution,
        verify_current_installation_distribution_v1,
    )

    if not is_verified_distribution(distribution):
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "verified artifact",
        )
    verified = verify_current_installation_distribution_v1(
        distribution.encoded, distribution.signature,
    )
    if verified != distribution:
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "distribution changed",
        )
    loaded = _load_verified_service_catalog_v1(verified)
    repeated = verify_current_installation_distribution_v1(
        verified.encoded, verified.signature,
    )
    if repeated != verified:
        raise ServiceCatalogError(
            "birth_ownership_service_catalog_invalid", "distribution changed",
        )
    return loaded


_validate_service_source_v1()


__all__ = [
    "capture_current_service_catalog_v1",
    "load_service_catalog_v1",
]
