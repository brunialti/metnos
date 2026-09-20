"""G4-A: one real installer intent publishes code later loaded by the door."""
from __future__ import annotations

import hashlib
import shutil
import sys
import threading
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from . import support


DIGEST = "sha256:" + "1" * 64


class _PassingRunner:
    def run(self, case, *, fixture_id, isolation):
        from executor_birth_property_runner import PropertyRunResult

        count = case.input_value.get("fixture_count", 0)
        limit = case.input_value.get("limit", count)
        size = min(count, limit)
        return PropertyRunResult(
            {"ok": True, "entries": [{} for _ in range(size)]}, {}, DIGEST,
        )


def _candidate(ref, destination: Path) -> Path:
    from manifest_code_digest import prepare_manifest_digest_v1

    destination.mkdir()
    manifest = ref.manifest_path.read_bytes()
    parsed = tomllib.loads(manifest.decode("utf-8"))
    files = parsed["code"]["files"]
    payloads = {
        name: (ref.manifest_dir / name).read_bytes() + b"\n# G4-A revision\n"
        for name in files
    }
    (destination / "manifest.toml").write_bytes(
        prepare_manifest_digest_v1(manifest, payloads)
    )
    shutil.copyfile(
        ref.manifest_dir / "manifest.lang_state.json",
        destination / "manifest.lang_state.json",
    )
    for name, payload in payloads.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    return destination


