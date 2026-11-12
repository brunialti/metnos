"""Administrative review of retained Synth proposals, using the Birth owners.

Inputs contain candidate bytes and human cases, never paths, keys or a claimed
test outcome. Review does not admit a candidate. A separate, exact-subject
consent resumes the original producer through all ordinary Birth checks.

``activate`` joins the two for the one decision the person takes in the
administration page (ADR 0240): it adds no check and removes none.
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
from executor_birth_predecessor import (
    AuthenticatedPredecessorSnapshot, derive_revision_facts,
)
from executor_birth_semantic_authority import EVIDENCE_DOMAIN, _canonical
from executor_birth_snapshot import (
    acquire_candidate_snapshot, materialize_birth_candidate_from_authoring,
)
from manifest_inventory import ContractId, ManifestOrigin


_PRODUCERS = {cap.producer_id: cap for cap in (
    _SYNTH_MULTISTAGE, _SYNTH_SPECIALIZE, _SYNTH_APPROVE,
)}
_PROPOSAL_FIELDS = frozenset({"files", "contract_id", "reason", "producer"})
_REVIEW_FIELDS = frozenset({"proposal", "human_cases"})
_APPROVE_FIELDS = frozenset({"review_id", "subject_hash", "decision"})
_ACTIVATE_FIELDS = frozenset({"proposal", "human_cases", "consent"})
_CONSENT_FIELDS = frozenset({"decision", "by", "at"})
# The only process that may bring a person's answer: the administration service.
_PAGE_SERVICE_UNIT = "metnos-http.service"
_MAX_DOCUMENT = 4 * 1024 * 1024
_UNIT_PREFIX = "metnos-birth-review-"
_TIMEOUT = 600


class _AlreadyCurrent(Exception):
    def __init__(self, generation_id: str):
        self.generation_id = generation_id


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
        stage = Path(name) / "proposal"
        stage.mkdir(mode=0o700)
        for filename, payload in files.items():
            if (type(filename) is not str or type(payload) is not str
                    or (filename not in {"manifest.toml", "manifest.lang_state.json"}
                    and re.fullmatch(r"[a-z][a-z0-9_-]{0,119}\.py", filename) is None)):
                raise ValueError("synth_review_filename_invalid")
            raw = base64.b64decode(payload, validate=True)
            if not raw or len(raw) > 1024 * 1024:
                raise ValueError("synth_review_file_size_invalid")
            (stage / filename).write_bytes(raw)
        # Use the same preparation as the producer, before binding tests or
        # consent. A retained authoring proposal may still contain a placeholder
        # digest; only the captured source bytes can determine the real value.
        prepared = materialize_birth_candidate_from_authoring(stage, Path(name) / "candidate")
        with acquire_candidate_snapshot(prepared) as snapshot:
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
            predecessor, payloads = bundle.core.predecessor_resolver(request)
            if not isinstance(predecessor, AuthenticatedPredecessorSnapshot):
                raise ValueError("birth_predecessor_snapshot_invalid")
            facts = derive_revision_facts(predecessor, payloads, snapshot)
            # A new approval token changes candidate identity. Preserve the
            # authenticated current-context receipt for identical bytes.
            if facts.exact_republish and predecessor.admission_receipt_hash is not None:
                raise _AlreadyCurrent(predecessor.revision_id)
            prepared.update(subject=subject, bundle=bundle, current=current)
            return subject

        # Bootstrap first to obtain the owner's approval database; no candidate
        # is submitted or producer receipt claimed by this operation.
        from executor_birth_bootstrap import bootstrap_birth_runtime
        bundle = bootstrap_birth_runtime()
        try:
            token = prepare_pending_approval(observe, requested_actor="operator", created_at=now,
                                             db_path=_approval_db(bundle))
        except _AlreadyCurrent as current:
            return {"status": "already_current", "contract_id": contract.value,
                    "generation_id": current.generation_id}, None
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
    paths = ["METNOS_USER_DATA", "METNOS_USER_STATE", "METNOS_USER_CONFIG",
             "METNOS_WORKSPACE"]
    # The launcher selects this binding from the authenticated service catalog.
    # Omitting it would silently choose the default provider in the new unit.
    if "METNOS_LLM_TIERS_CONFIG" in os.environ:
        paths.append("METNOS_LLM_TIERS_CONFIG")
    for name in paths:
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


def _page_consent(consent: object) -> dict:
    """The person's answer as the administration page recorded it."""
    if (type(consent) is not dict or set(consent) != _CONSENT_FIELDS
            or consent["decision"] != "approved"
            or type(consent["by"]) is not str
            or re.fullmatch(r"[A-Za-z0-9_.@:+-]{1,120}", consent["by"]) is None
            or type(consent["at"]) is not str
            or re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", consent["at"]) is None):
        raise ValueError("synth_review_explicit_consent_required")
    return consent


def _require_page_peer(descriptor: int = 0) -> None:
    """A document that arrives on the decision socket comes from the service.

    The socket is reachable by the service account, and every executor runs
    under that same account. Three facts tell the administration service from
    anything it started: the account, the initial user namespace (an executor
    in its sandbox has its own) and the service's own control group. A direct
    call has no socket: only root can make it, and nothing is checked here.
    """
    import socket
    import stat
    import struct

    if not stat.S_ISSOCK(os.fstat(descriptor).st_mode):
        return
    from executor_birth_account_identity import resolve_posix_account_snapshot_v1
    from executor_birth_host_path_policy import SERVICE_ACCOUNT_NAME_V1

    with socket.socket(fileno=os.dup(descriptor)) as connection:
        pid, uid, _gid = struct.unpack("3i", connection.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
    account = resolve_posix_account_snapshot_v1(SERVICE_ACCOUNT_NAME_V1)
    try:
        initial_namespace = (os.readlink(f"/proc/{pid}/ns/user")
                             == os.readlink("/proc/self/ns/user"))
        groups = Path(f"/proc/{pid}/cgroup").read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError("synth_review_peer_invalid") from exc
    in_service = any(_PAGE_SERVICE_UNIT in line.split(":", 2)[-1].split("/")
                     for line in groups)
    if uid != account.record.uid or not initial_namespace or not in_service:
        raise ValueError("synth_review_peer_invalid")


def activate(document: dict) -> dict:
    """The review, then the consent the person gave in the page, in one call.

    Nothing is added to what ``review`` and ``approve`` check: the same cases
    run in the same runner, the same keys sign, and the candidate still passes
    every Birth check. What changes is who brings the consent: the service,
    reporting the answer of a signed-in administrator, instead of the
    developer's terminal. A review that does not pass ends here, unsigned.
    """
    _closed(document, _ACTIVATE_FIELDS)
    consent = _page_consent(document["consent"])
    _require_page_peer()
    reviewed = run_delegated("review", {"proposal": document["proposal"],
                                        "human_cases": document["human_cases"]})
    if reviewed.get("status") != "awaiting_consent":
        return reviewed
    # Who answered, and when, beside the signed record of what was reviewed.
    print(json.dumps({"event": "page_consent", "review_id": reviewed["review_id"],
                      "by": consent["by"], "at": consent["at"]}), file=sys.stderr)
    decided = run_delegated("approve", {"review_id": reviewed["review_id"],
                                        "subject_hash": reviewed["subject_hash"],
                                        "decision": "approved"})
    return {**decided, "name": reviewed["name"], "tests": reviewed["tests"]}


_OPERATIONS = ("review", "approve", "activate")
_CLOSED_CODE = re.compile(r"[a-z][a-z0-9_]{0,79}\Z")


def main(argv: list[str] | None = None) -> int:
    """Closed administrative entry: ``review``, ``approve`` or ``activate``, document on stdin.

    The root-owned launcher imports this from the verified release. There is
    deliberately no ``__main__`` guard: the launcher is the only way in, and
    the document arrives on standard input, never in the argument vector.
    """
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1 or arguments[0] not in _OPERATIONS:
        print("usage: synth_review review|approve|activate < document.json", file=sys.stderr)
        return 64
    from_page = arguments[0] == "activate"
    try:
        from executor_birth_semantic_authority import _pairs

        # The whole document, not its first line: a document written over
        # several lines is the same document.
        raw = sys.stdin.buffer.read(_MAX_DOCUMENT + 1)
        if len(raw) > _MAX_DOCUMENT:
            raise ValueError("synth_review_size_invalid")
        try:
            document = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
        except (UnicodeError, ValueError) as exc:
            raise ValueError("synth_review_document_invalid") from exc
        report = activate(document) if from_page else run_delegated(arguments[0], document)
    except Exception as exc:
        # Closed codes only: an exception text may carry paths or bytes.
        code = getattr(exc, "code", None)
        if code is None:
            text = str(exc)
            code = text if _CLOSED_CODE.match(text) else type(exc).__name__
        refusal = json.dumps({"error": str(code), "detail": str(getattr(exc, "detail", "") or "")})
        print(refusal, file=sys.stderr)
        if from_page:
            # The page has one channel back: it must learn why, not only that.
            print(refusal)
        return 1
    print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True, default=str))
    return 0
