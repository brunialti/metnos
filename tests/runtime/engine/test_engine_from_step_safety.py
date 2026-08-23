"""test_engine_from_step_safety — il resolver from_step dell'ENGINE non deve
espandere `entries` quando l'azione ha già un TARGET ESPLICITO.

Regressione dell'incidente live 16/5/2026 (chiuso nel legacy, riaperto quando
l'engine è diventato l'UNICO path): `delete_events(event_id="abc-123",
from_step=1)` non deve trasformarsi in `delete_events(event_id="abc-123",
entries=[…tutta la lista dello step 1…])` → cancellazione troppo larga su un
executor mutante. Il target esplicito vince, from_step viene droppato.
"""
from __future__ import annotations

import unittest

from engine.executor import (  # noqa: E402
    _resolve_from_step,
    _resolve_implicit_reverse_target,
)
from engine.types import StepRun  # noqa: E402
from engine.validator import Validator  # noqa: E402
from from_step_projection import (  # noqa: E402
    from_step_alternatives,
    has_explicit_from_step_alternative,
    normalize_from_step_contract,
    required_source_context_fields,
)


TARGET_SCHEMA = {
    "properties": {
        "event_id": {"type": "string"},
        "paths": {"type": "array"},
        "ids": {"type": "array"},
        "entries": {"type": "array"},
    },
    "from_step_alternatives": ["event_id", "paths", "ids", "entries"],
}


def _hist(entries):
    return [StepRun(step_idx=1, tool="read_events", args={},
                    result={"ok": True, "entries": entries},
                    ok=True, latency_ms=0)]


