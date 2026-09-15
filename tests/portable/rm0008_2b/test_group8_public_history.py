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

_POLICY_PATH = "runtime/executor_birth_producer_table_v1.py"


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


def _producer_policy_fixture(tmp_path, monkeypatch, *, source_transform=None):
    """Real signed files/public sets; chain and admin metadata remain test seams."""
    import executor_birth_distribution_manifest as distribution
    import executor_birth_ownership_authorities as authorities
    import test_executor_birth_distribution_manifest as distribution_support

    base = _prepared(tmp_path / "prepared", monkeypatch)
    source = (Path(__file__).parents[3] / _POLICY_PATH).read_bytes()
    if source_transform is not None:
        source = source_transform(source)
    private, key_id, registry = distribution_support._authority(distribution.PURPOSE)
    release = tmp_path / "release"
    _value, encoded, signature = distribution_support._manifest(
        release, private, key_id,
        files_mutate=lambda files, path: distribution_support._add_declared_file(
            files, path, _POLICY_PATH, "runtime_code", source,
        ),
    )
    verified = distribution._verify_distribution_manifest_for_test(
        encoded, signature, registry=registry,
        _environment=distribution_support._test_environment(release),
    )
    monkeypatch.setattr(
        authorities, "load_ownership_public_registries_v1",
        lambda: distribution_support._fixed_public_bundle(private),
    )
    record = distribution.authenticate_distribution_record_v1(encoded, signature)
    chain = _chain_boundary_fixture(
        base, monkeypatch, closed_build_id=record.closed_build_id,
    )
    chain = replace(
        chain, heads=(replace(chain.required_head, closed_build_id=record.closed_build_id),),
        authenticated_records=(record,), required_distribution=verified,
    )
    monkeypatch.setattr(chain_module, "inspect_ownership_chain_state_v1", lambda: chain)
    open_anchor = distribution._open_distribution_tree_anchor_v1
    monkeypatch.setattr(
        distribution, "_open_distribution_tree_anchor_v1",
        lambda path, *, administrative: open_anchor(path, administrative=False),
    )
    return chain, release, source


def test_historical_producer_authors_are_public_data_not_loaded_policy(tmp_path, monkeypatch):
    import executor_birth_producer_table_v1 as table

    chain, release, source = _producer_policy_fixture(tmp_path, monkeypatch)
    expected = {":".join(key): value.value for key, value in table.PRODUCER_AUTHOR_V1.items()}
    expected_origins = {
        key.value: value.value for key, value in table._MANIFEST_ORIGIN_TO_EXECUTOR_V1.items()
    }
    monkeypatch.setattr(table, "PRODUCER_AUTHOR_V1", {})
    monkeypatch.setattr(table, "_MANIFEST_ORIGIN_TO_EXECUTOR_V1", {})
    monkeypatch.setattr(
        table, "producer_author_v1",
        lambda *_args: pytest.fail("historical declaration executed as current policy"),
    )
    monkeypatch.setattr(
        table, "executor_origin_v1",
        lambda *_args: pytest.fail("historical origin executed as current policy"),
    )
    observed = root.load_historical_producer_declarations_v1(
        chain.context_transitions[0].prepared_admission_context_id,
    )
    assert observed.authors == expected
    assert observed.executor_origins == expected_origins
    assert observed.context.required_head_id == chain.required_head.head_id
    assert observed.closed_build_id == chain.authenticated_records[0].closed_build_id
    assert observed.source_path == _POLICY_PATH
    assert (release / _POLICY_PATH).read_bytes() == source
    with pytest.raises(TypeError):
        observed.authors["other:operation"] = "human"
    with pytest.raises(TypeError):
        observed.executor_origins["core"] = "human"
    with pytest.raises(FrozenInstanceError):
        observed.closed_build_id = _digest("0")


