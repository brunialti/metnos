"""Authenticated reuse of complete F5 observations, never an activation grant.

The administrative owner exports the entire evidence chronology. A destination
trusts an explicitly registered installation/key pair, recomputes both cycles,
and compares the closed source set with its own authenticated distribution.
Import records provenance; the issuer still derives all local obligations.
"""
from __future__ import annotations

import base64
from contextlib import closing
from dataclasses import asdict
import os
from pathlib import Path
import sqlite3

from cryptography.exceptions import InvalidSignature

from executor_birth_authority_files import (
    DEFAULT_OWNERSHIP_ROOT_V1, _directory_metadata, _read_regular, _root_owned_chain,
)
from executor_birth_canonical import encode_canonical_ascii_v1 as canonical
from executor_birth_certification_authority import (
    CertificationPublicKeyV1, decode_certification_registry_v1,
)
from install.birth_certification_evidence import (
    _DDL, _DOMAIN, _Evidence, _digest, _hash, _json,
    MAX_ARTIFACT_BYTES, MAX_EVENTS, MAX_RECORD_BYTES,
)
from install.birth_ownership_authority_provisioner import _provisioning_lock, _sync_directory


EXPORT_DOMAIN_V1 = b"metnos.executor-birth.f5-evidence-export/v1\0"
POLICY_V1 = "f5-complete-focused-reuse-v1"
TRUST_DIRECTORY_V1 = "certification-origins-v1"
_FIELDS = {"schema_version", "purpose", "policy_id", "key_id", "origin",
           "ledger_head", "profile", "cycles", "events", "artifacts", "signature"}
_CONTEXT_FIELDS = {"installation_id", "head_id", "closed_build_id", "migration_id",
                   "source_closure", "conditions"}
_MAX_EXPORT_BYTES = 48 * 1024 * 1024


class ReuseError(ValueError):
    def __init__(self, detail: str):
        self.code, self.detail = "certification_reuse_refused", detail
        super().__init__(f"{self.code}: {detail}")


def _require(condition, detail):
    if not condition:
        raise ReuseError(detail)


def source_closure_v1(distribution) -> str:
    """Close over ALL distributed executable/prompt/contract/collector sources.

    Generated deployment files carry installation-specific paths and identities;
    their native obligations are checked separately by the existing preflight.
    Public documentation is not executable. No caller supplies a path filter.
    """
    from executor_birth_distribution_manifest import VerifiedDistribution

    _require(type(distribution) is VerifiedDistribution, "unauthenticated distribution")
    entries = sorted((item.path, item.size, item.content_hash, item.role)
                     for item in distribution.files
                     if not item.path.startswith(("deployment/", "docs/")))
    paths = {item[0] for item in entries}
    _require({"install/certification/focused.py", "install/certification/oracle.py",
              "install/certification/postconditions.py", "install/certification/native_probe.py",
              "install/certification/observations.py", "install/certification/schemas.json",
              "install/certification/http_capture.py",
              "install/birth_certification_reuse.py", "runtime/llm_workloads.py",
              "requirements.lock"} <= paths, "source closure incomplete")
    return _hash(canonical(entries), domain=b"metnos.f5-source-closure/v1\0")


def _context(value):
    _require(type(value) is dict and set(value) == _CONTEXT_FIELDS, "context schema")
    for key in _CONTEXT_FIELDS - {"conditions"}:
        _digest(value[key])
    conditions = value["conditions"]
    _require(type(conditions) is dict
             and set(conditions) == {"platform", "architecture", "native_preflight", "migration",
                                    "service_policy"}
             and conditions["platform"] in {"linux", "windows"}
             and type(conditions["architecture"]) is str and bool(conditions["architecture"])
             and conditions["native_preflight"] == "verified"
             and conditions["migration"] == "completed", "environment conditions")
    _digest(conditions["service_policy"])
    return value


