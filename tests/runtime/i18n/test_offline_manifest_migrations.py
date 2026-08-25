from __future__ import annotations

from pathlib import Path

import pytest

from manifest_inventory import ManifestLayout
from migrate_manifest_descriptions import migrate_one


def test_description_migrator_rejects_store_only_mutation_before_source_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = tmp_path / "authoring" / "manifest.toml"
    monkeypatch.setattr(
        "manifest_inventory.resolve_manifest_layout",
        lambda: ManifestLayout.STORE_ONLY,
    )

    with pytest.raises(RuntimeError, match="versioned contract candidate"):
        migrate_one(missing, dry_run=False)


def test_description_migrator_keeps_store_only_dry_run_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = tmp_path / "manifest.toml"
    manifest.write_text(
        'name = "sample"\ndescription = "Example"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "manifest_inventory.resolve_manifest_layout",
        lambda: ManifestLayout.STORE_ONLY,
    )

    result = migrate_one(manifest, lang="en", dry_run=True, sign=False)

    assert result["status"] == "dry_run"
    assert manifest.read_text(encoding="utf-8") == (
        'name = "sample"\ndescription = "Example"\n'
    )
