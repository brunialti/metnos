from __future__ import annotations

import json
import hashlib
import inspect
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from executor_birth_cutover import CurrentReceiptProof
import executor_birth_ownership_chain as chain_module
from executor_birth_ownership_chain import (
    HEAD_PURPOSE, OwnershipChainError, OwnershipChainStore,
    _OwnershipChainCrashForTest,
    decode_required_head, encode_required_head, issue_ownership_head,
    verify_contiguous_chain, verify_ownership_head,
)
from executor_birth_ownership_cutover import (
    OwnershipCutoverKey, OwnershipCutoverRegistry,
    issue_ownership_cutover_certificate,
    ownership_key_id, verify_ownership_cutover_certificate,
)
from executor_birth_ownership_authorities import (
    OwnershipPublicRegistriesV1, _ownership_public_registries_for_test,
)
from executor_birth_ownership_preflight import _sealed_build_identity_for_test
from executor_birth_distribution_manifest import (
    DistributionKey, DistributionManifestError, DistributionRegistry,
    _verified_distribution_for_test, distribution_key_id,
)


def D(char: str) -> str:
    return "sha256:" + char * 64


@dataclass(frozen=True)
class Authorities:
    cutover_private: Ed25519PrivateKey
    cutover_key_id: str
    cutover_registry: OwnershipCutoverRegistry
    head_private: Ed25519PrivateKey
    head_key_id: str
    head_registry: OwnershipCutoverRegistry
    public: OwnershipPublicRegistriesV1

    def __iter__(self):
        # Most tests below exercise heads, so preserve their compact unpacking.
        return iter((self.head_private, self.head_key_id, self.head_registry))


@pytest.fixture
def authority():
    distribution_private = Ed25519PrivateKey.generate()
    cutover_private = Ed25519PrivateKey.generate()
    head_private = Ed25519PrivateKey.generate()
    distribution_key = DistributionKey(
        distribution_key_id(distribution_private.public_key()),
        distribution_private.public_key(), frozenset({"closed_distribution_v1"}),
        1, None,
    )
    cutover_key_id = ownership_key_id(cutover_private.public_key())
    head_key_id = ownership_key_id(head_private.public_key())
    cutover_registry = OwnershipCutoverRegistry({
        cutover_key_id: OwnershipCutoverKey(
            cutover_key_id, cutover_private.public_key(),
            frozenset({"ownership_cutover_v1"}),
        ),
    })
    head_registry = OwnershipCutoverRegistry({
        head_key_id: OwnershipCutoverKey(
            head_key_id, head_private.public_key(), frozenset({HEAD_PURPOSE}),
        ),
    })
    public = _ownership_public_registries_for_test(
        DistributionRegistry({distribution_key.key_id: distribution_key}),
        cutover_registry, head_registry,
    )
    return Authorities(
        cutover_private, cutover_key_id, cutover_registry,
        head_private, head_key_id, head_registry, public,
    )


def initialize_test_store(root, authority):
    """Private portable seam; it is not evidence of root-owned cold loading."""
    return OwnershipChainStore._initialize_with_authorities(
        root, authority.public,
    )


def open_test_store(root, authority):
    """Open an existing portable store through the same private test seam."""
    result = OwnershipChainStore.__new__(OwnershipChainStore)
    result._open_with_authorities(root, authority.public)
    return result


def cutover(authority, *, previous, build, request):
    encoded, signature = issue_ownership_cutover_certificate(
        proof=CurrentReceiptProof((), {}), previous_cutover_id=previous,
        request_id=request, signing_key_id=authority.cutover_key_id,
        maintenance_evidence_hash=D("1"), boundary_inventory_hash=D("2"),
        boundary_guard_version="closed-v1", closed_build_id=build,
        private_key=authority.cutover_private,
    )
    return encoded, signature, verify_ownership_cutover_certificate(
        encoded, signature, registry=authority.cutover_registry,
    )


def build_material(*, sequence=1, previous=None):
    unsigned = {
        "previous_closed_build_id": previous,
        "release_sequence": sequence,
        "schema_version": 1,
    }
    canonical_unsigned = json.dumps(
        unsigned, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    ).encode("ascii")
    build_id = "sha256:" + hashlib.sha256(
        b"metnos.executor-birth.closed-build-id/v1\0" + canonical_unsigned,
    ).hexdigest()
    identity = _sealed_build_identity_for_test(build_id, D("2"), "closed-v1")
    encoded = json.dumps(
        {**unsigned, "closed_build_id": identity.closed_build_id},
        ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    ).encode("ascii")
    distribution = _verified_distribution_for_test(
        identity, previous_closed_build_id=previous,
        release_sequence=sequence, encoded=encoded,
        signature=bytes([sequence]) * 64,
    )
    return distribution