def _service_conditions_v1() -> dict:
    """Read effective policy in the fresh, unprivileged service interpreter.

    Credentials and slot allocation are not evidence and never leave the child.
    Model/inference policy remain bound, including optional frontier. Host-local
    endpoint aliases may differ; credentials and addresses are not exported.
    This is a configuration observation, not an assertion of model availability.
    """
    import config
    from llm_router import FAST_LEVEL_ORDER, TierConfigError, resolved_tier_spec

    requests = [("fast", level) for level in FAST_LEVEL_ORDER]
    requests.extend((tier, None) for tier in ("middle", "wise", "creative", "frontier"))
    specs = {}
    for tier, level in requests:
        try:
            spec = resolved_tier_spec(tier, level=level)
        except TierConfigError:
            if tier != "frontier":
                raise
            spec = None
        specs[f"{tier}/{level or ''}"] = None if spec is None else {
            key: value for key, value in spec.items()
            if key not in {"api_key", "id_slot", "base_url"} and not key.startswith("_")}
    policy = dict(models=specs, language=config.INSTANCE_LANG,
                  engine=os.environ.get("METNOS_ENGINE", "v3"),
                  seed=os.environ.get("METNOS_LLM_SEED", "42"))
    return {"service_policy": _hash(canonical(policy), domain=b"metnos.f5-service-policy/v1\0")}


def observe_destination_v1() -> dict:
    """Observe the current native installation, never a supplied environment."""
    from executor_birth_ownership_chain import inspect_required_ownership_v1, VerifiedOwnershipWindowV1
    from install.birth_certification_issuer import _installation_frontier_v1, _completed_migration_id
    from install.birth_lifecycle_migration import _in_service_child

    window = inspect_required_ownership_v1()
    _require(type(window) is VerifiedOwnershipWindowV1, "native preflight")
    installation, head, build = _installation_frontier_v1()
    distribution = window.required_distribution
    _require(head == window.required_head.head_id
             and build == distribution.identity.closed_build_id, "installation changed")
    service = _in_service_child("conditions")
    _require(type(service) is dict and set(service) == {"service_policy"}, "service conditions")
    return _context(dict(installation_id=installation, head_id=head, closed_build_id=build,
        migration_id=_completed_migration_id(), source_closure=source_closure_v1(distribution),
        conditions=dict(platform=distribution.platform, architecture=distribution.architecture,
                        native_preflight="verified", migration="completed", **service)))


def _replay(events: list[bytes], artifacts: dict[str, bytes]):
    _require(0 < len(events) <= MAX_EVENTS, "event inventory")
    _require(0 < sum(map(len, artifacts.values())) <= MAX_ARTIFACT_BYTES, "artifact inventory budget")
    with closing(sqlite3.connect(":memory:")) as connection:
        for statement in _DDL:
            connection.execute(statement)
        owner = _Evidence(connection)
        for digest, raw in artifacts.items():
            _require(owner._put(raw) == digest, "artifact identity")
        for sequence, raw in enumerate(events, 1):
            connection.execute("INSERT INTO events VALUES (?, ?, ?)", (sequence, raw, _hash(raw, domain=_DOMAIN)))
        connection.commit()
        yield_owner = _Evidence(connection)
        return _audit(yield_owner)


