#!/usr/bin/env python3
"""Query-only offline runner.

There is deliberately no HTTP/model/GPU mode in candidate 0.1.  The runner
supports deterministic dry-run request construction and an injected fake
transport for tests.  It never imports the oracle, Phase-1 overlay, evaluator
or query-suite builder.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Callable

from intent_shadow_extract import envelope_json, extract_raw_json
from intent_shadow_io import canonical_json_bytes, file_sha256, strict_json_file
from intent_shadow_projection import build_prompt, build_schema, compile_model_contract
from intent_shadow_registry import load_frozen_registry


RUNNER_VERSION = "metnos.intent-shadow-offline-runner/0.1"
SUITE_FORMAT = "metnos.intent-shadow-query-suite/0.1"
LEGACY_FORMAT = "metnos.intent-shadow-legacy-panel/0.1"

HERE = Path(__file__).resolve().parent
REGISTRY_PATH = HERE.parent / "intent_shadow_registry_v0_1.json"
QUERY_SUITE_PATH = HERE / "intent_shadow_query_suite_v0_1.json"
LEGACY_PANEL_PATH = HERE / "legacy_panel_v0_1.json"


def _query_hash(query: str) -> str:
    return sha256(query.encode("utf-8")).hexdigest()


def _validate_query_case(case: Any, path: str, required_extra: str) -> None:
    required = {"ordinal", "opaque_case_id", "query", "query_sha256", required_extra}
    if type(case) is not dict or set(case) != required:
        raise RuntimeError(f"{path} is not a closed query-only case")
    if (
        type(case["ordinal"]) is not int
        or type(case[required_extra]) is not int
        or type(case["opaque_case_id"]) is not str
        or type(case["query"]) is not str
        or type(case["query_sha256"]) is not str
        or case["query_sha256"] != _query_hash(case["query"])
    ):
        raise RuntimeError(f"{path} query identity mismatch")


def load_query_suite(path: Path = QUERY_SUITE_PATH) -> dict[str, Any]:
    suite = strict_json_file(path)
    if (
        type(suite) is not dict
        or set(suite) != {
            "suite_format", "contract_version", "created_date", "status",
            "gold_fields_present", "sample_sha256", "registry_payload_sha256",
            "source_file_sha256", "counts", "panels", "suite_payload_sha256",
        }
        or suite["suite_format"] != SUITE_FORMAT
        or suite["gold_fields_present"] is not False
        or suite["counts"] != {"canonical_120": 120, "typed_controls_4": 4, "total": 124}
        or type(suite["panels"]) is not dict
        or set(suite["panels"]) != {"canonical_120", "typed_controls_4"}
    ):
        raise RuntimeError("query suite envelope mismatch")
    canonical = suite["panels"]["canonical_120"]
    typed = suite["panels"]["typed_controls_4"]
    if type(canonical) is not list or len(canonical) != 120 or type(typed) is not list or len(typed) != 4:
        raise RuntimeError("query suite panel counts mismatch")
    for index, case in enumerate(canonical):
        _validate_query_case(case, f"canonical[{index}]", "sample_index")
        if case["ordinal"] != index + 1 or case["sample_index"] != index:
            raise RuntimeError("canonical query order mismatch")
    for index, case in enumerate(typed):
        _validate_query_case(case, f"typed[{index}]", "authorized_control_ordinal")
        if case["ordinal"] != index + 1 or case["authorized_control_ordinal"] != index + 35:
            raise RuntimeError("typed-control query order mismatch")
    all_cases = canonical + typed
    if len({case["opaque_case_id"] for case in all_cases}) != 124:
        raise RuntimeError("query suite IDs not unique")
    if len({case["query_sha256"] for case in all_cases}) != 124:
        raise RuntimeError("query suite hashes not unique")
    observed = dict(suite)
    observed.pop("suite_payload_sha256")
    if sha256(canonical_json_bytes(observed)).hexdigest() != suite["suite_payload_sha256"]:
        raise RuntimeError("query suite payload hash mismatch")
    return suite


def load_legacy_panel(path: Path = LEGACY_PANEL_PATH) -> dict[str, Any]:
    panel = strict_json_file(path)
    if (
        type(panel) is not dict
        or set(panel) != {
            "panel_format", "created_date", "status", "decision",
            "gold_fields_present", "count", "source_file_sha256", "cases",
            "panel_payload_sha256",
        }
        or panel["panel_format"] != LEGACY_FORMAT
        or panel["gold_fields_present"] is not False
        or panel["count"] != 34
        or panel["decision"] != {
            "owner": "Roberto",
            "selected_option": "A",
            "automatic_intent_shadow_conversion": False,
            "cross_panel_compensation": False,
            "evaluation_authority": "phase1_typed_oracle_v1",
        }
        or type(panel["cases"]) is not list
        or len(panel["cases"]) != 34
    ):
        raise RuntimeError("legacy-panel envelope mismatch")
    for index, case in enumerate(panel["cases"]):
        if (
            type(case) is not dict
            or set(case) != {"ordinal", "opaque_case_id", "language", "query", "query_sha256"}
            or case["ordinal"] != index + 1
            or type(case["opaque_case_id"]) is not str
            or type(case["language"]) is not str
            or type(case["query"]) is not str
            or case["query_sha256"] != _query_hash(case["query"])
        ):
            raise RuntimeError(f"legacy case {index + 1} mismatch")
    observed = dict(panel)
    observed.pop("panel_payload_sha256")
    if sha256(canonical_json_bytes(observed)).hexdigest() != panel["panel_payload_sha256"]:
        raise RuntimeError("legacy-panel payload hash mismatch")
    return panel


def request_document(query: str, registry: dict[str, Any]) -> dict[str, Any]:
    if type(query) is not str:
        raise TypeError("query must be exact string")
    return {
        "contract_version": registry["contract_version"],
        "prompt": build_prompt(registry),
        "schema": build_schema(registry),
        "input": {"query": query},
    }


def _record(
    case: dict[str, Any],
    *,
    panel: str,
    registry: dict[str, Any],
    fake_transport: Callable[[dict[str, Any]], bytes] | None,
) -> dict[str, Any]:
    request = request_document(case["query"], registry)
    request_bytes = canonical_json_bytes(request)
    result: dict[str, Any] = {
        "ordinal": case["ordinal"],
        "opaque_case_id": case["opaque_case_id"],
        "query_sha256": case["query_sha256"],
        "panel": panel,
        "request_sha256": sha256(request_bytes).hexdigest(),
    }
    if fake_transport is None:
        result["status"] = "dry_run_no_transport"
        return result
    raw = fake_transport(request)
    if type(raw) is not bytes:
        raise TypeError("fake transport must return exact bytes")
    result["status"] = "fake_transport_complete"
    result["extraction"] = envelope_json(extract_raw_json(raw, registry))
    return result


def run_offline(
    *,
    include_legacy: bool = False,
    fake_transport: Callable[[dict[str, Any]], bytes] | None = None,
) -> dict[str, Any]:
    registry, identity = load_frozen_registry(REGISTRY_PATH)
    suite = load_query_suite()
    if suite["registry_payload_sha256"] != identity.payload_sha256:
        raise RuntimeError("query-suite registry binding mismatch")
    contract = compile_model_contract(registry)
    records: list[dict[str, Any]] = []
    for panel_name in ("canonical_120", "typed_controls_4"):
        for case in suite["panels"][panel_name]:
            records.append(
                _record(case, panel=panel_name, registry=registry, fake_transport=fake_transport)
            )
    panel_counts: dict[str, int] = {"canonical_120": 120, "typed_controls_4": 4}
    legacy_binding = None
    if include_legacy:
        legacy = load_legacy_panel()
        for case in legacy["cases"]:
            records.append(
                _record(case, panel="legacy_phase1_34", registry=registry, fake_transport=fake_transport)
            )
        panel_counts["legacy_phase1_34"] = 34
        legacy_binding = {
            "panel_file_sha256": file_sha256(LEGACY_PANEL_PATH),
            "evaluation_authority": "phase1_typed_oracle_v1",
            "separate_no_compensation": True,
        }
    return {
        "runner_version": RUNNER_VERSION,
        "artifact_kind": "offline_dry_run_batch" if fake_transport is None else "fake_transport_batch",
        "gpu_mode_present": False,
        "network_transport_present": False,
        "registry_file_sha256": identity.file_sha256,
        "registry_payload_sha256": identity.payload_sha256,
        "query_suite_file_sha256": file_sha256(QUERY_SUITE_PATH),
        "schema_sha256": contract.schema_sha256,
        "prompt_sha256": contract.prompt_sha256,
        "panel_counts": panel_counts,
        "legacy_binding": legacy_binding,
        "records": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", required=True)
    parser.add_argument("--include-legacy", action="store_true")
    args = parser.parse_args()
    report = run_offline(include_legacy=args.include_legacy)
    # Query text is never printed or persisted by this CLI.
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
