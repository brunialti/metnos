#!/usr/bin/env python3
"""V26.5.6.6 offline best-effort evaluation pipeline.

Pure, offline, deterministic. No network, no model, no transport, no gate.
This module never issues a request: it evaluates frames that are handed to it.

S3 of the V26.5.6.4 post mortem: the pipeline does NOT stop at the first
failing stage. Schema, expansion and validation all run, and every stage
reports its own codes, so one evaluation measures more than one failure. In
V26.5.6.4 the adapter aborted 33 of 34 cases before any semantic check, which
left the validator surface unmeasured; that cannot happen here.

S4: a query-free structural fingerprint is attached to every record, valid or
invalid, so a failure is diagnosable without keeping the frame or re-running.

Two hardenings after the independent V26.5.6.5 review:

* B1 - every stage, and the fingerprint, is guarded. A stage that raises is
  reported as a code and the other stages still run, so no case can be lost
  before it is measured. V26.5.6.5 guarded stage 3 only, so an expander
  exception escaped evaluate and the case was recorded on zero of three
  stages: the V26.5.6.4 failure mode, in the branch that exists to survive
  schema-invalid frames.
* B4 - the frozen validator returns immediately after its own internal schema
  check, so a frame that breaks it was reported with a single "schema" code
  and ZERO semantic codes while the record still claimed three measured
  stages. The semantic surface is now re-measured by replaying the frozen
  validator's own post-schema checks, in a guarded best-effort sweep that is
  diagnostic only: it never decides validity.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
from typing import Any

import jsonschema

VERSION = "metnos.v26.5.6.6-offline-pipeline/1.0"

HERE = pathlib.Path(__file__).resolve(strict=True).parent
CANDIDATES = HERE.parent

REGISTRY_PATH = CANDIDATES / "v2641" / "metnos_v2641_typed_registry.json"
REGISTRY_SHA256 = "448c058d805e141c871580253e815849b24a9999d98c505cab970fcd55d1e46f"
VALIDATOR_PATH = CANDIDATES / "v2653" / "metnos_v2653_injected_validator.py"
VALIDATOR_SHA256 = "66760987f5d2335c79114632affa5c04ffecd9ffe5f8e1a7adec7bc50b4b793d"

STAGE_SCHEMA = "schema"
STAGE_EXPANSION = "expansion"
STAGE_VALIDATOR = "validator"
STAGES = (STAGE_SCHEMA, STAGE_EXPANSION, STAGE_VALIDATOR)


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_hash(value: Any) -> str:
    return sha_bytes(json.dumps(
        value, ensure_ascii=True, allow_nan=False,
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8"))


def _read_pinned(path: pathlib.Path, expected: str) -> bytes:
    payload = path.read_bytes()
    if sha_bytes(payload) != expected:
        raise RuntimeError(f"pinned artifact drifted: {path.name}")
    return payload


def load_validator() -> Any:
    """Load the frozen V26.5.3 validator with its registry injected."""
    import types

    registry_bytes = _read_pinned(REGISTRY_PATH, REGISTRY_SHA256)
    source = _read_pinned(VALIDATOR_PATH, VALIDATOR_SHA256)
    module = types.ModuleType("metnos_v26566_pinned_validator")
    module.__dict__["__metnos_registry_bytes__"] = registry_bytes
    module.__file__ = str(VALIDATOR_PATH)
    module.__package__ = None
    exec(compile(source, str(VALIDATOR_PATH), "exec", dont_inherit=True),
         module.__dict__)
    return module


def load_registry() -> dict[str, Any]:
    return json.loads(_read_pinned(REGISTRY_PATH, REGISTRY_SHA256))


def semantic_sweep(
    validator_module: Any, expanded: Any, segments: list[dict[str, Any]],
) -> tuple[list[str], bool]:
    """Replay the frozen validator's post-schema checks, without its gate.

    Returns (codes, measured). Diagnostic only: the authority on validity
    stays validate_frame. Only functions of the pinned validator module are
    called, so no semantic rule is re-implemented here.
    """
    module = validator_module
    frame = expanded if isinstance(expanded, dict) else {}
    limits = module.LIMITS
    errors: list[dict[str, Any]] = []
    codes: list[str] = []
    ran = 0

    def _list(value: Any) -> list[Any]:
        return value if isinstance(value, list) else []

    def section(call: Any) -> Any:
        """One frozen check. A section that cannot run says so and the other
        sections still run, so a broken part never censors the whole frame."""
        nonlocal ran
        try:
            result = call()
        except Exception as error:                  # noqa: BLE001 - reported
            codes.append(f"sweep_exception:{type(error).__name__}")
            return None
        ran += 1
        return result

    def _extend(result: Any) -> None:
        if isinstance(result, list):
            errors.extend(result)

    unsupported = _list(frame.get("unsupported_clauses"))
    _extend(section(lambda: module._validate_unsupported_clauses(
        unsupported, segments, "/unsupported_clauses")))
    total_proofs: int = len(unsupported)
    status = frame.get("status")

    if status == "typed_ambiguity":
        alternatives = _list(frame.get("alternatives"))
        ids = [item.get("alternative_id") for item in alternatives
               if isinstance(item, dict)]
        if ids != list(range(1, len(alternatives) + 1)):
            codes.append("alternative_order")
        semantic: list[Any] = []
        footprints: list[Any] = []
        for index, alternative in enumerate(alternatives):
            atoms = _list((alternative or {}).get("atoms"))
            alternative_errors = section(lambda a=atoms, i=index: module._validate_atom_list(
                a, segments, f"/alternatives/{i}/atoms"))
            _extend(alternative_errors)
            if alternative_errors == []:
                analysis = section(lambda a=atoms: module.canonical_graph_analysis(a))
                if analysis is not None:
                    semantic.append(analysis)
            pair = section(lambda a=atoms, i=index: module._clause_footprint(
                a, unsupported, f"/alternatives/{i}"))
            if pair is not None:
                footprints.append(pair[0])
                _extend(pair[1])
            proof_count = section(lambda a=atoms: module._proof_count(a, unsupported))
            if isinstance(proof_count, int):
                total_proofs += proof_count - len(unsupported)
                if proof_count > limits["max_proofs_per_analysis"]:
                    codes.append("proof_limit")
        if len(semantic) == len(alternatives) and len(
                {module.canonical_hash(item) for item in semantic}) != len(semantic):
            codes.append("duplicate_alternative")
        if footprints and any(item != footprints[0] for item in footprints[1:]):
            codes.append("alternative_coverage")
    elif status == "unsupported":
        clauses = _list(frame.get("clauses"))
        _extend(section(lambda: module._validate_unsupported_clauses(
            clauses, segments, "/clauses")))
        pair = section(lambda: module._clause_footprint([], clauses, "/"))
        if pair is not None:
            _extend(pair[1])
        total_proofs = len(clauses)
    else:
        atoms = _list(frame.get("atoms"))
        _extend(section(lambda: module._validate_atom_list(atoms, segments, "/atoms")))
        pair = section(lambda: module._clause_footprint(atoms, unsupported, "/"))
        if pair is not None:
            _extend(pair[1])
        proof_count = section(lambda: module._proof_count(atoms, unsupported))
        if isinstance(proof_count, int):
            total_proofs = proof_count
            if proof_count > limits["max_proofs_per_analysis"]:
                codes.append("proof_limit")
    if total_proofs > limits["max_total_proofs"]:
        codes.append("total_proof_limit")
    codes.extend(item["code"] for item in errors)
    return sorted(set(codes)), ran > 0


def evaluate(
    frame: Any, segments: list[dict[str, Any]], *,
    schema: dict[str, Any], validator_module: Any, expander: Any,
    max_atoms: int | None = None, fingerprint_vocabulary: Any = frozenset(),
) -> dict[str, Any]:
    """Run every stage best-effort and return one closed record.

    The record carries per-stage codes, an overall verdict and a query-free
    structural fingerprint. It never contains the request, the segments' text,
    or the frame itself. It never raises: every stage is guarded.
    """
    stage_codes: dict[str, list[str]] = {stage: [] for stage in STAGES}
    stage_ran: dict[str, bool] = {stage: False for stage in STAGES}

    # stage 1: schema. Fatal set == exactly what the schema enforces.
    stage_ran[STAGE_SCHEMA] = True
    try:
        schema_errors = sorted(
            jsonschema.Draft202012Validator(schema).iter_errors(frame),
            key=lambda item: list(item.absolute_path),
        )
        stage_codes[STAGE_SCHEMA] = [
            "/" + "/".join(str(part) for part in error.absolute_path)
            for error in schema_errors
        ]
    except Exception as error:                      # noqa: BLE001 - reported
        stage_codes[STAGE_SCHEMA] = [f"schema_exception:{type(error).__name__}"]

    # stage 2: expansion. Total: it runs even when the schema rejected the
    # frame, and it never raises. The guard is belt and braces, symmetric with
    # stage 3, so a future regression of totality cannot lose the case.
    stage_ran[STAGE_EXPANSION] = True
    expanded: Any = {}
    try:
        expanded, expansion_codes = expander.expand_frame(
            frame, max_atoms=max_atoms)
        stage_codes[STAGE_EXPANSION] = list(expansion_codes)
    except Exception as error:                      # noqa: BLE001 - reported
        stage_codes[STAGE_EXPANSION] = [f"expander_exception:{type(error).__name__}"]

    # stage 3: validation. Runs regardless, so the semantic surface is always
    # measured instead of being censored by an earlier abort.
    stage_ran[STAGE_VALIDATOR] = True
    validator_valid = False
    try:
        verdict = validator_module.validate_frame(expanded, segments)
        stage_codes[STAGE_VALIDATOR] = sorted(
            {error["code"] for error in verdict["errors"]}
        )
        validator_valid = bool(verdict["valid"])
    except Exception as error:                      # noqa: BLE001 - reported
        stage_codes[STAGE_VALIDATOR] = [f"validator_exception:{type(error).__name__}"]

    # stage 3b: the frozen validator returns right after its own schema check,
    # so a single "schema" code means the semantic surface was NOT measured.
    # Replay its post-schema checks so the diagnosis is never censored.
    censored = bool(stage_codes[STAGE_VALIDATOR]) and all(
        code == "schema" or code.startswith("validator_exception:")
        for code in stage_codes[STAGE_VALIDATOR]
    )
    if censored:
        semantic_codes, semantic_measured = semantic_sweep(
            validator_module, expanded, segments)
    else:
        semantic_codes = [
            code for code in stage_codes[STAGE_VALIDATOR] if code != "schema"
        ]
        semantic_measured = True

    schema_ok = not stage_codes[STAGE_SCHEMA]
    expansion_ok = not stage_codes[STAGE_EXPANSION]
    valid = schema_ok and expansion_ok and validator_valid
    try:
        fingerprint = expander.structural_fingerprint(
            frame, vocabulary=fingerprint_vocabulary)
    except Exception as error:                      # noqa: BLE001 - reported
        fingerprint = {"fingerprint_exception": type(error).__name__}
    return {
        "valid": valid,
        "stage_ran": stage_ran,
        "stage_codes": stage_codes,
        "schema_ok": schema_ok,
        "expansion_ok": expansion_ok,
        "validator_ok": validator_valid,
        "stages_measured": sum(1 for stage in STAGES if stage_ran[stage]),
        "validator_schema_censored": censored,
        "validator_semantic_measured": semantic_measured,
        "validator_semantic_codes": semantic_codes,
        "fingerprint": fingerprint,
        "fingerprint_sha256": canonical_hash(fingerprint),
    }
