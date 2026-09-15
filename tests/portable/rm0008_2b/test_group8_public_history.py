"""Historical verification consumes public evidence, not signing material."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import executor_birth_context_v1 as catalog
import executor_birth_ownership_chain as chain_module
import executor_birth_prepared_root as root
from executor_birth_keystore import birth_key_id
from executor_birth_prepared_set import (
    SET_ID_DIGEST_DOMAIN_V1, PreparedSetError, is_prepared_set_v1,
    _decode, load_historical_marker_public_set_v1, load_historical_public_set_v1,
)
from . import support


pytestmark = pytest.mark.skipif(os.name == "nt", reason=support.POSIX_SCENARIO_ONLY_V1)


def _prepared(tmp_path: Path, monkeypatch):
    base = support.make_config(
        tmp_path, author=Ed25519PrivateKey.generate(), operator=True,
    )
    support.provision(monkeypatch, base)
    support.use_config(monkeypatch, base)
    return base


def _read_public():
    with root.open_prepared_root_session_v1() as session:
        with session.global_lock(exclusive=False, create=False):
            return load_historical_marker_public_set_v1(session)


def _rewrite(path, mutate):
    document = json.loads(path.read_bytes())
    mutate(document)
    support.write(path, support.canonical_json(document), 0o644)


def _rewrite_pinned_set(base, mutate):
    """Supply consistent outer pins to exercise deeper semantic validation.

    This is fixture preparation, not a way to change an authenticated chain.
    """
    installed = support.installed_set(base)
    document = json.loads((installed / "set.json").read_bytes())
    mutate(document)
    document.pop("set_id")
    set_id = hashlib.sha256(
        SET_ID_DIGEST_DOMAIN_V1 + support.canonical_json(document),
    ).hexdigest()
    document["set_id"] = set_id
    payload = support.canonical_json(document)
    support.write(installed / "set.json", payload, 0o644)
    installed.rename(installed.parent / set_id)
    _rewrite(support.installed_marker(base), lambda marker: marker.update(
        set_id=set_id, authority_set=f"authority-sets/{set_id}",
        set_json_sha256=hashlib.sha256(payload).hexdigest(),
    ))


def _digest(character):
    return "sha256:" + character * 64


def _chain_boundary_fixture(base, monkeypatch, **transition_changes):
    """Stub only the authenticated chain boundary; use real set/filesystem reads.

    The synthetic head is not a cryptographic or installed-chain proof.
    """
    from executor_birth_context_transition import issue_context_transition_v1
    from executor_birth_cutover import CurrentInventoryV1

    document = json.loads((support.installed_set(base) / "set.json").read_bytes())
    marker = json.loads(support.installed_marker(base).read_bytes())
    values = dict(
        request_id=_digest("1"), closed_build_id=_digest("2"),
        previous_cutover_id=_digest("3"), previous_set_id="4" * 64,
        previous_admission_context_id=_digest("5"), previous_context_epoch=_digest("6"),
        set_id=marker["set_id"],
        prepared_admission_context_id=document["prepared_admission_context_id"],
        prepared_context_epoch=document["prepared_context_epoch"],
        context_material_sha256=marker["context_material_sha256"],
        set_json_sha256=marker["set_json_sha256"],
        current_inventory=CurrentInventoryV1(()),
    )
    values.update(transition_changes)
    _encoded, transition = issue_context_transition_v1(**values)
    head = chain_module.OwnershipHead(
        1, _digest("7"), _digest("2"), None, _digest("8"),
        document["author_active_key_id"], b"fixture-head", b"",
    )
    chain = chain_module.VerifiedOwnershipChain(
        _digest("7"), (head,), context_transitions=(transition,),
    )
    monkeypatch.setattr(chain_module, "inspect_ownership_chain_state_v1", lambda: chain)
    return chain


def test_historical_material_is_independent_of_the_current_catalog(
    tmp_path, monkeypatch,
):
    historical_catalog = tuple(
        (name, "historical-version", files, state)
        for name, _version, files, state in catalog.CONTEXT_CATALOG_V1
    )
    monkeypatch.setattr(catalog, "CONTEXT_CATALOG_V1", historical_catalog)
    base = _prepared(tmp_path, monkeypatch)
    document = json.loads((support.installed_set(base) / "set.json").read_bytes())
    monkeypatch.setattr(catalog, "CONTEXT_CATALOG_V1", ())
    import executor_birth_producer_table_v1 as producer_table

    monkeypatch.setattr(producer_table, "PRODUCER_AUTHOR_V1", {})
    monkeypatch.setattr(
        catalog, "prepare_context_material_v1",
        lambda *_args, **_kwargs: pytest.fail("history rebuilt from current source"),
    )

    observed = _read_public()
    assert observed.material.pin.admission_context_id == (
        document["prepared_admission_context_id"]
    )
    assert observed.material.context.authority_registry.version == "historical-version"


def test_ownership_exclusion_needs_no_private_keys_after_author_rotation(
    tmp_path, monkeypatch,
):
    from executor_birth_ownership_authorities import _birth_public_keys_v1
    from executor_birth_secure_fs import _SecureRootSession

    base = _prepared(tmp_path, monkeypatch)
    expected = root._historical_birth_public_inventory_v1()
    author_root = base / "birth" / "author-root-v1"
    configuration = json.loads((author_root / "keystore.json").read_bytes())
    replacement = Ed25519PrivateKey.generate()
    replacement_public = support.public_bytes(replacement)
    replacement_id = birth_key_id(replacement_public)
    support.write(author_root / "public" / f"{replacement_id}.pub", replacement_public, 0o644)
    support.write(
        author_root / "private" / f"{replacement_id}.key",
        support.private_bytes(replacement), 0o600,
    )
    (author_root / configuration["private_file"]).unlink()
    for key in configuration["keys"]:
        key["status"] = "verifier"
    configuration["keys"].append({
        "key_id": replacement_id, "public_file": f"public/{replacement_id}.pub",
        "status": "active",
    })
    configuration["keys"].sort(key=lambda key: key["key_id"])
    configuration.update(
        active_key_id=replacement_id, private_file=f"private/{replacement_id}.key",
        config_revision=configuration["config_revision"] + 1,
    )
    support.write(author_root / "keystore.json", support.canonical_json(configuration), 0o600)

    reads = []
    read = _SecureRootSession.read_file

    def public_read(session, components, **kwargs):
        assert "private" not in components and "keystore.json" not in components
        reads.append(components)
        return read(session, components, **kwargs)

    monkeypatch.setattr(_SecureRootSession, "read_file", public_read)
    private_directories = tuple((base / "birth").rglob("private"))
    try:
        for directory in private_directories:
            directory.chmod(0)
        assert _birth_public_keys_v1() == expected | {replacement_public}
        # The new author is excluded from Ownership use, but cannot verify an
        # older act merely because its public file is now present.
        assert replacement_id not in _read_public().author_verifier_keys
    finally:
        for directory in private_directories:
            directory.chmod(0o700)
    assert reads


@pytest.mark.parametrize("case", (
    "set", "material", "public-key", "approval-registry", "marker",
    "source-inventory", "producer-namespace", "duplicate-key-id", "schema-type",
))
def test_historical_public_read_rejects_altered_bindings(tmp_path, monkeypatch, case):
    base = _prepared(tmp_path, monkeypatch)
    installed = support.installed_set(base)
    if case == "set":
        _rewrite(installed / "set.json", lambda value: value.update(provisioner_build_id="other"))
    elif case == "material":
        path = installed / "context" / "material-v1.json"
        support.write(path, path.read_bytes() + b" ", 0o644)
    elif case == "public-key":
        path = next((installed / "admission" / "public").glob("*.pub"))
        support.write(path, support.public_bytes(Ed25519PrivateKey.generate()), 0o644)
    elif case == "approval-registry":
        _rewrite(installed / "approval" / "authority.json", lambda value: value.update(revision=99))
    elif case == "marker":
        _rewrite(support.installed_marker(base), lambda value: value.update(transaction_id="0" * 32))
    elif case == "source-inventory":
        _rewrite_pinned_set(base, lambda value: value.update(context_source_inventory_sha256="0" * 64))
    elif case == "schema-type":
        _rewrite_pinned_set(base, lambda value: value.update(schema_version=True))
    elif case == "duplicate-key-id":
        _rewrite_pinned_set(base, lambda value: value["author_verifier_key_ids"].append(
            value["author_active_key_id"],
        ))
    else:
        def wrong_namespace(document):
            producers = document["producer_keys"]
            producers["different_producer:operation"] = producers.pop(next(iter(producers)))

        _rewrite_pinned_set(base, wrong_namespace)

    with pytest.raises(PreparedSetError) as error:
        _read_public()
    assert error.value.code in {"birth_prepared_set_invalid", "birth_prepared_set_mismatch"}
    assert error.value.__cause__ is None


@pytest.mark.parametrize("case", (
    "noncanonical", "unknown-field", "schema-type", "invalid-digest",
    "source-path", "registry-digest", "context-id", "context-epoch",
))
def test_historical_material_rejects_inconsistent_records(tmp_path, monkeypatch, case):
    base = _prepared(tmp_path, monkeypatch)
    encoded = (support.installed_set(base) / "context" / "material-v1.json").read_bytes()
    document = json.loads(encoded)
    if case == "unknown-field":
        document["untrusted"] = True
    elif case == "schema-type":
        document["schema_version"] = True
    elif case == "invalid-digest":
        document["components"]["standard"]["component_digest"] = "not-a-digest"
    elif case == "source-path":
        document["components"]["standard"]["files"][0]["label"] = "../outside"
    elif case == "registry-digest":
        document["components"]["authority_registry"]["configuration"]["registry"]["extra"] = {}
    elif case == "context-id":
        document["prepared_admission_context_id"] = _digest("0")
    elif case == "context-epoch":
        document["prepared_context_epoch"] = _digest("0")
    encoded = support.canonical_json(document) + (b"\n" if case == "noncanonical" else b"")
    with pytest.raises(catalog.ContextMaterialError, match="birth_prepared_set_invalid"):
        catalog.decode_historical_context_material_v1(encoded)


def test_exact_historical_context_is_inert_immutable_and_read_only(tmp_path, monkeypatch):
    from executor_birth_context_selection import is_context_selection_v1
    from executor_birth_secure_fs import _SecureRootSession

    base = _prepared(tmp_path, monkeypatch)
    chain = _chain_boundary_fixture(base, monkeypatch)
    transition = chain.context_transitions[0]
    # The transition selects an exact set, never the historical marker.
    _rewrite(support.installed_marker(base), lambda marker: marker.update(set_id="0" * 64))

    def snapshot():
        return {
            str(path.relative_to(base)): (path.stat().st_mtime_ns, path.read_bytes())
            for path in base.rglob("*") if path.is_file()
        }

    before = snapshot()
    for operation in (
        "create_file_exclusive", "create_directory_exclusive",
        "rename_no_replace", "dispose_transaction_object",
    ):
        monkeypatch.setattr(
            _SecureRootSession, operation,
            lambda *_args, **_kwargs: pytest.fail("historical reader attempted a write"),
        )
    observed = root.load_historical_context_verifiers_v1(
        transition.prepared_admission_context_id,
    )
    assert observed.required_head_id == chain.required_head.head_id
    assert observed.transition_id == transition.transition_id
    assert observed.public_set.set_json_sha256 == transition.set_json_sha256
    assert observed.public_set.material.pin.context_epoch == transition.prepared_context_epoch
    assert not is_prepared_set_v1(observed.public_set)
    assert not is_context_selection_v1(observed)
    assert not isinstance(observed, root.SealedAuthoritiesV1)
    with pytest.raises(FrozenInstanceError):
        observed.required_head_id = _digest("0")
    with pytest.raises(FrozenInstanceError):
        observed.public_set.material.context.standard.version = "other"
    for mapping in (
        observed.public_set.author_verifier_keys, observed.public_set.admission_verifier_keys,
        observed.public_set.producers,
        next(iter(observed.public_set.producers.values())).verifier_keys,
    ):
        with pytest.raises(TypeError):
            mapping["other"] = None
    assert snapshot() == before


@pytest.mark.parametrize("case", (
    "unknown", "invalid", "no-transition", "ambiguous", "epoch",
    "head-changed", "records-changed", "transitions-changed",
))
def test_context_selector_rejects_missing_ambiguous_or_changing_evidence(
    tmp_path, monkeypatch, case,
):
    base = _prepared(tmp_path, monkeypatch)
    changes = {"prepared_context_epoch": _digest("0")} if case == "epoch" else {}
    chain = _chain_boundary_fixture(base, monkeypatch, **changes)
    selector = chain.context_transitions[0].prepared_admission_context_id
    if case == "unknown":
        selector = _digest("0")
    elif case == "invalid":
        selector = "../not-a-context"
    elif case == "no-transition":
        chain = replace(chain, context_transitions=())
    elif case == "ambiguous":
        other = _chain_boundary_fixture(base, monkeypatch, request_id=_digest("9"))
        chain = replace(chain, context_transitions=chain.context_transitions + other.context_transitions)
    after = chain
    if case == "head-changed":
        after = replace(chain, heads=(replace(chain.required_head, head_id=_digest("9")),))
    elif case == "records-changed":
        after = replace(chain, authenticated_records=(b"different-boundary-record",))
    elif case == "transitions-changed":
        after = replace(chain, context_transitions=())
    observations = iter((chain, after))
    monkeypatch.setattr(
        chain_module, "inspect_ownership_chain_state_v1", lambda: next(observations),
    )
    with pytest.raises(root.PreparedRootError) as error:
        root.load_historical_context_verifiers_v1(selector)
    expected = (
        "birth_context_selection_changed" if case.endswith("-changed")
        else "birth_context_transition_required" if case == "no-transition"
        else "birth_context_selection_invalid"
    )
    assert error.value.code == expected


def test_historical_set_requires_the_held_barrier_and_both_owner_pins(tmp_path, monkeypatch):
    base = _prepared(tmp_path, monkeypatch)
    marker = json.loads(support.installed_marker(base).read_bytes())
    with root.open_prepared_root_session_v1() as session:
        with pytest.raises(PreparedSetError, match="birth_provisioning_lock_unsafe"):
            load_historical_marker_public_set_v1(session)
        with session.global_lock(exclusive=False, create=False):
            for missing in ("expected_set_json_sha256", "expected_context_material_sha256"):
                pins = {
                    "expected_set_json_sha256": marker["set_json_sha256"],
                    "expected_context_material_sha256": marker["context_material_sha256"],
                }
                pins[missing] = None
                with pytest.raises(PreparedSetError, match="birth_prepared_set_invalid"):
                    load_historical_public_set_v1(session, marker["set_id"], **pins)


@pytest.mark.parametrize("encoded", (
    b'{"invalid":NaN}', b'{"invalid":"\\ud800"}', b"[" * 10000 + b"]" * 10000,
), ids=("nonfinite", "surrogate", "deep"))
def test_public_document_decoder_reports_malformed_input_consistently(encoded):
    with pytest.raises(PreparedSetError, match="birth_prepared_set_invalid"):
        _decode(encoded)
