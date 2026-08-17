from __future__ import annotations

import ast
import json
from pathlib import Path
import tempfile
import unittest

from ..protocol import (
    FREEZE_PATH,
    HASH_SEED_MATRIX_PATH,
    MANIFEST_PATH,
    PROTOCOL_PATH,
    QUERY_PANEL_PATH,
    ProtocolError,
    load_manifest,
    load_hash_seed_matrix,
    load_protocol,
    load_query_panel,
    pretty_json_bytes,
    strict_json_loads,
)
from ..verify import verify_protocol


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


class Run3StyleMutationTests(unittest.TestCase):
    def test_source_has_no_duplicate_literal_dict_keys(self) -> None:
        source_root = Path(__file__).resolve().parents[1]
        duplicates: list[tuple[str, int, str]] = []
        for path in sorted(source_root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Dict):
                    continue
                seen: set[str] = set()
                for key in node.keys:
                    if isinstance(key, ast.Constant) and type(key.value) is str:
                        if key.value in seen:
                            duplicates.append((path.name, node.lineno, key.value))
                        seen.add(key.value)
        self.assertEqual(duplicates, [])

    def test_d01_duplicate_and_d02_nonfinite(self) -> None:
        with self.assertRaises(ProtocolError):
            strict_json_loads(b'{"run_id":"a","run_id":"b"}')
        for raw in (b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e9999}', b'{"x":123456789012345678901234567890123456789012345678901234567890123456789}'):
            with self.assertRaises(ProtocolError):
                strict_json_loads(raw)

    def test_d03_exact_types_and_closed_objects(self) -> None:
        mutations = (
            lambda value: value["counts"].__setitem__("total", True),
            lambda value: value["counts"].__setitem__("total", 158.0),
            lambda value: value["cases"][0].__setitem__("sample_index", False),
            lambda value: value.__setitem__("extra", True),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate), temporary_json(QUERY_PANEL_PATH, mutate) as path:
                with self.assertRaises(ProtocolError):
                    load_query_panel(path)

    def test_protocol_style_schedule_safety_and_no_combined_score_are_closed(self) -> None:
        mutations = (
            lambda value: value["iteration"].__setitem__("logical_change", "coverage"),
            lambda value: value["prompt_authority"].__setitem__("new_semantic_rules", 1),
            lambda value: value["schedule"]["sequence_counts"].__setitem__("ABCD", 39),
            lambda value: value["evaluation"].__setitem__("combined_score", True),
            lambda value: value["evaluation"].__setitem__("safety_columns", value["evaluation"]["safety_columns"][:-1]),
            lambda value: value["evaluation"]["run2_anchors"].__setitem__("cases_per_anchor", 157),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate), temporary_json(PROTOCOL_PATH, mutate) as path:
                with self.assertRaises(ProtocolError):
                    load_protocol(path)

    def test_hash_seed_matrix_selection_diff_and_confirmation_fail_closed(self) -> None:
        mutations = (
            lambda value: value.__setitem__("selected_seed", 1),
            lambda value: value["rows"][0].__setitem__("A_mismatch_diff_paths", ["/unexpected"]),
            lambda value: value["fresh_process_confirmations"][0].__setitem__("A_request_exact", 157),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate), temporary_json(HASH_SEED_MATRIX_PATH, mutate) as path:
                value = json.loads(path.read_text(encoding="utf-8"))
                value["payload_sha256"] = ""
                from ..protocol import payload_sha256
                value["payload_sha256"] = payload_sha256(value, "payload_sha256")
                path.write_bytes(pretty_json_bytes(value))
                with self.assertRaises(ProtocolError):
                    load_hash_seed_matrix(path)

    def test_manifest_arm_order_hash_extra_and_count_fail_closed(self) -> None:
        mutations = (
            lambda value: value["records"][0].__setitem__("arm", "S1_METNOS_SHORT"),
            lambda value: value["records"][0].__setitem__("request_ordinal", 2),
            lambda value: value["records"][0].__setitem__("request_sha256", "0" * 64),
            lambda value: value["records"][0].__setitem__("expected", {}),
            lambda value: value["records"].pop(),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate), temporary_json(MANIFEST_PATH, mutate) as path:
                with self.assertRaises(ProtocolError):
                    load_manifest(path)

    def test_freeze_mutation_and_missing_two_audit_auth_fail(self) -> None:
        with temporary_json(FREEZE_PATH, lambda value: value["local_files"].__setitem__("runner.py", "0" * 64)) as path:
            report = verify_protocol(path, check_runtime_environment=False)
            self.assertGreater(report["error_count"], 0)
        report = verify_protocol(
            authorization_state="armed", authorization_path=Path("/definitely/missing"),
            check_runtime_environment=False,
        )
        self.assertGreater(report["error_count"], 0)


if __name__ == "__main__":
    unittest.main()
