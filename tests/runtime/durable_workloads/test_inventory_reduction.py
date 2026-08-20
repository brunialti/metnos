from __future__ import annotations

import os

import pytest

from durable_workloads.inventory import (
    InventoryLimits,
    InventorySealError,
    seal_local_inventory,
)
from durable_workloads.reduction import ReductionPlanError, build_reduction_graph


def _limits(**overrides) -> InventoryLimits:
    values = {"max_sources": 10, "max_total_bytes": 1024 * 1024, "max_depth": 4}
    values.update(overrides)
    return InventoryLimits(**values)


def test_inventory_is_locale_independent_redacted_and_stable(tmp_path):
    root = tmp_path / "private-name"
    root.mkdir()
    (root / "z.txt").write_text("z", encoding="utf-8")
    (root / "a.txt").write_text("a", encoding="utf-8")
    first = seal_local_inventory([root], device_id="device-a", limits=_limits())
    second = seal_local_inventory([root], device_id="device-a", limits=_limits())
    assert first == second
    assert [item["locator_redacted"] for item in first["sources"]] == [
        "root-0000/a.txt",
        "root-0000/z.txt",
    ]
    assert all(str(tmp_path) not in item["locator_redacted"] for item in first["sources"])


def test_inventory_rejects_growth_during_hash(tmp_path):
    path = tmp_path / "growing.bin"
    path.write_bytes(b"before")
    mutated = False

    def grow(selected):
        nonlocal mutated
        if not mutated:
            with selected.open("ab") as stream:
                stream.write(b"after")
            mutated = True

    with pytest.raises(InventorySealError, match="changed"):
        seal_local_inventory(
            [path], device_id="device-a", limits=_limits(), before_final_stat=grow,
        )


def test_inventory_ignores_child_symlinks_but_rejects_symlink_roots(tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("symbolic links unavailable")
    root = tmp_path / "root"
    root.mkdir()
    target = root / "target.txt"
    target.write_text("data", encoding="utf-8")
    link = root / "link.txt"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symbolic links unavailable")
    sealed = seal_local_inventory([root], device_id="device-a", limits=_limits())
    assert [item["locator_redacted"] for item in sealed["sources"]] == [
        "root-0000/target.txt"
    ]
    with pytest.raises(InventorySealError, match="symbolic link"):
        seal_local_inventory([link], device_id="device-a", limits=_limits())


@pytest.mark.parametrize("path", ("file:///etc/passwd", "https://example.test/a", "//host/share"))
def test_inventory_rejects_uri_network_and_device_paths(path):
    with pytest.raises(InventorySealError):
        seal_local_inventory([path], device_id="device-a", limits=_limits())


def test_inventory_caps_and_depth_fail_closed(tmp_path):
    root = tmp_path / "root"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (root / "one").write_bytes(b"1")
    (nested / "two").write_bytes(b"22")
    with pytest.raises(InventorySealError, match="source count"):
        seal_local_inventory([root], device_id="device-a", limits=_limits(max_sources=1))
    with pytest.raises(InventorySealError, match="byte size"):
        seal_local_inventory([root], device_id="device-a", limits=_limits(max_total_bytes=2))
    with pytest.raises(InventorySealError, match="depth"):
        seal_local_inventory([root], device_id="device-a", limits=_limits(max_depth=0))


def test_reduction_graph_is_stable_and_bounded():
    first = build_reduction_graph(["c", "a", "b"], fan_in=2)
    second = build_reduction_graph(["b", "c", "a"], fan_in=2)
    assert first.canonical_json == second.canonical_json
    assert first.digest == second.digest
    assert first.leaves == ("a", "b", "c")
    assert first.root_key is not None

    with pytest.raises(ReductionPlanError, match="width"):
        build_reduction_graph(["a", "b", "c"], fan_in=2, max_inputs=2)
    with pytest.raises(ReductionPlanError, match="node maximum"):
        build_reduction_graph(["a", "b", "c"], fan_in=2, max_nodes=1)
