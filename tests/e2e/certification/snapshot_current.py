#!/usr/bin/env python3
"""Capture the reproducible C0 inventory without importing or calling Metnos."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path


HERE = Path(__file__).resolve().parent
E2E_ROOT = HERE.parent
REPO_ROOT = E2E_ROOT.parent.parent


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_digest(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(str(path.relative_to(REPO_ROOT)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def snapshot() -> dict:
    corpus = E2E_ROOT / "corpus" / "corpus.sqlite"
    corpus_counts = {"available": corpus.exists(), "records": None, "deduplicated_roots": None}
    if corpus.exists():
        with sqlite3.connect(corpus) as connection:
            corpus_counts.update({
                "records": connection.execute("SELECT count(*) FROM queries").fetchone()[0],
                "deduplicated_roots": connection.execute(
                    "SELECT count(*) FROM queries WHERE dedup_master_id IS NULL"
                ).fetchone()[0],
            })
    scenario_files = sorted((E2E_ROOT / "scenarios").glob("test_*.py"))
    suite_files = sorted(
        path for path in E2E_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts
    )
    surfaces = [
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
        "schema_version": "metnos.certification-snapshot/1",
        "captured_on": "2026-08-22",
        "git_commit": commit,
        "suite": {
            "python_files": len(suite_files),
            "scenario_files": len(scenario_files),
            "sha256": _tree_digest(suite_files),
        },
        "corpus": {
            **corpus_counts,
            "sha256": _digest(corpus) if corpus.exists() else None,
        },
        "configuration": {
            "quality_targets_sha256": _digest(E2E_ROOT / "quality_targets.json"),
            "llm_workloads_sha256": _digest(REPO_ROOT / "runtime/llm_workloads.py"),
            "catalog_snapshot_sha256": _digest(REPO_ROOT / "tests/benchmarks/catalog_snapshot.json"),
        },
        "surfaces": {
            "files": [str(path.relative_to(REPO_ROOT)) for path in surfaces],
            "sha256": _tree_digest(surfaces),
        },
        "constraints": {
            "runtime_imported": False,
            "metnos_called": False,
            "private_requests_persisted": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rendered = json.dumps(snapshot(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