@pytest.mark.parametrize("same_source", (True, False))
def test_historical_author_projection_selects_the_old_build_not_the_latest(
    tmp_path, monkeypatch, same_source,
):
    import config
    import executor_birth_distribution_manifest as distribution
    import test_executor_birth_distribution_manifest as distribution_support

    original, _release, source = _producer_policy_fixture(tmp_path, monkeypatch)
    private, key_id, registry = distribution_support._authority(distribution.PURPOSE)
    latest_root = tmp_path / "next-release"
    _value, encoded, signature = distribution_support._manifest(
        latest_root, private, key_id,
        mutate=lambda value: value.update(
            release_sequence=2, previous_closed_build_id=original.required_head.closed_build_id,
        ),
        files_mutate=lambda files, path: distribution_support._add_declared_file(
            files, path, _POLICY_PATH, "runtime_code",
            source if same_source else source + b"\n# Different future source.\n",
        ),
    )
    latest = distribution.authenticate_distribution_record_v1(encoded, signature)
    verified = distribution._verify_distribution_manifest_for_test(
        encoded, signature, registry=registry,
        _environment=distribution_support._test_environment(latest_root),
    )
    later = _chain_boundary_fixture(
        config.PATH_USER_CONFIG, monkeypatch, closed_build_id=latest.closed_build_id,
        request_id=_digest("9"), prepared_admission_context_id=_digest("a"),
    )
    chain = replace(
        original,
        heads=original.heads + (replace(
            later.required_head, closed_build_id=latest.closed_build_id,
            release_sequence=2, head_id=_digest("b"), previous_head_id=original.required_head.head_id,
        ),),
        authenticated_records=original.authenticated_records + (latest,),
        context_transitions=original.context_transitions + later.context_transitions,
        required_distribution=verified,
    )
    monkeypatch.setattr(chain_module, "inspect_ownership_chain_state_v1", lambda: chain)
    selector = original.context_transitions[0].prepared_admission_context_id
    if not same_source:
        with pytest.raises(root.PreparedRootError, match="birth_context_producer_policy_invalid"):
            root.load_historical_producer_declarations_v1(selector)
    else:
        observed = root.load_historical_producer_declarations_v1(selector)
        assert observed.closed_build_id == original.required_head.closed_build_id
        assert observed.context.required_head_id == chain.required_head.head_id


@pytest.mark.parametrize("source", (
    b"", b"x" * (1024 * 1024 + 1), b"(invalid", b"OTHER = {}",
    b"PRODUCER_AUTHOR_V1 = {}",
    b"PRODUCER_AUTHOR_V1 = dict({('p', 'o'): RevisionAuthor.MODEL})",
    b"PRODUCER_AUTHOR_V1 = MappingProxyType({('p', 'o'): RevisionAuthor.MODEL}, {})",
    b"PRODUCER_AUTHOR_V1 = MappingProxyType(value={('p', 'o'): RevisionAuthor.MODEL})",
    b"PRODUCER_AUTHOR_V1 = MappingProxyType([])",
    b"PRODUCER_AUTHOR_V1 = MappingProxyType({**other})",
    b"PRODUCER_AUTHOR_V1 = MappingProxyType({(producer(), 'o'): RevisionAuthor.MODEL})",
    b"PRODUCER_AUTHOR_V1 = MappingProxyType({('p', 'o'): RevisionAuthor.UNKNOWN})",
    b"PRODUCER_AUTHOR_V1 = MappingProxyType({('p', 'o'): Other.MODEL})",
    b"PRODUCER_AUTHOR_V1 = MappingProxyType({('p', 'o'): author()})",
    b"PRODUCER_AUTHOR_V1 = MappingProxyType({('', 'o'): RevisionAuthor.MODEL})",
    b"PRODUCER_AUTHOR_V1 = MappingProxyType({('p:o', 'x'): RevisionAuthor.MODEL})",
    b"PRODUCER_AUTHOR_V1 = MappingProxyType({('p', 'o'): RevisionAuthor.MODEL, ('p', 'o'): RevisionAuthor.HUMAN})",
    b"PRODUCER_AUTHOR_V1 = MappingProxyType({('p', 'o'): RevisionAuthor.MODEL})\nPRODUCER_AUTHOR_V1 = other",
    b"PRODUCER_AUTHOR_V1 = MappingProxyType({('p', 'o'): RevisionAuthor.MODEL})\ndef f():\n global PRODUCER_AUTHOR_V1\n PRODUCER_AUTHOR_V1 = other",
    b"OTHER = PRODUCER_AUTHOR_V1 = MappingProxyType({('p', 'o'): RevisionAuthor.MODEL})",
))
def test_historical_producer_declaration_rejects_unsupported_source(source):
    with pytest.raises(root.PreparedRootError, match="birth_context_producer_policy_invalid"):
        root._historical_producer_author_declaration_v1(source)


