"""Validate six activity artifacts and optionally build the pre-fix aggregate."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
_A_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_A_ROOT))
import certification_v1 as _certification

if Path(_certification.__file__).resolve() != _A_ROOT / "certification_v1.py":
    raise RuntimeError("certification_v1 did not resolve to the A-only package")

sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "runtime"))
from certification_v1 import (  # noqa: E402 - provenance precedes product paths.
    ACTIVITIES,
    CertificationError,
    INVENTORY_PATH,
    MANIFEST_PATH,
    SUITE_ID,
    canonical_json_bytes,
    digest_file,
    validate_final_evidence,
    validate_manifest,
    validate_snapshot_activity_evidence,
    validate_snapshot_aggregate,
    write_canonical_json,
)


def _load_activity(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CertificationError(f"cannot load activity evidence: {path}") from exc
    if not isinstance(value, dict) or raw != canonical_json_bytes(value):
        raise CertificationError(f"activity evidence is not canonical: {path}")
    return value


def aggregate(mode: str, evidence_dir: Path, output: Path | None) -> None:
    manifest = validate_manifest()
    documents = {
        activity: _load_activity(
            evidence_dir / f"rm0008-2a-evidence-{activity}.json"
        )
        for activity in ACTIVITIES
    }
    for activity, document in documents.items():
        if mode == "final":
            validate_final_evidence(document, manifest, activity)
        else:
            validate_snapshot_activity_evidence(document, manifest, activity)
    common_fields = ("git_sha", "manifest_sha256", "production_inventory_sha256", "suite_id")
    for field in common_fields:
        values = {document[field] for document in documents.values()}
        if len(values) != 1:
            raise CertificationError(f"activity evidence disagrees on {field}")
    common_sha = documents[ACTIVITIES[0]]["git_sha"]
    github_sha = os.environ.get("GITHUB_SHA")
    if github_sha and common_sha != github_sha:
        raise CertificationError("activity evidence SHA differs from GITHUB_SHA")
    if documents[ACTIVITIES[0]]["manifest_sha256"] != digest_file(MANIFEST_PATH):
        raise CertificationError("activity evidence manifest digest is stale")
    if documents[ACTIVITIES[0]]["production_inventory_sha256"] != digest_file(
        INVENTORY_PATH
    ):
        raise CertificationError("activity evidence production inventory digest is stale")
    if mode == "final":
        validate_snapshot_aggregate(manifest=manifest)
        if output is not None:
            raise CertificationError("final summary does not emit an aggregate file")
        return
    if output is None:
        raise CertificationError("snapshot aggregation requires --output")
    result_by_cell: dict[tuple[str, str], dict[str, Any]] = {}
    for activity, document in documents.items():
        for result in document["results"]:
            key = (activity, result["node_id"])
            if key in result_by_cell:
                raise CertificationError(f"duplicate activity result: {key!r}")
            result_by_cell[key] = result
    aggregate_results = []
    for cell in manifest["cells"]:
        result = result_by_cell.get((cell["activity"], cell["node_id"]))
        if result is None:
            raise CertificationError("snapshot aggregate is missing a manifest cell")
        aggregate_results.append(
            {
                "activity": cell["activity"],
                "node_id": cell["node_id"],
                "declared_disposition": result["declared_disposition"],
                "observed_outcome": result["observed_outcome"],
            }
        )
    aggregate_document = {
        "schema_version": 1,
        "suite_id": SUITE_ID,
        "source_git_sha": common_sha,
        "manifest_sha256": documents[ACTIVITIES[0]]["manifest_sha256"],
        "production_inventory_sha256": documents[ACTIVITIES[0]][
            "production_inventory_sha256"
        ],
        "activities": sorted(
            (
                {
                    "activity": activity,
                    "runner_image": documents[activity]["runner_image"],
                }
                for activity in ACTIVITIES
            ),
            key=lambda item: item["activity"].encode("utf-8"),
        ),
        "results": aggregate_results,
    }
    write_canonical_json(output, aggregate_document)


def main(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("final", "snapshot"), required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    options = parser.parse_args(arguments)
    try:
        aggregate(options.mode, options.evidence_dir, options.output)
    except CertificationError as exc:
        print(f"RM-0008 2A evidence summary failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
