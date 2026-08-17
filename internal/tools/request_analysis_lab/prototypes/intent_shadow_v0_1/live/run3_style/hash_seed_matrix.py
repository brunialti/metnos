#!/usr/bin/env python3
"""Gold-free fresh-process hash-seed matrix for both frozen RUN2 anchors."""
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[6]
RUN1 = HERE.parent / "run1"
RUN2 = HERE.parent / "run2"
REPORT_PATH = HERE / "run2_anchor_hash_seed_matrix_v0_1.json"
FORMAT = "metnos.intent-prompt-style-hash-seed-matrix/0.1"
SEED_START = 0
SEED_END = 255
WORKERS = 16
SENTINEL = ("metnos", "intent", "run3_style")


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _payload_sha256(value: dict[str, Any]) -> str:
    payload = deepcopy(value)
    payload.pop("payload_sha256", None)
    return sha256(_canonical(payload)).hexdigest()


def _diffs(left: Any, right: Any, path: str = "") -> list[tuple[str, type, type]]:
    if type(left) is not type(right):
        return [(path or "/", type(left), type(right))]
    if type(left) is dict:
        differences: list[tuple[str, type, type]] = []
        for key in sorted(set(left) | set(right)):
            child = path + "/" + str(key).replace("~", "~0").replace("/", "~1")
            if key not in left or key not in right:
                differences.append((child, type(left.get(key)), type(right.get(key))))
            else:
                differences.extend(_diffs(left[key], right[key], child))
        return differences
    if type(left) is list:
        if len(left) != len(right):
            return [(path + "/length", int, int)]
        differences = []
        for index, (left_item, right_item) in enumerate(zip(left, right, strict=True)):
            differences.extend(_diffs(left_item, right_item, f"{path}/{index}"))
        return differences
    return [] if left == right else [(path or "/", type(left), type(right))]


def _child(seed: int, *, include_requests: bool) -> dict[str, Any]:
    if os.environ.get("PYTHONHASHSEED") != str(seed):
        raise RuntimeError("PYTHONHASHSEED must be fixed before interpreter start")
    logging.disable(logging.CRITICAL)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.live.run2.arm_a import (
        build_request as build_a_request,
        extract_response as extract_a_response,
    )
    from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.live.run2.arm_b import (
        build_request as build_b_request,
        extract_response as extract_b_response,
    )

    batch = json.loads((RUN2 / "sealed_batch_run2.json").read_text(encoding="utf-8"))
    panel = json.loads((RUN1 / "query_panel_run1.json").read_text(encoding="utf-8"))
    cases = {case["sample_index"]: case for case in panel["cases"]}
    public = {(row["sample_index"], row["arm"]): row for row in batch["records"]}
    counts = {
        "A_full_extraction_exact": 0, "B_full_extraction_exact": 0,
        "A_semantic_status_exact": 0, "B_semantic_status_exact": 0,
    }
    diff_paths = {"A": set(), "B": set()}
    boolean_flips = {"A": 0, "B": 0}
    for saved in batch["records"]:
        arm = saved["arm"]
        if arm not in {"A", "B"}:
            continue
        case = cases[saved["sample_index"]]
        content = base64.b64decode(saved["model_content_b64"], validate=True)
        replay = (
            extract_a_response(content, query=case["query"], language=case["language"])
            if arm == "A" else extract_b_response(content)
        )
        differences = _diffs(saved["extraction"], replay)
        if not differences:
            counts[f"{arm}_full_extraction_exact"] += 1
        else:
            diff_paths[arm].update(path for path, _left, _right in differences)
            if (
                len(differences) == 1
                and differences[0][0] == "/adapter_metadata/implicit_actions_ignored"
                and differences[0][1] is bool and differences[0][2] is bool
            ):
                boolean_flips[arm] += 1
        semantic_status = {
            "status": replay.get("status"),
            "semantic_document": replay.get("semantic_document"),
        }
        saved_semantic_status = {
            "status": saved["extraction"].get("status"),
            "semantic_document": saved["extraction"].get("semantic_document"),
        }
        if _canonical(semantic_status) == _canonical(saved_semantic_status):
            counts[f"{arm}_semantic_status_exact"] += 1
    result: dict[str, Any] = {
        "seed": seed,
        **counts,
        "A_mismatch_diff_paths": sorted(diff_paths["A"]),
        "B_mismatch_diff_paths": sorted(diff_paths["B"]),
        "A_boolean_flip_count": boolean_flips["A"],
        "B_boolean_flip_count": boolean_flips["B"],
        "hash_sentinel": hash(SENTINEL),
    }
    if include_requests:
        request_counts = {"A_request_exact": 0, "B_request_exact": 0}
        for case in panel["cases"]:
            for arm, builder in (("A", build_a_request), ("B", build_b_request)):
                request = builder(case["query"], case["language"])
                if sha256(_canonical(request)).hexdigest() == public[(case["sample_index"], arm)]["request_sha256"]:
                    request_counts[f"{arm}_request_exact"] += 1
        result.update(request_counts)
    return result


