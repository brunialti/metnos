"""Catalog-owned, forward-only quiescence of legacy systemd units."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import PurePosixPath
import subprocess
import sys

from executor_birth_account_identity import (
    PosixAccountRecordV1,
    PosixAccountResolutionError,
    PosixAccountSnapshotChangedError,
    PosixAccountSnapshotV1,
    is_posix_account_name_v1,
    resolve_posix_account_snapshot_v1,
)
from executor_birth_canonical import encode_canonical_ascii_v1
from executor_birth_crypto_framing import framed_sha256_v1
from executor_birth_service_catalog import maintenance_targets_from_source_v1


SYSTEMD_QUIESCENCE_PROTOCOL_V1 = "metnos.executor-birth.systemd-quiescence/1"
_PLAN_DOMAIN_V1 = b"metnos.executor-birth.systemd-quiescence-plan/v1\0"
_SCOPES_V1 = ("system", "user")
_QUIESCENT_STATES_V1 = frozenset({"failed", "inactive"})
_INHIBITED_STATES_V1 = frozenset({
    "disabled", "masked", "masked-runtime", "not-found", "static",
})
_SYSTEMCTL_V1 = "/usr/bin/systemctl"
_RUNUSER_V1 = "/usr/sbin/runuser"
_MAX_OUTPUT_BYTES_V1 = 64 * 1024
_TIMEOUT_SECONDS_V1 = 45


class SystemdQuiescenceError(RuntimeError):
    """Closed failure for invalid plans, effects, or final observations."""

    def __init__(self, detail: str, cause: BaseException | None = None) -> None:
        self.code = "birth_systemd_quiescence_invalid"
        self.detail = detail
        self.internal_cause = cause
        super().__init__(self.code)


def _fail(detail: str, cause: BaseException | None = None):
    raise SystemdQuiescenceError(detail, cause) from cause


@dataclass(frozen=True, slots=True)
class SystemdQuiescenceBatchV1:
    scope: str
    units: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            type(self.scope) is not str or self.scope not in _SCOPES_V1
            or type(self.units) is not tuple or not self.units
            or any(type(unit) is not str or not unit for unit in self.units)
            or self.units != tuple(sorted(set(self.units), key=str.encode))
        ):
            _fail("plan batch")


@dataclass(frozen=True, slots=True)
class SystemdQuiescencePlanV1:
    batches: tuple[SystemdQuiescenceBatchV1, ...]
    plan_digest: str


@dataclass(frozen=True, slots=True)
class SystemdUnitObservationV1:
    scope: str
    unit: str
    load_state: str
    active_state: str
    unit_file_state: str
    main_pid: int

    def __post_init__(self) -> None:
        texts = (
            self.scope, self.unit, self.load_state,
            self.active_state, self.unit_file_state,
        )
        if (
            any(type(value) is not str or not value for value in texts)
            or self.scope not in _SCOPES_V1
            or type(self.main_pid) is not int or self.main_pid < 0
        ):
            _fail("unit observation")


@dataclass(frozen=True, slots=True)
class SystemdQuiescenceProofV1:
    plan_digest: str
    observations: tuple[SystemdUnitObservationV1, ...]


def _catalog_targets_v1() -> tuple[tuple[str, str], ...]:
    targets = maintenance_targets_from_source_v1()
    if type(targets) is not tuple or not targets:
        _fail("catalog targets")
    for target in targets:
        if (
            type(target) is not tuple or len(target) != 2
            or type(target[0]) is not str or target[0] not in _SCOPES_V1
            or type(target[1]) is not str or not target[1]
        ):
            _fail("catalog target")
    ordered = tuple(sorted(set(targets), key=lambda item: (
        item[0].encode("ascii"), item[1].encode("utf-8"),
    )))
    if targets != ordered:
        _fail("catalog target order")
    return targets


def _plan_payload_v1(batches: tuple[SystemdQuiescenceBatchV1, ...]) -> bytes:
    return encode_canonical_ascii_v1({
        "operations": ["disable", "stop", "verify"],
        "protocol": SYSTEMD_QUIESCENCE_PROTOCOL_V1,
        "targets": [
            {"scope": batch.scope, "units": list(batch.units)}
            for batch in batches
        ],
    })


def plan_legacy_systemd_quiescence_v1() -> SystemdQuiescencePlanV1:
    """Derive the sole plan from the canonical service-catalog source."""
    targets = _catalog_targets_v1()
    batches = tuple(
        SystemdQuiescenceBatchV1(
            scope, tuple(unit for selected, unit in targets if selected == scope),
        )
        for scope in _SCOPES_V1 if any(selected == scope for selected, _ in targets)
    )
    if tuple(batch.scope for batch in batches) != _SCOPES_V1:
        _fail("catalog scope coverage")
    digest = framed_sha256_v1(_PLAN_DOMAIN_V1, _plan_payload_v1(batches))
    return SystemdQuiescencePlanV1(batches, digest)


def _require_legacy_snapshot_v1(snapshot: object) -> PosixAccountSnapshotV1:
    if type(snapshot) is not PosixAccountSnapshotV1:
        _fail("legacy account type")
    record, groups = snapshot.record, snapshot.supplementary_gids
    if (
        type(record) is not PosixAccountRecordV1 or type(groups) is not tuple
        or not is_posix_account_name_v1(record.name)
        or type(record.uid) is not int or record.uid <= 0
        or type(record.gid) is not int or record.gid <= 0
        or any(type(value) is not int or value <= 0 for value in groups)
        or groups != tuple(sorted(set(groups))) or record.gid not in groups
        or type(record.home) is not str
        or not PurePosixPath(record.home).is_absolute()
    ):
        _fail("legacy account snapshot")
    return snapshot


def _require_current_legacy_snapshot_v1(snapshot: PosixAccountSnapshotV1) -> None:
    try:
        current = resolve_posix_account_snapshot_v1(snapshot.record.name)
        snapshot.assert_unchanged(current)
    except (PosixAccountResolutionError, PosixAccountSnapshotChangedError) as exc:
        _fail("legacy account changed", exc)


def _command_environment_v1(
    scope: str, snapshot: PosixAccountSnapshotV1,
) -> dict[str, str]:
    environment = {
        "LANG": "C", "LC_ALL": "C",
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    }
    if scope == "user":
        record = snapshot.record
        environment.update({
            "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{record.uid}/bus",
            "HOME": record.home, "LOGNAME": record.name,
            "USER": record.name, "XDG_RUNTIME_DIR": f"/run/user/{record.uid}",
        })
    return environment


def _command_v1(
    scope: str, action: str, units: tuple[str, ...],
    snapshot: PosixAccountSnapshotV1,
) -> tuple[str, ...]:
    command = (_SYSTEMCTL_V1,)
    if scope == "user":
        command = (
            _RUNUSER_V1, "--user", snapshot.record.name, "--",
            _SYSTEMCTL_V1, "--user",
        )
    return (*command, action, "--", *units)


def _run_command_v1(
    command: tuple[str, ...], environment: dict[str, str],
) -> subprocess.CompletedProcess[bytes]:
    try:
        completed = subprocess.run(
            list(command), stdin=subprocess.DEVNULL, capture_output=True,
            check=False, close_fds=True, env=environment, shell=False,
            timeout=_TIMEOUT_SECONDS_V1,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _fail("systemctl execution", exc)
    if (
        type(completed.stdout) is not bytes or type(completed.stderr) is not bytes
        or len(completed.stdout) > _MAX_OUTPUT_BYTES_V1
        or len(completed.stderr) > _MAX_OUTPUT_BYTES_V1
    ):
        _fail("systemctl output")
    return completed


def _parse_show_v1(
    raw: bytes, scope: str, unit: str,
) -> SystemdUnitObservationV1:
    try:
        lines = raw.decode("ascii").splitlines()
        pairs = tuple(line.split("=", 1) for line in lines)
        fields = {key: value for key, value in pairs if len((key, value)) == 2}
    except (UnicodeDecodeError, ValueError) as exc:
        _fail("systemctl show output", exc)
    required = {"LoadState", "ActiveState"}
    admitted = required | {"UnitFileState", "MainPID"}
    if (
        any(len(pair) != 2 for pair in pairs)
        or len(fields) != len(pairs)
        or not required.issubset(fields)
        or not set(fields).issubset(admitted)
    ):
        _fail("systemctl show output")
    try:
        if "MainPID" in fields:
            pid = int(fields["MainPID"])
        elif (
            unit.endswith((".timer", ".target"))
            or fields["LoadState"] == "not-found"
        ):
            pid = 0
        else:
            _fail("systemctl show PID")
    except ValueError as exc:
        _fail("systemctl show PID", exc)
    unit_file_state = fields.get("UnitFileState")
    if not unit_file_state and fields["LoadState"] == "not-found":
        unit_file_state = "not-found"
    elif unit_file_state is None:
        _fail("systemctl show output")
    return SystemdUnitObservationV1(
        scope, unit, fields["LoadState"], fields["ActiveState"],
        unit_file_state, pid,
    )


class _SubprocessSystemdEffectsV1:
    def apply(
        self, action: str, batch: SystemdQuiescenceBatchV1,
        snapshot: PosixAccountSnapshotV1,
    ) -> None:
        if action not in {"disable", "stop"}:
            _fail("systemctl action")
        if batch.scope == "user":
            _require_current_legacy_snapshot_v1(snapshot)
        completed = _run_command_v1(
            _command_v1(batch.scope, action, batch.units, snapshot),
            _command_environment_v1(batch.scope, snapshot),
        )
        if type(completed.returncode) is not int or completed.returncode != 0:
            _fail(f"systemctl {action} {batch.scope}")

    def observe(
        self, scope: str, unit: str, snapshot: PosixAccountSnapshotV1,
    ) -> SystemdUnitObservationV1:
        if scope == "user":
            _require_current_legacy_snapshot_v1(snapshot)
        command = _command_v1(scope, "show", (unit,), snapshot)
        completed = _run_command_v1(
            command[:-2] + (
                "--no-pager", "--plain", "--all",
                "--property=LoadState,ActiveState,UnitFileState,MainPID",
                "--", unit,
            ),
            _command_environment_v1(scope, snapshot),
        )
        return _parse_show_v1(completed.stdout, scope, unit)


def _observations_v1(plan, snapshot, effects) -> tuple[SystemdUnitObservationV1, ...]:
    observed = tuple(
        effects.observe(batch.scope, unit, snapshot)
        for batch in plan.batches for unit in batch.units
    )
    expected = tuple(
        (batch.scope, unit) for batch in plan.batches for unit in batch.units
    )
    if (
        any(type(item) is not SystemdUnitObservationV1 for item in observed)
        or tuple((item.scope, item.unit) for item in observed) != expected
    ):
        _fail("effects observation")
    return observed


def _action_batch_v1(
    action: str, batch: SystemdQuiescenceBatchV1,
    observations: tuple[SystemdUnitObservationV1, ...],
) -> SystemdQuiescenceBatchV1 | None:
    scoped = tuple(item for item in observations if item.scope == batch.scope)
    if tuple(item.unit for item in scoped) != batch.units:
        _fail("action observations")
    if action == "disable":
        units = tuple(
            item.unit for item in scoped
            if item.unit_file_state not in _INHIBITED_STATES_V1
        )
    elif action == "stop":
        units = tuple(
            item.unit for item in scoped
            if item.active_state not in _QUIESCENT_STATES_V1 or item.main_pid != 0
        )
    else:
        _fail("systemctl action")
    return SystemdQuiescenceBatchV1(batch.scope, units) if units else None


def _is_quiescent_v1(observations: tuple[SystemdUnitObservationV1, ...]) -> bool:
    return bool(observations) and all(
        item.active_state in _QUIESCENT_STATES_V1
        and item.main_pid == 0
        and item.unit_file_state in _INHIBITED_STATES_V1
        for item in observations
    )


def _checkpoint_v1(crash_seam, name: str) -> None:
    if crash_seam is not None:
        if not callable(crash_seam):
            _fail("crash seam")
        crash_seam(name)


def _quiesce_core_v1(snapshot, effects, crash_seam=None) -> SystemdQuiescenceProofV1:
    account = _require_legacy_snapshot_v1(snapshot)
    plan = plan_legacy_systemd_quiescence_v1()
    before = _observations_v1(plan, account, effects)
    if _is_quiescent_v1(before):
        return SystemdQuiescenceProofV1(plan.plan_digest, before)
    for batch in plan.batches:
        action_batch = _action_batch_v1("disable", batch, before)
        if action_batch is not None:
            effects.apply("disable", action_batch, account)
        _checkpoint_v1(crash_seam, f"{batch.scope}_disabled")
    after_disable = _observations_v1(plan, account, effects)
    for batch in plan.batches:
        action_batch = _action_batch_v1("stop", batch, after_disable)
        if action_batch is not None:
            effects.apply("stop", action_batch, account)
        _checkpoint_v1(crash_seam, f"{batch.scope}_stopped")
    after = _observations_v1(plan, account, effects)
    if not _is_quiescent_v1(after):
        _fail("legacy units not quiescent")
    _checkpoint_v1(crash_seam, "quiescence_proven")
    return SystemdQuiescenceProofV1(plan.plan_digest, after)


def _quiesce_legacy_systemd_for_test_v1(
    snapshot: object, effects: object, crash_seam=None,
) -> SystemdQuiescenceProofV1:
    if not callable(getattr(effects, "apply", None)) or not callable(
        getattr(effects, "observe", None),
    ):
        _fail("effects port")
    return _quiesce_core_v1(snapshot, effects, crash_seam)


def quiesce_legacy_systemd_v1(
    legacy_account: object,
) -> SystemdQuiescenceProofV1:
    """Disable, stop and prove only the catalog-owned legacy unit set."""
    if not sys.platform.startswith("linux") or os.geteuid() != 0:
        _fail("root Linux required")
    snapshot = _require_legacy_snapshot_v1(legacy_account)
    _require_current_legacy_snapshot_v1(snapshot)
    proof = _quiesce_core_v1(snapshot, _SubprocessSystemdEffectsV1())
    _require_current_legacy_snapshot_v1(snapshot)
    return proof


def _plan_release_systemd_quiescence_v1(loaded) -> SystemdQuiescencePlanV1:
    """Derive a stop-only plan from a reread, authenticated release catalog."""
    from executor_birth_service_catalog import (
        LoadedServiceCatalogV1, _LOADED_CATALOG_SEAL,
    )

    if (
        type(loaded) is not LoadedServiceCatalogV1
        or loaded._seal is not _LOADED_CATALOG_SEAL
    ):
        _fail("release catalog")
    entries = tuple(item for item in loaded.catalog.entries if item.unit_spec is not None)
    units = tuple(sorted(item.unit_name for item in entries))
    if (
        not units or any(item.scope != "system" for item in entries)
        or len(set(units)) != len(units)
        or tuple(name for name, _content in loaded.unit_fragments) != units
    ):
        _fail("release unit coverage")
    batches = (SystemdQuiescenceBatchV1("system", units),)
    payload = encode_canonical_ascii_v1({
        "catalog_id": loaded.catalog.catalog_id,
        "operations": ["stop", "verify"],
        "protocol": SYSTEMD_QUIESCENCE_PROTOCOL_V1,
        "targets": [{"scope": "system", "units": list(units)}],
    })
    return SystemdQuiescencePlanV1(
        batches, framed_sha256_v1(_PLAN_DOMAIN_V1, payload),
    )


def _require_release_observations_v1(observed) -> None:
    """An unknown or missing installed unit is not evidence of safe shutdown."""
    active_states = {
        "active", "activating", "deactivating", "failed", "inactive", "reloading",
    }
    file_states = {
        "enabled", "enabled-runtime", "linked", "linked-runtime", "alias",
        "static", "disabled", "indirect",
    }
    if not observed or any(
        item.load_state != "loaded" or item.active_state not in active_states
        or item.unit_file_state not in file_states
        for item in observed
    ):
        _fail("release unit state")


def _quiesce_release_systemd_core_v1(
    loaded, effects, require_idle, crash_seam=None,
) -> SystemdQuiescenceProofV1:
    """Stop the selected predecessor without altering its signed enablement.

    The G7 caller authenticates the immediate predecessor and retains deployment,
    startup and lifecycle exclusion.  ``require_idle`` must prove that no HTTP
    turn or browser operation will be interrupted before this core stops units.
    This private effect core neither selects a release nor grants those locks.
    """
    if (
        not callable(require_idle) or not callable(getattr(effects, "apply", None))
        or not callable(getattr(effects, "observe", None))
        or crash_seam is not None and not callable(crash_seam)
    ):
        _fail("release effects port")
    plan = _plan_release_systemd_quiescence_v1(loaded)
    before = _observations_v1(plan, None, effects)
    _require_release_observations_v1(before)
    if require_idle() is not True:
        _fail("release work not idle")
    batch = _action_batch_v1("stop", plan.batches[0], before)
    if batch is not None:
        effects.apply("stop", batch, None)
    _checkpoint_v1(crash_seam, "release_units_stopped")
    after = _observations_v1(plan, None, effects)
    _require_release_observations_v1(after)
    if any(
        item.active_state not in _QUIESCENT_STATES_V1 or item.main_pid != 0
        or item.unit_file_state != previous.unit_file_state
        for previous, item in zip(before, after, strict=True)
    ):
        _fail("release units not quiescent")
    _checkpoint_v1(crash_seam, "release_quiescence_proven")
    return SystemdQuiescenceProofV1(plan.plan_digest, after)


__all__ = [
    "SYSTEMD_QUIESCENCE_PROTOCOL_V1",
    "SystemdQuiescenceBatchV1",
    "SystemdQuiescenceError",
    "SystemdQuiescencePlanV1",
    "SystemdQuiescenceProofV1",
    "SystemdUnitObservationV1",
    "plan_legacy_systemd_quiescence_v1",
    "quiesce_legacy_systemd_v1",
]
