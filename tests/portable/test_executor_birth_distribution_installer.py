"""G6-B4: the private publication core, its idempotence and its denials."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

import executor_birth_distribution_installer as installer


POSIX_ONLY = pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="the core denies on Windows before touching the filesystem",
)


def _staging(root: Path, files: dict[str, bytes]) -> Path:
    staging = root / "staging"
    for relative, content in files.items():
        path = staging / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        path.chmod(0o644)
    return staging


def _capability(
    staging: Path, final: Path, bundle_hash: str | None = None,
) -> installer._TestOnlyPublicationCapabilityV1:
    if bundle_hash is None:
        bundle_hash = installer.bundle_hash_v1(
            installer.observe_staging_v1(staging),
        )
    return installer._TestOnlyPublicationCapabilityV1(
        staging, final, bundle_hash,
    )


@POSIX_ONLY
def test_publication_is_complete_only_after_the_reread(tmp_path: Path) -> None:
    """The receipt is the re-read, not the rename."""
    staging = _staging(tmp_path, {
        "admin/preflight.py": b"# admin\n",
        "systemd/probe.service": b"[Unit]\n",
    })
    final = tmp_path / "releases" / "00000000000000000001"
    final.parent.mkdir(parents=True)
    expected = installer.bundle_hash_v1(installer.observe_staging_v1(staging))

    published = installer.publish_for_test_v1(_capability(staging, final))

    assert published.repeated is False
    assert published.bundle_hash == expected
    assert Path(published.final_path) == final
    assert not staging.exists()
    assert {item.relative_path for item in published.files} == {
        "admin/preflight.py", "systemd/probe.service",
    }
    assert (final / "admin/preflight.py").read_bytes() == b"# admin\n"


@POSIX_ONLY
def test_exact_repetition_is_idempotent(tmp_path: Path) -> None:
    """A publication interrupted after the rename must be resumable."""
    staging = _staging(tmp_path, {"admin/preflight.py": b"# admin\n"})
    final = tmp_path / "releases" / "00000000000000000001"
    final.parent.mkdir(parents=True)
    expected = installer.bundle_hash_v1(installer.observe_staging_v1(staging))
    first = installer.publish_for_test_v1(_capability(staging, final))

    # The staging tree is gone; the resumed attempt observes the final name.
    repeated = installer.publish_for_test_v1(
        _capability(final, final, expected),
    )
    assert first.repeated is False
    assert repeated.repeated is True
    assert repeated.bundle_hash == first.bundle_hash


@POSIX_ONLY
@pytest.mark.parametrize(("case", "code"), [
    ("collision", "final_name_collision"),
    ("changed_bytes", "staging_bundle_mismatch"),
    ("changed_metadata", "staging_bundle_mismatch"),
    ("empty_staging", "staging_empty"),
    ("link_in_staging", "staging_link"),
    ("final_name_is_link", "final_name_link"),
])
def test_publication_denials(tmp_path: Path, case: str, code: str) -> None:
    """Every denial is one row of one table, not one apparatus each."""
    staging = _staging(tmp_path, {"admin/preflight.py": b"# admin\n"})
    final = tmp_path / "releases" / "00000000000000000001"
    final.parent.mkdir(parents=True)
    expected = installer.bundle_hash_v1(installer.observe_staging_v1(staging))

    if case == "collision":
        final.mkdir()
        (final / "other.txt").write_bytes(b"someone else\n")
    elif case == "changed_bytes":
        (staging / "admin/preflight.py").write_bytes(b"# tampered\n")
    elif case == "changed_metadata":
        (staging / "admin/preflight.py").chmod(0o600)
    elif case == "empty_staging":
        for path in sorted(staging.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
    elif case == "link_in_staging":
        (staging / "admin/alias.py").symlink_to("preflight.py")
    elif case == "final_name_is_link":
        final.symlink_to(tmp_path / "elsewhere")

    with pytest.raises(installer.DistributionInstallerError) as denied:
        installer.publish_for_test_v1(_capability(staging, final, expected))
    assert denied.value.code == code
    # A denial publishes nothing: either the final name was never created, or
    # it still holds exactly what it held before.
    if case == "collision":
        assert {item.name for item in final.iterdir()} == {"other.txt"}
    elif case != "final_name_is_link":
        assert not final.exists()


@POSIX_ONLY
def test_only_the_nominal_capability_reaches_the_core(tmp_path: Path) -> None:
    """A look-alike does not open the door; the type is the credential."""
    staging = _staging(tmp_path, {"admin/preflight.py": b"# admin\n"})
    final = tmp_path / "releases" / "00000000000000000001"
    final.parent.mkdir(parents=True)
    expected = installer.bundle_hash_v1(installer.observe_staging_v1(staging))

    class LookAlike:
        def __init__(self) -> None:
            self.staging_root = staging
            self.final_path = final
            self.expected_bundle_hash = expected

    with pytest.raises(installer.DistributionInstallerError) as denied:
        installer.publish_for_test_v1(LookAlike())
    assert denied.value.code == "test_capability_invalid"
    assert not final.exists()


def test_no_productive_publisher_is_exported() -> None:
    """The module exposes no installer; G6-D owns the authority that wraps it.

    Publication must not become reachable by importing a name. Everything a
    caller can reach from `__all__` observes or frames; nothing publishes.
    """
    assert "publish_for_test_v1" not in installer.__all__
    assert not any(
        name.startswith("install") or name.startswith("publish")
        for name in installer.__all__
    )


def test_windows_denies_before_consulting_the_filesystem(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The denial does not depend on how well an emulation held."""
    monkeypatch.setattr(installer.sys, "platform", "win32")

    def _must_not_run(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the filesystem must not be consulted")

    monkeypatch.setattr(installer.os, "open", _must_not_run)
    with pytest.raises(installer.DistributionInstallerError) as denied:
        installer.observe_staging_v1(tmp_path / "absent")
    assert denied.value.code == "unsupported_platform"
