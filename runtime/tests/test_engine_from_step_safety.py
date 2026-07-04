"""test_engine_from_step_safety — il resolver from_step dell'ENGINE non deve
espandere `entries` quando l'azione ha già un TARGET ESPLICITO.

Regressione dell'incidente live 16/5/2026 (chiuso nel legacy, riaperto quando
l'engine è diventato l'UNICO path): `delete_events(event_id="abc-123",
from_step=1)` non deve trasformarsi in `delete_events(event_id="abc-123",
entries=[…tutta la lista dello step 1…])` → cancellazione troppo larga su un
executor mutante. Il target esplicito vince, from_step viene droppato.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.executor import _resolve_from_step  # noqa: E402
from engine.types import StepRun  # noqa: E402


def _hist(entries):
    return [StepRun(step_idx=1, tool="read_events", args={},
                    result={"ok": True, "entries": entries},
                    ok=True, latency_ms=0)]


class FromStepSafetyTests(unittest.TestCase):
    def setUp(self):
        self.hist = _hist([{"event_id": f"e{i}"} for i in range(9)])

    def test_explicit_event_id_drops_from_step(self):
        out = _resolve_from_step({"event_id": "abc-123", "from_step": 1}, self.hist)
        self.assertEqual(out, {"event_id": "abc-123"})
        self.assertNotIn("entries", out)
        self.assertNotIn("from_step", out)

    def test_explicit_paths_drops_from_step(self):
        out = _resolve_from_step({"paths": ["/tmp/x"], "from_step": 1}, self.hist)
        self.assertEqual(out, {"paths": ["/tmp/x"]})

    def test_explicit_ids_list_drops_from_step(self):
        out = _resolve_from_step({"ids": ["a", "b"], "from_step": 1}, self.hist)
        self.assertEqual(out, {"ids": ["a", "b"]})

    def test_no_explicit_target_expands_normally(self):
        out = _resolve_from_step({"from_step": 1}, self.hist)
        self.assertIn("entries", out)
        self.assertEqual(len(out["entries"]), 9)
        self.assertNotIn("from_step", out)

    def test_empty_target_does_not_block_expansion(self):
        # un target VUOTO ("", [], None) non è un target reale → from_step espande
        out = _resolve_from_step({"event_id": "", "from_step": 1}, self.hist)
        self.assertIn("entries", out)

    def test_string_from_step_coerced(self):
        out = _resolve_from_step({"from_step": "1"}, self.hist)
        self.assertIn("entries", out)
        self.assertEqual(len(out["entries"]), 9)


if __name__ == "__main__":
    unittest.main()