def test_head_codec_is_canonical_signed_and_purpose_separated(authority):
    private, key_id, registry = authority
    encoded, signature = issue_ownership_head(
        release_sequence=1, cutover_id=D("3"), closed_build_id=D("4"),
        previous_head_id=None, signing_key_id=key_id, private_key=private,
    )
    head = verify_ownership_head(encoded, signature, registry=registry)
    assert head.release_sequence == 1
    assert head.previous_head_id is None
    with pytest.raises(OwnershipChainError, match="key_unauthorized"):
        other = OwnershipCutoverRegistry({
            key_id: OwnershipCutoverKey(
                key_id, private.public_key(), frozenset({"ownership_cutover_v1"}),
            ),
        })
        verify_ownership_head(encoded, signature, registry=other)


@pytest.mark.parametrize("mutation", ["duplicate", "newline", "signature", "sequence"])
def test_head_codec_rejects_noncanonical_or_tampered(authority, mutation):
    private, key_id, registry = authority
    encoded, signature = issue_ownership_head(
        release_sequence=1, cutover_id=D("3"), closed_build_id=D("4"),
        previous_head_id=None, signing_key_id=key_id, private_key=private,
    )
    if mutation == "duplicate":
        encoded = encoded.replace(b'{"closed_build_id"', b'{"closed_build_id":"' + D("4").encode() + b'","closed_build_id"', 1)
    elif mutation == "newline":
        encoded += b"\n"
    elif mutation == "signature":
        signature = b"x" * 64
    else:
        value = json.loads(encoded)
        value["release_sequence"] = True
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(OwnershipChainError):
        verify_ownership_head(encoded, signature, registry=registry)


def test_chain_requires_unique_contiguous_required_head(authority):
    private, key_id, registry = authority
    build1 = build_material(sequence=1)
    build2 = build_material(
        sequence=2, previous=build1.identity.closed_build_id,
    )
    c1b, c1s, c1 = cutover(
        authority, previous=None, build=build1.identity.closed_build_id,
        request=D("5"),
    )
    c2b, c2s, c2 = cutover(
        authority, previous=c1.cutover_id,
        build=build2.identity.closed_build_id, request=D("7"),
    )
    h1b, h1s = issue_ownership_head(
        release_sequence=1, cutover_id=c1.cutover_id,
        closed_build_id=build1.identity.closed_build_id,
        previous_head_id=None, signing_key_id=key_id, private_key=private,
    )
    h1 = verify_ownership_head(h1b, h1s, registry=registry)
    h2b, h2s = issue_ownership_head(
        release_sequence=2, cutover_id=c2.cutover_id,
        closed_build_id=build2.identity.closed_build_id,
        previous_head_id=h1.head_id, signing_key_id=key_id, private_key=private,
    )
    h2 = verify_ownership_head(h2b, h2s, registry=registry)
    builds = {
        build1.identity.closed_build_id: build1,
        build2.identity.closed_build_id: build2,
    }
    verified = verify_contiguous_chain(
        anchor=c1, heads=(h1, h2), required_head=h2,
        cutovers={c1.cutover_id: c1, c2.cutover_id: c2}, builds=builds,
    )
    assert verified.required_head == h2
    with pytest.raises(OwnershipChainError, match="downgrade"):
        verify_contiguous_chain(
            anchor=c1, heads=(h1, h2), required_head=h1,
            cutovers={c1.cutover_id: c1, c2.cutover_id: c2}, builds=builds,
        )
    with pytest.raises(OwnershipChainError, match="recovery_required"):
        verify_contiguous_chain(
            anchor=c1, heads=(h2,), required_head=h2,
            cutovers={c1.cutover_id: c1, c2.cutover_id: c2}, builds=builds,
        )
    wrong_build2 = build_material(sequence=2, previous=None)
    wrong_cutover_bytes, wrong_cutover_sig, wrong_cutover = cutover(
        authority, previous=c1.cutover_id,
        build=wrong_build2.identity.closed_build_id, request=D("8"),
    )
    wrong_head_bytes, wrong_head_sig = issue_ownership_head(
        release_sequence=2, cutover_id=wrong_cutover.cutover_id,
        closed_build_id=wrong_build2.identity.closed_build_id,
        previous_head_id=h1.head_id, signing_key_id=key_id, private_key=private,
    )
    wrong_head = verify_ownership_head(
        wrong_head_bytes, wrong_head_sig, registry=registry,
    )
    with pytest.raises(OwnershipChainError, match="build predecessor"):
        verify_contiguous_chain(
            anchor=c1, heads=(h1, wrong_head), required_head=wrong_head,
            cutovers={c1.cutover_id: c1, wrong_cutover.cutover_id: wrong_cutover},
            builds={
                build1.identity.closed_build_id: build1,
                wrong_build2.identity.closed_build_id: wrong_build2,
            },
        )


