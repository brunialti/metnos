"""G7-F: the topology is installed, re-read, and safe to install again."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

import executor_birth_dominant_topology as topology


POSIX_ONLY = pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="a unit fragment needs a systemd manager; the core denies first",
)

_UNIT = b"[Unit]\nDescription=probe\n[Service]\nExecStart=/bin/true\n"
_TIMER = b"[Unit]\nDescription=probe timer\n[Timer]\nOnActiveSec=1s\n"


def _capability(root: Path):
    return topology._TestOnlyTopologyCapabilityV1(root)


@POSIX_ONLY
def test_the_receipt_is_the_reread_not_the_write(tmp_path: Path) -> None:
    """A write that returned success is a claim; a re-read is a fact."""
    installed = topology.install_for_test_v1(_capability(tmp_path), {
        "metnos-probe.service": _UNIT,
        "metnos-probe.timer": _TIMER,
    })

    assert [unit.unit_name for unit in installed] == [
        "metnos-probe.service", "metnos-probe.timer",
    ]
    assert (tmp_path / "metnos-probe.service").read_bytes() == _UNIT
    assert all(unit.mode == topology.UNIT_MODE_V1 for unit in installed)
    assert all(unit.repeated is False for unit in installed)
    # Nothing temporary survives the installation.
    assert not any(item.name.startswith(".") for item in tmp_path.iterdir())


@POSIX_ONLY
def test_installing_the_same_topology_again_is_idempotent(tmp_path: Path) -> None:
    """A resumed installation recognises its own bytes instead of failing."""
    fragments = {"metnos-probe.service": _UNIT}
    first = topology.install_for_test_v1(_capability(tmp_path), fragments)
    second = topology.install_for_test_v1(_capability(tmp_path), fragments)

    assert [unit.repeated for unit in first] == [False]
    assert [unit.repeated for unit in second] == [True]
    assert first[0].content_hash == second[0].content_hash


@POSIX_ONLY
def test_a_name_holding_other_bytes_is_a_collision(tmp_path: Path) -> None:
    """Never an overwrite: those bytes are a topology nobody declared."""
    existing = tmp_path / "metnos-probe.service"
    existing.write_bytes(b"[Unit]\nDescription=someone else\n")
    with pytest.raises(topology.DominantTopologyError) as denied:
        topology.install_for_test_v1(_capability(tmp_path), {
            "metnos-probe.service": _UNIT,
        })
    assert denied.value.code == "topology_unit_collision"
    assert existing.read_bytes() == b"[Unit]\nDescription=someone else\n"


@POSIX_ONLY
def test_the_digest_covers_the_observed_topology(tmp_path: Path) -> None:
    """A unit that differs by one byte must move the digest."""
    first = topology.install_for_test_v1(
        _capability(tmp_path), {"metnos-probe.service": _UNIT},
    )
    other = tmp_path / "other"
    other.mkdir()
    second = topology.install_for_test_v1(
        _capability(other), {"metnos-probe.service": _UNIT + b"\n"},
    )
    assert topology.topology_digest_v1(first) != topology.topology_digest_v1(second)


@POSIX_ONLY
@pytest.mark.parametrize(("case", "code"), [
    ("path_in_name", "topology_unit_name_invalid"),
    ("wrong_extension", "topology_unit_name_invalid"),
    ("hidden", "topology_unit_name_invalid"),
    ("empty_fragment", "topology_fragment_invalid"),
    ("empty_topology", "topology_fragments_invalid"),
    ("existing_link", "topology_unit_link"),
])
def test_installation_denials(tmp_path: Path, case: str, code: str) -> None:
    """Every denial is one row of one table, not one apparatus each."""
    fragments: dict[str, bytes] = {"metnos-probe.service": _UNIT}
    if case == "path_in_name":
        fragments = {"../escape.service": _UNIT}
    elif case == "wrong_extension":
        fragments = {"metnos-probe.conf": _UNIT}
    elif case == "hidden":
        fragments = {".metnos-probe.service": _UNIT}
    elif case == "empty_fragment":
        fragments = {"metnos-probe.service": b""}
    elif case == "empty_topology":
        fragments = {}
    else:
        (tmp_path / "metnos-probe.service").symlink_to(tmp_path / "elsewhere")

    with pytest.raises(topology.DominantTopologyError) as denied:
        topology.install_for_test_v1(_capability(tmp_path), fragments)
    assert denied.value.code == code


@POSIX_ONLY
def test_a_look_alike_capability_does_not_open_the_door(tmp_path: Path) -> None:
    """The type is the credential; a shape with the same field is not."""
    class LookAlike:
        def __init__(self, root: Path) -> None:
            self.root = root

    with pytest.raises(topology.DominantTopologyError) as denied:
        topology.install_for_test_v1(LookAlike(tmp_path), {"a.service": _UNIT})
    assert denied.value.code == "topology_capability_invalid"


def test_windows_denies_before_resolving_any_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A unit fragment on Windows would be a green result proving nothing."""
    monkeypatch.setattr(topology.sys, "platform", "win32")

    def _must_not_run(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the filesystem must not be consulted")

    monkeypatch.setattr(topology.os, "open", _must_not_run)
    with pytest.raises(topology.DominantTopologyError) as denied:
        topology.install_for_test_v1(_capability(tmp_path), {"a.service": _UNIT})
    assert denied.value.code == "topology_unsupported_platform"


def test_no_productive_installer_is_exported() -> None:
    """G7's wrapper owns the authority; importing a name must not."""
    assert "install_for_test_v1" not in topology.__all__
    assert not any(name.startswith("install") for name in topology.__all__)
