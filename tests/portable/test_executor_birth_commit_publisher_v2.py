"""Focused checks for the accepted V2 reattestation publisher boundary."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import contract_store
from executor_birth_commit_publisher import (
    BirthCommitLinkError,
    _BirthCommitPublisher,
    _PUBLISHER_TOKEN,
    _is_birth_reattestation_port,
)
from executor_birth_producer_context import ProducerRequestV2, _REQUEST_SEAL


def D(character: str) -> str:
    return "sha256:" + character * 64


CONTEXT = D("1")
EPOCH = D("2")


def _request(*, context: str = CONTEXT, epoch: str = EPOCH) -> ProducerRequestV2:
    return ProducerRequestV2(
        D("3"), D("4"), "explicit:alpha/manifest.toml", D("5"),
        context, D("6"), epoch, "7" * 64, D("8"), _REQUEST_SEAL,
    )


def _publisher(*, context: str | None = CONTEXT) -> _BirthCommitPublisher:
    author = Ed25519PrivateKey.generate()
    admission = Ed25519PrivateKey.generate()
    return _BirthCommitPublisher(
        _PUBLISHER_TOKEN,
        author_private=author,
        author_ring=(("author", author.public_key()),),
        admission_private=admission,
        admission_key_id="admission",
        admission_verifiers={"admission": admission.public_key()},
        prepared_admission_context_id=context,
        prepared_context_epoch=EPOCH,
        primitive=lambda *_args, **_kwargs: None,
        store_root="store-root",
        registry_reconciler=lambda _revision: None,
    )


def _expected() -> dict[str, object]:
    return {
        "candidate_id": D("9"),
        "semantic_core_id": D("a"),
        "predecessor_id": D("5"),
        "admission_context_id": CONTEXT,
    }


def _current(*, contract: str = "explicit:alpha/manifest.toml", generation: str = D("5")):
    ref = SimpleNamespace(contract_id=SimpleNamespace(value=contract))
    return SimpleNamespace(ref=ref, generation_id=generation)


def test_v2_port_delegates_through_fixed_context_authority(monkeypatch):
    observed: dict[str, object] = {}

    def persist(ref, encoded, **kwargs):
        observed.update(ref=ref, encoded=encoded, **kwargs)
        return b"durable"

    def read(ref, **kwargs):
        observed.update(read_ref=ref, read_kwargs=kwargs)
        return b"reread"

    monkeypatch.setattr(
        contract_store, "persist_current_reattestation_receipt_v2", persist,
    )
    monkeypatch.setattr(contract_store, "read_current_birth_receipt_v2", read)

    publisher = _publisher()
    port = publisher.reattestation_port()
    assert _is_birth_reattestation_port(port)
    current = _current()
    request = _request()
    expected = _expected()

    assert port.persist_v2(current, b"wire", expected, request) == b"durable"
    authorization = observed["authorization"]
    assert authorization.admission_context_id == CONTEXT
    assert authorization.context_epoch == EPOCH
    assert authorization.context_epoch_resolver() == EPOCH
    assert authorization.candidate_id == expected["candidate_id"]
    assert authorization.semantic_core_id == expected["semantic_core_id"]
    assert authorization.predecessor_id == current.generation_id
    assert observed["request"] is request
    assert observed["trusted_publics"] == publisher._author_ring
    assert observed["store_root"] == "store-root"

    assert port.read_v2(current, request) == b"reread"
    assert observed["read_kwargs"]["request"] is request


@pytest.mark.parametrize(
    "candidate",
    (
        (_current(), _request(context=D("b"))),
        (_current(), _request(epoch=D("c"))),
        (_current(contract="explicit:other/manifest.toml"), _request()),
        (_current(generation=D("d")), _request()),
    ),
)
def test_v2_port_rejects_a_request_outside_its_prepared_context(
    monkeypatch, candidate,
):
    called = False

    def persist(*_args, **_kwargs):
        nonlocal called
        called = True
        return b"unexpected"

    monkeypatch.setattr(
        contract_store, "persist_current_reattestation_receipt_v2", persist,
    )
    current, request = candidate
    port = _publisher().reattestation_port()

    with pytest.raises(
        BirthCommitLinkError, match="birth_reattestation_v2_context_invalid",
    ):
        port.persist_v2(current, b"wire", _expected(), request)
    assert called is False


def test_legacy_publisher_without_context_cannot_use_the_v2_port(monkeypatch):
    monkeypatch.setattr(
        contract_store,
        "read_current_birth_receipt_v2",
        lambda *_args, **_kwargs: b"unexpected",
    )
    current = _current()
    with pytest.raises(
        BirthCommitLinkError, match="birth_reattestation_v2_context_invalid",
    ):
        _publisher(context=None).reattestation_port().read_v2(
            current, _request(),
        )


def test_v2_port_rejects_a_request_subclass_that_skips_the_seal(monkeypatch):
    class LookAlike(ProducerRequestV2):
        def __post_init__(self) -> None:
            return None

    genuine = _request()
    look_alike = LookAlike(
        genuine.request_id, genuine.objective_hash, genuine.contract_id,
        genuine.generation_id, genuine.admission_context_id,
        genuine.transition_id, genuine.context_epoch, genuine.set_id,
        genuine.candidate_source_id, None,
    )
    monkeypatch.setattr(
        contract_store, "read_current_birth_receipt_v2",
        lambda *_args, **_kwargs: pytest.fail("untrusted request reached storage"),
    )
    with pytest.raises(
        BirthCommitLinkError, match="birth_reattestation_v2_context_invalid",
    ):
        _publisher().reattestation_port().read_v2(
            _current(), look_alike,
        )
