from __future__ import annotations

import errno
import hashlib
import json
import multiprocessing
import os
import tomllib
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from contract_store import (
    BINDING_FILE,
    GENERATION_FILES,
    ContractStoreError,
    contract_storage_key,
    current_manifest,
    decode_binding,
    diagnose_store,
    encode_binding,
    generation_directory_name,
    generation_id,
    publish_signed_source,
    read_binding,
    verify_manifest_source,
)
from i18n_materializer import (
    LanguageStateError,
    decode_language_state,
    encode_language_state,
    manifest_language_selectors,
    migrate_language_state_bytes,
)
from manifest_inventory import (
    ContractId,
    ManifestOrigin,
    ManifestRef,
    ManifestSource,
    inventory_manifests,
)
from sign import sign_manifest_bytes
import contract_store as contract_store_module


def _hash_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _manifest_text(
    *,
    name: str,
    code_file: str,
    code_digest: str,
    it_text: str = "SCOPO: prova. PATTERN: sample(). NON: modifica. OUT: results=[].",
    en_text: str = "SCOPE: test. PATTERN: sample(). NOT: modify. OUT: results=[].",
) -> str:
    return f'''name = "{name}"
version = "1.0.0"

[description]
it = "{it_text}"
en = "{en_text}"

[code]
files = ["{code_file}"]
digest = "{code_digest}"

[args]
type = "object"
required = []

[args.properties.query]
type = "string"

[args.properties.query.description]
it = "Testo da cercare."
en = "Text to find."
'''


def _state_for(manifest: Mapping[str, Any]) -> dict[str, Any]:
    selectors = manifest_language_selectors(manifest)
    return {
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


def test_nested_json_schema_descriptions_are_canonical_surfaces() -> None:
    manifest = {
        "description": {"en": "Top", "it": "Alto"},
        "args": {
            "type": "object",
            "properties": {
                "messages": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "body": {
                                "type": "string",
                                "description": {"en": "Body", "it": "Corpo"},
                            },
                        },
                    },
                },
                "description": {
                    "type": "string",
                    "description": {"en": "Detail", "it": "Dettaglio"},
                },
            },
        },
    }

    selectors = manifest_language_selectors(manifest)

    assert set(selectors) == {
        "description",
        "args.properties.description.description",
        "args.properties.messages.items.properties.body.description",
    }
    state_bytes = encode_language_state(_state_for(manifest), manifest=manifest)
    assert set(decode_language_state(state_bytes, manifest=manifest)["selectors"]) == set(
        selectors
    )


def _inventory_ref(
    root: Path,
    *,
    name: str,
    origin: ManifestOrigin = ManifestOrigin.EXPLICIT,
    allowed_code_roots: tuple[Path, ...] | None = None,
) -> ManifestRef:
    inventory = inventory_manifests((ManifestSource(
        origin,
        root,
        min_depth=1,
        max_depth=1,
        allowed_code_roots=allowed_code_roots or (root,),
    ),))
    assert not inventory.problems
    return next(ref for ref in inventory.manifests if ref.name == name)


def _create_source(
    tmp_path: Path,
    *,
    name: str = "sample",
) -> tuple[Path, ManifestRef, Ed25519PrivateKey, tuple[tuple[str, Any], ...]]:
    root = tmp_path / "sources"
    directory = root / name
    directory.mkdir(parents=True)
    code = directory / f"{name}.py"
    code.write_text("def invoke(args):\n    return {'results': []}\n", encoding="utf-8")
    digest = "sha256:" + hashlib.sha256(code.read_bytes()).hexdigest()
    manifest = directory / "manifest.toml"
    manifest.write_text(
        _manifest_text(name=name, code_file=code.name, code_digest=digest),
        encoding="utf-8",
    )
    parsed = tomllib.loads(manifest.read_text(encoding="utf-8"))
    (directory / "manifest.lang_state.json").write_bytes(
        encode_language_state(_state_for(parsed), manifest=parsed)
    )
    private = Ed25519PrivateKey.generate()
    (directory / "manifest.toml.sig").write_bytes(sign_manifest_bytes(
        manifest.read_bytes(), private_key=private,
    ))
    ref = _inventory_ref(root, name=name)
    trusted = (("author", private.public_key()),)
    return root, ref, private, trusted


def _generation_path(store: Path, ref: ManifestRef, identifier: str) -> Path:
    return (
        store / contract_storage_key(ref.contract_id) / "generations"
        / generation_directory_name(identifier)
    )


def _source_payloads(ref: ManifestRef) -> dict[str, bytes]:
    return {name: (ref.manifest_dir / name).read_bytes() for name in GENERATION_FILES}


def _hold_writer_lock(
    contract_id,
    store: str,
    ready,
    release,
) -> None:
    with contract_store_module._writer_lock(
        contract_id, store_root=Path(store), timeout=2.0,
    ):
        ready.set()
        release.wait(5.0)