class FromStepSafetyTests(unittest.TestCase):
    def setUp(self):
        self.hist = _hist([{"event_id": f"e{i}"} for i in range(9)])

    def test_explicit_event_id_drops_from_step(self):
        out = _resolve_from_step(
            {"event_id": "abc-123", "from_step": 1}, self.hist,
            TARGET_SCHEMA)
        self.assertEqual(out, {"event_id": "abc-123"})
        self.assertNotIn("entries", out)
        self.assertNotIn("from_step", out)

    def test_explicit_paths_drops_from_step(self):
        out = _resolve_from_step(
            {"paths": ["/tmp/x"], "from_step": 1}, self.hist,
            TARGET_SCHEMA)
        self.assertEqual(out, {"paths": ["/tmp/x"]})

    def test_explicit_ids_list_drops_from_step(self):
        out = _resolve_from_step(
            {"ids": ["a", "b"], "from_step": 1}, self.hist,
            TARGET_SCHEMA)
        self.assertEqual(out, {"ids": ["a", "b"]})

    def test_no_explicit_target_expands_normally(self):
        out = _resolve_from_step({"from_step": 1}, self.hist)
        self.assertIn("entries", out)
        self.assertEqual(len(out["entries"]), 9)
        self.assertNotIn("from_step", out)

    def test_empty_target_does_not_block_expansion(self):
        # un target VUOTO ("", [], None) non è un target reale → from_step espande
        out = _resolve_from_step(
            {"event_id": "", "from_step": 1}, self.hist,
            TARGET_SCHEMA)
        self.assertIn("entries", out)

    def test_alternatives_are_manifest_driven(self):
        schema = {
            "properties": {
                "items": {"type": "array"},
                "entries": {"type": "array"},
                "destination": {"type": "string"},
            },
            "requires_one_of": [["items", "entries", "from_step"]],
        }
        self.assertEqual(from_step_alternatives(schema), ("items", "entries"))
        self.assertTrue(has_explicit_from_step_alternative(
            {"items": ["a"], "from_step": 1}, schema))
        self.assertFalse(has_explicit_from_step_alternative(
            {"destination": "/tmp/out", "from_step": 1}, schema))

    def test_manifest_override_excludes_destination_metadata(self):
        schema = {
            "properties": {
                "values": {"type": "array"},
                "entries": {"type": "array"},
                "title": {"type": "string"},
            },
            "requires_one_of": [
                ["values", "entries", "from_step", "title"],
            ],
            "from_step_alternatives": ["values", "entries"],
        }
        self.assertFalse(has_explicit_from_step_alternative(
            {"title": "output", "from_step": 1}, schema))
        self.assertTrue(has_explicit_from_step_alternative(
            {"values": [[1]], "from_step": 1}, schema))

    def test_manifest_alternatives_compile_to_required_source_group(self):
        normalized = normalize_from_step_contract(TARGET_SCHEMA)
        self.assertIn(
            ["event_id", "paths", "ids", "entries", "from_step"],
            normalized["requires_one_of"],
        )

    def test_broader_manifest_source_group_remains_authoritative(self):
        schema = {
            "properties": {
                "values": {"type": "array"},
                "entries": {"type": "array"},
                "title": {"type": "string"},
            },
            "requires_one_of": [
                ["values", "entries", "from_step", "title"],
            ],
            "from_step_alternatives": ["values", "entries"],
        }
        self.assertEqual(
            normalize_from_step_contract(schema)["requires_one_of"],
            schema["requires_one_of"],
        )

    def test_loaded_delete_contract_rejects_missing_target_and_pipe(self):
        from loader import load_catalog

        executor = next(
            item for item in load_catalog(verify=True, include_synth=False)
            if item.name == "delete_events")
        schema = executor.args_schema
        alternatives = set(from_step_alternatives(schema))
        self.assertEqual(
            alternatives,
            {"event_id", "event_ids", "entries", "time_window"},
        )
        self.assertIn("from_step", schema["properties"])
        self.assertIsNotNone(Validator._check_args({}, schema))
        self.assertIsNone(Validator._check_args({"from_step": 1}, schema))

    def test_boolean_alternative_must_be_selected(self):
        schema = {
            "properties": {
                "all": {"type": "boolean"},
                "from_step": {"type": "integer"},
            },
            "from_step_alternatives": ["all"],
        }
        self.assertFalse(has_explicit_from_step_alternative(
            {"all": False, "from_step": 1}, schema))
        self.assertTrue(has_explicit_from_step_alternative(
            {"all": True, "from_step": 1}, schema))

    def test_string_from_step_coerced(self):
        out = _resolve_from_step({"from_step": "1"}, self.hist)
        self.assertIn("entries", out)
        self.assertEqual(len(out["entries"]), 9)

    def test_inverse_target_comes_from_standard_undo_envelope(self):
        history = [StepRun(
            step_idx=1,
            tool="create_events",
            args={},
            result={
                "ok": True,
                "_undo": {
                    "reverse_pattern": "delete_events_by_id",
                    "ids": ["evt-1"],
                    "scope": {"calendar_id": "primary", "client": "local"},
                },
            },
            ok=True,
            latency_ms=0,
        )]
        schema = {
            "properties": {
                "event_ids": {"type": "array"},
                "calendar_id": {"type": "string"},
                "client": {"type": "string"},
                "from_step": {"type": "integer"},
            },
            "from_step_alternatives": ["event_ids"],
        }
        self.assertEqual(
            _resolve_implicit_reverse_target(
                "delete_events", {"client": "local"}, history, schema,
            ),
            {
                "client": "local",
                "event_ids": ["evt-1"],
                "calendar_id": "primary",
            },
        )

    def test_explicit_inverse_target_is_never_replaced(self):
        history = [StepRun(
            step_idx=1,
            tool="create_events",
            args={},
            result={
                "_undo": {
                    "reverse_pattern": "delete_events_by_id",
                    "ids": ["evt-created"],
                },
            },
            ok=True,
            latency_ms=0,
        )]
        schema = {
            "properties": {"event_ids": {"type": "array"}},
            "from_step_alternatives": ["event_ids"],
        }
        explicit = {"event_ids": ["evt-explicit"]}
        self.assertEqual(
            _resolve_implicit_reverse_target(
                "delete_events", explicit, history, schema,
            ),
            explicit,
        )

    def test_unresolved_inverse_placeholder_is_replaced_by_undo_receipt(self):
        history = [StepRun(
            step_idx=1,
            tool="create_events",
            args={},
            result={
                "ok": True,
                "_undo": {
                    "reverse_pattern": "delete_events_by_id",
                    "ids": ["evt-created"],
                    "scope": {
                        "calendar_id": "primary", "client": "local",
                    },
                },
            },
            ok=True,
            latency_ms=0,
        )]
        schema = {
            "properties": {
                "event_ids": {"type": "array"},
                "calendar_id": {"type": "string"},
                "client": {"type": "string"},
            },
            "from_step_alternatives": ["event_ids"],
        }

        self.assertEqual(
            _resolve_implicit_reverse_target(
                "delete_events",
                {
                    "event_ids": ["${step1.results.*.id}"],
                    "client": "local",
                },
                history,
                schema,
            ),
            {
                "event_ids": ["evt-created"],
                "calendar_id": "primary",
                "client": "local",
            },
        )

    def test_unresolved_inverse_placeholder_survives_without_receipt(self):
        history = [StepRun(
            step_idx=1, tool="create_events", args={},
            result={"ok": True}, ok=True, latency_ms=0,
        )]
        schema = {
            "properties": {"event_ids": {"type": "array"}},
            "from_step_alternatives": ["event_ids"],
        }
        args = {"event_ids": ["${step1.results.*.id}"]}

        self.assertEqual(
            _resolve_implicit_reverse_target(
                "delete_events", args, history, schema),
            args,
        )

    def test_undo_contract_cannot_target_a_different_executor(self):
        history = [StepRun(
            step_idx=1,
            tool="create_events",
            args={},
            result={
                "_undo": {
                    "reverse_pattern": "delete_events_by_id",
                    "ids": ["evt-1"],
                },
            },
            ok=True,
            latency_ms=0,
        )]
        self.assertEqual(
            _resolve_implicit_reverse_target(
                "delete_contacts", {}, history, {"properties": {}},
            ),
            {},
        )

    def test_failed_or_unschematized_result_cannot_supply_reverse_target(self):
        history = [StepRun(
            step_idx=1,
            tool="create_events",
            args={},
            result={
                "_undo": {
                    "reverse_pattern": "delete_events_by_id",
                    "ids": ["evt-1"],
                },
            },
            ok=False,
            latency_ms=0,
        )]
        self.assertEqual(
            _resolve_implicit_reverse_target(
                "delete_events", {}, history,
                {"properties": {"event_ids": {"type": "array"}}},
            ),
            {},
        )
        history[0].ok = True
        self.assertEqual(
            _resolve_implicit_reverse_target(
                "delete_events", {}, history, {"properties": {}},
            ),
            {},
        )

    def test_manifest_projects_vector_and_uniform_scalar_context(self):
        history = _hist([
            {"uid": "23", "account": "metnos_system", "folder": "INBOX"},
            {"uid": "22", "account": "metnos_system", "folder": "INBOX"},
        ])
        schema = {
            "properties": {
                "message_ids": {
                    "type": "array", "from_entries_key": "uid"},
                "account": {
                    "type": "string", "from_entries_key": "account"},
                "src_folder": {
                    "type": "string", "from_entries_key": "folder"},
            },
        }
        out = _resolve_from_step(
            {"from_step": 1, "dst_folder": "Trash"}, history, schema)
        self.assertEqual(out["message_ids"], ["23", "22"])
        self.assertEqual(out["account"], "metnos_system")
        self.assertEqual(out["src_folder"], "INBOX")

    def test_manifest_projects_top_level_typed_metadata(self):
        roles = [{"field": "path", "roles": ["path", "duplicate"]}]
        history = [StepRun(
            step_idx=1, tool="producer", args={}, ok=True, latency_ms=0,
            result={
                "ok": True,
                "entries": [{"path": "/copy/a"}],
                "entry_field_roles": roles,
            })]
        schema = {"properties": {
            "entries": {"type": "array"},
            "field_roles": {
                "type": "array",
                "from_result_key": "entry_field_roles",
            },
        }}

        out = _resolve_from_step({"from_step": 1}, history, schema)

        self.assertEqual(out["entries"], [{"path": "/copy/a"}])
        self.assertEqual(out["field_roles"], roles)

    def test_heterogeneous_scalar_context_is_never_guessed(self):
        history = _hist([
            {"uid": "1", "account": "work"},
            {"uid": "2", "account": "personal"},
        ])
        schema = {
            "properties": {
                "message_ids": {
                    "type": "array", "from_entries_key": "uid"},
                "account": {
                    "type": "string", "from_entries_key": "account"},
            },
        }
        out = _resolve_from_step({"from_step": 1}, history, schema)
        self.assertEqual(out["message_ids"], ["1", "2"])
        self.assertNotIn("account", out)

    def test_required_heterogeneous_context_fails_before_consumer(self):
        from types import SimpleNamespace
        from engine.executor import Executor
        from engine.types import Framework, StepSpec

        calls = []

        def invoke(tool, args):
            calls.append((tool, args))
            if tool == "read_messages":
                return {
                    "ok": True,
                    "entries": [
                        {"uid": "1", "account": "work", "folder": "INBOX"},
                        {"uid": "2", "account": "personal", "folder": "INBOX"},
                    ],
                }
            raise AssertionError("ambiguous consumer must not be invoked")

        catalog = [
            SimpleNamespace(name="read_messages", args_schema={}),
            SimpleNamespace(name="move_messages", args_schema={
                "properties": {
                    "message_ids": {
                        "type": "array", "from_entries_key": "uid"},
                    "account": {
                        "type": "string", "from_entries_key": "account",
                        "from_entries_required": True},
                    "src_folder": {
                        "type": "string", "from_entries_key": "folder",
                        "from_entries_required": True},
                    "dst_folder": {"type": "string"},
                },
            }),
        ]
        framework = Framework(steps=[
            StepSpec(tool="read_messages", args={}),
            StepSpec(tool="move_messages", args={
                "from_step": 1, "dst_folder": "Trash"}),
        ])

        run = Executor(invoke_executor=invoke, catalog=catalog).run(framework)

        self.assertEqual([tool for tool, _args in calls], ["read_messages"])
        self.assertEqual(
            run.steps[-1].result["error_code"],
            "ERR_FROM_STEP_CONTEXT_AMBIGUOUS",
        )

    @staticmethod
    def _conditional_context_schema():
        return {
            "requires_one_of": [["message_ids", "message_id", "from_step"]],
            "properties": {
                "message_ids": {
                    "type": "array", "from_entries_key": "uid"},
                "message_id": {"type": "string"},
                "account": {
                    "type": "string", "from_entries_key": "account",
                    "from_entries_required": {
                        "arg": "client", "values": ["metnos"]}},
                "src_folder": {
                    "type": "string", "from_entries_key": "folder",
                    "from_entries_required": {
                        "arg": "client", "values": ["metnos"]}},
                "client": {
                    "type": "string", "default": "metnos"},
            },
        }

    def test_inline_source_ids_require_manifest_declared_context(self):
        schema = self._conditional_context_schema()
        missing = required_source_context_fields(
            {"message_ids": ["17"]}, schema)
        self.assertEqual(missing, ["account", "src_folder"])

    def test_provider_condition_can_disable_source_context_requirement(self):
        schema = self._conditional_context_schema()
        missing = required_source_context_fields({
            "message_ids": ["g-id"], "client": "google_workspace",
        }, schema)
        self.assertEqual(missing, [])

    def test_prevalidation_defers_context_to_from_step_projection(self):
        schema = self._conditional_context_schema()
        missing = required_source_context_fields(
            {"from_step": 1}, schema, allow_deferred_from_step=True)
        self.assertEqual(missing, [])

    def test_invocation_chokepoint_rejects_unbound_identifiers(self):
        from types import SimpleNamespace
        from agent_runtime import _invoke_executor_impl

        executor = SimpleNamespace(
            name="generic_context_bound_operation",
            args_schema=self._conditional_context_schema(),
        )
        result = _invoke_executor_impl(executor, {
            "message_ids": ["17"], "dst_folder": "Trash",
        })

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"],
                         "ERR_SOURCE_CONTEXT_REQUIRED")
        self.assertEqual(result["context_fields"],
                         ["account", "src_folder"])


if __name__ == "__main__":
    unittest.main()