def _audit(owner: _Evidence) -> dict:
    """Replay already authenticates failed history; recompute the last two cycles."""
    from install.certification.focused import _matrix, audit_completed

    frontier = owner.frontier
    _require(frontier.profile is not None and frontier.pending_cycle is None
             and not frontier.open_findings and len(frontier.consecutive_successes) == 2,
             "two complete consecutive cycles required")
    profile = owner.event(frontier.profile)["payload"]
    manifest, cases = (_json(owner._artifact(profile[key])) for key in ("manifest", "cases"))
    _matrix(manifest, cases)
    subjects = manifest.get("cycle_subjects", {})
    _require(set(subjects) == {"1", "2"} and subjects["1"] != subjects["2"]
             and manifest.get("availability_subject") not in {*subjects.values(), None},
             "two independent frozen subjects required")
    _require(manifest["platform"] in {"linux", "windows"}, "native profile platform")
    turns_seen = set()
    cycles_seen = []
    previous_end, jobs_seen = 0, set()
    all_events, _ = owner.snapshot()
    for raw in all_events:
        event = _json(raw)
        if event["kind"] != "cycle_finished" or event["payload"]["start"] not in frontier.consecutive_successes:
            continue
        payload = event["payload"]
        cycle = len(cycles_seen) + 1
        results = _json(owner._artifact(payload["results"]))
        references = _json(owner._artifact(payload["turns"]))
        recomputed = []
        for case in cases:
            entries = references[case["case_id"]]
            _require(entries and len(set(entries.values())) == 1, "case artifact split")
            completed = _json(owner._artifact(next(iter(entries.values()))))
            inputs = completed["inputs"]
            _require(inputs["started_at"] >= previous_end, "case interval ordering")
            previous_end = inputs["finished_at"]
            result = audit_completed(manifest, case, cycle, completed, turns_seen)
            if case["case_id"] == "f5-restart-resume":
                before = inputs["native_evidence"]["restarted_attempt"][0]
                job = (before["workload"]["owner_user_id"], before["workload"]["id"])
                _require(job not in jobs_seen, "restart workload reused")
                jobs_seen.add(job)
            _require(set(entries) == {item["native"]["turn_id"]
                     for item in completed["inputs"]["captures"]}, "turn artifact coverage")
            recomputed.append(result)
        _require(results == recomputed, "cycle result changed")
        cycles_seen.append(payload["start"])
    _require(tuple(cycles_seen) == frontier.consecutive_successes, "cycle ordering")
    return dict(ledger_head=frontier.head, profile=frontier.profile,
                cycles=cycles_seen, platform=manifest["platform"], bindings=asdict(frontier.profile_bindings))


def build_export_v1(owner: _Evidence, origin: dict, key_id: str) -> tuple[dict, dict[str, bytes]]:
    """Administrative preparation: no signer or native identity supplied by CLI."""
    _context(origin)
    audit = _audit(owner)
    _require(audit.pop("platform") == origin["conditions"]["platform"], "profile platform mismatch")
    bindings = audit.pop("bindings")
    _require((bindings["installation_id"], bindings["head_id"], bindings["source_id"])
             == (origin["installation_id"], origin["head_id"], origin["source_closure"]),
             "profile origin or source mismatch")
    events, artifacts = owner.snapshot()
    for raw in events:
        artifacts[_hash(raw)] = raw
    document = dict(schema_version=1, purpose="f5_evidence_export_v1", policy_id=POLICY_V1,
        key_id=key_id, origin=origin, **audit, events=[_hash(raw) for raw in events],
        artifacts={key: len(raw) for key, raw in sorted(artifacts.items())})
    _require(len(canonical(document)) + 128 <= MAX_RECORD_BYTES, "export manifest size")
    return document, artifacts


def encode_bundle_v1(signed: bytes, artifacts: dict[str, bytes]) -> dict:
    bundle = {"signed": _json(signed), "artifacts": {
        key: base64.b64encode(raw).decode("ascii") for key, raw in artifacts.items()}}
    _require(len(canonical(bundle)) <= _MAX_EXPORT_BYTES, "export transport budget")
    return bundle


def decode_bundle_v1(bundle: dict) -> tuple[bytes, dict[str, bytes]]:
    _require(type(bundle) is dict and set(bundle) == {"signed", "artifacts"}, "bundle schema")
    _require(len(canonical(bundle)) <= _MAX_EXPORT_BYTES, "export transport budget")
    _require(type(bundle["artifacts"]) is dict, "bundle artifacts")
    try:
        artifacts = {key: base64.b64decode(value, validate=True)
                     for key, value in bundle["artifacts"].items()}
    except (ValueError, TypeError) as error:
        raise ReuseError("artifact encoding") from error
    return canonical(bundle["signed"]), artifacts