def _publish_same_candidate(
    ref: ManifestRef,
    public_key_bytes: bytes,
    store: str,
    ready,
    start,
    results,
) -> None:
    trusted = ((
        "author",
        Ed25519PublicKey.from_public_bytes(public_key_bytes),
    ),)
    ready.set()
    start.wait(5.0)
    try:
        result = publish_signed_source(
            ref,
            expected_generation_id=None,
            trusted_publics=trusted,
            store_root=Path(store),
            lock_timeout=3.0,
        )
    except Exception as exc:  # pragma: no cover - surfaced through the queue
        results.put(("error", type(exc).__name__, str(exc)))
    else:
        results.put(("ok", result.repeated, result.current_generation_id))


def test_state_is_canonical_deterministic_and_strict(tmp_path: Path) -> None:
    _root, ref, _private, _trusted = _create_source(tmp_path)
    parsed = tomllib.loads(ref.manifest_path.read_text(encoding="utf-8"))
    state = _state_for(parsed)
    reversed_state = {
        "selectors": dict(reversed(tuple(state["selectors"].items()))),
        "schema_version": 1,
    }

    first = encode_language_state(state, manifest=parsed)
    second = encode_language_state(reversed_state, manifest=parsed)

    assert first == second
    assert first.endswith(b"\n") and not first.endswith(b"\n\n")
    assert decode_language_state(first, manifest=parsed)["schema_version"] == 1

    legacy = json.loads(first)
    legacy["selectors"]["args.query.description"] = legacy["selectors"].pop(
        "args.properties.query.description"
    )
    with pytest.raises(LanguageStateError, match="language_state_coverage"):
        decode_language_state(
            json.dumps(legacy, sort_keys=True, separators=(",", ":")).encode() + b"\n",
            manifest=parsed,
        )

    noncanonical = json.loads(first)
    noncanonical["selectors"]["description"]["EN"] = (
        noncanonical["selectors"]["description"].pop("en")
    )
    with pytest.raises(LanguageStateError, match="language_state_language_coverage"):
        encode_language_state(noncanonical, manifest=parsed)


def test_explicit_migration_rebuilds_and_preserves_only_current_provenance(
    tmp_path: Path,
) -> None:
    _root, ref, _private, _trusted = _create_source(tmp_path)
    parsed = tomllib.loads(ref.manifest_path.read_text(encoding="utf-8"))
    source_hash = _hash_text(parsed["description"]["it"])
    legacy = {
        "description": {
            "en": {
                "version_hash": _hash_text(parsed["description"]["en"]),
                "source_lang": "it",
                "source_hash": source_hash,
            },
            "it": {
                "version_hash": "sha256:" + "0" * 64,
                "source_lang": "en",
                "source_hash": "sha256:" + "1" * 64,
            },
        },
        "args.query.description": {
            "en": {
                "version_hash": _hash_text("Text to find."),
                "source_lang": "it",
                "source_hash": "sha256:" + "2" * 64,
            },
        },
        "args.orphan.description": {"en": {}},
    }
    migration = migrate_language_state_bytes(
        json.dumps(legacy).encode("utf-8"),
        manifest=parsed,
    )
    decoded = decode_language_state(migration.state_bytes, manifest=parsed)

    assert set(decoded["selectors"]) == {
        "description", "args.properties.query.description",
    }
    assert decoded["selectors"]["description"]["en"]["source_lang"] == "it"
    assert decoded["selectors"]["description"]["it"]["source_lang"] is None
    assert decoded["selectors"]["args.properties.query.description"]["it"][
        "version_hash"
    ] == _hash_text("Testo da cercare.")
    assert decoded["selectors"]["args.properties.query.description"]["en"][
        "source_lang"
    ] is None
    assert migration.added_entries == (
        "args.properties.query.description:it",
    )
    assert migration.dropped_entries == (
        "args.orphan.description:*",
    )
    assert migration.cleared_provenance == (
        "args.properties.query.description:en",
        "description:it",
    )

    collision = dict(legacy)
    collision["args.properties.query.description"] = collision[
        "args.query.description"
    ]
    with pytest.raises(LanguageStateError, match="language_state_migration_ambiguous"):
        migrate_language_state_bytes(
            json.dumps(collision).encode("utf-8"),
            manifest=parsed,
        )


def test_language_state_rejects_unknown_fields_duplicates_and_noncanonical_bytes(
    tmp_path: Path,
) -> None:
    _root, ref, _private, _trusted = _create_source(tmp_path)
    parsed = tomllib.loads(ref.manifest_path.read_text(encoding="utf-8"))
    canonical = encode_language_state(_state_for(parsed), manifest=parsed)
    decoded = json.loads(canonical)
    decoded["extra"] = True
    with pytest.raises(LanguageStateError, match="language_state_schema"):
        encode_language_state(decoded, manifest=parsed)
    wrong_version = _state_for(parsed)
    wrong_version["schema_version"] = True
    with pytest.raises(LanguageStateError, match="language_state_version"):
        encode_language_state(wrong_version, manifest=parsed)

    wrong_source = _state_for(parsed)
    wrong_source["selectors"]["description"]["en"].update({
        "source_lang": "it",
        "source_hash": "sha256:" + "0" * 64,
    })
    with pytest.raises(LanguageStateError, match="language_source_mismatch"):
        encode_language_state(wrong_source, manifest=parsed)

    with pytest.raises(LanguageStateError, match="language_state_duplicate_key"):
        decode_language_state(
            canonical[:-2] + b',"schema_version":1}\n',
            manifest=parsed,
        )
    with pytest.raises(LanguageStateError, match="language_state_noncanonical"):
        decode_language_state(b" " + canonical, manifest=parsed)