@pytest.mark.parametrize("source", (
    b"", b"OTHER = {}", b"_MANIFEST_ORIGIN_TO_EXECUTOR_V1 = {}",
    b"_MANIFEST_ORIGIN_TO_EXECUTOR_V1 = MappingProxyType({**other})",
    b"_MANIFEST_ORIGIN_TO_EXECUTOR_V1 = MappingProxyType({origin(): ExecutorOrigin.CORE})",
    b"_MANIFEST_ORIGIN_TO_EXECUTOR_V1 = MappingProxyType({'core': ExecutorOrigin.CORE})",
    b"_MANIFEST_ORIGIN_TO_EXECUTOR_V1 = MappingProxyType({ManifestOrigin.CORE: 'core'})",
    b"_MANIFEST_ORIGIN_TO_EXECUTOR_V1 = MappingProxyType({ManifestOrigin.CORE: origin()})",
    b"_MANIFEST_ORIGIN_TO_EXECUTOR_V1 = MappingProxyType({Other.CORE: ExecutorOrigin.CORE})",
    b"_MANIFEST_ORIGIN_TO_EXECUTOR_V1 = MappingProxyType({ManifestOrigin.CORE: Other.CORE})",
    b"_MANIFEST_ORIGIN_TO_EXECUTOR_V1 = MappingProxyType({ManifestOrigin.UNKNOWN: ExecutorOrigin.CORE})",
    b"_MANIFEST_ORIGIN_TO_EXECUTOR_V1 = MappingProxyType({ManifestOrigin.CORE: ExecutorOrigin.UNKNOWN})",
    b"_MANIFEST_ORIGIN_TO_EXECUTOR_V1 = MappingProxyType({ManifestOrigin.CORE: ExecutorOrigin.CORE, ManifestOrigin.CORE: ExecutorOrigin.HUMAN})",
    b"_MANIFEST_ORIGIN_TO_EXECUTOR_V1 = MappingProxyType({})\n_MANIFEST_ORIGIN_TO_EXECUTOR_V1 = other",
    b"_MANIFEST_ORIGIN_TO_EXECUTOR_V1 = MappingProxyType({})\ndef f():\n global _MANIFEST_ORIGIN_TO_EXECUTOR_V1\n _MANIFEST_ORIGIN_TO_EXECUTOR_V1 = other",
    b"OTHER = _MANIFEST_ORIGIN_TO_EXECUTOR_V1 = MappingProxyType({})",
))
def test_historical_origin_declaration_rejects_unsupported_source(source):
    with pytest.raises(root.PreparedRootError, match="birth_context_producer_policy_invalid"):
        root._historical_executor_origin_declaration_v1(source)


@pytest.mark.parametrize("entries, expected", (
    (b"", {}),
    (b"ManifestOrigin.CORE: ExecutorOrigin.HUMAN", {"core": "human"}),
    (b"ManifestOrigin.BUILTIN_SKILL: ExecutorOrigin.IMPORTED", {"builtin_skill": "imported"}),
))
def test_historical_origin_declaration_preserves_sparse_historical_maps(entries, expected):
    observed = root._historical_executor_origin_declaration_v1(
        b"_MANIFEST_ORIGIN_TO_EXECUTOR_V1 = MappingProxyType({" + entries + b"})",
    )
    assert observed == expected
    with pytest.raises(TypeError):
        observed["user"] = "human"


def test_historical_origin_projection_uses_authenticated_source_not_loaded_map(tmp_path, monkeypatch):
    def historical_source(source):
        return source.replace(b"ManifestOrigin.CORE: ExecutorOrigin.CORE",
                              b"ManifestOrigin.CORE: ExecutorOrigin.HUMAN")

    chain, _release, _source = _producer_policy_fixture(
        tmp_path, monkeypatch, source_transform=historical_source,
    )
    observed = root.load_historical_producer_declarations_v1(
        chain.context_transitions[0].prepared_admission_context_id,
    )
    assert observed.executor_origins["core"] == "human"


