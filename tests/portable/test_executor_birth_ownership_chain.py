from __future__ import annotations

import json
import hashlib
import inspect
import multiprocessing
import os
import shutil
import stat
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from executor_birth_cutover import CurrentReceiptProof
import executor_birth_ownership_chain as chain_module
import executor_birth_distribution_manifest as distribution_module
import executor_birth_ownership_authorities as authority_module
from executor_birth_ownership_chain import (
    HEAD_PURPOSE, REQUIRED_HEAD_BASENAME, REQUIRED_HEAD_LOCK_BASENAME,
    OwnershipChainError,
    OwnershipChainStore,
    _OwnershipChainCrashForTest, _OwnershipChainStoreForTest,
    _InitialOwnershipChainStateForTestV1,
    _inspect_ownership_chain_state_for_test_v1,
    decode_required_head, encode_required_head, issue_ownership_head,
    inspect_ownership_chain_state_v1, verify_contiguous_chain,
    verify_ownership_head,
)
from executor_birth_ownership_cutover import (
    PAYLOAD_BASENAME, SIGNATURE_BASENAME,
    OwnershipCutoverKey, OwnershipCutoverRegistry,
    issue_ownership_cutover_certificate,
    ownership_key_id, verify_ownership_cutover_certificate,
)
from executor_birth_ownership_authorities import (
    OwnershipPublicRegistriesV1, _ownership_public_registries_for_test,
    decode_ownership_registry_v1, encode_ownership_registry_v1,
)
from executor_birth_ownership_preflight import _sealed_build_identity_for_test
from executor_birth_distribution_manifest import (
    DistributionKey, DistributionManifestError, DistributionRegistry,
    _verified_distribution_for_test, distribution_key_id,
    _verify_distribution_manifest_for_test,
)
from contract_boundary_guard import (
    BIRTH_CLOSED_COORDINATOR_STORE_OWNERS, BIRTH_CLOSED_EXCEPTION_SCOPES,
    BIRTH_CLOSED_GUARD_VERSION, BIRTH_CLOSED_OWNER, BIRTH_CLOSED_SCHEMA,
    BIRTH_CLOSED_SEALED_MODULES, SCAN_ROOTS,
    SCHEMA as BOUNDARY_INVENTORY_SCHEMA,
)


def D(char: str) -> str:
    return "sha256:" + char * 64


@dataclass(frozen=True)
class Authorities:
    distribution_private: Ed25519PrivateKey
    distribution_key_id: str
    distribution_registry: DistributionRegistry
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
        distribution_private, distribution_key.key_id,
        public.distribution, cutover_private, cutover_key_id, cutover_registry,
        head_private, head_key_id, head_registry, public,
    )


def initialize_test_store(root, authority):
    """Private portable seam; it is not evidence of root-owned cold loading."""
    return _OwnershipChainStoreForTest._initialize_with_authorities(
        root, authority.public,
    )


def open_test_store(root, authority):
    """Open an existing portable store through the same private test seam."""
    return _OwnershipChainStoreForTest(root, authority.public)


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


def _cold_canonical(value):
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def _cold_inventory_bytes():
    return _cold_canonical({
        "schema": BOUNDARY_INVENTORY_SCHEMA,
        "source_census": "signed-release",
        "scan_roots": list(SCAN_ROOTS),
        "entries": [],
        "birth_closed": {
            "schema": BIRTH_CLOSED_SCHEMA,
            "guard_version": BIRTH_CLOSED_GUARD_VERSION,
            "owner": BIRTH_CLOSED_OWNER,
            "coordinator_store_owners": sorted(
                BIRTH_CLOSED_COORDINATOR_STORE_OWNERS
            ),
            "sealed_modules": list(BIRTH_CLOSED_SEALED_MODULES),
            "exceptions": [
                {"scope": scope, "exception": exception}
                for scope, exception in sorted(
                    BIRTH_CLOSED_EXCEPTION_SCOPES.items()
                )
            ],
        },
    })