def test_portable_store_is_no_replace_and_exact_retry(authority, tmp_path):
    private, key_id, registry = authority
    tmp_path.chmod(0o755)
    store = initialize_test_store(tmp_path, authority)
    distribution = build_material()
    store.append_authenticated_build(distribution)
    store.append_authenticated_build(distribution)
    cbytes, csig, certificate = cutover(
        authority, previous=None, build=D("4"), request=D("5"),
    )
    store.append_cutover(cbytes, csig)
    hbytes, hsig = issue_ownership_head(
        release_sequence=1, cutover_id=certificate.cutover_id,
        closed_build_id=D("4"), previous_head_id=None,
        signing_key_id=key_id, private_key=private,
    )
    store.append_head(hbytes, hsig)
    store.append_head(hbytes, hsig)
    head_path = next((tmp_path / "heads-v1").glob("*.json"))
    head_path.write_bytes(b"tampered")
    with pytest.raises(OwnershipChainError, match="recovery_required"):
        store.append_head(hbytes, hsig)


def test_verified_distribution_binds_exact_authenticated_signature():
    distribution = build_material()
    with pytest.raises(DistributionManifestError, match="verified artifact"):
        replace(distribution, signature=b"x" * 64)


def test_store_reads_only_required_contiguous_prefix(authority, tmp_path):
    private, key_id, registry = authority
    tmp_path.chmod(0o755)
    store = initialize_test_store(tmp_path, authority)
    distribution = build_material()
    identity = distribution.identity
    store.append_authenticated_build(distribution)
    cutover_bytes, cutover_signature, certificate = cutover(
        authority, previous=None, build=identity.closed_build_id, request=D("5"),
    )
    store.append_cutover(cutover_bytes, cutover_signature)
    head_bytes, head_signature = issue_ownership_head(
        release_sequence=1, cutover_id=certificate.cutover_id,
        closed_build_id=identity.closed_build_id, previous_head_id=None,
        signing_key_id=key_id, private_key=private,
    )
    store.append_head(head_bytes, head_signature)
    store.update_required_head(
        head_bytes, head_signature, expected_head_id=None,
    )
    verified = store.read_required_chain(
        anchor=certificate, builds={identity.closed_build_id: distribution},
    )
    assert verified.required_head.release_sequence == 1


def test_required_head_missing_never_falls_back_to_highest(authority, tmp_path):
    _private, _key_id, registry = authority
    tmp_path.chmod(0o755)
    store = initialize_test_store(tmp_path, authority)
    with pytest.raises(OwnershipChainError, match="downgrade"):
        store.read_required_chain(anchor=object(), builds={})


def test_store_rejects_fork_at_same_sequence(authority, tmp_path):
    private, key_id, registry = authority
    tmp_path.chmod(0o755)
    store = initialize_test_store(tmp_path, authority)
    distribution = build_material()
    identity = distribution.identity
    store.append_authenticated_build(distribution)
    c1b, c1s, c1 = cutover(
        authority, previous=None, build=identity.closed_build_id, request=D("5"),
    )
    c2b, c2s, c2 = cutover(
        authority, previous=None, build=identity.closed_build_id, request=D("6"),
    )
    store.append_cutover(c1b, c1s)
    store.append_cutover(c2b, c2s)
    first_bytes, first_signature = issue_ownership_head(
        release_sequence=1, cutover_id=c1.cutover_id,
        closed_build_id=identity.closed_build_id, previous_head_id=None,
        signing_key_id=key_id, private_key=private,
    )
    fork_bytes, fork_signature = issue_ownership_head(
        release_sequence=1, cutover_id=c2.cutover_id,
        closed_build_id=identity.closed_build_id, previous_head_id=None,
        signing_key_id=key_id, private_key=private,
    )
    store.append_head(first_bytes, first_signature)
    store.append_head(fork_bytes, fork_signature)
    store.update_required_head(
        first_bytes, first_signature, expected_head_id=None,
    )
    with pytest.raises(OwnershipChainError, match="recovery_required"):
        store.read_required_chain(
            anchor=c1, builds={identity.closed_build_id: distribution},
        )


