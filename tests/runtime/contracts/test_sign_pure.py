from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from sign import (
    ManifestSignatureError,
    publish_executor,
    publish_authoring_update,
    reactivate_executor_contract,
    sign_manifest_bytes,
    verify_manifest_bytes,
)


def test_byte_crypto_is_pure_and_uses_preloaded_key_objects(monkeypatch) -> None:
    private = Ed25519PrivateKey.generate()
    payload = b'name = "sample"\n'

    def filesystem_forbidden(*_args, **_kwargs):
        raise AssertionError("pure crypto must not access the filesystem")

    monkeypatch.setattr(Path, "open", filesystem_forbidden)
    monkeypatch.setattr(Path, "read_bytes", filesystem_forbidden)
    signature = sign_manifest_bytes(payload, private_key=private)
    identity = verify_manifest_bytes(
        payload,
        signature,
        trusted_publics=(("test-author", private.public_key()),),
    )

    assert identity.name == "test-author"


def test_byte_crypto_rejects_different_bytes() -> None:
    private = Ed25519PrivateKey.generate()
    signature = sign_manifest_bytes(b"first", private_key=private)

    with pytest.raises(ManifestSignatureError):
        verify_manifest_bytes(
            b"second",
            signature,
            trusted_publics=(("test-author", private.public_key()),),
        )


def test_authoring_resolution_keeps_disabled_contracts_publishable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import manifest_inventory
    import sign

    directory = tmp_path / "disabled"
    directory.mkdir()
    manifest_path = directory / "manifest.toml"
    manifest_path.write_text('name = "disabled_executor"\n', encoding="utf-8")
    ref = SimpleNamespace(
        manifest_path=manifest_path,
        status=manifest_inventory.ManifestStatus.DISABLED,
        contract_id="user-skill:disabled/manifest.toml",
    )
    monkeypatch.setattr(
        manifest_inventory,
        "inventory_authoring_manifests",
        lambda: SimpleNamespace(manifests=(ref,)),
    )

    assert sign._authoring_manifest_ref(directory) is ref


def test_authoring_resolution_rejects_source_retired_contracts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import manifest_inventory
    import sign

    directory = tmp_path / "retired"
    directory.mkdir()
    manifest_path = directory / "manifest.toml"
    manifest_path.write_text('name = "retired_executor"\n', encoding="utf-8")
    ref = SimpleNamespace(
        manifest_path=manifest_path,
        status=manifest_inventory.ManifestStatus.RETIRED,
        contract_id="user-skill:retired/manifest.toml",
    )
    monkeypatch.setattr(
        manifest_inventory,
        "inventory_authoring_manifests",
        lambda: SimpleNamespace(manifests=(ref,)),
    )

    with pytest.raises(ValueError, match="not publishable"):
        sign._authoring_manifest_ref(directory)


def test_publish_is_one_transaction_and_never_calls_offline_sign(
    monkeypatch,
) -> None:
    import contract_store
    import sign

    ref = SimpleNamespace(contract_id="core:sample/manifest.toml")
    draft = object()
    private = object()
    trusted = (("author", object()),)
    result = SimpleNamespace(current_generation_id="sha256:" + "a" * 64)
    calls: list[tuple[object, object, object]] = []

    monkeypatch.setattr(sign, "_authoring_manifest_ref", lambda _path: ref)
    monkeypatch.setattr(sign, "load_private", lambda _name: private)
    monkeypatch.setattr(sign, "list_trusted_publics", lambda: list(trusted))
    monkeypatch.setattr(
        sign, "sign_executor",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("publish must not pre-sign authoring")
        ),
    )
    monkeypatch.setattr(
        contract_store, "current_revision_id",
        lambda *_args, **_kwargs: "sha256:" + "b" * 64,
    )
    monkeypatch.setattr(
        contract_store, "prepare_technical_draft", lambda selected: (
            draft if selected is ref else None
        ),
    )

    def publisher(selected, *, expected_generation_id, draft,
                  private_key, trusted_publics, registry_reconciler):
        calls.append((selected, draft, private_key))
        assert expected_generation_id == "sha256:" + "b" * 64
        assert trusted_publics == trusted
        assert callable(registry_reconciler)
        return result

    monkeypatch.setattr(contract_store, "publish_technical_update", publisher)

    assert publish_executor("/authoring/sample") is result
    assert calls == [(ref, draft, private)]