def verify_export_v1(signed: bytes, artifacts: dict[str, bytes], trusted, destination: dict) -> dict:
    """Verify scope/signature/inventory/replay/oracle before comparing sources."""
    document = _json(signed)
    _require(type(document) is dict and set(document) == _FIELDS
             and type(document["schema_version"]) is int and document["schema_version"] == 1
             and document["purpose"] == "f5_evidence_export_v1"
             and document["policy_id"] == POLICY_V1, "export schema or purpose")
    origin = _context(document["origin"])
    _context(destination)
    _require(type(trusted) is CertificationPublicKeyV1 and trusted.status == "active"
             and trusted.key_id == document["key_id"], "unknown or revoked origin key")
    try:
        signature = base64.b64decode(document["signature"], validate=True)
        _require(len(signature) == 64 and base64.b64encode(signature).decode("ascii")
                 == document["signature"], "export signature encoding")
        trusted.public_key.verify(signature, EXPORT_DOMAIN_V1 + canonical({
            key: value for key, value in document.items() if key != "signature"}))
    except (InvalidSignature, ValueError, TypeError) as error:
        raise ReuseError("export signature") from error
    inventory = document["artifacts"]
    _require(type(inventory) is dict and set(inventory) == set(artifacts), "artifact inventory mismatch")
    for digest, size in inventory.items():
        raw = artifacts[digest]
        _require(type(size) is int and 0 < size <= MAX_RECORD_BYTES and type(raw) is bytes
                 and len(raw) == size and _hash(raw) == digest, "artifact missing or changed")
    events = document["events"]
    _require(type(events) is list and events and all(type(item) is str for item in events)
             and len(events) == len(set(events))
             and all(item in artifacts for item in events), "event references")
    audit = _replay([artifacts[item] for item in events], artifacts)
    _require(audit.pop("platform") == origin["conditions"]["platform"], "profile platform mismatch")
    bindings = audit.pop("bindings")
    _require(all(audit[key] == document[key] for key in audit), "export chronology changed")
    _require((bindings["installation_id"], bindings["head_id"], bindings["source_id"])
             == (origin["installation_id"], origin["head_id"], origin["source_closure"]),
             "profile origin or source mismatch")
    _require(origin["source_closure"] == destination["source_closure"], "source closure changed")
    _require(origin["conditions"] == destination["conditions"], "environment obligations changed")
    return document


def _trust_file(root: Path, installation: str) -> Path:
    _digest(installation)
    return root / TRUST_DIRECTORY_V1 / (installation.removeprefix("sha256:") + ".json")


def _read_trust(root: Path, installation: str, *, root_owned: bool = True):
    path = _trust_file(root, installation)
    _directory_metadata(path.parent, root_owned=root_owned)
    raw = _read_regular(path, maximum=8192, mode=0o644, root_owned=root_owned)
    value = _json(raw)
    _require(type(value) is dict and set(value) == {"installation_id", "public_registry"}
             and value["installation_id"] == installation, "origin trust binding")
    return decode_certification_registry_v1(canonical(value["public_registry"])), raw


def _write_trust(root: Path, installation: str, registry: bytes, *, root_owned: bool = True) -> dict:
    # Decode before writing; trust is an explicit administrative operation and
    # cannot be established from a bundle while importing it.
    public = decode_certification_registry_v1(registry)
    _directory_metadata(root, root_owned=root_owned)
    with _provisioning_lock(root, root_owned=root_owned):
        directory = root / TRUST_DIRECTORY_V1
        if not directory.exists():
            directory.mkdir(mode=0o755)
            directory.chmod(0o755)
            _sync_directory(root)
        _directory_metadata(directory, root_owned=root_owned)
        path = _trust_file(root, installation)
        _require(len(tuple(directory.iterdir())) < 128 or path.exists(), "origin trust limit")
        if path.exists() or path.is_symlink():
            previous, _ = _read_trust(root, installation, root_owned=root_owned)
            _require(previous.key_id == public.key_id, "origin key replacement requires new identity")
            _require(previous.status == "active" or public.status == "revoked", "origin revocation is final")
        raw = canonical(dict(installation_id=installation, public_registry=_json(registry)))
        staged = path.with_suffix(".staged")
        descriptor = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fchmod(stream.fileno(), 0o644)
                os.fsync(stream.fileno())
            os.replace(staged, path)
            _sync_directory(directory)
        finally:
            staged.unlink(missing_ok=True)
        accepted, reread = _read_trust(root, installation, root_owned=root_owned)
        _require(reread == raw, "trust changed")
    return dict(installation_id=installation, key_id=accepted.key_id, status=accepted.status)


def register_origin_v1(installation: str, registry: bytes) -> dict:
    from install.birth_certification_issuer import _require_root_v1

    _require_root_v1()
    _root_owned_chain(DEFAULT_OWNERSHIP_ROOT_V1)
    return _write_trust(DEFAULT_OWNERSHIP_ROOT_V1, installation, registry)


