"""Explicit adoption uses real signed sources; ownership chain is a test seam."""
import os
from dataclasses import replace

import pytest
import executor_birth_prepared_root as root
import executor_birth_ownership_chain as ownership
import executor_birth_distribution_manifest as distribution
import executor_birth_ownership_authorities as authorities
import test_executor_birth_distribution_manifest as signing
from .test_group8_public_history import _producer_policy_fixture, _read_public, _digest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="native POSIX authority custody")
PATH = "runtime/executor_birth_initial_policy_v1.py"
POLICY = "runtime/executor_birth_producer_table_v1.py"


def load(identifier, monkeypatch, shared, **options):
    if not shared:
        return root.load_historical_producer_declarations_for_contexts_v1((identifier,), **options)
    with root.open_prepared_root_session_v1() as session:
        with session.global_lock(exclusive=True, create=False):
            binding, = root.load_historical_context_verifiers_for_contexts_in_session_v1((identifier,), session)
            with monkeypatch.context() as context:
                context.setattr(root, '_read_historical_context_set_in_session_v1',
                    lambda *args, **kwargs: pytest.fail('initial public set was reread'))
                return root.load_historical_producer_declarations_for_contexts_in_session_v1(
                    (identifier,), session, _context_bindings={identifier: binding}, **options)


def scenario(tmp_path, monkeypatch, fault=None, *, successor_changed=False):
    original, old_release, source = _producer_policy_fixture(tmp_path, monkeypatch)
    public = _read_public()
    first = replace(original.context_transitions[0],
        previous_set_id=public.set_id,
        previous_admission_context_id=public.material.pin.admission_context_id,
        previous_context_epoch=public.material.pin.context_epoch,
        prepared_admission_context_id=_digest("c"))
    record = original.authenticated_records[0]
    binding = (public.set_id, first.transition_id, record.closed_build_id,
               next(item.content_hash for item in record.files if item.path == POLICY))
    if fault in {"set", "transition", "build", "hash"}:
        values = list(binding)
        index = ["set", "transition", "build", "hash"].index(fault)
        values[index] = "0" * 64 if index == 0 else _digest("0")
        binding = tuple(values)
    key = first.previous_admission_context_id if fault != "context" else _digest("0")
    entries = repr(key) + ": " + repr(binding)
    if fault == "absent": entries = ""
    if fault == "duplicate": entries += ", " + entries
    declaration = ("from types import MappingProxyType\nINITIAL_PRODUCER_POLICY_V1 = MappingProxyType({" + entries + "})\n").encode()
    private, key_id, registry = signing._authority(distribution.PURPOSE)
    release = tmp_path / "later"
    def files(items, directory):
        signing._add_declared_file(items, directory, POLICY, "runtime_code", source + b"# successor policy bytes\n" if successor_changed else source)
        signing._add_declared_file(items, directory, PATH, "runtime_code", declaration)
    _value, encoded, signature = signing._manifest(release, private, key_id,
        mutate=lambda value: value.update(release_sequence=2, previous_closed_build_id=record.closed_build_id),
        files_mutate=files)
    monkeypatch.setattr(authorities, "load_ownership_public_registries_v1", lambda: signing._fixed_public_bundle(private))
    latest = distribution.authenticate_distribution_record_v1(encoded, signature)
    verified = distribution._verify_distribution_manifest_for_test(encoded, signature, registry=registry,
        _environment=signing._test_environment(release))
    chain = replace(original, heads=original.heads + (replace(original.required_head,
        closed_build_id=latest.closed_build_id, release_sequence=2, head_id=_digest("b")),),
        context_transitions=(first, replace(first, closed_build_id=latest.closed_build_id,
            prepared_admission_context_id=_digest("d"), encoded=b"later transition")),
        authenticated_records=(record, latest), required_distribution=verified)
    monkeypatch.setattr(ownership, "inspect_ownership_chain_state_v1", lambda: chain)
    if fault == "tamper": (release / PATH).write_bytes(declaration + b"# changed")
    return first.previous_admission_context_id, public, chain


@pytest.mark.parametrize('shared', [False, True])
def test_initial_adoption_preserves_original_keys_and_explicit_provenance(tmp_path, monkeypatch, shared):
    identifier, public, chain = scenario(tmp_path, monkeypatch)
    result, = load(identifier, monkeypatch, shared, include_reattestation=True)
    assert result.context.binding_kind == "initial_predecessor"
    assert result.context.public_set.set_id == public.set_id
    assert result.context.public_set.admission_verifier_keys.keys() == public.admission_verifier_keys.keys()
    assert result.closed_build_id == chain.authenticated_records[0].closed_build_id
    assert result.reattestation_scope is None and result.reattestation_scope_error
    assert result.authors and result.executor_origins
    # Ordinary historical selection still cannot borrow the successor policy.
    with pytest.raises(root.PreparedRootError, match="birth_context_selection_invalid"):
        root.load_historical_producer_declarations_v1(identifier)


@pytest.mark.parametrize("fault", ["absent", "context", "set", "transition", "build", "hash", "duplicate", "tamper"])
@pytest.mark.parametrize('shared', [False, True])
def test_initial_adoption_rejects_unbound_or_changed_declarations(tmp_path, monkeypatch, fault, shared):
    identifier, _public, _chain = scenario(tmp_path, monkeypatch, fault)
    with pytest.raises(RuntimeError):
        load(identifier, monkeypatch, shared)


def test_initial_adoption_rereads_the_chain(tmp_path, monkeypatch):
    identifier, _public, chain = scenario(tmp_path, monkeypatch)
    observations = iter((chain, replace(chain, context_transitions=())))
    monkeypatch.setattr(ownership, "inspect_ownership_chain_state_v1", lambda: next(observations))
    with pytest.raises(root.PreparedRootError, match="birth_context_selection_changed"):
        root.load_historical_producer_declarations_for_contexts_v1((identifier,))


@pytest.mark.parametrize('shared', [False, True])
def test_initial_adoption_requires_exact_historical_source_after_successor_change(tmp_path, monkeypatch, shared):
    identifier, _public, chain = scenario(tmp_path, monkeypatch, successor_changed=True)
    with pytest.raises(root.PreparedRootError, match="birth_context_producer_policy_invalid"):
        load(identifier, monkeypatch, shared)
    # The fixture root is selected by its original authenticated distribution.
    from executor_birth_distribution_manifest import file_content_hash
    expected = next(item for item in chain.authenticated_records[0].files if item.path == POLICY)
    candidates = [path for path in tmp_path.rglob("executor_birth_producer_table_v1.py")
                  if file_content_hash(POLICY, path.read_bytes()) == expected.content_hash]
    assert len(candidates) == 1
    source = candidates[0].read_bytes()
    result, = load(identifier, monkeypatch, shared, public_sources=((POLICY, source),))
    assert result.context.binding_kind == "initial_predecessor"
    with pytest.raises(root.PreparedRootError, match="birth_context_producer_policy_invalid"):
        load(identifier, monkeypatch, shared, public_sources=((POLICY, source + b"# altered\n"),))
