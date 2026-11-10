"""Review/consent boundary: real snapshots, signatures and approval persistence."""
import base64
from contextlib import nullcontext
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import json
import hashlib
import os
from pathlib import Path
import subprocess
import tomllib
from types import SimpleNamespace as NS

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from install import synth_review as review
from executor_birth_approval import ApprovalSubject
from executor_birth_approval_authority import ApprovalAuthority
from executor_birth_approval_store import verified_approval, consume_verified_approval
from executor_birth_functional import SynthTestReport
from executor_birth_predecessor import predecessor_snapshot
from executor_birth_semantic_authority import PreprovisionedSemanticAuthority
from executor_birth_semantic_review import ReviewPolicyV1, IndependentEvidenceKind


NOW = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
D = "sha256:" + "a" * 64
PUBLISH = review._publish_evidence
pytestmark = pytest.mark.skipif(os.name != "posix", reason="administrative launcher uses POSIX root custody")


@pytest.fixture
def launcher(monkeypatch, tmp_path):
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    for name in ("METNOS_USER_DATA", "METNOS_USER_STATE", "METNOS_USER_CONFIG", "METNOS_WORKSPACE"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    monkeypatch.setenv("PYTHONPATH", "/untrusted/imports")
    monkeypatch.setenv("METNOS_INSTALL_ROOT", "/untrusted/release")
    monkeypatch.setenv("METNOS_LLM_TIERS_CONFIG", str(tmp_path / "services-llm-tiers.toml"))
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return NS(returncode=0, stdout=b'{"status":"awaiting_consent"}', stderr=b'')

    monkeypatch.setattr(review.subprocess, "run", run)
    return calls


def test_administrative_review_delegates_only_installed_code_and_passes_data_on_stdin(document, launcher):
    document["proposal"]["reason"] = "$(touch /untrusted); %i"
    assert review.run_delegated("review", document)["status"] == "awaiting_consent"
    command, kwargs = launcher[0]
    assert command[0] == "/usr/bin/systemd-run"
    assert "Delegate=yes" in command and "DelegateSubgroup=metnos-birth-host" in command
    assert "KillMode=control-group" in command and "RuntimeMaxSec=600" in command
    assert "MemoryMax=1G" in command and "CPUQuota=100%" in command
    assert "/untrusted/imports" not in str(command)
    assert "/untrusted/release" not in str(command)
    assert "METNOS_INSTALL_ROOT=" + str(Path(review.__file__).resolve().parents[1]) in command
    assert "METNOS_LLM_TIERS_CONFIG=" + os.environ["METNOS_LLM_TIERS_CONFIG"] in command
    assert document["proposal"]["reason"] not in str(command)
    assert json.loads(kwargs["input"]) == document
    assert kwargs["timeout"] == 610 and set(kwargs["env"]) == {"PATH", "LANG"}
    assert command[-4:-1] == ["-I", "-B", "-c"]


@pytest.mark.parametrize("fault", ["identity", "operation", "environment", "specifier"])
def test_administrative_review_rejects_invalid_launch_before_execution(document, launcher, monkeypatch, fault):
    operation = "review"
    if fault == "identity":
        monkeypatch.setattr(os, "geteuid", lambda: 1000)
    elif fault == "operation":
        operation = "review; arbitrary()"
    elif fault == "environment":
        monkeypatch.delenv("METNOS_USER_STATE")
    else:
        monkeypatch.setenv("METNOS_LLM_TIERS_CONFIG", "/tmp/%i")
    with pytest.raises(ValueError):
        review.run_delegated(operation, document)
    assert launcher == []


def test_interrupted_review_stops_the_entire_transient_unit(document, launcher, monkeypatch):
    calls = []

    def interrupted(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(command, 610)
        return NS(returncode=0)

    monkeypatch.setattr(review.subprocess, "run", interrupted)
    with pytest.raises(subprocess.TimeoutExpired):
        review.run_delegated("review", document)
    unit = next(arg.removeprefix("--unit=") for arg in calls[0] if arg.startswith("--unit="))
    assert calls[1] == ["/usr/bin/systemctl", "stop", "--", unit]


def test_failed_delegate_cannot_be_reported_as_success(document, launcher, monkeypatch, capsys):
    monkeypatch.setattr(review.subprocess, "run", lambda *_a, **_k:
                        NS(returncode=1, stdout=b'{"status":"admitted"}', stderr=b'x' * 5000))
    with pytest.raises(ValueError, match="synth_review_service_failed"):
        review.run_delegated("review", document)
    assert len(capsys.readouterr().err) == 4096


@pytest.fixture
def document():
    files = {"manifest.toml": b'name="example"\n[code]\nfiles=["example.py"]\n',
             "manifest.lang_state.json": b'{}',
             "example.py": b'def invoke(args): return {"result": args["value"]}\n'}
    return {"proposal": {"files": {name: base64.b64encode(raw).decode()
                                    for name, raw in files.items()},
                         "contract_id": "user:example/manifest.toml",
                         "producer": "synt_multistage", "reason": "Return the given value"},
            "human_cases": [{"name": f"case {n}", "input": {"value": n},
                             "expect": {"result": n}} for n in (-1, 0, 3)]}


@pytest.fixture
def setup(monkeypatch, tmp_path):
    keys = {name: Ed25519PrivateKey.generate() for name in ("review-key", "operator-key")}
    approval = ApprovalAuthority(1, {"operator-key": keys["operator-key"].public_key()}, {
        "operator": {"key_ids": frozenset({"operator-key"}), "scopes": frozenset({"active"})}})
    current = NS(selection=NS(admission_context_id=D, set_id="set"),
                 authorities=NS(approval=approval))
    bundle = NS(core=NS(
        producer_db=tmp_path / "producer.sqlite",
        predecessor_resolver=lambda _request: (
            predecessor_snapshot(None, "absent", None), None,
        ),
    ))
    calls = []

    def request(intent, cap):
        assert cap is review._SYNTH_MULTISTAGE
        manifest = tomllib.loads((intent.candidate_source_root / "manifest.toml").read_text())
        source = (intent.candidate_source_root / "example.py").read_bytes()
        assert manifest["code"]["digest"] == "sha256:" + hashlib.sha256(source).hexdigest()
        # Bind the real closed candidate bytes and the allocated token, just
        # as the runtime factory does (its algorithm has separate tests).
        candidate = review._digest({"source": (intent.candidate_source_root / "example.py").read_text(),
                                    "refs": list(intent.approval_refs)})
        return NS(candidate_id=candidate, request_id=candidate, producer=cap), bundle

    def subject(request, core, *, expires_at):
        return ApprovalSubject(request.candidate_id, D, D, "active", expires_at)

    def functional(data):
        calls.append(data)
        return SynthTestReport(tuple({**case, "passed": True} for case in data.cases()))

    monkeypatch.setattr(review, "_environment", lambda: (None, keys, tmp_path))
    monkeypatch.setattr(review, "_service_identity", lambda _: nullcontext())
    monkeypatch.setattr(review, "_current", lambda: current)
    monkeypatch.setattr(review, "_now", lambda: NOW)
    monkeypatch.setattr(review, "_request_for_intent_with_capability", request)
    monkeypatch.setattr(review, "_preview_approval_subject", subject)
    monkeypatch.setattr(review, "_validate_synth_tests", functional)
    monkeypatch.setattr("executor_birth_bootstrap.bootstrap_birth_runtime", lambda: bundle)
    published = []
    monkeypatch.setattr(review, "_publish_evidence", lambda *args: published.append(args))
    # Privilege/secure-file ownership is separately verified natively; these
    # unit tests deliberately retain the invoking user's identity.
    monkeypatch.setattr("executor_birth_authority_files._read_regular",
                        lambda path, **_: path.read_bytes())
    return NS(keys=keys, current=current, bundle=bundle, calls=calls,
              published=published, root=tmp_path)


@pytest.mark.parametrize("declared", [None, "sha256:placeholder", "sha256:" + "0" * 64])
def test_retained_authoring_proposal_is_prepared_before_review(document, declared):
    proposal = document["proposal"]
    manifest = base64.b64decode(proposal["files"]["manifest.toml"])
    if declared is not None:
        manifest += f'digest="{declared}"\n'.encode()
    proposal["files"]["manifest.toml"] = base64.b64encode(manifest).decode()
    original = json.dumps(proposal, sort_keys=True)
    with review._candidate(proposal) as (snapshot, _contract, _producer):
        prepared = tomllib.loads(snapshot.manifest_bytes.decode())
        expected = tomllib.loads(manifest.decode())
        source = base64.b64decode(proposal["files"]["example.py"])
        expected["code"]["digest"] = "sha256:" + hashlib.sha256(source).hexdigest()
        assert prepared == expected
        assert snapshot.code_files == {"example.py": source}
        assert snapshot.language_state_bytes == base64.b64decode(proposal["files"]["manifest.lang_state.json"])
    assert json.dumps(proposal, sort_keys=True) == original


def test_review_runs_supplied_cases_and_binds_complete_results(document, setup):
    result = review.review(document)
    assert result["status"] == "awaiting_consent"
    assert setup.calls[0].cases() == document["human_cases"]
    record = json.loads((setup.root / (result["review_id"][7:] + ".json")).read_bytes())
    assert record["human_cases"] == document["human_cases"]
    assert review._digest(record) == result["review_id"]
    proof = setup.published[0][0]
    policy = ReviewPolicyV1({kind: frozenset({"v1"}) for kind in IndependentEvidenceKind},
                           {kind: frozenset({"independent-owner"}) for kind in IndependentEvidenceKind})
    verifier = PreprovisionedSemanticAuthority(policy, setup.root,
                                               {"review-key": setup.keys["review-key"].public_key()})
    evidence = verifier._decode_record(proof, "proof.json")
    assert evidence.evidence_hash == result["review_id"]
    assert evidence.candidate_id == result["subject"]["candidate_id"]
    with pytest.raises(ValueError, match="approval_required"):
        verified_approval(ApprovalSubject(**record["subject"]), token=record["token"], now=NOW,
                          db_path=review._approval_db(setup.bundle), authority=setup.current.authorities.approval)


def test_review_does_not_request_consent_for_authenticated_current_generation(
    document, setup,
):
    with review._candidate(document["proposal"]) as (candidate, _contract, _producer):
        payloads = {"manifest.toml": candidate.manifest_bytes,
                    "manifest.lang_state.json": candidate.language_state_bytes}
    previous = predecessor_snapshot(D, "generation", payloads,
                                    approved_lifecycle="active",
                                    admission_receipt=b"existing signed receipt")
    setup.bundle.core.predecessor_resolver = lambda _request: (previous, payloads)

    result = review.review(document)

    assert result == {"status": "already_current", "contract_id": "user:example/manifest.toml",
                      "generation_id": D}
    assert not setup.calls and not setup.published
    assert not list(setup.root.glob("*.json"))
    assert not review._approval_db(setup.bundle).exists()


def test_same_generation_without_current_context_receipt_still_requires_review(
    document, setup,
):
    with review._candidate(document["proposal"]) as (candidate, _contract, _producer):
        payloads = {"manifest.toml": candidate.manifest_bytes,
                    "manifest.lang_state.json": candidate.language_state_bytes}
    previous = predecessor_snapshot(D, "generation", payloads)
    setup.bundle.core.predecessor_resolver = lambda _request: (previous, payloads)

    result = review.review(document)

    assert result["status"] == "awaiting_consent"
    assert len(setup.calls) == 1 and len(setup.published) == 1


@pytest.mark.parametrize("changed_file", ["example.py", "manifest.toml", "manifest.lang_state.json"])
def test_changed_candidate_cannot_reuse_current_admission(document, setup, changed_file):
    with review._candidate(document["proposal"]) as (candidate, _contract, _producer):
        payloads = {"manifest.toml": candidate.manifest_bytes,
                    "manifest.lang_state.json": candidate.language_state_bytes}
    previous = predecessor_snapshot(D, "generation", payloads,
                                    approved_lifecycle="active",
                                    admission_receipt=b"existing signed receipt")
    setup.bundle.core.predecessor_resolver = lambda _request: (previous, payloads)
    replacement = {
        "example.py": b'def invoke(args): return {"result": args["value"] + 1}\n',
        "manifest.toml": b'name="example"\n[description]\nen="Return one value"\n[code]\nfiles=["example.py"]\n',
        "manifest.lang_state.json": b'{"en": "revised"}',
    }
    document["proposal"]["files"][changed_file] = base64.b64encode(replacement[changed_file]).decode()

    result = review.review(document)

    assert result["status"] == "awaiting_consent"
    assert len(setup.calls) == 1 and len(setup.published) == 1


def test_untrusted_predecessor_cannot_skip_review(document, setup):
    setup.bundle.core.predecessor_resolver = lambda _request: (
        NS(revision_id=D, admission_receipt_hash=D), {},
    )

    with pytest.raises(ValueError, match="birth_predecessor_snapshot_invalid"):
        review.review(document)

    assert not setup.calls and not setup.published
    assert not review._approval_db(setup.bundle).exists()


def test_failed_or_unavailable_sandbox_never_signs_proof(document, setup, monkeypatch):
    monkeypatch.setattr(review, "_validate_synth_tests",
                        lambda _: SynthTestReport(error_code="test_environment_unavailable"))
    result = review.review(document)
    assert result == {"status": "tests_failed", "tests": [], "error": "test_environment_unavailable"}
    assert not setup.published
    assert not list(setup.root.glob("*.json"))


def test_context_drift_during_tests_invalidates_review(document, setup, monkeypatch):
    preview = review._preview_approval_subject
    count = 0

    def drift(*args, **kwargs):
        nonlocal count
        count += 1
        subject = preview(*args, **kwargs)
        return subject if count == 1 else replace(subject, admission_context_id="sha256:" + "b" * 64)

    monkeypatch.setattr(review, "_preview_approval_subject", drift)
    with pytest.raises(ValueError, match="synth_review_subject_changed"):
        review.review(document)
    assert not setup.published


@pytest.mark.parametrize("change", ["extra", "path", "code_count", "producer", "origin", "test_claim"])
def test_untrusted_inputs_cannot_supply_authority_or_paths(document, setup, change):
    if change == "extra":
        document["passed"] = True
    elif change == "path":
        document["proposal"]["files"]["../escape.py"] = document["proposal"]["files"].pop("example.py")
    elif change == "code_count":
        document["proposal"]["files"]["other.py"] = "eA=="
    elif change == "producer":
        document["proposal"]["producer"] = "human"
    elif change == "origin":
        document["proposal"]["contract_id"] = "builtin:example/manifest.toml"
    else:
        document["human_cases"][0]["passed"] = True
    with pytest.raises(ValueError):
        review.review(document)
    assert not setup.calls and not setup.published


def test_exact_consent_survives_interruption_and_birth_still_decides(document, setup, monkeypatch):
    result = review.review(document)
    record = json.loads((setup.root / (result["review_id"][7:] + ".json")).read_bytes())
    command = {"review_id": result["review_id"], "subject_hash": result["subject_hash"], "decision": "approved"}
    subject = ApprovalSubject(**record["subject"])
    attempts = []

    def execute(request, _core):
        evidence = consume_verified_approval(subject, token=record["token"], request_id=request.request_id,
                                            now=NOW, db_path=review._approval_db(setup.bundle),
                                            authority=setup.current.authorities.approval)
        attempts.append(evidence)
        if len(attempts) == 1:
            raise RuntimeError("interrupted")
        # The full gate may still refuse: approval is not a semantic verdict.
        return NS(publication=None, error_code="semantic_review_uncertain", report=subject)

    monkeypatch.setattr(review, "_execute", execute)
    with pytest.raises(RuntimeError, match="interrupted"):
        review.approve(command)
    monkeypatch.setattr(review, "_preview_approval_subject",
                        lambda *_a, **_k: pytest.fail("Birth owns recovery after the decision"))
    resumed = review.approve(command)
    assert resumed["status"] == "birth_refused"
    assert resumed["error"] == "semantic_review_uncertain"
    assert attempts[0] == attempts[1]


@pytest.mark.parametrize("change", ["hash", "record", "expiry", "context", "decision"])
def test_consent_cannot_authorize_changed_or_expired_review(document, setup, monkeypatch, change):
    result = review.review(document)
    command = {"review_id": result["review_id"], "subject_hash": result["subject_hash"], "decision": "approved"}
    if change == "hash":
        command["subject_hash"] = "sha256:" + "f" * 64
    elif change == "record":
        path = setup.root / (result["review_id"][7:] + ".json")
        record = json.loads(path.read_bytes())
        record["human_cases"][0]["expect"] = {"result": 999}
        path.write_text(json.dumps(record))
    elif change == "expiry":
        monkeypatch.setattr(review, "_now", lambda: NOW + timedelta(hours=2))
    elif change == "context":
        original = review._preview_approval_subject
        monkeypatch.setattr(review, "_preview_approval_subject",
                            lambda *a, **k: replace(original(*a, **k), admission_context_id="sha256:" + "e" * 64))
    else:
        command["decision"] = "inferred"
    monkeypatch.setattr(review, "_execute", lambda *_: pytest.fail("must not reach Birth"))
    with pytest.raises(ValueError):
        review.approve(command)


@pytest.mark.parametrize("scenario", ["write", "wrong_key", "full", "retry", "context_changed"])
def test_proof_publication_authenticates_before_mutating_selected_authority(document, setup, monkeypatch, scenario):
    # Exercise the real signature decoder; only the filesystem session is a
    # stand-in here. Native secure-store mutation is tested in the installed lab.
    from install.synth_review import _record_evidence
    import executor_birth_semantic_authority as semantic
    import executor_birth_prepared_root as prepared
    from install import birth_authority_provisioner as provisioner
    current = setup.current
    subject = ApprovalSubject(D, D, D, "active", "2026-09-27T13:00:00Z")
    record = {"subject": asdict(subject), "tests": document["human_cases"]}
    key = setup.keys["review-key"]
    policy = ReviewPolicyV1({kind: frozenset({"v1"}) for kind in IndependentEvidenceKind},
                           {kind: frozenset({"independent-owner"}) for kind in IndependentEvidenceKind})
    verifier = PreprovisionedSemanticAuthority(policy, setup.root, {"review-key": key.public_key()})
    proof = _record_evidence(record, key)
    item = verifier._decode_record(proof, "proof.json")
    if scenario == "wrong_key":
        proof = _record_evidence(record, Ed25519PrivateKey.generate())
    existing = (item,) if scenario == "retry" else tuple(
        replace(item, evidence_id=f"proof-{n}") for n in range(semantic._MAX_EVIDENCE_FILES)
    ) if scenario == "full" else ()
    authority = NS(_decode_record=verifier._decode_record, _records_from_capability=lambda: existing)
    writes = []
    session = NS(global_lock=lambda **_: nullcontext(),
                 create_file_exclusive=lambda *a, **k: writes.append((a, k)))
    monkeypatch.setattr(provisioner, "_open_installer_layout_v1",
                        lambda: NS(birth_session=nullcontext(session)))
    monkeypatch.setattr(semantic, "_load_semantic_authority_in_session", lambda *_: authority)
    monkeypatch.setattr(prepared, "load_required_context_runtime_v1",
                        lambda: NS(selection="changed") if scenario == "context_changed" else current)
    if scenario in {"wrong_key", "full", "context_changed"}:
        with pytest.raises((ValueError, semantic.SemanticReviewError)):
            PUBLISH(proof, current, review._digest(record))
        assert not writes
    else:
        PUBLISH(proof, current, review._digest(record))
        assert len(writes) == (0 if scenario == "retry" else 1)


@pytest.mark.parametrize("producer", ["promoter", "stack_reconcile"])
def test_review_accepts_only_synth_producers(document, setup, producer):
    document["proposal"]["producer"] = producer
    with pytest.raises(ValueError, match="producer_invalid"):
        review.review(document)
    assert not setup.calls and not setup.published


@pytest.mark.parametrize("argv", [[], ["proposal", "review"], ["REVIEW"], ["review", "--force"]])
def test_entry_accepts_only_the_closed_forms(argv, monkeypatch):
    monkeypatch.setattr(review, "run_delegated", lambda *_: pytest.fail("an operation ran"))
    assert review.main(argv) == 64


@pytest.mark.parametrize("payload", [b'{"proposal":{},"proposal":{},"human_cases":[]}', b'not json'])
def test_entry_rejects_ambiguous_json_before_launch(monkeypatch, capsys, payload):
    import io
    monkeypatch.setattr(review.sys, "stdin", type("S", (), {"buffer": io.BytesIO(payload)})())
    monkeypatch.setattr(review, "run_delegated",
                        lambda *_: pytest.fail("ambiguous proposal must not launch"))
    assert review.main(["review"]) == 1
    assert json.loads(capsys.readouterr().err) == {
        "error": "synth_review_document_invalid", "detail": ""}


def test_entry_reports_the_delegated_result(monkeypatch, capsys):
    import io
    monkeypatch.setattr(review.sys, "stdin",
                        type("S", (), {"buffer": io.BytesIO(b'{"review_id":"x"}\n')})())
    monkeypatch.setattr(review, "run_delegated",
                        lambda operation, document: {"operation": operation, **document})
    assert review.main(["approve"]) == 0
    assert json.loads(capsys.readouterr().out) == {"operation": "approve", "review_id": "x"}


def test_entry_reads_a_document_written_over_several_lines(monkeypatch, capsys):
    """Review of 10/11/2026 (A8): only the first line was read, so an
    indented document was refused as invalid."""
    import io
    payload = b'{\n  "review_id": "x",\n  "human_cases": []\n}\n'
    monkeypatch.setattr(review.sys, "stdin", type("S", (), {"buffer": io.BytesIO(payload)})())
    monkeypatch.setattr(review, "run_delegated",
                        lambda operation, document: {"operation": operation, **document})
    assert review.main(["review"]) == 0
    assert json.loads(capsys.readouterr().out)["review_id"] == "x"
