from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import tempfile

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import config
from executor_birth_keystore import load_birth_keystore, raw_public_key
from install import birth_authority_provisioning as provisioning
from install.phases import phase3_code
import sign


def _write(path: Path, payload: bytes, mode: int) -> None:
    path.write_bytes(payload)
    if os.name == "posix":
        path.chmod(mode)


def _legacy_keys(root: Path) -> tuple[Ed25519PrivateKey, Ed25519PrivateKey]:
    root.mkdir(mode=0o700)
    author = Ed25519PrivateKey.generate()
    historical = Ed25519PrivateKey.generate()
    _write(root / "author_priv.bin", author.private_bytes_raw(), 0o600)
    _write(root / "author_pub.bin", raw_public_key(author.public_key()), 0o644)
    _write(root / "synt_pub.bin", raw_public_key(historical.public_key()), 0o644)
    return author, historical


def test_provisions_exact_author_and_historical_verifier_ring_idempotently(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "legacy"
    author, historical = _legacy_keys(legacy)
    birth = tmp_path / "birth"

    first = provisioning.provision_author_keystore(
        legacy_keys_dir=legacy,
        birth_dir=birth,
    )
    second = provisioning.provision_author_keystore(
        legacy_keys_dir=legacy,
        birth_dir=birth,
    )

    assert first["created"] is True
    assert second == {**first, "created": False}
    loaded = load_birth_keystore(birth / "author-keystore")
    assert loaded.active_private_key.public_key().public_bytes_raw() == (
        author.public_key().public_bytes_raw()
    )
    assert {
        raw_public_key(key) for key in loaded.verifier_keys.values()
    } == {
        raw_public_key(author.public_key()),
        raw_public_key(historical.public_key()),
    }
    assert len(tuple((birth / "author-keystore" / "private").iterdir())) == 1


def test_valid_existing_store_is_authoritative_without_legacy_comparison(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "legacy"
    _legacy_keys(legacy)
    birth = tmp_path / "birth"
    birth.mkdir(mode=0o700)
    other_legacy = tmp_path / "other-legacy"
    _legacy_keys(other_legacy)
    provisioning.provision_author_keystore(
        legacy_keys_dir=other_legacy,
        birth_dir=birth,
    )
    before = (birth / "author-keystore" / "keystore.json").read_bytes()

    result = provisioning.provision_author_keystore(
        legacy_keys_dir=legacy,
        birth_dir=birth,
    )

    assert (birth / "author-keystore" / "keystore.json").read_bytes() == before
    assert result["created"] is False


def test_rejects_unsafe_or_incomplete_legacy_registry(tmp_path: Path) -> None:
    incomplete = tmp_path / "incomplete"
    incomplete.mkdir(mode=0o700)
    _write(
        incomplete / "author_priv.bin",
        Ed25519PrivateKey.generate().private_bytes_raw(),
        0o600,
    )
    with pytest.raises(
        provisioning.BirthAuthorityProvisioningError,
        match="^birth_author_identity_incomplete$",
    ):
        provisioning.provision_author_keystore(
            legacy_keys_dir=incomplete,
            birth_dir=tmp_path / "birth-incomplete",
        )

    unsafe = tmp_path / "unsafe"
    _legacy_keys(unsafe)
    os.link(unsafe / "author_pub.bin", tmp_path / "leaked-author-pub")
    with pytest.raises(
        provisioning.BirthAuthorityProvisioningError,
        match="^birth_author_provisioning_unsafe",
    ):
        provisioning.provision_author_keystore(
            legacy_keys_dir=unsafe,
            birth_dir=tmp_path / "birth-unsafe",
        )


def test_existing_hardlinked_lock_is_rejected_before_any_write(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "legacy"
    _legacy_keys(legacy)
    birth = tmp_path / "birth"
    birth.mkdir(mode=0o700)
    victim = tmp_path / "victim"
    victim.write_bytes(b"")
    if os.name == "posix":
        victim.chmod(0o600)
    os.link(victim, birth / provisioning._PROVISION_LOCK)

    with pytest.raises(
        provisioning.BirthAuthorityProvisioningError,
        match="^birth_author_provisioning_unsafe",
    ):
        provisioning.provision_author_keystore(
            legacy_keys_dir=legacy,
            birth_dir=birth,
        )

    assert victim.read_bytes() == b""


def test_failed_atomic_install_leaves_no_target_or_temporary_tree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "legacy"
    _legacy_keys(legacy)
    birth = tmp_path / "birth"
    original_install = provisioning._install_directory

    def fail(source: Path, target: Path) -> None:
        if Path(target).name == "author-keystore":
            raise OSError("injected install failure")
        original_install(source, target)

    monkeypatch.setattr(provisioning, "_install_directory", fail)
    with pytest.raises(
        provisioning.BirthAuthorityProvisioningError,
        match="^birth_author_provisioning_unavailable",
    ):
        provisioning.provision_author_keystore(
            legacy_keys_dir=legacy,
            birth_dir=birth,
        )

    assert not (birth / "author-keystore").exists()
    assert not tuple(birth.glob(".author-keystore.staging.*"))


def test_retry_promotes_a_complete_staging_tree(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    _legacy_keys(legacy)
    birth = tmp_path / "birth"
    birth.mkdir(mode=0o700)
    private_raw, public_ring = provisioning._legacy_author_material(legacy)
    config_value, active_id = provisioning._expected_store(
        private_raw, public_ring,
    )
    stage = Path(tempfile.mkdtemp(
        prefix=provisioning._STAGING_PREFIX,
        dir=birth,
    ))
    provisioning._build_stage(
        stage,
        config=config_value,
        active_id=active_id,
        private_raw=private_raw,
        public_ring=public_ring,
    )

    result = provisioning.provision_author_keystore(
        legacy_keys_dir=legacy,
        birth_dir=birth,
    )

    assert result["created"] is True
    assert (birth / "author-keystore").is_dir()
    assert not tuple(birth.glob(".author-keystore.staging.*"))


def test_concurrent_migration_has_one_creator_and_one_idempotent_retry(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "legacy"
    _legacy_keys(legacy)
    birth = tmp_path / "birth"

    def migrate(_index: int) -> dict[str, object]:
        return provisioning.provision_author_keystore(
            legacy_keys_dir=legacy,
            birth_dir=birth,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(migrate, range(2)))

    assert sorted(result["created"] for result in results) == [False, True]
    assert len({result["active_key_id"] for result in results}) == 1
    assert not tuple(birth.glob(".author-keystore.staging.*"))


def test_installed_authority_no_longer_depends_on_legacy_files(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "legacy"
    _legacy_keys(legacy)
    birth = tmp_path / "birth"
    created = provisioning.provision_author_keystore(
        legacy_keys_dir=legacy,
        birth_dir=birth,
    )
    for item in legacy.iterdir():
        item.unlink()
    legacy.rmdir()

    inspected = provisioning.inspect_author_keystore(birth_dir=birth)
    retried = provisioning.provision_author_keystore(
        legacy_keys_dir=legacy,
        birth_dir=birth,
    )

    assert inspected == {**created, "created": False}
    assert retried == inspected


def test_present_invalid_authority_never_falls_back_to_legacy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    birth = tmp_path / "birth"
    birth.mkdir(mode=0o700)
    target = birth / "author-keystore"
    target.mkdir(mode=0o700)
    legacy_consulted = False

    def forbidden(_path: Path):
        nonlocal legacy_consulted
        legacy_consulted = True
        raise AssertionError("legacy must not be consulted")

    monkeypatch.setattr(provisioning, "_legacy_author_material", forbidden)

    with pytest.raises(
        provisioning.BirthAuthorityProvisioningError,
        match="^birth_author_keystore_existing_invalid",
    ):
        provisioning.provision_author_keystore(
            legacy_keys_dir=tmp_path / "legacy",
            birth_dir=birth,
        )
    assert legacy_consulted is False


def test_phase3_wrapper_owns_the_productive_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observed: dict[str, Path] = {}
    monkeypatch.setattr(sign, "KEYS_DIR", tmp_path / "legacy")
    monkeypatch.setattr(config, "PATH_USER_CONFIG", tmp_path / "config")

    def provision(*, legacy_keys_dir: Path, birth_dir: Path):
        observed.update({
            "legacy_keys_dir": legacy_keys_dir,
            "birth_dir": birth_dir,
        })
        return {"created": True}

    def absent(*, birth_dir: Path):
        assert birth_dir == tmp_path / "config" / "birth"
        raise provisioning.BirthAuthorityProvisioningError(
            "birth_author_keystore_unavailable",
        )

    monkeypatch.setattr(provisioning, "inspect_author_keystore", absent)
    monkeypatch.setattr(provisioning, "provision_author_keystore", provision)

    assert phase3_code._provision_birth_author_keystore() == {"created": True}
    assert observed == {
        "legacy_keys_dir": tmp_path / "legacy",
        "birth_dir": tmp_path / "config" / "birth",
    }


def test_phase3_wrapper_uses_installed_authority_without_legacy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(config, "PATH_USER_CONFIG", tmp_path / "config")
    expected = {
        "active_key_id": "ed25519:installed",
        "created": False,
        "verifiers": 2,
    }
    monkeypatch.setattr(
        provisioning,
        "inspect_author_keystore",
        lambda *, birth_dir: expected,
    )

    def legacy_forbidden(**_kwargs):
        raise AssertionError("legacy authority must not be consulted")

    monkeypatch.setattr(
        provisioning, "provision_author_keystore", legacy_forbidden,
    )

    assert phase3_code._provision_birth_author_keystore() == expected


def test_phase3_wrapper_preserves_the_stable_provisioning_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject(**_kwargs):
        raise provisioning.BirthAuthorityProvisioningError(
            "birth_author_keystore_conflict", "different active identity",
        )

    monkeypatch.setattr(provisioning, "inspect_author_keystore", reject)

    with pytest.raises(phase3_code.ContractCatalogInstallError) as caught:
        phase3_code._provision_birth_author_keystore()

    assert caught.value.code == "birth_author_keystore_conflict"
    assert caught.value.detail == "different active identity"
