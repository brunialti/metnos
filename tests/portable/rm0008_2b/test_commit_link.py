"""The private commit link: facts in, publication out, nothing selectable.

Section 5.3 is a shape, not an intention: what is certified here is that the
core cannot choose an authority, and that the public view of a prepared bundle
leads to no key, no core, no factory and no signing closure (section 5.4).
"""
from __future__ import annotations

import os
from dataclasses import FrozenInstanceError, fields

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)

import executor_birth_commit_publisher as link
from executor_birth_commit_publisher import (
    BirthCommitFactsV1, BirthCommitLinkError, PreparedBundleViewV1,
)

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="the Windows profile is certified by its own job"
)

DIGEST = "sha256:" + "1" * 64


def _facts(**overrides) -> BirthCommitFactsV1:
    values = {
        "manifest_ref": object(),
        "snapshot": object(),
        "request_id": DIGEST,
        "birth_request_id": DIGEST,
        "policy_version": "birth-policy-v1",
        "contract_id": "contract",
        "candidate_id": DIGEST,
        "semantic_core_id": DIGEST,
        "admission_context_id": DIGEST,
        "expected_generation_id": None,
        "predecessor_id": None,
        "predecessor_snapshot_id": None,
        "revision_facts_id": None,
        "observed_context_epoch": DIGEST,
        "producer_receipt_hash": DIGEST,
        "revision_class": "code_revision",
        "approved_lifecycle": "active",
        "check_results": {},
        "semantic_review_hash": None,
        "approval_hash": None,
        "issued_at": "2026-08-27T12:00:00Z",
    }
    values.update(overrides)
    return BirthCommitFactsV1(**values)


def _keystore(active_id: str):
    private = Ed25519PrivateKey.generate()
    return type("Loaded", (), {
        "active_key_id": active_id,
        "active_private_key": private,
        "verifier_keys": {active_id: private.public_key()},
    })()


def _bundle():
    return link._build_prepared_bundle_v1(
        author=_keystore("author-key"),
        admission=_keystore("admission-key"),
        set_id="a" * 64,
        prepared_admission_context_id=DIGEST,
        prepared_context_epoch=DIGEST,
        store_root=None,
    )


def test_the_facts_are_values_only():
    from types import MappingProxyType

    facts = _facts()
    assert isinstance(facts.check_results, MappingProxyType)
    with pytest.raises(FrozenInstanceError):
        facts.candidate_id = "other"
    with pytest.raises(TypeError):
        facts.check_results["x"] = 1


@pytest.mark.parametrize("field", [
    "manifest_ref", "snapshot", "revision_class", "approved_lifecycle",
    "semantic_review_hash", "predecessor_id",
])
def test_no_field_may_carry_a_callable(field: str):
    with pytest.raises(BirthCommitLinkError) as error:
        _facts(**{field: lambda: None})
    assert error.value.args == ("birth_commit_facts_invalid",)


def test_the_facts_have_no_place_for_an_authority():
    names = {item.name for item in fields(BirthCommitFactsV1)}
    assert not names & {
        "issuer", "verifier", "private_key", "key", "authorization",
        "context_epoch_resolver", "publisher", "primitive", "store_root",
        "trusted_publics",
    }


def test_the_publisher_cannot_be_built_from_outside():
    with pytest.raises(BirthCommitLinkError) as error:
        link._BirthCommitPublisher(
            object(),
            author_private=Ed25519PrivateKey.generate(),
            author_ring=(),
            admission_private=Ed25519PrivateKey.generate(),
            admission_key_id="k",
            admission_verifiers={"k": Ed25519PrivateKey.generate().public_key()},
            prepared_context_epoch=DIGEST,
            primitive=None,
            store_root=None,
        )
    assert error.value.args == ("birth_commit_publisher_private",)


def test_the_commit_accepts_facts_and_nothing_else():
    bundle = _bundle()
    for wrong in (None, object(), {"candidate_id": DIGEST}, lambda: None):
        with pytest.raises(BirthCommitLinkError) as error:
            bundle.publisher.commit(wrong)
        assert error.value.args == ("birth_commit_facts_required",)


def test_the_public_view_leads_to_nothing_private():
    view = _bundle().view
    seen: set[int] = set()
    stack = [view]
    while stack:
        value = stack.pop()
        if id(value) in seen:
            continue
        seen.add(id(value))
        assert not isinstance(value, Ed25519PrivateKey)
        assert not isinstance(value, type(link)), "a module is reachable"
        assert not isinstance(value, link._BirthCommitPublisher)
        assert not isinstance(value, link._PreparedBirthBundleV1)
        if isinstance(value, Ed25519PublicKey) or isinstance(
            value, (str, int, bytes, bool)
        ) or value is None:
            continue
        assert not callable(value), f"a callable is reachable: {value!r}"
        if isinstance(value, dict) or hasattr(value, "items"):
            stack.extend(value.keys())
            stack.extend(value.values())
            continue
        if isinstance(value, (list, tuple, set, frozenset)):
            stack.extend(value)
            continue
        for name in getattr(type(value), "__slots__", ()):
            stack.append(getattr(value, name))
        stack.extend(vars(value).values() if hasattr(value, "__dict__") else ())


def test_the_view_is_immutable_and_declares_it_is_not_active():
    view = _bundle().view
    assert view.state == "prepared_not_active" and view.version == 1
    with pytest.raises(FrozenInstanceError):
        view.set_id = "b" * 64
    with pytest.raises(TypeError):
        view.author_public_keys["other"] = None
    assert not hasattr(view, "__dict__")
    with pytest.raises(BirthCommitLinkError):
        PreparedBundleViewV1(
            version=1,
            author_active_key_id="a",
            author_public_keys={},
            admission_active_key_id="b",
            admission_public_keys={},
            set_id="a" * 64,
            prepared_admission_context_id=DIGEST,
            prepared_context_epoch=DIGEST,
        )


def test_the_module_exports_no_publisher_and_no_factory():
    assert set(link.__all__) == {
        "BirthCommitFactsV1", "BirthCommitLinkError",
        "PREPARED_BUNDLE_STATE_V1", "PreparedBundleViewV1",
    }
    assert not any(
        name.startswith("build") or name.startswith("make")
        for name in link.__all__
    )


def test_the_primitive_is_the_single_owned_one():
    import inspect

    import contract_store

    source = inspect.getsource(link)
    assert "commit_birth_snapshot" in source
    assert source.count("primitive=") == 1
    assert "publish_signed_source" not in source
    assert "publish_technical_update" not in source
    bundle = _bundle()
    assert bundle.publisher._primitive is contract_store.commit_birth_snapshot


def test_the_publisher_owns_the_ring_that_authenticates_a_predecessor():
    """One place knows which keys authenticate a generation, and it is sealed."""
    import inspect

    bundle = _bundle()
    assert hasattr(bundle.publisher, "resolve_predecessor")
    source = inspect.getsource(link._BirthCommitPublisher.resolve_predecessor)
    # The ring and the store root come from the publisher, never from a
    # caller-supplied option.
    assert "self._author_ring" in source and "self._store_root" in source
    assert "trusted_publics=" in source and "options" not in source
