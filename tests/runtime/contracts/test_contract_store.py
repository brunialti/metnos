from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import multiprocessing
import os
import shutil
import threading
import tomllib
import tomlkit
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
    ACTIVE_BYTES,
    BINDING_FILE,
    BirthCommitAuthorization,
    GENERATION_FILES,
    RETIREMENT_FILES,
    ContractRetirement,
    ContractStoreError,
    LocalizationPatch,
    ProductionStoreMode,
    SurfaceRemoval,
    TechnicalDraft,
    activate_store,
    authenticate_execution_binding,
    commit_birth_snapshot,
    contract_storage_key,
    contract_revision_id,
    current_contract,
    current_manifest,
    current_revision_id,
    decode_binding,
    diagnose_store,
    encode_binding,
    generation_directory_name,
    generation_id,
    prepare_technical_draft,
    persist_current_reattestation_receipt_v2,
    production_store_mode,
    publish_localization,
    publish_signed_source,
    publish_technical_update,
    reactivate_technical_update,
    read_current_birth_receipt_v2,
    read_binding,
    rollback,
    retire,
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
    ManifestInventory,
    ManifestOrigin,
    ManifestRef,
    ManifestSource,
    ManifestStatus,
    inventory_manifests,
)
import manifest_inventory as manifest_inventory_module
from sign import sign_manifest_bytes
from executor_birth_receipts import (
    AdmissionCheck,
    AdmittedCheckStatus,
    AdmissionKind,
    ApprovedLifecycle,
    RevisionClass,
    issue_admission_receipt,
    verify_admission_receipt,
)
from executor_birth_snapshot import acquire_candidate_snapshot
import contract_store as contract_store_module
import audit_jsonl as audit_jsonl_module
from audit_jsonl import append_jsonl


def _hash_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _manifest_text(
    *,
    name: str,
    code_file: str,
    code_digest: str,
    it_text: str = "SCOPO: prova. PATTERN: sample(). NON: modifica. OUT: results=[].",
    en_text: str = "SCOPO: test. PATTERN: sample(). NON: modify. OUT: results=[].",
    placement_scope: str | None = None,
) -> str:
    placement = (
        f'\n[placement]\nscope = "{placement_scope}"\n'
        if placement_scope is not None else ""
    )
    return f'''manifest_format = "1.0"
executor_standard = "metnos.executor/1.0"
name = "{name}"
version = "1.0.0"

[description]
it = "{it_text}"
en = "{en_text}"

[code]
files = ["{code_file}"]
digest = "{code_digest}"

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

[args.properties.query]
type = "string"

[args.properties.query.description]
it = "Testo da cercare."
en = "Text to find."
{placement}'''


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


def test_every_admitted_contract_has_an_exact_editing_round_trip() -> None:
    shared_inventory = inventory_manifests()
    assert not shared_inventory.problems
    manifests = shared_inventory.admitted()
    assert manifests
    failures: list[str] = []
    for ref in manifests:
        try:
            contract_store_module._editable_manifest(ref.manifest_path.read_bytes())
        except ContractStoreError as exc:
            failures.append(f"{ref.contract_id}: {exc.code}")
    assert not failures, "\n".join(failures)


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
    name: str = "read_files",
) -> tuple[Path, ManifestRef, Ed25519PrivateKey, tuple[tuple[str, Any], ...]]:
    root = tmp_path / "sources"
    # Keep the storage slug independent from the manifest-owned canonical
    # name: the contract identity is structural, while the executor name must
    # obey the naming authority.
    directory = root / ("sample" if name == "read_files" else name)
    directory.mkdir(parents=True)
    code = directory / f"{directory.name}.py"
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


def _add_source_contract(
    root: Path,
    *,
    directory_name: str,
    name: str,
    private: Ed25519PrivateKey,
) -> ManifestRef:
    directory = root / directory_name
    directory.mkdir(parents=True)
    code = directory / f"{directory_name}.py"
    code.write_text(
        "def invoke(args):\n    return {'results': []}\n",
        encoding="utf-8",
    )
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
    (directory / "manifest.toml.sig").write_bytes(sign_manifest_bytes(
        manifest.read_bytes(), private_key=private,
    ))
    return _inventory_ref(root, name=name)


def _create_productive_shadow(
    tmp_path: Path,
    monkeypatch,
) -> tuple[
    Path,
    ManifestRef,
    Ed25519PrivateKey,
    tuple[tuple[str, Any], ...],
    Path,
    Any,
]:
    # These tests exercise the lower-level pre-cutover store invariants.  The
    # compiled productive denial has its own closed-build test suite.
    monkeypatch.setattr(
        contract_store_module, "_deny_closed_legacy_api",
        lambda _operation, _store_root: None,
    )
    root, _explicit_ref, private, trusted = _create_source(tmp_path)
    ref = _inventory_ref(root, name="read_files", origin=ManifestOrigin.CORE)
    user_state = tmp_path / "user-state"
    module_root = Path(contract_store_module.__file__).resolve().parents[1]
    monkeypatch.setenv("METNOS_INSTALL_ROOT", str(module_root))
    monkeypatch.setattr(contract_store_module._C, "PATH_ROOT", module_root)
    monkeypatch.setattr(contract_store_module._C, "PATH_USER_STATE", user_state)
    monkeypatch.setattr(contract_store_module._C, "PATH_EXECUTORS", root)
    monkeypatch.setattr(contract_store_module._C, "PATH_RUNTIME", root)
    # Keep the freshly regenerated activation inventory bounded to this
    # fixture instead of inheriting contracts from the developer machine.
    for attribute in (
        "PATH_SKILLS_BUILTIN",
        "PATH_SYNTH_EXECUTORS",
        "PATH_SKILLS_USER",
        "PATH_SKILLS_USER_LEGACY",
    ):
        monkeypatch.setattr(
            contract_store_module._C,
            attribute,
            tmp_path / "empty-inventory" / attribute.lower(),
        )
    shadow = user_state / "contract-publications-shadow" / "attempt" / "v1"
    initial = publish_signed_source(
        ref,
        expected_generation_id=None,
        trusted_publics=trusted,
        store_root=shadow,
    )
    return root, ref, private, trusted, shadow, initial


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


def _try_catalog_lock_after_fork(store: str, result, exclusive: bool) -> None:
    try:
        with contract_store_module.catalog_admission_lock(
            store_root=Path(store), exclusive=exclusive, timeout=0.05,
        ):
            result.send("acquired")
    except ContractStoreError as exc:
        result.send(exc.code)
    finally:
        result.close()


@pytest.mark.skipif(
    not hasattr(os, "geteuid"), reason="requires POSIX ownership",
)
def test_privileged_catalog_lock_accepts_only_the_declared_service_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = tmp_path / "store"
    with contract_store_module.catalog_admission_lock(store_root=store):
        pass

    expected = (os.getuid(), os.getgid())
    if expected[0] == 0:
        expected = (995, 985)
        real_fstat = os.fstat

        def service_owned(descriptor: int):
            values = list(real_fstat(descriptor))
            values[4], values[5] = expected
            return os.stat_result(values)

        monkeypatch.setattr(contract_store_module.os, "fstat", service_owned)
    monkeypatch.setattr(contract_store_module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(contract_store_module.os, "getegid", lambda: 0)

    with contract_store_module.catalog_admission_lock(
        store_root=store, trusted_owner=expected,
    ):
        pass
    with pytest.raises(ContractStoreError, match="catalog_lock_invalid"):
        with contract_store_module.catalog_admission_lock(
            store_root=store,
            trusted_owner=(expected[0] + 1, expected[1]),
        ):
            pass


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


def test_first_publish_recovers_only_reserved_crash_staging(tmp_path: Path) -> None:
    _root, ref, _private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    contract_dir = store / contract_storage_key(ref.contract_id)
    staging = contract_dir / "generations" / ".generation-crashed-writer"
    staging.mkdir(parents=True)
    (contract_dir / BINDING_FILE).write_bytes(encode_binding(ref.contract_id))
    # Each file is installed atomically, so a hard stop can leave any proper
    # subset, including a partially useful manifest-only staging directory.
    (staging / "manifest.toml").write_bytes(ref.manifest_path.read_bytes())

    published = publish_signed_source(
        ref,
        expected_generation_id=None,
        trusted_publics=trusted,
        store_root=store,
    )

    assert published.current_generation_id
    assert not staging.exists()


def test_first_publish_rejects_arbitrary_staging_debris_without_deleting_it(
    tmp_path: Path,
) -> None:
    _root, ref, _private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    contract_dir = store / contract_storage_key(ref.contract_id)
    staging = contract_dir / "generations" / ".generation-not-a-transaction"
    staging.mkdir(parents=True)
    (contract_dir / BINDING_FILE).write_bytes(encode_binding(ref.contract_id))
    debris = staging / "unrelated.bin"
    debris.write_bytes(b"not publication staging")

    with pytest.raises(ContractStoreError, match="staging_invalid"):
        publish_signed_source(
            ref,
            expected_generation_id=None,
            trusted_publics=trusted,
            store_root=store,
        )

    assert debris.read_bytes() == b"not publication staging"


def test_direct_staging_is_all_validated_before_any_cleanup(
    tmp_path: Path,
) -> None:
    _root, ref, _private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    initial = publish_signed_source(
        ref,
        expected_generation_id=None,
        trusted_publics=trusted,
        store_root=store,
    )
    contract_dir = store / contract_storage_key(ref.contract_id)
    valid = contract_dir / ".binding.json.1.2.3.tmp"
    mixed = contract_dir / ".current.4.5.6.tmp"
    valid.write_bytes(encode_binding(ref.contract_id))
    mixed.write_bytes(encode_binding(ref.contract_id))

    with pytest.raises(ContractStoreError, match="staging_invalid"):
        publish_signed_source(
            ref,
            expected_generation_id=initial.current_generation_id,
            trusted_publics=trusted,
            store_root=store,
        )

    assert valid.read_bytes() == encode_binding(ref.contract_id)
    assert mixed.read_bytes() == encode_binding(ref.contract_id)


@pytest.mark.parametrize("staging_kind", ("binding", "current"))
def test_direct_staging_requires_exact_authenticated_payload(
    tmp_path: Path,
    staging_kind: str,
) -> None:
    _root, ref, _private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    initial = publish_signed_source(
        ref,
        expected_generation_id=None,
        trusted_publics=trusted,
        store_root=store,
    )
    contract_dir = store / contract_storage_key(ref.contract_id)
    if staging_kind == "binding":
        staged = contract_dir / ".binding.json.1.2.3.tmp"
        staged.write_bytes(b'{}\n')
    else:
        staged = contract_dir / ".current.1.2.3.tmp"
        staged.write_bytes(("sha256:" + "0" * 64 + "\n").encode("ascii"))

    with pytest.raises(ContractStoreError, match="staging_invalid"):
        publish_signed_source(
            ref,
            expected_generation_id=initial.current_generation_id,
            trusted_publics=trusted,
            store_root=store,
        )

    assert staged.exists()


def test_direct_staging_rejects_noncanonical_name_without_cleanup(
    tmp_path: Path,
) -> None:
    _root, ref, _private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    initial = publish_signed_source(
        ref,
        expected_generation_id=None,
        trusted_publics=trusted,
        store_root=store,
    )
    contract_dir = store / contract_storage_key(ref.contract_id)
    valid = contract_dir / ".binding.json.1.2.3.tmp"
    malformed = contract_dir / ".current.01.2.3.tmp"
    valid.write_bytes(encode_binding(ref.contract_id))
    malformed.write_bytes((initial.current_generation_id + "\n").encode("ascii"))

    with pytest.raises(ContractStoreError, match="staging_invalid"):
        publish_signed_source(
            ref,
            expected_generation_id=initial.current_generation_id,
            trusted_publics=trusted,
            store_root=store,
        )

    assert valid.exists()
    assert malformed.exists()


def test_direct_staging_link_is_rejected_without_following_or_cleanup(
    tmp_path: Path,
) -> None:
    _root, ref, _private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    initial = publish_signed_source(
        ref,
        expected_generation_id=None,
        trusted_publics=trusted,
        store_root=store,
    )
    contract_dir = store / contract_storage_key(ref.contract_id)
    valid = contract_dir / ".binding.json.1.2.3.tmp"
    linked = contract_dir / ".current.4.5.6.tmp"
    valid.write_bytes(encode_binding(ref.contract_id))
    try:
        linked.symlink_to(contract_dir / "current")
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(ContractStoreError, match="staging_invalid"):
        publish_signed_source(
            ref,
            expected_generation_id=initial.current_generation_id,
            trusted_publics=trusted,
            store_root=store,
        )

    assert valid.exists()
    assert linked.is_symlink()
    assert (contract_dir / "current").read_bytes() == (
        initial.current_generation_id + "\n"
    ).encode("ascii")


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


def test_publication_cannot_bypass_standard_by_removing_its_declaration(
    tmp_path: Path,
) -> None:
    root, ref, private, trusted = _create_source(tmp_path)
    text = ref.manifest_path.read_text(encoding="utf-8")
    ref.manifest_path.write_text(
        text.replace('executor_standard = "metnos.executor/1.0"\n', "", 1),
        encoding="utf-8",
    )
    (ref.manifest_dir / "manifest.toml.sig").write_bytes(sign_manifest_bytes(
        ref.manifest_path.read_bytes(), private_key=private,
    ))
    refreshed = _inventory_ref(root, name="read_files")

    with pytest.raises(ContractStoreError, match="standard_missing"):
        verify_manifest_source(refreshed, trusted_publics=trusted)


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
    monkeypatch.setattr(
        contract_store_module, "_deny_closed_legacy_api",
        lambda _operation, _store_root: None,
    )
    _root, ref, _private, trusted = _create_source(tmp_path)
    user_state = tmp_path / "user-state"
    module_root = Path(contract_store_module.__file__).resolve().parents[1]
    monkeypatch.setenv("METNOS_INSTALL_ROOT", str(module_root))
    monkeypatch.setattr(contract_store_module._C, "PATH_ROOT", module_root)
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


def test_productive_publication_refuses_unselected_checkout_before_state_read(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        contract_store_module, "_deny_closed_legacy_api",
        lambda _operation, _store_root: None,
    )
    _root, ref, _private, trusted = _create_source(tmp_path)
    user_state = tmp_path / "shared-user-state"
    monkeypatch.setattr(contract_store_module._C, "PATH_USER_STATE", user_state)
    monkeypatch.delenv("METNOS_INSTALL_ROOT", raising=False)

    observed = False

    def unexpected_state_read() -> ProductionStoreMode:
        nonlocal observed
        observed = True
        return ProductionStoreMode.ACTIVE

    monkeypatch.setattr(
        contract_store_module, "production_store_mode", unexpected_state_read,
    )
    with pytest.raises(
        ContractStoreError, match="publication_installation_root_required",
    ):
        publish_signed_source(
            ref,
            expected_generation_id=None,
            trusted_publics=trusted,
        )

    assert not observed
    assert not user_state.exists()


def test_productive_publication_refuses_different_configured_checkout(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        contract_store_module, "_deny_closed_legacy_api",
        lambda _operation, _store_root: None,
    )
    _root, ref, _private, trusted = _create_source(tmp_path)
    user_state = tmp_path / "shared-user-state"
    monkeypatch.setattr(contract_store_module._C, "PATH_USER_STATE", user_state)
    monkeypatch.setenv("METNOS_INSTALL_ROOT", str(tmp_path / "other-checkout"))

    with pytest.raises(
        ContractStoreError, match="publication_installation_root_mismatch",
    ):
        publish_signed_source(
            ref,
            expected_generation_id=None,
            trusted_publics=trusted,
        )

    assert not user_state.exists()


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
    assert hasattr(contract_store_module, "activate_store")
    assert hasattr(contract_store_module, "rollback")
    assert not hasattr(contract_store_module, "reconcile_authoring")

    (generation / "manifest.toml.sig").write_bytes(b"tampered")
    with pytest.raises(ContractStoreError, match="signature_invalid"):
        publish_signed_source(
            ref,
            expected_generation_id=None,
            trusted_publics=trusted,
            store_root=store,
        )


def test_disabled_source_can_be_installed_without_becoming_a_retired_source(
    tmp_path: Path,
) -> None:
    root, ref, private, trusted = _create_source(tmp_path)
    manifest = ref.manifest_path
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            'version = "1.0.0"\n',
            'version = "1.0.0"\nlifecycle = "disabled"\n',
            1,
        ),
        encoding="utf-8",
    )
    (ref.manifest_dir / "manifest.toml.sig").write_bytes(
        sign_manifest_bytes(manifest.read_bytes(), private_key=private),
    )
    disabled = _inventory_ref(root, name="read_files")
    assert disabled.status is ManifestStatus.DISABLED

    store = tmp_path / "store"
    published = publish_signed_source(
        disabled,
        expected_generation_id=None,
        trusted_publics=trusted,
        store_root=store,
    )

    live = current_manifest(
        disabled, trusted_publics=trusted, store_root=store,
    )
    assert live.generation_id == published.current_generation_id
    assert live.parsed["lifecycle"] == "disabled"


