from __future__ import annotations

import hashlib
import os
import shutil
import tomllib
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import loader
import contract_store as contract_store_module
from contract_store import (
    ContractStoreError,
    encode_binding,
    prepare_technical_draft,
    publish_signed_source,
    reactivate_technical_update,
    retire,
)
from i18n_materializer import encode_language_state, manifest_language_selectors
from manifest_inventory import (
    ContractId,
    ManifestBootstrapError,
    ManifestLayout,
    ManifestOrigin,
    ManifestSource,
    inventory_authoring_manifests,
)
from sign import sign_manifest_bytes


def _hash_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _language_state(manifest: Mapping[str, Any]) -> bytes:
    selectors = manifest_language_selectors(manifest)
    state = {
        "schema_version": 1,
        "selectors": {
            selector: {
                language: {
                    "version_hash": _hash_text(text),
                    "source_lang": None,
                    "source_hash": None,
                }
                for language, text in table.items()
            }
            for selector, table in selectors.items()
        },
    }
    return encode_language_state(state, manifest=manifest)


def _source(
    tmp_path: Path,
    *,
    name: str = "read_files",
) -> tuple[Path, object, tuple[tuple[str, object], ...]]:
    root = tmp_path / "authoring"
    directory = root / name
    directory.mkdir(parents=True)
    code = directory / f"{name}.py"
    code.write_text(
        "def invoke(args):\n"
        "    return {'ok': True, 'results': []}\n"
        "if __name__ == \"__main__\":\n"
        "    pass\n",
        encoding="utf-8",
    )
    digest = "sha256:" + hashlib.sha256(code.read_bytes()).hexdigest()
    manifest = directory / "manifest.toml"
    manifest.write_text(
        f'''manifest_format = "1.0"
executor_standard = "metnos.executor/1.0"
name = "{name}"
version = "1.0.0"
lifecycle = "active"
affinity = []

[description]
it = "SCOPO: Legge una prova. PATTERN: read_files(). NON: modificare dati. OUT: results=[]."
en = "SCOPO: Reads a probe. PATTERN: read_files(). NON: modify data. OUT: results=[]."

[code]
files = ["{code.name}"]
digest = "{digest}"

[output]
schema_inline = "{{ ok: bool, results: list }}"

[[capabilities]]
name = "compute:pure"
hint = []

[[tests]]
name = "sample"
input = {{}}
expect = {{ ok = true }}

[args]
type = "object"
required = []
''',
        encoding="utf-8",
    )
    parsed = tomllib.loads(manifest.read_text(encoding="utf-8"))
    (directory / "manifest.lang_state.json").write_bytes(
        _language_state(parsed),
    )
    private = Ed25519PrivateKey.generate()
    (directory / "manifest.toml.sig").write_bytes(sign_manifest_bytes(
        manifest.read_bytes(), private_key=private,
    ))
    inventory = inventory_authoring_manifests((ManifestSource(
        ManifestOrigin.CORE,
        root,
        allowed_code_roots=(root,),
    ),))
    assert not inventory.problems
    ref = inventory.manifests[0]
    return root, ref, (("test-author", private.public_key()),)


def _activate_published_store(
    tmp_path: Path,
    ref,
    trusted,
) -> tuple[Path, str]:
    shadow = tmp_path / "shadow-v1"
    result = publish_signed_source(
        ref,
        expected_generation_id=None,
        trusted_publics=trusted,
        store_root=shadow,
    )
    state = tmp_path / "state"
    production = state / "contract-publications"
    production.mkdir(parents=True)
    shadow.rename(production / "v1")
    relative = Path(ref.contract_id.relative_manifest).parent
    external = (
        state / "contract-authoring" / "v1"
        / ref.contract_id.origin.value / relative
    )
    external.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ref.manifest_dir, external)
    return state, result.current_generation_id