def test_historical_origin_projection_rejects_invalid_authenticated_declaration(tmp_path, monkeypatch):
    chain, _release, _source = _producer_policy_fixture(
        tmp_path, monkeypatch,
        source_transform=lambda source: source.replace(
            b"ManifestOrigin.CORE: ExecutorOrigin.CORE",
            b"ManifestOrigin.CORE: ExecutorOrigin.UNKNOWN",
        ),
    )
    with pytest.raises(root.PreparedRootError, match="birth_context_producer_policy_invalid"):
        root.load_historical_producer_declarations_v1(
            chain.context_transitions[0].prepared_admission_context_id,
        )


@pytest.mark.parametrize("case", (
    "source-changed", "source-missing", "historical-hash", "historical-size",
    "historical-role", "historical-file-missing", "historical-file-duplicate",
    "historical-build", "head-build", "sequence", "missing-record",
    "missing-distribution", "missing-namespace", "frontier-changed",
))
def test_historical_producer_authors_reject_incomplete_or_unbound_evidence(
    tmp_path, monkeypatch, case,
):
    import executor_birth_distribution_manifest as distribution

    chain, release, source = _producer_policy_fixture(tmp_path, monkeypatch)
    record = chain.authenticated_records[0]
    item = next(item for item in record.files if item.path == _POLICY_PATH)
    if case == "source-changed":
        support.write(release / _POLICY_PATH, source + b"\n", 0o644)
    elif case == "source-missing":
        (release / _POLICY_PATH).unlink()
    elif case.startswith("historical-"):
        if case == "historical-hash":
            replacement = replace(item, content_hash=_digest("0"))
        elif case == "historical-size":
            replacement = replace(item, size=item.size + 1)
        elif case == "historical-role":
            replacement = replace(item, role="public_document")
        else:
            replacement = item
        files = tuple(replacement if entry.path == _POLICY_PATH else entry for entry in record.files)
        if case == "historical-file-missing":
            files = tuple(entry for entry in files if entry.path != _POLICY_PATH)
        elif case == "historical-file-duplicate":
            files += (item,)
        record = replace(record, files=files)
        if case == "historical-build":
            record = replace(record, closed_build_id=_digest("0"))
        chain = replace(chain, authenticated_records=(record,))
    elif case == "head-build":
        chain = replace(chain, heads=(replace(chain.required_head, closed_build_id=_digest("0")),))
    elif case == "sequence":
        chain = replace(chain, authenticated_records=(replace(record, release_sequence=2),))
    elif case == "missing-record":
        chain = replace(chain, authenticated_records=())
    elif case == "missing-distribution":
        chain = replace(chain, required_distribution=None)
    elif case == "missing-namespace":
        monkeypatch.setattr(root, "_historical_producer_author_declaration_v1", lambda _source: {})
    after = chain
    if case == "frontier-changed":
        after = replace(chain, heads=(replace(chain.required_head, head_id=_digest("0")),))
    observations = iter((chain, after))
    monkeypatch.setattr(chain_module, "inspect_ownership_chain_state_v1", lambda: next(observations))
    with pytest.raises((root.PreparedRootError, distribution.DistributionManifestError)):
        root.load_historical_producer_declarations_v1(
            chain.context_transitions[0].prepared_admission_context_id,
        )


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
    assert observed.binding_kind == "transition_target"
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


def _initial_context_fixture(tmp_path, monkeypatch, **changes):
    """Real predecessor set with a mocked authenticated first-edge boundary."""
    base = _prepared(tmp_path, monkeypatch)
    public = _read_public()
    values = dict(
        previous_set_id=public.set_id,
        previous_admission_context_id=public.material.pin.admission_context_id,
        previous_context_epoch=public.material.pin.context_epoch,
        prepared_admission_context_id=_digest("a"), prepared_context_epoch=_digest("b"),
    )
    values.update(changes)
    return base, public, _chain_boundary_fixture(base, monkeypatch, **values)