@pytest.mark.parametrize("candidate_status", [
    ManifestStatus.ADMITTED,
    ManifestStatus.DISABLED,
])
def test_publication_rejects_duplicate_installed_name_before_creating_binding(
    tmp_path: Path,
    candidate_status: ManifestStatus,
) -> None:
    root, first_ref, private, trusted = _create_source(tmp_path)
    second_ref = _add_source_contract(
        root,
        directory_name="second",
        name="second_name",
        private=private,
    )
    store = tmp_path / "store"
    publish_signed_source(
        first_ref,
        expected_generation_id=None,
        trusted_publics=trusted,
        store_root=store,
    )

    second_manifest = second_ref.manifest_path
    second_manifest.write_text(
        second_manifest.read_text(encoding="utf-8").replace(
            'name = "second_name"',
            'name = "read_files"',
            1,
        ),
        encoding="utf-8",
    )
    (second_ref.manifest_dir / "manifest.toml.sig").write_bytes(
        sign_manifest_bytes(second_manifest.read_bytes(), private_key=private),
    )
    colliding = replace(
        second_ref,
        status=candidate_status,
        name="read_files",
        manifest_hash=(
            "sha256:" + hashlib.sha256(second_manifest.read_bytes()).hexdigest()
        ),
    )

    with pytest.raises(ContractStoreError) as caught:
        publish_signed_source(
            colliding,
            expected_generation_id=None,
            trusted_publics=trusted,
            store_root=store,
        )

    assert caught.value.code == "published_name_collision"
    assert not (store / contract_storage_key(colliding.contract_id)).exists()


@pytest.mark.parametrize(
    "unavailable_code",
    ("code_file_missing", "code_file_invalid", "code_file_unreadable"),
)
def test_unavailable_payload_does_not_block_unrelated_contract_repair(
    tmp_path: Path,
    monkeypatch,
    unavailable_code: str,
) -> None:
    root, first_ref, private, trusted = _create_source(tmp_path)
    second_ref = _add_source_contract(
        root,
        directory_name="second",
        name="read_contacts",
        private=private,
    )
    store = tmp_path / "store"
    publish_signed_source(
        first_ref,
        expected_generation_id=None,
        trusted_publics=trusted,
        store_root=store,
    )
    second = publish_signed_source(
        second_ref,
        expected_generation_id=None,
        trusted_publics=trusted,
        store_root=store,
    )
    (second_ref.manifest_dir / "second.py").write_text(
        "def invoke(args):\n    return {'results': ['repaired']}\n",
        encoding="utf-8",
    )
    draft = prepare_technical_draft(second_ref)
    real_current = contract_store_module.current_contract

    def current_with_unavailable_payload(current_ref, **kwargs):
        if current_ref.contract_id == first_ref.contract_id:
            raise ContractStoreError(unavailable_code)
        return real_current(current_ref, **kwargs)

    monkeypatch.setattr(
        contract_store_module, "current_contract", current_with_unavailable_payload,
    )
    repaired = publish_technical_update(
        second_ref,
        expected_generation_id=second.current_generation_id,
        draft=draft,
        private_key=private,
        trusted_publics=trusted,
        store_root=store,
    )

    assert repaired.current_generation_id != second.current_generation_id


