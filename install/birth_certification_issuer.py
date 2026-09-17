"""Issue the F5 certificate from evidence this installation can reread.

The certificate attests one derived qualification and one completed migration.
It is not the evidence: the runtime reader authenticates this attestation, not
the history behind it, which is exactly why the issuer must compose that
history itself. Nothing here accepts a count, a receipt list or a cycle
outcome from its caller.

Order matters and is not negotiable. A certificate before the migration would
authorise a lifecycle the installation has not moved to, so the marker is read
first and its absence refuses. The signed document is then verified by the very
reader that will trust it, because one this writer accepts and that reader
refuses is the failure an operator cannot diagnose.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from executor_birth_authority_files import (
    DEFAULT_OWNERSHIP_ROOT_V1, OwnershipAuthorityError, _directory_metadata,
    _managed_authority_platform_supported_v1, _read_regular, _root_owned_chain,
)
from executor_birth_canonical import encode_canonical_ascii_v1
from install.birth_ownership_authority_provisioner import (
    _provisioning_lock, _sync_directory,
)


ACTIVATION_DIRECTORY_V1 = DEFAULT_OWNERSHIP_ROOT_V1 / "certification-v1"
CERTIFICATE_BASENAME_V1 = "active.json"


class CertificationIssueError(RuntimeError):
    __slots__ = ("code", "detail")

    def __init__(self, code: str, detail: str = "") -> None:
        self.code, self.detail = code, detail
        super().__init__(f"{code}: {detail}" if detail else code)


def _require_root_v1() -> None:
    if not _managed_authority_platform_supported_v1():
        raise CertificationIssueError("certification_platform_unsupported")
    if getattr(os, "geteuid", lambda: -1)() != 0:
        raise CertificationIssueError("certification_root_required")


def observe_history_v1(
    *, public_sources: tuple[tuple[str, bytes], ...] = (),
) -> object:
    """Compose the authenticated historical reconciliation from its owners.

    The issuer reads the history rather than receiving it. Public archive
    candidates are the one input, and they are untrusted bytes: the declaration
    owner accepts them only where path, role, size and hash match the
    historical signed distribution. Both raw inventories are reread afterwards,
    because a history that moved during the join was never one observation.
    """
    from contract_store import read_historical_birth_inventory_v1
    from executor_birth_history import reconcile_historical_birth_v1
    from executor_birth_ownership_chain import (
        OwnershipChainStore, VerifiedOwnershipWindowV1,
    )
    from executor_birth_prepared_root import (
        load_historical_producer_declarations_for_contexts_v1,
    )
    from executor_birth_producer_store import read_producer_history_v1

    chain = OwnershipChainStore().read_required_window_v1()
    if not isinstance(chain, VerifiedOwnershipWindowV1):
        raise CertificationIssueError("certification_frontier_invalid")
    producer_history = read_producer_history_v1()
    inventory = read_historical_birth_inventory_v1()
    # Selectors only: the join verifies every receipt, its physical locator
    # and its independent reread.
    wanted = set()
    for contract in inventory.contracts:
        for located in contract.receipts:
            try:
                wanted.add(json.loads(located.encoded)["admission_context_id"])
            except (KeyError, TypeError, ValueError, UnicodeDecodeError) as exc:
                raise CertificationIssueError(
                    "certification_history_unreadable", "admission context") from exc
    known = {item.prepared_admission_context_id for item in chain.context_transitions}
    declarations = load_historical_producer_declarations_for_contexts_v1(
        tuple(sorted(wanted & known)), include_reattestation=True,
        public_sources=tuple(public_sources),
    )
    if any(item.context.required_head_id != chain.required_head.head_id
           for item in declarations):
        raise CertificationIssueError("certification_frontier_changed")
    joined = reconcile_historical_birth_v1(inventory, producer_history, declarations)
    if (read_historical_birth_inventory_v1() != inventory
            or read_producer_history_v1() != producer_history):
        raise CertificationIssueError("certification_history_changed")
    if joined.required_head_id != chain.required_head.head_id:
        raise CertificationIssueError("certification_frontier_changed", "join")
    return joined


def _completed_migration_id() -> str:
    """Read the marker, then prove the migration it names is really complete."""
    import config
    from executor_birth_activation_mode import (
        BirthStateOwner, read_birth_activation_state,
    )
    from executor_birth_lifecycle_migration import (
        LifecycleMigrationError, verify_migration_v1,
    )

    state = read_birth_activation_state()
    if state.owner is BirthStateOwner.LEGACY:
        # Certifying here would authorise a lifecycle this installation has
        # not moved to. The migration comes first, always.
        raise CertificationIssueError("certification_before_migration")
    epoch_db = Path(config.PATH_USER_STATE) / "birth" / "executor_epochs.sqlite"
    try:
        verify_migration_v1(state.migration_id, epoch_db_path=epoch_db)
    except LifecycleMigrationError as exc:
        raise CertificationIssueError(exc.code, exc.detail) from exc
    return state.migration_id


def _installation_frontier_v1() -> tuple[str, str, str]:
    """The exact installation, head and closed build this certificate binds."""
    from executor_birth_lifecycle import _installation_id_v1
    from executor_birth_ownership_authorities import (
        load_ownership_public_registries_v1,
    )
    from executor_birth_ownership_chain import (
        inspect_required_ownership_v1, VerifiedOwnershipWindowV1,
    )

    window = inspect_required_ownership_v1()
    if type(window) is not VerifiedOwnershipWindowV1:
        raise CertificationIssueError("certification_frontier_invalid")
    return (
        _installation_id_v1(load_ownership_public_registries_v1()),
        window.required_head.head_id,
        window.required_distribution.identity.closed_build_id,
    )


def _sign_certificate_v1(payload: dict) -> bytes:
    """Sign with the dedicated key, which never leaves this function."""
    from executor_birth_certification_authority import DEFAULT_DIRECTORY_V1, PRIVATE_BASENAME_V1
    from install.birth_certification_authority_provisioner import _verify_pair

    _root_owned_chain(DEFAULT_DIRECTORY_V1)
    public = _verify_pair(DEFAULT_DIRECTORY_V1, root_owned=True,
                          forbidden_public_keys=frozenset())
    if public.status != "active":
        raise CertificationIssueError("certification_authority_revoked")
    if payload["key_id"] != public.key_id:
        raise CertificationIssueError("certification_authority_mismatch")
    raw = _read_regular(DEFAULT_DIRECTORY_V1 / PRIVATE_BASENAME_V1,
                        maximum=32, mode=0o600, root_owned=True)
    private = Ed25519PrivateKey.from_private_bytes(raw)
    del raw
    from executor_birth_lifecycle import CERTIFICATION_DOMAIN

    signature = private.sign(CERTIFICATION_DOMAIN + encode_canonical_ascii_v1(payload))
    return encode_canonical_ascii_v1({
        **payload, "signature": base64.b64encode(signature).decode("ascii"),
    })


def build_certificate_v1(
    *, qualification_id: str, migration_id: str, installation_id: str,
    head_id: str, closed_build_id: str, key_id: str,
) -> dict:
    """The frozen schema-1 payload, without its signature."""
    from executor_birth_lifecycle import ACTIVATION_POLICY

    for name, value in (
        ("qualification_id", qualification_id), ("migration_id", migration_id),
        ("installation_id", installation_id), ("head_id", head_id),
        ("closed_build_id", closed_build_id),
    ):
        if not isinstance(value, str) or not value.startswith("sha256:"):
            raise CertificationIssueError("certification_binding_invalid", name)
    if not isinstance(key_id, str) or not key_id:
        raise CertificationIssueError("certification_binding_invalid", "key_id")
    return {
        "schema_version": 1, "purpose": "f5_activation_v1",
        "installation_id": installation_id, "qualification_id": qualification_id,
        "head_id": head_id, "closed_build_id": closed_build_id,
        "migration_id": migration_id, "policy_id": ACTIVATION_POLICY,
        "key_id": key_id,
    }


def _install_certificate_v1(encoded: bytes) -> Path:
    """Publish the certificate, then read it back with the runtime's reader."""
    from executor_birth_lifecycle import ACTIVATION_MAX_BYTES, load_f5_activation

    _root_owned_chain(DEFAULT_OWNERSHIP_ROOT_V1)
    with _provisioning_lock(DEFAULT_OWNERSHIP_ROOT_V1, root_owned=True):
        directory = ACTIVATION_DIRECTORY_V1
        _directory_metadata(directory, root_owned=True)
        path = directory / CERTIFICATE_BASENAME_V1
        staged = directory / (CERTIFICATE_BASENAME_V1 + ".staged")
        descriptor = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            os.write(descriptor, encoded)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.chmod(staged, 0o644)
        os.replace(staged, path)
        _sync_directory(directory)
        if _read_regular(path, maximum=ACTIVATION_MAX_BYTES, mode=0o644,
                         root_owned=True) != encoded:
            raise CertificationIssueError("certification_document_unsafe")
    try:
        accepted = load_f5_activation().certificate
    except Exception as exc:
        raise CertificationIssueError(
            "certification_document_refused", getattr(exc, "code", type(exc).__name__),
        ) from exc
    return path


