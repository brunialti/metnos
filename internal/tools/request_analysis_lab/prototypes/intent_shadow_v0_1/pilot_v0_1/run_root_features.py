#!/usr/bin/env python3
"""Root probe using orthogonal features and deterministic precedence."""
from __future__ import annotations

from hashlib import sha256
import json

from .run_pilot import HERE, atomic_exclusive, canonical, post, profiled, validate_cases


OUTPUT = HERE / "root_features_v3_results.json"
CASE_IDS = ("bp-001", "bp-002", "bp-004", "bp-006", "bp-007", "bp-020")


def expected_decision(expected):
    if expected["kind"] == "operation_graph":
        return "operation_graph"
    if expected["kind"] == "system_control":
        return "system_control:" + expected["control"]
    return "unrepresentable:" + expected["reason"]


def decide(features):
    if features["conditional_branch"]:
        return "unrepresentable:unsupported_dependency"
    control = features["system_control"]
    if control != "none" and features["ordinary_action"]:
        return "unrepresentable:mixed_root_kinds"
    if control != "none":
        return "system_control:" + control
    if features["registry_coverage"] == "missing":
        return "unrepresentable:outside_registry"
    if features["fallback_reason"] != "none":
        return "unrepresentable:" + features["fallback_reason"]
    if features["ordinary_action"]:
        return "operation_graph"
    return "unrepresentable:no_actionable_intent"


def scope_lines(entries):
    lines = []
    for name, metadata in sorted(entries.items()):
        scopes = []
        seen = set()
        for item in metadata.get("descriptions", []):
            languages = item.get("languages", {})
            for tag, value in sorted(languages.items()):
                if tag in seen or type(value) is not dict:
                    continue
                scope = value.get("scope")
                if type(scope) is str and scope:
                    scopes.append(f"[{tag}] {scope}")
                    seen.add(tag)
        lines.append(f"- {name}" + (" | " + " | ".join(scopes) if scopes else ""))
    return lines


def build_request(case, registry):
    controls = ["none", *sorted(registry["system_controls"])]
    fallback = ["none", "ambiguous_intent", "missing_required_information", "no_actionable_intent"]
    prompt = "\n".join((
        f"INPUT_LANGUAGE_TAG: {case['proposal'].language_tag}",
        "Analyse the complete request. Return five independent features.",
        "conditional_branch=true only when alternative action paths are requested (IF X DO A ELSE B); approval before action is a barrier, not a branch.",
        "system_control names an explicit system control; otherwise none.",
        "ordinary_action=true when any ordinary action is requested, including inside a branch.",
        "registry_coverage=missing only when a requested action itself lacks an exact OPERATIONS or BARRIERS entry; objects, filters, names, dates, locations and other arguments are not capabilities.",
        "fallback_reason is none unless the request is ambiguous, lacks information needed to choose a typed intent, or has no action.",
        "SYSTEM CONTROLS:", *scope_lines(registry["system_controls"]),
        "BARRIERS:", *scope_lines(registry["barriers"]),
        "OPERATIONS:", *scope_lines(registry["operations"]),
    )) + "\n"
    schema = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "conditional_branch": {"type": "boolean"},
            "system_control": {"type": "string", "enum": controls},
            "ordinary_action": {"type": "boolean"},
            "registry_coverage": {"type": "string", "enum": ["complete", "missing"]},
            "fallback_reason": {"type": "string", "enum": fallback},
        },
        "required": ["conditional_branch", "system_control", "ordinary_action",
                     "registry_coverage", "fallback_reason"],
    }
    request = profiled({
        "messages": [{"role": "system", "content": prompt},
                     {"role": "user", "content": case["query"]}],
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "metnos_root_features_probe", "strict": True, "schema": schema}},
        "temperature": 0,
    })
    request["max_tokens"] = 200
    return request


def main() -> int:
    cases, registry = validate_cases()
    if OUTPUT.exists():
        raise RuntimeError("single-use output already exists")
    records = []
    selected = [case for case in cases if case["proposal"].proposal_id in CASE_IDS]
    if [case["proposal"].proposal_id for case in selected] != list(CASE_IDS):
        raise RuntimeError("probe cases")
    for case in selected:
        request = build_request(case, registry)
        body, elapsed, transport_error = post(request)
        features = None
        error = transport_error
        if error is None:
            try:
                wrapper = json.loads(body)
                content = wrapper["choices"][0]["message"]["content"]
                features = json.loads(content)
                decision = decide(features)
            except Exception as exc:
                decision, error = None, type(exc).__name__
        else:
            decision = None
        expected = expected_decision(case["expected"])
        record = {"case_id": case["proposal"].proposal_id, "features": features,
                  "decision": decision, "expected": expected, "exact": decision == expected,
                  "root_exact": decision is not None and decision.split(":", 1)[0] == expected.split(":", 1)[0],
                  "error": error, "elapsed_ms": elapsed,
                  "request_sha256": sha256(canonical(request)).hexdigest()}
        records.append(record)
        print(json.dumps({"completed": len(records), "case": record["case_id"],
                          "decision": decision, "exact": record["exact"]}, sort_keys=True), flush=True)
        if error is not None:
            break
    result = {"format": "metnos.root-features-probe/0.1", "count": len(records),
              "exact": sum(item["exact"] for item in records),
              "root_exact": sum(item["root_exact"] for item in records),
              "records": records}
    atomic_exclusive(OUTPUT, result)
    print(json.dumps({key: result[key] for key in ("count", "exact", "root_exact")}, sort_keys=True), flush=True)
    return 0 if len(records) == len(selected) else 2


if __name__ == "__main__":
    raise SystemExit(main())
