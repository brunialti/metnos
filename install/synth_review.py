"""Administrative review of retained Synth proposals, using the Birth owners.

Inputs contain candidate bytes and human cases, never paths, keys or a claimed
test outcome. Review does not admit a candidate. A separate, exact-subject
consent resumes the original producer through all ordinary Birth checks.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import tomllib
from types import SimpleNamespace

from executor_birth_approval import (
    ApprovalDecision, ApprovalSubject, BirthApprovalError, approval_subject_hash,
)
from executor_birth_approval_authority import decision_payload
from executor_birth_approval_store import (
    prepare_pending_approval, resolve_pending_approval, verified_approval,
)
from executor_birth_functional import SynthTestData
from executor_birth_intent import (
    BirthIntent, _SYNTH_APPROVE, _SYNTH_MULTISTAGE, _SYNTH_SPECIALIZE,
)
from executor_birth_operational import (
    _execute, _preview_approval_subject, _request_for_intent_with_capability,
    _validate_synth_tests,
)
from executor_birth_semantic_authority import EVIDENCE_DOMAIN, _canonical
from executor_birth_snapshot import acquire_candidate_snapshot
from manifest_inventory import ContractId, ManifestOrigin


_PRODUCERS = {cap.producer_id: cap for cap in (
    _SYNTH_MULTISTAGE, _SYNTH_SPECIALIZE, _SYNTH_APPROVE,
)}
_PROPOSAL_FIELDS = frozenset({"files", "contract_id", "reason", "producer"})
_REVIEW_FIELDS = frozenset({"proposal", "human_cases"})
_APPROVE_FIELDS = frozenset({"review_id", "subject_hash", "decision"})
_MAX_DOCUMENT = 4 * 1024 * 1024
_UNIT_PREFIX = "metnos-birth-review-"
_TIMEOUT = 600


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(instant: datetime) -> str:
    return instant.strftime("%Y-%m-%dT%H:%M:%SZ")


def _closed(document: object, fields: frozenset[str]) -> dict:
    if type(document) is not dict or set(document) != fields:
        raise ValueError("synth_review_fields_invalid")
    if len(_canonical(document)) > _MAX_DOCUMENT:
        raise ValueError("synth_review_size_invalid")
    return document


@contextmanager
def _candidate(proposal: dict):
    """Materialise data only in a new private tree, then use Birth's snapshot."""
    _closed(proposal, _PROPOSAL_FIELDS)
    if (type(proposal["producer"]) is not str
            or type(proposal["contract_id"]) is not str
            or type(proposal["reason"]) is not str or not proposal["reason"].strip()):
        raise ValueError("synth_review_proposal_invalid")
    producer = _PRODUCERS.get(proposal["producer"])
    if producer is None:
        raise ValueError("synth_review_producer_invalid")
    origin, relative = proposal["contract_id"].split(":", 1)
    contract = ContractId(ManifestOrigin(origin), relative)
    if contract.origin is not ManifestOrigin.USER:
        raise ValueError("synth_review_origin_invalid")
    files = proposal["files"]
    if type(files) is not dict or len(files) != 3:
        # The existing declarative runner supports one Python source. Do not
        # silently test only a subset of a multi-file candidate.
        raise ValueError("synth_review_single_source_required")
    with tempfile.TemporaryDirectory(prefix="metnos-review-") as name:
        stage = Path(name)
        for filename, payload in files.items():
            if (type(filename) is not str or type(payload) is not str
                    or (filename not in {"manifest.toml", "manifest.lang_state.json"}
                    and re.fullmatch(r"[a-z][a-z0-9_-]{0,119}\.py", filename) is None)):
                raise ValueError("synth_review_filename_invalid")
            raw = base64.b64decode(payload, validate=True)
            if not raw or len(raw) > 1024 * 1024:
                raise ValueError("synth_review_file_size_invalid")
            (stage / filename).write_bytes(raw)
        with acquire_candidate_snapshot(stage) as snapshot:
            if len(snapshot.code_files) != 1:
                raise ValueError("synth_review_single_source_required")
            yield snapshot, contract, producer


