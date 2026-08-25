from __future__ import annotations

from pathlib import Path

import config
import manifest_inventory
import sign
from contract_store import publish_signed_source
from manifest_inventory import (
    ManifestOrigin,
    ManifestSource,
    inventory_authoring_manifests,
)
from sign import sign_manifest_bytes

from test_i18n_materializer import _versioned_fixture


def test_store_corpus_identity_ignores_authoring_and_tracks_publication(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (
        paths,
        ref,
        private_key,
        trusted,
        shadow,
        initial,
        _snapshot_provider,
    ) = _versioned_fixture(tmp_path)
    state = tmp_path / "state"
    version_root = state / "contract-publications" / "v1"
    version_root.parent.mkdir(parents=True)
    shadow.rename(version_root)
    (state / "contract-publications.ACTIVE").write_bytes(b"v1\n")
    source = ManifestSource(
        ManifestOrigin.EXPLICIT,
        paths.manifest_roots[0],
        min_depth=1,
        max_depth=1,
        allowed_code_roots=paths.manifest_roots,
    )
    monkeypatch.setattr(config, "PATH_ROOT", tmp_path)
    monkeypatch.setattr(config, "PATH_USER_STATE", state)
    monkeypatch.setattr(config, "PATH_EXECUTORS", paths.manifest_roots[0])
    monkeypatch.setattr(config, "PATH_RUNTIME", tmp_path / "runtime")
    monkeypatch.setattr(config, "PATH_DOCS", paths.docs)
    monkeypatch.setattr(
        manifest_inventory,
        "default_manifest_sources",
        lambda: (source,),
    )
    monkeypatch.setattr(sign, "list_trusted_publics", lambda: list(trusted))

    before = config.localization_corpus_version()
    manifest_path = ref.manifest_path
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8") + "\n# unpublished draft\n",
        encoding="utf-8",
    )
    state_path = ref.manifest_dir / "manifest.lang_state.json"
    canonical_state = state_path.read_bytes()
    state_path.write_bytes(canonical_state + b"\n")

    assert config.localization_corpus_version() == before

    state_path.write_bytes(canonical_state)
    (ref.manifest_dir / "manifest.toml.sig").write_bytes(
        sign_manifest_bytes(manifest_path.read_bytes(), private_key=private_key),
    )
    refreshed = inventory_authoring_manifests((source,)).admitted()[0]
    publication = publish_signed_source(
        refreshed,
        expected_generation_id=initial.current_generation_id,
        trusted_publics=trusted,
        registry_reconciler=lambda _snapshot: None,
    )

    assert publication.current_generation_id != initial.current_generation_id
    assert config.localization_corpus_version() != before
