"""Blocking calibration for RM-0008 Windows identities and ACL evidence."""
from __future__ import annotations

import ctypes
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest

import win32_identity_oracle as oracle


@dataclass(slots=True)
class _IdentityLab:
    root: Path
    probe: Path
    service: oracle.LocalAccount
    outsider: oracle.LocalAccount

    def file(
        self, name: str, profile: str, *, corrupted_for: str | None = None
    ) -> Path:
        path = self.root / name
        path.write_bytes(b"rm-0008-real-identity-oracle\n")
        oracle.apply_profile(
            path,
            profile,
            self.service.sid,
            directory=False,
            extra_read_sid=corrupted_for,
        )
        return path

    def directory(self, name: str, profile: str) -> Path:
        path = self.root / name
        path.mkdir()
        (path / "visible.txt").write_bytes(b"enumeration sentinel\n")
        oracle.apply_profile(
            path, profile, self.service.sid, directory=True
        )
        return path

    def run(
        self,
        account: oracle.LocalAccount,
        operation: str,
        target: Path,
        *,
        directory: bool = False,
    ) -> int:
        return oracle.run_probe_as(
            account,
            self.probe,
            operation,
            target,
            directory=directory,
        )


@pytest.fixture(scope="session")
def identity_lab() -> _IdentityLab:
    assert ctypes.sizeof(ctypes.c_void_p) == 8, "the oracle requires Windows x64"
    parent = oracle.current_token_facts()
    assert parent.elevated, "the controller is not elevated"
    assert parent.administrator, "the controller is not an administrator"
    assert parent.integrity_rid >= 0x3000, "the controller integrity is below high"

    service: oracle.LocalAccount | None = None
    outsider: oracle.LocalAccount | None = None
    root: Path | None = None
    cleanup_errors: list[BaseException] = []
    try:
        service = oracle.create_standard_account("mt8s")
        outsider = oracle.create_standard_account("mt8o")
        assert service.sid.casefold() != outsider.sid.casefold()

        public = os.environ.get("PUBLIC")
        assert public, "Windows did not expose the public profile directory"
        root = Path(public) / f"metnos-rm0008-{uuid.uuid4().hex}"
        root.mkdir()
        oracle.assert_supported_volume(root)
        probe = root / "win32_identity_oracle.py"
        shutil.copyfile(Path(oracle.__file__).resolve(), probe)
        oracle.apply_profile(
            probe, "integrity_only", service.sid, directory=False
        )
        oracle.assert_exact_profile(
            probe, "integrity_only", service.sid, directory=False
        )
        oracle.apply_profile(
            root, "integrity_only", service.sid, directory=True
        )
        yield _IdentityLab(root, probe, service, outsider)
    finally:
        if root is not None and root.exists():
            try:
                shutil.rmtree(root)
            except BaseException as exc:
                cleanup_errors.append(exc)
        for account in (outsider, service):
            if account is not None:
                try:
                    oracle.delete_account(account)
                except BaseException as exc:
                    cleanup_errors.append(exc)
        if cleanup_errors:
            raise BaseExceptionGroup(
                "RM-0008 Windows identity cleanup did not complete", cleanup_errors
            )


def _assert_access(
    lab: _IdentityLab,
    account: oracle.LocalAccount,
    operation: str,
    target: Path,
    expected: int,
    *,
    directory: bool = False,
) -> None:
    observed = lab.run(
        account, operation, target, directory=directory
    )
    assert observed == expected, (
        f"{operation} returned probe status {observed}; expected {expected}"
    )


def test_fresh_accounts_produce_distinct_real_standard_tokens(
    identity_lab: _IdentityLab,
) -> None:
    lab = identity_lab
    for account in (lab.service, lab.outsider):
        _assert_access(
            lab,
            account,
            "noop",
            lab.root,
            oracle.ACCESS_RESULT_ALLOWED,
            directory=True,
        )


@pytest.mark.parametrize("profile", ["confidential", "integrity_only"])
def test_independent_structural_oracle_accepts_only_the_closed_profiles(
    identity_lab: _IdentityLab, profile: str
) -> None:
    lab = identity_lab
    file_path = lab.file(f"exact-{profile}.bin", profile)
    directory_path = lab.directory(f"exact-{profile}-dir", profile)
    oracle.assert_exact_profile(
        file_path, profile, lab.service.sid, directory=False
    )
    oracle.assert_exact_profile(
        directory_path, profile, lab.service.sid, directory=True
    )


def test_negative_sentinel_detects_and_exposes_an_overbroad_confidential_acl(
    identity_lab: _IdentityLab,
) -> None:
    lab = identity_lab
    path = lab.file(
        "negative-overbroad.bin",
        "confidential",
        corrupted_for=lab.outsider.sid,
    )
    with pytest.raises(AssertionError, match="unexpected number of ACEs"):
        oracle.assert_exact_profile(
            path, "confidential", lab.service.sid, directory=False
        )
    _assert_access(
        lab,
        lab.outsider,
        "read",
        path,
        oracle.ACCESS_RESULT_ALLOWED,
    )


