"""Fixed-root F5 activation decoding; fixture signatures are not qualification."""
from __future__ import annotations

import base64
import hashlib
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import executor_birth_lifecycle as lifecycle
from executor_birth_canonical import encode_canonical_ascii_v1 as encode
from executor_birth_certification_authority import CertificationPublicKeyV1
from executor_birth_keystore import birth_key_id


def digest(label):
    return "sha256:" + hashlib.sha256(label.encode()).hexdigest()


@pytest.fixture
def certificate():
    private = Ed25519PrivateKey.generate()
    public = private.public_key()
    authority = CertificationPublicKeyV1(birth_key_id(public), public, "active")
    payload = dict(
        schema_version=1, purpose="f5_activation_v1", policy_id="rm0008-f5/1",
        installation_id=digest("installation"), qualification_id=digest("qualification"),
        head_id=digest("head"), closed_build_id=digest("build"),
        migration_id=digest("migration"), key_id=authority.key_id,
    )

    def sign(value=payload, *, domain=lifecycle.CERTIFICATION_DOMAIN):
        return encode({**value, "signature": base64.b64encode(
            private.sign(domain + encode(value))).decode("ascii")})

    def decode(raw=None, *, selected=authority):
        return lifecycle._decode_f5_activation_v1(
            sign() if raw is None else raw, authority=selected,
            installation_id=payload["installation_id"], head_id=payload["head_id"],
            closed_build_id=payload["closed_build_id"],
        )

    return payload, authority, sign, decode


def test_compact_attestation_roundtrip_does_not_accept_threshold_counts(certificate):
    payload, authority, sign, decode = certificate
    observed = decode().certificate
    assert observed.certificate_id == "sha256:" + hashlib.sha256(
        lifecycle.CERTIFICATION_DOMAIN + encode(payload)).hexdigest()
    assert observed.qualification_id == payload["qualification_id"]
    assert observed.migration_id == payload["migration_id"]
    assert observed.key_id == authority.key_id
    with pytest.raises(TypeError):
        lifecycle.load_f5_activation(sign(), authorities={authority.key_id: authority.public_key})


@pytest.mark.parametrize("field,value", [
    ("schema_version", True), ("schema_version", 2),
    ("purpose", "f5_certification_v1"), ("policy_id", "unknown"),
    ("installation_id", digest("another installation")),
    ("head_id", digest("another head")), ("closed_build_id", digest("another build")),
    ("qualification_id", "not-a-digest"), ("migration_id", None),
    ("key_id", "another-key"), ("routing_cycles", 2),
    ("admission_receipt_ids", [digest(str(index)) for index in range(5)]),
], ids=["bool-version", "version", "purpose", "policy", "installation", "head",
        "build", "qualification", "migration", "authority", "caller-cycles", "invented-receipts"])
def test_even_signed_wrong_context_and_extended_claims_are_refused(certificate, field, value):
    payload, _authority, sign, decode = certificate
    with pytest.raises(lifecycle.LifecycleError, match="f5_activation_invalid"):
        decode(sign({**payload, field: value}))


@pytest.mark.parametrize("damage", ["signature", "domain", "base64", "duplicate", "spacing", "oversized", "revoked"])
def test_signature_and_canonical_document_are_strict(certificate, damage):
    payload, authority, sign, decode = certificate
    raw = sign()
    selected = authority
    if damage == "signature":
        other = Ed25519PrivateKey.generate().public_key()
        selected = replace(authority, public_key=other)
    elif damage == "domain":
        raw = sign(domain=b"another-purpose\0")
    elif damage == "base64":
        import json
        value = json.loads(raw)
        value["signature"] = value["signature"].rstrip("=")
        raw = encode(value)
    elif damage == "duplicate":
        raw = b'{"schema_version":1,' + raw[1:]
    elif damage == "spacing":
        raw += b"\n"
    elif damage == "oversized":
        raw += b" " * lifecycle.ACTIVATION_MAX_BYTES
    elif damage == "revoked":
        selected = replace(authority, status="revoked")
    with pytest.raises(lifecycle.LifecycleError, match="f5_activation_invalid"):
        decode(raw, selected=selected)