def _builtin_source(
    tmp_path: Path,
) -> tuple[Path, Path, object, tuple[tuple[str, object], ...]]:
    runtime_root = tmp_path / "runtime-root"
    contracts = runtime_root / "builtin_executor_contracts"
    directory = contracts / "list_tasks"
    directory.mkdir(parents=True)
    module_path = runtime_root / "recurring_tasks.py"
    module_path.write_text("def invoke(args):\n    return {'ok': True}\n")
    implementation_path = directory / "implementation.py.src"
    implementation_path.write_bytes(module_path.read_bytes())
    digest = "sha256:" + hashlib.sha256(
        implementation_path.read_bytes(),
    ).hexdigest()
    manifest = directory / "manifest.toml"
    manifest.write_text(
        f'''manifest_format = "1.0"
executor_standard = "metnos.executor/1.0"
name = "list_tasks"
version = "1.0.0"
lifecycle = "active"
affinity = []
platforms = ["linux"]

[description]
it = "SCOPO: Elenca task. PATTERN: list_tasks(). NON: modificare task. OUT: entries=[]."
en = "SCOPO: Lists tasks. PATTERN: list_tasks(). NON: modify tasks. OUT: entries=[]."

[code]
files = ["implementation.py.src"]
digest = "{digest}"

[placement]
scope = "server"

[output]
schema_inline = "{{ ok: bool, entries: list }}"

[[capabilities]]
name = "metnos:read"
hint = []

[[tests]]
name = "sample"
input = {{}}
expect = {{ ok = true }}

[args]
type = "object"
required = []
''',
        encoding="utf-8",
    )
    parsed = tomllib.loads(manifest.read_text(encoding="utf-8"))
    (directory / "manifest.lang_state.json").write_bytes(
        _language_state(parsed),
    )
    private = Ed25519PrivateKey.generate()
    (directory / "manifest.toml.sig").write_bytes(sign_manifest_bytes(
        manifest.read_bytes(), private_key=private,
    ))
    inventory = inventory_authoring_manifests((ManifestSource(
        ManifestOrigin.BUILTIN,
        contracts,
        allowed_code_roots=(runtime_root,),
    ),))
    assert not inventory.problems
    return runtime_root, module_path, inventory.manifests[0], (
        ("test-author", private.public_key()),
    )


def _point_loader_at(
    monkeypatch,
    *,
    state: Path,
    source_root: Path,
    trusted=(),
) -> None:
    monkeypatch.setattr(loader._C, "PATH_USER_STATE", state)
    # A fixture that exercises the PRODUCTIVE wrapper must declare the
    # installation it runs from: the barrier cannot tell a redirected fixture
    # from a different checkout writing into an installed instance.
    monkeypatch.setenv("METNOS_INSTALL_ROOT", str(loader._C.PATH_ROOT))
    monkeypatch.setattr(loader._C, "PATH_EXECUTORS", source_root)
    monkeypatch.setattr(loader, "list_trusted_publics", lambda: list(trusted))
    loader.invalidate_catalog_cache()