def test_payload_digest_inconsistency_still_blocks_unrelated_repair(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root, first_ref, private, trusted = _create_source(tmp_path)
    second_ref = _add_source_contract(
        root,
        directory_name="second",
        name="read_contacts",
        private=private,
    )
    store = tmp_path / "store"
    publish_signed_source(
        first_ref,
        expected_generation_id=None,
        trusted_publics=trusted,
        store_root=store,
    )
    second = publish_signed_source(
        second_ref,
        expected_generation_id=None,
        trusted_publics=trusted,
        store_root=store,
    )
    (second_ref.manifest_dir / "second.py").write_text(
        "def invoke(args):\n    return {'results': ['candidate']}\n",
        encoding="utf-8",
    )
    draft = prepare_technical_draft(second_ref)
    real_current = contract_store_module.current_contract

    def current_with_inconsistent_digest(current_ref, **kwargs):
        if current_ref.contract_id == first_ref.contract_id:
            raise ContractStoreError("code_digest_mismatch")
        return real_current(current_ref, **kwargs)

    monkeypatch.setattr(
        contract_store_module, "current_contract", current_with_inconsistent_digest,
    )
    with pytest.raises(ContractStoreError, match="code_digest_mismatch"):
        publish_technical_update(
            second_ref,
            expected_generation_id=second.current_generation_id,
            draft=draft,
            private_key=private,
            trusted_publics=trusted,
            store_root=store,
        )

    assert current_revision_id(
        second_ref, store_root=store,
    ) == second.current_generation_id


def test_unavailable_payload_keeps_its_signed_name_reserved(
    tmp_path: Path,
) -> None:
    root, first_ref, private, trusted = _create_source(tmp_path)
    second_ref = _add_source_contract(
        root,
        directory_name="second",
        name="read_contacts",
        private=private,
    )
    store = tmp_path / "store"
    publish_signed_source(
        first_ref,
        expected_generation_id=None,
        trusted_publics=trusted,
        store_root=store,
    )
    (first_ref.manifest_dir / "sample.py").unlink()
    second_manifest = second_ref.manifest_path
    second_manifest.write_text(
        second_manifest.read_text(encoding="utf-8").replace(
            'name = "read_contacts"', 'name = "read_files"', 1,
        ),
        encoding="utf-8",
    )
    (second_ref.manifest_dir / "manifest.toml.sig").write_bytes(
        sign_manifest_bytes(second_manifest.read_bytes(), private_key=private),
    )
    colliding = replace(
        second_ref,
        name="read_files",
        manifest_hash=(
            "sha256:" + hashlib.sha256(second_manifest.read_bytes()).hexdigest()
        ),
    )

    with pytest.raises(ContractStoreError, match="published_name_collision"):
        publish_signed_source(
            colliding,
            expected_generation_id=None,
            trusted_publics=trusted,
            store_root=store,
        )

    assert not (store / contract_storage_key(colliding.contract_id)).exists()


def test_retirement_keeps_name_reserved_for_its_contract_identity(
    tmp_path: Path,
) -> None:
    root, first_ref, private, trusted = _create_source(tmp_path)
    second_ref = _add_source_contract(
        root,
        directory_name="second",
        name="second_name",
        private=private,
    )
    store = tmp_path / "store"
    initial = publish_signed_source(
        first_ref,
        expected_generation_id=None,
        trusted_publics=trusted,
        store_root=store,
    )
    retire(
        first_ref,
        expected_generation_id=initial.current_generation_id,
        actor="operator",
        reason="remove the executor without reassigning its identity",
        private_key=private,
        trusted_publics=trusted,
        audit_sink=lambda _event: None,
        store_root=store,
    )

    second_manifest = second_ref.manifest_path
    second_manifest.write_text(
        second_manifest.read_text(encoding="utf-8").replace(
            'name = "second_name"', 'name = "read_files"', 1,
        ),
        encoding="utf-8",
    )
    (second_ref.manifest_dir / "manifest.toml.sig").write_bytes(
        sign_manifest_bytes(second_manifest.read_bytes(), private_key=private),
    )
    colliding = replace(
        second_ref,
        name="read_files",
        manifest_hash=(
            "sha256:" + hashlib.sha256(second_manifest.read_bytes()).hexdigest()
        ),
    )

    with pytest.raises(ContractStoreError) as caught:
        publish_signed_source(
            colliding,
            expected_generation_id=None,
            trusted_publics=trusted,
            store_root=store,
        )

    assert caught.value.code == "published_name_collision"
    assert not (store / contract_storage_key(colliding.contract_id)).exists()


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
    refreshed = _inventory_ref(root, name="read_files")

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
    assert snapshot.parsed["name"] == "read_files"


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
    real_current = contract_store_module.current_contract

    def superseded(*args, **kwargs):
        snapshot = real_current(*args, **kwargs)
        return replace(snapshot, generation_id="sha256:" + "f" * 64)

    monkeypatch.setattr(contract_store_module, "current_contract", superseded)
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


@pytest.mark.skipif(
    "fork" not in multiprocessing.get_all_start_methods(),
    reason="requires POSIX fork inheritance",
)
def test_catalog_lock_bookkeeping_is_reset_after_fork(tmp_path: Path) -> None:
    store = tmp_path / "store"
    context = multiprocessing.get_context("fork")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(
        target=_try_catalog_lock_after_fork,
        args=(str(store), send, True),
    )

    with contract_store_module.catalog_admission_lock(
        store_root=store, timeout=0.2,
    ):
        process.start()
        send.close()
        assert receive.poll(2.0)
        assert receive.recv() == "catalog_lock_timeout"
    process.join(3.0)
    if process.is_alive():
        process.terminate()
        process.join(2.0)
    assert process.exitcode == 0


@pytest.mark.skipif(
    "fork" not in multiprocessing.get_all_start_methods(),
    reason="requires POSIX fork inheritance",
)
@pytest.mark.parametrize(
    "held_exclusive, expected",
    [(False, "acquired"), (True, "catalog_lock_timeout")],
)
def test_a_catalog_reader_waits_for_a_writer_but_not_for_another_reader(
    tmp_path: Path, held_exclusive: bool, expected: str,
) -> None:
    """Two processes may read the catalog together; a publication excludes both.

    Authenticating the whole store is seconds of work.  While readers
    excluded each other, a turn that only needed the executor names expired
    waiting for a periodic audit that was reading the same thing, and the
    turn failed with an unavailable dependency.
    """
    store = tmp_path / "store"
    context = multiprocessing.get_context("fork")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(
        target=_try_catalog_lock_after_fork,
        args=(str(store), send, False),
    )

    with contract_store_module.catalog_admission_lock(
        store_root=store, exclusive=held_exclusive, timeout=0.2,
    ):
        process.start()
        send.close()
        assert receive.poll(2.0)
        assert receive.recv() == expected
    process.join(3.0)
    if process.is_alive():
        process.terminate()
        process.join(2.0)
    assert process.exitcode == 0


def test_a_catalog_reader_is_never_upgraded_to_a_writer(tmp_path: Path) -> None:
    """A reader reentering as a writer would deadlock; it is refused instead.

    The other direction is legitimate and stays allowed: a transition holds
    the writer and may read the catalog it is about to change.
    """
    store = tmp_path / "store"
    with contract_store_module.catalog_admission_lock(
        store_root=store, exclusive=False,
    ):
        with pytest.raises(ContractStoreError, match="reentrant lock upgrade"):
            with contract_store_module.catalog_admission_lock(store_root=store):
                pass

    with contract_store_module.catalog_admission_lock(store_root=store):
        with contract_store_module.catalog_admission_lock(
            store_root=store, exclusive=False,
        ):
            pass


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
        name="read_files", code_file="../../shared.py", code_digest=digest,
        placement_scope="server",
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
        name="read_files",
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
        name="read_files", code_file="../../../outside.py", code_digest=outside_digest,
        placement_scope="server",
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
        name="read_files",
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
        name="read_files", code_file="shared.py", code_digest=digest,
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
        name="read_files",
        origin=ManifestOrigin.CORE,
        allowed_code_roots=(executors, runtime),
    )
    assert verify_manifest_source(allowed, trusted_publics=trusted).verified_code_digest == digest

    denied = _inventory_ref(
        executors,
        name="read_files",
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


def _localization_patch(
    snapshot,
    *,
    selector: str,
    source_language: str,
    target_language: str,
    candidate: str,
) -> LocalizationPatch:
    table = manifest_language_selectors(snapshot.parsed)[selector]
    source = table[source_language]
    previous = table.get(target_language)
    return LocalizationPatch(
        selector=selector,
        source_hash=_hash_text(source),
        previous_target_hash=(None if previous is None else _hash_text(previous)),
        candidate_text=candidate,
        candidate_hash=_hash_text(candidate),
    )


def _rewrite_authoring_state(ref: ManifestRef) -> ManifestRef:
    parsed = tomllib.loads(ref.manifest_path.read_text(encoding="utf-8"))
    (ref.manifest_dir / "manifest.lang_state.json").write_bytes(
        encode_language_state(_state_for(parsed), manifest=parsed)
    )
    return _inventory_ref(Path(ref.source_root), name=str(parsed["name"]))


def test_localization_publication_is_atomic_idempotent_and_does_not_mirror(
    tmp_path: Path,
) -> None:
    _root, ref, private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    initial = publish_signed_source(
        ref,
        expected_generation_id=None,
        trusted_publics=trusted,
        store_root=store,
    )
    before_authoring = _source_payloads(ref)
    base = current_manifest(ref, trusted_publics=trusted, store_root=store)
    candidate = (
        "SCOPO: test safely. PATTERN: sample(). "
        "NON: modify. OUT: results=[]."
    )
    patch = _localization_patch(
        base,
        selector="description",
        source_language="it",
        target_language="en",
        candidate=candidate,
    )

    published = publish_localization(
        ref,
        expected_generation_id=initial.current_generation_id,
        source_language="it",
        target_language="en",
        patches=(patch,),
        private_key=private,
        trusted_publics=trusted,
        store_root=store,
    )
    repeated = publish_localization(
        ref,
        expected_generation_id=initial.current_generation_id,
        source_language="it",
        target_language="en",
        patches=(patch,),
        private_key=private,
        trusted_publics=trusted,
        store_root=store,
    )

    assert not published.repeated
    assert repeated.repeated
    assert repeated.current_generation_id == published.current_generation_id
    current = current_manifest(ref, trusted_publics=trusted, store_root=store)
    assert current.parsed["description"]["en"] == candidate
    assert current.parsed["description"]["it"] == base.parsed["description"]["it"]
    assert current.parsed["args"] == base.parsed["args"]
    assert current.declared_code_digest == base.declared_code_digest
    assert current.language_state["selectors"]["description"]["en"] == {
        "version_hash": _hash_text(candidate),
        "source_lang": "it",
        "source_hash": patch.source_hash,
    }
    assert _source_payloads(ref) == before_authoring


@pytest.mark.parametrize(
    ("field", "expected_code"),
    (
        ("source_hash", "localization_source_changed"),
        ("previous_target_hash", "localization_target_changed"),
        ("candidate_hash", "localization_candidate_hash"),
    ),
)
def test_localization_hash_mismatch_is_precommit(
    tmp_path: Path,
    field: str,
    expected_code: str,
) -> None:
    _root, ref, private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    initial = publish_signed_source(
        ref, expected_generation_id=None,
        trusted_publics=trusted, store_root=store,
    )
    base = current_manifest(ref, trusted_publics=trusted, store_root=store)
    candidate = "SCOPO: safe test. PATTERN: sample(). NON: modify. OUT: results=[]."
    patch = _localization_patch(
        base, selector="description", source_language="it",
        target_language="en", candidate=candidate,
    )
    patch = replace(patch, **{field: "sha256:" + "f" * 64})

    with pytest.raises(ContractStoreError, match=expected_code):
        publish_localization(
            ref,
            expected_generation_id=initial.current_generation_id,
            source_language="it",
            target_language="en",
            patches=(patch,),
            private_key=private,
            trusted_publics=trusted,
            store_root=store,
        )
    assert current_manifest(
        ref, trusted_publics=trusted, store_root=store,
    ).generation_id == initial.current_generation_id


def test_localization_rejects_unknown_duplicate_and_lint_invalid_patches(
    tmp_path: Path,
) -> None:
    _root, ref, private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    initial = publish_signed_source(
        ref, expected_generation_id=None,
        trusted_publics=trusted, store_root=store,
    )
    base = current_manifest(ref, trusted_publics=trusted, store_root=store)
    valid = _localization_patch(
        base, selector="description", source_language="it",
        target_language="en",
        candidate="SCOPO: safe. PATTERN: sample(). NON: modify. OUT: results=[].",
    )
    unknown = replace(valid, selector="args.properties.missing.description")
    for patches, error in (
        ((unknown,), "localization_selector_missing"),
        ((valid, valid), "localization_patch_duplicate"),
    ):
        with pytest.raises(ContractStoreError, match=error):
            publish_localization(
                ref,
                expected_generation_id=initial.current_generation_id,
                source_language="it",
                target_language="en",
                patches=patches,
                private_key=private,
                trusted_publics=trusted,
                store_root=store,
            )

    invalid = _localization_patch(
        base, selector="description", source_language="it",
        target_language="en", candidate="This removes every machine atom.",
    )
    with pytest.raises(ContractStoreError, match="contract_language_invalid"):
        publish_localization(
            ref,
            expected_generation_id=initial.current_generation_id,
            source_language="it",
            target_language="en",
            patches=(invalid,),
            private_key=private,
            trusted_publics=trusted,
            store_root=store,
        )
    assert current_manifest(
        ref, trusted_publics=trusted, store_root=store,
    ).generation_id == initial.current_generation_id


def test_new_language_requires_complete_surface_coverage(tmp_path: Path) -> None:
    _root, ref, private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    initial = publish_signed_source(
        ref, expected_generation_id=None,
        trusted_publics=trusted, store_root=store,
    )
    base = current_manifest(ref, trusted_publics=trusted, store_root=store)
    only_top = _localization_patch(
        base, selector="description", source_language="it", target_language="fr",
        candidate="SCOPO: test. PATTERN: sample(). NON: modifier. OUT: results=[].",
    )

    with pytest.raises(ContractStoreError, match="language_coverage_incomplete"):
        publish_localization(
            ref,
            expected_generation_id=initial.current_generation_id,
            source_language="it",
            target_language="fr",
            patches=(only_top,),
            private_key=private,
            trusted_publics=trusted,
            store_root=store,
        )


def test_stale_localization_candidate_cannot_overwrite_a_newer_publication(
    tmp_path: Path,
) -> None:
    _root, ref, private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    initial = publish_signed_source(
        ref, expected_generation_id=None,
        trusted_publics=trusted, store_root=store,
    )
    base = current_manifest(ref, trusted_publics=trusted, store_root=store)
    first = _localization_patch(
        base, selector="description", source_language="it", target_language="en",
        candidate="SCOPO: first. PATTERN: sample(). NON: modify. OUT: results=[].",
    )
    stale = _localization_patch(
        base, selector="description", source_language="it", target_language="en",
        candidate="SCOPO: stale. PATTERN: sample(). NON: modify. OUT: results=[].",
    )
    winner = publish_localization(
        ref,
        expected_generation_id=initial.current_generation_id,
        source_language="it",
        target_language="en",
        patches=(first,),
        private_key=private,
        trusted_publics=trusted,
        store_root=store,
    )

    with pytest.raises(ContractStoreError, match="commit_conflict"):
        publish_localization(
            ref,
            expected_generation_id=initial.current_generation_id,
            source_language="it",
            target_language="en",
            patches=(stale,),
            private_key=private,
            trusted_publics=trusted,
            store_root=store,
        )
    assert current_manifest(
        ref, trusted_publics=trusted, store_root=store,
    ).generation_id == winner.current_generation_id


def test_localization_is_inactive_without_an_explicit_isolated_store(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _root, ref, private, trusted = _create_source(tmp_path)
    module_root = Path(contract_store_module.__file__).resolve().parents[1]
    monkeypatch.setenv("METNOS_INSTALL_ROOT", str(module_root))
    monkeypatch.setattr(contract_store_module._C, "PATH_ROOT", module_root)
    patch = LocalizationPatch(
        selector="description",
        source_hash="sha256:" + "0" * 64,
        previous_target_hash=None,
        candidate_text="candidate",
        candidate_hash=_hash_text("candidate"),
    )
    with pytest.raises(ContractStoreError, match="publication_not_active"):
        publish_localization(
            ref,
            expected_generation_id="sha256:" + "1" * 64,
            source_language="it",
            target_language="en",
            patches=(patch,),
            private_key=private,
            trusted_publics=trusted,
        )


def test_localization_shares_the_catalog_writer_boundary(tmp_path: Path) -> None:
    _root, ref, private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    initial = publish_signed_source(
        ref,
        expected_generation_id=None,
        trusted_publics=trusted,
        store_root=store,
    )
    base = current_manifest(ref, trusted_publics=trusted, store_root=store)
    patch = _localization_patch(
        base,
        selector="description",
        source_language="it",
        target_language="en",
        candidate=(
            "SCOPO: translated safely. PATTERN: sample(). "
            "NON: modify. OUT: results=[]."
        ),
    )
    outcome: list[str] = []

    def publish_while_catalog_is_excluded() -> None:
        try:
            publish_localization(
                ref,
                expected_generation_id=initial.current_generation_id,
                source_language="it",
                target_language="en",
                patches=(patch,),
                private_key=private,
                trusted_publics=trusted,
                store_root=store,
                lock_timeout=0.05,
            )
        except ContractStoreError as exc:
            outcome.append(exc.code)

    with contract_store_module.catalog_admission_lock(
        store_root=store, timeout=0.2,
    ):
        worker = threading.Thread(target=publish_while_catalog_is_excluded)
        worker.start()
        worker.join(1.0)

    assert not worker.is_alive()
    assert outcome == ["catalog_lock_timeout"]
    assert current_manifest(
        ref, trusted_publics=trusted, store_root=store,
    ).generation_id == initial.current_generation_id


def test_technical_publication_updates_code_without_regressing_localization(
    tmp_path: Path,
) -> None:
    _root, ref, private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    initial = publish_signed_source(
        ref, expected_generation_id=None,
        trusted_publics=trusted, store_root=store,
    )
    before = current_manifest(ref, trusted_publics=trusted, store_root=store)
    code = ref.manifest_dir / "sample.py"
    code.write_text(
        "def invoke(args):\n    return {'results': [], 'revision': 2}\n",
        encoding="utf-8",
    )
    authoring_before = _source_payloads(ref)
    draft = prepare_technical_draft(ref)

    published = publish_technical_update(
        ref,
        expected_generation_id=initial.current_generation_id,
        draft=draft,
        private_key=private,
        trusted_publics=trusted,
        store_root=store,
    )
    repeated = publish_technical_update(
        ref,
        expected_generation_id=initial.current_generation_id,
        draft=draft,
        private_key=private,
        trusted_publics=trusted,
        store_root=store,
    )

    assert not published.repeated
    assert repeated.repeated
    current = current_manifest(ref, trusted_publics=trusted, store_root=store)
    assert current.declared_code_digest == draft.authoring_code_digest
    assert current.verified_code_digest == draft.authoring_code_digest
    assert current.parsed["description"] == before.parsed["description"]
    assert current.language_state == before.language_state
    assert _source_payloads(ref) == authoring_before


_BIRTH_DIGEST = "sha256:" + ("7" * 64)


def _birth_digest(character: str) -> str:
    return "sha256:" + character * 64


def _v2_request(
    ref: ManifestRef, generation_id: str, *, context: str, transition: str,
):
    from executor_birth_producer_context import ProducerRequestV2, _REQUEST_SEAL

    return ProducerRequestV2(
        _birth_digest("1"), _birth_digest("2"), ref.contract_id.value,
        generation_id, context, transition, _birth_digest("3"), "4" * 64,
        _birth_digest("5"), _REQUEST_SEAL,
    )


def _v2_reattestation_receipt(
    ref: ManifestRef, generation_id: str, request, private_key,
) -> bytes:
    return issue_admission_receipt(
        policy_version="birth-policy-v1",
        contract_id=ref.contract_id,
        generation_id=generation_id,
        candidate_id=_birth_digest("6"),
        semantic_core_id=_birth_digest("7"),
        admission_context_id=request.admission_context_id,
        birth_request_id=request.request_id,
        authoring_journal_hash=_birth_digest("8"),
        predecessor_id=generation_id,
        producer_receipt_hash=_birth_digest("9"),
        revision_class=RevisionClass.REATTESTATION,
        check_results={
            "reattestation_current_generation_v1": AdmissionCheck(
                "1", AdmittedCheckStatus.PASSED, _birth_digest("a"),
            ),
        },
        semantic_review_hash=None,
        approval_hash=None,
        approved_lifecycle=ApprovedLifecycle.ACTIVE,
        kind=AdmissionKind.REATTESTATION,
        issued_at="2026-09-07T12:00:00Z",
        key_id="birth-test-key",
        private_key=private_key,
    )


def _persist_v2_test_receipt(ref, generation_id, request, private, trusted, store):
    encoded = _v2_reattestation_receipt(ref, generation_id, request, private)
    authorization = BirthCommitAuthorization(
        _birth_digest("6"), _birth_digest("7"),
        request.admission_context_id, generation_id,
        lambda *_args: encoded,
        lambda wire: verify_admission_receipt(
            wire, public_key=private.public_key(),
            expected_key_id="birth-test-key",
        ),
    )
    return persist_current_reattestation_receipt_v2(
        ref, encoded, request=request, authorization=authorization,
        verifier=authorization.verifier,
        expected_bindings={
            "contract_id": ref.contract_id.value,
            "generation_id": generation_id,
            "admission_context_id": request.admission_context_id,
            "birth_request_id": request.request_id,
        },
        trusted_publics=trusted, store_root=store,
    )


def test_v2_reattestation_receipts_from_two_contexts_coexist(tmp_path: Path) -> None:
    _root, ref, _private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    initial = publish_signed_source(
        ref, expected_generation_id=None,
        trusted_publics=trusted, store_root=store,
    )
    generation_id = initial.current_generation_id
    first = _v2_request(
        ref, generation_id, context=_birth_digest("b"),
        transition=_birth_digest("c"),
    )
    second = _v2_request(
        ref, generation_id, context=_birth_digest("d"),
        transition=_birth_digest("e"),
    )
    admission_private = Ed25519PrivateKey.generate()

    def persist(request) -> bytes:
        return _persist_v2_test_receipt(
            ref, generation_id, request, admission_private, trusted, store,
        )

    first_wire = persist(first)
    assert read_current_birth_receipt_v2(
        ref, request=first, trusted_publics=trusted, store_root=store,
    ) == first_wire
    assert read_current_birth_receipt_v2(
        ref, request=second, trusted_publics=trusted, store_root=store,
    ) is None

    second_wire = persist(second)
    assert second_wire != first_wire
    assert read_current_birth_receipt_v2(
        ref, request=first, trusted_publics=trusted, store_root=store,
    ) == first_wire
    assert read_current_birth_receipt_v2(
        ref, request=second, trusted_publics=trusted, store_root=store,
    ) == second_wire
    assert len(tuple(store.rglob("admission-receipts-v2/**/*.json"))) == 2

    # A legitimate reattestation namespace is not abandoned publication staging.
    # Exercise both the writer and the read-only diagnostic after V2 persists.
    for request, wire in ((first, first_wire), (second, second_wire)):
        repeated = publish_signed_source(
            ref, expected_generation_id=generation_id,
            trusted_publics=trusted, store_root=store,
        )
        assert repeated.repeated and repeated.current_generation_id == generation_id
        assert read_current_birth_receipt_v2(
            ref, request=request, trusted_publics=trusted, store_root=store,
        ) == wire
    assert diagnose_store((ref,), trusted_publics=trusted, store_root=store) == ()


def test_technical_publication_preserves_existing_v2_receipts(tmp_path: Path) -> None:
    _root, ref, private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    initial = publish_signed_source(
        ref, expected_generation_id=None, trusted_publics=trusted, store_root=store,
    )
    request = _v2_request(
        ref, initial.current_generation_id,
        context=_birth_digest("b"), transition=_birth_digest("c"),
    )
    wire = _persist_v2_test_receipt(
        ref, initial.current_generation_id, request,
        Ed25519PrivateKey.generate(), trusted, store,
    )
    receipt_path, = store.rglob("admission-receipts-v2/**/*.json")
    before = (receipt_path.stat().st_ino, receipt_path.stat().st_mtime_ns, wire)
    (ref.manifest_dir / "sample.py").write_text(
        "def invoke(args):\n    return {'results': [], 'revision': 2}\n",
        encoding="utf-8",
    )
    published = publish_technical_update(
        ref, expected_generation_id=initial.current_generation_id,
        draft=prepare_technical_draft(ref), private_key=private,
        trusted_publics=trusted, store_root=store,
    )
    assert published.current_generation_id != initial.current_generation_id
    assert current_manifest(
        ref, trusted_publics=trusted, store_root=store,
    ).generation_id == published.current_generation_id
    assert before == (
        receipt_path.stat().st_ino, receipt_path.stat().st_mtime_ns,
        receipt_path.read_bytes(),
    )
    assert "contract_entry_unknown" not in {
        item.code for item in diagnose_store((ref,), trusted_publics=trusted, store_root=store)
    }


@pytest.mark.parametrize("receipt_versions", ((1,), (2,), (1, 2)))
def test_activation_preserves_supported_receipt_namespaces(
    tmp_path: Path, monkeypatch, receipt_versions: tuple[int, ...],
) -> None:
    _root, ref, private, trusted, shadow, initial = _create_productive_shadow(
        tmp_path, monkeypatch,
    )
    generation = initial.current_generation_id
    admission_private = Ed25519PrivateKey.generate()
    if 1 in receipt_versions:
        generation = commit_birth_snapshot(
            ref, expected_generation_id=generation,
            snapshot=_birth_snapshot(ref, tmp_path),
            request_id=_birth_digest("8"), private_key=private,
            trusted_publics=trusted, store_root=shadow,
            birth_authorization=_birth_authorization(ref, generation, admission_private),
        ).current_generation_id
    if 2 in receipt_versions:
        request = _v2_request(
            ref, generation, context=_birth_digest("b"), transition=_birth_digest("c"),
        )
        _persist_v2_test_receipt(
            ref, generation, request, admission_private, trusted, shadow,
        )
    before = {path.relative_to(shadow): path.read_bytes()
              for path in shadow.rglob("*.json") if "admission-receipts" in str(path)}
    assert len(before) == len(receipt_versions)
    assert {item.code for item in diagnose_store(
        (ref,), trusted_publics=trusted, store_root=shadow,
    )} <= {"generation_orphan"}
    expected = {ref.contract_id: generation}
    activate_store(
        expected, shadow_root=shadow, trusted_publics=trusted,
        quiescence_guard=lambda: True,
    )
    productive = contract_store_module._C.PATH_USER_STATE / "contract-publications" / "v1"
    assert before == {path: (productive / path).read_bytes() for path in before}
    assert {item.code for item in diagnose_store(
        (ref,), trusted_publics=trusted, store_root=productive,
    )} <= {"generation_orphan"}
    # Also exercise verification of an already activated store, not just shadow.
    activate_store(
        expected, shadow_root=shadow, trusted_publics=trusted,
        quiescence_guard=lambda: True,
    )
    assert before == {path: (productive / path).read_bytes() for path in before}


@pytest.mark.parametrize("entry_kind", ("file", "link", "unknown"))
def test_invalid_v2_or_unknown_namespace_fails_all_shape_checks(
    tmp_path: Path, monkeypatch, entry_kind: str,
) -> None:
    _root, ref, _private, trusted, shadow, initial = _create_productive_shadow(
        tmp_path, monkeypatch,
    )
    contract_dir = shadow / contract_storage_key(ref.contract_id)
    entry = contract_dir / (
        "foreign-receipts" if entry_kind == "unknown" else "admission-receipts-v2"
    )
    if entry_kind == "file":
        entry.write_bytes(b"not a receipt directory")
    elif entry_kind == "link":
        target = tmp_path / "outside-receipts"
        target.mkdir()
        (target / "keep").write_bytes(b"must not be touched")
        try:
            entry.symlink_to(target, target_is_directory=True)
        except OSError:
            pytest.skip("symlinks unavailable")
    else:
        entry.mkdir()
    valid_staging = contract_dir / ".binding.json.1.2.3.tmp"
    binding = encode_binding(ref.contract_id)
    valid_staging.write_bytes(binding)
    with pytest.raises(ContractStoreError, match="staging_invalid"):
        publish_signed_source(
            ref, expected_generation_id=initial.current_generation_id,
            trusted_publics=trusted, store_root=shadow,
        )
    assert valid_staging.read_bytes() == binding
    # Remove only this test's valid temporary file to test the activation
    # shape independently from its earlier staging-recovery guard.
    valid_staging.unlink()
    with pytest.raises(ContractStoreError, match="activation_contract_invalid"):
        contract_store_module._verify_activation_catalog(
            shadow, {ref.contract_id: initial.current_generation_id},
            trusted_publics=trusted,
        )
    expected_code = "contract_entry_unknown" if entry_kind == "unknown" else "birth_receipt_store_invalid"
    assert expected_code in {
        item.code for item in diagnose_store((ref,), trusted_publics=trusted, store_root=shadow)
    }
    assert os.path.lexists(entry)
    if entry_kind == "link":
        assert entry.is_symlink() and (target / "keep").read_bytes() == b"must not be touched"
    elif entry_kind == "file":
        assert entry.read_bytes() == b"not a receipt directory"


def _birth_authorization(
    ref: ManifestRef,
    predecessor_id: str | None,
    admission_private: Ed25519PrivateKey,
    *,
    observed: list[tuple[str, Mapping[str, str]]] | None = None,
) -> BirthCommitAuthorization:
    public = admission_private.public_key()

    def issue(
        identifier: str, payload_hashes: Mapping[str, str],
        _request_id: str, journal_hash: str,
    ) -> bytes:
        if observed is not None:
            observed.append((identifier, dict(payload_hashes)))
        return issue_admission_receipt(
            policy_version="birth-policy-v1",
            contract_id=ref.contract_id,
            generation_id=identifier,
            candidate_id=_BIRTH_DIGEST,
            semantic_core_id=_BIRTH_DIGEST,
            admission_context_id=_BIRTH_DIGEST,
            birth_request_id=_request_id,
            authoring_journal_hash=journal_hash,
            predecessor_id=predecessor_id,
            producer_receipt_hash=_BIRTH_DIGEST,
            revision_class=RevisionClass.CODE_REVISION,
            check_results={
                "authoring_install_journal_v1": AdmissionCheck(
                    rule_version="1", status=AdmittedCheckStatus.PASSED,
                    evidence_hash=journal_hash,
                ),
            },
            semantic_review_hash=None,
            approval_hash=None,
            approved_lifecycle=ApprovedLifecycle.ACTIVE,
            kind=AdmissionKind.ADMISSION,
            issued_at="2026-08-25T12:00:00Z",
            key_id="birth-test-key",
            private_key=admission_private,
        )

    return BirthCommitAuthorization(
        candidate_id=_BIRTH_DIGEST,
        semantic_core_id=_BIRTH_DIGEST,
        admission_context_id=_BIRTH_DIGEST,
        predecessor_id=predecessor_id,
        issuer=issue,
        verifier=lambda encoded: verify_admission_receipt(
            encoded, public_key=public, expected_key_id="birth-test-key",
        ),
    )


def _birth_draft(ref: ManifestRef) -> TechnicalDraft:
    (ref.manifest_dir / "sample.py").write_text(
        "def invoke(args):\n    return {'results': [], 'birth': 2}\n",
        encoding="utf-8",
    )
    return prepare_technical_draft(ref)


def _birth_snapshot(ref: ManifestRef, tmp_path: Path):
    _birth_draft(ref)
    document = tomlkit.parse((ref.manifest_dir / "manifest.toml").read_text())
    code_bytes = (ref.manifest_dir / "sample.py").read_bytes()
    document["code"]["digest"] = "sha256:" + hashlib.sha256(code_bytes).hexdigest()
    (ref.manifest_dir / "manifest.toml").write_text(tomlkit.dumps(document))
    source = tmp_path / "birth-candidate"
    source.mkdir()
    parsed = tomllib.loads((ref.manifest_dir / "manifest.toml").read_text())
    names = ("manifest.toml", "manifest.lang_state.json", *parsed["code"]["files"])
    for name in names:
        destination = source / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ref.manifest_dir / name).read_bytes())
    return acquire_candidate_snapshot(source, private_parent=tmp_path)