def _fresh(seed: int, *, confirm: bool = False) -> dict[str, Any]:
    environment = dict(os.environ)
    environment["PYTHONHASHSEED"] = str(seed)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    command = [sys.executable, str(Path(__file__).resolve()), "--child-seed", str(seed)]
    if confirm:
        command.append("--confirm")
    completed = subprocess.run(
        command, cwd=ROOT, env=environment, capture_output=True,
        text=True, check=True, timeout=180,
    )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise RuntimeError(f"unexpected child output for seed {seed}")
    return json.loads(lines[0])


def build_report() -> dict[str, Any]:
    seeds = list(range(SEED_START, SEED_END + 1))
    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        rows = list(executor.map(_fresh, seeds))
    rows.sort(key=lambda row: row["seed"])
    allowed_path = "/adapter_metadata/implicit_actions_ignored"
    for row in rows:
        if row["A_semantic_status_exact"] != 158 or row["B_semantic_status_exact"] != 158:
            raise RuntimeError("semantic/status drift across seed matrix")
        for arm in ("A", "B"):
            mismatches = 158 - row[f"{arm}_full_extraction_exact"]
            paths = row[f"{arm}_mismatch_diff_paths"]
            flips = row[f"{arm}_boolean_flip_count"]
            if (
                (mismatches == 0 and (paths != [] or flips != 0))
                or (mismatches > 0 and (paths != [allowed_path] or flips != mismatches))
            ):
                raise RuntimeError(f"non-diagnostic extraction drift at seed {row['seed']} arm {arm}")
    exact_seeds = [
        row["seed"] for row in rows
        if row["A_full_extraction_exact"] == 158
        and row["B_full_extraction_exact"] == 158
    ]
    if not exact_seeds:
        raise RuntimeError("no whole-anchor exact seed in predeclared range")
    selected = min(exact_seeds)
    confirmations = [_fresh(selected, confirm=True), _fresh(selected, confirm=True)]
    selected_row = rows[selected - SEED_START]
    required_confirmation = {
        "A_full_extraction_exact": 158, "B_full_extraction_exact": 158,
        "A_semantic_status_exact": 158, "B_semantic_status_exact": 158,
        "A_request_exact": 158, "B_request_exact": 158,
        "A_mismatch_diff_paths": [], "B_mismatch_diff_paths": [],
        "A_boolean_flip_count": 0, "B_boolean_flip_count": 0,
    }
    for confirmation in confirmations:
        if confirmation.get("seed") != selected:
            raise RuntimeError("fresh confirmation seed mismatch")
        if confirmation.get("hash_sentinel") != selected_row["hash_sentinel"]:
            raise RuntimeError("fresh confirmation hash sentinel mismatch")
        if any(confirmation.get(key) != value for key, value in required_confirmation.items()):
            raise RuntimeError("fresh confirmation is not whole-anchor/request exact")
    if _canonical(confirmations[0]) != _canonical(confirmations[1]):
        raise RuntimeError("fresh confirmations disagree")
    report: dict[str, Any] = {
        "format": FORMAT,
        "status": "whole_anchor_exact_seed_found",
        "seed_range": {"start": SEED_START, "end": SEED_END, "inclusive": True},
        "selection_scope": "all_158_RUN2_A_and_all_158_RUN2_B_full_extractions",
        "selection_rule": "smallest_seed_with_both_full_extraction_exact_counts_158",
        "semantic_status_invariance_required": True,
        "only_permitted_nonexact_diff_path": allowed_path,
        "only_permitted_nonexact_diff_types": ["bool", "bool"],
        "selected_seed": selected,
        "exact_seed_count": len(exact_seeds),
        "rows": rows,
        "fresh_process_confirmations": confirmations,
        "network_touched": False,
        "gpu_touched": False,
        "gold_or_oracle_opened": False,
        "payload_sha256": "",
    }
    report["payload_sha256"] = _payload_sha256(report)
    return report


def _apply(report: dict[str, Any]) -> None:
    rendered = json.dumps(report, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + "\n"
    if REPORT_PATH.is_file() and REPORT_PATH.read_text(encoding="utf-8") == rendered:
        return
    if REPORT_PATH.exists():
        subprocess.run(
            ["apply_patch"], input=f"*** Begin Patch\n*** Delete File: {REPORT_PATH}\n*** End Patch\n",
            text=True, check=True,
        )
    additions = "\n".join("+" + line for line in rendered.rstrip("\n").split("\n"))
    subprocess.run(
        ["apply_patch"],
        input=f"*** Begin Patch\n*** Add File: {REPORT_PATH}\n{additions}\n*** End Patch\n",
        text=True, check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--child-seed", type=int)
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.child_seed is not None:
        print(json.dumps(_child(args.child_seed, include_requests=args.confirm), sort_keys=True))
        return 0
    if args.apply:
        _apply(build_report())
        return 0
    parser.error("choose --apply or --child-seed")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
