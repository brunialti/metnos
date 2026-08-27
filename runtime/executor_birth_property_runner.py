"""Core-owned orchestration of the seven RM-0008 property groups."""
from __future__ import annotations

import tomllib
import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Mapping, Protocol

from executor_birth_identity import encode_framed_v1
from executor_birth_properties import (
    PROPERTY_CATALOG_V1,
    PropertyContractError,
    PropertyEvidence,
    PropertySpec,
    PropertyStatus,
)
from executor_birth_primitive_table_v1 import (
    PrimitiveTableError, check_primitive_v1, check_registry_v1,
)
from executor_birth_runner import (
    FixtureOp, FixtureOpKind, LinuxSandboxRegistry, RunnerStatus,
    WindowsSandboxRegistry,
    run_birth_phase,
)


@dataclass(frozen=True, slots=True)
class PropertyCase:
    case_id: str
    input_value: Mapping[str, object]
    expectation: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class PropertyRunResult:
    output: Mapping[str, object]
    observations: Mapping[str, object]
    runner_attestation_hash: str


class PropertyRunner(Protocol):
    def run(self, case: PropertyCase, *, fixture_id: str, isolation: str) -> PropertyRunResult: ...


def _attestation_hash(result: object, *, candidate_id: str, case_id: str,
                      fixture_id: str, isolation: str) -> str:
    attestation = getattr(result, "attestation")
    return _hash({
        "candidate_id": candidate_id,
        "case_id": case_id,
        "fixture_id": fixture_id,
        "isolation": isolation,
        "backend": attestation.backend,
        "sandboxed": attestation.sandboxed,
        "network_unshared": attestation.network_unshared,
        "pid_unshared": attestation.pid_unshared,
        "user_unshared": attestation.user_unshared,
        "ipc_unshared": attestation.ipc_unshared,
        "uts_unshared": attestation.uts_unshared,
        "cgroup_v2": attestation.cgroup_v2,
        "tree_empty": attestation.tree_empty,
        "termination_attested": attestation.termination_attested,
    })


_HARNESS_PATH = "_metnos_birth_property_harness_v1.py"
_HARNESS_SOURCE = r'''
import hashlib, json, pathlib, subprocess, sys
harness_dir = pathlib.Path(__file__).resolve().parent
entrypoint = str(harness_dir.joinpath(*pathlib.PurePosixPath(sys.argv[1]).parts))
request = json.loads(pathlib.Path('request.json').read_text())
fixture = pathlib.Path('fixture')
def tree_hash():
    digest = hashlib.sha256(b'metnos.birth.fixture-tree/v1\0')
    for path in sorted(fixture.rglob('*')):
        relative = path.relative_to(fixture).as_posix().encode()
        digest.update(len(relative).to_bytes(8, 'big')); digest.update(relative)
        if path.is_file():
            payload = path.read_bytes()
            digest.update(b'f'); digest.update(len(payload).to_bytes(8, 'big')); digest.update(payload)
        elif path.is_dir(): digest.update(b'd')
        else: raise RuntimeError('fixture_node_invalid')
    return 'sha256:' + digest.hexdigest()
def file_hash(path):
    return 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest()
def invoke(action):
    value = dict(request); value['birth_property_action'] = action
    result = subprocess.run([sys.executable, entrypoint], input=json.dumps(value).encode(),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        sys.stderr.buffer.write(result.stderr); raise SystemExit(result.returncode)
    parsed = json.loads(result.stdout)
    if not isinstance(parsed, dict): raise RuntimeError('candidate_output_invalid')
    return parsed
before = tree_hash(); observations = {}
if request['fixture_id'] == 'private_deletion_tree':
    source = fixture / 'source.bin'; recovery = fixture / 'recovery.bin'
    source_hash = file_hash(source)
    output = invoke('prepare_delete')
    if source.is_file() and recovery.is_file() and file_hash(recovery) == source_hash:
        output = invoke('commit_delete')
        if not source.exists() and recovery.is_file() and file_hash(recovery) == source_hash:
            observations.update(filesystem_events=['copy', 'delete'],
                                source_before_hash=source_hash,
                                recovery_copy_hash=file_hash(recovery))
    after = tree_hash()
else:
    output = invoke('forward'); after = tree_hash()
if request['fixture_id'] == 'private_mutable_state':
    invoke('undo'); restored = tree_hash()
    observations.update(state_before_hash=before, state_after_forward_hash=after,
                        state_after_undo_hash=restored)
print(json.dumps({'output': output, 'observations': observations},
                 sort_keys=True, separators=(',', ':')))
'''.encode("utf-8")