def test_birth_receipt_is_durable_and_reread_before_pointer(tmp_path: Path) -> None:
    _root, ref, private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    initial = publish_signed_source(
        ref, expected_generation_id=None,
        trusted_publics=trusted, store_root=store,
    )
    snapshot = _birth_snapshot(ref, tmp_path)
    observed: list[tuple[str, Mapping[str, str]]] = []
    admission_private = Ed25519PrivateKey.generate()
    authorization = _birth_authorization(
        ref, initial.current_generation_id, admission_private, observed=observed,
    )

    result = commit_birth_snapshot(
        ref,
        expected_generation_id=initial.current_generation_id,
        snapshot=snapshot,
        request_id="sha256:" + "8" * 64,
        private_key=private,
        trusted_publics=trusted,
        birth_authorization=authorization,
        store_root=store,
    )

    assert len(observed) == 1
    desired, payload_hashes = observed[0]
    assert desired == result.current_generation_id
    assert tuple(payload_hashes) == GENERATION_FILES
    receipt_files = tuple(store.rglob("admission-receipts/*.json"))
    assert len(receipt_files) == 1
    receipt = verify_admission_receipt(
        receipt_files[0].read_bytes(),
        public_key=admission_private.public_key(),
        expected_key_id="birth-test-key",
    )
    assert receipt.generation_id == current_revision_id(ref, store_root=store)


def _execution_binding_fixture(tmp_path, monkeypatch):
    _root, ref, private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    predecessor = publish_signed_source(
        ref, expected_generation_id=None,
        trusted_publics=trusted, store_root=store,
    ).current_generation_id
    snapshot = _birth_snapshot(ref, tmp_path)
    admission_private = Ed25519PrivateKey.generate()
    authorization = _birth_authorization(
        ref, predecessor, admission_private,
    )
    publication = commit_birth_snapshot(
        ref, expected_generation_id=predecessor, snapshot=snapshot,
        request_id="sha256:" + "8" * 64, private_key=private,
        trusted_publics=trusted, birth_authorization=authorization,
        store_root=store,
    )
    monkeypatch.setattr(
        manifest_inventory_module, "inventory_authoring_manifests",
        lambda: ManifestInventory((ref,), ()),
    )
    return ref, store, trusted, admission_private, publication.current_generation_id


def test_execution_binding_authenticates_exact_generation_and_receipt(
    tmp_path: Path, monkeypatch,
) -> None:
    ref, store, trusted, admission_private, generation = (
        _execution_binding_fixture(tmp_path, monkeypatch)
    )
    binding = authenticate_execution_binding(
        ref.contract_id, generation, trusted_publics=trusted,
        admission_verifier_keys={"birth-test-key": admission_private.public_key()},
        store_root=store,
    )
    assert binding.contract_id == ref.contract_id
    assert binding.generation_id == generation
    assert binding.executor_name == "read_files"
    assert binding.candidate_id == _BIRTH_DIGEST


def test_execution_binding_rejects_wrong_or_tampered_generation(
    tmp_path: Path, monkeypatch,
) -> None:
    ref, store, trusted, admission_private, generation = (
        _execution_binding_fixture(tmp_path, monkeypatch)
    )
    with pytest.raises(ContractStoreError, match="execution_generation_stale"):
        authenticate_execution_binding(
            ref.contract_id, "sha256:" + "f" * 64, trusted_publics=trusted,
            admission_verifier_keys={"birth-test-key": admission_private.public_key()},
            store_root=store,
        )
    generation_path = (
        store / contract_storage_key(ref.contract_id) / "generations"
        / generation_directory_name(generation) / "manifest.toml"
    )
    generation_path.write_bytes(generation_path.read_bytes() + b"\n")
    with pytest.raises(ContractStoreError):
        authenticate_execution_binding(
            ref.contract_id, generation, trusted_publics=trusted,
            admission_verifier_keys={"birth-test-key": admission_private.public_key()},
            store_root=store,
        )


def test_execution_binding_rejects_receipt_race(
    tmp_path: Path, monkeypatch,
) -> None:
    ref, store, trusted, admission_private, generation = (
        _execution_binding_fixture(tmp_path, monkeypatch)
    )
    receipt_path = (
        store / contract_storage_key(ref.contract_id) / "admission-receipts"
        / (generation_directory_name(generation) + ".json")
    )
    original_read = contract_store_module._read_regular_file
    receipt_reads = {"count": 0}

    def racing_read(path, *, code):
        if Path(path) == receipt_path:
            receipt_reads["count"] += 1
            if receipt_reads["count"] == 2:
                receipt_path.write_bytes(b"{}")
        return original_read(path, code=code)

    monkeypatch.setattr(contract_store_module, "_read_regular_file", racing_read)
    with pytest.raises(ContractStoreError, match="birth_receipt_reread_mismatch"):
        authenticate_execution_binding(
            ref.contract_id, generation, trusted_publics=trusted,
            admission_verifier_keys={"birth-test-key": admission_private.public_key()},
            store_root=store,
        )


def test_birth_commit_rejects_changed_context_epoch(tmp_path: Path) -> None:
    _root, ref, private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    initial = publish_signed_source(
        ref, expected_generation_id=None, trusted_publics=trusted, store_root=store,
    )
    snapshot = _birth_snapshot(ref, tmp_path)
    base = _birth_authorization(
        ref, initial.current_generation_id, Ed25519PrivateKey.generate(),
    )
    epoch = {"value": "sha256:" + "1" * 64}
    authorization = BirthCommitAuthorization(
        base.candidate_id, base.semantic_core_id, base.admission_context_id,
        base.predecessor_id, base.issuer, base.verifier,
        context_epoch="sha256:" + "0" * 64,
        context_epoch_resolver=lambda: epoch["value"],
    )

    with pytest.raises(ContractStoreError, match="birth_context_changed"):
        commit_birth_snapshot(
            ref, expected_generation_id=initial.current_generation_id,
            snapshot=snapshot, request_id="sha256:" + "2" * 64,
            private_key=private, trusted_publics=trusted,
            birth_authorization=authorization, store_root=store,
        )
    assert current_revision_id(ref, store_root=store) == initial.current_generation_id


def test_birth_commit_rejects_changed_authenticated_predecessor(tmp_path: Path) -> None:
    from executor_birth_predecessor import predecessor_snapshot

    _root, ref, private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    initial = publish_signed_source(
        ref, expected_generation_id=None, trusted_publics=trusted, store_root=store,
    )
    snapshot = _birth_snapshot(ref, tmp_path)
    base = _birth_authorization(
        ref, initial.current_generation_id, Ed25519PrivateKey.generate(),
    )
    stale = predecessor_snapshot(
        initial.current_generation_id, "generation",
        {"manifest.toml": b"authenticated-old-revision"},
    )
    authorization = BirthCommitAuthorization(
        base.candidate_id, base.semantic_core_id, base.admission_context_id,
        base.predecessor_id, base.issuer, base.verifier,
        predecessor_snapshot_id=stale.snapshot_id,
    )

    with pytest.raises(ContractStoreError, match="birth_predecessor_changed"):
        commit_birth_snapshot(
            ref, expected_generation_id=initial.current_generation_id,
            snapshot=snapshot, request_id="sha256:" + "a" * 64,
            private_key=private, trusted_publics=trusted,
            birth_authorization=authorization, store_root=store,
        )


def test_birth_crash_after_receipt_before_generation_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _root, ref, private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    initial = publish_signed_source(
        ref, expected_generation_id=None,
        trusted_publics=trusted, store_root=store,
    )
    snapshot = _birth_snapshot(ref, tmp_path)
    observed: list[tuple[str, Mapping[str, str]]] = []
    authorization = _birth_authorization(
        ref, initial.current_generation_id, Ed25519PrivateKey.generate(),
        observed=observed,
    )
    original_install = contract_store_module._install_generation

    def crash(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected crash after receipt")

    monkeypatch.setattr(contract_store_module, "_install_generation", crash)
    with pytest.raises(RuntimeError, match="injected crash"):
        commit_birth_snapshot(
            ref, expected_generation_id=initial.current_generation_id,
            snapshot=snapshot, request_id="sha256:" + "9" * 64,
            private_key=private, trusted_publics=trusted,
            birth_authorization=authorization, store_root=store,
        )
    assert current_revision_id(ref, store_root=store) == initial.current_generation_id
    assert len(tuple(store.rglob("admission-receipts/*.json"))) == 1

    monkeypatch.setattr(contract_store_module, "_install_generation", original_install)
    result = commit_birth_snapshot(
        ref, expected_generation_id=initial.current_generation_id,
        snapshot=snapshot, request_id="sha256:" + "9" * 64,
        private_key=private, trusted_publics=trusted,
        birth_authorization=authorization, store_root=store,
    )
    assert current_revision_id(ref, store_root=store) == result.current_generation_id
    assert len(observed) == 1


def test_birth_receipt_failure_leaves_pointer_unchanged(tmp_path: Path) -> None:
    _root, ref, private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    initial = publish_signed_source(
        ref, expected_generation_id=None,
        trusted_publics=trusted, store_root=store,
    )
    snapshot = _birth_snapshot(ref, tmp_path)
    good = _birth_authorization(
        ref, initial.current_generation_id, Ed25519PrivateKey.generate(),
    )
    invalid = BirthCommitAuthorization(
        candidate_id=good.candidate_id,
        semantic_core_id=good.semantic_core_id,
        admission_context_id=good.admission_context_id,
        predecessor_id=good.predecessor_id,
        issuer=lambda _identifier, _hashes, _request, _journal: b"not a receipt",
        verifier=good.verifier,
    )

    with pytest.raises(ContractStoreError, match="birth_receipt_invalid"):
        commit_birth_snapshot(
            ref, expected_generation_id=initial.current_generation_id,
            snapshot=snapshot, request_id="sha256:" + "6" * 64,
            private_key=private, trusted_publics=trusted,
            birth_authorization=invalid, store_root=store,
        )
    assert current_revision_id(ref, store_root=store) == initial.current_generation_id
    assert not tuple(store.rglob("admission-receipts/*.json"))


def test_birth_crash_after_pointer_completes_version_on_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import executor_birth_authoring as authoring

    _root, ref, private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    initial = publish_signed_source(
        ref, expected_generation_id=None,
        trusted_publics=trusted, store_root=store,
    )
    snapshot = _birth_snapshot(ref, tmp_path)
    authorization = _birth_authorization(
        ref, initial.current_generation_id, Ed25519PrivateKey.generate(),
    )
    original_advance = authoring.advance_version
    calls = 0

    def crash(*args: object, **kwargs: object):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("injected crash after pointer")
        return original_advance(*args, **kwargs)

    monkeypatch.setattr(authoring, "advance_version", crash)
    request_id = "sha256:" + "5" * 64
    with pytest.raises(RuntimeError, match="injected crash after pointer"):
        commit_birth_snapshot(
            ref, expected_generation_id=initial.current_generation_id,
            snapshot=snapshot, request_id=request_id, private_key=private,
            trusted_publics=trusted, birth_authorization=authorization,
            store_root=store,
        )
    committed = current_revision_id(ref, store_root=store)
    assert committed != initial.current_generation_id

    result = commit_birth_snapshot(
        ref, expected_generation_id=initial.current_generation_id,
        snapshot=snapshot, request_id=request_id, private_key=private,
        trusted_publics=trusted, birth_authorization=authorization,
        store_root=store,
    )
    assert result.repeated
    assert result.current_generation_id == committed


def test_birth_post_cleanup_replay_requires_exact_request_id(tmp_path: Path) -> None:
    _root, ref, private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    initial = publish_signed_source(
        ref, expected_generation_id=None,
        trusted_publics=trusted, store_root=store,
    )
    snapshot = _birth_snapshot(ref, tmp_path)
    authorization = _birth_authorization(
        ref, initial.current_generation_id, Ed25519PrivateKey.generate(),
    )
    request_id = "sha256:" + "4" * 64
    first = commit_birth_snapshot(
        ref, expected_generation_id=initial.current_generation_id,
        snapshot=snapshot, request_id=request_id, private_key=private,
        trusted_publics=trusted, birth_authorization=authorization,
        store_root=store,
    )
    repeated = commit_birth_snapshot(
        ref, expected_generation_id=initial.current_generation_id,
        snapshot=snapshot, request_id=request_id, private_key=private,
        trusted_publics=trusted, birth_authorization=authorization,
        store_root=store,
    )
    assert repeated.repeated
    assert repeated.current_generation_id == first.current_generation_id

    with pytest.raises(ContractStoreError, match="birth_receipt_binding_invalid"):
        commit_birth_snapshot(
            ref, expected_generation_id=initial.current_generation_id,
            snapshot=snapshot, request_id="sha256:" + "3" * 64,
            private_key=private, trusted_publics=trusted,
            birth_authorization=authorization, store_root=store,
        )


_BIRTH_COMMIT_CRASH_BOUNDARIES = (
    "receipt",
    "journal",
    "rename_old",
    "rename_new",
    "generation",
    "current",
    "reread",
    "version",
    "cleanup",
)


def _inject_birth_commit_crash(
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    """Raise once immediately after the named durable F4 operation."""
    import executor_birth_authoring as authoring

    fired = False

    def once_after(original, label: str):
        def wrapped(*args, **kwargs):
            nonlocal fired
            result = original(*args, **kwargs)
            if not fired:
                fired = True
                raise RuntimeError(f"injected birth crash after {label}")
            return result
        return wrapped

    if boundary == "receipt":
        monkeypatch.setattr(
            contract_store_module,
            "_persist_birth_receipt_locked",
            once_after(contract_store_module._persist_birth_receipt_locked, boundary),
        )
    elif boundary == "journal":
        monkeypatch.setattr(
            authoring,
            "persist_prepared_journal",
            once_after(authoring.persist_prepared_journal, boundary),
        )
    elif boundary in {"rename_old", "rename_new"}:
        original = authoring.os.replace

        def replace_then_crash(source, destination):
            nonlocal fired
            result = original(source, destination)
            destination_name = Path(destination).name
            matches = (
                boundary == "rename_old" and destination_name.startswith(".birth-backup-")
            ) or (
                boundary == "rename_new" and not destination_name.startswith(".birth-backup-")
                and Path(source).name.startswith(".birth-stage-")
            )
            if matches and not fired:
                fired = True
                raise RuntimeError(f"injected birth crash after {boundary}")
            return result

        monkeypatch.setattr(authoring.os, "replace", replace_then_crash)
    elif boundary == "generation":
        monkeypatch.setattr(
            contract_store_module,
            "_install_generation",
            once_after(contract_store_module._install_generation, boundary),
        )
    elif boundary == "current":
        monkeypatch.setattr(
            contract_store_module,
            "_write_current",
            once_after(contract_store_module._write_current, boundary),
        )
    elif boundary == "reread":
        monkeypatch.setattr(
            contract_store_module,
            "_verify_published_postcondition",
            once_after(contract_store_module._verify_published_postcondition, boundary),
        )
    elif boundary == "version":
        monkeypatch.setattr(
            authoring,
            "advance_version",
            once_after(authoring.advance_version, boundary),
        )
    elif boundary == "cleanup":
        monkeypatch.setattr(
            authoring,
            "cleanup_transaction",
            once_after(authoring.cleanup_transaction, boundary),
        )
    else:  # pragma: no cover - closed test-owned boundary registry
        raise AssertionError(boundary)


@pytest.mark.parametrize("boundary", _BIRTH_COMMIT_CRASH_BOUNDARIES)
@pytest.mark.parametrize("first_birth", (True, False), ids=("first-birth", "update"))
def test_birth_commit_recovers_every_durable_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    first_birth: bool,
) -> None:
    _root, ref, private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    predecessor = None
    if not first_birth:
        predecessor = publish_signed_source(
            ref, expected_generation_id=None,
            trusted_publics=trusted, store_root=store,
        ).current_generation_id
    snapshot = _birth_snapshot(ref, tmp_path)
    observed: list[tuple[str, Mapping[str, str]]] = []
    authorization = _birth_authorization(
        ref, predecessor, Ed25519PrivateKey.generate(), observed=observed,
    )
    request_id = "sha256:" + ("1" if first_birth else "2") * 64
    _inject_birth_commit_crash(monkeypatch, boundary)

    with pytest.raises(RuntimeError, match=f"after {boundary}"):
        commit_birth_snapshot(
            ref, expected_generation_id=predecessor, snapshot=snapshot,
            request_id=request_id, private_key=private,
            trusted_publics=trusted, birth_authorization=authorization,
            store_root=store,
        )

    recovered = commit_birth_snapshot(
        ref, expected_generation_id=predecessor, snapshot=snapshot,
        request_id=request_id, private_key=private,
        trusted_publics=trusted, birth_authorization=authorization,
        store_root=store,
    )
    assert recovered.current_generation_id == current_revision_id(
        ref, store_root=store,
    )
    assert recovered.current_generation_id != predecessor
    assert len(observed) == 1


@pytest.mark.parametrize(
    "tamper", ("receipt", "journal", "unjournaled_staging", "canonical", "generation"),
)
def test_birth_crash_recovery_rejects_tampered_durable_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
) -> None:
    import executor_birth_authoring as authoring

    _root, ref, private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    predecessor = publish_signed_source(
        ref, expected_generation_id=None,
        trusted_publics=trusted, store_root=store,
    ).current_generation_id
    snapshot = _birth_snapshot(ref, tmp_path)
    authorization = _birth_authorization(
        ref, predecessor, Ed25519PrivateKey.generate(),
    )
    request_id = "sha256:" + "d" * 64
    crash_boundary = (
        "generation" if tamper == "generation"
        else "receipt" if tamper == "unjournaled_staging"
        else "rename_new"
    )
    _inject_birth_commit_crash(monkeypatch, crash_boundary)
    with pytest.raises(RuntimeError):
        commit_birth_snapshot(
            ref, expected_generation_id=predecessor, snapshot=snapshot,
            request_id=request_id, private_key=private,
            trusted_publics=trusted, birth_authorization=authorization,
            store_root=store,
        )

    contract_dir = store / contract_storage_key(ref.contract_id)
    control = authoring.authoring_paths(ref.manifest_dir, ref.contract_id.value)
    pending = authoring.load_prepared_journal(control)
    if tamper != "unjournaled_staging":
        assert pending is not None
    if tamper == "receipt":
        assert pending is not None
        receipt = contract_dir / "admission-receipts" / (
            generation_directory_name(pending.new_generation_id) + ".json"
        )
        receipt.write_bytes(b"{}")
    elif tamper == "journal":
        control.journal.write_bytes(control.journal.read_bytes() + b"\n")
    elif tamper == "unjournaled_staging":
        assert pending is None
        staging = ref.manifest_dir.parent / (
            ".birth-stage-" + request_id.removeprefix("sha256:")
        )
        (staging / "sample.py").write_bytes(b"tampered")
    elif tamper == "canonical":
        (control.canonical / "sample.py").write_bytes(b"tampered")
    else:
        assert pending is not None
        generation = contract_dir / "generations" / generation_directory_name(
            pending.new_generation_id,
        )
        (generation / "manifest.toml").write_bytes(b"tampered")

    with pytest.raises(ContractStoreError):
        commit_birth_snapshot(
            ref, expected_generation_id=predecessor, snapshot=snapshot,
            request_id=request_id, private_key=private,
            trusted_publics=trusted, birth_authorization=authorization,
            store_root=store,
        )