def test_binding_is_canonical_path_free_and_bound_to_its_storage_key(
    tmp_path: Path,
) -> None:
    root, ref, _private, _trusted = _create_source(tmp_path)
    encoded = encode_binding(ref.contract_id)

    assert encoded == (
        b'{"contract_id":"explicit:sample/manifest.toml",'
        b'"schema_version":1}\n'
    )
    assert str(root.resolve()).encode("utf-8") not in encoded
    assert contract_storage_key(ref.contract_id) == ref.contract_id.storage_key
    decoded = decode_binding(
        encoded,
        storage_key=contract_storage_key(ref.contract_id),
    )
    assert decoded.contract_id == ref.contract_id
    assert decoded.storage_key == contract_storage_key(ref.contract_id)

    with pytest.raises(ContractStoreError, match="binding_invalid"):
        decode_binding(encoded, storage_key="0" * 64)
    with pytest.raises(ContractStoreError, match="binding_invalid"):
        decode_binding(encoded, storage_key=ref.contract_id.storage_key.upper())
    with pytest.raises(ContractStoreError, match="binding_invalid"):
        decode_binding(b" " + encoded, storage_key=ref.contract_id.storage_key)
    unsupported = encoded.replace(b'"schema_version":1', b'"schema_version":2')
    with pytest.raises(ContractStoreError, match="binding_invalid"):
        decode_binding(unsupported, storage_key=ref.contract_id.storage_key)
    with pytest.raises(ContractStoreError, match="binding_invalid"):
        decode_binding(
            b'{"contract_id":"explicit:sample/manifest.toml",'
            b'"contract_id":"explicit:sample/manifest.toml",'
            b'"schema_version":1}\n',
            storage_key=ref.contract_id.storage_key,
        )
    invalid_path = (
        b'{"contract_id":"explicit:../manifest.toml",'
        b'"schema_version":1}\n'
    )
    with pytest.raises(ContractStoreError, match="binding_invalid"):
        decode_binding(invalid_path, storage_key="0" * 64)
    windows_absolute = (
        b'{"contract_id":"explicit:C:/metnos/manifest.toml",'
        b'"schema_version":1}\n'
    )
    with pytest.raises(ContractStoreError, match="binding_invalid"):
        decode_binding(windows_absolute, storage_key="0" * 64)


