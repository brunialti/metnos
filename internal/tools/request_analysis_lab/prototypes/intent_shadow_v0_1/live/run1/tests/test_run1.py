from __future__ import annotations

import ast
import base64
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from .. import arm_b
from ..build_artifacts import check
from .. import evaluator
from ..evaluator import canonical_semantic_projection, validate_complete_batch
from ..protocol import (
    AUTHORIZATION_PATH,
    CANDIDATE_PROJECTION_PATH,
    FREEZE_PATH,
    HERE,
    canonical_json_bytes,
    file_sha256,
    load_manifest,
    load_protocol,
    load_query_panel,
    payload_sha256,
    pretty_json_bytes,
    request_for,
    strict_json_file,
)
from ..runner import (
    RunPaths,
    TransportResponse,
    execute_fake_full_run,
    preflight,
    urllib_transport,
)
from ..verify import verify_frozen_files, verify_protocol


def response_body(content: bytes) -> bytes:
    wrapper = {
        "id": "offline-fake", "object": "chat.completion", "created": 0,
        "model": "local",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content.decode("utf-8")},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    return canonical_json_bytes(wrapper)


class Run1ProtocolTests(unittest.TestCase):
    def test_materialized_artifacts_and_disarmed_preflight(self) -> None:
        self.assertEqual(check(), [])
        report = verify_protocol(check_runtime_environment=True)
        self.assertEqual(report["error_count"], 0, report)
        self.assertFalse(AUTHORIZATION_PATH.exists())
        status = preflight()
        self.assertEqual(status["status"], "disarmed_ready_for_audit")
        self.assertFalse(status["network_touched"])
        self.assertFalse(status["gpu_touched"])

    def test_manifest_316_schedule_panels_and_request_contracts(self) -> None:
        panel = load_query_panel()
        manifest = load_manifest()
        self.assertEqual(len(panel["cases"]), 158)
        self.assertEqual(len(manifest["records"]), 316)
        self.assertEqual([row["arm"] for row in manifest["records"][:6]], ["A", "B", "B", "A", "A", "B"])
        counts = {name: sum(case["panel"] == name for case in panel["cases"]) for name in ("canonical_120", "typed_controls_4", "legacy_phase1_34")}
        self.assertEqual(counts, {"canonical_120": 120, "typed_controls_4": 4, "legacy_phase1_34": 34})
        case = panel["cases"][0]
        a = request_for("A", case["query"], case["language"])
        b = request_for("B", case["query"], case["language"])
        self.assertNotIn("response_format", a)
        self.assertEqual(b["response_format"]["type"], "json_schema")
        self.assertIs(b["response_format"]["json_schema"]["strict"], True)
        self.assertNotIn("grammar", b)
        self.assertNotIn("tools", b)
        for key in ("model", "temperature", "seed", "max_tokens", "stream", "cache_prompt", "chat_template_kwargs"):
            self.assertEqual(a[key], b[key])

    def test_arm_b_is_byte_preserving_one_call_no_repair_no_critic(self) -> None:
        raw = b'{"kind":"unrepresentable","reason":"no_actionable_intent"}'
        result = arm_b.extract_response(raw)
        self.assertEqual(base64.b64decode(result["raw_model_output_b64"]), raw)
        self.assertEqual(result["raw_model_output_sha256"], sha256(raw).hexdigest())
        self.assertEqual(result["status"], "valid_unrepresentable")
        self.assertEqual(result["model_calls"], 1)
        self.assertFalse(result["repair_attempted"])
        self.assertFalse(result["critic_enabled"])
        invalid = arm_b.extract_response(b'{"kind":"operation_graph","steps":[{"route":"hostile/route"}]}')
        self.assertEqual(invalid["status"], "document_invalid")
        self.assertFalse(invalid["repair_attempted"])

    def test_candidate_freeze_drift_fails_closed(self) -> None:
        from ..protocol import verify_candidate_environment
        for result in (["forced_candidate_drift"], None, ()):
            with self.subTest(result=result), patch(
                "internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.candidate_v0_2.build_artifacts.check",
                return_value=result,
            ):
                with self.assertRaisesRegex(Exception, "CANDIDATE_FREEZE_DRIFT"):
                    verify_candidate_environment()

    def test_post_seal_source_drift_is_rejected_before_gold(self) -> None:
        freeze = strict_json_file(FREEZE_PATH)
        freeze["local_files"]["evaluator.py"] = "0" * 64
        freeze["lock_payload_sha256"] = payload_sha256(freeze, "lock_payload_sha256")
        with tempfile.TemporaryDirectory() as temporary:
            mutated = Path(temporary) / "mutated.freeze.json"
            mutated.write_bytes(pretty_json_bytes(freeze))
            with self.assertRaisesRegex(RuntimeError, "FREEZE_LOCAL_HASH:evaluator.py"):
                verify_frozen_files(mutated, check_runtime_environment=False)
        with (
            patch.object(evaluator, "verify_frozen_files", side_effect=RuntimeError("forced evaluator drift")),
            patch.object(evaluator, "_load_gold") as gold_loader,
        ):
            with self.assertRaisesRegex(RuntimeError, "forced evaluator drift"):
                evaluator.evaluate()
            gold_loader.assert_not_called()

    def test_fake_full_run_316_continues_document_invalid(self) -> None:
        manifest = load_manifest()
        candidate_invalid = b'{"kind":"operation_graph","steps":[{"route":"hostile/route"}]}'
        control_valid = b'{"verb":"find","object":"files","actions":[{"verb":"find","object":"files"}]}'
        calls = 0

        def fake(request: dict, timeout: int) -> TransportResponse:
            nonlocal calls
            self.assertEqual(timeout, 120)
            calls += 1
            content = candidate_invalid if "response_format" in request else control_valid
            return TransportResponse(True, 200, (("content-type", "application/json"),), response_body(content), 1, None)

        with tempfile.TemporaryDirectory() as temporary:
            paths = RunPaths.in_directory(Path(temporary))
            batch = execute_fake_full_run(manifest, transport=fake, paths=paths)
            self.assertEqual(calls, 316)
            self.assertEqual(batch["state"], "complete")
            self.assertEqual(batch["accepted_http_posts"], 316)
            self.assertEqual(len(paths.journal.read_bytes().splitlines()), 316)
            self.assertTrue(paths.seal.is_file())
            b_records = [row for row in batch["records"] if row["arm"] == "B"]
            self.assertEqual(len(b_records), 158)
            self.assertTrue(all(row["extraction"]["status"] == "document_invalid" for row in b_records))
            replayed, _public, gate = validate_complete_batch(paths, require_authorization=False)
            self.assertEqual(replayed["record_count"], 316)
            self.assertEqual(gate["raw_replayed"], 316)

    def test_marker_and_checkpoint_mutations_are_rejected_before_gold(self) -> None:
        manifest = load_manifest()

        def fake(request: dict, _timeout: int) -> TransportResponse:
            content = (
                b'{"kind":"unrepresentable","reason":"no_actionable_intent"}'
                if "response_format" in request
                else b'{"verb":"find","object":"files","actions":[{"verb":"find","object":"files"}]}'
            )
            return TransportResponse(True, 200, (), response_body(content), 1, None)

        with tempfile.TemporaryDirectory() as temporary:
            paths = RunPaths.in_directory(Path(temporary))
            execute_fake_full_run(manifest, transport=fake, paths=paths)
            marker_bytes = paths.consumption.read_bytes()
            seal_bytes = paths.seal.read_bytes()
            marker = strict_json_file(paths.consumption)
            marker["extra"] = True
            paths.consumption.write_bytes(pretty_json_bytes(marker))
            seal = strict_json_file(paths.seal)
            seal["consumption_marker_sha256"] = file_sha256(paths.consumption)
            paths.seal.write_bytes(pretty_json_bytes(seal))
            with patch.object(evaluator, "_load_gold") as gold_loader:
                with self.assertRaisesRegex(RuntimeError, "consumption mismatch"):
                    validate_complete_batch(paths, require_authorization=False)
                gold_loader.assert_not_called()

            paths.consumption.write_bytes(marker_bytes)
            paths.seal.write_bytes(seal_bytes)
            checkpoint = strict_json_file(paths.checkpoint)
            checkpoint["run_id"] = "another-run"
            paths.checkpoint.write_bytes(pretty_json_bytes(checkpoint))
            with patch.object(evaluator, "_load_gold") as gold_loader:
                with self.assertRaisesRegex(RuntimeError, "checkpoint mismatch"):
                    validate_complete_batch(paths, require_authorization=False)
                gold_loader.assert_not_called()

    def test_transport_failure_stops_and_seals_partial(self) -> None:
        manifest = load_manifest()
        calls = 0

        def fake(_request: dict, _timeout: int) -> TransportResponse:
            nonlocal calls
            calls += 1
            if calls == 2:
                return TransportResponse(False, None, (), b"", 1, "TRANSPORT_TIMEOUT")
            return TransportResponse(True, 200, (), response_body(b'{"verb":"find","object":"files"}'), 1, None)

        with tempfile.TemporaryDirectory() as temporary:
            paths = RunPaths.in_directory(Path(temporary))
            batch = execute_fake_full_run(manifest, transport=fake, paths=paths)
            self.assertEqual(batch["state"], "partial_transport_stop")
            self.assertEqual(batch["record_count"], 2)
            self.assertEqual(batch["accepted_http_posts"], 1)
            self.assertTrue(paths.partial.is_file())
            self.assertEqual(strict_json_file(paths.seal)["state"], "partial")

    def test_body_limit_and_malformed_envelope_stop_partial(self) -> None:
        manifest = load_manifest()
        failures = (
            TransportResponse(True, 200, (), b"{}", 1, "HTTP_BODY_LIMIT"),
            TransportResponse(True, 200, (), b"{}", 1, None),
        )
        for response in failures:
            with self.subTest(response=response), tempfile.TemporaryDirectory() as temporary:
                calls = 0

                def fake(_request: dict, _timeout: int) -> TransportResponse:
                    nonlocal calls
                    calls += 1
                    return response

                paths = RunPaths.in_directory(Path(temporary))
                batch = execute_fake_full_run(manifest, transport=fake, paths=paths)
                self.assertEqual(calls, 1)
                self.assertEqual(batch["state"], "partial_transport_stop")
                self.assertEqual(batch["accepted_http_posts"], 1)
                self.assertEqual(batch["record_count"], 1)
                self.assertTrue(paths.partial.is_file())
                self.assertEqual(strict_json_file(paths.seal)["state"], "partial")

    def test_unexpected_adapter_failure_stops_partial(self) -> None:
        manifest = load_manifest()

        def fake(_request: dict, _timeout: int) -> TransportResponse:
            return TransportResponse(
                True, 200, (), response_body(b'{"synthetic":true}'), 1, None,
            )

        with tempfile.TemporaryDirectory() as temporary, patch(
            "internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.live.run1.runner._extract",
            side_effect=RuntimeError("synthetic adapter failure"),
        ):
            paths = RunPaths.in_directory(Path(temporary))
            batch = execute_fake_full_run(manifest, transport=fake, paths=paths)
            self.assertEqual(batch["state"], "partial_transport_stop")
            self.assertEqual(batch["record_count"], 1)
            self.assertEqual(batch["stopped_reason"], "ADAPTER_RUNTIMEERROR")

    def test_direct_live_transport_is_rejected_before_send(self) -> None:
        from .. import runner
        manifest = load_manifest()
        self.assertFalse(hasattr(runner, "execute_manifest"))
        with tempfile.TemporaryDirectory() as temporary:
            paths = RunPaths.in_directory(Path(temporary))
            with self.assertRaisesRegex(RuntimeError, "cannot receive network"):
                execute_fake_full_run(
                    manifest,
                    transport=urllib_transport,
                    paths=paths,
                )
            self.assertFalse(paths.consumption.exists())

    def test_symmetric_v03_projection_only_removes_derived_representation(self) -> None:
        registry = strict_json_file(CANDIDATE_PROJECTION_PATH)
        verbose = {"kind": "operation_graph", "body": [{"kind": "operation", "route": "find/files", "data_from": []}]}
        compact = {"kind": "operation_graph", "body": [{"kind": "operation", "route": "find/files"}]}
        self.assertEqual(canonical_semantic_projection(verbose, registry), canonical_semantic_projection(compact, registry))
        changed = {"kind": "operation_graph", "body": [{"kind": "operation", "route": "read/files"}]}
        self.assertNotEqual(canonical_semantic_projection(verbose, registry), canonical_semantic_projection(changed, registry))

    def test_gold_filenames_absent_from_live_import_graph(self) -> None:
        source = "\n".join((HERE / name).read_text(encoding="utf-8") for name in ("protocol.py", "arm_a.py", "arm_b.py", "runner.py"))
        for token in ("intent_shadow_oracle_v0_1.json", "metnos_phase1_typed_oracle_v1.overlay.json", "question_focus_controls_v1.json"):
            self.assertNotIn(token, source)

    def test_no_benchmark_values_or_fragments_in_candidate_and_adapters(self) -> None:
        panel = load_query_panel()
        candidate = HERE.parents[1] / "candidate_v0_2"
        paths = [
            candidate / name
            for name in (
                "api.py", "compiler.py", "validator.py", "structured_client.py",
                "projection.py", "language_tag.py",
            )
        ] + [HERE / "arm_a.py", HERE / "arm_b.py"]
        queries = [" ".join(case["query"].casefold().split()) for case in panel["cases"]]
        identities = {
            value
            for case in panel["cases"]
            for value in (case["opaque_case_id"], case["query_sha256"])
        }
        for path in paths:
            source = path.read_text(encoding="utf-8")
            folded = source.casefold()
            for identity in identities:
                self.assertNotIn(identity.casefold(), folded, path.name)
            for query in queries:
                self.assertNotIn(query, folded, path.name)
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and type(node.value) is str:
                    literal = " ".join(node.value.casefold().split())
                    if len(literal) >= 12:
                        self.assertFalse(
                            any(literal in query for query in queries),
                            f"benchmark fragment in {path.name}: {literal!r}",
                        )
                if isinstance(node, ast.Constant) and type(node.value) is int:
                    self.assertNotIn(node.value, {120, 124, 139, 156, 158, 316}, path.name)


if __name__ == "__main__":
    unittest.main()
