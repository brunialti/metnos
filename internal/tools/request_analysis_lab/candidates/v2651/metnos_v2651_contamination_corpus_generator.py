#!/usr/bin/env python3
"""Generate the durable, query-only 109+34+70 contamination corpus."""
from __future__ import annotations

import ast
import hashlib
import json
import types
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[4]
SOURCES = {
    "mono_benchmark": REPOSITORY / "tests/benchmarks/intent_accuracy_bench.py",
    "compound_benchmark": REPOSITORY / "tests/benchmarks/intent_compound_bench.py",
    "question_controls": REPOSITORY / "internal/tools/request_analysis_lab/question_focus_controls_v1.json",
    "adversarial_source": REPOSITORY / "internal/tools/request_analysis_lab/request_analysis_adversarial_v1.py",
}
EXPECTED = {
    "mono_benchmark": "412eaa6620bb2f29486510196e37267f92c1b817d13b469734bd045693140a51",
    "compound_benchmark": "85ef748c5384e72bfeacf018b9e7fdb3f6c74fcc5d64abe518303d4e96657cd0",
    "question_controls": "22dac65689f720e07022ac204ba0fd25feebdf5e8db98a9d89f5bf00c120dcbf",
    "adversarial_source": "e06c45789f748c7275a47c8d63086a1b97a662fabe6f9dd57001ae0bda4977ab",
}
EXPECTED_ADVERSARIAL_LOGICAL_SHA256 = (
    "620803a44fc63e88d4b613963ca55e7f9a82a4c87f2f6c912b1e65904ea472f8"
)
LIST_NAMES = {
    "mono_benchmark": ("GOLD", "EDGE_GOLD"),
    "compound_benchmark": ("COMPOUND_GOLD", "COMPOUND_XL_GOLD"),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def verify_sources() -> None:
    for name, path in SOURCES.items():
        if not path.is_file() or sha256(path) != EXPECTED[name]:
            raise RuntimeError(f"source hash mismatch before parse/import: {name}")


def literal_lists(path: Path, names: tuple[str, ...]) -> dict[str, list[Any]]:
    tree = ast.parse(path.read_text())
    values: dict[str, list[Any]] = {}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name) and target.id in names:
                value = ast.literal_eval(node.value)
                if not isinstance(value, list):
                    raise RuntimeError(f"{target.id} is not a literal list")
                values[target.id] = value
    if set(values) != set(names):
        raise RuntimeError(f"missing benchmark lists in {path.name}")
    return values


def load_verified_adversarial() -> Any:
    path = SOURCES["adversarial_source"]
    source = path.read_bytes()
    if hashlib.sha256(source).hexdigest() != EXPECTED["adversarial_source"]:
        raise RuntimeError("adversarial source changed before execution")
    module = types.ModuleType("metnos_v2651_adversarial_source")
    module.__file__ = str(path)
    exec(compile(source, str(path), "exec"), module.__dict__)
    if module.validate_fixture() != []:
        raise RuntimeError("adversarial fixture validation failed")
    if module.fixture_digest() != EXPECTED_ADVERSARIAL_LOGICAL_SHA256:
        raise RuntimeError("adversarial logical digest mismatch")
    return module


def records(dataset: str, rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    result = []
    for index, row in enumerate(rows, 1):
        source_id = row.get("id", index)
        query = row["query"]
        result.append({
            "opaque_case_id": sha_text(f"{dataset}\0{source_id}"),
            "query_sha256_utf8": sha_text(query),
            "query": query,
        })
    return result


def sequence_sha256(rows: list[dict[str, str]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(row["opaque_case_id"].encode())
        digest.update(b"\0")
        digest.update(row["query"].encode())
        digest.update(b"\0")
    return digest.hexdigest()


def build() -> dict[str, Any]:
    verify_sources()
    mono = literal_lists(SOURCES["mono_benchmark"], LIST_NAMES["mono_benchmark"])
    compound = literal_lists(
        SOURCES["compound_benchmark"], LIST_NAMES["compound_benchmark"],
    )
    # Historical glob order was compound shards first, then mono shards.
    full_rows = [
        {"query": query}
        for name in ("COMPOUND_GOLD", "COMPOUND_XL_GOLD")
        for query, _expected in compound[name]
    ] + [
        {"query": query}
        for name in ("GOLD", "EDGE_GOLD")
        for query, _expected in mono[name]
    ]
    controls = json.loads(SOURCES["question_controls"].read_text())["cases"]
    adversarial = load_verified_adversarial()
    datasets = {
        "full109": records("full109", full_rows),
        "question-controls34": records("question-controls34", controls),
        "adversarial70": records("adversarial70", adversarial.CASES),
    }
    expected_counts = {
        "full109": 109, "question-controls34": 34, "adversarial70": 70,
    }
    actual_counts = {name: len(rows) for name, rows in datasets.items()}
    if actual_counts != expected_counts:
        raise RuntimeError(f"unexpected corpus counts: {actual_counts}")
    return {
        "version": "metnos.v26.5.1-contamination-query-corpus/0.1",
        "purpose": "audit_only_never_prompt_runtime_or_projector_input",
        "contains_expected_or_gold": False,
        "raw_query_count": sum(actual_counts.values()),
        "source_hashes": EXPECTED,
        "adversarial_logical_sha256": EXPECTED_ADVERSARIAL_LOGICAL_SHA256,
        "dataset_manifest": {
            name: {
                "records": len(rows),
                "query_sequence_sha256": sequence_sha256(rows),
            }
            for name, rows in datasets.items()
        },
        "datasets": datasets,
    }


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=2, sort_keys=True))