def test_negative_sentinel_rejects_an_owner_other_than_system(
    identity_lab: _IdentityLab,
) -> None:
    lab = identity_lab
    path = lab.root / "negative-owner.bin"
    path.write_bytes(b"wrong owner\n")
    oracle.apply_profile(
        path,
        "confidential",
        lab.service.sid,
        directory=False,
        owner="BA",
    )
    with pytest.raises(AssertionError, match="owner is not SYSTEM"):
        oracle.assert_exact_profile(
            path, "confidential", lab.service.sid, directory=False
        )


def test_negative_sentinel_rejects_and_exposes_a_service_write_grant(
    identity_lab: _IdentityLab,
) -> None:
    lab = identity_lab
    path = lab.root / "negative-service-write.bin"
    path.write_bytes(b"overbroad service access\n")
    oracle.apply_profile(
        path,
        "confidential",
        lab.service.sid,
        directory=False,
        service_mask=0x0012019F,
    )
    with pytest.raises(AssertionError, match="ACE mask differs"):
        oracle.assert_exact_profile(
            path, "confidential", lab.service.sid, directory=False
        )
    _assert_access(
        lab,
        lab.service,
        "write",
        path,
        oracle.ACCESS_RESULT_ALLOWED,
    )


@pytest.mark.parametrize("profile", ["confidential", "integrity_only"])
def test_real_service_token_has_read_only_effective_access(
    identity_lab: _IdentityLab, profile: str
) -> None:
    lab = identity_lab
    file_path = lab.file(f"service-{profile}.bin", profile)
    directory_path = lab.directory(f"service-{profile}-dir", profile)

    _assert_access(
        lab,
        lab.service,
        "read",
        file_path,
        oracle.ACCESS_RESULT_ALLOWED,
    )
    _assert_access(
        lab,
        lab.service,
        "read",
        directory_path,
        oracle.ACCESS_RESULT_ALLOWED,
        directory=True,
    )
    for operation in ("write", "append", "delete", "write_dac"):
        _assert_access(
            lab,
            lab.service,
            operation,
            file_path,
            oracle.ACCESS_RESULT_DENIED,
        )
    for operation in ("write", "delete", "delete_child", "write_dac"):
        _assert_access(
            lab,
            lab.service,
            operation,
            directory_path,
            oracle.ACCESS_RESULT_DENIED,
            directory=True,
        )
    _assert_access(
        lab,
        lab.service,
        "create_child",
        directory_path,
        oracle.ACCESS_RESULT_DENIED,
        directory=True,
    )
    assert not (directory_path / "unexpected.secret").exists()


@pytest.mark.parametrize(
    ("profile", "read_result"),
    [
        ("confidential", oracle.ACCESS_RESULT_DENIED),
        ("integrity_only", oracle.ACCESS_RESULT_ALLOWED),
    ],
)
def test_real_outsider_token_obeys_confidentiality_and_cannot_mutate(
    identity_lab: _IdentityLab, profile: str, read_result: int
) -> None:
    lab = identity_lab
    file_path = lab.file(f"outsider-{profile}.bin", profile)
    directory_path = lab.directory(f"outsider-{profile}-dir", profile)

    _assert_access(lab, lab.outsider, "read", file_path, read_result)
    _assert_access(
        lab,
        lab.outsider,
        "read",
        directory_path,
        read_result,
        directory=True,
    )
    for operation in ("write", "append", "delete", "write_dac"):
        _assert_access(
            lab,
            lab.outsider,
            operation,
            file_path,
            oracle.ACCESS_RESULT_DENIED,
        )
    for operation in ("write", "delete", "delete_child", "write_dac"):
        _assert_access(
            lab,
            lab.outsider,
            operation,
            directory_path,
            oracle.ACCESS_RESULT_DENIED,
            directory=True,
        )
    _assert_access(
        lab,
        lab.outsider,
        "create_child",
        directory_path,
        oracle.ACCESS_RESULT_DENIED,
        directory=True,
    )
    assert not (directory_path / "unexpected.secret").exists()


def test_oracle_source_is_independent_from_product_security_helpers() -> None:
    source = Path(oracle.__file__).read_text(encoding="utf-8")
    forbidden = (
        "executor_birth_secure_fs",
        "_win_sddl",
        "_win_verify_security",
        "AccessCheck(",
    )
    assert all(symbol not in source for symbol in forbidden)
    assert "GetSecurityInfo" in source
    assert "GetAce" in source
    assert "EqualSid" in source