class ObservedPropertyRunner:
    """Core-owned adapter bound to the exact private candidate observation.

    The candidate receives a closed JSON request and may emit only its ordinary
    JSON result.  Evidence about fixtures and isolation is reconstructed here;
    candidate fields that resemble attestations are never trusted.
    """

    def __init__(self, observed: object, *,
                 windows_registry: WindowsSandboxRegistry | None = None,
                 linux_registry: LinuxSandboxRegistry | None = None) -> None:
        from executor_birth import ObservedCandidate
        if not isinstance(observed, ObservedCandidate):
            raise PropertyContractError("property_candidate_invalid", "observation")
        self._observed = observed
        self._windows_registry = windows_registry
        self._linux_registry = linux_registry

    def _entrypoint(self) -> str:
        try:
            manifest = tomllib.loads(
                self._observed.snapshot.manifest_bytes.decode("utf-8")
            )
            files = manifest["code"]["files"]
            entrypoint = files[0]
        except (KeyError, IndexError, TypeError, UnicodeDecodeError, ValueError) as exc:
            raise PropertyContractError("property_candidate_invalid", "entrypoint") from exc
        if not isinstance(entrypoint, str) or PurePosixPath(entrypoint).is_absolute():
            raise PropertyContractError("property_candidate_invalid", "entrypoint")
        return entrypoint

    @staticmethod
    def _fixture(case: PropertyCase, fixture_id: str) -> tuple[FixtureOp, ...]:
        count = case.input_value.get("fixture_count", case.expectation.get("fixture_total", 0))
        count = count if type(count) is int and 0 <= count <= 16 else 0
        request = {"case_id": case.case_id, "fixture_id": fixture_id,
                   "input": dict(case.input_value), "fixture_root": "fixture"}
        if fixture_id == "private_mutable_state":
            request["state_path"] = "fixture/state.json"
        if fixture_id == "private_deletion_tree":
            request.update(source_path="fixture/source.bin",
                           recovery_path="fixture/recovery.bin")
        ops: list[FixtureOp] = [
            FixtureOp(FixtureOpKind.MKDIR, "fixture"),
            FixtureOp(FixtureOpKind.SEED_JSON, "request.json", request),
        ]
        if count:
            ops.append(FixtureOp(FixtureOpKind.MKDIR, "fixture/entries"))
            for index in range(count):
                ops.append(FixtureOp(FixtureOpKind.SEED_JSON,
                                     f"fixture/entries/{index}.json", {"index": index}))
        if fixture_id == "private_mutable_state":
            ops.append(FixtureOp(FixtureOpKind.SEED_JSON, "fixture/state.json", {"value": "before"}))
        if fixture_id == "private_deletion_tree":
            ops.append(FixtureOp(FixtureOpKind.WRITE_BYTES, "fixture/source.bin", b"birth-fixture-v1"))
        return tuple(ops)

    def run(self, case: PropertyCase, *, fixture_id: str, isolation: str) -> PropertyRunResult:
        # A fixed core harness supplies stdin from the immutable request file;
        # neither command nor candidate bytes come from the Birth caller.
        entrypoint = self._entrypoint()
        candidate_files = dict(self._observed.snapshot.code_files)
        if _HARNESS_PATH in candidate_files:
            raise PropertyContractError("property_candidate_invalid", "reserved_harness_path")
        candidate_files[_HARNESS_PATH] = _HARNESS_SOURCE
        command = (
            (_HARNESS_PATH, entrypoint)
            if sys.platform == "win32"
            else (sys.executable, "-I", "candidate/" + _HARNESS_PATH,
                  entrypoint)
        )
        result = run_birth_phase(
            command,
            fixture_ops=self._fixture(case, fixture_id),
            candidate_id=self._observed.identities.candidate_id,
            candidate_files=candidate_files,
            windows_registry=self._windows_registry,
            linux_registry=self._linux_registry,
        )
        attestation_hash = _attestation_hash(
            result, candidate_id=self._observed.identities.candidate_id,
            case_id=case.case_id, fixture_id=fixture_id, isolation=isolation,
        )
        if result.status is not RunnerStatus.PASSED:
            raise RuntimeError(result.error_code or "property_runner_unavailable")
        try:
            envelope = json.loads(result.stdout)
        except (TypeError, ValueError) as exc:
            raise PropertyContractError("property_runner_result_invalid", "json") from exc
        if (not isinstance(envelope, Mapping) or set(envelope) != {"output", "observations"}
                or not isinstance(envelope["output"], Mapping)
                or not isinstance(envelope["observations"], Mapping)):
            raise PropertyContractError("property_runner_result_invalid", "output")
        output = dict(envelope["output"])
        observations: dict[str, object] = dict(envelope["observations"])
        fixture_total = case.expectation.get("fixture_total")
        if type(fixture_total) is int:
            observations["fixture_total"] = fixture_total
        return PropertyRunResult(output, observations, attestation_hash)


