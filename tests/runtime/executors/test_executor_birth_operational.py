from __future__ import annotations

import inspect
import shutil
import threading
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from contract_store import PublicationResult
from executor_birth import observe_candidate
from executor_birth_identity import (
    AdmissionContextV1, ContextComponent, ExecutorOrigin, RevisionAuthor,
)
from executor_birth_operational import (
    BirthRequest, _assemble_birth_runtime_bundle, _birth_executor_for_test,
    _install_birth_runtime_bundle, _runtime_bundle_snapshot, _sealed_core_for_test,
    birth_executor, candidate_source_id,
)
import executor_birth_operational as operational
import executor_birth_intent as intent_api
from executor_birth_producer_store import register_producer_receipt
from executor_birth_receipts import (
    IssuerKey, IssuerRegistry, issue_producer_receipt,
)
from executor_birth_shadow import (
    BirthOutcome, RevisionFacts, _assemble_production_dependencies,
)
from manifest_inventory import ContractId, ManifestOrigin, ManifestRef, ManifestStatus


NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
D = "sha256:" + "1" * 64
REQUEST_ID = "sha256:" + "2" * 64


def _context() -> AdmissionContextV1:
    component = ContextComponent("v1", D)
    return AdmissionContextV1(**{
        name: component for name in AdmissionContextV1.__dataclass_fields__
    })


def _candidate(tmp_path: Path) -> Path:
    source = Path("dist/metnos-public/executors/consult_frontier")
    destination = tmp_path / "candidate"
    destination.mkdir()
    manifest = tomllib.loads((source / "manifest.toml").read_text())
    for name in ("manifest.toml", "manifest.lang_state.json", *manifest["code"]["files"]):
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
    return destination


def _fixture(tmp_path: Path, publisher):
    candidate = _candidate(tmp_path)
    contract_id = ContractId(ManifestOrigin.USER, "demo/manifest.toml")
    context = _context()
    producer_private = Ed25519PrivateKey.generate()
    registry = IssuerRegistry({"human": (IssuerKey(
        "producer-1", producer_private.public_key(),
        frozenset({ExecutorOrigin.HUMAN}), frozenset({RevisionAuthor.HUMAN}),
    ),)})
    observed = observe_candidate(
        candidate, contract_id=contract_id, executor_origin=ExecutorOrigin.HUMAN,
        revision_authorship=RevisionAuthor.HUMAN, objective_hash=D,
        admission_context=context,
    )
    try:
        source_id = candidate_source_id(observed)
    finally:
        observed.close()
    encoded = issue_producer_receipt(
        issuer_id="human", executor_origin=ExecutorOrigin.HUMAN,
        revision_authorship=RevisionAuthor.HUMAN, objective_hash=D,
        candidate_source_id=source_id, issued_at="2026-08-25T12:00:00Z",
        expires_at="2026-08-25T13:00:00Z",
        nonce="0123456789abcdef0123456789abcdef", key_id="producer-1",
        private_key=producer_private,
    )
    db = tmp_path / "producer.sqlite"
    register_producer_receipt(encoded, registry=registry, now=NOW, db_path=db)
    admission_private = Ed25519PrivateKey.generate()
    shadow = _assemble_production_dependencies()
    core = _sealed_core_for_test(
        producer_registry=registry, producer_db=db,
        context_resolver=lambda _request: context,
        facts_resolver=lambda _request: RevisionFacts(first_birth=True),
        shadow_dependencies=shadow, admission_private_key=admission_private,
        admission_public_key=admission_private.public_key(),
        admission_key_id="birth-1", policy_version="birth-policy-v1",
        now=lambda: NOW, publisher=publisher, publisher_options={},
    )
    ref = ManifestRef(
        contract_id, ManifestOrigin.USER, ManifestStatus.ADMITTED,
        candidate, candidate / "manifest.toml", "demo/manifest.toml", (candidate,),
    )
    request = BirthRequest(
        REQUEST_ID, ref, None, encoded, "operator", "first birth", (),
        "create", candidate,
    )
    return request, core


def test_public_request_cannot_supply_trust_or_publication_authorities():
    assert {"checks", "issuer", "verifier", "publisher", "registry"}.isdisjoint(
        inspect.signature(BirthRequest).parameters
    )
    assert "_core" not in inspect.signature(birth_executor).parameters