def _environment():
    """Resolve the service identity and protected operator keys locally."""
    from executor_birth_account_identity import resolve_posix_account_snapshot_v1
    from executor_birth_host_path_policy import SERVICE_ACCOUNT_NAME_V1
    from install import operator_authority as operator
    from executor_birth_authority_files import _root_owned_chain

    if os.name != "posix" or os.geteuid() != 0:
        raise ValueError("synth_review_administrator_required")
    account = resolve_posix_account_snapshot_v1(SERVICE_ACCOUNT_NAME_V1)
    identity = SimpleNamespace(service_uid=account.record.uid,
                               service_gid=account.record.gid,
                               service_supplementary_gids=account.supplementary_gids)
    from install.birth_runner_host import delegate_service_checks
    delegate_service_checks(identity, unit_prefix=_UNIT_PREFIX)
    _root_owned_chain(operator.PRIVATE_BASE)
    operator._require_directory(operator.PRIVATE_BASE, owner=(0, 0),
                                modes=frozenset({0o700}))
    private = operator.PRIVATE_BASE / str(identity.service_uid)
    operator._require_directory(private, owner=(0, 0), modes=frozenset({0o700}))
    keys = {name: operator._read_private(private / (name + ".priv"), owner=(0, 0))
            for name in ("operator-key", "review-key")}
    reviews = operator.PRIVATE_BASE / "reviews"
    operator._ensure_directory(reviews, owner=(0, 0), mode=0o700)
    reviews = reviews / str(identity.service_uid)
    operator._ensure_directory(reviews, owner=(0, 0), mode=0o700)
    return identity, keys, reviews


def _service_identity(identity):
    from install.birth_authority_provisioner import _service_owned_birth_identity_v2
    return _service_owned_birth_identity_v2(identity)


def _current():
    from executor_birth_prepared_root import load_required_context_runtime_v1
    return load_required_context_runtime_v1()


def _approval_db(bundle) -> Path:
    from executor_birth_bootstrap import APPROVALS_BASENAME_V1
    return bundle.core.producer_db.parent / APPROVALS_BASENAME_V1


def _review_as_service(document: dict, now: datetime) -> tuple[dict, object]:
    from executor_birth_policy_v1 import birth_receipt_ttl_seconds_v1

    with _candidate(document["proposal"]) as (snapshot, contract, producer):
        name, source = next(iter(snapshot.code_files.items()))
        tests = SynthTestData.from_cases(name, source, document["human_cases"])
        expires = _iso(now + timedelta(seconds=birth_receipt_ttl_seconds_v1()))
        prepared = {}

        def observe(token):
            intent = BirthIntent(snapshot.private_root, contract,
                                 document["proposal"]["reason"], (token,))
            request, bundle = _request_for_intent_with_capability(intent, producer)
            subject = _preview_approval_subject(request, bundle.core, expires_at=expires)
            current = _current()
            actor = current.authorities.approval.actors.get("operator", {})
            if (subject.admission_context_id != current.selection.admission_context_id
                    or subject.lifecycle not in actor.get("scopes", ())
                    or "operator-key" not in actor.get("key_ids", ())):
                raise ValueError("synth_review_authority_upgrade_required")
            prepared.update(subject=subject, bundle=bundle, current=current)
            return subject

        # Bootstrap first to obtain the owner's approval database; no candidate
        # is submitted or producer receipt claimed by this operation.
        from executor_birth_bootstrap import bootstrap_birth_runtime
        bundle = bootstrap_birth_runtime()
        token = prepare_pending_approval(observe, requested_actor="operator", created_at=now,
                                         db_path=_approval_db(bundle))
        report = _validate_synth_tests(tests)
        if not report.all_passed:
            return {"status": "tests_failed", "tests": list(report.tests),
                    "error": report.error_code}, None
        # Observe again after execution: context/source/predecessor drift must
        # not turn results from one subject into evidence for another.
        intent = BirthIntent(snapshot.private_root, contract,
                             document["proposal"]["reason"], (token,))
        request, bundle = _request_for_intent_with_capability(intent, producer)
        subject = _preview_approval_subject(request, bundle.core, expires_at=expires)
        if subject != prepared["subject"]:
            raise ValueError("synth_review_subject_changed")
        manifest = tomllib.loads(snapshot.manifest_bytes.decode("utf-8"))
        record = {"schema_version": 1, "proposal": document["proposal"],
                  "human_cases": tests.cases(), "tests": list(report.tests),
                  "subject": asdict(subject), "token": token, "created_at": _iso(now),
                  "name": manifest["name"], "description": manifest.get("description", {}),
                  "capabilities": manifest.get("capabilities", {}),
                  "execution": manifest.get("execution", {})}
        return record, prepared["current"]