def test_initial_context_keys_are_public_inert_and_explicitly_predecessor(tmp_path, monkeypatch):
    import executor_birth_keystore as keystore
    from executor_birth_context_selection import is_context_selection_v1
    from executor_birth_secure_fs import _SecureRootSession

    _base, public, chain = _initial_context_fixture(tmp_path, monkeypatch)

    def forbidden(*args, **kwargs):
        pytest.fail("initial-context evidence entered a private or mutating boundary")

    for operation in ("create_file_exclusive", "create_directory_exclusive",
                      "rename_no_replace", "dispose_transaction_object"):
        monkeypatch.setattr(_SecureRootSession, operation, forbidden)
    monkeypatch.setattr(keystore, "_load_birth_keystore_in_session", forbidden)
    for name in ("load_sealed_authorities_v1", "load_required_context_runtime_v1",
                 "_load_historical_transition_verifiers_v1", "_load_historical_transition_anchor_v1"):
        monkeypatch.setattr(root, name, forbidden)
    original = _SecureRootSession.read_file
    observed_paths = []

    def read(session, components, **kwargs):
        assert "private" not in components and "keystore.json" not in components
        observed_paths.append(components)
        return original(session, components, **kwargs)

    monkeypatch.setattr(_SecureRootSession, "read_file", read)
    result = root.load_historical_context_verifiers_v1(public.material.pin.admission_context_id)
    assert result.binding_kind == "initial_predecessor"
    assert result.public_set.set_id == public.set_id
    assert result.required_head_id == chain.required_head.head_id
    assert result.transition_id == chain.context_transitions[0].transition_id
    assert len(result.public_set.producers) == len(public.producers)
    assert observed_paths and not is_context_selection_v1(result)
    assert not isinstance(result, root.SealedAuthoritiesV1)
    with pytest.raises(FrozenInstanceError):
        result.binding_kind = "transition_target"


@pytest.mark.parametrize("field", ["previous_set_id", "previous_admission_context_id", "previous_context_epoch"])
def test_initial_context_refuses_mismatched_first_edge_pin(tmp_path, monkeypatch, field):
    value = "0" * 64 if field == "previous_set_id" else _digest("0")
    _base, _public, chain = _initial_context_fixture(tmp_path, monkeypatch, **{field: value})
    with pytest.raises(root.PreparedRootError, match="birth_context_selection_invalid"):
        root.load_historical_context_verifiers_v1(chain.context_transitions[0].previous_admission_context_id)


@pytest.mark.parametrize("change", ["missing", "altered"])
def test_initial_context_requires_its_fixed_marker(tmp_path, monkeypatch, change):
    base, public, _chain = _initial_context_fixture(tmp_path, monkeypatch)
    if change == "missing":
        support.installed_marker(base).unlink()
    else:
        _rewrite(support.installed_marker(base), lambda marker: marker.update(set_id="0" * 64))
    with pytest.raises(PreparedSetError, match="birth_prepared_set_(invalid|unavailable|mismatch)"):
        root.load_historical_context_verifiers_v1(public.material.pin.admission_context_id)


def test_initial_context_and_target_collision_has_no_implicit_priority(tmp_path, monkeypatch):
    _base, public, chain = _initial_context_fixture(tmp_path, monkeypatch)
    transition = replace(chain.context_transitions[0],
                         prepared_admission_context_id=public.material.pin.admission_context_id)
    monkeypatch.setattr(chain_module, "inspect_ownership_chain_state_v1",
                        lambda: replace(chain, context_transitions=(transition,)))
    with pytest.raises(root.PreparedRootError, match="birth_context_selection_invalid"):
        root.load_historical_context_verifiers_v1(public.material.pin.admission_context_id)


@pytest.mark.parametrize("change", ["head", "transition", "record"])
def test_initial_context_requires_unchanged_chain_after_acquisition(tmp_path, monkeypatch, change):
    _base, public, chain = _initial_context_fixture(tmp_path, monkeypatch)
    if change == "head":
        after = replace(chain, heads=(replace(chain.required_head, head_id=_digest("9")),))
    elif change == "transition":
        after = replace(chain, context_transitions=())
    else:
        after = replace(chain, authenticated_records=(b"changed record",))
    observations = iter((chain, after))
    monkeypatch.setattr(chain_module, "inspect_ownership_chain_state_v1", lambda: next(observations))
    with pytest.raises(root.PreparedRootError, match="birth_context_selection_changed"):
        root.load_historical_context_verifiers_v1(public.material.pin.admission_context_id)


def test_initial_context_cannot_borrow_successor_producer_policy(tmp_path, monkeypatch):
    _base, public, _chain = _initial_context_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(root, "_read_historical_context_set_v1",
                        lambda *_args, **_kwargs: pytest.fail("policy lookup passed initial selector"))
    with pytest.raises(root.PreparedRootError, match="birth_context_selection_invalid"):
        root.load_historical_producer_declarations_v1(public.material.pin.admission_context_id)


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