def test_store_loader_consumes_verified_parsed_with_unreadable_authoring_sentinel(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_root, ref, trusted = _source(tmp_path)
    state, generation = _activate_published_store(tmp_path, ref, trusted)
    ref.manifest_path.unlink()
    ref.manifest_path.mkdir()  # any authoring reopen now raises IsADirectoryError
    _point_loader_at(
        monkeypatch, state=state, source_root=source_root, trusted=trusted,
    )

    catalog = loader.load_catalog(
        executors_dir=source_root,
        verify=True,
        include_synth=False,
        include_verb_unique=False,
        lang="en",
    )

    executor = catalog.get("read_files")
    assert executor is not None
    assert executor.description.startswith("SCOPO: Reads a probe.")
    assert executor.manifest_path.is_file()
    assert "generations" in executor.manifest_path.parts
    assert executor.authoring_manifest_path == (
        state / "contract-authoring" / "v1" / "core"
        / "read_files" / "manifest.toml"
    )
    assert executor.generation_id == generation


def test_builtin_contract_helper_returns_generation_path_without_authoring_reopen(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_root, module_path, ref, trusted = _builtin_source(tmp_path)
    state, generation = _activate_published_store(tmp_path, ref, trusted)
    ref.manifest_path.unlink()
    ref.manifest_path.mkdir()
    monkeypatch.setattr(loader._C, "PATH_USER_STATE", state)
    # A fixture that exercises the PRODUCTIVE wrapper must declare the
    # installation it runs from: the barrier cannot tell a redirected fixture
    # from a different checkout writing into an installed instance.
    monkeypatch.setenv("METNOS_INSTALL_ROOT", str(loader._C.PATH_ROOT))
    monkeypatch.setattr(loader._C, "PATH_RUNTIME", runtime_root)
    monkeypatch.setattr(
        loader,
        "BUILTIN_EXECUTOR_CONTRACTS_DIR",
        runtime_root / "builtin_executor_contracts",
    )
    monkeypatch.setattr(loader, "list_trusted_publics", lambda: list(trusted))

    manifest, live_path, signed_by, loaded_generation = (
        loader._load_builtin_contract("list_tasks", module_path)
    )

    assert manifest["name"] == "list_tasks"
    assert live_path.is_file()
    assert live_path != ref.manifest_path
    assert "generations" in live_path.parts
    assert signed_by == "test-author"
    assert loaded_generation == generation


def test_marker_without_production_root_blocks_entire_loader_bootstrap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_root, _ref, trusted = _source(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    (state / "contract-publications.ACTIVE").write_bytes(b"v1\n")
    _point_loader_at(
        monkeypatch, state=state, source_root=source_root, trusted=trusted,
    )

    with pytest.raises(ManifestBootstrapError, match="store_root_missing"):
        loader.load_catalog(
            executors_dir=source_root,
            verify=False,
            include_synth=False,
            include_verb_unique=False,
        )


def test_missing_current_is_rejected_without_per_contract_legacy_fallback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_root, ref, trusted = _source(tmp_path)
    state = tmp_path / "state"
    store_root = state / "contract-publications" / "v1"
    contract_dir = store_root / ref.contract_id.storage_key
    contract_dir.mkdir(parents=True)
    (contract_dir / "binding.json").write_bytes(encode_binding(ref.contract_id))
    (state / "contract-publications.ACTIVE").write_bytes(b"v1\n")
    _point_loader_at(
        monkeypatch, state=state, source_root=source_root, trusted=trusted,
    )

    catalog = loader.load_catalog(
        executors_dir=source_root,
        verify=False,
        include_synth=False,
        include_verb_unique=False,
    )

    assert catalog.get("read_files") is None
    assert any("current_missing" in reason for _path, reason in catalog.rejected)


def test_shadow_alone_keeps_legacy_loader_mode(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_root, _ref, trusted = _source(tmp_path)
    state = tmp_path / "state"
    (state / "contract-publications-shadow" / "nonce" / "v1").mkdir(
        parents=True,
    )
    _point_loader_at(
        monkeypatch, state=state, source_root=source_root, trusted=trusted,
    )

    catalog = loader.load_catalog(
        executors_dir=source_root,
        verify=False,
        include_synth=False,
        include_verb_unique=False,
    )

    assert catalog.get("read_files") is not None


def test_store_catalog_cache_hits_on_unchanged_revision_inventory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_root, ref, trusted = _source(tmp_path)
    state, _generation = _activate_published_store(tmp_path, ref, trusted)
    _point_loader_at(
        monkeypatch, state=state, source_root=source_root, trusted=trusted,
    )
    original = loader._load_store_into_catalog
    cold_loads = []

    def counted(*args, **kwargs):
        cold_loads.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(loader, "_load_store_into_catalog", counted)
    first = loader.load_catalog(
        executors_dir=source_root,
        include_synth=False,
        include_verb_unique=False,
        lang="en",
    )
    second = loader.load_catalog(
        executors_dir=source_root,
        include_synth=False,
        include_verb_unique=False,
        lang="en",
    )

    assert first is second
    assert cold_loads == [True]


def test_store_catalog_audit_has_no_cache_registration_log_or_ddl_effects(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import executor_aging
    import skill_credentials
    import skill_registry

    source_root, ref, trusted = _source(tmp_path)
    state, _generation = _activate_published_store(tmp_path, ref, trusted)
    _point_loader_at(
        monkeypatch, state=state, source_root=source_root, trusted=trusted,
    )
    owner = (os.getuid(), os.getgid())
    snapshots = []
    original_affinity = loader._check_affinity_overlap

    class ForbiddenCache(dict):
        def get(self, *_args, **_kwargs):
            pytest.fail("audit consulted the process catalog cache")

        def __setitem__(self, *_args, **_kwargs):
            pytest.fail("audit populated the process catalog cache")

    def fail(*_args, **_kwargs):
        pytest.fail("audit invoked a mutating or diagnostic hook")

    def skill_snapshot(trusted_owner):
        snapshots.append(trusted_owner)
        return (
            lambda _name: True,
            ("skill_state", "present", 2, "snapshot"),
        )

    def affinity(catalog, *, write_audit=True):
        assert write_audit is False
        return original_affinity(catalog, write_audit=write_audit)

    def lifecycle(*, read_only=False):
        assert read_only is True
        return {}

    monkeypatch.setattr(loader, "_CATALOG_CACHE", ForbiddenCache())
    monkeypatch.setattr(loader, "boot_register_verb_unique_builtins", fail)
    monkeypatch.setattr(loader, "_check_affinity_overlap", affinity)
    monkeypatch.setattr(executor_aging, "register", fail)
    monkeypatch.setattr(executor_aging, "lifecycle_override_map", lifecycle)
    monkeypatch.setattr(skill_credentials, "compute_dormancy", fail)
    monkeypatch.setattr(skill_credentials, "resolve_github_token", fail)
    monkeypatch.setattr(
        skill_registry, "_skill_policy_snapshot_for_owner_v1", skill_snapshot,
    )
    monkeypatch.setattr(skill_registry, "skill_state_cache_signature", fail)
    for name in ("debug", "info", "warning", "error"):
        monkeypatch.setattr(loader.log, name, fail)

    catalog = loader._load_catalog_for_cutover_audit_v1(
        catalog_trusted_owner=owner,
        _executors_dir=source_root,
        _include_synth=False,
        _lang="en",
    )

    assert catalog.get("read_files") is not None
    assert snapshots == [owner, owner]

    monkeypatch.setattr(
        executor_aging, "lifecycle_override_map",
        lambda *, read_only=False: (_ for _ in ()).throw(
            RuntimeError("invalid aging database")
        ),
    )
    with pytest.raises(RuntimeError, match="invalid aging database"):
        loader._load_catalog_for_cutover_audit_v1(
            catalog_trusted_owner=owner,
            _executors_dir=source_root,
            _include_synth=False,
            _lang="en",
        )


@pytest.mark.parametrize("owner", [
    [1, 2], (True, 2), (1,), (1, -1), (0, 2),
])
def test_catalog_read_options_fail_before_store_lock(
    monkeypatch, owner,
) -> None:
    monkeypatch.setattr(
        loader, "resolve_manifest_layout", lambda: ManifestLayout.STORE_ONLY,
    )
    monkeypatch.setattr(
        contract_store_module,
        "catalog_admission_lock",
        lambda **_kwargs: pytest.fail("invalid options reached the store lock"),
    )

    with pytest.raises(ValueError):
        loader.load_catalog(catalog_trusted_owner=owner)


def test_catalog_owner_and_cutover_audit_are_store_only(monkeypatch) -> None:
    monkeypatch.setattr(
        loader, "resolve_manifest_layout", lambda: ManifestLayout.AUTHORING,
    )
    monkeypatch.setattr(
        loader,
        "_load_catalog_under_catalog_lock",
        lambda *_args, **_kwargs: pytest.fail("invalid options reached loader"),
    )

    with pytest.raises(ValueError, match="catalog_trusted_owner"):
        loader.load_catalog(catalog_trusted_owner=(1, 2))
    with pytest.raises(ValueError, match="cutover audit"):
        loader._load_catalog_for_cutover_audit_v1(
            catalog_trusted_owner=None,
        )


def test_store_cache_uses_generation_id_when_pointer_mtime_is_unchanged(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Cache invalidation is a lower-level store property.  Productive legacy
    # entry-point denial is covered independently by the closed-build guards.
    monkeypatch.setattr(
        contract_store_module, "_deny_closed_legacy_api",
        lambda _operation, _store_root: None,
    )
    source_root, ref, original_trusted = _source(tmp_path)
    state, initial_generation = _activate_published_store(
        tmp_path, ref, original_trusted,
    )
    update_key = Ed25519PrivateKey.generate()
    trusted = (*original_trusted, ("update-author", update_key.public_key()))
    _point_loader_at(
        monkeypatch, state=state, source_root=source_root, trusted=trusted,
    )
    (state / "contract-publications.ACTIVE").write_bytes(b"v1\n")
    first = loader.load_catalog(
        executors_dir=source_root,
        include_synth=False,
        include_verb_unique=False,
        lang="en",
    )
    assert first.get("read_files").version == "1.0.0"

    manifest_path = ref.manifest_path
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8").replace(
            'version = "1.0.0"', 'version = "2.0.0"',
        ),
        encoding="utf-8",
    )
    (ref.manifest_dir / "manifest.toml.sig").write_bytes(
        sign_manifest_bytes(manifest_path.read_bytes(), private_key=update_key),
    )
    updated_inventory = inventory_authoring_manifests((ManifestSource(
        ManifestOrigin.CORE,
        source_root,
        allowed_code_roots=(source_root,),
    ),))
    assert not updated_inventory.problems
    updated_ref = updated_inventory.manifests[0]
    store_root = state / "contract-publications" / "v1"
    pointer = store_root / ref.contract_id.storage_key / "current"
    pointer_times = pointer.stat()
    published = publish_signed_source(
        updated_ref,
        expected_generation_id=initial_generation,
        trusted_publics=trusted,
        registry_reconciler=lambda _revision: None,
    )
    os.utime(
        pointer,
        ns=(pointer_times.st_atime_ns, pointer_times.st_mtime_ns),
    )

    second = loader.load_catalog(
        executors_dir=source_root,
        include_synth=False,
        include_verb_unique=False,
        lang="en",
    )

    assert published.current_generation_id != initial_generation
    assert pointer.stat().st_mtime_ns == pointer_times.st_mtime_ns
    assert second is not first
    assert second.get("read_files").version == "2.0.0"
    assert second.get("read_files").generation_id == published.current_generation_id


def test_store_cache_tracks_retirement_and_explicit_reactivation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        contract_store_module, "_deny_closed_legacy_api",
        lambda _operation, _store_root: None,
    )
    source_root, ref, original_trusted = _source(tmp_path)
    state, generation = _activate_published_store(tmp_path, ref, original_trusted)
    authority = Ed25519PrivateKey.generate()
    trusted = (*original_trusted, ("lifecycle-authority", authority.public_key()))
    _point_loader_at(
        monkeypatch, state=state, source_root=source_root, trusted=trusted,
    )
    (state / "contract-publications.ACTIVE").write_bytes(b"v1\n")
    active = loader.load_catalog(
        executors_dir=source_root,
        include_synth=False,
        include_verb_unique=False,
    )
    assert active.get("read_files") is not None

    retired = retire(
        ref,
        expected_generation_id=generation,
        actor="loader-cache-test",
        reason="verify retirement invalidation",
        private_key=authority,
        trusted_publics=trusted,
        audit_sink=lambda _event: None,
        registry_reconciler=lambda _revision: None,
    )
    absent = loader.load_catalog(
        executors_dir=source_root,
        include_synth=False,
        include_verb_unique=False,
    )
    assert absent is not active
    assert absent.get("read_files") is None
    assert any("contract_retired" in reason for _path, reason in absent.rejected)

    reactivated = reactivate_technical_update(
        ref,
        expected_retirement_id=retired.current_generation_id,
        draft=prepare_technical_draft(ref),
        actor="loader-cache-test",
        reason="verify reactivation invalidation",
        private_key=authority,
        trusted_publics=trusted,
        audit_sink=lambda _event: None,
        registry_reconciler=lambda _revision: None,
    )
    restored = loader.load_catalog(
        executors_dir=source_root,
        include_synth=False,
        include_verb_unique=False,
    )

    assert restored is not absent
    assert restored.get("read_files") is not None
    assert restored.get("read_files").generation_id == reactivated.current_generation_id


def test_store_cache_rejects_invalid_pointer_even_when_a_cache_exists(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_root, ref, trusted = _source(tmp_path)
    state, _generation = _activate_published_store(tmp_path, ref, trusted)
    _point_loader_at(
        monkeypatch, state=state, source_root=source_root, trusted=trusted,
    )
    loader.load_catalog(
        executors_dir=source_root,
        include_synth=False,
        include_verb_unique=False,
    )
    current = (
        state / "contract-publications" / "v1"
        / ref.contract_id.storage_key / "current"
    )
    previous = current.stat()
    current.write_bytes(b"not-a-canonical-revision\n")
    os.utime(current, ns=(previous.st_atime_ns, previous.st_mtime_ns))

    with pytest.raises(
        ContractStoreError,
        match="current_invalid|generation_id_invalid",
    ):
        loader.load_catalog(
            executors_dir=source_root,
            include_synth=False,
            include_verb_unique=False,
        )


def test_store_cache_fails_after_bounded_unstable_snapshot_retries(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_root, ref, trusted = _source(tmp_path)
    state, _generation = _activate_published_store(tmp_path, ref, trusted)
    _point_loader_at(
        monkeypatch, state=state, source_root=source_root, trusted=trusted,
    )
    signatures = iter(("before-1", "after-1", "before-2", "after-2"))
    cold_loads = []
    monkeypatch.setattr(
        loader,
        "_store_catalog_signature",
        lambda *_args, **_kwargs: next(signatures),
    )
    monkeypatch.setattr(
        loader,
        "_load_store_into_catalog",
        lambda *_args, **_kwargs: cold_loads.append(True),
    )

    with pytest.raises(ManifestBootstrapError, match="store_snapshot_unstable"):
        loader.load_catalog(
            executors_dir=source_root,
            include_synth=False,
            include_verb_unique=False,
        )

    assert cold_loads == [True, True]


def test_store_cache_never_caches_snapshot_loaded_under_another_pointer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import contract_store

    source_root, ref, trusted = _source(tmp_path)
    state, generation = _activate_published_store(tmp_path, ref, trusted)
    _point_loader_at(
        monkeypatch, state=state, source_root=source_root, trusted=trusted,
    )
    authentic = contract_store.current_manifest(
        ref,
        trusted_publics=trusted,
    )
    other_generation = "sha256:" + "a" * 64
    calls = 0
    original = contract_store.current_manifest

    def raced(current_ref, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return replace(authentic, generation_id=other_generation)
        return original(current_ref, **kwargs)

    monkeypatch.setattr(contract_store, "current_manifest", raced)

    catalog = loader.load_catalog(
        executors_dir=source_root,
        include_synth=False,
        include_verb_unique=False,
    )

    assert calls == 2
    assert catalog.get("read_files").generation_id == generation
    cached = loader.load_catalog(
        executors_dir=source_root,
        include_synth=False,
        include_verb_unique=False,
    )
    assert cached is catalog
    assert calls == 2
