"""G4-A: one real installer intent publishes code later loaded by the door."""
from __future__ import annotations

import hashlib
import os
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
    from executor_birth_producer_store import register_producer_receipt
    from executor_birth_receipts import (
        IssuerKey, IssuerRegistry, issue_producer_receipt,
    )
    from executor_birth_shadow import _sealed_dependencies_for_test
    from admitted_module_v1 import (
        AdmittedModuleError, load_admitted_module_v1,
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
    monkeypatch.setattr(operational, "_RUNTIME_BUNDLE", None)
    _install_birth_runtime_bundle(
        _assemble_birth_runtime_bundle(core, {_INSTALLER: factory})
    )

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
    module = load_admitted_module_v1(record)
    assert module.invoke({}) == {"results": []}

    code_path.write_bytes(b"raise RuntimeError('must not execute')\n")
    with pytest.raises(AdmittedModuleError, match="admitted_module_digest_mismatch"):
        load_admitted_module_v1(record)


def test_parent_projection_crosses_the_real_executor_subprocess(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The verified record, read-only mount and child door work together."""
    import agent_runtime
    import sandbox
    from admitted_module_v1 import code_digest_of_bytes_v1
    from loader import Catalog, Executor

    dependency_root = tmp_path / "dependency"
    consumer_root = tmp_path / "consumer"
    dependency_root.mkdir()
    consumer_root.mkdir()
    dependency_payload = (
        b"def invoke(args):\n"
        b"    return {'ok': True, 'dependency': 'authenticated'}\n"
    )
    consumer_payload = (
        b"import os, sys\n"
        b"sys.path.insert(0, os.environ['METNOS_RUNTIME'])\n"
        b"from admitted_module_v1 import (load_admitted_module_v1, "
        b"runtime_admitted_executor_v1)\n"
        b"from executor_helpers import run_stdio\n"
        b"def invoke(args):\n"
        b"    record = runtime_admitted_executor_v1('read_dependency')\n"
        b"    return load_admitted_module_v1(record).invoke(args)\n"
        b"if __name__ == '__main__':\n"
        b"    run_stdio(invoke)\n"
    )
    dependency_code = dependency_root / "read_dependency.py"
    consumer_code = consumer_root / "read_consumer.py"
    dependency_code.write_bytes(dependency_payload)
    consumer_code.write_bytes(consumer_payload)

    def executor(name: str, root: Path, code: Path, payload: bytes, **values):
        return Executor(
            name=name, version="1", description=name, affinity=[],
            args_schema={"type": "object"}, capabilities=[], tests=[],
            code_path=code, manifest_path=root / "manifest.toml",
            signed_by="author", code_files=(code.name,),
            digest=code_digest_of_bytes_v1([payload]), **values,
        )

    dependency = executor(
        "read_dependency", dependency_root, dependency_code,
        dependency_payload,
    )
    consumer = executor(
        "read_consumer", consumer_root, consumer_code, consumer_payload,
        code_dependencies=("read_dependency",),
    )
    monkeypatch.setattr(
        agent_runtime, "load_catalog",
        lambda: Catalog(executors={dependency.name: dependency}),
    )

    result = agent_runtime.invoke_executor(
        consumer, {}, timeout_s=15, actor="host", channel="test",
    )

    if (
        sys.platform.startswith("linux")
        and not os.environ.get("CI")
        and not result.get("ok")
        and "bwrap:" in str(result.get("error"))
        and "Operation not permitted" in str(result.get("error"))
    ):
        pytest.skip("local kernel denied bubblewrap namespace creation")
    if sys.platform.startswith("linux"):
        assert sandbox.bwrap_available(), "Linux certification requires bubblewrap"
    assert result == {"ok": True, "dependency": "authenticated"}
