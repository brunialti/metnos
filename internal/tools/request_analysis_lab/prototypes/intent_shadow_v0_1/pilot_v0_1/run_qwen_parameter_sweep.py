#!/usr/bin/env python3
"""Small controlled sweep of Qwen inference parameters on five hard cases."""
from __future__ import annotations

import base64
from copy import deepcopy
from hashlib import sha256
import json

from .run_kiss_semantic_probe import (
    CORRECTIONS,
    HERE,
    PREVIOUS,
    extract,
    extract_approval,
    post,
    request_for,
    validate_cases,
)
from .run_pilot import atomic_exclusive, canonical


OUTPUT = HERE / "qwen_parameter_sweep_results.json"
CASE_IDS = ("bp-001", "bp-007", "bp-012", "bp-013", "bp-018")
PROFILES = {
    "standard": {},
    "greedy_no_cache": {"cache_prompt": False, "top_k": 1, "top_p": 1.0, "min_p": 0.0},
    "thinking_1024": {"cache_prompt": False, "thinking": True, "reasoning_budget": 1024},
    "creative_035": {"cache_prompt": False, "temperature": 0.35},
}


def apply_profile(request, profile):
    result = deepcopy(request)
    for key, value in profile.items():
        if key == "thinking":
            result.setdefault("chat_template_kwargs", {})["enable_thinking"] = value
        else:
            result[key] = value
    return result


def response_metadata(body):
    try:
        wrapper = json.loads(body)
        choice = wrapper["choices"][0]
        message = choice.get("message") or {}
        return {
            "finish_reason": choice.get("finish_reason"),
            "reasoning_chars": len(message.get("reasoning_content") or ""),
            "usage": wrapper.get("usage") or {},
            "model": wrapper.get("model"),
        }
    except Exception:
        return {}


def main() -> int:
    cases, registry = validate_cases()
    if OUTPUT.exists():
        raise RuntimeError("single-use output already exists")
    by_id = {case["proposal"].proposal_id: case for case in cases}
    corrections = json.loads(CORRECTIONS.read_text(encoding="utf-8"))["corrections"]
    gates = {row["case_id"]: row["gate"] for row in json.loads(PREVIOUS.read_text(encoding="utf-8"))["records"]}
    names = tuple(PROFILES)
    records = []
    for case_index, case_id in enumerate(CASE_IDS):
        source = by_id[case_id]
        case = dict(source)
        case["query"] = corrections.get(case_id, source["query"])
        gate = gates[case_id]
        order = names[case_index % len(names):] + names[:case_index % len(names)]
        for profile_name in order:
            request = apply_profile(request_for(case, registry, gate["approval_scope"]), PROFILES[profile_name])
            body, elapsed, transport = post(request)
            status, semantic, raw, error = ("transport_invalid", None, None, transport)
            if transport is None:
                extractor = extract_approval if gate["approval_scope"] != "none" else extract
                status, semantic, raw, error = extractor(body, registry)
            exact = status == "valid" and semantic == case["expected"]
            record = {
                "case_id": case_id, "profile": profile_name, "status": status,
                "error": error, "exact": exact, "semantic": semantic, "expected": case["expected"],
                "elapsed_ms": elapsed, "request_sha256": sha256(canonical(request)).hexdigest(),
                "raw_model_output_b64": None if raw is None else base64.b64encode(raw).decode("ascii"),
                "response": response_metadata(body),
            }
            records.append(record)
            print(json.dumps({"completed": len(records), "case": case_id,
                              "profile": profile_name, "exact": exact,
                              "valid": status == "valid"}), flush=True)
            if transport is not None:
                break
    summary = {}
    for name in names:
        selected = [row for row in records if row["profile"] == name]
        summary[name] = {
            "exact": sum(row["exact"] for row in selected),
            "valid": sum(row["status"] == "valid" for row in selected),
            "median_elapsed_ms": sorted(row["elapsed_ms"] for row in selected)[len(selected) // 2],
        }
    atomic_exclusive(OUTPUT, {
        "format": "metnos.intent-qwen-parameter-sweep/0.1", "call_count": len(records),
        "retry_count": 0, "case_ids": list(CASE_IDS), "profiles": PROFILES,
        "summary": summary, "records": records,
    })
    print(json.dumps(summary, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