@pytest.mark.parametrize("candidate_succeeds", (True, False))
def test_installer_intent_publishes_the_bytes_the_door_later_executes(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, candidate_succeeds):
    import inspect

    import executor_birth_bootstrap as bootstrap
    import executor_birth_operational as operational
    import executor_birth_shadow as shadow
    from contract_store import (
        ContractStoreError, catalog_admission_lock, current_manifest,
        current_revision_id, publish_signed_source,
    )
    from executor_birth import observe_candidate
    from executor_birth_commit_publisher import _build_prepared_bundle_v1
    from executor_birth_identity import (
        AdmissionContextV1, ContextComponent, ExecutorOrigin, RevisionAuthor,
        admission_context_id,
    )
    from executor_birth_intent import (
        BirthIntent, _INSTALLER, submit_installer_birth,
    )
    from executor_birth_operational import (
        BirthRequest, _assemble_birth_core, _assemble_birth_runtime_bundle,
        _install_birth_runtime_bundle, candidate_source_id,
    )
    from executor_birth_predecessor import AdmissionContextPin
    from executor_birth_cutover import CurrentGeneration
    from executor_birth_reattestation import reattest_current_generation
    from executor_birth_producer_store import register_producer_receipt
    from executor_birth_receipts import (
        IssuerKey, IssuerRegistry, issue_producer_receipt,
    )
    from executor_birth_shadow import _sealed_dependencies_for_test
    from admitted_module_v1 import (
        ADMITTED_EXECUTORS_ENV_V1, AdmittedModuleError,
        encode_admitted_executor_records_v1, load_admitted_module_v1,
        runtime_admitted_executor_v1,
    )

    work = tmp_path / "work"
    work.mkdir()
    ref, author_private, trusted = support.create_contract_source(work)
    unrelated, _unrelated_key, unrelated_trusted = support.create_contract_source(
        work, name="read_contacts", directory_name="unrelated",
    )
    trusted += (("unrelated", unrelated_trusted[0][1]),)
    store = work / "store"
    initial = publish_signed_source(
        ref, expected_generation_id=None,
        trusted_publics=trusted, store_root=store,
    )
    publish_signed_source(
        unrelated, expected_generation_id=None,
        trusted_publics=trusted, store_root=store,
    )
    # Unrelated code is broken, but its authenticated name remains reserved.
    (unrelated.manifest_dir / "sample.py").write_bytes(b"# tampered\n")
    candidate = _candidate(ref, work / "candidate")

    checking = threading.Event()
    continue_check = threading.Event()

    real_properties = shadow.run_applicable_properties

    def waiting_properties(*args, **kwargs):
        # Delay the real check boundary, without inventing applicable cases
        # for this simple fixture or substituting a successful check result.
        checking.set()
        if not continue_check.wait(10):
            raise RuntimeError("test_reader_did_not_complete")
        if not candidate_succeeds:
            raise RuntimeError("test_candidate_check_failure")
        return real_properties(*args, **kwargs)

    context = AdmissionContextV1(**{
        name: ContextComponent("v1", DIGEST)
        for name in AdmissionContextV1.__dataclass_fields__
    })
    context_id = admission_context_id(context)
    admission_private = Ed25519PrivateKey.generate()
    admission_keys = {"admission-g4a": admission_private.public_key()}
    prepared = _build_prepared_bundle_v1(
        author=SimpleNamespace(
            active_key_id="author", active_private_key=author_private,
            verifier_keys=dict(trusted),
        ),
        admission=SimpleNamespace(
            active_key_id="admission-g4a",
            active_private_key=admission_private,
            verifier_keys=admission_keys,
        ),
        set_id="a" * 64,
        prepared_admission_context_id=context_id,
        prepared_context_epoch=DIGEST,
        store_root=store,
    )

    producer_private = Ed25519PrivateKey.generate()
    registry = IssuerRegistry({"installer_phase3": (IssuerKey(
        "installer-g4a", producer_private.public_key(),
        frozenset({ExecutorOrigin.HUMAN}),
        frozenset({RevisionAuthor.MAINTENANCE}),
    ),)})
    producer_db = work / "producer.sqlite"
    now = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)

    def factory(intent: BirthIntent) -> BirthRequest:
        observed = observe_candidate(
            intent.candidate_source_root, contract_id=ref.contract_id,
            executor_origin=ExecutorOrigin.HUMAN,
            revision_authorship=RevisionAuthor.MAINTENANCE,
            objective_hash=DIGEST, admission_context=context,
        )
        try:
            source_id = candidate_source_id(observed)
        finally:
            observed.close()
        request_id = "sha256:" + hashlib.sha256(
            intent.contract_id.value.encode("utf-8")
        ).hexdigest()
        receipt = issue_producer_receipt(
            issuer_id="installer_phase3", executor_origin=ExecutorOrigin.HUMAN,
            revision_authorship=RevisionAuthor.MAINTENANCE,
            objective_hash=DIGEST, candidate_source_id=source_id,
            issued_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            expires_at=(now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            nonce=hashlib.sha256(request_id.encode("ascii")).hexdigest()[:32],
            key_id="installer-g4a", private_key=producer_private,
        )
        register_producer_receipt(
            receipt, registry=registry, now=now, db_path=producer_db,
        )
        return BirthRequest(
            request_id, ref, receipt, "installer_phase3", intent.reason, (),
            "install", intent.candidate_source_root,
        )

    def postcondition(request, expected, receipt):
        from executor_birth_postcondition import verify_birth_postcondition

        return verify_birth_postcondition(
            request, expected, receipt, trusted_publics=trusted,
            admission_verifier_keys=admission_keys, store_root=store,
        )

    core = _assemble_birth_core(
        producer_registry=registry, producer_db=producer_db,
        context_resolver=lambda _request: (
            context, AdmissionContextPin(context_id, DIGEST),
        ),
        context_epoch_resolver=lambda: DIGEST,
        approval_resolver=lambda *_args: (None, None),
        shadow_dependencies=_sealed_dependencies_for_test(
            property_runner=_PassingRunner(),
        ),
        admission_private_key=admission_private,
        admission_verifier_keys=admission_keys,
        admission_key_id="admission-g4a", policy_version="birth-policy-v1",
        now=lambda: now, commit_publisher=prepared.publisher,
        postcondition_verifier=postcondition,
    )
    reattestation_factory = bootstrap._CutoverReattestationFactoryV1(
        bootstrap._REATTESTATION_FACTORY_TOKEN,
        port=prepared.publisher.reattestation_port(),
        authority=bootstrap._ProducerAuthority(
            _INSTALLER, "installer_phase3", "installer-g4a",
            producer_private, RevisionAuthor.MAINTENANCE,
        ),
        registry=registry, db_path=producer_db,
        ttl_seconds=3600, now=lambda: now,
    )
    monkeypatch.setattr(operational, "_RUNTIME_BUNDLE", None)
    _install_birth_runtime_bundle(
        _assemble_birth_runtime_bundle(
            core, {_INSTALLER: factory}, reattestation_factory,
            author_verifier_keys=dict(trusted),
        )
    )

    assert tuple(inspect.signature(reattest_current_generation).parameters) == (
        "current",
    )
    legacy_current = CurrentGeneration(ref, initial.current_generation_id)
    reattested = reattest_current_generation(legacy_current)
    assert reattested.generation_id == initial.current_generation_id
    assert reattested.repeated is False
    assert reattest_current_generation(legacy_current).repeated is True

    monkeypatch.setattr(shadow, "run_applicable_properties", waiting_properties)
    monkeypatch.setattr(
        "admitted_module_v1._trusted_public_keys_v1",
        lambda: tuple(public for _name, public in trusted),
    )
    monkeypatch.setattr(
        "admitted_module_v1._projected_trusted_public_keys_v1",
        lambda: tuple(public for _name, public in trusted),
    )

    def invoke_current():
        # Same shared lock and verified store reader used by catalog loading.
        with catalog_admission_lock(store_root=store, exclusive=False, timeout=1):
            live = current_manifest(ref, trusted_publics=trusted, store_root=store)
            manifest = live.parsed
            code_path = ref.manifest_dir / manifest["code"]["files"][0]
            record = SimpleNamespace(
                name=manifest["name"], manifest_path=ref.manifest_path,
                code_path=code_path, code_files=tuple(manifest["code"]["files"]),
                digest=manifest["code"]["digest"],
            )
            monkeypatch.setenv(
                ADMITTED_EXECUTORS_ENV_V1,
                encode_admitted_executor_records_v1([record]),
            )
            projected = runtime_admitted_executor_v1(record.name)
            assert load_admitted_module_v1(projected).invoke({}) == {"results": []}
            return live.generation_id, projected, code_path

    results = []
    worker = threading.Thread(target=lambda: results.append(submit_installer_birth(
        BirthIntent(candidate, ref.contract_id, "G4-A authenticated dependency proof"),
    )), daemon=True)
    worker.start()
    try:
        assert checking.wait(5), f"candidate did not reach the check boundary: {results!r}"
        assert invoke_current()[0] == initial.current_generation_id
        assert worker.is_alive(), "the read must complete while Birth is waiting"
    finally:
        continue_check.set()
        worker.join(timeout=10)
    assert not worker.is_alive()
    assert len(results) == 1
    result = results[0]
    if candidate_succeeds:
        assert result.error_code is None
        assert result.publication.previous_generation_id == initial.current_generation_id
        assert current_revision_id(ref, store_root=store) == result.publication.current_generation_id
    else:
        assert result.error_code is not None
        assert result.publication is None
        assert current_revision_id(ref, store_root=store) == initial.current_generation_id
    _identifier, projected, code_path = invoke_current()
    with pytest.raises(ContractStoreError, match="code_digest_mismatch"):
        current_manifest(unrelated, trusted_publics=trusted, store_root=store)

    code_path.write_bytes(b"raise RuntimeError('must not execute')\n")
    with pytest.raises(AdmittedModuleError, match="admitted_module_digest_mismatch"):
        load_admitted_module_v1(projected)


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="requires the Linux Bubblewrap executor sandbox",
)
def test_parent_projection_crosses_the_real_executor_subprocess(
        tmp_path: Path):
    """The verified record, read-only mount and child door work together."""
    support.exercise_authenticated_dependency_subprocess(tmp_path)
