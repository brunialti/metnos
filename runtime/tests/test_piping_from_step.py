"""Test del piping `from_step` → arg consumer auto-espansione (5/5/2026, Bug A).

Caso live: turn 23be1548 ha mostrato che read_urls_html(from_step=2) FALLISCE
con `validation failed: ["missing required arg 'urls'"]` perche' resolve_from_step
inietta sotto la chiave `entries`, ma read_urls_html richiede `urls`.

Fix strutturale Layer 4 (simmetrico a Layer 3 consumer-match in adaptive_rerank):
quando il consumer NON espone `entries` ma ha un arg array singolare-aggettivo
matchabile su un campo prodotto da entries[0] (es. arg `urls` ↔ campo `url`),
il runtime estrae i valori e li inietta sotto quell'arg.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


class TestPipingFromStep(unittest.TestCase):
    def _hist(self, tool: str, observation: dict) -> list:
        """Costruisce un singolo step di history come usato in run_turn."""
        return [{"step": 1, "tool": tool, "args": {}, "observation": observation}]

    def test_url_consumer_match_from_entries(self):
        """find_urls produce entries=[{url,...}], read_urls_html consuma urls=[...]."""
        from agent_runtime import resolve_from_step
        history = self._hist("find_urls", {
            "ok": True,
            "entries": [
                {"url": "https://a.example.org/p1", "title": "A"},
                {"url": "https://b.example.org/p2", "title": "B"},
            ],
        })
        consumer_schema = {
            "type": "object",
            "required": ["urls"],
            "properties": {
                "urls": {"type": "array"},
                "timeout_s": {"type": "number"},
            },
        }
        new_args, errors = resolve_from_step({"from_step": 1}, history,
                                              consumer_schema=consumer_schema)
        self.assertEqual(errors, [])
        self.assertEqual(new_args.get("urls"),
                         ["https://a.example.org/p1", "https://b.example.org/p2"])
        self.assertNotIn("from_step", new_args)
        self.assertNotIn("entries", new_args)

    def test_paths_consumer_match_from_entries(self):
        """find_files produce entries=[{path,...}], read_files_csv consuma paths."""
        from agent_runtime import resolve_from_step
        history = self._hist("find_files", {
            "ok": True,
            "entries": [
                {"path": "/tmp/a.csv", "size": 100},
                {"path": "/tmp/b.csv", "size": 200},
            ],
        })
        consumer_schema = {
            "type": "object",
            "required": ["paths"],
            "properties": {"paths": {"type": "array"}},
        }
        new_args, errors = resolve_from_step({"from_step": 1}, history,
                                              consumer_schema=consumer_schema)
        self.assertEqual(errors, [])
        self.assertEqual(new_args.get("paths"), ["/tmp/a.csv", "/tmp/b.csv"])
        self.assertNotIn("entries", new_args)

    def test_entries_consumer_uses_entries_target(self):
        """Schema con entries (legacy) → comportamento storico, inietta entries."""
        from agent_runtime import resolve_from_step
        history = self._hist("find_files", {
            "ok": True,
            "entries": [{"path": "/x"}, {"path": "/y"}],
        })
        consumer_schema = {
            "type": "object",
            "required": ["entries"],
            "properties": {"entries": {"type": "array"}},
        }
        new_args, errors = resolve_from_step({"from_step": 1}, history,
                                              consumer_schema=consumer_schema)
        self.assertEqual(errors, [])
        self.assertEqual(new_args.get("entries"),
                         [{"path": "/x"}, {"path": "/y"}])

    def test_empty_entries_returns_empty_array(self):
        """entries=[] vuote → arg consumer = []."""
        from agent_runtime import resolve_from_step
        history = self._hist("find_urls", {"ok": True, "entries": []})
        consumer_schema = {
            "type": "object",
            "required": ["urls"],
            "properties": {"urls": {"type": "array"}},
        }
        new_args, errors = resolve_from_step({"from_step": 1}, history,
                                              consumer_schema=consumer_schema)
        self.assertEqual(errors, [])
        self.assertEqual(new_args.get("urls"), [])

    def test_entries_with_missing_field_skipped(self):
        """entries con campo url mancante → skip, solo i validi nell'output."""
        from agent_runtime import resolve_from_step
        history = self._hist("find_urls", {
            "ok": True,
            "entries": [
                {"url": "https://a.example.org/p1"},
                {"title": "no url here"},  # missing url
                {"url": "https://b.example.org/p2"},
            ],
        })
        consumer_schema = {
            "type": "object",
            "required": ["urls"],
            "properties": {"urls": {"type": "array"}},
        }
        new_args, errors = resolve_from_step({"from_step": 1}, history,
                                              consumer_schema=consumer_schema)
        self.assertEqual(errors, [])
        self.assertEqual(new_args.get("urls"),
                         ["https://a.example.org/p1", "https://b.example.org/p2"])

    def test_no_consumer_match_falls_back_to_entries(self):
        """Schema senza arg matchabile → fallback storico (inietta entries)."""
        from agent_runtime import resolve_from_step
        history = self._hist("find_urls", {
            "ok": True,
            "entries": [{"url": "https://x"}],
        })
        # Schema con arg che non hanno alcun match (no `urls`, no `entries`)
        consumer_schema = {
            "type": "object",
            "required": ["foo"],
            "properties": {"foo": {"type": "string"}},
        }
        new_args, errors = resolve_from_step({"from_step": 1}, history,
                                              consumer_schema=consumer_schema)
        self.assertEqual(errors, [])
        # Fallback: inietta entries (legacy behavior)
        self.assertEqual(new_args.get("entries"), [{"url": "https://x"}])

    def test_no_schema_fallback_to_entries(self):
        """consumer_schema=None → fallback storico (inietta entries)."""
        from agent_runtime import resolve_from_step
        history = self._hist("find_urls", {
            "ok": True,
            "entries": [{"url": "https://x"}],
        })
        new_args, errors = resolve_from_step({"from_step": 1}, history,
                                              consumer_schema=None)
        self.assertEqual(errors, [])
        self.assertEqual(new_args.get("entries"), [{"url": "https://x"}])

    def test_from_step_invalid_step_index(self):
        """from_step fuori range → errore istruttivo."""
        from agent_runtime import resolve_from_step
        history = self._hist("find_urls", {"ok": True, "entries": []})
        new_args, errors = resolve_from_step({"from_step": 5}, history)
        self.assertEqual(len(errors), 1)
        self.assertIn("inesistente", errors[0])

    def test_from_step_string_int_coercion(self):
        """from_step come stringa numerica coercito a int."""
        from agent_runtime import resolve_from_step
        history = self._hist("find_urls", {
            "ok": True,
            "entries": [{"url": "https://a"}],
        })
        consumer_schema = {
            "type": "object",
            "required": ["urls"],
            "properties": {"urls": {"type": "array"}},
        }
        new_args, errors = resolve_from_step({"from_step": "1"}, history,
                                              consumer_schema=consumer_schema)
        self.assertEqual(errors, [])
        self.assertEqual(new_args.get("urls"), ["https://a"])

    def test_signatures_consumer_match(self):
        """get_signatures produce entries=[{signature,...}], hypothetical
        consumer arg `signatures` → match singular."""
        from agent_runtime import resolve_from_step
        history = self._hist("get_signatures", {
            "ok": True,
            "entries": [
                {"signature": "fs.read:/tmp/x"},
                {"signature": "net.read:https://y"},
            ],
        })
        consumer_schema = {
            "type": "object",
            "required": ["signatures"],
            "properties": {"signatures": {"type": "array"}},
        }
        new_args, errors = resolve_from_step({"from_step": 1}, history,
                                              consumer_schema=consumer_schema)
        self.assertEqual(errors, [])
        self.assertEqual(new_args.get("signatures"),
                         ["fs.read:/tmp/x", "net.read:https://y"])


if __name__ == "__main__":
    unittest.main()