def test_binding_survives_interruption_and_is_never_replaced(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _root, ref, _private, trusted = _create_source(tmp_path)
    store = tmp_path / "shadow" / "attempt" / "v1"
    real_install = contract_store_module._install_generation

    def interrupt_after_binding(*_args, **_kwargs):
        raise ContractStoreError("injected_interruption")

    monkeypatch.setattr(
        contract_store_module, "_install_generation", interrupt_after_binding,
    )
    with pytest.raises(ContractStoreError, match="injected_interruption"):
        publish_signed_source(
            ref,
            expected_generation_id=None,
            trusted_publics=trusted,
            store_root=store,
        )
    contract_dir = store / contract_storage_key(ref.contract_id)
    binding_bytes = (contract_dir / BINDING_FILE).read_bytes()
    assert read_binding(contract_dir).contract_id == ref.contract_id
    assert not (contract_dir / "current").exists()

    monkeypatch.setattr(contract_store_module, "_install_generation", real_install)
    published = publish_signed_source(
        ref,
        expected_generation_id=None,
        trusted_publics=trusted,
        store_root=store,
    )
    assert published.current_generation_id
    assert (contract_dir / BINDING_FILE).read_bytes() == binding_bytes

    replacement = encode_binding(ContractId(
        ManifestOrigin.EXPLICIT, "other/manifest.toml",
    ))
    (contract_dir / BINDING_FILE).write_bytes(replacement)
    with pytest.raises(ContractStoreError, match="binding_invalid"):
        publish_signed_source(
            ref,
            expected_generation_id=published.current_generation_id,
            trusted_publics=trusted,
            store_root=store,
        )
    assert (contract_dir / BINDING_FILE).read_bytes() == replacement


def test_missing_binding_with_history_is_corruption_not_reinitialization(
    tmp_path: Path,
) -> None:
    _root, ref, _private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    result = publish_signed_source(
        ref,
        expected_generation_id=None,
        trusted_publics=trusted,
        store_root=store,
    )
    contract_dir = store / contract_storage_key(ref.contract_id)
    (contract_dir / BINDING_FILE).unlink()

    with pytest.raises(ContractStoreError, match="binding_invalid"):
        current_manifest(ref, trusted_publics=trusted, store_root=store)
    with pytest.raises(ContractStoreError, match="binding_invalid"):
        publish_signed_source(
            ref,
            expected_generation_id=result.current_generation_id,
            trusted_publics=trusted,
            store_root=store,
        )
    assert not (contract_dir / BINDING_FILE).exists()


def test_binding_link_is_rejected_without_following_it(tmp_path: Path) -> None:
    _root, ref, _private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    contract_dir = store / contract_storage_key(ref.contract_id)
    (contract_dir / "generations").mkdir(parents=True)
    (contract_dir / "writer.lock").write_bytes(b"\0")
    outside = tmp_path / "outside-binding.json"
    outside.write_bytes(encode_binding(ref.contract_id))
    try:
        (contract_dir / BINDING_FILE).symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(ContractStoreError, match="binding_invalid"):
        publish_signed_source(
            ref,
            expected_generation_id=None,
            trusted_publics=trusted,
            store_root=store,
        )
    assert outside.read_bytes() == encode_binding(ref.contract_id)


def test_trusted_publics_are_snapshotted_and_validated_completely(
    tmp_path: Path,
) -> None:
    _root, ref, _private, trusted = _create_source(tmp_path)
    snapshot = verify_manifest_source(
        ref,
        trusted_publics=(item for item in trusted),
    )
    assert snapshot.signed_by == "author"

    with pytest.raises(ContractStoreError, match="trusted_keys_missing"):
        verify_manifest_source(ref, trusted_publics=())
    with pytest.raises(ContractStoreError, match="trusted_keys_invalid"):
        verify_manifest_source(
            ref,
            trusted_publics=trusted + (("malformed", object()),),
        )
    with pytest.raises(ContractStoreError, match="trusted_keys_invalid"):
        verify_manifest_source(
            ref,
            trusted_publics=trusted + (("author", Ed25519PrivateKey.generate().public_key()),),
        )
    with pytest.raises(ContractStoreError, match="trusted_keys_invalid"):
        verify_manifest_source(
            ref,
            trusted_publics=trusted + (("author-alias", trusted[0][1]),),
        )
    with pytest.raises(ContractStoreError, match="signature_invalid"):
        verify_manifest_source(
            ref,
            trusted_publics=(("other", Ed25519PrivateKey.generate().public_key()),),
        )


def test_source_snapshot_rejects_an_inventory_reference_made_stale(
    tmp_path: Path,
) -> None:
    _root, ref, private, trusted = _create_source(tmp_path)
    changed = ref.manifest_path.read_text(encoding="utf-8").replace(
        'version = "1.0.0"', 'version = "1.0.1"',
    )
    ref.manifest_path.write_text(changed, encoding="utf-8")
    (ref.manifest_dir / "manifest.toml.sig").write_bytes(sign_manifest_bytes(
        ref.manifest_path.read_bytes(), private_key=private,
    ))

    with pytest.raises(ContractStoreError, match="source_changed_since_inventory"):
        verify_manifest_source(ref, trusted_publics=trusted)


def test_m2_publish_requires_an_explicit_nonproduction_shadow_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _root, ref, _private, trusted = _create_source(tmp_path)
    user_state = tmp_path / "user-state"
    monkeypatch.setattr(contract_store_module._C, "PATH_USER_STATE", user_state)

    with pytest.raises(ContractStoreError, match="publication_not_active"):
        publish_signed_source(
            ref,
            expected_generation_id=None,
            trusted_publics=trusted,
        )
    with pytest.raises(ContractStoreError, match="publication_not_active"):
        publish_signed_source(
            ref,
            expected_generation_id=None,
            trusted_publics=trusted,
            store_root=user_state / "contract-publications" / "v1",
        )
    for overlapping in (
        user_state,
        user_state / "contract-publications",
        user_state / "contract-publications" / "nested" / "v1",
        user_state / "contract-publications.ACTIVE" / "nested",
    ):
        with pytest.raises(ContractStoreError, match="publication_not_active"):
            publish_signed_source(
                ref,
                expected_generation_id=None,
                trusted_publics=trusted,
                store_root=overlapping,
            )
    assert not user_state.exists()
    assert not hasattr(contract_store_module, "writer_lock")


def test_shadow_root_parent_link_is_rejected_before_creating_outside(
    tmp_path: Path,
) -> None:
    _root, ref, _private, trusted = _create_source(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    linked_parent = tmp_path / "linked-shadow"
    try:
        linked_parent.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(ContractStoreError, match="store_root_invalid"):
        publish_signed_source(
            ref,
            expected_generation_id=None,
            trusted_publics=trusted,
            store_root=linked_parent / "attempt" / "v1",
        )
    assert not (outside / "attempt").exists()


def test_generation_identifier_has_distinct_logical_and_windows_safe_forms(
    tmp_path: Path,
) -> None:
    _root, ref, _private, _trusted = _create_source(tmp_path)
    identifier = generation_id(_source_payloads(ref))

    assert identifier.startswith("sha256:")
    physical = generation_directory_name(identifier)
    assert len(physical) == 64
    assert ":" not in physical


def test_publish_and_idempotent_retry_verify_complete_current(tmp_path: Path) -> None:
    _root, ref, _private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    authoring_before = _source_payloads(ref)
    first = publish_signed_source(
        ref,
        expected_generation_id=None,
        trusted_publics=trusted,
        store_root=store,
    )
    repeated = publish_signed_source(
        ref,
        expected_generation_id=None,
        trusted_publics=trusted,
        store_root=store,
    )

    assert not first.repeated
    assert repeated.repeated
    assert repeated.current_generation_id == first.current_generation_id
    contract_dir = store / contract_storage_key(ref.contract_id)
    assert read_binding(contract_dir).contract_id == ref.contract_id
    assert {path.name for path in contract_dir.iterdir()} == {
        BINDING_FILE, "writer.lock", "current", "generations",
    }
    generation = _generation_path(store, ref, first.current_generation_id)
    assert {path.name for path in generation.iterdir()} == set(GENERATION_FILES)
    assert all(path.is_file() and not path.is_symlink() for path in generation.iterdir())
    assert _source_payloads(ref) == authoring_before
    assert not hasattr(contract_store_module, "activate_store")
    assert not hasattr(contract_store_module, "rollback")
    assert not hasattr(contract_store_module, "reconcile_authoring")

    (generation / "manifest.toml.sig").write_bytes(b"tampered")
    with pytest.raises(ContractStoreError, match="signature_invalid"):
        publish_signed_source(
            ref,
            expected_generation_id=None,
            trusted_publics=trusted,
            store_root=store,
        )


def test_stale_expected_generation_conflicts(tmp_path: Path) -> None:
    _root, ref, private, trusted = _create_source(tmp_path)
    other = Ed25519PrivateKey.generate()
    trusted_both = trusted + (("other", other.public_key()),)
    store = tmp_path / "store"
    first = publish_signed_source(
        ref, expected_generation_id=None,
        trusted_publics=trusted_both, store_root=store,
    )
    del private
    (ref.manifest_dir / "manifest.toml.sig").write_bytes(sign_manifest_bytes(
        ref.manifest_path.read_bytes(), private_key=other,
    ))

    with pytest.raises(ContractStoreError, match="commit_conflict"):
        publish_signed_source(
            ref,
            expected_generation_id=None,
            trusted_publics=trusted_both,
            store_root=store,
        )
    assert current_manifest(
        ref, trusted_publics=trusted_both, store_root=store,
    ).generation_id == first.current_generation_id


def test_signed_technical_update_authenticates_old_cas_without_old_code(
    tmp_path: Path,
) -> None:
    root, ref, private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    first = publish_signed_source(
        ref,
        expected_generation_id=None,
        trusted_publics=trusted,
        store_root=store,
    )
    code = ref.manifest_dir / "sample.py"
    old_digest = "sha256:" + hashlib.sha256(code.read_bytes()).hexdigest()
    code.write_text(
        "def invoke(args):\n    return {'results': [], 'version': 2}\n",
        encoding="utf-8",
    )
    new_digest = "sha256:" + hashlib.sha256(code.read_bytes()).hexdigest()
    changed = ref.manifest_path.read_text(encoding="utf-8").replace(
        old_digest, new_digest,
    )
    ref.manifest_path.write_text(changed, encoding="utf-8")
    (ref.manifest_dir / "manifest.toml.sig").write_bytes(sign_manifest_bytes(
        ref.manifest_path.read_bytes(), private_key=private,
    ))
    refreshed = _inventory_ref(root, name="sample")

    second = publish_signed_source(
        refreshed,
        expected_generation_id=first.current_generation_id,
        trusted_publics=trusted,
        store_root=store,
    )

    assert second.current_generation_id != first.current_generation_id
    snapshot = current_manifest(
        refreshed, trusted_publics=trusted, store_root=store,
    )
    assert snapshot.declared_code_digest == new_digest
    assert snapshot.verified_code_digest == new_digest


def test_published_generation_accepts_a_structural_ref_without_authoring_facts(
    tmp_path: Path,
) -> None:
    _root, ref, _private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    published = publish_signed_source(
        ref,
        expected_generation_id=None,
        trusted_publics=trusted,
        store_root=store,
    )
    structural = replace(
        ref,
        manifest_hash=None,
        name=None,
        lifecycle=None,
        skill_name=None,
    )
    ref.manifest_path.unlink()
    (ref.manifest_dir / "manifest.toml.sig").unlink()
    (ref.manifest_dir / "manifest.lang_state.json").unlink()

    snapshot = current_manifest(
        structural, trusted_publics=trusted, store_root=store,
    )

    assert snapshot.generation_id == published.current_generation_id
    assert snapshot.parsed["name"] == "sample"


def test_existing_generation_is_reused_or_rejected_without_replacement(
    tmp_path: Path,
) -> None:
    _root, ref, _private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    payloads = _source_payloads(ref)
    identifier = generation_id(payloads)
    final = _generation_path(store, ref, identifier)
    final.mkdir(parents=True)
    (final.parent.parent / BINDING_FILE).write_bytes(encode_binding(ref.contract_id))
    for name, payload in payloads.items():
        (final / name).write_bytes(payload)

    result = publish_signed_source(
        ref, expected_generation_id=None,
        trusted_publics=trusted, store_root=store,
    )
    assert result.current_generation_id == identifier

    # A same-name generation with any extra entry is corruption, never a
    # directory replacement candidate.
    (store / contract_storage_key(ref.contract_id) / "current").unlink()
    (final / "extra").write_text("unexpected", encoding="utf-8")
    with pytest.raises(ContractStoreError, match="generation_corrupt"):
        publish_signed_source(
            ref, expected_generation_id=None,
            trusted_publics=trusted, store_root=store,
        )
    assert (final / "extra").exists()


def test_missing_current_rejects_unrelated_history(tmp_path: Path) -> None:
    _root, ref, _private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    unrelated = _generation_path(store, ref, "sha256:" + "0" * 64)
    unrelated.mkdir(parents=True)
    (unrelated.parent.parent / BINDING_FILE).write_bytes(
        encode_binding(ref.contract_id)
    )

    with pytest.raises(ContractStoreError, match="current_missing_with_history"):
        publish_signed_source(
            ref, expected_generation_id=None,
            trusted_publics=trusted, store_root=store,
        )


def test_publish_fails_loud_if_a_newer_writer_wins_after_unlock(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _root, ref, _private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    real_current = contract_store_module.current_manifest

    def superseded(*args, **kwargs):
        snapshot = real_current(*args, **kwargs)
        return replace(snapshot, generation_id="sha256:" + "f" * 64)

    monkeypatch.setattr(contract_store_module, "current_manifest", superseded)
    with pytest.raises(ContractStoreError, match="publication_superseded"):
        publish_signed_source(
            ref, expected_generation_id=None,
            trusted_publics=trusted, store_root=store,
        )


@pytest.mark.parametrize("filename", GENERATION_FILES)
def test_every_generation_payload_is_integrity_checked(
    tmp_path: Path,
    filename: str,
) -> None:
    _root, ref, _private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    result = publish_signed_source(
        ref, expected_generation_id=None,
        trusted_publics=trusted, store_root=store,
    )
    target = _generation_path(store, ref, result.current_generation_id) / filename
    target.write_bytes(target.read_bytes() + b"tamper")

    with pytest.raises(ContractStoreError):
        current_manifest(ref, trusted_publics=trusted, store_root=store)


def test_generation_rejects_symlink_and_extra_entry(tmp_path: Path) -> None:
    _root, ref, _private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    result = publish_signed_source(
        ref, expected_generation_id=None,
        trusted_publics=trusted, store_root=store,
    )
    generation = _generation_path(store, ref, result.current_generation_id)
    signature = generation / "manifest.toml.sig"
    signature.unlink()
    try:
        signature.symlink_to(ref.manifest_dir / "manifest.toml.sig")
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(ContractStoreError, match="generation_file_invalid"):
        current_manifest(ref, trusted_publics=trusted, store_root=store)


def test_lock_timeout_uses_in_process_mutex_and_permanent_one_byte_file(
    tmp_path: Path,
) -> None:
    _root, ref, _private, _trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    with contract_store_module._writer_lock(
        ref.contract_id, store_root=store, timeout=0.2,
    ):
        with pytest.raises(ContractStoreError, match="lock_timeout"):
            with contract_store_module._writer_lock(
                ref.contract_id, store_root=store, timeout=0.02,
            ):
                pass
    lock_file = store / contract_storage_key(ref.contract_id) / "writer.lock"
    assert lock_file.read_bytes() == b"\0"


def test_lock_serializes_distinct_processes(tmp_path: Path) -> None:
    _root, ref, _private, _trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_writer_lock,
        args=(ref.contract_id, str(store), ready, release),
    )
    process.start()
    try:
        assert ready.wait(4.0)
        with pytest.raises(ContractStoreError, match="lock_timeout"):
            with contract_store_module._writer_lock(
                ref.contract_id, store_root=store, timeout=0.05,
            ):
                pass
    finally:
        release.set()
        process.join(5.0)
        if process.is_alive():
            process.terminate()
            process.join(2.0)
    assert process.exitcode == 0


def test_two_process_publishers_commit_one_complete_generation(
    tmp_path: Path,
) -> None:
    _root, ref, private, trusted = _create_source(tmp_path)
    del trusted
    store = tmp_path / "store"
    context = multiprocessing.get_context("spawn")
    ready = (context.Event(), context.Event())
    start = context.Event()
    results = context.Queue()
    public_bytes = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    processes = tuple(
        context.Process(
            target=_publish_same_candidate,
            args=(ref, public_bytes, str(store), ready[index], start, results),
        )
        for index in range(2)
    )
    for process in processes:
        process.start()
    try:
        assert all(event.wait(4.0) for event in ready)
        start.set()
        observed = [results.get(timeout=6.0) for _ in processes]
    finally:
        start.set()
        for process in processes:
            process.join(6.0)
            if process.is_alive():
                process.terminate()
                process.join(2.0)

    assert all(process.exitcode == 0 for process in processes)
    assert sorted(item[:2] for item in observed) == [
        ("ok", False), ("ok", True),
    ]
    assert len({item[2] for item in observed}) == 1
    generation = _generation_path(store, ref, observed[0][2])
    assert {path.name for path in generation.iterdir()} == set(GENERATION_FILES)


def test_existing_writer_lock_link_is_rejected_explicitly(tmp_path: Path) -> None:
    _root, ref, _private, _trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    contract_dir = store / contract_storage_key(ref.contract_id)
    (contract_dir / "generations").mkdir(parents=True)
    target = tmp_path / "outside-lock"
    target.write_bytes(b"\0")
    link = contract_dir / "writer.lock"
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(ContractStoreError, match="lock_file_invalid"):
        with contract_store_module._writer_lock(
            ref.contract_id, store_root=store, timeout=0.1,
        ):
            pass


def test_windows_pointer_replace_retries_only_for_a_finite_deadline(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "current"
    source.write_bytes(b"new")
    destination.write_bytes(b"old")
    real_replace = os.replace
    attempts = 0

    def transient_replace(first, second):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            error = PermissionError(errno.EACCES, "sharing violation")
            error.winerror = 32
            raise error
        real_replace(first, second)

    monkeypatch.setattr(contract_store_module, "_windows_platform", lambda: True)
    monkeypatch.setattr(contract_store_module.os, "replace", transient_replace)
    contract_store_module._replace_retry(source, destination, timeout=0.2)
    assert destination.read_bytes() == b"new"
    assert attempts == 3

    second_source = tmp_path / "source-2"
    second_source.write_bytes(b"newer")

    def permanent_failure(_first, _second):
        error = PermissionError(errno.EACCES, "sharing violation")
        error.winerror = 32
        raise error

    monkeypatch.setattr(contract_store_module.os, "replace", permanent_failure)
    with pytest.raises(ContractStoreError, match="pointer_replace_timeout"):
        contract_store_module._replace_retry(
            second_source, destination, timeout=0.0,
        )
    assert destination.read_bytes() == b"new"


def test_pointer_replace_does_not_retry_plain_windows_access_denied(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "current"
    source.write_bytes(b"new")
    destination.write_bytes(b"old")
    attempts = 0

    def access_denied(_first, _second):
        nonlocal attempts
        attempts += 1
        error = PermissionError(errno.EACCES, "access denied")
        error.winerror = 5
        raise error

    probed: list[Path] = []

    def no_delete_share_conflict(path: Path) -> bool:
        probed.append(path)
        return False

    monkeypatch.setattr(contract_store_module, "_windows_platform", lambda: True)
    monkeypatch.setattr(
        contract_store_module,
        "_windows_delete_share_conflict",
        no_delete_share_conflict,
    )
    monkeypatch.setattr(contract_store_module.os, "replace", access_denied)

    with pytest.raises(PermissionError, match="access denied"):
        contract_store_module._replace_retry(
            source, destination, timeout=1.0,
        )
    assert attempts == 1
    assert probed == [destination, source]
    assert destination.read_bytes() == b"old"


def test_pointer_replace_retries_winerror_5_only_after_delete_share_probe(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "current"
    source.write_bytes(b"new")
    destination.write_bytes(b"old")
    real_replace = os.replace
    attempts = 0
    probed: list[Path] = []

    def transient_access_denied(first, second):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            error = PermissionError(errno.EACCES, "ambiguous access denied")
            error.winerror = 5
            raise error
        real_replace(first, second)

    def confirmed_delete_share_conflict(path: Path) -> bool:
        probed.append(path)
        return path == destination

    monkeypatch.setattr(contract_store_module, "_windows_platform", lambda: True)
    monkeypatch.setattr(
        contract_store_module,
        "_windows_delete_share_conflict",
        confirmed_delete_share_conflict,
    )
    monkeypatch.setattr(
        contract_store_module.os,
        "replace",
        transient_access_denied,
    )

    contract_store_module._replace_retry(source, destination, timeout=0.2)

    assert attempts == 2
    assert probed == [destination]
    assert destination.read_bytes() == b"new"


def test_native_no_replace_never_overwrites_file_or_directory(tmp_path: Path) -> None:
    source_file = tmp_path / "source-file"
    destination_file = tmp_path / "destination-file"
    source_file.write_bytes(b"new")
    destination_file.write_bytes(b"old")

    with pytest.raises(FileExistsError):
        contract_store_module._rename_no_replace(source_file, destination_file)
    assert source_file.read_bytes() == b"new"
    assert destination_file.read_bytes() == b"old"

    source_directory = tmp_path / "source-directory"
    destination_directory = tmp_path / "destination-directory"
    source_directory.mkdir()
    destination_directory.mkdir()
    with pytest.raises(FileExistsError):
        contract_store_module._rename_no_replace(
            source_directory, destination_directory,
        )
    assert source_directory.is_dir()
    assert destination_directory.is_dir()


def test_process_mutex_is_released_even_if_system_unlock_raises(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _root, ref, _private, _trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    real_release = contract_store_module._release_system_lock

    def failed_release(_handle):
        raise OSError("injected unlock failure")

    monkeypatch.setattr(
        contract_store_module, "_release_system_lock", failed_release,
    )
    with pytest.raises(OSError, match="injected unlock failure"):
        with contract_store_module._writer_lock(
            ref.contract_id, store_root=store, timeout=0.2,
        ):
            pass

    monkeypatch.setattr(
        contract_store_module, "_release_system_lock", real_release,
    )
    with contract_store_module._writer_lock(
        ref.contract_id, store_root=store, timeout=0.2,
    ):
        pass


def test_builtin_parent_paths_and_allowed_symlink_targets_are_general(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    contracts = runtime_root / "builtin_executor_contracts"
    directory = contracts / "sample"
    directory.mkdir(parents=True)
    code = runtime_root / "shared.py"
    code.write_text("VALUE = 1\n", encoding="utf-8")
    digest = "sha256:" + hashlib.sha256(code.read_bytes()).hexdigest()
    manifest = directory / "manifest.toml"
    manifest.write_text(_manifest_text(
        name="sample", code_file="../../shared.py", code_digest=digest,
    ), encoding="utf-8")
    parsed = tomllib.loads(manifest.read_text(encoding="utf-8"))
    (directory / "manifest.lang_state.json").write_bytes(
        encode_language_state(_state_for(parsed), manifest=parsed)
    )
    private = Ed25519PrivateKey.generate()
    (directory / "manifest.toml.sig").write_bytes(sign_manifest_bytes(
        manifest.read_bytes(), private_key=private,
    ))
    ref = _inventory_ref(
        contracts,
        name="sample",
        origin=ManifestOrigin.BUILTIN,
        allowed_code_roots=(runtime_root,),
    )

    verified = verify_manifest_source(
        ref, trusted_publics=(("author", private.public_key()),),
    )
    assert verified.verified_code_digest == digest

    outside = tmp_path / "outside.py"
    outside.write_text("VALUE = 2\n", encoding="utf-8")
    outside_digest = "sha256:" + hashlib.sha256(outside.read_bytes()).hexdigest()
    manifest.write_text(_manifest_text(
        name="sample", code_file="../../../outside.py", code_digest=outside_digest,
    ), encoding="utf-8")
    parsed = tomllib.loads(manifest.read_text(encoding="utf-8"))
    (directory / "manifest.lang_state.json").write_bytes(
        encode_language_state(_state_for(parsed), manifest=parsed)
    )
    (directory / "manifest.toml.sig").write_bytes(sign_manifest_bytes(
        manifest.read_bytes(), private_key=private,
    ))
    escaped_ref = _inventory_ref(
        contracts,
        name="sample",
        origin=ManifestOrigin.BUILTIN,
        allowed_code_roots=(runtime_root,),
    )
    with pytest.raises(ContractStoreError, match="code_path_escape"):
        verify_manifest_source(
            escaped_ref, trusted_publics=(("author", private.public_key()),),
        )


def test_code_symlink_requires_both_lexical_and_resolved_roots(tmp_path: Path) -> None:
    executors = tmp_path / "executors"
    runtime = tmp_path / "runtime"
    directory = executors / "sample"
    directory.mkdir(parents=True)
    runtime.mkdir()
    target = runtime / "shared.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    link = directory / "shared.py"
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    digest = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
    manifest = directory / "manifest.toml"
    manifest.write_text(_manifest_text(
        name="sample", code_file="shared.py", code_digest=digest,
    ), encoding="utf-8")
    parsed = tomllib.loads(manifest.read_text(encoding="utf-8"))
    (directory / "manifest.lang_state.json").write_bytes(
        encode_language_state(_state_for(parsed), manifest=parsed)
    )
    private = Ed25519PrivateKey.generate()
    (directory / "manifest.toml.sig").write_bytes(sign_manifest_bytes(
        manifest.read_bytes(), private_key=private,
    ))
    trusted = (("author", private.public_key()),)
    allowed = _inventory_ref(
        executors,
        name="sample",
        origin=ManifestOrigin.CORE,
        allowed_code_roots=(executors, runtime),
    )
    assert verify_manifest_source(allowed, trusted_publics=trusted).verified_code_digest == digest

    denied = _inventory_ref(
        executors,
        name="sample",
        origin=ManifestOrigin.CORE,
        allowed_code_roots=(executors,),
    )
    with pytest.raises(ContractStoreError, match="code_target_escape"):
        verify_manifest_source(denied, trusted_publics=trusted)


def test_diagnostics_are_read_only(tmp_path: Path) -> None:
    _root, ref, _private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    result = publish_signed_source(
        ref, expected_generation_id=None,
        trusted_publics=trusted, store_root=store,
    )
    (store / contract_storage_key(ref.contract_id) / "writer.lock").unlink()
    orphan = _generation_path(store, ref, "sha256:" + "f" * 64)
    orphan.mkdir()

    def snapshot() -> dict[str, tuple[int, bytes | None]]:
        out: dict[str, tuple[int, bytes | None]] = {}
        for path in sorted(store.rglob("*")):
            stat = path.lstat()
            out[str(path.relative_to(store))] = (
                stat.st_mode,
                path.read_bytes() if path.is_file() else None,
            )
        return out

    before = snapshot()
    diagnostics = diagnose_store(
        (ref,), trusted_publics=trusted, store_root=store,
        orphan_warning_threshold=0,
    )
    after = snapshot()

    assert before == after
    assert result.current_generation_id
    assert {item.code for item in diagnostics} >= {
        "generation_structure", "lock_file_missing", "orphan_threshold_exceeded",
    }