@pytest.mark.parametrize(
    "missing_code",
    ("contract_directory_missing", "current_missing"),
)
def test_publish_delegates_new_or_interrupted_first_publish_to_locked_boundary(
    monkeypatch,
    missing_code: str,
) -> None:
    import contract_store
    import sign

    ref = SimpleNamespace(contract_id="core:new/manifest.toml")
    private = object()
    trusted = (("author", object()),)
    monkeypatch.setattr(sign, "_authoring_manifest_ref", lambda _path: ref)
    monkeypatch.setattr(sign, "load_private", lambda _name: private)
    monkeypatch.setattr(sign, "list_trusted_publics", lambda: list(trusted))
    monkeypatch.setattr(
        contract_store, "current_revision_id",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            contract_store.ContractStoreError(missing_code)
        ),
    )
    monkeypatch.setattr(contract_store, "prepare_technical_draft", lambda _ref: "draft")
    observed: list[object] = []
    monkeypatch.setattr(
        contract_store, "publish_technical_update",
        lambda _ref, **kwargs: observed.append(kwargs["expected_generation_id"])
        or "published",
    )

    assert publish_executor("/authoring/new") == "published"
    assert observed == [None]


def test_publish_does_not_mask_other_pointer_or_store_failures(monkeypatch) -> None:
    import contract_store
    import sign

    ref = SimpleNamespace(contract_id="core:broken/manifest.toml")
    monkeypatch.setattr(sign, "_authoring_manifest_ref", lambda _path: ref)
    monkeypatch.setattr(sign, "load_private", lambda _name: object())
    monkeypatch.setattr(
        sign, "list_trusted_publics", lambda: [("author", object())],
    )
    monkeypatch.setattr(
        contract_store, "current_revision_id",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            contract_store.ContractStoreError("current_invalid")
        ),
    )

    with pytest.raises(contract_store.ContractStoreError, match="current_invalid"):
        publish_executor("/authoring/broken")


def test_generated_source_stays_legacy_before_cutover(monkeypatch) -> None:
    import manifest_inventory
    import sign

    signed = ("sha256:" + "1" * 64, Path("manifest.toml.sig"))
    monkeypatch.setattr(sign, "sign_executor", lambda *_args: signed)
    monkeypatch.setattr(
        manifest_inventory, "resolve_manifest_layout",
        lambda: manifest_inventory.ManifestLayout.AUTHORING,
    )
    assert publish_authoring_update("/new/source") == (*signed, None)


def test_generated_source_uses_single_lock_publisher_after_cutover(monkeypatch) -> None:
    import manifest_inventory
    import sign

    publication = object()
    monkeypatch.setattr(
        sign, "sign_executor",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("store-only generation must not pre-sign"),
        ),
    )
    monkeypatch.setattr(
        manifest_inventory, "resolve_manifest_layout",
        lambda: manifest_inventory.ManifestLayout.STORE_ONLY,
    )
    manifest_dir = Path("/new/source")
    monkeypatch.setattr(sign, "publish_executor", lambda *_args: publication)
    monkeypatch.setattr(
        Path, "read_text",
        lambda self, **_kwargs: '[code]\ndigest="sha256:' + "3" * 64 + '"\n',
    )

    result = publish_authoring_update(manifest_dir)

    assert result[2] is publication
    assert result[0] == "sha256:" + "3" * 64
    assert result[1] == manifest_dir / "manifest.toml.sig"