def _record_evidence(record: dict, key) -> bytes:
    review_id = _digest(record)
    evidence = {"evidence_id": review_id, "evidence_version": "v1",
                "kind": "human_case", "owner_id": "independent-owner",
                "candidate_id": record["subject"]["candidate_id"],
                "admission_context_id": record["subject"]["admission_context_id"],
                "status": "passed", "evidence_hash": review_id}
    encoded = _canonical({"schema_version": 1, "key_id": "review-key", "evidence": evidence,
                          "signature": base64.b64encode(
                              key.sign(EVIDENCE_DOMAIN + _canonical(evidence))).decode("ascii")})
    return encoded


def _publish_evidence(encoded: bytes, current, review_id: str) -> None:
    from install.birth_authority_provisioner import publish_independent_evidence_v1
    publish_independent_evidence_v1(encoded, current.selection, review_id)


def review(document: dict) -> dict:
    _closed(document, _REVIEW_FIELDS)
    identity, keys, reviews = _environment()
    with _service_identity(identity):
        record, current = _review_as_service(document, _now())
    if current is None:
        return record
    review_id = _digest(record)
    evidence = _record_evidence(record, keys["review-key"])
    path = reviews / (review_id.removeprefix("sha256:") + ".json")
    # Persist the complete inputs/results before making their signed digest
    # visible. A crash cannot leave a valid proof without its audit material.
    from install.birth_ownership_authority_provisioner import _sync_directory
    with path.open("xb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(_canonical(record))
        stream.flush()
        os.fsync(stream.fileno())
    _sync_directory(reviews)
    with _service_identity(identity):
        _publish_evidence(evidence, current, review_id)
    return {"status": "awaiting_consent", "review_id": review_id,
            "subject_hash": approval_subject_hash(ApprovalSubject(**record["subject"])),
            **{name: record[name] for name in (
                "subject", "name", "description", "capabilities", "execution", "human_cases", "tests")}}


def approve(document: dict) -> dict:
    _closed(document, _APPROVE_FIELDS)
    if document["decision"] != "approved":
        raise ValueError("synth_review_explicit_consent_required")
    review_id = document["review_id"]
    if not isinstance(review_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", review_id):
        raise ValueError("synth_review_id_invalid")
    identity, keys, reviews = _environment()
    from executor_birth_authority_files import _read_regular
    record = json.loads(_read_regular(
        reviews / (review_id[7:] + ".json"), maximum=_MAX_DOCUMENT,
        mode=0o600, root_owned=True,
    ))
    subject = ApprovalSubject(**record["subject"])
    if _digest(record) != review_id or approval_subject_hash(subject) != document["subject_hash"]:
        raise ValueError("synth_review_subject_changed")
    now = _now()
    if _iso(now) >= subject.expires_at:
        raise ValueError("approval_expired")
    signature = keys["operator-key"].sign(decision_payload(
        token=record["token"], subject_hash=document["subject_hash"], actor="operator",
        decision=ApprovalDecision.APPROVED, decided_at=_iso(now), key_id="operator-key",
    )).hex()
    with _service_identity(identity):
        with _candidate(record["proposal"]) as (snapshot, contract, producer):
            intent = BirthIntent(snapshot.private_root, contract, record["proposal"]["reason"],
                                 (record["token"],))
            request, bundle = _request_for_intent_with_capability(intent, producer)
            current = _current()
            if current.selection.admission_context_id != subject.admission_context_id:
                raise ValueError("synth_review_subject_changed")
            # Re-entry after an interruption may reuse an authenticated decision
            # for the same subject. A denial, forgery or different subject is
            # never overwritten; Birth still owns one-use consumption.
            try:
                verified_approval(subject, token=record["token"], now=now,
                                  authority=current.authorities.approval, db_path=_approval_db(bundle))
            except BirthApprovalError as exc:
                if exc.code != "approval_required" or exc.detail != "pending":
                    raise
                if _preview_approval_subject(request, bundle.core, expires_at=subject.expires_at) != subject:
                    raise ValueError("synth_review_subject_changed")
                resolve_pending_approval(
                    record["token"], ApprovalDecision.APPROVED, actor="operator", key_id="operator-key",
                    signature=signature, authority=current.authorities.approval,
                    decided_at=now, db_path=_approval_db(bundle),
                )
            # After a signed decision, Birth also owns terminal replay. A
            # successful earlier commit has already changed the predecessor;
            # previewing it again would incorrectly prevent crash recovery.
            result = _execute(request, bundle.core)
    return {"status": "admitted" if result.publication is not None else "birth_refused",
            "review_id": review_id, "error": result.error_code,
            "report": asdict(result.report),
            "publication": asdict(result.publication) if result.publication is not None else None}


def run_delegated(operation: str, document: dict) -> dict:
    """Run this installed operation with the runner's ordinary cgroup boundary."""
    if os.name != "posix" or os.geteuid() != 0:
        raise ValueError("synth_review_administrator_required")
    fields = {"review": _REVIEW_FIELDS, "approve": _APPROVE_FIELDS}.get(operation)
    if fields is None:
        raise ValueError("synth_review_operation_invalid")
    _closed(document, fields)
    root = Path(__file__).resolve().parents[1]
    # The administrative launcher already authenticated this code/interpreter.
    # No executable, import path, environment or command comes from the input.
    stage = ("import sys,json; sys.path[:0]=%r; "
             "from install.synth_review import %s as operation; "
             "print(json.dumps(operation(json.load(sys.stdin)),default=str))") % (
                 [str(root), str(root / "runtime")], operation)
    environment = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8",
                   "METNOS_INSTALL_ROOT": str(root)}
    for name in ("METNOS_USER_DATA", "METNOS_USER_STATE", "METNOS_USER_CONFIG",
                 "METNOS_WORKSPACE"):
        value = os.environ.get(name)
        if not value or not Path(value).is_absolute():
            raise ValueError("synth_review_service_environment_missing")
        environment[name] = value
    unit = _UNIT_PREFIX + secrets.token_hex(8) + ".service"
    properties = ("Type=exec", "User=0", "Group=0", "Delegate=yes",
                  "DelegateSubgroup=metnos-birth-host", "UMask=0077",
                  "KillMode=control-group", "TimeoutStopSec=5",
                  f"RuntimeMaxSec={_TIMEOUT}", "NoNewPrivileges=yes",
                  "MemoryMax=1G", "CPUQuota=100%", "WorkingDirectory=/")
    command = ["/usr/bin/systemd-run", "--quiet", "--wait", "--pipe", "--collect",
               "--expand-environment=no", "--unit=" + unit]
    for value in properties:
        command += ["-p", value]
    command += ["--", "/usr/bin/env", "-i",
                *(key + "=" + value for key, value in environment.items()),
                sys.executable, "-I", "-B", "-c", stage]
    if any("%" in value for value in (*environment.values(), sys.executable, stage)):
        raise ValueError("synth_review_launch_value_invalid")
    controller = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"}
    try:
        result = subprocess.run(command, input=_canonical(document), capture_output=True,
                                env=controller, timeout=_TIMEOUT + 10, check=False)
    except BaseException:
        subprocess.run(["/usr/bin/systemctl", "stop", "--", unit],
                       stdin=subprocess.DEVNULL, capture_output=True, env=controller,
                       timeout=10, check=False)
        raise
    if result.returncode:
        sys.stderr.write(result.stderr[-4096:].decode("utf-8", errors="replace"))
        raise ValueError("synth_review_service_failed")
    return json.loads(result.stdout)
