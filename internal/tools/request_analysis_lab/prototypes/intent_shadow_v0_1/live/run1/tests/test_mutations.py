from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from ..protocol import (
    FREEZE_PATH,
    MANIFEST_PATH,
    PROTOCOL_PATH,
    QUERY_PANEL_PATH,
    ProtocolError,
    load_manifest,
    load_protocol,
    load_query_panel,
    pretty_json_bytes,
    strict_json_loads,
)
from ..verify import verify_protocol


def mutated_file(source: Path, mutate) -> tempfile.TemporaryDirectory:
    raise AssertionError("use temporary_json")


class temporary_json:
    def __init__(self, source: Path, mutate) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / source.name
        value = json.loads(source.read_text(encoding="utf-8"))
        mutate(value)
        self.path.write_bytes(pretty_json_bytes(value))

    def __enter__(self) -> Path:
        return self.path

    def __exit__(self, *_args) -> None:
        self.directory.cleanup()


class Run1MutationTests(unittest.TestCase):
    def test_d01_duplicate_and_d02_nonfinite(self) -> None:
        with self.assertRaises(ProtocolError):
            strict_json_loads(b'{"run_id":"a","run_id":"b"}')
        for raw in (b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e9999}', b'{"x":123456789012345678901234567890123456789012345678901234567890123456789}'):
            with self.assertRaises(ProtocolError):
                strict_json_loads(raw)

    def test_d03_exact_json_types(self) -> None:
        mutations = (
            lambda value: value["counts"].__setitem__("total", True),
            lambda value: value["counts"].__setitem__("total", 158.0),
            lambda value: value["cases"][0].__setitem__("sample_index", False),
            lambda value: value["cases"][0].__setitem__("panel_ordinal", 1.0),
            lambda value: value.__setitem__("gold_fields_present", 0),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate), temporary_json(QUERY_PANEL_PATH, mutate) as path:
                with self.assertRaises(ProtocolError):
                    load_query_panel(path)

    def test_query_panel_closed_counts_order_hash_and_gold(self) -> None:
        mutations = (
            lambda value: value.__setitem__("extra", True),
            lambda value: value["counts"].__setitem__("total", 157),
            lambda value: value["cases"][0].__setitem__("sample_index", 1),
            lambda value: value["cases"][0].__setitem__("query_sha256", "0" * 64),
            lambda value: value["cases"][0].__setitem__("expected", {}),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate), temporary_json(QUERY_PANEL_PATH, mutate) as path:
                with self.assertRaises(ProtocolError):
                    load_query_panel(path)

    def test_protocol_roots_profiles_limits_critic_and_v03(self) -> None:
        mutations = (
            lambda value: value.__setitem__("run_id", "run2"),
            lambda value: value["iteration"].__setitem__("run_number", 2),
            lambda value: value["authorization"].__setitem__("state", "armed"),
            lambda value: value["arms"].__setitem__("B", "critic on"),
            lambda value: value["generation_profile"].__setitem__("retry_count", 1),
            lambda value: value["technical_limits"].__setitem__("json_bytes", 1),
            lambda value: value["failure_policy"].__setitem__("document_invalid", "stop"),
            lambda value: value["evaluation"].__setitem__("version", "v0.2"),
            lambda value: value["evaluation"].__setitem__("critical_columns", value["evaluation"]["critical_columns"][:-1]),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate), temporary_json(PROTOCOL_PATH, mutate) as path:
                with self.assertRaises(ProtocolError):
                    load_protocol(path)

    def test_critical_column_order_is_nonsemantic_but_unique(self) -> None:
        with temporary_json(PROTOCOL_PATH, lambda value: value["evaluation"]["critical_columns"].reverse()) as path:
            # Recompute only the payload because list order is explicitly nonsemantic authority.
            value = json.loads(path.read_text())
            from ..protocol import payload_sha256
            value["protocol_payload_sha256"] = payload_sha256(value, "protocol_payload_sha256")
            path.write_bytes(pretty_json_bytes(value))
            self.assertEqual(load_protocol(path)["run_id"], "intent-shadow-v0.2-run1")
        with temporary_json(PROTOCOL_PATH, lambda value: value["evaluation"]["critical_columns"].append(value["evaluation"]["critical_columns"][0])) as path:
            with self.assertRaises(ProtocolError):
                load_protocol(path)

    def test_d04_control_authorities_are_exact_unique_sets(self) -> None:
        mutations = (
            lambda value: value["current_extractor"]["prompt_languages"].append("it"),
            lambda value: value["current_extractor"].__setitem__("prompt_languages", ["it"]),
            lambda value: value["evaluation"]["critical_columns"].append(value["evaluation"]["critical_columns"][0]),
        )
        for mutate in mutations[:2]:
            from ..protocol import CONTROL_SNAPSHOT_PATH, load_control_snapshot
            with self.subTest(mutate=mutate), temporary_json(CONTROL_SNAPSHOT_PATH, mutate) as path:
                with self.assertRaises(ProtocolError):
                    load_control_snapshot(path)
        with temporary_json(PROTOCOL_PATH, mutations[2]) as path:
            with self.assertRaises(ProtocolError):
                load_protocol(path)

    def test_manifest_self_forward_order_hash_and_extra_fail_closed(self) -> None:
        mutations = (
            lambda value: value["records"][0].__setitem__("request_ordinal", 2),
            lambda value: value["records"][0].__setitem__("arm", "B"),
            lambda value: value["records"][0].__setitem__("request_sha256", "0" * 64),
            lambda value: value["records"][0].__setitem__("expected", {}),
            lambda value: value["records"].pop(),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate), temporary_json(MANIFEST_PATH, mutate) as path:
                with self.assertRaises(ProtocolError):
                    load_manifest(path)

    def test_freeze_mutation_and_missing_auth_fail(self) -> None:
        with temporary_json(FREEZE_PATH, lambda value: value["local_files"].__setitem__("runner.py", "0" * 64)) as path:
            report = verify_protocol(path, check_runtime_environment=False)
            self.assertGreater(report["error_count"], 0)
        report = verify_protocol(authorization_state="armed", authorization_path=Path("/definitely/missing"), check_runtime_environment=False)
        self.assertGreater(report["error_count"], 0)


if __name__ == "__main__":
    unittest.main()