def export_evidence_v1() -> dict:
    from executor_birth_certification_authority import load_certification_public_key_v1
    from install.birth_certification_evidence import administrative_evidence_v1
    from install.birth_certification_issuer import _require_root_v1, _sign_certificate_v1

    _require_root_v1()
    origin = observe_destination_v1()
    with administrative_evidence_v1() as owner:
        document, artifacts = build_export_v1(owner, origin, load_certification_public_key_v1().key_id)
        _require(observe_destination_v1() == origin, "origin changed")
        signed = _sign_certificate_v1(document)
        # Independently verify the signer output, using only its public half.
        verify_export_v1(signed, artifacts, load_certification_public_key_v1(), origin)
        return encode_bundle_v1(signed, artifacts)


def import_evidence_v1(bundle: dict) -> dict:
    from install.birth_certification_evidence import administrative_evidence_v1
    from install.birth_certification_issuer import _require_root_v1

    _require_root_v1()
    _root_owned_chain(DEFAULT_OWNERSHIP_ROOT_V1)
    signed, artifacts = decode_bundle_v1(bundle)
    origin = _context(_json(signed)["origin"])
    with administrative_evidence_v1() as owner:
        destination = observe_destination_v1()
        trusted, raw_trust = _read_trust(DEFAULT_OWNERSHIP_ROOT_V1, origin["installation_id"])
        verified = verify_export_v1(signed, artifacts, trusted, destination)
        _check_import_history(owner, verified, destination)
        _require(observe_destination_v1() == destination
                 and _read_trust(DEFAULT_OWNERSHIP_ROOT_V1, origin["installation_id"])[1] == raw_trust,
                 "import frontier changed")
        frontier = owner.import_operational(exported=signed, artifacts=artifacts,
            destination=canonical(destination), predecessor=None)
    return dict(import_id=frontier.operational_import, origin=verified["origin"],
                destination=destination, evidence_head=frontier.head)


def _check_import_history(owner: _Evidence, document: dict, destination: dict) -> None:
    """A fresh import cannot erase a continuation or resurrect invalidated cycles."""
    prior, invalidated = None, False
    events, _ = owner.snapshot()
    for raw in events:
        event = _json(raw)
        if event["kind"] == "operational_import":
            payload = event["payload"]
            exported = _json(owner._artifact(payload["export"]))
            if (exported["origin"]["installation_id"] == document["origin"]["installation_id"]
                    and exported["cycles"] == document["cycles"]):
                prior, invalidated = payload, False
        elif prior is not None and (event["kind"] == "defect_opened"
                or event["kind"] == "census" and event["payload"]["findings"]):
            invalidated = True
    if prior is not None:
        _require(not invalidated, "cycles invalidated by local finding")
        _require(_json(owner._artifact(prior["destination"])) == destination
                 and prior["predecessor"] is None, "continuation required")


def audit_local_evidence_v1(owner: _Evidence) -> dict:
    """Local certification has the same native replay obligations as export."""
    destination = observe_destination_v1()
    audit = _audit(owner)
    _require(audit["platform"] == destination["conditions"]["platform"], "profile platform mismatch")
    bindings = audit["bindings"]
    _require((bindings["installation_id"], bindings["head_id"], bindings["source_id"])
             == (destination["installation_id"], destination["head_id"], destination["source_closure"]),
             "local profile origin or source mismatch")
    return destination


def operational_evidence_v1(owner: _Evidence, destination: dict):
    """Every qualification rechecks current trust and the complete retained bundle."""
    from install.birth_certification_qualification import OperationalEvidenceV1

    reference = owner.frontier.operational_import
    if reference is None:
        return None
    event = owner.event(reference)["payload"]
    imported_destination = _json(owner._artifact(event["destination"]))
    _require(imported_destination == destination, "destination changed; continuation required")
    signed = owner._artifact(event["export"])
    document = _json(signed)
    artifacts = {digest: owner._artifact(digest) for digest in document["artifacts"]}
    trusted, _ = _read_trust(DEFAULT_OWNERSHIP_ROOT_V1, document["origin"]["installation_id"])
    verified = verify_export_v1(signed, artifacts, trusted, destination)
    return OperationalEvidenceV1(reference, _hash(signed), verified["origin"]["installation_id"],
        destination["installation_id"], destination["head_id"], verified["profile"],
        tuple(verified["cycles"]), event["predecessor"])