def test_technical_publication_rebases_and_preserves_live_localization(
    tmp_path: Path,
) -> None:
    _root, ref, private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    initial = publish_signed_source(
        ref, expected_generation_id=None,
        trusted_publics=trusted, store_root=store,
    )
    base = current_manifest(ref, trusted_publics=trusted, store_root=store)
    translated_text = (
        "SCOPO: test safely after translation. PATTERN: sample(). "
        "NON: modify. OUT: results=[]."
    )
    patch = _localization_patch(
        base,
        selector="description",
        source_language="it",
        target_language="en",
        candidate=translated_text,
    )
    localized = publish_localization(
        ref,
        expected_generation_id=initial.current_generation_id,
        source_language="it",
        target_language="en",
        patches=(patch,),
        private_key=private,
        trusted_publics=trusted,
        store_root=store,
    )
    localized_snapshot = current_manifest(
        ref, trusted_publics=trusted, store_root=store,
    )
    assert localized_snapshot.parsed["description"]["en"] == translated_text

    code = ref.manifest_dir / "sample.py"
    code.write_text(
        "def invoke(args):\n    return {'results': [], 'revision': 3}\n",
        encoding="utf-8",
    )
    authoring_before = _source_payloads(ref)
    draft = prepare_technical_draft(ref)
    published = publish_technical_update(
        ref,
        expected_generation_id=localized.current_generation_id,
        draft=draft,
        private_key=private,
        trusted_publics=trusted,
        store_root=store,
    )
    repeated = publish_technical_update(
        ref,
        expected_generation_id=localized.current_generation_id,
        draft=draft,
        private_key=private,
        trusted_publics=trusted,
        store_root=store,
    )

    assert published.repeated is False
    assert repeated.repeated is True
    current = current_manifest(ref, trusted_publics=trusted, store_root=store)
    assert current.generation_id == published.current_generation_id
    assert current.declared_code_digest == draft.authoring_code_digest
    assert current.parsed["description"] == localized_snapshot.parsed["description"]
    assert current.language_state == localized_snapshot.language_state
    assert _source_payloads(ref) == authoring_before


def test_technical_and_localization_writers_share_one_cas_boundary(
    tmp_path: Path,
) -> None:
    root, ref, private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    initial = publish_signed_source(
        ref, expected_generation_id=None,
        trusted_publics=trusted, store_root=store,
    )
    base = current_manifest(ref, trusted_publics=trusted, store_root=store)
    candidate = (
        "SCOPO: safely test concurrency. PATTERN: sample(). "
        "NON: modify. OUT: results=[]."
    )
    patch = _localization_patch(
        base, selector="description", source_language="it",
        target_language="en", candidate=candidate,
    )
    ref.manifest_path.write_text(
        ref.manifest_path.read_text(encoding="utf-8") + '''
[args.properties.limit]
type = "integer"

[args.properties.limit.description]
it = "Numero massimo di risultati."
en = "Maximum number of results."
''',
        encoding="utf-8",
    )
    ref = _rewrite_authoring_state(ref)
    draft = prepare_technical_draft(ref)
    start = threading.Barrier(3)
    outcomes: list[object] = []

    def technical_writer() -> None:
        start.wait()
        try:
            outcomes.append(publish_technical_update(
                ref,
                expected_generation_id=initial.current_generation_id,
                draft=draft,
                private_key=private,
                trusted_publics=trusted,
                store_root=store,
            ))
        except ContractStoreError as exc:
            outcomes.append(exc)

    def localization_writer() -> None:
        start.wait()
        try:
            outcomes.append(publish_localization(
                ref,
                expected_generation_id=initial.current_generation_id,
                source_language="it",
                target_language="en",
                patches=(patch,),
                private_key=private,
                trusted_publics=trusted,
                store_root=store,
            ))
        except ContractStoreError as exc:
            outcomes.append(exc)

    threads = (
        threading.Thread(target=technical_writer),
        threading.Thread(target=localization_writer),
    )
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join(timeout=10.0)
        assert not thread.is_alive()

    committed = [item for item in outcomes if not isinstance(item, Exception)]
    rejected = [item for item in outcomes if isinstance(item, ContractStoreError)]
    assert len(committed) == 1
    assert len(rejected) == 1
    assert rejected[0].code == "commit_conflict"
    current = current_manifest(ref, trusted_publics=trusted, store_root=store)
    assert current.generation_id == committed[0].current_generation_id
    assert current.generation_id != initial.current_generation_id
    has_localization = current.parsed["description"]["en"] == candidate
    has_technical = "limit" in current.parsed["args"]["properties"]
    assert has_localization is not has_technical


def test_new_unsigned_contract_is_signed_inside_the_technical_transaction(
    tmp_path: Path,
) -> None:
    _root, ref, private, trusted = _create_source(tmp_path)
    (ref.manifest_dir / "manifest.toml.sig").unlink()
    draft = prepare_technical_draft(ref)
    assert draft.authoring_signature_hash is None

    result = publish_technical_update(
        ref,
        expected_generation_id=None,
        draft=draft,
        private_key=private,
        trusted_publics=trusted,
        store_root=tmp_path / "store",
    )
    assert current_manifest(
        ref, trusted_publics=trusted, store_root=tmp_path / "store",
    ).generation_id == result.current_generation_id
    assert not (ref.manifest_dir / "manifest.toml.sig").exists()


def test_localization_selector_traverses_schema_arrays_without_special_cases(
    tmp_path: Path,
) -> None:
    root, ref, private, trusted = _create_source(tmp_path)
    ref.manifest_path.write_text(
        ref.manifest_path.read_text(encoding="utf-8").replace(
            "required = []\n",
            'required = []\nprefixItems = [{ type = "string", '
            'description = { it = "Elemento.", en = "Item." } }]\n',
        ),
        encoding="utf-8",
    )
    ref = _rewrite_authoring_state(ref)
    (ref.manifest_dir / "manifest.toml.sig").write_bytes(sign_manifest_bytes(
        ref.manifest_path.read_bytes(), private_key=private,
    ))
    ref = _inventory_ref(root, name="read_files")
    store = tmp_path / "store"
    initial = publish_signed_source(
        ref, expected_generation_id=None,
        trusted_publics=trusted, store_root=store,
    )
    base = current_manifest(ref, trusted_publics=trusted, store_root=store)
    selector = "args.prefixItems.0.description"
    assert selector in manifest_language_selectors(base.parsed)
    patch = _localization_patch(
        base,
        selector=selector,
        source_language="it",
        target_language="en",
        candidate="Item to inspect.",
    )
    result = publish_localization(
        ref,
        expected_generation_id=initial.current_generation_id,
        source_language="it",
        target_language="en",
        patches=(patch,),
        private_key=private,
        trusted_publics=trusted,
        store_root=store,
    )
    assert manifest_language_selectors(current_manifest(
        ref, trusted_publics=trusted, store_root=store,
    ).parsed)[selector]["en"] == "Item to inspect."
    assert result.current_generation_id != initial.current_generation_id


def test_technical_draft_staleness_is_detected_under_the_writer_lock(
    tmp_path: Path,
) -> None:
    _root, ref, private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    initial = publish_signed_source(
        ref, expected_generation_id=None,
        trusted_publics=trusted, store_root=store,
    )
    draft = prepare_technical_draft(ref)
    (ref.manifest_dir / "manifest.toml.sig").write_bytes(b"concurrent edit")

    with pytest.raises(ContractStoreError, match="technical_draft_stale"):
        publish_technical_update(
            ref,
            expected_generation_id=initial.current_generation_id,
            draft=draft,
            private_key=private,
            trusted_publics=trusted,
            store_root=store,
        )
    assert current_manifest(
        ref, trusted_publics=trusted, store_root=store,
    ).generation_id == initial.current_generation_id


def test_technical_publication_rejects_existing_text_change(
    tmp_path: Path,
) -> None:
    root, ref, private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    initial = publish_signed_source(
        ref, expected_generation_id=None,
        trusted_publics=trusted, store_root=store,
    )
    changed = ref.manifest_path.read_text(encoding="utf-8").replace(
        "Text to find.", "Different text.",
    )
    ref.manifest_path.write_text(changed, encoding="utf-8")
    ref = _rewrite_authoring_state(ref)
    assert Path(ref.source_root) == root
    draft = prepare_technical_draft(ref)

    with pytest.raises(ContractStoreError, match="existing_localization_changed"):
        publish_technical_update(
            ref,
            expected_generation_id=initial.current_generation_id,
            draft=draft,
            private_key=private,
            trusted_publics=trusted,
            store_root=store,
        )