@pytest.fixture
def installed_reader(tmp_path, monkeypatch, certificate):
    """Real bounded file reads; owner-return fixtures are not native trust proof."""
    if not sys.platform.startswith("linux"):
        pytest.skip("installed authority custody is Linux-only; codec remains portable")
    import executor_birth_authority_files as files
    import executor_birth_certification_authority as authority_owner
    import executor_birth_ownership_authorities as ownership
    import executor_birth_ownership_chain as chain

    payload, authority, sign, _decode = certificate
    registries = SimpleNamespace(**{
        purpose: SimpleNamespace(keys={purpose: SimpleNamespace(key_id=purpose + "-key")})
        for purpose in ("distribution", "cutover", "head")
    })
    payload["installation_id"] = lifecycle._installation_id_v1(registries)
    directory = tmp_path / "activation"
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)
    path = directory / "active.json"
    path.write_bytes(sign())
    path.chmod(0o644)
    window = chain.VerifiedOwnershipWindowV1(
        heads=(SimpleNamespace(head_id=payload["head_id"]),),
        authenticated_records=(), context_transitions=(),
        required_distribution=SimpleNamespace(identity=SimpleNamespace(
            closed_build_id=payload["closed_build_id"])),
    )
    monkeypatch.setattr(lifecycle, "ACTIVATION_DIRECTORY", directory)
    monkeypatch.setattr(lifecycle, "_root_owned_chain", lambda location: None)
    monkeypatch.setattr(lifecycle, "_directory_metadata", lambda location, **kw: files._directory_metadata(location, root_owned=False))
    monkeypatch.setattr(lifecycle, "_read_regular", lambda location, **kw: files._read_regular(location, **{**kw, "root_owned": False}))
    monkeypatch.setattr(authority_owner, "load_certification_public_key_v1", lambda: authority)
    monkeypatch.setattr(ownership, "load_ownership_public_registries_v1", lambda: registries)
    monkeypatch.setattr(chain, "inspect_required_ownership_v1", lambda: window)
    monkeypatch.setattr(chain, "OwnershipChainStore", lambda: SimpleNamespace(read_required_head=lambda: window.required_head))
    monkeypatch.setattr(chain, "inspect_ownership_chain_state_v1", lambda: pytest.fail("historical replay"))
    return path, window, authority, registries


def test_fixed_entry_reads_only_current_public_material(installed_reader):
    path, window, authority, _registries = installed_reader
    result = lifecycle.load_f5_activation()
    assert result.certificate.head_id == window.required_head.head_id
    assert result.certificate.key_id == authority.key_id
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("change", ["document", "key", "ownership", "head"])
def test_frontier_change_during_read_is_refused(installed_reader, monkeypatch, change):
    import executor_birth_certification_authority as authority_owner
    import executor_birth_ownership_authorities as ownership
    import executor_birth_ownership_chain as chain

    path, window, authority, registries = installed_reader
    original_read = lifecycle._read_regular
    reads = []

    def read(location, **kwargs):
        reads.append(location)
        if change == "document" and len(reads) == 2:
            return b"{}"
        return original_read(location, **kwargs)

    monkeypatch.setattr(lifecycle, "_read_regular", read)
    if change == "key":
        keys = iter([authority, replace(authority, key_id="another-key")])
        monkeypatch.setattr(authority_owner, "load_certification_public_key_v1", lambda: next(keys))
    elif change == "ownership":
        altered = SimpleNamespace(**vars(registries))
        altered.head = SimpleNamespace(keys={"new": SimpleNamespace(key_id="new-head-key")})
        values = iter([registries, altered])
        monkeypatch.setattr(ownership, "load_ownership_public_registries_v1", lambda: next(values))
    elif change == "head":
        monkeypatch.setattr(chain, "OwnershipChainStore", lambda: SimpleNamespace(
            read_required_head=lambda: SimpleNamespace(head_id=digest("new head"))))
    with pytest.raises(lifecycle.LifecycleError, match="changed activation frontier"):
        lifecycle.load_f5_activation()