def _cold_distribution(
    root: Path, authority: Authorities, *, sequence: int,
    previous_closed_build_id: str | None,
):
    inventory = _cold_inventory_bytes()
    content_by_path = {
        "requirements.lock": ("dependency_lock", b"cryptography==47.0.0\n"),
        "runtime/__version__.py": ("product_version", b'__version__ = "1.2.3"\n'),
        "runtime/contract_boundary_guard.py": ("boundary_guard", b"GUARD = 1\n"),
        "runtime/contract_store.py": ("runtime_code", b"STORE = 1\n"),
        "runtime/executor_birth.py": ("runtime_code", b"BIRTH = 1\n"),
        "runtime/executor_birth_distribution_manifest.py": ("preflight", b"VERIFY = 1\n"),
        "runtime/executor_birth_ownership_preflight.py": ("preflight", b"PREFLIGHT = 1\n"),
        "runtime/sign.py": ("runtime_code", b"SIGN = 1\n"),
        "share/metnos/executor-birth/birth-closed-boundary-inventory-v1.json": (
            "boundary_inventory", inventory,
        ),
        "systemd/metnos-http-birth-closed.conf": ("service_unit", b"[Service]\n"),
    }
    files = []
    for path, (role, content) in content_by_path.items():
        target = root.joinpath(*path.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        files.append({
            "path": path, "size": len(content), "role": role,
            "content_hash": distribution_module.file_content_hash(path, content),
        })
    files.sort(key=lambda item: item["path"].encode("utf-8"))
    value = {
        "schema_version": 1,
        "closed_build_id": None,
        "previous_closed_build_id": previous_closed_build_id,
        "release_sequence": sequence,
        "product_version": "1.2.3",
        "platform": "linux",
        "architecture": "x86_64",
        "signing_key_id": authority.distribution_key_id,
        "installation_root": root.as_posix(),
        "certificate_directory": "/var/lib/metnos/executor-birth",
        "boundary_inventory_path": (
            "share/metnos/executor-birth/birth-closed-boundary-inventory-v1.json"
        ),
        "boundary_inventory_hash": "sha256:" + hashlib.sha256(
            distribution_module.BOUNDARY_INVENTORY_DOMAIN + inventory
        ).hexdigest(),
        "boundary_guard_version": BIRTH_CLOSED_GUARD_VERSION,
        "preflight_entrypoint": (
            "runtime/executor_birth_distribution_manifest.py"
        ),
        "files": files,
    }
    value["closed_build_id"] = "sha256:" + hashlib.sha256(
        distribution_module.BUILD_ID_DOMAIN + _cold_canonical({
            key: item for key, item in value.items()
            if key != "closed_build_id"
        })
    ).hexdigest()
    encoded = _cold_canonical(value)
    signature = authority.distribution_private.sign(
        distribution_module.SIGNATURE_DOMAIN + encoded
    )
    verified = _verify_distribution_manifest_for_test(
        encoded, signature, registry=authority.distribution_registry,
        _environment=distribution_module._environment_for_test(
            "linux", "x86_64", root,
        ),
    )
    return verified


def _cold_chain_worker(
    chain_root: str, result_queue,
):
    try:
        authority_root = Path(chain_root).parent / "authorities-v1"
        public = _ownership_public_registries_for_test(
            decode_ownership_registry_v1(
                (authority_root / "distribution-registry-v1.json").read_bytes(),
                expected_kind="distribution",
            ),
            decode_ownership_registry_v1(
                (authority_root / "cutover-registry-v1.json").read_bytes(),
                expected_kind="cutover",
            ),
            decode_ownership_registry_v1(
                (authority_root / "head-registry-v1.json").read_bytes(),
                expected_kind="head",
            ),
        )
        store = _OwnershipChainStoreForTest(Path(chain_root), public)
        verified = store._read_required_chain_cold_for_test()
        result_queue.put((
            "ok",
            tuple(record.release_sequence for record in verified.authenticated_records),
            verified.required_distribution.release_sequence,
        ))
    except Exception as exc:
        result_queue.put((
            "error", type(exc).__name__, str(exc), repr(exc.__cause__),
        ))


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
    assert tuple(inspect.signature(OwnershipChainStore).parameters) == ()
    assert tuple(inspect.signature(OwnershipChainStore.initialize).parameters) == ()
    assert tuple(
        inspect.signature(OwnershipChainStore.read_required_chain_cold_v1).parameters
    ) == ("self",)
    assert not hasattr(OwnershipChainStore, "_initialize_with_authorities")
    assert not hasattr(OwnershipChainStore, "_open_with_authorities")


def test_product_store_fails_closed_off_linux_before_authority_or_filesystem(
    monkeypatch,
):
    import executor_birth_ownership_authorities as authority_module

    def unexpected(*_args, **_kwargs):
        raise AssertionError("productive gate performed I/O")

    monkeypatch.setattr(chain_module.sys, "platform", "win32")
    monkeypatch.setattr(
        authority_module, "_load_fixed_ownership_public_snapshot_v1", unexpected,
    )
    monkeypatch.setattr(chain_module.Path, "mkdir", unexpected)
    for operation in (
        OwnershipChainStore,
        OwnershipChainStore.initialize,
        OwnershipChainStore.__new__(OwnershipChainStore).read_required_chain_cold_v1,
    ):
        with pytest.raises(OwnershipChainError) as failure:
            operation()
        assert failure.value.code == "birth_ownership_platform_unsupported"


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory modes")
def test_exact_directory_creation_and_retry_ignore_restrictive_umask(tmp_path):
    target = tmp_path / "chain-v1"
    previous_umask = os.umask(0o077)
    try:
        target.mkdir(mode=0o755)
    finally:
        os.umask(previous_umask)
    assert target.stat().st_mode & 0o777 == 0o700
    first = chain_module._ensure_exact_directory_v1(target)
    second = chain_module._ensure_exact_directory_v1(target)
    assert first.st_ino == second.st_ino
    assert target.stat().st_mode & 0o777 == 0o755

    unsafe = tmp_path / "unsafe-chain-v1"
    unsafe.mkdir(mode=0o777)
    unsafe.chmod(0o777)
    with pytest.raises(OwnershipChainError, match="directory metadata"):
        chain_module._ensure_exact_directory_v1(unsafe)
    assert unsafe.stat().st_mode & 0o777 == 0o777


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory durability")
def test_exact_directory_retries_parent_sync_after_failure(tmp_path, monkeypatch):
    target = tmp_path / "chain-v1"
    calls = []
    real_sync = chain_module._sync_directory

    def fail_once(path):
        calls.append(Path(path))
        if len(calls) == 1:
            raise OSError("injected parent fsync failure")
        real_sync(path)

    monkeypatch.setattr(chain_module, "_sync_directory", fail_once)
    with pytest.raises(OwnershipChainError) as failure:
        chain_module._ensure_exact_directory_v1(target)
    assert failure.value.detail == "directory sync"
    chain_module._ensure_exact_directory_v1(target)
    assert calls == [tmp_path, tmp_path]
    assert target.stat().st_mode & 0o777 == 0o755


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory durability")
def test_exact_directory_retries_target_sync_after_failure(tmp_path, monkeypatch):
    target = tmp_path / "chain-v1"
    calls = []
    real_fsync = os.fsync

    def fail_target_once(fd):
        target_inode = target.stat().st_ino if target.exists() else None
        observed = "target" if os.fstat(fd).st_ino == target_inode else "parent"
        calls.append(observed)
        if calls == ["target"]:
            raise OSError("injected target fsync failure")
        real_fsync(fd)

    monkeypatch.setattr(chain_module.os, "fsync", fail_target_once)
    with pytest.raises(OwnershipChainError) as failure:
        chain_module._ensure_exact_directory_v1(target)
    assert failure.value.detail == "directory metadata"
    chain_module._ensure_exact_directory_v1(target)
    assert calls == ["target", "target", "parent"]
    assert target.stat().st_mode & 0o777 == 0o755


@pytest.mark.skipif(os.name == "nt", reason="cold ownership store is Linux-only")
@pytest.mark.parametrize(
    "mutation",
    (
        "ok", "historical_signature", "predecessor", "sequence", "gap",
        "duplicate", "fork", "required_head",
    ),
)
def test_cold_two_release_chain_reopens_from_disk(
    authority, tmp_path, mutation, monkeypatch,
):
    ownership_root = tmp_path / "ownership"
    ownership_root.mkdir(mode=0o755)
    chain_root = ownership_root / "chain-v1"
    releases = ownership_root / "releases-v1"
    releases.mkdir(mode=0o755)
    first_root = releases / "00000000000000000001"
    second_root = releases / "00000000000000000002"
    first = _cold_distribution(
        first_root, authority, sequence=1, previous_closed_build_id=None,
    )
    second = _cold_distribution(
        second_root, authority,
        sequence=3 if mutation == "sequence" else 2,
        previous_closed_build_id=(
            D("f") if mutation == "predecessor"
            else first.identity.closed_build_id
        ),
    )
    store = _OwnershipChainStoreForTest._initialize_with_authorities(
        chain_root, authority.public,
    )
    store.append_authenticated_build(first)
    store.append_authenticated_build(second)

    first_cutover_bytes, first_cutover_signature, first_cutover = cutover(
        authority, previous=None, build=first.identity.closed_build_id,
        request=D("5"),
    )
    second_cutover_bytes, second_cutover_signature, second_cutover = cutover(
        authority, previous=first_cutover.cutover_id,
        build=second.identity.closed_build_id, request=D("7"),
    )
    store.append_cutover(first_cutover_bytes, first_cutover_signature)
    store.append_cutover(second_cutover_bytes, second_cutover_signature)
    (ownership_root / "ownership-cutover-v1.json").write_bytes(
        first_cutover_bytes
    )
    (ownership_root / "ownership-cutover-v1.sig").write_bytes(
        first_cutover_signature
    )
    (ownership_root / "ownership-cutover-v1.json").chmod(0o644)
    (ownership_root / "ownership-cutover-v1.sig").chmod(0o644)

    first_head_bytes, first_head_signature = issue_ownership_head(
        release_sequence=1, cutover_id=first_cutover.cutover_id,
        closed_build_id=first.identity.closed_build_id,
        previous_head_id=None, signing_key_id=authority.head_key_id,
        private_key=authority.head_private,
    )
    first_head = store.append_head(first_head_bytes, first_head_signature)
    second_head_bytes, second_head_signature = issue_ownership_head(
        release_sequence=2, cutover_id=second_cutover.cutover_id,
        closed_build_id=second.identity.closed_build_id,
        previous_head_id=first_head.head_id,
        signing_key_id=authority.head_key_id,
        private_key=authority.head_private,
    )
    store.append_head(second_head_bytes, second_head_signature)
    store.update_required_head(
        first_head_bytes, first_head_signature, expected_head_id=None,
    )
    store.update_required_head(
        second_head_bytes, second_head_signature,
        expected_head_id=first_head.head_id,
    )

    if mutation == "historical_signature":
        (chain_root / "builds-v1" / (
            first.identity.closed_build_id.removeprefix("sha256:") + ".sig"
        )).write_bytes(b"x" * 64)
    elif mutation == "gap":
        first_stem = (
            f"{1:020d}-{first_cutover.cutover_id.removeprefix('sha256:')}"
        )
        (chain_root / "heads-v1" / f"{first_stem}.json").unlink()
        (chain_root / "heads-v1" / f"{first_stem}.sig").unlink()
    elif mutation == "duplicate":
        duplicate_root = releases / "duplicate-sequence-one"
        duplicate = _cold_distribution(
            duplicate_root, authority, sequence=1,
            previous_closed_build_id=None,
        )
        store.append_authenticated_build(duplicate)
        shutil.rmtree(duplicate_root)
    elif mutation == "fork":
        fork_cutover_bytes, fork_cutover_signature, fork_cutover = cutover(
            authority, previous=first_cutover.cutover_id,
            build=second.identity.closed_build_id, request=D("8"),
        )
        store.append_cutover(fork_cutover_bytes, fork_cutover_signature)
        fork_head_bytes, fork_head_signature = issue_ownership_head(
            release_sequence=2, cutover_id=fork_cutover.cutover_id,
            closed_build_id=second.identity.closed_build_id,
            previous_head_id=first_head.head_id,
            signing_key_id=authority.head_key_id,
            private_key=authority.head_private,
        )
        store.append_head(fork_head_bytes, fork_head_signature)
    elif mutation == "required_head":
        required = chain_root / chain_module.REQUIRED_HEAD_BASENAME
        required.write_bytes(required.read_bytes() + b"x")

    shutil.rmtree(first_root)
    assert not first_root.exists()
    authority_root = ownership_root / "authorities-v1"
    authority_root.mkdir(mode=0o755)
    registry_bytes = {
        "distribution-registry-v1.json": encode_ownership_registry_v1(
            "distribution", authority.distribution_private.public_key(),
        ),
        "cutover-registry-v1.json": encode_ownership_registry_v1(
            "cutover", authority.cutover_private.public_key(),
        ),
        "head-registry-v1.json": encode_ownership_registry_v1(
            "head", authority.head_private.public_key(),
        ),
    }
    for basename, payload in registry_bytes.items():
        path = authority_root / basename
        path.write_bytes(payload)
        path.chmod(0o644)
    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue()
    process = context.Process(
        target=_cold_chain_worker,
        args=(str(chain_root), result_queue),
    )
    process.start()
    process.join(15)
    if process.is_alive():
        process.terminate()
        process.join(5)
        pytest.fail("cold reader child did not terminate")
    assert process.exitcode == 0
    observed = result_queue.get(timeout=2)
    if mutation == "ok":
        assert observed == ("ok", (1, 2), 2)
        authority_loads = []

        def load_fixed_public():
            authority_loads.append("load")
            return authority.public

        monkeypatch.setattr(
            authority_module, "load_ownership_public_registries_v1",
            load_fixed_public,
        )
        monkeypatch.setattr(
            chain_module, "DEFAULT_OWNERSHIP_CHAIN_ROOT_V1", chain_root,
        )
        monkeypatch.setattr(
            distribution_module, "DEFAULT_RELEASE_DIRECTORY_V1", releases,
        )
        monkeypatch.setattr(
            distribution_module, "_require_product_release_metadata_v1",
            lambda _root: None,
        )
        real_lstat = Path.lstat
        metadata_mutation = [None]
        required_pointer = chain_root / chain_module.REQUIRED_HEAD_BASENAME
        anchor_files = {
            ownership_root / "ownership-cutover-v1.json",
            ownership_root / "ownership-cutover-v1.sig",
        }

        def productive_metadata_lstat(path):
            candidate = Path(path)
            info = real_lstat(candidate)
            relevant = (
                candidate == chain_root
                or candidate in chain_root.parents
                or chain_root in candidate.parents
                or candidate in anchor_files
            )
            if not relevant:
                return info
            mode = info.st_mode
            uid = gid = 0
            links = info.st_nlink
            mutation_name = metadata_mutation[0]
            target = (
                None if mutation_name is None else
                ownership_root if mutation_name.startswith("chain_")
                else required_pointer
            )
            if candidate == target:
                if mutation_name.endswith("link"):
                    mode = stat.S_IFLNK | 0o777
                elif mutation_name.endswith("mode"):
                    mode = (
                        stat.S_IFDIR | 0o775
                        if mutation_name.startswith("chain_")
                        else stat.S_IFREG | 0o664
                    )
                elif mutation_name.endswith("owner"):
                    uid = 1000
                elif mutation_name.endswith("hardlink"):
                    links = 2
            if candidate in chain_root.parents and candidate != ownership_root:
                mode = stat.S_IFDIR | 0o755
            return SimpleNamespace(
                st_mode=mode, st_uid=uid, st_gid=gid, st_nlink=links,
                st_dev=info.st_dev, st_ino=info.st_ino,
                st_file_attributes=0,
            )

        monkeypatch.setattr(Path, "lstat", productive_metadata_lstat)
        original_full_verify = (
            distribution_module._verify_authenticated_distribution_record
        )
        product_routes = []

        def verify_product_record(record, environment, *, for_test):
            product_routes.append((type(record), for_test))
            relaxed = distribution_module._environment_for_test(
                environment.platform, environment.architecture,
                environment.installation_root,
                claimed_installation_root=environment.claimed_installation_root,
                verify_static_boundary=False,
            )
            return original_full_verify(record, relaxed, for_test=for_test)

        monkeypatch.setattr(
            distribution_module, "_verify_authenticated_distribution_record",
            verify_product_record,
        )
        productive = OwnershipChainStore()
        product_result = productive.read_required_chain_cold_v1()
        assert authority_loads == ["load"]
        assert product_routes == [
            (distribution_module.AuthenticatedDistributionRecordV1, False),
        ]
        assert tuple(
            type(record) for record in product_result.authenticated_records
        ) == (
            distribution_module.AuthenticatedDistributionRecordV1,
            distribution_module.AuthenticatedDistributionRecordV1,
        )
        completed_routes = len(product_routes)
        for mutation_name in (
            "chain_link", "chain_mode", "chain_owner",
            "object_link", "object_mode", "object_owner", "object_hardlink",
        ):
            metadata_mutation[0] = mutation_name
            with pytest.raises(
                OwnershipChainError, match="(?:chain|object) metadata",
            ):
                productive.read_required_chain_cold_v1()
            assert len(product_routes) == completed_routes
        metadata_mutation[0] = None
        productive.distribution_registry = decode_ownership_registry_v1(
            encode_ownership_registry_v1(
                "distribution", authority.distribution_private.public_key(),
            ),
            expected_kind="distribution",
        )
        with pytest.raises(OwnershipChainError, match="authority snapshot"):
            productive.read_required_chain_cold_v1()
    else:
        expected = {
            "historical_signature": "build object",
            "predecessor": "predecessor",
            "sequence": "object binding",
            "gap": "head gap",
            "duplicate": "build fork",
            "fork": "head fork",
            "required_head": "required",
        }[mutation]
        assert observed[0] == "error"
        assert expected in " ".join(str(item) for item in observed)


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


def test_chain_initial_inspection_is_empty_stable_and_nonmutating(
    authority, tmp_path,
):
    root = tmp_path / "chain-v1"
    store = initialize_test_store(root, authority)
    before = tuple(sorted(
        path.relative_to(tmp_path).as_posix()
        for path in tmp_path.rglob("*")
    ))

    state = _inspect_ownership_chain_state_for_test_v1(store)

    assert type(state) is _InitialOwnershipChainStateForTestV1
    assert state.root == root
    assert tuple(sorted(
        path.relative_to(tmp_path).as_posix()
        for path in tmp_path.rglob("*")
    )) == before


def test_chain_initial_inspection_rejects_inventory_change_before_mint(
    authority, tmp_path, monkeypatch,
):
    root = tmp_path / "chain-v1"
    store = initialize_test_store(root, authority)
    real_snapshot = chain_module._chain_inventory_snapshot_v1
    calls = []

    def changing_snapshot(observed_root):
        calls.append("snapshot")
        if len(calls) == 2:
            (root / REQUIRED_HEAD_BASENAME).write_bytes(b"appeared")
        return real_snapshot(observed_root)

    monkeypatch.setattr(
        chain_module, "_chain_inventory_snapshot_v1", changing_snapshot,
    )
    with pytest.raises(OwnershipChainError) as failure:
        _inspect_ownership_chain_state_for_test_v1(store)

    assert calls == ["snapshot", "snapshot"]
    assert failure.value.code == "birth_ownership_recovery_required"


@pytest.mark.parametrize(
    "partial",
    (
        "anchor-payload", "anchor-signature", "anchor-payload-temp",
        "anchor-signature-temp", "anchor-malformed-temp", "required",
        "required-lock", "head", "temporary",
    ),
)
def test_chain_initial_inspection_rejects_every_partial_prefix(
    authority, tmp_path, partial,
):
    root = tmp_path / "chain-v1"
    store = initialize_test_store(root, authority)
    target = {
        "anchor-payload": tmp_path / PAYLOAD_BASENAME,
        "anchor-signature": tmp_path / SIGNATURE_BASENAME,
        "anchor-payload-temp": (
            tmp_path / f".{PAYLOAD_BASENAME}.{'a' * 64}.tmp"
        ),
        "anchor-signature-temp": (
            tmp_path / f".{SIGNATURE_BASENAME}.{'b' * 64}.tmp"
        ),
        "anchor-malformed-temp": (
            tmp_path / f".{PAYLOAD_BASENAME}.bad.tmp"
        ),
        "required": root / REQUIRED_HEAD_BASENAME,
        "required-lock": root / REQUIRED_HEAD_LOCK_BASENAME,
        "head": root / "heads-v1" / ("0" * 64 + ".json"),
        "temporary": root / ".partial.tmp",
    }[partial]
    target.write_bytes(b"partial")

    with pytest.raises(OwnershipChainError) as failure:
        _inspect_ownership_chain_state_for_test_v1(store)

    assert failure.value.code == "birth_ownership_recovery_required"


@pytest.mark.parametrize(
    "mutation", ("mode", "owner", "hardlink", "marker", "size"),
)
def test_chain_inspection_rejects_unsafe_persistent_required_lock(
    authority, tmp_path, mutation, monkeypatch,
):
    root = tmp_path / "chain-v1"
    store = initialize_test_store(root, authority)
    (tmp_path / PAYLOAD_BASENAME).write_bytes(b"payload")
    (tmp_path / SIGNATURE_BASENAME).write_bytes(b"signature")
    (root / REQUIRED_HEAD_BASENAME).write_bytes(b"required")
    lock = root / REQUIRED_HEAD_LOCK_BASENAME
    lock.write_bytes(b"\0")
    if os.name != "nt":
        lock.chmod(0o600)
    if mutation == "mode":
        if os.name == "nt":
            pytest.skip("Windows has no exact POSIX lock mode")
        lock.chmod(0o644)
    elif mutation == "owner":
        if os.name == "nt":
            pytest.skip("Windows has no POSIX lock ownership")
        real_lstat = Path.lstat

        def wrong_lock_owner(path):
            info = real_lstat(path)
            if Path(path) != lock:
                return info
            return SimpleNamespace(
                st_mode=info.st_mode, st_nlink=info.st_nlink,
                st_size=info.st_size, st_uid=info.st_uid + 1,
                st_gid=info.st_gid + 1, st_file_attributes=0,
            )

        monkeypatch.setattr(Path, "lstat", wrong_lock_owner)
    elif mutation == "hardlink":
        try:
            os.link(lock, tmp_path / "required-lock-copy")
        except OSError:
            pytest.skip("hard links unavailable")
    elif mutation == "marker":
        lock.write_bytes(b"x")
    else:
        lock.write_bytes(b"\0x")

    def unexpected_cold_read(_self):
        raise AssertionError("unsafe lock reached the cold reader")

    monkeypatch.setattr(
        _OwnershipChainStoreForTest,
        "_read_required_chain_cold_for_test",
        unexpected_cold_read,
    )

    with pytest.raises(OwnershipChainError) as failure:
        _inspect_ownership_chain_state_for_test_v1(store)

    assert failure.value.code == "birth_ownership_recovery_required"
    assert failure.value.detail == "required lock metadata"


def test_chain_inspection_delegates_complete_prefix_with_known_lock(
    authority, tmp_path, monkeypatch,
):
    root = tmp_path / "chain-v1"
    store = initialize_test_store(root, authority)
    (tmp_path / PAYLOAD_BASENAME).write_bytes(b"payload")
    (tmp_path / SIGNATURE_BASENAME).write_bytes(b"signature")
    (root / REQUIRED_HEAD_BASENAME).write_bytes(b"required")
    (root / REQUIRED_HEAD_LOCK_BASENAME).write_bytes(b"\0")
    if os.name != "nt":
        (root / REQUIRED_HEAD_LOCK_BASENAME).chmod(0o600)
    sentinel = object()
    calls = []

    def cold_success(_self):
        calls.append("cold")
        return sentinel

    monkeypatch.setattr(
        _OwnershipChainStoreForTest,
        "_read_required_chain_cold_for_test",
        cold_success,
    )

    assert _inspect_ownership_chain_state_for_test_v1(store) is sentinel
    assert calls == ["cold"]


def test_chain_inspection_never_falls_back_to_initial_after_cold_failure(
    authority, tmp_path, monkeypatch,
):
    root = tmp_path / "chain-v1"
    store = initialize_test_store(root, authority)
    (tmp_path / PAYLOAD_BASENAME).write_bytes(b"payload")
    (tmp_path / SIGNATURE_BASENAME).write_bytes(b"signature")
    (root / REQUIRED_HEAD_BASENAME).write_bytes(b"required")
    calls = []

    def cold_failure(_self):
        calls.append("cold")
        raise OwnershipChainError("birth_ownership_distribution_chain_invalid")

    monkeypatch.setattr(
        _OwnershipChainStoreForTest,
        "_read_required_chain_cold_for_test",
        cold_failure,
    )
    with pytest.raises(OwnershipChainError) as failure:
        _inspect_ownership_chain_state_for_test_v1(store)

    assert calls == ["cold"]
    assert failure.value.code == "birth_ownership_recovery_required"


def test_product_initial_state_cannot_be_constructed_with_a_public_seal():
    assert not hasattr(chain_module, "_INITIAL_CHAIN_STATE_SEAL_V1")
    with pytest.raises(OwnershipChainError) as failure:
        chain_module._InitialOwnershipChainStateV1(
            chain_module.DEFAULT_OWNERSHIP_CHAIN_ROOT_V1, object(),
        )

    assert failure.value.code == "birth_ownership_recovery_required"


def test_initial_state_core_rejects_a_forged_product_store():
    forged = object.__new__(OwnershipChainStore)
    forged.root = chain_module.DEFAULT_OWNERSHIP_CHAIN_ROOT_V1
    fake_public = SimpleNamespace(
        distribution=object(), cutover=object(), head=object(),
    )
    forged._fixed_authority_snapshot = SimpleNamespace(public=fake_public)
    forged._authorities = fake_public
    forged.distribution_registry = fake_public.distribution
    forged.cutover_registry = fake_public.cutover
    forged.head_registry = fake_public.head

    with pytest.raises(OwnershipChainError) as failure:
        chain_module._inspect_ownership_chain_state_core_v1(
            forged, for_test=False,
        )

    assert failure.value.code == "birth_ownership_recovery_required"
    assert failure.value.detail == "productive store"


def test_initial_state_core_rejects_hostile_store_before_getters():
    class HostileStore:
        @property
        def _fixed_authority_snapshot(self):
            raise RuntimeError("hostile getter escaped")

    with pytest.raises(OwnershipChainError) as failure:
        chain_module._inspect_ownership_chain_state_core_v1(
            HostileStore(), for_test=False,
        )

    assert failure.value.code == "birth_ownership_recovery_required"
    assert failure.value.detail == "productive store"


def test_product_chain_inspection_fails_off_linux_before_io(monkeypatch):
    def unexpected(*_args, **_kwargs):
        raise AssertionError("chain inspection performed I/O")

    monkeypatch.setattr(chain_module.sys, "platform", "win32")
    monkeypatch.setattr(chain_module, "OwnershipChainStore", unexpected)
    monkeypatch.setattr(chain_module.Path, "lstat", unexpected)

    with pytest.raises(OwnershipChainError) as failure:
        inspect_ownership_chain_state_v1()

    assert failure.value.code == "birth_ownership_platform_unsupported"
