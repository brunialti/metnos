"""G4-A: one real installer intent publishes code later loaded by the door."""
from __future__ import annotations

import hashlib
import shutil
import sys
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


def test_installer_intent_publishes_the_bytes_the_door_later_executes(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import inspect

    import executor_birth_bootstrap as bootstrap
    import executor_birth_operational as operational
    from contract_store import publish_signed_source
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
    store = work / "store"
    initial = publish_signed_source(
        ref, expected_generation_id=None,
        trusted_publics=trusted, store_root=store,
    )
    candidate = _candidate(ref, work / "candidate")

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

    result = submit_installer_birth(BirthIntent(
        candidate, ref.contract_id, "G4-A authenticated dependency proof",
    ))

    assert result.error_code is None
    assert result.publication.previous_generation_id == initial.current_generation_id
    manifest = tomllib.loads(ref.manifest_path.read_text(encoding="utf-8"))
    code_name = manifest["code"]["files"][0]
    code_path = ref.manifest_dir / code_name
    record = SimpleNamespace(
        name=manifest["name"], manifest_path=ref.manifest_path,
        code_path=code_path, code_files=tuple(manifest["code"]["files"]),
        digest=manifest["code"]["digest"],
    )
    monkeypatch.setenv(
        ADMITTED_EXECUTORS_ENV_V1,
        encode_admitted_executor_records_v1([record]),
    )
    monkeypatch.setattr(
        "admitted_module_v1._trusted_public_keys_v1",
        lambda: tuple(public for _name, public in trusted),
    )
    projected = runtime_admitted_executor_v1(record.name)
    module = load_admitted_module_v1(projected)
    assert module.invoke({}) == {"results": []}

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