def test_admitted_pipeline_consumes_receipt_issues_admission_and_publishes_once(tmp_path):
    calls = []

    def publisher(ref, *, expected_generation_id, snapshot, request_id,
                  birth_authorization, **_options):
        calls.append(snapshot)
        generation = "sha256:" + "3" * 64
        journal = "sha256:" + "4" * 64
        encoded = birth_authorization.issuer(generation, {}, request_id, journal)
        receipt = birth_authorization.verifier(encoded)
        assert receipt.candidate_id == birth_authorization.candidate_id
        assert receipt.birth_request_id == request_id
        assert receipt.check_results["authoring_install_journal_v1"].evidence_hash == journal
        return PublicationResult(ref.contract_id, expected_generation_id, generation,
                                 "commit_birth_snapshot", False)

    request, core = _fixture(tmp_path, publisher)
    result = _birth_executor_for_test(request, _core=core)
    assert result.error_code is None
    assert result.report.outcome is BirthOutcome.ADMITTED
    assert result.publication is not None
    assert len(calls) == 1
    assert not calls[0].private_root.exists()

    replay = _birth_executor_for_test(request, _core=core)
    assert replay.publication is None
    assert replay.report.outcome is BirthOutcome.REJECTED
    assert len(calls) == 1


def test_source_binding_rejection_reports_and_never_calls_publisher(tmp_path):
    calls = []
    request, core = _fixture(tmp_path, lambda *args, **kwargs: calls.append(1))
    (request.candidate_source_root / "consult_frontier.py").write_bytes(b"pass\n")
    result = _birth_executor_for_test(request, _core=core)
    assert result.report.outcome is BirthOutcome.REJECTED
    assert result.publication is None
    assert result.error_code == "producer_receipt_binding_invalid"
    assert calls == []


def test_publisher_failure_is_a_rejection_not_a_false_admission(tmp_path):
    def unavailable(*_args, **_kwargs):
        raise OSError("publisher unavailable")

    request, core = _fixture(tmp_path, unavailable)
    result = _birth_executor_for_test(request, _core=core)
    assert result.report.outcome is BirthOutcome.REJECTED
    assert result.report.error_code == "birth_unavailable"
    assert result.publication is None


def test_runtime_bundle_install_is_atomic_and_install_once(monkeypatch, tmp_path):
    request, core = _fixture(tmp_path, lambda *_args, **_kwargs: None)
    capability = intent_api._producer_capabilities_for_bootstrap()[0]
    bundle = _assemble_birth_runtime_bundle(core, {capability: lambda _intent: request})
    monkeypatch.setattr(operational, "_RUNTIME_BUNDLE", None)
    barrier = threading.Barrier(3)
    outcomes = []

    def install() -> None:
        barrier.wait()
        try:
            _install_birth_runtime_bundle(bundle)
            outcomes.append("installed")
        except ValueError as exc:
            outcomes.append(str(exc))

    threads = [threading.Thread(target=install) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()
    assert outcomes.count("installed") == 1
    assert outcomes.count("birth_runtime_bundle_already_installed") == 1
    snapshot = _runtime_bundle_snapshot()
    assert snapshot is bundle
    assert snapshot.core is core
    assert snapshot.producer_factories[capability](
        intent_api.BirthIntent(tmp_path, request.manifest_ref.contract_id, "test")
    ) is request


def test_racing_readers_never_observe_a_partial_runtime(monkeypatch, tmp_path):
    request, core = _fixture(tmp_path, lambda *_args, **_kwargs: None)
    capability = intent_api._producer_capabilities_for_bootstrap()[0]
    bundle = _assemble_birth_runtime_bundle(core, {capability: lambda _intent: request})
    monkeypatch.setattr(operational, "_RUNTIME_BUNDLE", None)
    barrier = threading.Barrier(9)
    observations = []

    def read() -> None:
        barrier.wait()
        snapshot = _runtime_bundle_snapshot()
        observations.append(None if snapshot is None else (
            snapshot.core, snapshot.producer_factories.get(capability),
        ))

    readers = [threading.Thread(target=read) for _ in range(8)]
    for reader in readers:
        reader.start()
    barrier.wait()
    _install_birth_runtime_bundle(bundle)
    for reader in readers:
        reader.join()
    assert all(item is None or (item[0] is core and callable(item[1]))
               for item in observations)


def test_forged_actor_cannot_be_expressed_or_select_another_capability(tmp_path):
    with pytest.raises(TypeError):
        intent_api.BirthIntent(
            tmp_path, ContractId(ManifestOrigin.USER, "x/manifest.toml"), "reason",
            actor="promoter",  # type: ignore[call-arg]
        )
    capability = intent_api._producer_capabilities_for_bootstrap()[0]
    forged = object()
    assert capability is not forged
    assert not intent_api._is_producer_capability(forged)
