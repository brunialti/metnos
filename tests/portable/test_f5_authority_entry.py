"""The one administrative entry point: closed forms, closed documents."""
from __future__ import annotations

import base64
import io
import json

import pytest

import install.f5_authority as authority
from install.f5_authority import AuthorityInputError


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


DIGEST = "sha256:" + "a" * 64


def census(**overrides):
    document = {"kind": "census", "scope_id": DIGEST, "sources": [b64(b"known")],
                "review": b64(b"review"), "findings": {"f1": b64(b"evidence")}}
    document.update(overrides)
    return document


def feed(monkeypatch, document):
    payload = json.dumps(document).encode("utf-8")
    monkeypatch.setattr(authority.sys, "stdin",
                        type("S", (), {"buffer": io.BytesIO(payload)})())


# --- the closed argument set -------------------------------------------------

def test_only_the_six_exact_forms_exist():
    assert set(authority._COMMANDS) == {
        ("provision-key",), ("evidence",), ("migrate", "plan"),
        ("migrate", "apply"), ("certify", "derive"), ("certify", "issue"),
    }


@pytest.mark.parametrize("argv", [
    [], ["migrate"], ["certify"], ["migrate", "plan", "--force"],
    ["provision-key", "extra"], ["certify", "sign"], ["evidence", "census"],
    ["MIGRATE", "plan"],
])
def test_anything_else_is_refused_before_anything_runs(argv, monkeypatch):
    for name in ("_provision_key", "_evidence", "_migrate", "_certify"):
        monkeypatch.setattr(authority, name,
                            lambda *_a, **_k: pytest.fail("an operation ran"))
    assert authority.main(argv) == 64


def test_a_failing_operation_reports_a_closed_code(monkeypatch, capsys):
    def refuse():
        raise AuthorityInputError("certification_before_migration", "no marker")

    monkeypatch.setitem(authority._COMMANDS, ("certify", "issue"), refuse)
    assert authority.main(["certify", "issue"]) == 1
    reported = json.loads(capsys.readouterr().err)
    assert reported == {"error": "certification_before_migration",
                        "detail": "no marker"}


# --- the evidence document ---------------------------------------------------

class Owner:
    def __init__(self):
        self.calls = []

    def _frontier(self):
        return type("F", (), {
            "head": DIGEST, "event_count": 1, "census_scope": DIGEST,
            "open_findings": (), "profile": None, "consecutive_successes": (),
            "pending_cycle": None})()

    def census(self, **kwargs):
        self.calls.append(("census", kwargs))
        return self._frontier()

    def close_defect(self, finding_id, **kwargs):
        self.calls.append(("close_defect", finding_id, kwargs))
        return self._frontier()

    def profile(self, **kwargs):
        self.calls.append(("profile", kwargs))
        return self._frontier()

    def start_cycle(self):
        self.calls.append(("start_cycle",))
        return self._frontier()

    def finish_cycle(self, **kwargs):
        self.calls.append(("finish_cycle", kwargs))
        return self._frontier()


def test_a_census_reaches_the_owner_as_decoded_bytes(monkeypatch):
    feed(monkeypatch, census())
    owner = Owner()
    report = authority._apply_evidence(owner, authority._read_document())
    name, kwargs = owner.calls[0]
    assert name == "census"
    assert kwargs["sources"] == (b"known",)
    assert kwargs["review"] == b"review"
    assert kwargs["findings"] == {"f1": b"evidence"}
    assert report["census_scope"] == DIGEST


def test_every_evidence_kind_reaches_its_own_owner_method(monkeypatch):
    documents = [
        census(),
        {"kind": "close_defect", "finding_id": "f1", "opening": DIGEST,
         "repair": b64(b"r"), "verification": b64(b"v"), "review": b64(b"w")},
        {"kind": "profile", "base": {"installation_id": DIGEST},
         "manifest": b64(b"m"), "cases": b64(b"c")},
        {"kind": "start_cycle"},
        {"kind": "finish_cycle", "start": DIGEST, "results": b64(b"r"),
         "turns": {"case": {"turn": b64(b"t")}}},
    ]
    owner = Owner()
    for document in documents:
        feed(monkeypatch, document)
        authority._apply_evidence(owner, authority._read_document())
    assert [item[0] for item in owner.calls] == [
        "census", "close_defect", "profile", "start_cycle", "finish_cycle"]


@pytest.mark.parametrize("document,detail", [
    ({"kind": "invented"}, "kind"),
    (census(extra=1), "fields"),
    ({"kind": "census", "scope_id": DIGEST, "sources": []}, "fields"),
    (census(sources=[]), "census"),
    (census(sources="not a list"), "census"),
    (census(findings=[]), "census"),
    (census(review="not base64!!"), "review"),
    (census(sources=[""]), "sources"),
    (census(review=b64(b"")), "review"),
])
def test_a_document_that_is_not_exactly_one_kind_is_refused(monkeypatch, document, detail):
    feed(monkeypatch, document)
    with pytest.raises(AuthorityInputError) as raised:
        authority._apply_evidence(Owner(), authority._read_document())
    assert raised.value.detail == detail


@pytest.mark.parametrize("payload,detail", [
    (b"not json", "json"), (b"[]", "shape"), (b'"text"', "shape"),
])
def test_a_document_that_is_not_a_document_is_refused(monkeypatch, payload, detail):
    monkeypatch.setattr(authority.sys, "stdin",
                        type("S", (), {"buffer": io.BytesIO(payload)})())
    with pytest.raises(AuthorityInputError) as raised:
        authority._read_document()
    assert raised.value.detail == detail


def test_an_oversized_document_is_refused_without_parsing(monkeypatch):
    monkeypatch.setattr(authority, "_MAX_DOCUMENT_BYTES", 8)
    monkeypatch.setattr(authority.sys, "stdin",
                        type("S", (), {"buffer": io.BytesIO(b"x" * 64)})())
    with pytest.raises(AuthorityInputError) as raised:
        authority._read_document()
    assert raised.value.detail == "size"


def test_the_key_operation_returns_public_material_only(monkeypatch):
    monkeypatch.setattr(
        "install.birth_certification_authority_provisioner.provision_certification_authority_v1",
        lambda: type("K", (), {"key_id": "key-1", "status": "active",
                               "public_key": object()})())
    assert authority._provision_key() == {"key_id": "key-1", "status": "active"}
