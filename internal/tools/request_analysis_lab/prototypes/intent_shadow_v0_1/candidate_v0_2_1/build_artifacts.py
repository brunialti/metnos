"""Build/check the isolated BCP47 prerequisite freeze."""
from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path
import subprocess
from typing import Any

from ..candidate_v0_2 import build_artifacts as base_build
from ..candidate_v0_2.canonical import canonical_sha256, file_sha256, load_json_file, pretty_json_bytes


HERE = Path(__file__).resolve().parent
BASE_DIR = HERE.parent / "candidate_v0_2"
FREEZE_FILE = HERE / "candidate_v0_2_1.freeze.json"
FREEZE_FORMAT = "metnos.intent-ir-language-prerequisite-freeze/0.2.1"
BASE_FILES = (
    "candidate_v0_2.freeze.json",
    "language_tag.py",
    "projection.py",
    "registry_projection.py",
    "structured_client.py",
)


def _files() -> list[Path]:
    return sorted(
        path for path in HERE.rglob("*")
        if path.is_file() and path != FREEZE_FILE and "__pycache__" not in path.parts
        and path.suffix in {".py", ".md", ".json", ".txt"}
    )


def _payload(value: dict[str, Any]) -> str:
    copy = deepcopy(value)
    copy["integrity"].pop("freeze_payload_sha256", None)
    return canonical_sha256(copy)


def build_freeze() -> dict[str, Any]:
    value = {
        "freeze_format": FREEZE_FORMAT,
        "status": "offline_language_prerequisite_not_runtime",
        "base_candidate": {
            "freeze_sha256": file_sha256(BASE_DIR / "candidate_v0_2.freeze.json"),
            "files": {name: file_sha256(BASE_DIR / name) for name in BASE_FILES},
        },
        "files": {str(path.relative_to(HERE)): file_sha256(path) for path in _files()},
        "constraints": {
            "logical_change": ["complete_grandfathered_bcp47_normalization"],
            "grandfathered_record_count": 26,
            "prompt_changed": False,
            "schema_changed": False,
            "compiler_changed": False,
            "validator_changed": False,
            "registry_changed": False,
            "gpu_calls": 0,
            "network_calls": 0,
        },
        "integrity": {"algorithm": "sha256", "freeze_payload_sha256": ""},
    }
    value["integrity"]["freeze_payload_sha256"] = _payload(value)
    return value


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
    errors = base_build.check()
    if type(errors) is not list or errors != []:
        raise SystemExit("base candidate verification failed")
    _apply(FREEZE_FILE, pretty_json_bytes(build_freeze()))


def check() -> list[str]:
    errors = base_build.check()
    if type(errors) is not list or errors != []:
        raise SystemExit("base candidate verification failed")
    freeze = load_json_file(FREEZE_FILE)
    expected = build_freeze()
    if freeze != expected:
        raise SystemExit("language prerequisite freeze mismatch")
    return []


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    check() if args.check else write()


if __name__ == "__main__":
    main()
