"""Build/check the immutable candidate v0.3 prompt-only overlay."""
from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path
import subprocess
from typing import Any

from ..candidate_v0_2 import build_artifacts as base_build
from ..candidate_v0_2_1 import build_artifacts as language_build
from ..candidate_v0_2.canonical import (
    canonical_sha256,
    file_sha256,
    load_json_file,
    pretty_json_bytes,
)
from ..candidate_v0_2.registry_projection import load_projection, validate_projection
from .projection import build_prompt, build_schema, contract_hashes


HERE = Path(__file__).resolve().parent
BASE_DIR = HERE.parent / "candidate_v0_2"
LANGUAGE_DIR = HERE.parent / "candidate_v0_2_1"
SCHEMA_FILE = HERE / "intent_ir_v0_3.schema.json"
PROMPT_FILE = HERE / "intent_ir_v0_3.prompt.txt"
FREEZE_FILE = HERE / "candidate_v0_3.freeze.json"
FREEZE_FORMAT = "metnos.intent-ir-prompt-overlay-freeze/0.3"

BASE_CRITICAL_FILES = (
    "candidate_v0_2.freeze.json",
    "intent_ir_registry_projection_v0_2.json",
    "intent_ir_v0_2.schema.json",
    "intent_ir_v0_2.prompt.txt",
    "api.py",
    "build_artifacts.py",
    "canonical.py",
    "compiler.py",
    "critic.py",
    "language_tag.py",
    "projection.py",
    "registry_projection.py",
    "structured_client.py",
    "types.py",
    "validator.py",
)

LANGUAGE_PREREQUISITE_FILES = (
    "candidate_v0_2_1.freeze.json",
    "grandfathered_tags.py",
    "language_tag.py",
)


def _overlay_files() -> list[Path]:
    result = []
    for path in HERE.rglob("*"):
        if not path.is_file() or path == FREEZE_FILE or "__pycache__" in path.parts:
            continue
        if path.suffix in {".py", ".json", ".txt", ".md"}:
            result.append(path)
    return sorted(result)


def _base_hashes() -> dict[str, str]:
    return {name: file_sha256(BASE_DIR / name) for name in BASE_CRITICAL_FILES}


def _freeze_payload(value: dict[str, Any]) -> str:
    payload = deepcopy(value)
    payload["integrity"].pop("freeze_payload_sha256", None)
    return canonical_sha256(payload)


def expected_artifacts() -> tuple[bytes, bytes]:
    projection = validate_projection(load_projection())
    schema = pretty_json_bytes(build_schema(projection))
    if schema != (BASE_DIR / "intent_ir_v0_2.schema.json").read_bytes():
        raise SystemExit("v0.3 schema differs from frozen v0.2")
    return schema, build_prompt(projection).encode("utf-8")


def build_freeze() -> dict[str, Any]:
    projection = validate_projection(load_projection())
    base_freeze = load_json_file(BASE_DIR / "candidate_v0_2.freeze.json")
    freeze = {
        "freeze_format": FREEZE_FORMAT,
        "status": "offline_prompt_overlay_not_runtime",
        "base_candidate": {
            "freeze_sha256": file_sha256(BASE_DIR / "candidate_v0_2.freeze.json"),
            "contract": base_freeze["contract"],
            "critical_files": _base_hashes(),
        },
        "language_prerequisite": {
            "freeze_sha256": file_sha256(LANGUAGE_DIR / "candidate_v0_2_1.freeze.json"),
            "critical_files": {
                name: file_sha256(LANGUAGE_DIR / name)
                for name in LANGUAGE_PREREQUISITE_FILES
            },
        },
        "contract": contract_hashes(projection),
        "files": {
            str(path.relative_to(HERE)): file_sha256(path) for path in _overlay_files()
        },
        "constraints": {
            "logical_change": ["prompt_projection_only"],
            "schema_changed": False,
            "validator_changed": False,
            "compiler_changed": False,
            "adapter_semantics_changed": False,
            "registry_changed": False,
            "gpu_calls": 0,
            "network_calls": 0,
            "oracle_in_runner": False,
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
            text=True,
            check=True,
        )
    additions = "\n".join("+" + line for line in text.split("\n"))
    subprocess.run(
        ["apply_patch"],
        input=f"*** Begin Patch\n*** Add File: {path}\n{additions}\n*** End Patch\n",
        text=True,
        check=True,
    )


def write() -> None:
    base_errors = base_build.check()
    if type(base_errors) is not list or base_errors != []:
        raise SystemExit("base candidate verification failed")
    language_errors = language_build.check()
    if type(language_errors) is not list or language_errors != []:
        raise SystemExit("language prerequisite verification failed")
    schema, prompt = expected_artifacts()
    _apply(SCHEMA_FILE, schema)
    _apply(PROMPT_FILE, prompt)
    _apply(FREEZE_FILE, pretty_json_bytes(build_freeze()))


def check() -> list[str]:
    base_errors = base_build.check()
    if type(base_errors) is not list or base_errors != []:
        raise SystemExit("base candidate verification failed")
    language_errors = language_build.check()
    if type(language_errors) is not list or language_errors != []:
        raise SystemExit("language prerequisite verification failed")
    schema, prompt = expected_artifacts()
    expected = {SCHEMA_FILE: schema, PROMPT_FILE: prompt}
    for path, content in expected.items():
        if not path.is_file() or path.read_bytes() != content:
            raise SystemExit(f"stale overlay artifact: {path.name}")
    freeze = load_json_file(FREEZE_FILE)
    if type(freeze) is not dict or set(freeze) != {
        "freeze_format", "status", "base_candidate", "language_prerequisite", "contract", "files",
        "constraints", "integrity",
    }:
        raise SystemExit("overlay freeze root mismatch")
    if freeze.get("freeze_format") != FREEZE_FORMAT:
        raise SystemExit("overlay freeze format mismatch")
    if freeze.get("integrity", {}).get("freeze_payload_sha256") != _freeze_payload(freeze):
        raise SystemExit("overlay freeze payload mismatch")
    expected_base = {
        "freeze_sha256": file_sha256(BASE_DIR / "candidate_v0_2.freeze.json"),
        "contract": load_json_file(BASE_DIR / "candidate_v0_2.freeze.json")["contract"],
        "critical_files": _base_hashes(),
    }
    if freeze.get("base_candidate") != expected_base:
        raise SystemExit("base candidate binding mismatch")
    expected_language = {
        "freeze_sha256": file_sha256(LANGUAGE_DIR / "candidate_v0_2_1.freeze.json"),
        "critical_files": {
            name: file_sha256(LANGUAGE_DIR / name)
            for name in LANGUAGE_PREREQUISITE_FILES
        },
    }
    if freeze.get("language_prerequisite") != expected_language:
        raise SystemExit("language prerequisite binding mismatch")
    expected_files = {
        str(path.relative_to(HERE)): file_sha256(path) for path in _overlay_files()
    }
    if freeze.get("files") != expected_files:
        raise SystemExit("overlay freeze file set or hash mismatch")
    expected_constraints = {
        "logical_change": ["prompt_projection_only"],
        "schema_changed": False,
        "validator_changed": False,
        "compiler_changed": False,
        "adapter_semantics_changed": False,
        "registry_changed": False,
        "gpu_calls": 0,
        "network_calls": 0,
        "oracle_in_runner": False,
    }
    if freeze.get("constraints") != expected_constraints:
        raise SystemExit("overlay constraints mismatch")
    if freeze.get("contract") != contract_hashes(load_projection()):
        raise SystemExit("overlay contract mismatch")
    return []


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    check() if args.check else write()


if __name__ == "__main__":
    main()
