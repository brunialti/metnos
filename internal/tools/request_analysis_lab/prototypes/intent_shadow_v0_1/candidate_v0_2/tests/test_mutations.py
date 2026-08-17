from __future__ import annotations

from copy import deepcopy
import unittest

from ..api import compile_ir
from ..canonical import canonical_ir_json_bytes, canonical_sha256, strict_json_loads
from ..compiler import semantic_projection
from ..registry_projection import (
    RegistryProjectionError,
    load_projection,
    validate_projection,
    validate_projection_against_source,
)


def _resign(projection: dict) -> dict:
    value = deepcopy(projection)
    value["integrity"]["projection_payload_sha256"] = ""
    payload = deepcopy(value)
    payload["integrity"].pop("projection_payload_sha256", None)
    value["integrity"]["projection_payload_sha256"] = canonical_sha256(payload)
    return value


class MutationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_projection()
        cls.route = next(iter(cls.registry["operations"]))

    def assertRejected(self, value):
        result = compile_ir(value, self.registry)
        self.assertFalse(result.valid, value)
        self.assertTrue(result.issues)

    def test_d01_duplicate_keys(self):
        for raw in (
            b'{"kind":"system_control","kind":"unrepresentable","control":"undo_last_turn"}',
            b'{"kind":"operation_graph","steps":[{"route":"change/files","route":"change/pulls"}]}',
        ):
            result = compile_ir(raw, self.registry)
            self.assertFalse(result.valid)
            self.assertEqual(result.issues[0].code, "D01_DUPLICATE_KEY")

    def test_d02_nonfinite_and_overflow(self):
        huge_integer = b"1" * 4301
        for token in (b"NaN", b"Infinity", b"-Infinity", b"1e999", b"-1e999", huge_integer):
            raw = b'{"kind":"operation_graph","steps":[{"route":"' + self.route.encode() + b'","from":[' + token + b']}]}'
            result = compile_ir(raw, self.registry)
            self.assertFalse(result.valid)
            self.assertEqual(result.issues[0].code, "D02_NONFINITE")

    def test_d03_exact_types_and_closed_objects(self):
        values = [
            [],
            {"kind": "operation_graph", "steps": []},
            {"kind": "operation_graph", "steps": [{"route": self.route, "from": [True]}]},
            {"kind": "operation_graph", "steps": [{"route": self.route, "from": [0.0]}]},
            {"kind": "operation_graph", "steps": [{"route": self.route, "extra": 1}]},
            {"kind": "system_control", "control": "undo_last_turn", "inputs": {}},
            {"kind": "unrepresentable", "reason": "outside_registry", "route": self.route},
        ]
        for value in values:
            self.assertRejected(value)

    def test_root_key_order_is_not_semantic_and_serializer_puts_kind_first(self):
        pairs = (
            (
                b'{"kind":"operation_graph","steps":[{"route":"change/files"}]}',
                b'{"steps":[{"route":"change/files"}],"kind":"operation_graph"}',
            ),
            (
                b'{"kind":"system_control","control":"undo_last_turn"}',
                b'{"control":"undo_last_turn","kind":"system_control"}',
            ),
            (
                b'{"kind":"unrepresentable","reason":"outside_registry"}',
                b'{"reason":"outside_registry","kind":"unrepresentable"}',
            ),
        )
        for kind_first, kind_last in pairs:
            first_result = compile_ir(kind_first, self.registry)
            last_result = compile_ir(kind_last, self.registry)
            self.assertTrue(first_result.valid, first_result.issues)
            self.assertTrue(last_result.valid, last_result.issues)
            self.assertEqual(first_result.document, last_result.document)
            self.assertEqual(first_result.document_sha256, last_result.document_sha256)
            self.assertTrue(canonical_ir_json_bytes(strict_json_loads(kind_last)).startswith(b'{"kind":'))

    def test_d04_from_is_unique_and_order_insensitive(self):
        route = self.route
        duplicate = {
            "kind": "operation_graph",
            "steps": [{"route": route}, {"route": route, "from": [0, 0]}],
        }
        self.assertRejected(duplicate)
        value_a = {
            "kind": "operation_graph",
            "steps": [
                {"route": route},
                {"route": route},
                {"route": route, "from": [1, 0]},
            ],
        }
        value_b = deepcopy(value_a)
        value_b["steps"][2]["from"] = [0, 1]
        result_a = compile_ir(value_a, self.registry)
        result_b = compile_ir(value_b, self.registry)
        self.assertTrue(result_a.valid)
        self.assertEqual(result_a.document_sha256, result_b.document_sha256)

    def test_d04_registry_lists_and_source_binding(self):
        multi_route = next(
            name for name, metadata in self.registry["operations"].items()
            if len(metadata["source_executors"]) > 1
        )
        reordered = deepcopy(self.registry)
        reordered["operations"][multi_route]["source_executors"].reverse()
        reordered = _resign(reordered)
        with self.assertRaises(RegistryProjectionError):
            validate_projection(reordered)

        duplicated = deepcopy(self.registry)
        first = duplicated["operations"][multi_route]["source_executors"][0]
        duplicated["operations"][multi_route]["source_executors"].append(first)
        duplicated = _resign(duplicated)
        with self.assertRaises(RegistryProjectionError):
            validate_projection(duplicated)

        deleted = deepcopy(self.registry)
        deleted["operations"].pop(multi_route)
        deleted = _resign(deleted)
        validate_projection(deleted)
        with self.assertRaises(RegistryProjectionError):
            validate_projection_against_source(deleted)

    def test_root_route_and_authority_mutations(self):
        for value in (
            {"kind": "unknown"},
            {"kind": "operation_graph", "steps": [{"route": "unknown/route"}]},
            {"kind": "system_control", "control": "unknown"},
            {"kind": "unrepresentable", "reason": "unknown"},
            {"kind": "operation_graph", "steps": [{"barrier": "unknown", "body": [{"route": self.route}]}]},
        ):
            self.assertRejected(value)

    def test_self_forward_and_cycle_are_rejected(self):
        self.assertRejected({"kind": "operation_graph", "steps": [{"route": self.route, "from": [0]}]})
        self.assertRejected({"kind": "operation_graph", "steps": [{"route": self.route, "from": [1]}, {"route": self.route}]})
        self.assertRejected({"kind": "operation_graph", "steps": [{"route": self.route, "from": [1]}, {"route": self.route, "from": [0]}]})

    def test_barrier_scope_blocks_branch_escape(self):
        barrier = next(iter(self.registry["barriers"]))
        value = {
            "kind": "operation_graph",
            "steps": [
                {"barrier": barrier, "body": [{"route": self.route}]},
                {"route": self.route, "from": [0]},
            ],
        }
        result = compile_ir(value, self.registry)
        self.assertFalse(result.valid)
        self.assertIn("FROM_OUT_OF_SCOPE", {issue.code for issue in result.issues})

    def test_outer_source_is_visible_inside_barrier(self):
        barrier = next(iter(self.registry["barriers"]))
        value = {
            "kind": "operation_graph",
            "steps": [
                {"route": self.route},
                {"barrier": barrier, "body": [{"route": self.route, "from": [0]}]},
            ],
        }
        result = compile_ir(value, self.registry)
        self.assertTrue(result.valid, result.issues)

    def test_model_cannot_emit_derived_fields(self):
        forbidden = ("input", "output", "path", "node_path", "ordinal", "outcome", "continuation")
        for key in forbidden:
            value = {
                "kind": "operation_graph",
                "steps": [{"route": self.route, key: "arbitrary"}],
            }
            self.assertRejected(value)

    def test_barrier_extra_cases_and_empty_body_rejected(self):
        barrier = next(iter(self.registry["barriers"]))
        for value in (
            {"kind": "operation_graph", "steps": [{"barrier": barrier, "body": []}]},
            {"kind": "operation_graph", "steps": [{"barrier": barrier, "body": [{"route": self.route}], "cases": []}]},
            {"kind": "operation_graph", "steps": [{"barrier": barrier, "body": [{"route": self.route}], "outcome": "approved"}]},
        ):
            self.assertRejected(value)

    def test_ambiguous_ports_fail_closed(self):
        changed = deepcopy(self.registry)
        changed["operations"][self.route]["input_ports"] = ["a", "b"]
        changed = _resign(changed)
        value = {
            "kind": "operation_graph",
            "steps": [{"route": self.route}, {"route": self.route, "from": [0]}],
        }
        result = compile_ir(value, changed)
        self.assertFalse(result.valid)
        self.assertIn("INPUT_PORT_AMBIGUOUS", {issue.code for issue in result.issues})


if __name__ == "__main__":
    unittest.main()