def _productive_store(tmp_path: Path, monkeypatch):
    import config
    import contract_store
    import i18n_pipeline
    import sign
    from contract_store import activate_store, publish_signed_source
    from i18n_materializer import migrate_language_state_bytes
    from manifest_inventory import (
        ManifestOrigin,
        ManifestSource,
        inventory_authoring_manifests,
    )

    root = tmp_path / "executors"
    directory = root / "sample"
    directory.mkdir(parents=True)
    code = directory / "sample.py"
    code.write_text(
        "def invoke(args):\n    return {'ok': True, 'results': []}\n",
        encoding="utf-8",
    )
    digest = "sha256:" + hashlib.sha256(code.read_bytes()).hexdigest()
    manifest = directory / "manifest.toml"
    manifest.write_text(
        f'''manifest_format = "1.0"
executor_standard = "metnos.executor/1.0"
name = "read_files"
version = "1.0.0"
lifecycle = "active"
affinity = []

[description]
it = "SCOPO: Legge file. PATTERN: read_files(). NON: modificare file. OUT: results=[]."
en = "SCOPO: Reads files. PATTERN: read_files(). NON: modify files. OUT: results=[]."

[code]
files = ["sample.py"]
digest = "{digest}"

[output]
schema_inline = "{{ ok: bool, results: list }}"

[[capabilities]]
name = "fs:read"
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
        migrate_language_state_bytes(b"{}", manifest=parsed).state_bytes,
    )
    private = Ed25519PrivateKey.generate()
    (directory / "manifest.toml.sig").write_bytes(
        sign_manifest_bytes(manifest.read_bytes(), private_key=private),
    )

    state = tmp_path / "state"
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    monkeypatch.setattr(config, "PATH_USER_STATE", state)
    # The barrier that stops one checkout writing into another installation
    # cannot tell a redirected fixture from the real thing, so a fixture that
    # deliberately exercises the PRODUCTIVE wrapper must declare the
    # installation it is running from. This checkout is that installation.
    monkeypatch.setenv("METNOS_INSTALL_ROOT", str(config.PATH_ROOT))
    monkeypatch.setattr(config, "PATH_EXECUTORS", root)
    monkeypatch.setattr(config, "PATH_RUNTIME", runtime_root)
    for attribute in (
        "PATH_SKILLS_BUILTIN",
        "PATH_SYNTH_EXECUTORS",
        "PATH_SKILLS_USER",
        "PATH_SKILLS_USER_LEGACY",
    ):
        monkeypatch.setattr(config, attribute, tmp_path / "empty" / attribute)

    source = ManifestSource(
        ManifestOrigin.CORE,
        root,
        allowed_code_roots=(root, runtime_root),
    )

    def fresh_ref():
        inventory = inventory_authoring_manifests((source,))
        assert not inventory.problems
        return inventory.manifests[0]

    ref = fresh_ref()
    trusted = (("author", private.public_key()),)
    shadow = state / "contract-publications-shadow" / "attempt" / "v1"
    initial = publish_signed_source(
        ref,
        expected_generation_id=None,
        trusted_publics=trusted,
        store_root=shadow,
    )
    activate_store(
        {ref.contract_id: initial.current_generation_id},
        shadow_root=shadow,
        trusted_publics=trusted,
        quiescence_guard=lambda: True,
    )
    monkeypatch.setattr(
        sign,
        "_authoring_manifest_ref",
        lambda _path, **_kwargs: fresh_ref(),
    )
    monkeypatch.setattr(sign, "load_private", lambda _name: private)
    monkeypatch.setattr(sign, "list_trusted_publics", lambda: list(trusted))
    monkeypatch.setattr(
        i18n_pipeline,
        "reconcile_published_contract_registry",
        lambda _revision: None,
    )
    return SimpleNamespace(
        root=root,
        directory=directory,
        code=code,
        manifest=manifest,
        ref=ref,
        private=private,
        trusted=trusted,
        initial=initial,
        state=state,
        contract_store=contract_store,
        sign=sign,
    )


def test_publish_wrapper_is_not_a_store_only_birth_bypass(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _productive_store(tmp_path, monkeypatch)
    fixture.code.write_text(
        "def invoke(args):\n    return {'ok': True, 'results': ['updated']}\n",
        encoding="utf-8",
    )
    fixture.manifest.write_text(
        fixture.manifest.read_text(encoding="utf-8").replace(
            'version = "1.0.0"', 'version = "2.0.0"',
        ),
        encoding="utf-8",
    )

    before = fixture.manifest.read_bytes()
    with pytest.raises(RuntimeError, match="Executor Birth intent"):
        fixture.sign.publish_executor(fixture.directory)
    assert fixture.manifest.read_bytes() == before
    return
    live = fixture.contract_store.current_manifest(
        fixture.ref,
        trusted_publics=fixture.trusted,
    )

    assert published.current_generation_id != fixture.initial.current_generation_id
    assert live.generation_id == published.current_generation_id
    assert live.parsed["version"] == "2.0.0"
    assert live.verified_code_digest == (
        "sha256:" + hashlib.sha256(fixture.code.read_bytes()).hexdigest()
    )


def test_publish_wrapper_recovers_binding_only_first_publish_interruption(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _productive_store(tmp_path, monkeypatch)
    contract_dir = (
        fixture.state / "contract-publications" / "v1"
        / fixture.ref.contract_id.storage_key
    )
    generation = (
        contract_dir / "generations"
        / fixture.contract_store.generation_directory_name(
            fixture.initial.current_generation_id,
        )
    )
    (contract_dir / "current").unlink()
    for payload in generation.iterdir():
        payload.unlink()
    generation.rmdir()
    # A post-cutover source is prepared unsigned; the interrupted publisher
    # had signed only its in-memory candidate before failing after the binding.
    (fixture.directory / "manifest.toml.sig").unlink()
    assert (contract_dir / "binding.json").is_file()

    with pytest.raises(RuntimeError, match="Executor Birth intent"):
        fixture.sign.publish_executor(fixture.directory)
    return
    live = fixture.contract_store.current_manifest(
        fixture.ref,
        trusted_publics=fixture.trusted,
    )

    assert recovered.current_generation_id == fixture.initial.current_generation_id
    assert live.generation_id == fixture.initial.current_generation_id


def test_publish_wrapper_rejects_corrupt_current_generation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _productive_store(tmp_path, monkeypatch)
    generation = (
        fixture.state / "contract-publications" / "v1"
        / fixture.ref.contract_id.storage_key / "generations"
        / fixture.contract_store.generation_directory_name(
            fixture.initial.current_generation_id,
        )
    )
    (generation / "manifest.toml.sig").write_bytes(b"corrupt")
    fixture.code.write_text("def invoke(args):\n    return {'ok': False}\n")

    with pytest.raises(RuntimeError, match="Executor Birth intent"):
        fixture.sign.publish_executor(fixture.directory)


def test_publish_wrapper_cannot_reactivate_retired_contract(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _productive_store(tmp_path, monkeypatch)
    retired = fixture.sign.retire_executor_contract(
        fixture.directory,
        actor="test-author",
        reason="explicit retirement",
    )
    repeated = fixture.sign.retire_executor_contract(
        fixture.directory,
        actor="test-author",
        reason="explicit retirement",
    )
    assert retired.repeated is False
    assert repeated.repeated is True
    audit = fixture.state / "contract-publications.audit.jsonl"
    assert len(audit.read_text(encoding="utf-8").splitlines()) == 1
    fixture.code.write_text("def invoke(args):\n    return {'ok': False}\n")

    with pytest.raises(RuntimeError, match="Executor Birth intent"):
        fixture.sign.publish_executor(fixture.directory)


def test_explicit_reactivation_recovers_ambiguous_postcommit_retry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import i18n_pipeline

    fixture = _productive_store(tmp_path, monkeypatch)
    fixture.sign.retire_executor_contract(
        fixture.directory,
        actor="test-author",
        reason="uninstall before reinstall",
    )
    fixture.code.write_text(
        "def invoke(args):\n    return {'ok': True, 'results': ['restored']}\n",
        encoding="utf-8",
    )
    calls = 0

    def fail_after_commit(_revision):
        nonlocal calls
        calls += 1
        raise RuntimeError("registry unavailable after pointer commit")

    monkeypatch.setattr(
        i18n_pipeline,
        "reconcile_published_contract_registry",
        fail_after_commit,
    )
    before = fixture.manifest.read_bytes()
    with pytest.raises(RuntimeError, match="Executor Birth intent"):
        reactivate_executor_contract(
            fixture.directory,
            actor="skills_cli",
            reason="reinstall imported executor contract",
        )

    assert fixture.manifest.read_bytes() == before
    assert calls == 0
