from __future__ import annotations

import os
from pathlib import Path

from manifest_inventory import (
    ContractId,
    ManifestOrigin,
    ManifestSource,
    ManifestStatus,
    inventory_manifests,
)


def _manifest(directory: Path, name: str, *, lifecycle: str = "active") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "manifest.toml"
    path.write_text(
        f'name="{name}"\nlifecycle="{lifecycle}"\n',
        encoding="utf-8",
    )
    return path


def test_inventory_classifies_every_topology_without_granting_authority(
    tmp_path: Path,
) -> None:
    core = tmp_path / "core"
    builtin = tmp_path / "builtin"
    user_skills = tmp_path / "user-skills"
    retired = tmp_path / "retired"
    imported = _manifest(user_skills / "calendar" / "read_events", "read_events")
    before = imported.read_bytes()
    _manifest(core / "find_files", "find_files")
    _manifest(builtin / "list_tasks", "list_tasks")
    _manifest(retired / "reply_messages", "reply_messages")

    inventory = inventory_manifests((
        ManifestSource(ManifestOrigin.CORE, core),
        ManifestSource(ManifestOrigin.BUILTIN, builtin),
        ManifestSource(
            ManifestOrigin.USER_SKILL, user_skills,
            min_depth=2, max_depth=2, skill_scoped=True,
        ),
        ManifestSource(
            ManifestOrigin.RETIRED, retired,
            default_status=ManifestStatus.RETIRED,
        ),
    ), skill_enabled=lambda name: name != "calendar")

    view = {(item.origin, item.name): item.status for item in inventory.manifests}
    assert view == {
        (ManifestOrigin.CORE, "find_files"): ManifestStatus.ADMITTED,
        (ManifestOrigin.BUILTIN, "list_tasks"): ManifestStatus.ADMITTED,
        (ManifestOrigin.USER_SKILL, "read_events"): ManifestStatus.DISABLED,
        (ManifestOrigin.RETIRED, "reply_messages"): ManifestStatus.RETIRED,
    }
    assert imported.read_bytes() == before


def test_inventory_reports_symlinks_aliases_collisions_and_parse_errors(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    original = _manifest(first / "a", "same_name")
    _manifest(second / "b", "same_name")
    alias_dir = first / "alias"
    alias_dir.mkdir()
    os.link(original, alias_dir / "manifest.toml")
    link_dir = first / "linked"
    link_dir.mkdir()
    (link_dir / "manifest.toml").symlink_to(original)
    broken = first / "broken"
    broken.mkdir()
    (broken / "manifest.toml").write_text("not = [valid", encoding="utf-8")

    inventory = inventory_manifests((
        ManifestSource(ManifestOrigin.CORE, first),
        ManifestSource(ManifestOrigin.BUILTIN, second),
    ))

    codes = {problem.code for problem in inventory.problems}
    assert {"alias", "symlink", "parse_error", "name_collision"} <= codes
    assert [item.name for item in inventory.manifests] == ["same_name", "same_name"]


def test_inventory_order_and_contract_storage_keys_are_portable(tmp_path: Path) -> None:
    root = tmp_path / "root"
    _manifest(root / "zeta", "zeta")
    _manifest(root / "alpha", "alpha")

    first = inventory_manifests((ManifestSource(ManifestOrigin.CORE, root),))
    second = inventory_manifests((ManifestSource(ManifestOrigin.CORE, root),))

    assert [item.name for item in first.manifests] == ["alpha", "zeta"]
    assert first == second
    for item in first.manifests:
        key = item.contract_id.storage_key
        assert len(key) == 64
        assert key.isascii() and key.isalnum()
        assert ":" not in key


def test_contract_id_rejects_noncanonical_paths() -> None:
    try:
        ContractId(ManifestOrigin.CORE, "../manifest.toml")
    except ValueError as exc:
        assert "canonical" in str(exc)
    else:  # pragma: no cover - assertion made explicit for readable failures
        raise AssertionError("noncanonical contract id accepted")
