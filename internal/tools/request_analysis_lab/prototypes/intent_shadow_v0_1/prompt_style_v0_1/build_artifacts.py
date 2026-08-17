"""Build/check immutable artifacts for the three-arm prompt-style experiment."""
from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path
import subprocess
from typing import Any

from ..candidate_v0_2.canonical import canonical_sha256, file_sha256, load_json_file, pretty_json_bytes
from ..candidate_v0_2.registry_projection import load_projection, validate_projection
from ..candidate_v0_3 import build_artifacts as current_build
from .authority_inventory import inventory_document
from .projection import PromptStyle, build_prompt, build_schema, contract_hashes


HERE = Path(__file__).resolve().parent
CURRENT_DIR = HERE.parent / "candidate_v0_3"
SCHEMA_FILE = HERE / "intent_ir_style_v0_1.schema.json"
INVENTORY_FILE = HERE / "authority_rule_inventory_v0_1.json"
METRICS_FILE = HERE / "prompt_style_metrics_v0_1.json"
FREEZE_FILE = HERE / "prompt_style_v0_1.freeze.json"
FREEZE_FORMAT = "metnos.intent-prompt-style-freeze/0.1"

PROMPT_FILES = {
    PromptStyle.S0_CURRENT: HERE / "prompt_s0_current.txt",
    PromptStyle.S1_METNOS_SHORT: HERE / "prompt_s1_metnos_short.txt",
    PromptStyle.S2_PROCEDURAL: HERE / "prompt_s2_procedural.txt",
}

CURRENT_CRITICAL_FILES = (
    "candidate_v0_3.freeze.json",
    "intent_ir_v0_3.schema.json",
    "intent_ir_v0_3.prompt.txt",
    "build_artifacts.py",
    "projection.py",
    "structured_client.py",
)


def _package_files() -> list[Path]:
    result: list[Path] = []
    for path in HERE.rglob("*"):
        if not path.is_file() or path == FREEZE_FILE or "__pycache__" in path.parts:
            continue
        if path.suffix in {".py", ".json", ".txt", ".md"}:
            result.append(path)
    return sorted(result)


def _freeze_payload(document: dict[str, Any]) -> str:
    payload = deepcopy(document)
    payload["integrity"].pop("freeze_payload_sha256", None)
    return canonical_sha256(payload)


def expected_artifacts() -> dict[Path, bytes]:
    registry = validate_projection(load_projection())
    prompts = {path: build_prompt(style, registry).encode("utf-8") for style, path in PROMPT_FILES.items()}
    if prompts[PROMPT_FILES[PromptStyle.S0_CURRENT]] != (CURRENT_DIR / "intent_ir_v0_3.prompt.txt").read_bytes():
        raise SystemExit("S0 prompt differs from frozen candidate v0.3")
    schema = pretty_json_bytes(build_schema(registry))
    if schema != (CURRENT_DIR / "intent_ir_v0_3.schema.json").read_bytes():
        raise SystemExit("style schema differs from frozen candidate v0.3")
    contract = contract_hashes(registry)
    metrics = {
        "metrics_format": "metnos.intent-prompt-style-metrics/0.1",
        "measurement": "static UTF-8 bytes, lines, and whitespace-token proxy; live tokenizer usage is measured separately",
        "styles": contract["prompt_metrics"],
        "delta_from_s0": {
            style.value: {
                key: contract["prompt_metrics"][style.value][key] - contract["prompt_metrics"][PromptStyle.S0_CURRENT.value][key]
                for key in ("utf8_bytes", "lines", "whitespace_tokens")
            }
            for style in (PromptStyle.S1_METNOS_SHORT, PromptStyle.S2_PROCEDURAL)
        },
    }
    return {
        **prompts,
        SCHEMA_FILE: schema,
        INVENTORY_FILE: pretty_json_bytes(inventory_document(registry)),
        METRICS_FILE: pretty_json_bytes(metrics),
    }


def build_freeze() -> dict[str, Any]:
    registry = validate_projection(load_projection())
    current_freeze = load_json_file(CURRENT_DIR / "candidate_v0_3.freeze.json")
    document = {
        "freeze_format": FREEZE_FORMAT,
        "status": "offline_style_experiment_not_runtime",
        "current_candidate": {
            "freeze_sha256": file_sha256(CURRENT_DIR / "candidate_v0_3.freeze.json"),
            "contract": current_freeze["contract"],
            "critical_files": {
                name: file_sha256(CURRENT_DIR / name) for name in CURRENT_CRITICAL_FILES
            },
        },
        "contract": contract_hashes(registry),
        "files": {str(path.relative_to(HERE)): file_sha256(path) for path in _package_files()},
        "constraints": {
            "arms": [style.value for style in PromptStyle],
            "logical_change": ["prompt_style_only"],
            "new_semantic_rules": 0,
            "registry_changed": False,
            "schema_changed": False,
            "validator_changed": False,
            "compiler_changed": False,
            "adapter_semantics_changed": False,
            "model_or_limits_changed": False,
            "gpu_calls": 0,
            "network_calls": 0,
            "oracle_in_live_path": False,
        },
        "integrity": {"algorithm": "sha256", "freeze_payload_sha256": ""},
    }
    document["integrity"]["freeze_payload_sha256"] = _freeze_payload(document)
    return document


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
    errors = current_build.check()
    if type(errors) is not list or errors != []:
        raise SystemExit("current candidate verification failed")
    for path, content in expected_artifacts().items():
        _apply(path, content)
    _apply(FREEZE_FILE, pretty_json_bytes(build_freeze()))


def check() -> list[str]:
    errors = current_build.check()
    if type(errors) is not list or errors != []:
        raise SystemExit("current candidate verification failed")
    for path, content in expected_artifacts().items():
        if not path.is_file() or path.read_bytes() != content:
            raise SystemExit(f"stale style artifact:{path.name}")
    freeze = load_json_file(FREEZE_FILE)
    expected = build_freeze()
    if freeze != expected:
        raise SystemExit("style freeze drift")
    if freeze.get("integrity", {}).get("freeze_payload_sha256") != _freeze_payload(freeze):
        raise SystemExit("style freeze payload mismatch")
    return []


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    check() if args.check else write()


if __name__ == "__main__":
    main()