def test_incomplete_append_pair_requires_recovery(authority, tmp_path):
    private, key_id, registry = authority
    tmp_path.chmod(0o755)
    store = initialize_test_store(tmp_path, authority)
    encoded, signature = issue_ownership_head(
        release_sequence=1, cutover_id=D("3"), closed_build_id=D("4"),
        previous_head_id=None, signing_key_id=key_id, private_key=private,
    )
    store.append_head(encoded, signature)
    next((tmp_path / "heads-v1").glob("*.sig")).unlink()
    with pytest.raises(OwnershipChainError, match="recovery_required"):
        store.append_head(encoded, signature)


def test_required_head_framing_is_exact_and_rejects_trailing_bytes(authority):
    private, key_id, registry = authority
    encoded, signature = issue_ownership_head(
        release_sequence=1, cutover_id=D("3"), closed_build_id=D("4"),
        previous_head_id=None, signing_key_id=key_id, private_key=private,
    )
    head = verify_ownership_head(encoded, signature, registry=registry)
    frame = encode_required_head(head)
    assert decode_required_head(frame, registry=registry) == head
    with pytest.raises(OwnershipChainError, match="recovery_required"):
        decode_required_head(frame + b"x", registry=registry)


@pytest.mark.parametrize("crash_at", ["before_replace", "after_replace"])
def test_required_head_upgrade_recovers_exactly_across_atomic_point(
    authority, tmp_path, crash_at,
):
    private, key_id, registry = authority
    tmp_path.chmod(0o755)
    store = initialize_test_store(tmp_path, authority)
    first_bytes, first_signature = issue_ownership_head(
        release_sequence=1, cutover_id=D("3"), closed_build_id=D("4"),
        previous_head_id=None, signing_key_id=key_id, private_key=private,
    )
    first = store.append_head(first_bytes, first_signature)
    store.update_required_head(
        first_bytes, first_signature, expected_head_id=None,
    )
    second_bytes, second_signature = issue_ownership_head(
        release_sequence=2, cutover_id=D("5"), closed_build_id=D("6"),
        previous_head_id=first.head_id, signing_key_id=key_id, private_key=private,
    )
    second = store.append_head(second_bytes, second_signature)

    def crash(point):
        if point == crash_at:
            raise _OwnershipChainCrashForTest("injected crash")

    with pytest.raises(_OwnershipChainCrashForTest, match="injected crash"):
        store.update_required_head(
            second_bytes, second_signature, expected_head_id=first.head_id,
            _crash_seam=crash,
        )
    observed = store.read_required_head()
    assert observed.head_id == (
        first.head_id if crash_at == "before_replace" else second.head_id
    )
    assert store.update_required_head(
        second_bytes, second_signature, expected_head_id=first.head_id,
    ).head_id == second.head_id


def test_required_head_upgrade_rejects_stale_cas(authority, tmp_path):
    private, key_id, registry = authority
    tmp_path.chmod(0o755)
    store = initialize_test_store(tmp_path, authority)
    encoded, signature = issue_ownership_head(
        release_sequence=1, cutover_id=D("3"), closed_build_id=D("4"),
        previous_head_id=None, signing_key_id=key_id, private_key=private,
    )
    store.append_head(encoded, signature)
    store.update_required_head(encoded, signature, expected_head_id=None)
    second_bytes, second_signature = issue_ownership_head(
        release_sequence=2, cutover_id=D("5"), closed_build_id=D("6"),
        previous_head_id=verify_ownership_head(encoded, signature, registry=registry).head_id,
        signing_key_id=key_id, private_key=private,
    )
    store.append_head(second_bytes, second_signature)
    with pytest.raises(OwnershipChainError, match="downgrade"):
        store.update_required_head(
            second_bytes, second_signature, expected_head_id=D("9"),
        )


def test_required_head_cas_allows_only_one_competing_successor(authority, tmp_path):
    private, key_id, registry = authority
    tmp_path.chmod(0o755)
    store = initialize_test_store(tmp_path, authority)
    first_bytes, first_signature = issue_ownership_head(
        release_sequence=1, cutover_id=D("3"), closed_build_id=D("4"),
        previous_head_id=None, signing_key_id=key_id, private_key=private,
    )
    first = store.append_head(first_bytes, first_signature)
    store.update_required_head(first_bytes, first_signature, expected_head_id=None)
    candidates = []
    for cutover, build in ((D("5"), D("6")), (D("7"), D("8"))):
        encoded, signature = issue_ownership_head(
            release_sequence=2, cutover_id=cutover, closed_build_id=build,
            previous_head_id=first.head_id, signing_key_id=key_id,
            private_key=private,
        )
        store.append_head(encoded, signature)
        candidates.append((encoded, signature))

    def update(candidate):
        try:
            return store.update_required_head(
                *candidate, expected_head_id=first.head_id,
            ).head_id
        except OwnershipChainError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(update, candidates))
    assert sum(item.startswith("sha256:") for item in outcomes) == 1
    assert outcomes.count("birth_ownership_downgrade") == 1


