from __future__ import annotations

import base64
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ....prompt_style_v0_1.projection import PromptStyle
from .. import arm_style, evaluator
from ..build_artifacts import check
from ..evaluator import validate_complete_batch
from ..protocol import (
    ARMS,
    AUTHORIZATION_PATH,
    HERE,
    REQUEST_COUNT,
    STYLE_ARMS,
    canonical_json_bytes,
    load_manifest,
    load_query_panel,
    request_for,
    load_hash_seed_matrix,
)
from ..runner import RunPaths, TransportResponse, execute_fake_full_run, preflight, urllib_transport
from ..verify import verify_protocol


def response_body(content: bytes) -> bytes:
    wrapper = {
        "id": "offline-fake", "object": "chat.completion", "created": 0,
        "model": "local",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content.decode("utf-8")},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    return canonical_json_bytes(wrapper)


class Run3StyleTests(unittest.TestCase):
    def test_hash_seed_matrix_and_current_interpreter_are_exact(self) -> None:
        matrix = load_hash_seed_matrix()
        selected = matrix["selected_seed"]
        self.assertEqual(selected, min(
            row["seed"] for row in matrix["rows"]
            if row["A_full_extraction_exact"] == row["B_full_extraction_exact"] == 158
        ))
        self.assertEqual(__import__("os").environ.get("PYTHONHASHSEED"), str(selected))
        for confirmation in matrix["fresh_process_confirmations"]:
            for field in (
                "A_full_extraction_exact", "B_full_extraction_exact",
                "A_semantic_status_exact", "B_semantic_status_exact",
                "A_request_exact", "B_request_exact",
            ):
                self.assertEqual(confirmation[field], 158)

    def test_hash_seed_preflight_gate_rejects_a_different_seed(self) -> None:
        matrix = load_hash_seed_matrix()
        nonexact = next(
            row["seed"] for row in matrix["rows"]
            if row["A_full_extraction_exact"] != 158 or row["B_full_extraction_exact"] != 158
        )
        command = [
            sys.executable, "-c",
            "from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.live.run3_style.runner import preflight; preflight(check_runtime_environment=False)",
        ]
        environment = dict(os.environ)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["PYTHONHASHSEED"] = str(nonexact)
        rejected = subprocess.run(command, cwd=HERE.parents[6], env=environment, capture_output=True)
        self.assertNotEqual(rejected.returncode, 0)
        environment.pop("PYTHONHASHSEED")
        missing = subprocess.run(command, cwd=HERE.parents[6], env=environment, capture_output=True)
        self.assertNotEqual(missing.returncode, 0)

    def test_materialized_artifacts_and_disarmed_preflight(self) -> None:
        self.assertEqual(check(), [])
        report = verify_protocol(check_runtime_environment=False)
        self.assertEqual(report["error_count"], 0, report)
        self.assertFalse(AUTHORIZATION_PATH.exists())
        status = preflight(check_runtime_environment=False)
        self.assertEqual(status["status"], "disarmed_ready_for_two_audits")
        self.assertFalse(status["network_touched"])
        self.assertFalse(status["gpu_touched"])

    def test_manifest_632_latin_schedule_and_panel_counts(self) -> None:
        panel = load_query_panel()
        manifest = load_manifest()
        self.assertEqual(len(panel["cases"]), 158)
        self.assertEqual(len(manifest["records"]), REQUEST_COUNT)
        self.assertEqual(
            [row["arm"] for row in manifest["records"][:12]],
            [
                "A_SYSTEM_CURRENT", "S0_CURRENT", "S1_METNOS_SHORT", "S2_PROCEDURAL",
                "S0_CURRENT", "S1_METNOS_SHORT", "S2_PROCEDURAL", "A_SYSTEM_CURRENT",
                "S1_METNOS_SHORT", "S2_PROCEDURAL", "A_SYSTEM_CURRENT", "S0_CURRENT",
            ],
        )
        for arm in ARMS:
            rows = [row for row in manifest["records"] if row["arm"] == arm]
            self.assertEqual(len(rows), 158)
            self.assertEqual(
                {name: sum(row["panel"] == name for row in rows) for name in ("canonical_120", "typed_controls_4", "legacy_phase1_34")},
                {"canonical_120": 120, "typed_controls_4": 4, "legacy_phase1_34": 34},
            )
            positions = {position: sum(row["within_case_arm_ordinal"] == position for row in rows) for position in (1, 2, 3, 4)}
            self.assertLessEqual(max(positions.values()) - min(positions.values()), 1)

    def test_two_run2_requests_are_exact_and_style_arms_differ_only_system_prompt(self) -> None:
        from ...run2.protocol import request_for as run2_request_for
        for case in load_query_panel()["cases"]:
            requests = {arm: request_for(arm, case["query"], case["language"]) for arm in ARMS}
            self.assertEqual(
                canonical_json_bytes(requests["A_SYSTEM_CURRENT"]),
                canonical_json_bytes(run2_request_for("A", case["query"], case["language"])),
            )
            self.assertEqual(
                canonical_json_bytes(requests["S0_CURRENT"]),
                canonical_json_bytes(run2_request_for("B", case["query"], case["language"])),
            )
            baseline = requests["S0_CURRENT"]
            for arm in STYLE_ARMS:
                request = requests[arm]
                self.assertEqual(request["response_format"]["type"], "json_schema")
                self.assertIs(request["response_format"]["json_schema"]["strict"], True)
                self.assertNotIn("grammar", request)
                self.assertNotIn("tools", request)
                normalized = deepcopy(request)
                normalized["messages"][0]["content"] = baseline["messages"][0]["content"]
                self.assertEqual(canonical_json_bytes(normalized), canonical_json_bytes(baseline))

    def test_one_call_byte_preserving_no_repair_no_critic(self) -> None:
        raw = b'{"kind":"unrepresentable","reason":"no_actionable_intent"}'
        result = arm_style.extract_response(raw)
        self.assertEqual(base64.b64decode(result["raw_model_output_b64"]), raw)
        self.assertEqual(result["raw_model_output_sha256"], sha256(raw).hexdigest())
        self.assertEqual(result["model_calls"], 1)
        self.assertFalse(result["repair_attempted"])
        self.assertFalse(result["critic_enabled"])

    def test_both_adapter_outputs_are_exact_for_real_run2_model_content(self) -> None:
        run2 = json.loads((HERE.parent / "run2" / "sealed_batch_run2.json").read_text(encoding="utf-8"))
        panel = {row["sample_index"]: row for row in load_query_panel()["cases"]}
        records = [row for row in run2["records"] if row["arm"] in {"A", "B"}]
        self.assertEqual(len(records), 316)
        for record in records:
            content = base64.b64decode(record["model_content_b64"], validate=True)
            case = panel[record["sample_index"]]
            replay = arm_style.extract_response(
                content,
                arm="A_SYSTEM_CURRENT" if record["arm"] == "A" else "S0_CURRENT",
                query=case["query"], language=case["language"],
            )
            self.assertEqual(canonical_json_bytes(replay), canonical_json_bytes(record["extraction"]))
            expected_version = "current-control/run2" if record["arm"] == "A" else "candidate-v0.3/run2"
            self.assertEqual(replay["adapter_version"], expected_version)

    def test_malformed_language_is_rejected_before_send(self) -> None:
        with patch("urllib.request.urlopen") as send:
            for arm in STYLE_ARMS:
                for tag in ("", "it_IT", "sl-rozaj-ROZAJ", "i-Klingon", "ſgn-BE-FR"):
                    with self.subTest(arm=arm, tag=tag), self.assertRaises(ValueError):
                        request_for(arm, "valid Unicode query 雪", tag)
            send.assert_not_called()

    def test_fake_full_run_632_and_exact_replay(self) -> None:
        manifest = load_manifest()
        outputs = {
            "A_SYSTEM_CURRENT": b'{"verb":"get","object":"now"}',
            "S0_CURRENT": b'{"kind":"unrepresentable","reason":"no_actionable_intent"}',
            "S1_METNOS_SHORT": b'{"kind":"operation_graph","steps":[{"route":"get/now"}]}',
            "S2_PROCEDURAL": b'{"kind":"operation_graph","steps":[{"route":"hostile/route"}]}',
        }
        request_arm = {row["request_sha256"]: row["arm"] for row in manifest["records"]}
        calls = 0

        def fake(request: dict, timeout: int) -> TransportResponse:
            nonlocal calls
            self.assertEqual(timeout, 120)
            calls += 1
            arm = request_arm[sha256(canonical_json_bytes(request)).hexdigest()]
            return TransportResponse(True, 200, (), response_body(outputs[arm]), 1, None)

        with tempfile.TemporaryDirectory() as temporary:
            paths = RunPaths.in_directory(Path(temporary))
            batch = execute_fake_full_run(manifest, transport=fake, paths=paths)
            self.assertEqual(calls, REQUEST_COUNT)
            self.assertEqual(batch["state"], "complete")
            self.assertEqual(batch["accepted_http_posts"], REQUEST_COUNT)
            self.assertEqual(len(paths.journal.read_bytes().splitlines()), REQUEST_COUNT)
            statuses = {
                arm: {row["extraction"]["status"] for row in batch["records"] if row["arm"] == arm}
                for arm in ARMS
            }
            self.assertTrue(all(status.startswith("valid_") for status in statuses["A_SYSTEM_CURRENT"]))
            self.assertEqual(statuses["S0_CURRENT"], {"valid_unrepresentable"})
            self.assertEqual(statuses["S1_METNOS_SHORT"], {"valid_representable"})
            self.assertEqual(statuses["S2_PROCEDURAL"], {"document_invalid"})
            replayed, _public, gate = validate_complete_batch(paths, require_authorization=False)
            self.assertEqual(replayed["record_count"], REQUEST_COUNT)
            self.assertEqual(gate["raw_replayed"], REQUEST_COUNT)
            self.assertEqual(gate["unexpected_differences"], 0)

    def test_transport_envelope_failures_stop_partial_but_model_invalid_continues(self) -> None:
        manifest = load_manifest()

        def body_limit(_request: dict, _timeout: int) -> TransportResponse:
            return TransportResponse(True, 200, (), response_body(b'{}'), 1, "HTTP_BODY_LIMIT")

        with tempfile.TemporaryDirectory() as temporary:
            batch = execute_fake_full_run(manifest, transport=body_limit, paths=RunPaths.in_directory(Path(temporary)))
            self.assertEqual(batch["state"], "partial_transport_stop")
            self.assertEqual(batch["record_count"], 1)
        with tempfile.TemporaryDirectory() as temporary:
            paths = RunPaths.in_directory(Path(temporary))
            def malformed(_request: dict, _timeout: int) -> TransportResponse:
                return TransportResponse(True, 200, (), b'{"choices":[]}', 1, None)
            batch = execute_fake_full_run(manifest, transport=malformed, paths=paths)
            self.assertEqual(batch["state"], "partial_transport_stop")
            self.assertEqual(batch["record_count"], 1)

    def test_direct_live_transport_path_is_not_publicly_callable(self) -> None:
        from .. import runner
        with patch.object(runner, "urllib_transport") as send:
            with self.assertRaisesRegex(RuntimeError, "internal execution seal"):
                runner._execute_manifest(
                    load_manifest(), authorization_sha256="0" * 64,
                    transport=send, paths=RunPaths.in_directory(Path(tempfile.mkdtemp())),
                    _seal=object(),
                )
            send.assert_not_called()

    def test_evaluator_reuses_run2_metric_and_has_no_combined_score(self) -> None:
        from ...run2 import evaluator as run2_evaluator
        self.assertIs(evaluator.semantic_row_v03, run2_evaluator._row)
        self.assertIs(evaluator.semantic_aggregate_v03, run2_evaluator._aggregate)
        sample = {
            arm: {column: 100 for column in evaluator.CRITICAL_COLUMNS}
            for arm in ARMS
        }
        sample["S1_METNOS_SHORT"]["semantic_exact_canonical"] = 103
        comparison = evaluator._pairwise(sample)
        self.assertEqual(len(comparison), 6)
        self.assertIsNone(comparison["S0_CURRENT_vs_S1_METNOS_SHORT"]["combined_score"])

    def test_one_case_anchor_drift_blocks_every_style_winner(self) -> None:
        sample = {
            arm: {column: 100 for column in evaluator.CRITICAL_COLUMNS}
            for arm in ARMS
        }
        sample["S1_METNOS_SHORT"]["semantic_exact_canonical"] = 110
        comparison = evaluator._pairwise(sample, attributable=False)
        self.assertTrue(all(item["headline_interpretation"] == "non_attributable_anchor_drift" for item in comparison.values()))
        self.assertTrue(all(item["left_style_pass"] is False for item in comparison.values()))
        self.assertTrue(all(item["right_style_pass"] is False for item in comparison.values()))

    def test_both_real_run2_anchors_detect_one_raw_drift_and_block_attribution(self) -> None:
        run2_batch = json.loads((HERE.parent / "run2" / "sealed_batch_run2.json").read_text(encoding="utf-8"))
        run2_eval = json.loads((HERE.parent / "run2" / "evaluation_run2.json").read_text(encoding="utf-8"))
        records = [deepcopy(row) for row in run2_batch["records"] if row["arm"] in {"A", "B"}]
        for record in records:
            record["arm"] = "A_SYSTEM_CURRENT" if record["arm"] == "A" else "S0_CURRENT"
        grouped = {
            "canonical_120.A_SYSTEM_CURRENT": deepcopy(run2_eval["typed_panels"]["canonical_120.A"]),
            "typed_controls_4.A_SYSTEM_CURRENT": deepcopy(run2_eval["typed_panels"]["typed_controls_4.A"]),
            "canonical_120.S0_CURRENT": deepcopy(run2_eval["typed_panels"]["canonical_120.B"]),
            "typed_controls_4.S0_CURRENT": deepcopy(run2_eval["typed_panels"]["typed_controls_4.B"]),
        }
        legacy = {
            "A_SYSTEM_CURRENT": deepcopy(run2_eval["legacy_phase1"]["arms"]["A"]),
            "S0_CURRENT": deepcopy(run2_eval["legacy_phase1"]["arms"]["B"]),
        }
        exact = evaluator._run2_anchors({"records": records}, grouped, legacy)
        self.assertTrue(exact["both_exact"])
        swapped_aggregates = {
            "canonical_120.A_SYSTEM_CURRENT": deepcopy(run2_eval["typed_panels"]["canonical_120.B"]),
            "typed_controls_4.A_SYSTEM_CURRENT": deepcopy(run2_eval["typed_panels"]["typed_controls_4.B"]),
            "canonical_120.S0_CURRENT": deepcopy(run2_eval["typed_panels"]["canonical_120.A"]),
            "typed_controls_4.S0_CURRENT": deepcopy(run2_eval["typed_panels"]["typed_controls_4.A"]),
        }
        swapped_legacy = {
            "A_SYSTEM_CURRENT": deepcopy(run2_eval["legacy_phase1"]["arms"]["B"]),
            "S0_CURRENT": deepcopy(run2_eval["legacy_phase1"]["arms"]["A"]),
        }
        aggregate_drift = evaluator._run2_anchors(
            {"records": records}, swapped_aggregates, swapped_legacy,
        )
        self.assertFalse(aggregate_drift["both_exact"])
        for arm in ("A_SYSTEM_CURRENT", "S0_CURRENT"):
            anchor = aggregate_drift["anchors"][arm]
            self.assertEqual(anchor["raw_exact_count"], 158)
            self.assertEqual(anchor["extraction_exact_count"], 158)
            self.assertFalse(anchor["aggregate_exact"])
            self.assertTrue(any(field.startswith("canonical_120.") for field in anchor["aggregate_drift_fields"]))
            self.assertTrue(any(field.startswith("typed_controls_4.") for field in anchor["aggregate_drift_fields"]))
            self.assertIn("legacy_phase1.direct_binding_exact", anchor["aggregate_drift_fields"])
        swapped_records = deepcopy(records)
        for record in swapped_records:
            record["arm"] = "S0_CURRENT" if record["arm"] == "A_SYSTEM_CURRENT" else "A_SYSTEM_CURRENT"
        swapped_anchors = evaluator._run2_anchors({"records": swapped_records}, grouped, legacy)
        self.assertFalse(swapped_anchors["both_exact"])
        records[0]["model_content_sha256"] = "0" * 64
        drifted = evaluator._run2_anchors({"records": records}, grouped, legacy)
        self.assertFalse(drifted["both_exact"])
        self.assertEqual(drifted["anchors"]["A_SYSTEM_CURRENT"]["raw_exact_count"], 157)
        self.assertEqual(drifted["anchors"]["S0_CURRENT"]["raw_exact_count"], 158)
        sample = {arm: {column: 100 for column in evaluator.CRITICAL_COLUMNS} for arm in ARMS}
        sample["S1_METNOS_SHORT"]["semantic_exact_canonical"] = 110
        comparison = evaluator._pairwise(sample, attributable=drifted["both_exact"])
        self.assertTrue(all(item["headline_interpretation"] == "non_attributable_anchor_drift" for item in comparison.values()))
        self.assertTrue(all(not item["left_style_pass"] and not item["right_style_pass"] for item in comparison.values()))

    def test_pairwise_safety_is_symmetric_and_blocks_right_gain(self) -> None:
        sample = {
            arm: {column: 100 for column in evaluator.CRITICAL_COLUMNS}
            for arm in ARMS
        }
        sample["S1_METNOS_SHORT"]["semantic_exact_canonical"] = 103
        sample["S1_METNOS_SHORT"]["false_action_avoided"] = 99
        comparison = evaluator._pairwise(sample)
        pair = comparison["S0_CURRENT_vs_S1_METNOS_SHORT"]
        self.assertFalse(pair["right_style_pass"])
        self.assertEqual(pair["headline_interpretation"], "headline_gain_blocked_by_safety")
        self.assertIn("false_action_avoided", pair["right_safety_regressions"])
        self.assertEqual(
            pair["right_minus_left_deltas"],
            {key: -value for key, value in pair["left_minus_right_deltas"].items()},
        )
        swapped = {
            "A_SYSTEM_CURRENT": sample["A_SYSTEM_CURRENT"],
            "S0_CURRENT": sample["S1_METNOS_SHORT"],
            "S1_METNOS_SHORT": sample["S0_CURRENT"],
            "S2_PROCEDURAL": sample["S2_PROCEDURAL"],
        }
        mirrored = evaluator._pairwise(swapped)["S0_CURRENT_vs_S1_METNOS_SHORT"]
        self.assertEqual(pair["left_style_pass"], mirrored["right_style_pass"])
        self.assertEqual(pair["right_style_pass"], mirrored["left_style_pass"])
        self.assertEqual(pair["left_safety_regressions"], mirrored["right_safety_regressions"])
        self.assertEqual(pair["right_safety_regressions"], mirrored["left_safety_regressions"])


if __name__ == "__main__":
    unittest.main()