@dataclass(frozen=True, slots=True)
class PropertyCandidateProfile:
    """Core-derived applicability facts; never decoded from a manifest table."""
    output_schema: tuple[tuple[str, str], ...] = ()
    collection_output: bool = False
    limit_input: bool = False
    truncation_declared: bool = False
    revertible: bool = False
    destructive_with_undo: bool = False
    entries_and_results: bool = False

    def __post_init__(self) -> None:
        allowed_types = {"array", "boolean", "integer", "null", "number", "object", "string"}
        keys: set[str] = set()
        for item in self.output_schema:
            if (
                not isinstance(item, tuple) or len(item) != 2
                or not isinstance(item[0], str) or not item[0]
                or item[0] in keys or not isinstance(item[1], str)
                or item[1] not in allowed_types
            ):
                raise PropertyContractError("property_candidate_invalid", "output_schema")
            keys.add(item[0])
        for name in (
            "collection_output", "limit_input", "truncation_declared", "revertible",
            "destructive_with_undo", "entries_and_results",
        ):
            if type(getattr(self, name)) is not bool:
                raise PropertyContractError("property_candidate_invalid", name)


def _hash(value: object) -> str:
    return "sha256:" + hashlib.sha256(encode_framed_v1(value)).hexdigest()


def _collection_cases(_candidate: PropertyCandidateProfile) -> tuple[PropertyCase, ...]:
    return tuple(PropertyCase(f"cardinality.{count}", {"fixture_count": count}, {"count": count}) for count in (0, 1, 3))


def _limit_cases(_candidate: PropertyCandidateProfile) -> tuple[PropertyCase, ...]:
    return (PropertyCase("limit.0", {"fixture_count": 3, "limit": 0}, {"max_count": 0}),
            PropertyCase("limit.below_total", {"fixture_count": 3, "limit": 2}, {"max_count": 2}))


def _single(case_id: str):
    def generate(_candidate: PropertyCandidateProfile) -> tuple[PropertyCase, ...]:
        return (PropertyCase(case_id, {}, {}),)
    return generate


def _truncation_cases(_candidate: PropertyCandidateProfile) -> tuple[PropertyCase, ...]:
    expectation = {"fixture_total": 3, "limit": 2}
    return (PropertyCase("truncation.boundary", expectation, expectation),)