def test_missing_document_cannot_fall_back_to_caller_material(installed_reader):
    path, *_ = installed_reader
    path.unlink()
    with pytest.raises((OSError, RuntimeError)):
        lifecycle.load_f5_activation()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="ownership installation is Linux-only")
def test_activation_follows_authenticated_files_not_fixture_claims(tmp_path, monkeypatch, certificate):
    """Compose real signed F4 files and F5 decoding through isolated owner seams.

    Reuse the preceding group's filesystem fixture, not its entire suite.
    This observes signatures, required-head selection and current file bytes;
    native administrator custody is covered by the existing owner family.
    """
    import executor_birth_authority_files as files
    import executor_birth_certification_authority as certification
    import executor_birth_ownership_authorities as ownership
    import executor_birth_ownership_chain as chain
    from test_executor_birth_ownership_chain import authority, _window_history, _read_window

    f4 = authority.__wrapped__()
    store, builds = _window_history(tmp_path, f4, 1)
    root = store.root.parent
    registry_dir = root / "authorities-v1"
    registry_dir.mkdir(mode=0o755)
    for kind in ("distribution", "cutover", "head"):
        path = registry_dir / f"{kind}-registry-v1.json"
        path.write_bytes(ownership.encode_ownership_registry_v1(
            kind, getattr(f4, kind + "_private").public_key()))
        path.chmod(0o644)

    def public_ownership():
        registries = [ownership.decode_ownership_registry_v1(files._read_regular(
            registry_dir / f"{kind}-registry-v1.json", maximum=65536,
            mode=0o644, root_owned=False), expected_kind=kind)
            for kind in ("distribution", "cutover", "head")]
        return ownership._ownership_public_registries_for_test(*registries)

    payload, public, sign, _decode = certificate
    payload.update(
        installation_id=lifecycle._installation_id_v1(public_ownership()),
        head_id=store.read_required_head().head_id,
        closed_build_id=builds[0].identity.closed_build_id,
    )
    cert_dir = root / certification.DIRECTORY_BASENAME_V1
    cert_dir.mkdir(mode=0o755)
    (cert_dir / certification.REGISTRY_BASENAME_V1).write_bytes(
        certification.encode_certification_registry_v1(public.public_key))
    (cert_dir / certification.REGISTRY_BASENAME_V1).chmod(0o644)
    activation_dir = root / "certification-v1"
    activation_dir.mkdir(mode=0o755)
    (activation_dir / "active.json").write_bytes(sign())
    (activation_dir / "active.json").chmod(0o644)
    monkeypatch.setattr(lifecycle, "ACTIVATION_DIRECTORY", activation_dir)
    monkeypatch.setattr(lifecycle, "_root_owned_chain", lambda path: None)
    monkeypatch.setattr(lifecycle, "_directory_metadata", lambda path, **kw:
                        files._directory_metadata(path, root_owned=False))
    monkeypatch.setattr(lifecycle, "_read_regular", lambda path, **kw:
                        files._read_regular(path, **{**kw, "root_owned": False}))
    monkeypatch.setattr(certification, "load_certification_public_key_v1", lambda:
                        certification._load_certification_public_at_v1(cert_dir, root_owned=False))
    monkeypatch.setattr(ownership, "load_ownership_public_registries_v1", public_ownership)
    monkeypatch.setattr(chain, "inspect_required_ownership_v1", lambda: _read_window(store))
    monkeypatch.setattr(chain, "OwnershipChainStore", lambda: store)
    monkeypatch.setattr(chain, "inspect_ownership_chain_state_v1", lambda: pytest.fail("full-history replay"))

    accepted = lifecycle.load_f5_activation().certificate
    assert accepted.head_id == payload["head_id"]
    assert accepted.closed_build_id == payload["closed_build_id"]
    # Keep the signed certificate unchanged, but damage its actual release.
    # A metadata-only mocked window would incorrectly continue to accept it.
    changed = root / "releases-v1" / f"{1:020d}" / "runtime" / "__version__.py"
    changed.write_bytes(b"changed\n")
    with pytest.raises(RuntimeError):
        lifecycle.load_f5_activation()