def test_surface_removal_requires_exact_explicit_evidence(tmp_path: Path) -> None:
    _root, ref, private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    initial = publish_signed_source(
        ref, expected_generation_id=None,
        trusted_publics=trusted, store_root=store,
    )
    text = ref.manifest_path.read_text(encoding="utf-8")
    start = text.index("\n[args.properties.query]\n")
    ref.manifest_path.write_text(text[:start] + "\n", encoding="utf-8")
    ref = _rewrite_authoring_state(ref)
    draft = prepare_technical_draft(ref)

    with pytest.raises(ContractStoreError, match="surface_removal_required"):
        publish_technical_update(
            ref,
            expected_generation_id=initial.current_generation_id,
            draft=draft,
            private_key=private,
            trusted_publics=trusted,
            store_root=store,
        )
    wrong = SurfaceRemoval(
        selectors=("description",), actor="tester", reason="schema cleanup",
    )
    with pytest.raises(ContractStoreError, match="surface_removal_invalid"):
        publish_technical_update(
            ref,
            expected_generation_id=initial.current_generation_id,
            draft=draft,
            private_key=private,
            trusted_publics=trusted,
            removal=wrong,
            store_root=store,
        )
    exact = SurfaceRemoval(
        selectors=("args.properties.query.description",),
        actor="tester",
        reason="query was removed from the schema",
    )
    with pytest.raises(ContractStoreError, match="surface_removal_audit_required"):
        publish_technical_update(
            ref,
            expected_generation_id=initial.current_generation_id,
            draft=draft,
            private_key=private,
            trusted_publics=trusted,
            removal=exact,
            store_root=store,
        )
    events: list[Mapping[str, object]] = []
    audit_path = tmp_path / "audit" / "contract-removals.jsonl"

    def record_removal(event: Mapping[str, object]) -> None:
        events.append(event)
        append_jsonl(audit_path, event, fsync=False)

    published = publish_technical_update(
        ref,
        expected_generation_id=initial.current_generation_id,
        draft=draft,
        private_key=private,
        trusted_publics=trusted,
        removal=exact,
        removal_audit=record_removal,
        store_root=store,
    )
    assert published.current_generation_id != initial.current_generation_id
    assert len(events) == 1
    assert events[0]["contract_id"] == ref.contract_id.value
    assert events[0]["selectors"] == exact.selectors
    assert events[0]["diff"] == {"removed_selectors": exact.selectors}
    assert events[0]["candidate_generation_id"] == published.current_generation_id
    persisted = [
        json.loads(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(persisted) == 1
    assert persisted[0]["event"] == "contract_surface_removal_authorized"
    assert persisted[0]["diff"] == {
        "removed_selectors": list(exact.selectors),
    }
    repeated = publish_technical_update(
        ref,
        expected_generation_id=initial.current_generation_id,
        draft=draft,
        private_key=private,
        trusted_publics=trusted,
        removal=exact,
        removal_audit=record_removal,
        store_root=store,
    )
    assert repeated.repeated is True
    assert len(events) == 1
    assert len(audit_path.read_text(encoding="utf-8").splitlines()) == 1


def test_surface_removal_cannot_delete_prose_from_an_existing_schema_node(
    tmp_path: Path,
) -> None:
    _root, ref, private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    initial = publish_signed_source(
        ref, expected_generation_id=None,
        trusted_publics=trusted, store_root=store,
    )
    text = ref.manifest_path.read_text(encoding="utf-8")
    description = text.index("\n[args.properties.query.description]\n")
    ref.manifest_path.write_text(text[:description] + "\n", encoding="utf-8")
    ref = _rewrite_authoring_state(ref)
    draft = prepare_technical_draft(ref)
    removal = SurfaceRemoval(
        selectors=("args.properties.query.description",),
        actor="tester",
        reason="attempted prose-only removal",
    )

    with pytest.raises(
        ContractStoreError, match="surface_removal_still_applicable",
    ):
        publish_technical_update(
            ref,
            expected_generation_id=initial.current_generation_id,
            draft=draft,
            private_key=private,
            trusted_publics=trusted,
            removal=removal,
            removal_audit=lambda _event: None,
            store_root=store,
        )
    assert current_manifest(
        ref, trusted_publics=trusted, store_root=store,
    ).generation_id == initial.current_generation_id


def test_surface_removal_audit_cannot_invalidate_code_before_commit(
    tmp_path: Path,
) -> None:
    _root, ref, private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    initial = publish_signed_source(
        ref, expected_generation_id=None,
        trusted_publics=trusted, store_root=store,
    )
    text = ref.manifest_path.read_text(encoding="utf-8")
    start = text.index("\n[args.properties.query]\n")
    ref.manifest_path.write_text(text[:start] + "\n", encoding="utf-8")
    ref = _rewrite_authoring_state(ref)
    draft = prepare_technical_draft(ref)
    removal = SurfaceRemoval(
        selectors=("args.properties.query.description",),
        actor="tester",
        reason="query was removed from the schema",
    )
    code_path = ref.manifest_dir / "sample.py"
    original_code = code_path.read_bytes()

    def mutating_audit(_event: Mapping[str, object]) -> None:
        code_path.write_text("VALUE = 'changed by faulty audit sink'\n", encoding="utf-8")

    with pytest.raises(ContractStoreError, match="code_digest_mismatch"):
        publish_technical_update(
            ref,
            expected_generation_id=initial.current_generation_id,
            draft=draft,
            private_key=private,
            trusted_publics=trusted,
            removal=removal,
            removal_audit=mutating_audit,
            store_root=store,
        )
    code_path.write_bytes(original_code)
    assert current_manifest(
        ref, trusted_publics=trusted, store_root=store,
    ).generation_id == initial.current_generation_id


def test_technical_publication_accepts_only_complete_new_surfaces(
    tmp_path: Path,
) -> None:
    root, ref, private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    initial = publish_signed_source(
        ref, expected_generation_id=None,
        trusted_publics=trusted, store_root=store,
    )
    addition = '''
[args.properties.limit]
type = "integer"

[args.properties.limit.description]
it = "Numero massimo di risultati."
en = "Maximum number of results."
'''
    ref.manifest_path.write_text(
        ref.manifest_path.read_text(encoding="utf-8") + addition,
        encoding="utf-8",
    )
    ref = _rewrite_authoring_state(ref)
    draft = prepare_technical_draft(ref)
    published = publish_technical_update(
        ref,
        expected_generation_id=initial.current_generation_id,
        draft=draft,
        private_key=private,
        trusted_publics=trusted,
        store_root=store,
    )
    assert "args.properties.limit.description" in manifest_language_selectors(
        current_manifest(ref, trusted_publics=trusted, store_root=store).parsed,
    )
    assert published.current_generation_id != initial.current_generation_id

    # A fresh fixture proves that incompleteness is rejected before locking or
    # signing, without relying on an executor-specific allowlist.
    second_root, second_ref, _second_private, _second_trusted = _create_source(
        tmp_path / "incomplete", name="read_events",
    )
    incomplete = '''
[args.properties.limit]
type = "integer"

[args.properties.limit.description]
it = "Numero massimo di risultati."
'''
    second_ref.manifest_path.write_text(
        second_ref.manifest_path.read_text(encoding="utf-8") + incomplete,
        encoding="utf-8",
    )
    second_ref = _rewrite_authoring_state(second_ref)
    assert Path(second_ref.source_root) == second_root
    with pytest.raises(ContractStoreError, match="language_coverage_incomplete"):
        prepare_technical_draft(second_ref)


def test_signed_import_obeys_the_same_localization_non_regression_policy(
    tmp_path: Path,
) -> None:
    root, ref, private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    initial = publish_signed_source(
        ref, expected_generation_id=None,
        trusted_publics=trusted, store_root=store,
    )
    ref.manifest_path.write_text(
        ref.manifest_path.read_text(encoding="utf-8").replace(
            "Text to find.", "Changed through signed import.",
        ),
        encoding="utf-8",
    )
    ref = _rewrite_authoring_state(ref)
    (ref.manifest_dir / "manifest.toml.sig").write_bytes(sign_manifest_bytes(
        ref.manifest_path.read_bytes(), private_key=private,
    ))
    ref = _inventory_ref(root, name="read_files")

    with pytest.raises(ContractStoreError, match="existing_localization_changed"):
        publish_signed_source(
            ref,
            expected_generation_id=initial.current_generation_id,
            trusted_publics=trusted,
            store_root=store,
        )


def test_technical_publisher_owns_exactly_one_writer_lock(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _root, ref, private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    initial = publish_signed_source(
        ref, expected_generation_id=None,
        trusted_publics=trusted, store_root=store,
    )
    draft = prepare_technical_draft(ref)
    real_lock = contract_store_module._writer_lock
    acquisitions = 0

    @contextlib.contextmanager
    def counted_lock(*args, **kwargs):
        nonlocal acquisitions
        acquisitions += 1
        with real_lock(*args, **kwargs):
            yield

    monkeypatch.setattr(contract_store_module, "_writer_lock", counted_lock)
    publish_technical_update(
        ref,
        expected_generation_id=initial.current_generation_id,
        draft=draft,
        private_key=private,
        trusted_publics=trusted,
        store_root=store,
    )
    assert acquisitions == 1


def test_activation_requires_quiescence_and_an_exact_verified_catalog(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _root, ref, _private, trusted, shadow, initial = _create_productive_shadow(
        tmp_path, monkeypatch,
    )
    expected = {ref.contract_id: initial.current_generation_id}
    marker = contract_store_module._C.PATH_USER_STATE / "contract-publications.ACTIVE"

    with pytest.raises(ContractStoreError, match="activation_not_quiescent"):
        activate_store(expected, shadow_root=shadow, trusted_publics=trusted)
    with pytest.raises(ContractStoreError, match="activation_not_quiescent"):
        activate_store(
            expected,
            shadow_root=shadow,
            trusted_publics=trusted,
            quiescence_guard=lambda: False,
        )
    assert production_store_mode() is ProductionStoreMode.LEGACY
    assert not marker.exists()
    assert shadow.is_dir()

    wrong = {ref.contract_id: "sha256:" + "0" * 64}
    with pytest.raises(ContractStoreError, match="activation_catalog_mismatch"):
        activate_store(
            wrong,
            shadow_root=shadow,
            trusted_publics=trusted,
            quiescence_guard=lambda: True,
        )
    assert not marker.exists()

    expanded = dict(expected)
    expanded[ContractId(ManifestOrigin.CORE, "foreign/manifest.toml")] = (
        initial.current_generation_id
    )
    with pytest.raises(ContractStoreError, match="activation_catalog_mismatch"):
        activate_store(
            expanded,
            shadow_root=shadow,
            trusted_publics=trusted,
            quiescence_guard=lambda: True,
        )
    assert not marker.exists()

    (shadow / "unexpected-contract").mkdir()
    with pytest.raises(ContractStoreError, match="activation_catalog_mismatch"):
        activate_store(
            expected,
            shadow_root=shadow,
            trusted_publics=trusted,
            quiescence_guard=lambda: True,
        )
    assert not marker.exists()


def test_cutover_externalizes_repository_authoring_and_birth_keeps_release_unchanged(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root, ref, private, trusted, shadow, initial = _create_productive_shadow(
        tmp_path, monkeypatch,
    )
    monkeypatch.setattr(
        contract_store_module, "_deny_closed_legacy_api",
        lambda _operation, _store_root: None,
    )
    release_before = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*") if path.is_file()
    }

    activate_store(
        {ref.contract_id: initial.current_generation_id},
        shadow_root=shadow,
        trusted_publics=trusted,
        quiescence_guard=lambda: True,
    )

    productive = (
        contract_store_module._C.PATH_USER_STATE
        / "contract-publications" / "v1"
    )
    inventory = manifest_inventory_module.inventory_store_manifests(
        store_root=productive,
    )
    assert not inventory.problems
    live_ref = inventory.by_id()[ref.contract_id]
    external = (
        contract_store_module._C.PATH_USER_STATE
        / "contract-authoring" / "v1" / "core" / "sample"
    )
    assert live_ref.manifest_dir == external
    control_directories = tuple(external.parent.glob(".sample.birth-control-*"))
    assert len(control_directories) == 1
    assert (control_directories[0] / "authoring.lock").is_file()
    assert (control_directories[0] / "version.json").is_file()
    assert current_manifest(
        live_ref, trusted_publics=trusted, store_root=productive,
    ).generation_id == initial.current_generation_id

    code = external / "sample.py"
    code.write_text(
        "def invoke(args):\n    return {'results': [], 'birth': 2}\n",
        encoding="utf-8",
    )
    document = tomlkit.parse((external / "manifest.toml").read_text())
    document["code"]["digest"] = (
        "sha256:" + hashlib.sha256(code.read_bytes()).hexdigest()
    )
    (external / "manifest.toml").write_text(tomlkit.dumps(document))
    candidate = tmp_path / "external-candidate"
    candidate.mkdir()
    for name in ("manifest.toml", "manifest.lang_state.json", "sample.py"):
        (candidate / name).write_bytes((external / name).read_bytes())
    snapshot = acquire_candidate_snapshot(candidate, private_parent=tmp_path)
    authorization = _birth_authorization(
        live_ref,
        initial.current_generation_id,
        Ed25519PrivateKey.generate(),
    )
    result = commit_birth_snapshot(
        live_ref,
        expected_generation_id=initial.current_generation_id,
        snapshot=snapshot,
        request_id="sha256:" + "9" * 64,
        private_key=private,
        trusted_publics=trusted,
        birth_authorization=authorization,
        registry_reconciler=lambda _snapshot: None,
    )
    snapshot.close()

    restarted_inventory = manifest_inventory_module.inventory_store_manifests(
        store_root=productive,
    )
    restarted_ref = restarted_inventory.by_id()[ref.contract_id]
    restarted = current_manifest(
        restarted_ref, trusted_publics=trusted, store_root=productive,
    )
    assert restarted.generation_id == result.current_generation_id
    assert restarted.verified_code_digest == document["code"]["digest"]
    assert {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*") if path.is_file()
    } == release_before
    assert not tuple(root.rglob("*.birth-control-*"))


def test_store_inventory_ignores_only_exact_empty_unbound_publication_residue(
    tmp_path: Path,
) -> None:
    store = tmp_path / "v1"
    store.mkdir(mode=0o700)
    store.chmod(0o700)
    contract_dir = store / ("a" * 64)
    generations = contract_dir / "generations"
    generations.mkdir(parents=True, mode=0o700)
    contract_dir.chmod(0o700)
    generations.chmod(0o700)
    lock_path = contract_dir / "writer.lock"
    lock_path.write_bytes(b"\0")
    lock_path.chmod(0o600)

    inventory = manifest_inventory_module.inventory_store_manifests(
        sources=(), store_root=store,
    )

    assert not inventory.manifests
    assert not inventory.problems


@pytest.mark.parametrize("corruption", (
    "unexpected_entry",
    "generation_payload",
    "wrong_lock_bytes",
    "wrong_lock_mode",
))
def test_store_inventory_exposes_deviating_unbound_publication_residue(
    tmp_path: Path,
    corruption: str,
) -> None:
    store = tmp_path / "v1"
    store.mkdir(mode=0o700)
    store.chmod(0o700)
    contract_dir = store / ("b" * 64)
    generations = contract_dir / "generations"
    generations.mkdir(parents=True, mode=0o700)
    contract_dir.chmod(0o700)
    generations.chmod(0o700)
    lock_path = contract_dir / "writer.lock"
    lock_path.write_bytes(b"\0")
    lock_path.chmod(0o600)
    if corruption == "unexpected_entry":
        (contract_dir / "unexpected").write_bytes(b"")
    elif corruption == "generation_payload":
        (generations / "payload").write_bytes(b"")
    elif corruption == "wrong_lock_bytes":
        lock_path.write_bytes(b"x")
    elif corruption == "wrong_lock_mode":
        lock_path.chmod(0o640)

    inventory = manifest_inventory_module.inventory_store_manifests(
        sources=(), store_root=store,
    )

    assert not inventory.manifests
    assert [problem.code for problem in inventory.problems] == ["binding_invalid"]


def test_cutover_resumes_an_exact_external_authoring_seed_before_marker(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _root, ref, _private, trusted, shadow, initial = _create_productive_shadow(
        tmp_path, monkeypatch,
    )
    monkeypatch.setattr(
        contract_store_module, "_deny_closed_legacy_api",
        lambda _operation, _store_root: None,
    )
    marker = (
        contract_store_module._C.PATH_USER_STATE
        / "contract-publications.ACTIVE"
    )
    real_rename = contract_store_module._rename_no_replace
    interrupted = False

    def stop_once(source: Path, destination: Path) -> None:
        nonlocal interrupted
        if not interrupted and source.name.startswith(".birth-stage-"):
            interrupted = True
            raise ContractStoreError("simulated_authoring_seed_stop")
        real_rename(source, destination)

    monkeypatch.setattr(
        contract_store_module, "_rename_no_replace", stop_once,
    )
    with pytest.raises(
        ContractStoreError, match="simulated_authoring_seed_stop",
    ):
        activate_store(
            {ref.contract_id: initial.current_generation_id},
            shadow_root=shadow,
            trusted_publics=trusted,
            quiescence_guard=lambda: True,
        )
    assert interrupted
    assert not marker.exists()
    assert shadow.is_dir()
    seed_root = (
        contract_store_module._C.PATH_USER_STATE
        / "contract-authoring" / "v1"
    )
    assert tuple(seed_root.rglob(".birth-stage-*"))

    monkeypatch.setattr(
        contract_store_module, "_rename_no_replace", real_rename,
    )
    activate_store(
        {ref.contract_id: initial.current_generation_id},
        shadow_root=shadow,
        trusted_publics=trusted,
        quiescence_guard=lambda: True,
    )

    assert marker.read_bytes() == ACTIVE_BYTES
    assert not tuple(seed_root.rglob(".birth-stage-*"))


def test_transition_materialization_has_no_ownership_authority(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _root, ref, _private, trusted, shadow, initial = _create_productive_shadow(
        tmp_path, monkeypatch,
    )
    monkeypatch.setattr(
        contract_store_module, "_deny_closed_legacy_api",
        lambda _operation, _store_root: None,
    )
    activate_store(
        {ref.contract_id: initial.current_generation_id},
        shadow_root=shadow,
        trusted_publics=trusted,
        quiescence_guard=lambda: True,
    )
    monkeypatch.setattr(
        contract_store_module.os, "fchown",
        lambda *_args: pytest.fail("contract store attempted ownership transfer"),
    )
    assert contract_store_module.materialize_repository_authoring_for_transition_v1(
        trusted_publics=trusted,
    ) == 1


def test_transition_owner_binding_rejects_a_linked_authoring_inode(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _root, ref, _private, trusted, shadow, initial = _create_productive_shadow(
        tmp_path, monkeypatch,
    )
    monkeypatch.setattr(
        contract_store_module, "_deny_closed_legacy_api",
        lambda _operation, _store_root: None,
    )
    activate_store(
        {ref.contract_id: initial.current_generation_id},
        shadow_root=shadow,
        trusted_publics=trusted,
        quiescence_guard=lambda: True,
    )
    manifest = (
        contract_store_module._C.PATH_USER_STATE
        / "contract-authoring" / "v1" / "core" / "sample"
        / "manifest.toml"
    )
    linked = tmp_path / "linked-manifest.toml"
    linked.write_bytes(manifest.read_bytes())
    manifest.unlink()
    os.link(linked, manifest)

    with pytest.raises(ContractStoreError, match="authoring_tree_invalid"):
        contract_store_module.materialize_repository_authoring_for_transition_v1(
            trusted_publics=trusted,
        )


def test_activation_owns_catalog_locks_in_production_then_shadow_order(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _root, ref, _private, trusted, shadow, initial = _create_productive_shadow(
        tmp_path, monkeypatch,
    )
    production = (
        contract_store_module._C.PATH_USER_STATE
        / "contract-publications" / "v1"
    )
    events: list[tuple[str, Path]] = []
    real_lock = contract_store_module.catalog_admission_lock

    @contextlib.contextmanager
    def traced_lock(*, store_root=None, timeout=contract_store_module.DEFAULT_LOCK_TIMEOUT):
        root = Path(store_root)
        events.append(("enter", root))
        with real_lock(store_root=root, timeout=timeout):
            yield
        events.append(("exit", root))

    monkeypatch.setattr(
        contract_store_module, "catalog_admission_lock", traced_lock,
    )

    activate_store(
        {ref.contract_id: initial.current_generation_id},
        shadow_root=shadow,
        trusted_publics=trusted,
        quiescence_guard=lambda: True,
    )

    assert events == [
        ("enter", production),
        ("enter", shadow),
        ("exit", shadow),
        ("exit", production),
    ]


def test_activation_proves_quiescence_under_production_catalog_exclusion(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _root, ref, _private, trusted, shadow, initial = _create_productive_shadow(
        tmp_path, monkeypatch,
    )
    production = (
        contract_store_module._C.PATH_USER_STATE
        / "contract-publications" / "v1"
    )
    outcome: list[str] = []

    def contend_as_offline_writer() -> None:
        try:
            with contract_store_module.catalog_admission_lock(
                store_root=production,
                timeout=0.05,
            ):
                outcome.append("acquired")
        except ContractStoreError as exc:
            outcome.append(exc.code)

    def proof() -> bool:
        worker = threading.Thread(target=contend_as_offline_writer)
        worker.start()
        worker.join(1.0)
        assert not worker.is_alive()
        return True

    activate_store(
        {ref.contract_id: initial.current_generation_id},
        shadow_root=shadow,
        trusted_publics=trusted,
        quiescence_guard=proof,
    )

    assert outcome == ["catalog_lock_timeout"]
    assert production_store_mode() is ProductionStoreMode.ACTIVE


def test_activation_excludes_direct_shadow_publication_through_global_swap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _root, ref, _private, trusted, shadow, initial = _create_productive_shadow(
        tmp_path, monkeypatch,
    )
    real_verify = contract_store_module._recover_and_verify_activation_catalog
    attempted = False
    outcome: list[str] = []

    def publish_directly() -> None:
        try:
            publish_signed_source(
                ref,
                expected_generation_id=initial.current_generation_id,
                trusted_publics=trusted,
                store_root=shadow,
                lock_timeout=0.05,
            )
        except ContractStoreError as exc:
            outcome.append(exc.code)

    def verify_then_contend(root, expected_catalog, **kwargs):
        nonlocal attempted
        real_verify(root, expected_catalog, **kwargs)
        if Path(root) == shadow and not attempted:
            attempted = True
            worker = threading.Thread(target=publish_directly)
            worker.start()
            worker.join(1.0)
            assert not worker.is_alive()

    monkeypatch.setattr(
        contract_store_module,
        "_recover_and_verify_activation_catalog",
        verify_then_contend,
    )

    activate_store(
        {ref.contract_id: initial.current_generation_id},
        shadow_root=shadow,
        trusted_publics=trusted,
        quiescence_guard=lambda: True,
    )

    assert attempted
    assert outcome == ["catalog_lock_timeout"]
    assert production_store_mode() is ProductionStoreMode.ACTIVE


def test_pre_cutover_inventory_requires_admitted_and_disabled_contracts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _root, admitted, _private, _trusted = _create_source(tmp_path)
    disabled_id = ContractId(
        ManifestOrigin.EXPLICIT, "disabled/manifest.toml",
    )
    disabled = replace(
        admitted,
        contract_id=disabled_id,
        status=ManifestStatus.DISABLED,
        manifest_path=admitted.source_root / "disabled" / "manifest.toml",
        manifest_relative=disabled_id.relative_manifest,
        name="disabled_executor",
    )
    inventory = ManifestInventory((admitted, disabled), ())
    monkeypatch.setattr(
        manifest_inventory_module,
        "inventory_authoring_manifests",
        lambda: inventory,
    )

    expected = {
        admitted.contract_id: "sha256:" + "1" * 64,
        disabled.contract_id: "sha256:" + "2" * 64,
    }
    contract_store_module._verify_pre_cutover_inventory(expected)

    with pytest.raises(ContractStoreError, match="activation_catalog_mismatch"):
        contract_store_module._verify_pre_cutover_inventory({
            admitted.contract_id: "sha256:" + "1" * 64,
        })


@pytest.mark.parametrize("marker_present", (False, True))
@pytest.mark.parametrize(
    "incomplete_layout",
    ("container_only", "empty_root", "root_debris"),
)
def test_production_mode_classifies_incomplete_or_empty_store_as_recovery(
    tmp_path: Path,
    monkeypatch,
    marker_present: bool,
    incomplete_layout: str,
) -> None:
    user_state = tmp_path / "user-state"
    monkeypatch.setattr(contract_store_module._C, "PATH_USER_STATE", user_state)
    container = user_state / "contract-publications"
    root = container / "v1"
    container.mkdir(parents=True)
    if incomplete_layout != "container_only":
        root.mkdir()
    if incomplete_layout == "root_debris":
        (root / "not-a-contract").write_bytes(b"incomplete")
    if marker_present:
        (user_state / "contract-publications.ACTIVE").write_bytes(ACTIVE_BYTES)

    assert production_store_mode() is ProductionStoreMode.RECOVERY_REQUIRED


def test_cutover_recovers_reserved_staging_before_verifying_shadow(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _root, ref, _private, trusted, shadow, initial = _create_productive_shadow(
        tmp_path, monkeypatch,
    )
    staging = (
        shadow / contract_storage_key(ref.contract_id) / "generations"
        / ".generation-crashed-writer"
    )
    staging.mkdir()
    (staging / "manifest.toml.sig").write_bytes(b"interrupted write")
    contract_dir = shadow / contract_storage_key(ref.contract_id)
    (contract_dir / ".binding.json.1.2.3.tmp").write_bytes(
        encode_binding(ref.contract_id)
    )
    (contract_dir / ".current.4.5.6.tmp").write_bytes(
        (initial.current_generation_id + "\n").encode("ascii")
    )

    activate_store(
        {ref.contract_id: initial.current_generation_id},
        shadow_root=shadow,
        trusted_publics=trusted,
        quiescence_guard=lambda: True,
    )

    productive_generations = (
        contract_store_module._C.PATH_USER_STATE
        / "contract-publications" / "v1"
        / contract_storage_key(ref.contract_id) / "generations"
    )
    assert not any(
        child.name.startswith(".generation-")
        for child in productive_generations.iterdir()
    )
    productive_contract = productive_generations.parent
    assert not tuple(productive_contract.glob(".binding.json.*.tmp"))
    assert not tuple(productive_contract.glob(".current.*.tmp"))


def test_cutover_rejects_unknown_debris_in_reserved_staging_namespace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _root, ref, _private, trusted, shadow, initial = _create_productive_shadow(
        tmp_path, monkeypatch,
    )
    staging = (
        shadow / contract_storage_key(ref.contract_id) / "generations"
        / ".generation-unrelated"
    )
    staging.mkdir()
    debris = staging / "payload.bin"
    debris.write_bytes(b"unrelated")

    with pytest.raises(ContractStoreError, match="staging_invalid"):
        activate_store(
            {ref.contract_id: initial.current_generation_id},
            shadow_root=shadow,
            trusted_publics=trusted,
            quiescence_guard=lambda: True,
        )

    assert debris.read_bytes() == b"unrelated"
    assert not (
        contract_store_module._C.PATH_USER_STATE
        / "contract-publications.ACTIVE"
    ).exists()


def test_activation_is_global_exact_and_repairs_store_only_marker(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _root, ref, _private, trusted, shadow, initial = _create_productive_shadow(
        tmp_path, monkeypatch,
    )
    expected = {ref.contract_id: initial.current_generation_id}
    user_state = contract_store_module._C.PATH_USER_STATE
    marker = user_state / "contract-publications.ACTIVE"
    productive = user_state / "contract-publications" / "v1"

    activate_store(
        expected,
        shadow_root=shadow,
        trusted_publics=trusted,
        quiescence_guard=lambda: True,
    )

    assert marker.read_bytes() == ACTIVE_BYTES
    assert productive.is_dir()
    assert not shadow.parent.exists()
    assert production_store_mode() is ProductionStoreMode.ACTIVE
    assert current_manifest(
        ref, trusted_publics=trusted, store_root=productive,
    ).generation_id == initial.current_generation_id

    # A completed cutover is an exact verified no-op even though the old
    # shadow path no longer exists.
    activate_store(
        expected,
        shadow_root=shadow,
        trusted_publics=trusted,
        quiescence_guard=lambda: True,
    )
    marker.unlink()
    assert production_store_mode() is ProductionStoreMode.STORE_ONLY
    activate_store(
        expected,
        shadow_root=shadow,
        trusted_publics=trusted,
        quiescence_guard=lambda: True,
    )
    assert marker.read_bytes() == ACTIVE_BYTES
    assert production_store_mode() is ProductionStoreMode.ACTIVE


def test_activation_resumes_after_durable_marker_before_global_swap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _root, ref, _private, trusted, shadow, initial = _create_productive_shadow(
        tmp_path, monkeypatch,
    )
    expected = {ref.contract_id: initial.current_generation_id}
    real_move = contract_store_module._move_activation_container

    def interrupted_move(_source: Path, _destination: Path) -> None:
        raise ContractStoreError("simulated_cutover_crash")

    monkeypatch.setattr(
        contract_store_module, "_move_activation_container", interrupted_move,
    )
    with pytest.raises(ContractStoreError, match="simulated_cutover_crash"):
        activate_store(
            expected,
            shadow_root=shadow,
            trusted_publics=trusted,
            quiescence_guard=lambda: True,
        )
    assert production_store_mode() is ProductionStoreMode.RECOVERY_REQUIRED
    assert shadow.is_dir()

    monkeypatch.setattr(contract_store_module, "_move_activation_container", real_move)
    activate_store(
        expected,
        shadow_root=shadow,
        trusted_publics=trusted,
        quiescence_guard=lambda: True,
    )
    assert production_store_mode() is ProductionStoreMode.ACTIVE


def test_productive_publish_requires_registry_and_repairs_authoring_on_retry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _root, ref, private, trusted, shadow, initial = _create_productive_shadow(
        tmp_path, monkeypatch,
    )
    expected = {ref.contract_id: initial.current_generation_id}
    activate_store(
        expected,
        shadow_root=shadow,
        trusted_publics=trusted,
        quiescence_guard=lambda: True,
    )
    productive = (
        contract_store_module._C.PATH_USER_STATE / "contract-publications" / "v1"
    )
    base = current_manifest(ref, trusted_publics=trusted, store_root=productive)
    patch = _localization_patch(
        base,
        selector="description",
        source_language="it",
        target_language="en",
        candidate=(
            "SCOPO: productive test. PATTERN: sample(). "
            "NON: modify. OUT: results=[]."
        ),
    )

    with pytest.raises(ContractStoreError, match="registry_reconciler_required"):
        publish_localization(
            ref,
            expected_generation_id=initial.current_generation_id,
            source_language="it",
            target_language="en",
            patches=(patch,),
            private_key=private,
            trusted_publics=trusted,
        )
    assert current_manifest(
        ref, trusted_publics=trusted, store_root=productive,
    ).generation_id == initial.current_generation_id

    reconciled = []
    published = publish_localization(
        ref,
        expected_generation_id=initial.current_generation_id,
        source_language="it",
        target_language="en",
        patches=(patch,),
        private_key=private,
        trusted_publics=trusted,
        registry_reconciler=reconciled.append,
    )
    live = current_manifest(ref, trusted_publics=trusted, store_root=productive)
    assert _source_payloads(ref) == contract_store_module._snapshot_payloads(live)
    assert reconciled[-1].generation_id == published.current_generation_id
    audit_path = (
        contract_store_module._C.PATH_USER_STATE
        / contract_store_module.PUBLICATION_AUDIT_BASENAME
    )
    audit = tuple(
        json.loads(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
    )
    assert len(audit) == 1
    assert audit[0]["event"] == "contract_generation_commit_authorized"
    assert audit[0]["operation"] == "publish_localization"
    assert audit[0]["contract_id"] == ref.contract_id.value
    assert audit[0]["candidate_generation_id"] == published.current_generation_id

    # Simulate an interruption half way through the three-file authoring
    # mirror.  The idempotent branch must repair it and still call RM-0005 from
    # a fresh authoritative read.
    (ref.manifest_dir / "manifest.toml").write_bytes(base.manifest_bytes)
    repaired = publish_localization(
        ref,
        expected_generation_id=initial.current_generation_id,
        source_language="it",
        target_language="en",
        patches=(patch,),
        private_key=private,
        trusted_publics=trusted,
        registry_reconciler=reconciled.append,
    )
    assert repaired.repeated
    assert _source_payloads(ref) == contract_store_module._snapshot_payloads(live)
    assert reconciled[-1].generation_id == published.current_generation_id
    assert len(audit_path.read_text(encoding="utf-8").splitlines()) == 1


def test_productive_publication_stops_before_commit_when_audit_is_unavailable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _root, ref, private, trusted, shadow, initial = _create_productive_shadow(
        tmp_path, monkeypatch,
    )
    activate_store(
        {ref.contract_id: initial.current_generation_id},
        shadow_root=shadow,
        trusted_publics=trusted,
        quiescence_guard=lambda: True,
    )
    productive = (
        contract_store_module._C.PATH_USER_STATE / "contract-publications" / "v1"
    )
    base = current_manifest(ref, trusted_publics=trusted, store_root=productive)
    patch = _localization_patch(
        base,
        selector="description",
        source_language="it",
        target_language="en",
        candidate=(
            "SCOPO: audit boundary test. PATTERN: sample(). "
            "NON: modify. OUT: results=[]."
        ),
    )
    generations = (
        productive / contract_storage_key(ref.contract_id) / "generations"
    )
    before = {entry.name for entry in generations.iterdir()}

    def unavailable(*_args, **_kwargs):
        raise OSError("audit unavailable")

    monkeypatch.setattr(
        audit_jsonl_module, "append_unique_jsonl", unavailable,
    )
    with pytest.raises(
        ContractStoreError, match="publication_audit_unavailable",
    ):
        publish_localization(
            ref,
            expected_generation_id=initial.current_generation_id,
            source_language="it",
            target_language="en",
            patches=(patch,),
            private_key=private,
            trusted_publics=trusted,
            registry_reconciler=lambda _snapshot: None,
        )

    assert current_manifest(
        ref, trusted_publics=trusted, store_root=productive,
    ).generation_id == initial.current_generation_id
    assert {entry.name for entry in generations.iterdir()} == before


def test_productive_technical_retry_repairs_a_partial_authoring_mirror(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _root, ref, private, trusted, shadow, initial = _create_productive_shadow(
        tmp_path, monkeypatch,
    )
    activate_store(
        {ref.contract_id: initial.current_generation_id},
        shadow_root=shadow,
        trusted_publics=trusted,
        quiescence_guard=lambda: True,
    )
    productive = (
        contract_store_module._C.PATH_USER_STATE / "contract-publications" / "v1"
    )
    (ref.manifest_dir / "sample.py").write_text(
        "def invoke(args):\n    return {'results': [], 'revision': 2}\n",
        encoding="utf-8",
    )
    draft = prepare_technical_draft(ref)
    real_reconcile = contract_store_module._reconcile_authoring_locked
    interrupted = False

    def partial_reconcile(_ref, payloads, **_kwargs):
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            (_ref.manifest_dir / "manifest.toml").write_bytes(
                payloads["manifest.toml"],
            )
            raise ContractStoreError("simulated_authoring_crash")
        return real_reconcile(_ref, payloads, **_kwargs)

    monkeypatch.setattr(
        contract_store_module, "_reconcile_authoring_locked", partial_reconcile,
    )
    with pytest.raises(ContractStoreError, match="simulated_authoring_crash"):
        publish_technical_update(
            ref,
            expected_generation_id=initial.current_generation_id,
            draft=draft,
            private_key=private,
            trusted_publics=trusted,
            registry_reconciler=lambda _snapshot: None,
        )
    committed = current_manifest(ref, trusted_publics=trusted, store_root=productive)

    reconciled = []
    retried = publish_technical_update(
        ref,
        expected_generation_id=initial.current_generation_id,
        draft=draft,
        private_key=private,
        trusted_publics=trusted,
        registry_reconciler=reconciled.append,
    )
    assert retried.repeated
    assert retried.current_generation_id == committed.generation_id
    assert _source_payloads(ref) == contract_store_module._snapshot_payloads(committed)
    assert reconciled[-1].generation_id == committed.generation_id


def test_productive_rollback_is_pointer_only_audited_and_reconciled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _root, ref, private, trusted, shadow, initial = _create_productive_shadow(
        tmp_path, monkeypatch,
    )
    activate_store(
        {ref.contract_id: initial.current_generation_id},
        shadow_root=shadow,
        trusted_publics=trusted,
        quiescence_guard=lambda: True,
    )
    productive = (
        contract_store_module._C.PATH_USER_STATE / "contract-publications" / "v1"
    )
    base = current_manifest(ref, trusted_publics=trusted, store_root=productive)
    patch = _localization_patch(
        base,
        selector="description",
        source_language="it",
        target_language="en",
        candidate=(
            "SCOPO: rollback test. PATTERN: sample(). "
            "NON: modify. OUT: results=[]."
        ),
    )
    registry = []
    localized = publish_localization(
        ref,
        expected_generation_id=initial.current_generation_id,
        source_language="it",
        target_language="en",
        patches=(patch,),
        private_key=private,
        trusted_publics=trusted,
        registry_reconciler=registry.append,
    )
    generations = productive / contract_storage_key(ref.contract_id) / "generations"
    generation_names = {entry.name for entry in generations.iterdir()}
    audit: list[Mapping[str, object]] = []

    restored = rollback(
        ref,
        expected_generation_id=localized.current_generation_id,
        target_generation_id=initial.current_generation_id,
        actor="operator",
        reason="verified recovery",
        trusted_publics=trusted,
        audit_sink=audit.append,
        registry_reconciler=registry.append,
    )
    repeated = rollback(
        ref,
        expected_generation_id=localized.current_generation_id,
        target_generation_id=initial.current_generation_id,
        actor="operator",
        reason="verified recovery",
        trusted_publics=trusted,
        audit_sink=audit.append,
        registry_reconciler=registry.append,
    )

    live = current_manifest(ref, trusted_publics=trusted, store_root=productive)
    assert restored.current_generation_id == initial.current_generation_id
    assert repeated.repeated
    assert live.generation_id == initial.current_generation_id
    assert {entry.name for entry in generations.iterdir()} == generation_names
    assert _source_payloads(ref) == contract_store_module._snapshot_payloads(live)
    assert len(audit) == 1
    assert audit[0] == {
        "event": "contract_generation_rollback",
        "contract_id": ref.contract_id.value,
        "expected_generation_id": localized.current_generation_id,
        "target_generation_id": initial.current_generation_id,
        "actor": "operator",
        "reason": "verified recovery",
        "event_id": audit[0]["event_id"],
    }
    assert str(audit[0]["event_id"]).startswith("sha256:")
    assert registry[-1].generation_id == initial.current_generation_id


def test_rollback_authenticates_current_structurally_across_real_code_change(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _root, ref, private, trusted, shadow, initial = _create_productive_shadow(
        tmp_path, monkeypatch,
    )
    activate_store(
        {ref.contract_id: initial.current_generation_id},
        shadow_root=shadow,
        trusted_publics=trusted,
        quiescence_guard=lambda: True,
    )
    original_payloads = _source_payloads(ref)
    code = ref.manifest_dir / "sample.py"
    original_code = code.read_bytes()

    code.write_text(
        "def invoke(args):\n    return {'results': ['version-b']}\n",
        encoding="utf-8",
    )
    ref.manifest_path.write_text(
        ref.manifest_path.read_text(encoding="utf-8").replace(
            'version = "1.0.0"', 'version = "2.0.0"', 1,
        ),
        encoding="utf-8",
    )
    draft = prepare_technical_draft(ref)
    registry: list[object] = []
    second = publish_technical_update(
        ref,
        expected_generation_id=initial.current_generation_id,
        draft=draft,
        private_key=private,
        trusted_publics=trusted,
        registry_reconciler=registry.append,
    )

    # A real technical rollback restores source/code first.  The live B
    # generation must still authenticate as the CAS selector even though the
    # authoring tree now contains A; only target A must match that code.
    code.write_bytes(original_code)
    for name, payload in original_payloads.items():
        (ref.manifest_dir / name).write_bytes(payload)
    audit: list[Mapping[str, object]] = []
    restored = rollback(
        ref,
        expected_generation_id=second.current_generation_id,
        target_generation_id=initial.current_generation_id,
        actor="operator",
        reason="restore source and contract A",
        trusted_publics=trusted,
        audit_sink=audit.append,
        registry_reconciler=registry.append,
    )

    live = current_manifest(ref, trusted_publics=trusted)
    assert restored.current_generation_id == initial.current_generation_id
    assert live.generation_id == initial.current_generation_id
    assert live.verified_code_digest == (
        "sha256:" + hashlib.sha256(original_code).hexdigest()
    )
    assert _source_payloads(ref) == original_payloads
    assert len(audit) == 1


def test_retirement_is_signed_atomic_idempotent_and_source_independent(
    tmp_path: Path,
) -> None:
    _root, ref, private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    initial = publish_signed_source(
        ref,
        expected_generation_id=None,
        trusted_publics=trusted,
        store_root=store,
    )
    audit: list[Mapping[str, object]] = []

    retired = retire(
        ref,
        expected_generation_id=initial.current_generation_id,
        actor="operator",
        reason="remove synthesized executor",
        private_key=private,
        trusted_publics=trusted,
        audit_sink=audit.append,
        store_root=store,
    )
    state = current_contract(ref, trusted_publics=trusted, store_root=store)

    assert isinstance(state, ContractRetirement)
    assert current_revision_id(ref, store_root=store) == retired.current_generation_id
    assert state.retirement_id == retired.current_generation_id
    assert state.previous_generation_id == initial.current_generation_id
    assert state.actor == "operator"
    assert state.reason == "remove synthesized executor"
    with pytest.raises(ContractStoreError, match="contract_retired"):
        current_manifest(ref, trusted_publics=trusted, store_root=store)

    revision = _generation_path(store, ref, retired.current_generation_id)
    assert {path.name for path in revision.iterdir()} == set(RETIREMENT_FILES)
    shutil.rmtree(ref.manifest_dir)

    # Retirement, its retry and diagnostics never reopen deleted authoring
    # code.  The original generation remains immutable audit evidence.
    repeated = retire(
        ref,
        expected_generation_id=initial.current_generation_id,
        actor="operator",
        reason="remove synthesized executor",
        private_key=private,
        trusted_publics=trusted,
        audit_sink=audit.append,
        store_root=store,
    )
    assert repeated.repeated
    assert repeated.current_generation_id == retired.current_generation_id
    assert len(audit) == 1
    findings = diagnose_store((ref,), trusted_publics=trusted, store_root=store)
    assert findings == ()


def test_retirement_rejects_tampering_and_conflicting_retry(tmp_path: Path) -> None:
    _root, ref, private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    initial = publish_signed_source(
        ref,
        expected_generation_id=None,
        trusted_publics=trusted,
        store_root=store,
    )
    retired = retire(
        ref,
        expected_generation_id=initial.current_generation_id,
        actor="operator",
        reason="uninstall skill",
        private_key=private,
        trusted_publics=trusted,
        audit_sink=lambda _event: None,
        store_root=store,
    )
    with pytest.raises(ContractStoreError, match="commit_conflict"):
        retire(
            ref,
            expected_generation_id=initial.current_generation_id,
            actor="operator",
            reason="different authorization",
            private_key=private,
            trusted_publics=trusted,
            audit_sink=lambda _event: None,
            store_root=store,
        )

    revision = _generation_path(store, ref, retired.current_generation_id)
    (revision / "retirement.json.sig").write_bytes(b"tampered")
    with pytest.raises(ContractStoreError, match="retirement_signature_invalid"):
        current_manifest(ref, trusted_publics=trusted, store_root=store)


def test_retirement_audit_event_id_survives_crash_before_pointer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _root, ref, private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    initial = publish_signed_source(
        ref,
        expected_generation_id=None,
        trusted_publics=trusted,
        store_root=store,
    )
    real_install = contract_store_module._install_retirement
    interrupted = True
    deliveries: list[Mapping[str, object]] = []
    durable_by_id: dict[str, Mapping[str, object]] = {}

    def idempotent_sink(event: Mapping[str, object]) -> None:
        deliveries.append(dict(event))
        event_id = str(event["event_id"])
        durable_by_id.setdefault(event_id, dict(event))

    def fail_once(*args, **kwargs) -> None:
        nonlocal interrupted
        if interrupted:
            interrupted = False
            raise ContractStoreError("simulated_pre_pointer_crash")
        real_install(*args, **kwargs)

    monkeypatch.setattr(
        contract_store_module, "_install_retirement", fail_once,
    )
    with pytest.raises(ContractStoreError, match="simulated_pre_pointer_crash"):
        retire(
            ref,
            expected_generation_id=initial.current_generation_id,
            actor="operator",
            reason="stable audit retry",
            private_key=private,
            trusted_publics=trusted,
            audit_sink=idempotent_sink,
            store_root=store,
        )
    assert current_manifest(
        ref, trusted_publics=trusted, store_root=store,
    ).generation_id == initial.current_generation_id

    retired = retire(
        ref,
        expected_generation_id=initial.current_generation_id,
        actor="operator",
        reason="stable audit retry",
        private_key=private,
        trusted_publics=trusted,
        audit_sink=idempotent_sink,
        store_root=store,
    )

    assert not retired.repeated
    assert len(deliveries) == 2
    assert deliveries[0] == deliveries[1]
    assert len(durable_by_id) == 1


def test_retired_contract_requires_explicit_reactivation(tmp_path: Path) -> None:
    root, ref, private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    initial = publish_signed_source(
        ref,
        expected_generation_id=None,
        trusted_publics=trusted,
        store_root=store,
    )
    retired = retire(
        ref,
        expected_generation_id=initial.current_generation_id,
        actor="operator",
        reason="uninstall skill",
        private_key=private,
        trusted_publics=trusted,
        audit_sink=lambda _event: None,
        store_root=store,
    )

    # Reinstall the same ContractId with changed code and no inherited
    # signature.  Merely publishing it remains forbidden while retired.
    shutil.rmtree(ref.manifest_dir)
    directory = root / "sample"
    directory.mkdir(parents=True)
    code = directory / "sample.py"
    code.write_text(
        "def invoke(args):\n    return {'results': [], 'revision': 2}\n",
        encoding="utf-8",
    )
    digest = "sha256:" + hashlib.sha256(code.read_bytes()).hexdigest()
    manifest = directory / "manifest.toml"
    manifest.write_text(
        _manifest_text(
            name="read_files",
            code_file=code.name,
            code_digest=digest,
        ),
        encoding="utf-8",
    )
    parsed = tomllib.loads(manifest.read_text(encoding="utf-8"))
    (directory / "manifest.lang_state.json").write_bytes(
        encode_language_state(_state_for(parsed), manifest=parsed),
    )
    reinstalled = _inventory_ref(root, name="read_files")
    draft = prepare_technical_draft(reinstalled)

    with pytest.raises(ContractStoreError, match="contract_retired"):
        publish_technical_update(
            reinstalled,
            expected_generation_id=retired.current_generation_id,
            draft=draft,
            private_key=private,
            trusted_publics=trusted,
            store_root=store,
        )

    audit: list[Mapping[str, object]] = []
    reactivated = reactivate_technical_update(
        reinstalled,
        expected_retirement_id=retired.current_generation_id,
        draft=draft,
        actor="operator",
        reason="reinstall reviewed skill",
        private_key=private,
        trusted_publics=trusted,
        audit_sink=audit.append,
        store_root=store,
    )
    repeated = reactivate_technical_update(
        reinstalled,
        expected_retirement_id=retired.current_generation_id,
        draft=draft,
        actor="operator",
        reason="reinstall reviewed skill",
        private_key=private,
        trusted_publics=trusted,
        audit_sink=audit.append,
        store_root=store,
    )

    assert reactivated.operation == "reactivate_technical_update"
    assert repeated.repeated
    assert repeated.current_generation_id == reactivated.current_generation_id
    assert len(audit) == 1
    live = current_manifest(
        reinstalled, trusted_publics=trusted, store_root=store,
    )
    assert live.generation_id == reactivated.current_generation_id
    assert live.verified_code_digest == digest


def test_rollback_can_explicitly_restore_a_retired_generation(tmp_path: Path) -> None:
    _root, ref, private, trusted = _create_source(tmp_path)
    store = tmp_path / "store"
    initial = publish_signed_source(
        ref,
        expected_generation_id=None,
        trusted_publics=trusted,
        store_root=store,
    )
    retired = retire(
        ref,
        expected_generation_id=initial.current_generation_id,
        actor="operator",
        reason="temporary removal",
        private_key=private,
        trusted_publics=trusted,
        audit_sink=lambda _event: None,
        store_root=store,
    )
    audit: list[Mapping[str, object]] = []

    restored = rollback(
        ref,
        expected_generation_id=retired.current_generation_id,
        target_generation_id=initial.current_generation_id,
        actor="operator",
        reason="restore exact previous version",
        trusted_publics=trusted,
        audit_sink=audit.append,
        store_root=store,
    )

    assert restored.current_generation_id == initial.current_generation_id
    assert current_manifest(
        ref, trusted_publics=trusted, store_root=store,
    ).generation_id == initial.current_generation_id
    assert audit[0]["event"] == "contract_generation_rollback"


def test_productive_retirement_requires_reconciliation_and_repairs_on_retry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _root, ref, private, trusted, shadow, initial = _create_productive_shadow(
        tmp_path, monkeypatch,
    )
    activate_store(
        {ref.contract_id: initial.current_generation_id},
        shadow_root=shadow,
        trusted_publics=trusted,
        quiescence_guard=lambda: True,
    )
    productive = (
        contract_store_module._C.PATH_USER_STATE
        / "contract-publications"
        / "v1"
    )
    audit: list[Mapping[str, object]] = []

    with pytest.raises(ContractStoreError, match="registry_reconciler_required"):
        retire(
            ref,
            expected_generation_id=initial.current_generation_id,
            actor="operator",
            reason="uninstall skill",
            private_key=private,
            trusted_publics=trusted,
            audit_sink=audit.append,
        )
    assert current_manifest(
        ref, trusted_publics=trusted, store_root=productive,
    ).generation_id == initial.current_generation_id

    def interrupted_reconcile(_retirement: ContractRetirement) -> None:
        raise RuntimeError("registry unavailable")

    with pytest.raises(RuntimeError, match="registry unavailable"):
        retire(
            ref,
            expected_generation_id=initial.current_generation_id,
            actor="operator",
            reason="uninstall skill",
            private_key=private,
            trusted_publics=trusted,
            audit_sink=audit.append,
            registry_reconciler=interrupted_reconcile,
        )
    committed = current_contract(
        ref, trusted_publics=trusted, store_root=productive,
    )
    assert isinstance(committed, ContractRetirement)

    reconciled: list[ContractRetirement] = []
    repaired = retire(
        ref,
        expected_generation_id=initial.current_generation_id,
        actor="operator",
        reason="uninstall skill",
        private_key=private,
        trusted_publics=trusted,
        audit_sink=audit.append,
        registry_reconciler=reconciled.append,
    )
    assert repaired.repeated
    assert len(audit) == 1
    assert reconciled == [committed]


def test_store_only_marker_recovery_accepts_authenticated_retirement_history(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _root, ref, private, trusted, shadow, initial = _create_productive_shadow(
        tmp_path, monkeypatch,
    )
    expected = {ref.contract_id: initial.current_generation_id}
    activate_store(
        expected,
        shadow_root=shadow,
        trusted_publics=trusted,
        quiescence_guard=lambda: True,
    )
    retired = retire(
        ref,
        expected_generation_id=initial.current_generation_id,
        actor="operator",
        reason="remove before marker recovery",
        private_key=private,
        trusted_publics=trusted,
        audit_sink=lambda _event: None,
        registry_reconciler=lambda _revision: None,
    )
    shutil.rmtree(ref.manifest_dir)
    marker = (
        contract_store_module._C.PATH_USER_STATE
        / "contract-publications.ACTIVE"
    )
    marker.unlink()
    assert production_store_mode() is ProductionStoreMode.STORE_ONLY

    # The cutover report contains the original generation ID.  Recovery owns
    # only the identity set after cutover and accepts the newer authenticated
    # tombstone plus its manifest history.
    activate_store(
        expected,
        shadow_root=shadow,
        trusted_publics=trusted,
        quiescence_guard=lambda: True,
    )

    assert production_store_mode() is ProductionStoreMode.ACTIVE
    current = current_contract(ref, trusted_publics=trusted)
    assert isinstance(current, ContractRetirement)
    assert current.retirement_id == retired.current_generation_id


def test_registry_converges_when_manifest_callbacks_finish_in_reverse_order(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _root, ref, private, trusted, shadow, initial = _create_productive_shadow(
        tmp_path, monkeypatch,
    )
    activate_store(
        {ref.contract_id: initial.current_generation_id},
        shadow_root=shadow,
        trusted_publics=trusted,
        quiescence_guard=lambda: True,
    )
    base = current_manifest(ref, trusted_publics=trusted)
    patch = _localization_patch(
        base,
        selector="description",
        source_language="it",
        target_language="en",
        candidate=(
            "SCOPO: callback race. PATTERN: sample(). "
            "NON: modify. OUT: results=[]."
        ),
    )
    registry: dict[str, str] = {}
    calls: list[tuple[str, str]] = []
    triggered = False

    def inner(revision) -> None:
        identifier = contract_revision_id(revision)
        registry[ref.contract_id.value] = identifier
        calls.append(("inner", identifier))

    def outer(revision) -> None:
        nonlocal triggered
        identifier = contract_revision_id(revision)
        if not triggered:
            triggered = True
            rollback(
                ref,
                expected_generation_id=identifier,
                target_generation_id=initial.current_generation_id,
                actor="concurrent operator",
                reason="win manifest race",
                trusted_publics=trusted,
                audit_sink=lambda _event: None,
                registry_reconciler=inner,
            )
        # Deliberately finish A after B and overwrite the simulated registry.
        registry[ref.contract_id.value] = identifier
        calls.append(("outer", identifier))

    with pytest.raises(ContractStoreError, match="publication_superseded"):
        publish_localization(
            ref,
            expected_generation_id=initial.current_generation_id,
            source_language="it",
            target_language="en",
            patches=(patch,),
            private_key=private,
            trusted_publics=trusted,
            registry_reconciler=outer,
        )

    assert registry[ref.contract_id.value] == initial.current_generation_id
    assert calls[-1] == ("outer", initial.current_generation_id)
    assert current_manifest(
        ref, trusted_publics=trusted,
    ).generation_id == initial.current_generation_id


def test_registry_converges_when_manifest_callback_finishes_after_retirement(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _root, ref, private, trusted, shadow, initial = _create_productive_shadow(
        tmp_path, monkeypatch,
    )
    activate_store(
        {ref.contract_id: initial.current_generation_id},
        shadow_root=shadow,
        trusted_publics=trusted,
        quiescence_guard=lambda: True,
    )
    base = current_manifest(ref, trusted_publics=trusted)
    patch = _localization_patch(
        base,
        selector="description",
        source_language="it",
        target_language="en",
        candidate=(
            "SCOPO: cross-type race. PATTERN: sample(). "
            "NON: modify. OUT: results=[]."
        ),
    )
    registry: dict[str, tuple[str, str]] = {}
    calls: list[tuple[str, str, str]] = []
    triggered = False

    def assign(label: str, revision) -> None:
        kind = (
            "retirement"
            if isinstance(revision, ContractRetirement)
            else "manifest"
        )
        identifier = contract_revision_id(revision)
        registry[ref.contract_id.value] = (kind, identifier)
        calls.append((label, kind, identifier))

    def inner(revision) -> None:
        assign("inner", revision)

    def outer(revision) -> None:
        nonlocal triggered
        if not triggered:
            triggered = True
            assert isinstance(revision, contract_store_module.VerifiedManifest)
            retire(
                ref,
                expected_generation_id=str(revision.generation_id),
                actor="concurrent operator",
                reason="win cross-type race",
                private_key=private,
                trusted_publics=trusted,
                audit_sink=lambda _event: None,
                registry_reconciler=inner,
            )
        # Deliberately complete the stale manifest callback after retirement.
        assign("outer", revision)

    with pytest.raises(ContractStoreError, match="publication_superseded"):
        publish_localization(
            ref,
            expected_generation_id=initial.current_generation_id,
            source_language="it",
            target_language="en",
            patches=(patch,),
            private_key=private,
            trusted_publics=trusted,
            registry_reconciler=outer,
        )

    current = current_contract(ref, trusted_publics=trusted)
    assert isinstance(current, ContractRetirement)
    assert registry[ref.contract_id.value] == (
        "retirement", current.retirement_id,
    )
    assert calls[-1] == ("outer", "retirement", current.retirement_id)
