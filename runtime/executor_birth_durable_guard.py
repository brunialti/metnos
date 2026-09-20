"""RM-0008 F5 guard for exact durable executor attempts.

The guard holds no trust of its own: it authenticates through the sealed
Birth publisher that the runtime bootstrap owns, then binds the served
identity to its exact lifecycle epoch.  Composition is refused while the
installation still owns its lifecycle state in the legacy name-based
stores, so an uncertified installation keeps working unchanged.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from executor_birth_epoch_store import (
    ExecutionEpochAttestation,
    attest_execution_epoch,
)
from manifest_inventory import ContractId, ManifestOrigin


class DurableBirthGuardError(RuntimeError):
    __slots__ = ("code", "detail")

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def _contract_id(value: object) -> ContractId:
    if not isinstance(value, str) or value.count(":") != 1:
        raise DurableBirthGuardError("execution.runner_absent", "contract_id")
    origin, relative = value.split(":", 1)
    try:
        contract_id = ContractId(ManifestOrigin(origin), relative)
    except (TypeError, ValueError) as exc:
        raise DurableBirthGuardError("execution.runner_absent", "contract_id") from exc
    if contract_id.value != value:
        raise DurableBirthGuardError("execution.runner_absent", "contract_id")
    return contract_id


class DurableBirthAttemptGuard:
    """Authenticate one exact loaded executor object for each attempt."""

    __slots__ = ("_authenticate", "_epoch_db_path")

    def __init__(
        self,
        *,
        authenticate: Callable[[ContractId, str], object],
        epoch_db_path: Path,
    ) -> None:
        if not callable(authenticate):
            raise DurableBirthGuardError("execution.runner_absent", "authority")
        self._authenticate = authenticate
        self._epoch_db_path = Path(epoch_db_path)

    def __call__(self, executor: object) -> ExecutionEpochAttestation:
        name = getattr(executor, "name", None)
        generation_id = getattr(executor, "generation_id", None)
        if not isinstance(name, str) or not name:
            raise DurableBirthGuardError("execution.runner_absent", "name")
        contract_id = _contract_id(getattr(executor, "contract_id", None))
        if not isinstance(generation_id, str):
            raise DurableBirthGuardError("execution.runner_absent", "generation_id")
        try:
            binding = self._authenticate(contract_id, generation_id)
        except Exception as exc:
            raise DurableBirthGuardError("execution.runner_absent", "generation") from exc
        if getattr(binding, "executor_name", None) != name:
            raise DurableBirthGuardError("execution.runner_absent", "name_mismatch")
        try:
            return attest_execution_epoch(
                contract_id=contract_id,
                generation_id=generation_id,
                name=name,
                db_path=self._epoch_db_path,
            )
        except Exception as exc:
            code = getattr(exc, "code", "execution.runner_absent")
            if code not in {
                "execution.runner_absent", "execution.dormant",
                "execution.retired", "execution.quarantined",
            }:
                code = "execution.runner_absent"
            raise DurableBirthGuardError(code) from exc


def productive_birth_attempt_guard() -> DurableBirthAttemptGuard | None:
    """Compose the guard from installed state, or report the legacy owner.

    ``None`` means this installation has not migrated: the existing
    name-based lifecycle readers remain authoritative and the durable
    service composes exactly as it did before F5.  Once the migration
    marker exists the epoch store is the only lifecycle source, so a
    missing epoch database is a fault and not a reason to run unguarded.
    """
    import config
    from executor_birth_activation_mode import BirthStateOwner, read_birth_activation_state
    from executor_birth_bootstrap import bootstrap_birth_runtime, _secure_state_dir

    if read_birth_activation_state().owner is BirthStateOwner.LEGACY:
        return None
    state = _secure_state_dir(Path(config.PATH_USER_STATE) / "birth")
    epochs = state / "executor_epochs.sqlite"
    if not epochs.is_file():
        raise DurableBirthGuardError("execution.runner_absent", "f5_epoch_migration_required")
    publisher = bootstrap_birth_runtime().core.commit_publisher
    return DurableBirthAttemptGuard(
        authenticate=publisher.authenticate_execution_binding,
        epoch_db_path=epochs,
    )
