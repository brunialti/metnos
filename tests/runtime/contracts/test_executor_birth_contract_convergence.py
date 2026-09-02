from __future__ import annotations

import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
import tomllib

import pytest

import install.executor_birth_contract_convergence as convergence
from i18n_materializer import decode_language_state, encode_language_state
from manifest_inventory import ContractId, ManifestOrigin


def _authoring(root: Path):
    directory = root / "sample"
    directory.mkdir(parents=True)
    code = b"def invoke(args):\n    return args\n"
    (directory / "main.py").write_bytes(code)
    digest = "sha256:" + hashlib.sha256(code).hexdigest()
    manifest = (
        'name = "sample"\n'
        'version = "1.0.0"\n'
        'description = "sample"\n'
        '[code]\n'
        'files = ["main.py"]\n'
        f'digest = "{digest}"\n'
    ).encode()
    (directory / "manifest.toml").write_bytes(manifest)
    parsed = tomllib.loads(manifest.decode())
    (directory / "manifest.lang_state.json").write_bytes(
        encode_language_state(
            {"schema_version": 1, "selectors": {}}, manifest=parsed,
        )
    )
    (directory / "manifest.toml.sig").write_bytes(b"historical-signature")
    return SimpleNamespace(
        manifest_dir=directory,
        contract_id=ContractId(ManifestOrigin.CORE, "sample/manifest.toml"),
    )


def test_historical_receipt_uses_a_deterministic_packaging_revision(
    tmp_path: Path,
) -> None:
    ref = _authoring(tmp_path / "source")
    exact = convergence._candidate_for_transition(
        ref, tmp_path / "exact", packaging_revision=False,
    )
    revised = convergence._candidate_for_transition(
        ref, tmp_path / "revised", packaging_revision=True,
    )

    assert tomllib.loads((exact / "manifest.toml").read_text())["version"] == "1.0.0"
    revised_manifest = tomllib.loads((revised / "manifest.toml").read_text())
    assert revised_manifest["version"] == "1.0.1"
    decode_language_state(
        (revised / "manifest.lang_state.json").read_bytes(),
        manifest=revised_manifest,
    )


def _convergence_environment(monkeypatch, tmp_path: Path):
    import contract_store
    import executor_birth_bootstrap
    import executor_birth_intent
    import executor_birth_prepared_root
    import manifest_inventory

    contract_id = ContractId(ManifestOrigin.CORE, "sample/manifest.toml")
    ref = SimpleNamespace(contract_id=contract_id)
    inventory = SimpleNamespace(
        problems=(), by_id=lambda: {contract_id: ref},
    )
    monkeypatch.setattr(os, "geteuid", lambda: 991)
    monkeypatch.setenv("METNOS_INSTALL_ROOT", str(convergence._REPOSITORY))
    monkeypatch.setattr(
        manifest_inventory, "inventory_authoring_manifests", lambda: inventory,
    )
    monkeypatch.setattr(
        manifest_inventory, "inventory_store_manifests", lambda **_kwargs: inventory,
    )
    store_root = tmp_path / "store"
    monkeypatch.setattr(
        contract_store, "_production_paths",
        lambda: (store_root.parent, store_root, store_root.parent / "ACTIVE"),
    )
    monkeypatch.setattr(contract_store, "current_revision_id", lambda *_args, **_kwargs: "g")
    monkeypatch.setattr(
        executor_birth_prepared_root, "load_sealed_authorities_v1",
        lambda: SimpleNamespace(
            author=SimpleNamespace(verifier_keys={}),
            admission=SimpleNamespace(verifier_keys={}),
        ),
    )
    monkeypatch.setattr(executor_birth_intent, "require_birth_intent_adapter", lambda: None)
    monkeypatch.setattr(
        executor_birth_bootstrap, "_build_initial_transition_installer_runtime_v1",
        lambda: SimpleNamespace(
            submit=lambda intent: executor_birth_intent.submit_installer_birth(intent),
        ),
    )
    monkeypatch.setattr(
        contract_store,
        "materialize_repository_authoring_for_transition_v1",
        lambda **_kwargs: 1,
    )
    return ref


def _write_candidate(destination: Path, manifest: bytes, state: bytes) -> Path:
    destination.mkdir()
    (destination / "manifest.toml").write_bytes(manifest)
    (destination / "manifest.lang_state.json").write_bytes(state)
    return destination


def test_exact_current_contract_is_not_revised_or_republished(
    tmp_path: Path, monkeypatch,
) -> None:
    import contract_store
    import executor_birth_intent

    _convergence_environment(monkeypatch, tmp_path)
    manifest = b'exact = true\n'
    state = b'{}\n'
    monkeypatch.setattr(
        convergence, "_candidate_for_transition",
        lambda _ref, destination, *, packaging_revision:
        _write_candidate(destination, manifest, state)
        if packaging_revision is False
        else pytest.fail("exact current contract was revised"),
    )
    monkeypatch.setattr(
        convergence, "_source_generation_has_historical_receipt",
        lambda *_args, **_kwargs: pytest.fail("historical receipt was consulted"),
    )
    monkeypatch.setattr(
        contract_store, "_load_generation",
        lambda *_args, **_kwargs: SimpleNamespace(
            manifest_bytes=manifest, language_state_bytes=state,
        ),
    )
    monkeypatch.setattr(
        executor_birth_intent, "submit_installer_birth",
        lambda _intent: pytest.fail("exact contract was republished"),
    )

    assert convergence.converge() == {
        "changed": 0, "current": 1, "examined": 1,
    }


def test_mismatch_with_historical_receipt_publishes_only_the_revision(
    tmp_path: Path, monkeypatch,
) -> None:
    import contract_store
    import executor_birth_intent

    _convergence_environment(monkeypatch, tmp_path)
    exact = b'version = "1.0.0"\n'
    revised = b'version = "1.0.1"\n'
    state = b'{}\n'

    def candidate(_ref, destination, *, packaging_revision):
        return _write_candidate(
            destination, revised if packaging_revision else exact, state,
        )

    monkeypatch.setattr(convergence, "_candidate_for_transition", candidate)
    monkeypatch.setattr(
        convergence, "_source_generation_has_historical_receipt",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        contract_store, "_load_generation",
        lambda *_args, **_kwargs: SimpleNamespace(
            manifest_bytes=b'old = true\n', language_state_bytes=state,
        ),
    )
    published: list[bytes] = []

    def submit(intent):
        published.append((intent.candidate_source_root / "manifest.toml").read_bytes())
        return SimpleNamespace(error_code=None, publication=object())

    monkeypatch.setattr(executor_birth_intent, "submit_installer_birth", submit)

    assert convergence.converge() == {
        "changed": 1, "current": 0, "examined": 1,
    }
    assert published == [revised]


def test_corrupt_current_generation_is_not_treated_as_a_mismatch(
    tmp_path: Path, monkeypatch,
) -> None:
    import contract_store

    _convergence_environment(monkeypatch, tmp_path)
    monkeypatch.setattr(
        convergence, "_candidate_for_transition",
        lambda _ref, destination, *, packaging_revision:
        _write_candidate(destination, b'exact = true\n', b'{}\n'),
    )

    def corrupt(*_args, **_kwargs):
        raise contract_store.ContractStoreError("signature_invalid")

    monkeypatch.setattr(contract_store, "_load_generation", corrupt)
    with pytest.raises(contract_store.ContractStoreError, match="signature_invalid"):
        convergence.converge()
