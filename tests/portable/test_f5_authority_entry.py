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

def test_only_the_closed_forms_exist():
    assert set(authority._COMMANDS) == {
        ("provision-key",), ("evidence",), ("migrate", "plan"),
        ("migrate", "apply"), ("certify", "derive"), ("certify", "issue"),
        ("rehearse", "plan"), ("rehearse", "issue"),
        ("proposal", "review"), ("proposal", "approve"),
    }


@pytest.mark.parametrize("argv", [
    [], ["migrate"], ["certify"], ["migrate", "plan", "--force"],
    ["provision-key", "extra"], ["certify", "sign"], ["evidence", "census"],
    ["MIGRATE", "plan"], ["rehearse", "issue", "--host"], ["rehearse", "renew"],
])
def test_anything_else_is_refused_before_anything_runs(argv, monkeypatch):
    for name in ("_provision_key", "_evidence", "_migrate", "_certify", "_rehearse"):
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


@pytest.mark.parametrize("payload", [b'{"proposal":{},"proposal":{},"human_cases":[]}', b'not json'])
def test_proposal_input_rejects_ambiguous_json_before_launch(monkeypatch, payload):
    monkeypatch.setattr(authority.sys, "stdin", type("S", (), {"buffer": io.BytesIO(payload)})())
    monkeypatch.setattr("install.synth_review.run_delegated",
                        lambda *_: pytest.fail("ambiguous proposal must not launch"))
    with pytest.raises(AuthorityInputError, match="synth_review_document_invalid"):
        authority._proposal("review")


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


@pytest.mark.skipif(__import__('sys').platform != 'linux', reason='native evidence custody')
@pytest.mark.parametrize('ending', ['finish', 'disconnect', 'wrong_document'])
def test_cycle_session_preserves_its_owner_and_records_interruption(tmp_path, ending):
    """A real pipe client can finish two cycles; a lost client cannot pass."""
    import hashlib
    from pathlib import Path
    import selectors
    import subprocess
    import sys
    import install.birth_certification_evidence as evidence
    from executor_birth_canonical import encode_canonical_ascii_v1 as canonical

    tmp_path.chmod(0o755)
    cases = [{'case_id': 'observable', 'postcondition_probes': ['effect']}]
    matrix = canonical(cases[0]) + b'\n'
    base = dict.fromkeys(('installation_id', 'head_id', 'source_id', 'catalog_id', 'harness_id'), DIGEST)
    with evidence._evidence_at_v1(tmp_path, root_owned=False) as owner:
        owner.census(scope_id=DIGEST, sources=(b'fixture source',), review=b'fixture review', findings={})
        owner.profile(base=base, manifest=canonical({'case_matrix_sha256': hashlib.sha256(matrix).hexdigest()}),
                      cases=canonical(cases))
    root = Path(__file__).resolve().parents[2]
    code = '\n'.join([
        'import sys', 'from pathlib import Path',
        f'sys.path[:0] = {[str(root), str(root / "runtime")]!r}',
        'import install.birth_certification_evidence as evidence',
        'evidence.administrative_evidence_v1 = lambda: evidence._evidence_at_v1(Path(sys.argv[1]), root_owned=False)',
        'from install.f5_authority import main', 'raise SystemExit(main(["evidence"]))',
    ])
    for number in range(2 if ending == 'finish' else 1):
        with subprocess.Popen([sys.executable, '-I', '-B', '-c', code, str(tmp_path)],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE) as process:
            try:
                process.stdin.write(b'{"kind":"start_cycle"}\n')
                process.stdin.flush()
                with selectors.DefaultSelector() as ready:
                    ready.register(process.stdout, selectors.EVENT_READ)
                    assert ready.select(10), 'start acknowledgment must not wait for EOF'
                started = json.loads(process.stdout.readline())
                assert started['pending_cycle']
                if ending == 'finish':
                    result = [{'case_id': 'observable', 'verdict': 'pass', 'failure_reasons': [],
                               'probe_results': [{'name': 'effect', 'passed': True, 'detail': 'fixture'}]}]
                    document = {'kind': 'finish_cycle', 'start': started['pending_cycle'],
                                'results': b64(canonical(result)),
                                'turns': {'observable': {f'turn-{number}': b64(f'turn body {number}'.encode())}}}
                    output, error = process.communicate(json.dumps(document).encode() + b'\n', timeout=10)
                    assert process.returncode == 0, error
                    assert len(json.loads(output)['consecutive_successes']) == number + 1
                else:
                    payload = b'{"kind":"start_cycle"}\n' if ending == 'wrong_document' else b''
                    process.communicate(payload, timeout=10)
                    assert process.returncode == 1
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
    with evidence._evidence_at_v1(tmp_path, root_owned=False) as owner:
        assert owner.frontier.pending_cycle is None
        assert len(owner.frontier.consecutive_successes) == (2 if ending == 'finish' else 0)