def _continue(owner: _Evidence, destination: dict, certificate: bytes, authority,
              trusted) -> dict:
    """An unchanged successor keeps the exact signed and archived predecessor."""
    from executor_birth_lifecycle import _decode_f5_activation_v1

    _context(destination)
    frontier = owner.frontier
    _require(frontier.operational_import is not None and not frontier.open_findings
             and frontier.pending_cycle is None, "continuation evidence unavailable")
    imported = owner.event(frontier.operational_import)["payload"]
    before = _context(_json(owner._artifact(imported["destination"])))
    _require(before["installation_id"] == destination["installation_id"], "continuation installation")
    accepted = _decode_f5_activation_v1(certificate, authority=authority,
        installation_id=before["installation_id"], head_id=before["head_id"],
        closed_build_id=before["closed_build_id"]).certificate
    _require(accepted.migration_id == before["migration_id"] == destination["migration_id"],
             "continuation migration changed")
    # The active predecessor must have been prepared against this exact import;
    # a valid signature alone cannot invent the missing qualification history.
    events, _ = owner.snapshot()
    matches = [value["payload"] for raw in events
               if (value := _json(raw))["kind"] == "certificate_prepared"
               and value["payload"]["certificate"] == _hash(certificate)
               and value["payload"]["operational_import"] == frontier.operational_import]
    _require(len(matches) == 1, "predecessor chain missing or ambiguous")
    prior_qualification = _json(owner._artifact(matches[0]["qualification"]))
    _require(prior_qualification["qualification_id"] == accepted.qualification_id,
             "predecessor qualification mismatch")
    _require(before["head_id"] != destination["head_id"], "continuation requires new head")
    signed = owner._artifact(imported["export"])
    document = _json(signed)
    artifacts = {digest: owner._artifact(digest) for digest in document["artifacts"]}
    verify_export_v1(signed, artifacts, trusted, destination)
    return dict(exported=signed, artifacts=artifacts, destination=canonical(destination),
                predecessor=certificate)


def continue_evidence_v1() -> dict:
    from executor_birth_certification_authority import load_certification_public_key_v1
    from executor_birth_lifecycle import ACTIVATION_MAX_BYTES
    from install.birth_certification_evidence import administrative_evidence_v1
    from install.birth_certification_issuer import (
        _require_root_v1, ACTIVATION_DIRECTORY_V1, CERTIFICATE_BASENAME_V1,
    )

    _require_root_v1()
    _root_owned_chain(DEFAULT_OWNERSHIP_ROOT_V1)
    with administrative_evidence_v1() as owner:
        destination = observe_destination_v1()
        _require(owner.frontier.operational_import is not None, "continuation import missing")
        imported = owner.event(owner.frontier.operational_import)["payload"]
        origin = _json(owner._artifact(imported["export"]))["origin"]
        trusted, raw_trust = _read_trust(DEFAULT_OWNERSHIP_ROOT_V1, origin["installation_id"])
        certificate_path = ACTIVATION_DIRECTORY_V1 / CERTIFICATE_BASENAME_V1
        certificate = _read_regular(certificate_path, maximum=ACTIVATION_MAX_BYTES,
                                    mode=0o644, root_owned=True)
        prepared = _continue(owner, destination, certificate, load_certification_public_key_v1(), trusted)
        _require(observe_destination_v1() == destination
                 and _read_trust(DEFAULT_OWNERSHIP_ROOT_V1, origin["installation_id"])[1] == raw_trust
                 and _read_regular(certificate_path, maximum=ACTIVATION_MAX_BYTES,
                                   mode=0o644, root_owned=True) == certificate,
                 "continuation frontier changed")
        frontier = owner.import_operational(**prepared)
    return dict(import_id=frontier.operational_import, destination=destination,
                predecessor=_hash(certificate), certificate=None)
