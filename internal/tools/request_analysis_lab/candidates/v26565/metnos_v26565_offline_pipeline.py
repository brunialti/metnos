#!/usr/bin/env python3
"""V26.5.6.5 offline best-effort evaluation pipeline.

Pure, offline, deterministic. No network, no model, no transport, no gate.
This module never issues a request: it evaluates frames that are handed to it.

S3 of the V26.5.6.4 post mortem: the pipeline does NOT stop at the first
failing stage. Schema, expansion and validation all run, and every stage
reports its own codes, so one evaluation measures more than one failure. In
V26.5.6.4 the adapter aborted 33 of 34 cases before any semantic check, which
left the validator surface unmeasured; that cannot happen here.

S4: a query-free structural fingerprint is attached to every record, valid or
invalid, so a failure is diagnosable without keeping the frame or re-running.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
from typing import Any

import jsonschema

VERSION = "metnos.v26.5.6.5-offline-pipeline/1.0"

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
    module = types.ModuleType("metnos_v26565_pinned_validator")
    module.__dict__["__metnos_registry_bytes__"] = registry_bytes
    module.__file__ = str(VALIDATOR_PATH)
    module.__package__ = None
    exec(compile(source, str(VALIDATOR_PATH), "exec", dont_inherit=True),
         module.__dict__)
    return module


def load_registry() -> dict[str, Any]:
    return json.loads(_read_pinned(REGISTRY_PATH, REGISTRY_SHA256))


def evaluate(
    frame: Any, segments: list[dict[str, Any]], *,
    schema: dict[str, Any], validator_module: Any, expander: Any,
) -> dict[str, Any]:
    """Run every stage best-effort and return one closed record.

    The record carries per-stage codes, an overall verdict and a query-free
    structural fingerprint. It never contains the request, the segments' text,
    or the frame itself.
    """
    stage_codes: dict[str, list[str]] = {stage: [] for stage in STAGES}
    stage_ran: dict[str, bool] = {stage: False for stage in STAGES}

    # stage 1: schema. Fatal set == exactly what the schema enforces.
    stage_ran[STAGE_SCHEMA] = True
    schema_errors = sorted(
        jsonschema.Draft202012Validator(schema).iter_errors(frame),
        key=lambda item: list(item.absolute_path),
    )
    stage_codes[STAGE_SCHEMA] = [
        "/" + "/".join(str(part) for part in error.absolute_path)
        for error in schema_errors
    ]

    # stage 2: expansion. Total: it runs even when the schema rejected the
    # frame, and it never raises.
    stage_ran[STAGE_EXPANSION] = True
    expanded, expansion_codes = expander.expand_frame(frame)
    stage_codes[STAGE_EXPANSION] = list(expansion_codes)

    # stage 3: validation. Runs regardless, so the semantic surface is always
    # measured instead of being censored by an earlier abort.
    stage_ran[STAGE_VALIDATOR] = True
    try:
        verdict = validator_module.validate_frame(expanded, segments)
        stage_codes[STAGE_VALIDATOR] = sorted(
            {error["code"] for error in verdict["errors"]}
        )
        validator_valid = bool(verdict["valid"])
    except Exception as error:                      # noqa: BLE001 - reported
        stage_codes[STAGE_VALIDATOR] = [f"validator_exception:{type(error).__name__}"]
        validator_valid = False

    schema_ok = not stage_codes[STAGE_SCHEMA]
    expansion_ok = not stage_codes[STAGE_EXPANSION]
    valid = schema_ok and expansion_ok and validator_valid
    fingerprint = expander.structural_fingerprint(frame)
    return {
        "valid": valid,
        "stage_ran": stage_ran,
        "stage_codes": stage_codes,
        "schema_ok": schema_ok,
        "expansion_ok": expansion_ok,
        "validator_ok": validator_valid,
        "stages_measured": sum(1 for stage in STAGES if stage_ran[stage]),
        "fingerprint": fingerprint,
        "fingerprint_sha256": canonical_hash(fingerprint),
    }
