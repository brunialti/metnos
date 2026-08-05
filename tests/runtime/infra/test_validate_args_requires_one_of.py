"""Test schema validation `requires_one_of` (§7.3 universale, 25/5/2026).

Pattern dichiarativo: il manifest puo' esprimere vincoli del tipo
«almeno uno fra X, Y, Z deve essere non-vuoto» senza prompt teaching
ad-hoc nella description. Runtime `validate_args` enforce-a il vincolo
generando un fail message standard che il PLANNER puo' usare come hint.

Bug live find_images_web (turn 93645ef1, 25/5/2026): paths=[]/urls=[]
emessi dal PLANNER senza from_step. Senza requires_one_of, l'executor
veniva eseguito comunque e ritornava invalid_args runtime con un
messaggio inconsistente dal backend. Con il vincolo dichiarativo, il
runtime rifiuta PRIMA dell'esecuzione con messaggio chiaro.

Run: python3 -m pytest tests/runtime/infra/test_validate_args_requires_one_of.py -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


def _schema_with_one_of(*keys: str) -> dict:
    """Helper: schema base con requires_one_of=[[keys...]]."""
    props = {k: {"type": "array", "default": []} for k in keys}
    return {
        "type": "object",
        "required": [],
        "requires_one_of": [list(keys)],
        "properties": props,
    }


class TestRequiresOneOf(unittest.TestCase):
    def test_all_empty_fails(self):
        from agent_runtime import validate_args
        schema = _schema_with_one_of("paths", "urls", "from_step")
        failures = validate_args(
            {"paths": [], "urls": []},
            schema,
        )
        self.assertEqual(len(failures), 1)
        self.assertIn("requires one of", failures[0])
        self.assertIn("paths", failures[0])
        self.assertIn("urls", failures[0])
        self.assertIn("from_step", failures[0])

    def test_paths_nonempty_ok(self):
        from agent_runtime import validate_args
        schema = _schema_with_one_of("paths", "urls", "from_step")
        self.assertEqual(
            validate_args({"paths": ["/x.jpg"]}, schema), [])

    def test_urls_nonempty_ok(self):
        from agent_runtime import validate_args
        schema = _schema_with_one_of("paths", "urls", "from_step")
        self.assertEqual(
            validate_args({"urls": ["https://x"]}, schema), [])

    def test_from_step_int_ok(self):
        from agent_runtime import validate_args
        schema = _schema_with_one_of("paths", "urls", "from_step")
        self.assertEqual(
            validate_args({"from_step": 1}, schema), [])

    def test_from_step_zero_is_not_provided(self):
        """from_step=0 e' placeholder convenzionale 'no limit' → non vale."""
        from agent_runtime import validate_args
        schema = _schema_with_one_of("paths", "urls", "from_step")
        failures = validate_args(
            {"from_step": 0, "paths": [], "urls": []},
            schema,
        )
        self.assertEqual(len(failures), 1)

    def test_entries_satisfies_from_step(self):
        """`from_step` viene risolto a `entries` upstream: se entries c'e',
        il vincolo e' soddisfatto."""
        from agent_runtime import validate_args
        schema = _schema_with_one_of("paths", "urls", "from_step")
        self.assertEqual(
            validate_args({"entries": [{"path": "/x.jpg"}]}, schema), [])

    def test_multiple_groups(self):
        """Schema con piu' gruppi disgiuntivi: ciascuno deve essere soddisfatto."""
        from agent_runtime import validate_args
        schema = {
            "type": "object", "required": [],
            "requires_one_of": [["a", "b"], ["c", "d"]],
            "properties": {},
        }
        # gruppo 1 ok, gruppo 2 vuoto → fail
        failures = validate_args({"a": "x"}, schema)
        self.assertEqual(len(failures), 1)
        self.assertIn("['c', 'd']", failures[0])

    def test_legacy_schema_no_constraint(self):
        """Schema senza requires_one_of → validator non aggiunge fail."""
        from agent_runtime import validate_args
        schema = {"type": "object", "required": [], "properties": {}}
        self.assertEqual(validate_args({}, schema), [])

    def test_empty_string_does_not_satisfy(self):
        from agent_runtime import validate_args
        schema = {
            "type": "object", "required": [],
            "requires_one_of": [["query"]],
            "properties": {"query": {"type": "string"}},
        }
        failures = validate_args({"query": "  "}, schema)
        self.assertEqual(len(failures), 1)
        # con valore non-blank passa
        self.assertEqual(validate_args({"query": "abc"}, schema), [])


if __name__ == "__main__":
    unittest.main()
