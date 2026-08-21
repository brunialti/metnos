from __future__ import annotations

import os

import pytest

from durable_workloads.inventory import (
    InventoryLimits,
    InventorySealError,
    SealedInventory,
    seal_local_inventory,
)
from durable_workloads.reduction import (
    MAX_REDUCTION_INPUTS,
    ReductionPlanError,
    hierarchical_node_bound,
)
from durable_workloads.schema import (
    SchemaValidationError,
    canonical_json,
    inventory_digest,
    validate_inventory,
)
from helpers import inventory, source


def _limits(**overrides) -> InventoryLimits:
    values = {"max_sources": 10, "max_total_bytes": 1024 * 1024, "max_depth": 4}
    values.update(overrides)
    return InventoryLimits(**values)


def test_large_inventory_keeps_a_digest_without_one_giant_json_copy(monkeypatch):
    import durable_workloads.schema as schema

    payload = inventory([source(index) for index in range(20)])
    expected = payload["digest"]
    monkeypatch.setattr(schema, "MAX_INVENTORY_JSON_BYTES", 2_048)

    inline, validated_sources = validate_inventory(payload)

    assert inline is None
    assert validated_sources is payload["sources"]
    assert schema.inventory_digest(validated_sources) == expected

    monkeypatch.setattr(schema, "MAX_INVENTORY_JSON_BYTES", 1_000_000)
    bounded_inline, _ = validate_inventory(payload)
    assert bounded_inline == canonical_json(payload, max_bytes=1_000_000)


def test_large_local_inventory_uses_a_repeatable_disposable_spool(
    tmp_path,
    monkeypatch,
):
    import durable_workloads.inventory as inventory_module

    root = tmp_path / "spooled"
    root.mkdir()
    for name in ("c.txt", "a.txt", "b.txt"):
        (root / name).write_text(name, encoding="utf-8")
    monkeypatch.setattr(inventory_module, "_IN_MEMORY_SOURCE_LIMIT", 2)
    monkeypatch.setattr(inventory_module, "_SPOOL_BATCH_SIZE", 2)

    sealed = seal_local_inventory(
        [root], device_id="device-a", limits=_limits(),
    )
    assert isinstance(sealed, SealedInventory)
    try:
        sources = sealed["sources"]
        assert [item["locator_redacted"] for item in sources] == [
            "root-0000/a.txt",
            "root-0000/b.txt",
            "root-0000/c.txt",
        ]
        assert sources[1]["ordinal"] == 1
        assert [item["ordinal"] for item in sources[::-1]] == [2, 1, 0]
        first_inline, first_sources = validate_inventory(sealed)
        second_inline, second_sources = validate_inventory(sealed)
        assert first_inline == second_inline
        assert first_sources is second_sources is sources
        assert inventory_digest(sources) == sealed["digest"]
    finally:
        sealed.close()
    with pytest.raises(InventorySealError, match="closed"):
        _ = sealed["sources"]


def test_disk_backed_inventory_validation_never_builds_an_inline_copy(
    tmp_path,
    monkeypatch,
):
    import durable_workloads.inventory as inventory_module

    root = tmp_path / "streamed"
    root.mkdir()
    for index in range(3):
        (root / f"source-{index}.txt").write_text(str(index), encoding="utf-8")
    monkeypatch.setattr(inventory_module, "_IN_MEMORY_SOURCE_LIMIT", 2)

    sealed = seal_local_inventory(
        [root], device_id="device-a", limits=_limits(),
    )
    assert isinstance(sealed, SealedInventory)
    try:
        inline, sources = validate_inventory(sealed)
        assert inline is None
        assert sources is sealed["sources"]
        assert len(sources) == 3
    finally:
        sealed.close()


def test_large_inventory_duplicate_check_spills_without_losing_exactness(
    monkeypatch,
):
    import durable_workloads.schema as schema

    monkeypatch.setattr(schema._BoundedUniqueValues, "_MEMORY_LIMIT", 2)
    sources = [source(index) for index in range(4)]
    sources[3]["source_id"] = sources[0]["source_id"]
    payload = {
        "schema_version": "metnos.durable-inventory/1",
        "sealed": True,
        "digest": inventory_digest(sources),
        "sources": sources,
    }

    with pytest.raises(SchemaValidationError, match="must be unique"):
        validate_inventory(payload)


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


def test_inventory_rejects_directory_growth_while_files_are_hashed(tmp_path):
    root = tmp_path / "changing-directory"
    root.mkdir()
    (root / "before.txt").write_text("before", encoding="utf-8")
    mutated = False

    def add_source(selected):
        nonlocal mutated
        if not mutated:
            (selected.parent / "after.txt").write_text("after", encoding="utf-8")
            mutated = True

    with pytest.raises(InventorySealError, match="directory changed"):
        seal_local_inventory(
            [root],
            device_id="device-a",
            limits=_limits(),
            before_final_stat=add_source,
        )


def test_inventory_rejects_an_oversized_file_before_hashing_it(tmp_path):
    path = tmp_path / "oversized.bin"
    path.write_bytes(b"x" * 4_097)
    final_stat_reached = False

    def mark_final_stat(_selected):
        nonlocal final_stat_reached
        final_stat_reached = True

    with pytest.raises(InventorySealError, match="byte size"):
        seal_local_inventory(
            [path],
            device_id="device-a",
            limits=_limits(max_total_bytes=4_096),
            chunk_bytes=4_096,
            before_final_stat=mark_final_stat,
        )
    assert final_stat_reached is False


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


def test_reduction_capacity_bound_is_constant_space_and_closed():
    assert hierarchical_node_bound(0) == 1
    assert hierarchical_node_bound(1) == 1
    assert hierarchical_node_bound(2) == 1
    assert hierarchical_node_bound(3) == 3
    assert hierarchical_node_bound(7) == 7
    assert hierarchical_node_bound(MAX_REDUCTION_INPUTS) == 1_000_007

    with pytest.raises(ReductionPlanError, match="supported bounds"):
        hierarchical_node_bound(MAX_REDUCTION_INPUTS + 1)