def test_read_only_open_never_creates_missing_store(authority, tmp_path):
    _private, _key_id, registry = authority
    tmp_path.chmod(0o755)
    with pytest.raises(OwnershipChainError, match="recovery_required"):
        open_test_store(tmp_path / "absent-store", authority)
    assert not (tmp_path / "absent-store").exists()


def test_product_store_constructors_do_not_accept_authority_injection():
    assert tuple(inspect.signature(OwnershipChainStore).parameters) == ("root",)
    assert tuple(inspect.signature(OwnershipChainStore.initialize).parameters) == (
        "root",
    )


def test_exact_retry_completes_matching_orphan_signature(authority, tmp_path):
    private, key_id, registry = authority
    tmp_path.chmod(0o755)
    store = initialize_test_store(tmp_path, authority)
    encoded, signature = issue_ownership_head(
        release_sequence=1, cutover_id=D("3"), closed_build_id=D("4"),
        previous_head_id=None, signing_key_id=key_id, private_key=private,
    )

    def crash(point):
        if point == "after_signature":
            raise _OwnershipChainCrashForTest("injected crash")

    with pytest.raises(_OwnershipChainCrashForTest, match="injected crash"):
        store.append_head(encoded, signature, _crash_seam=crash)
    assert len(tuple((tmp_path / "heads-v1").glob("*.sig"))) == 1
    assert len(tuple((tmp_path / "heads-v1").glob("*.json"))) == 0
    assert store.append_head(encoded, signature).encoded == encoded


def test_windows_required_replace_retries_only_transient_conflict(
    authority, tmp_path, monkeypatch,
):
    private, key_id, registry = authority
    tmp_path.chmod(0o755)
    store = initialize_test_store(tmp_path, authority)
    first_bytes, first_signature = issue_ownership_head(
        release_sequence=1, cutover_id=D("3"), closed_build_id=D("4"),
        previous_head_id=None, signing_key_id=key_id, private_key=private,
    )
    first = store.append_head(first_bytes, first_signature)
    store.update_required_head(first_bytes, first_signature, expected_head_id=None)
    second_bytes, second_signature = issue_ownership_head(
        release_sequence=2, cutover_id=D("5"), closed_build_id=D("6"),
        previous_head_id=first.head_id, signing_key_id=key_id, private_key=private,
    )
    store.append_head(second_bytes, second_signature)
    original_replace = chain_module.os.replace
    attempts = 0

    def transient_then_success(source, destination):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            error = OSError("sharing violation")
            error.winerror = 32
            raise error
        return original_replace(source, destination)

    monkeypatch.setattr(chain_module, "_windows_platform", lambda: True)
    monkeypatch.setattr(chain_module.os, "replace", transient_then_success)
    assert store.update_required_head(
        second_bytes, second_signature, expected_head_id=first.head_id,
        replace_timeout=0.2,
    ).release_sequence == 2
    assert attempts == 3


def test_windows_persistent_replace_denial_is_bounded_failure(
    authority, tmp_path, monkeypatch,
):
    private, key_id, registry = authority
    tmp_path.chmod(0o755)
    store = initialize_test_store(tmp_path, authority)
    first_bytes, first_signature = issue_ownership_head(
        release_sequence=1, cutover_id=D("3"), closed_build_id=D("4"),
        previous_head_id=None, signing_key_id=key_id, private_key=private,
    )
    first = store.append_head(first_bytes, first_signature)
    store.update_required_head(first_bytes, first_signature, expected_head_id=None)
    second_bytes, second_signature = issue_ownership_head(
        release_sequence=2, cutover_id=D("5"), closed_build_id=D("6"),
        previous_head_id=first.head_id, signing_key_id=key_id, private_key=private,
    )
    store.append_head(second_bytes, second_signature)

    def persistent(_source, _destination):
        error = OSError("sharing violation")
        error.winerror = 32
        raise error

    monkeypatch.setattr(chain_module, "_windows_platform", lambda: True)
    monkeypatch.setattr(chain_module.os, "replace", persistent)
    with pytest.raises(OwnershipChainError, match="replace timeout"):
        store.update_required_head(
            second_bytes, second_signature, expected_head_id=first.head_id,
            replace_timeout=0,
        )
    assert store.read_required_head().head_id == first.head_id