def issue_certificate_v1(
    *, apply: bool, public_sources: tuple[tuple[str, bytes], ...] = (),
) -> dict:
    """Derive, bind and sign, or report exactly what would be signed."""
    from install.birth_certification_evidence import administrative_evidence_v1
    from install.birth_certification_qualification import (
        QualificationRefused, derive_qualification_v1,
    )

    _require_root_v1()
    migration_id = _completed_migration_id()
    installation_id, head_id, closed_build_id = _installation_frontier_v1()
    observed = observe_history_v1(public_sources=public_sources)
    with administrative_evidence_v1() as evidence:
        frontier = evidence.frontier
    try:
        qualification = derive_qualification_v1(observed, frontier)
    except QualificationRefused as exc:
        raise CertificationIssueError(exc.code, exc.detail) from exc
    if qualification.required_head_id != head_id:
        raise CertificationIssueError("certification_frontier_changed", "qualification")
    report = {
        "qualification_id": qualification.qualification_id,
        "technical_admissions": qualification.technical_admissions,
        "authenticated_producers": list(qualification.authenticated_producers),
        "cycle_ids": list(qualification.cycle_ids),
        "evidence_head": qualification.evidence_head,
        "evidence_scope_id": qualification.evidence_scope_id,
        "migration_id": migration_id, "installation_id": installation_id,
        "head_id": head_id, "closed_build_id": closed_build_id,
        "certificate": None,
    }
    if not apply:
        return report
    from executor_birth_certification_authority import load_certification_public_key_v1

    payload = build_certificate_v1(
        qualification_id=qualification.qualification_id, migration_id=migration_id,
        installation_id=installation_id, head_id=head_id,
        closed_build_id=closed_build_id,
        key_id=load_certification_public_key_v1().key_id,
    )
    report["certificate"] = str(_install_certificate_v1(_sign_certificate_v1(payload)))
    return report


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments not in (["derive"], ["issue"]):
        print("usage: birth_certification_issuer.py derive|issue", file=sys.stderr)
        return 64
    try:
        report = issue_certificate_v1(apply=arguments == ["issue"])
    except (CertificationIssueError, OwnershipAuthorityError) as exc:
        print(json.dumps({"error": exc.code, "detail": exc.detail}), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - administrative entry point
    raise SystemExit(main())
