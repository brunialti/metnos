#!/usr/bin/env python3
"""Run the resumable C0 fixture without contacting Metnos."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from certification.coordinator import matrix_digest, read_jsonl, run_batch


HERE = Path(__file__).resolve().parent
E2E_ROOT = HERE.parent
REPO_ROOT = E2E_ROOT.parent.parent


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_digest(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(str(path.relative_to(REPO_ROOT)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def build_manifest(cases: list[dict], *, cycles: list[int]) -> dict:
    corpus = E2E_ROOT / "corpus" / "corpus.sqlite"
    suite_files = [
        path for path in E2E_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts
    ]
    surface_files = [
        REPO_ROOT / "runtime" / "http_routes_agent.py",
        REPO_ROOT / "runtime" / "http_routes_admin.py",
        REPO_ROOT / "runtime" / "http_routes_durable_workloads.py",
        REPO_ROOT / "runtime" / "ui_surfaces.py",
    ]
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    return {
        "schema_version": "metnos.certification-manifest/1",
        "certification_id": "rm0006-c0-synthetic-v1",
        "oracle_version": "rm0006-c0-oracle/1",
        "git_commit": commit,
        "suite_sha256": _tree_digest(suite_files),
        "corpus_sha256": _file_digest(corpus) if corpus.exists() else "0" * 64,
        "catalog_sha256": _file_digest(REPO_ROOT / "tests/benchmarks/catalog_snapshot.json"),
        "llm_config_sha256": _file_digest(REPO_ROOT / "runtime/llm_workloads.py"),
        "surface_sha256": _tree_digest(surface_files),
        "case_matrix_sha256": matrix_digest(cases),
        "platform": "synthetic-no-metnos",
        "fixture": "certification-c0-v1",
        "locales": ["it", "en"],
        "cycles": cycles,
        "created_at": "2026-08-22T00:00:00Z",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cycles", type=int, default=2)
    parser.add_argument("--max-new-cases", type=int)
    args = parser.parse_args()
    if args.cycles < 1:
        parser.error("--cycles must be at least 1")
    cases = read_jsonl(HERE / "fixtures" / "cases.jsonl")
    observations = read_jsonl(HERE / "fixtures" / "observations.jsonl")
    summary = run_batch(
        output_dir=args.output,
        manifest=build_manifest(cases, cycles=list(range(1, args.cycles + 1))),
        cases=cases,
        observations=observations,
        max_new_cases=args.max_new_cases,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if summary["objective_achieved"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
