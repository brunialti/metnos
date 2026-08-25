"""Inactive RM-0008 F5 guard for exact durable executor attempts.

Construction is explicit and is intentionally absent from the runtime bootstrap
until the F4 admission threshold has been certified.  Once supplied to the
durable bridge, every executor attempt authenticates its current RM-0007
generation, Birth receipt, and exact lifecycle epoch before invocation.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

from contract_store import authenticate_execution_binding
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

    def __init__(
        self,
        *,
        epoch_db_path: Path,
        trusted_publics: Iterable[object],
        admission_verifier_keys: Mapping[str, object],
        store_root: Path | str | None = None,
    ) -> None:
        self._epoch_db_path = Path(epoch_db_path)
        self._trusted_publics = tuple(trusted_publics)
        self._admission_verifier_keys = dict(admission_verifier_keys)
        self._store_root = store_root

    def __call__(self, executor: object) -> ExecutionEpochAttestation:
        name = getattr(executor, "name", None)
        generation_id = getattr(executor, "generation_id", None)
        if not isinstance(name, str) or not name:
            raise DurableBirthGuardError("execution.runner_absent", "name")
        contract_id = _contract_id(getattr(executor, "contract_id", None))
        if not isinstance(generation_id, str):
            raise DurableBirthGuardError("execution.runner_absent", "generation_id")
        try:
            binding = authenticate_execution_binding(
                contract_id,
                generation_id,
                trusted_publics=self._trusted_publics,
                admission_verifier_keys=self._admission_verifier_keys,
                store_root=self._store_root,
            )
        except Exception as exc:
            raise DurableBirthGuardError("execution.runner_absent", "generation") from exc
        if binding.executor_name != name:
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