_GENERATORS = {
    "declared_output_cases": _single("output.actual"),
    "cardinality_cases": _collection_cases,
    "limit_boundary_cases": _limit_cases,
    "truncation_cases": _truncation_cases,
    "undo_round_trip_cases": _single("undo.round_trip"),
    "delete_copy_cases": _single("delete.copy_before"),
    "entries_results_cases": _single("entries.results"),
}


def _output_schema(output, candidate, _expect, _observations):
    def matches(value: object, type_name: str) -> bool:
        return {
            "array": lambda: isinstance(value, list),
            "boolean": lambda: type(value) is bool,
            "integer": lambda: type(value) is int,
            "null": lambda: value is None,
            "number": lambda: type(value) is int or (
                type(value) is float and math.isfinite(value)
            ),
            "object": lambda: isinstance(value, Mapping),
            "string": lambda: isinstance(value, str),
        }[type_name]()

    return bool(candidate.output_schema) and all(
        key in output and matches(output[key], type_name)
        for key, type_name in candidate.output_schema
    )


def _cardinality(output, _candidate, expect, _observations):
    entries = output.get("entries", output.get("results"))
    return isinstance(entries, list) and len(entries) == expect["count"]


def _limit(output, _candidate, expect, _observations):
    entries = output.get("entries", output.get("results"))
    return isinstance(entries, list) and len(entries) <= expect["max_count"]


def _truncation(output, _candidate, expect, observations):
    entries = output.get("entries", output.get("results"))
    return (
        isinstance(entries, list)
        and len(entries) == expect["limit"]
        and output.get("truncated") is True
        and observations.get("fixture_total") == expect["fixture_total"]
    )


def _state_round_trip(_output, _candidate, _expect, observations):
    before = observations.get("state_before_hash")
    mutated = observations.get("state_after_forward_hash")
    restored = observations.get("state_after_undo_hash")
    return (
        isinstance(before, str) and _DIGEST_RE.fullmatch(before) is not None
        and isinstance(mutated, str) and _DIGEST_RE.fullmatch(mutated) is not None
        and isinstance(restored, str) and _DIGEST_RE.fullmatch(restored) is not None
        and restored == before and mutated != before
    )


def _copy_precedes_delete(_output, _candidate, _expect, observations):
    events = observations.get("filesystem_events")
    source = observations.get("source_before_hash")
    recovery = observations.get("recovery_copy_hash")
    return (
        isinstance(events, list)
        and events.count("copy") == 1
        and events.count("delete") == 1
        and events.index("copy") < events.index("delete")
        and isinstance(source, str) and _DIGEST_RE.fullmatch(source) is not None
        and isinstance(recovery, str) and _DIGEST_RE.fullmatch(recovery) is not None
        and recovery == source
    )


def _coherent(output, _candidate, _expect, _observations):
    return isinstance(output.get("entries"), list) and output.get("entries") == output.get("results")


_ORACLES = {
    "output_schema": _output_schema,
    "cardinality": _cardinality,
    "limit_semantics": _limit,
    "truncation": _truncation,
    "state_round_trip": _state_round_trip,
    "copy_precedes_delete": _copy_precedes_delete,
    "entries_results_coherence": _coherent,
}

_FIXTURES = frozenset({
    "empty_private_root", "bounded_collection", "oversized_collection",
    "private_mutable_state", "private_deletion_tree",
})
_APPLICABILITY = {
    "output_schema_declared": lambda c: bool(c.output_schema),
    "collection_output": lambda c: c.collection_output,
    "bounded_collection_input": lambda c: c.limit_input,
    "truncation_declared": lambda c: c.truncation_declared,
    "revertible_executor": lambda c: c.revertible,
    "destructive_with_undo": lambda c: c.destructive_with_undo,
    "entries_and_results_output": lambda c: c.entries_and_results,
}

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")

