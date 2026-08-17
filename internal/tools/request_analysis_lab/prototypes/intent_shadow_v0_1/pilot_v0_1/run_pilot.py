#!/usr/bin/env python3
"""Minimal, single-use 20x2 intent prompt pilot."""
from __future__ import annotations

import base64
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any
import urllib.error
import urllib.request

from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.candidate_v0_2.api import compile_ir
from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.candidate_v0_2.compiler import semantic_projection
from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.candidate_v0_2.registry_projection import load_projection
from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.candidate_v0_3.structured_client import build_request as build_baseline
from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.holdout_blueprint_v0_4 import build as blueprint_build
from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.holdout_blueprint_v0_4 import engine as blueprint
from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.prompt_challenger_v0_1.structured_client import build_request as build_challenger


HERE = Path(__file__).resolve().parent
CASES = HERE / "cases.json"
OUTPUT = HERE / "results.json"
ENDPOINT = "http://127.0.0.1:8080/v1/chat/completions"
BLUEPRINT_FREEZE = blueprint_build.HERE / "process.freeze.json"
CHALLENGER_FREEZE = HERE.parent / "prompt_challenger_v0_1" / "prompt_challenger_v0_1.freeze.json"
EXPECTED_BLUEPRINT_SHA = "aa4ccaea6f2de5b5919f0556a8fb28d9367ea542fb591624f2f970ceb490fddb"
EXPECTED_CHALLENGER_SHA = "91f7acb5b582f1ad064ed19e788207c8a0452e1a712d934a60f8e7e59463d966"


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def file_sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def atomic_exclusive(path: Path, value: Any) -> None:
    if path.exists():
        raise RuntimeError(f"single-use output already exists: {path}")
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2).encode("utf-8") + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def profiled(request: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(request)
    result.update({
        "model": "local", "temperature": 0, "seed": 42, "max_tokens": 4000,
        "stream": False, "cache_prompt": True,
        "chat_template_kwargs": {"enable_thinking": False},
    })
    return result


def validate_cases() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if file_sha(BLUEPRINT_FREEZE) != EXPECTED_BLUEPRINT_SHA:
        raise RuntimeError("blueprint freeze drift")
    if file_sha(CHALLENGER_FREEZE) != EXPECTED_CHALLENGER_SHA:
        raise RuntimeError("challenger freeze drift")
    value = json.loads(CASES.read_text(encoding="utf-8"))
    if set(value) != {"format", "blueprint_freeze_sha256", "challenger_freeze_sha256", "cases"}:
        raise RuntimeError("case file schema")
    if value["format"] != "metnos.intent-pilot-cases/0.1":
        raise RuntimeError("case format")
    if value["blueprint_freeze_sha256"] != EXPECTED_BLUEPRINT_SHA or value["challenger_freeze_sha256"] != EXPECTED_CHALLENGER_SHA:
        raise RuntimeError("case freeze binding")
    rows = value["cases"]
    if type(rows) is not list or len(rows) != 20:
        raise RuntimeError("exactly 20 cases required")
    registry = load_projection()
    glossary = blueprint.load_json(blueprint_build.HERE / "glossary.json")
    matrix = blueprint.load_json(blueprint_build.HERE / "pilot_matrix.json")
    proposals = []
    seen_queries = set()
    prepared = []
    for row in rows:
        if type(row) is not dict or set(row) != {"query", "proposal"} or type(row["query"]) is not str or not row["query"].strip():
            raise RuntimeError("case row schema")
        if row["query"] in seen_queries:
            raise RuntimeError("duplicate query")
        seen_queries.add(row["query"])
        proposal = blueprint.parse_proposal(row["proposal"])
        blueprint.validate_proposal(proposal, registry=registry, glossary=glossary,
                                    freeze_sha=EXPECTED_BLUEPRINT_SHA, matrix=matrix)
        expected = blueprint.derive_expected(proposal)
        proposals.append(proposal)
        prepared.append({"query": row["query"], "proposal": proposal, "expected": expected})
    blueprint.validate_fingerprint_set(tuple(proposals), scale="pilot")
    if [item["proposal"].proposal_id for item in prepared] != [f"bp-{index:03d}" for index in range(1, 21)]:
        raise RuntimeError("case order")
    return prepared, registry


def post(request_body: dict[str, Any]) -> tuple[bytes, int, str | None]:
    request = urllib.request.Request(ENDPOINT, data=canonical(request_body),
                                     headers={"Content-Type": "application/json"}, method="POST")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = response.read(1_048_577)
            elapsed = int((time.monotonic() - started) * 1000)
            if response.status != 200 or len(body) > 1_048_576:
                return body, elapsed, f"HTTP_{response.status}_OR_LIMIT"
            return body, elapsed, None
    except urllib.error.HTTPError as exc:
        return exc.read(1_048_577), int((time.monotonic() - started) * 1000), f"HTTP_{exc.code}"
    except Exception as exc:
        return b"", int((time.monotonic() - started) * 1000), type(exc).__name__


def extract(body: bytes, registry: dict[str, Any]) -> tuple[str, dict[str, Any] | None, bytes | None, str | None]:
    try:
        wrapper = json.loads(body)
        content = wrapper["choices"][0]["message"]["content"]
        if type(content) is not str:
            raise TypeError("content")
        raw = content.encode("utf-8", errors="strict")
        decoded = json.loads(raw)
    except Exception as exc:
        return "technical_invalid", None, None, type(exc).__name__
    compilation = compile_ir(decoded, registry)
    if not compilation.valid:
        return "document_invalid", None, raw, ";".join(item.code for item in compilation.issues)
    return "valid", semantic_projection(compilation), raw, None


def main() -> int:
    cases, registry = validate_cases()
    if OUTPUT.exists():
        raise RuntimeError("single-use output already exists")
    records = []
    for index, case in enumerate(cases):
        order = ("baseline", "challenger") if index % 2 == 0 else ("challenger", "baseline")
        for arm in order:
            builder = build_baseline if arm == "baseline" else build_challenger
            request_body = profiled(builder(case["query"], registry, case["proposal"].language_tag))
            body, elapsed, transport_error = post(request_body)
            status, semantic, raw, parse_error = ("transport_invalid", None, None, None)
            if transport_error is None:
                status, semantic, raw, parse_error = extract(body, registry)
            exact = status == "valid" and semantic == case["expected"]
            record = {
                "case_id": case["proposal"].proposal_id,
                "language_tag": case["proposal"].language_tag,
                "cell": case["proposal"].cell,
                "arm": arm,
                "request_sha256": sha256(canonical(request_body)).hexdigest(),
                "http_body_sha256": sha256(body).hexdigest(),
                "raw_model_output_b64": None if raw is None else base64.b64encode(raw).decode("ascii"),
                "status": status,
                "error": transport_error or parse_error,
                "semantic": semantic,
                "expected": case["expected"],
                "exact": exact,
                "elapsed_ms": elapsed,
            }
            records.append(record)
            print(json.dumps({"completed": len(records), "total": 40, "case": record["case_id"],
                              "arm": arm, "status": status, "exact": exact}, sort_keys=True), flush=True)
            if transport_error is not None:
                atomic_exclusive(OUTPUT, {"format": "metnos.intent-pilot-result/0.1", "state": "partial_transport_stop",
                                          "cases_sha256": file_sha(CASES), "records": records})
                return 2
    summary = {}
    for arm in ("baseline", "challenger"):
        selected = [item for item in records if item["arm"] == arm]
        summary[arm] = {
            "exact": sum(item["exact"] for item in selected),
            "valid": sum(item["status"] == "valid" for item in selected),
            "document_invalid": sum(item["status"] == "document_invalid" for item in selected),
            "technical_invalid": sum(item["status"] == "technical_invalid" for item in selected),
            "median_elapsed_ms": sorted(item["elapsed_ms"] for item in selected)[len(selected) // 2],
        }
    result = {"format": "metnos.intent-pilot-result/0.1", "state": "complete",
              "cases_sha256": file_sha(CASES), "blueprint_freeze_sha256": EXPECTED_BLUEPRINT_SHA,
              "challenger_freeze_sha256": EXPECTED_CHALLENGER_SHA, "request_count": 40,
              "retry_count": 0, "summary": summary, "records": records}
    atomic_exclusive(OUTPUT, result)
    print(json.dumps({"complete": True, "summary": summary}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
