"""Reproducibly build or check the query-free candidate 0.2 artifacts."""
from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path
import subprocess
from typing import Any

from .canonical import canonical_sha256, file_sha256, load_json_file, pretty_json_bytes
from .projection import build_prompt, build_schema, contract_hashes
from .registry_projection import build_projection, load_source_registry, validate_projection


HERE = Path(__file__).resolve().parent
PROJECTION_FILE = HERE / "intent_ir_registry_projection_v0_2.json"
SCHEMA_FILE = HERE / "intent_ir_v0_2.schema.json"
PROMPT_FILE = HERE / "intent_ir_v0_2.prompt.txt"
FREEZE_FILE = HERE / "candidate_v0_2.freeze.json"
FREEZE_FORMAT = "metnos.intent-ir-candidate-freeze/0.2"


def _candidate_files() -> list[Path]:
    result = []
    for path in HERE.rglob("*"):
        if not path.is_file() or path == FREEZE_FILE or "__pycache__" in path.parts:
            continue
        if path.suffix in {".py", ".json", ".txt", ".md"}:
            result.append(path)
    return sorted(result)


def _freeze_payload(value: dict[str, Any]) -> str:
    payload = deepcopy(value)
    payload["integrity"].pop("freeze_payload_sha256", None)
    return canonical_sha256(payload)


def expected_artifacts() -> tuple[bytes, bytes, bytes]:
    registry = load_source_registry()
    projection = build_projection(registry)
    validate_projection(projection)
    return (
        pretty_json_bytes(projection),
        pretty_json_bytes(build_schema(projection)),
        build_prompt(projection).encode("utf-8"),
    )


def build_freeze() -> dict[str, Any]:
    projection = validate_projection(load_json_file(PROJECTION_FILE))
    freeze = {
        "freeze_format": FREEZE_FORMAT,
        "status": "offline_candidate_not_runtime",
        "contract": contract_hashes(projection),
        "files": {
            str(path.relative_to(HERE)): file_sha256(path) for path in _candidate_files()
        },
        "constraints": {
            "gpu_calls": 0,
            "network_calls": 0,
            "production_writes": 0,
            "oracle_in_runner": False,
            "model_facing_ports": False,
        },
        "integrity": {"algorithm": "sha256", "freeze_payload_sha256": ""},
    }
    freeze["integrity"]["freeze_payload_sha256"] = _freeze_payload(freeze)
    return freeze


def _apply(path: Path, content: bytes) -> None:
    text = content.decode("utf-8").rstrip("\n")
    if path.is_file() and path.read_bytes() == content:
        return
    if path.exists():
        subprocess.run(
            ["apply_patch"],
            input=f"*** Begin Patch\n*** Delete File: {path}\n*** End Patch\n",
            text=True, check=True,
        )
    additions = "\n".join("+" + line for line in text.split("\n"))
    subprocess.run(
        ["apply_patch"],
        input=f"*** Begin Patch\n*** Add File: {path}\n{additions}\n*** End Patch\n",
        text=True, check=True,
    )


def write() -> None:
    projection, schema, prompt = expected_artifacts()
    _apply(PROJECTION_FILE, projection)
    _apply(SCHEMA_FILE, schema)
    _apply(PROMPT_FILE, prompt)
    _apply(FREEZE_FILE, pretty_json_bytes(build_freeze()))


def check() -> list[str]:
    expected_projection, expected_schema, expected_prompt = expected_artifacts()
    expected = {
        PROJECTION_FILE: expected_projection,
        SCHEMA_FILE: expected_schema,
        PROMPT_FILE: expected_prompt,
    }
    for path, content in expected.items():
        if not path.is_file() or path.read_bytes() != content:
            raise SystemExit(f"stale artifact: {path.name}")
    freeze = load_json_file(FREEZE_FILE)
    if type(freeze) is not dict or set(freeze) != {
        "freeze_format", "status", "contract", "files", "constraints", "integrity"
    }:
        raise SystemExit("freeze root mismatch")
    if freeze.get("freeze_format") != FREEZE_FORMAT:
        raise SystemExit("freeze format mismatch")
    if freeze.get("integrity", {}).get("freeze_payload_sha256") != _freeze_payload(freeze):
        raise SystemExit("freeze payload mismatch")
    expected_files = {
        str(path.relative_to(HERE)): file_sha256(path) for path in _candidate_files()
    }
    if freeze.get("files") != expected_files:
        raise SystemExit("freeze file set or hash mismatch")
    if type(freeze.get("files")) is not dict or any(
        type(key) is not str or type(value) is not str
        for key, value in freeze["files"].items()
    ):
        raise SystemExit("freeze file map invalid")
    expected_constraints = {
        "gpu_calls": 0,
        "network_calls": 0,
        "production_writes": 0,
        "oracle_in_runner": False,
        "model_facing_ports": False,
    }
    if freeze.get("constraints") != expected_constraints:
        raise SystemExit("freeze constraints mismatch")
    if type(freeze.get("integrity")) is not dict or set(freeze["integrity"]) != {
        "algorithm", "freeze_payload_sha256"
    } or freeze["integrity"].get("algorithm") != "sha256":
        raise SystemExit("freeze integrity shape mismatch")
    if freeze.get("contract") != contract_hashes(load_json_file(PROJECTION_FILE)):
        raise SystemExit("freeze contract mismatch")
    return []


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    check() if args.check else write()


if __name__ == "__main__":
    main()