# The four registries above are the implementation; the table is the closed set
# the admission context speaks about.  Comparing them here, at import, is what
# stops the identity and the reachable set from drifting apart in silence.
for _kind, _names in (
    ("applicability", _APPLICABILITY),
    ("fixture", _FIXTURES),
    ("generator", _GENERATORS),
    ("oracle", _ORACLES),
):
    check_registry_v1(_kind, _names)
del _kind, _names


def _resolve(spec: PropertySpec):
    """Resolve one property against the closed table, then the registries."""
    try:
        check_primitive_v1("applicability", spec.applicability_id)
        check_primitive_v1("generator", spec.generator_id)
        check_primitive_v1("oracle", spec.oracle_id)
        return (_APPLICABILITY[spec.applicability_id], _GENERATORS[spec.generator_id],
                _ORACLES[spec.oracle_id])
    except PrimitiveTableError as exc:
        raise PropertyContractError("property_registry_invalid", exc.detail) from exc
    except KeyError as exc:  # closed core configuration failure
        raise PropertyContractError("property_registry_invalid", str(exc)) from exc


def run_property(
    property_id: str,
    candidate: PropertyCandidateProfile,
    *,
    _runner: PropertyRunner,
) -> tuple[PropertyEvidence, ...]:
    """Run a core property.  `_runner` is a Birth-owned/test seam, not manifest data."""
    if not isinstance(candidate, PropertyCandidateProfile):
        raise PropertyContractError("property_candidate_invalid", "profile")
    try:
        spec = PROPERTY_CATALOG_V1[property_id]
    except KeyError as exc:
        raise PropertyContractError("property_unknown", property_id) from exc
    applicable, generate, oracle = _resolve(spec)
    try:
        check_primitive_v1("fixture", spec.fixture_id)
    except PrimitiveTableError as exc:
        raise PropertyContractError("property_registry_invalid", exc.detail) from exc
    if not applicable(candidate):
        return ()
    cases = generate(candidate)
    if not cases or len(cases) > spec.max_cases:
        raise PropertyContractError("property_cases_invalid", property_id)
    evidence = []
    for case in cases:
        try:
            result = _runner.run(case, fixture_id=spec.fixture_id, isolation=spec.isolation.value)
        except PropertyContractError:
            raise
        except Exception as exc:  # runner unavailability is fail-closed evidence
            status = PropertyStatus.UNAVAILABLE
            error = "property_runner_unavailable"
            output_hash = _hash({"unavailable": type(exc).__name__})
            attestation_hash = _hash({"attestation": "unavailable"})
        else:
            if not isinstance(result, PropertyRunResult):
                raise PropertyContractError("property_runner_result_invalid")
            if not isinstance(result.output, Mapping) or not isinstance(result.observations, Mapping):
                raise PropertyContractError("property_runner_result_invalid", "mappings")
            if not isinstance(result.runner_attestation_hash, str) or _DIGEST_RE.fullmatch(result.runner_attestation_hash) is None:
                raise PropertyContractError("property_runner_result_invalid", "attestation")
            passed = oracle(
                result.output, candidate, case.expectation, result.observations,
            )
            status = PropertyStatus.PASSED if passed else PropertyStatus.FAILED
            error = "" if passed else "property_oracle_failed"
            output_hash = _hash({
                "output": dict(result.output),
                "trusted_observations": dict(result.observations),
            })
            attestation_hash = result.runner_attestation_hash
        evidence.append(PropertyEvidence(
            property_id=spec.property_id, property_version=spec.version,
            case_id=case.case_id, status=status,
            input_hash=_hash(dict(case.input_value)), output_hash=output_hash,
            oracle_hash=_hash({"oracle_id": spec.oracle_id, "version": spec.version}),
            runner_attestation_hash=attestation_hash, error_code=error,
        ))
    return tuple(evidence)


def run_applicable_properties(
    candidate: PropertyCandidateProfile, *, _runner: PropertyRunner,
) -> tuple[PropertyEvidence, ...]:
    return tuple(
        evidence
        for property_id in PROPERTY_CATALOG_V1
        for evidence in run_property(property_id, candidate, _runner=_runner)
    )
