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

import unittest


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

    def test_manifest_projects_typed_result_metadata_with_entries(self):
        """Top-level producer metadata follows the payload only by opt-in."""
        from agent_runtime import resolve_from_step
        roles = [{"field": "a", "roles": ["path", "origin"]}]
        history = self._hist("producer", {
            "ok": True,
            "entries": [{"a": "/source/file"}],
            "entry_field_roles": roles,
        })
        schema = {
            "properties": {
                "entries": {"type": "array"},
                "field_roles": {
                    "type": "array",
                    "from_result_key": "entry_field_roles",
                },
            },
        }

        new_args, errors = resolve_from_step(
            {"from_step": 1}, history, consumer_schema=schema)

        self.assertEqual(errors, [])
        self.assertEqual(new_args["entries"], [{"a": "/source/file"}])
        self.assertEqual(new_args["field_roles"], roles)

    def test_result_metadata_requires_declared_type_and_never_overrides(self):
        from agent_runtime import resolve_from_step
        history = self._hist("producer", {
            "ok": True,
            "entries": [{"a": 1}],
            "metadata": {"unsafe": True},
        })
        schema = {"properties": {
            "entries": {"type": "array"},
            "roles": {"type": "array", "from_result_key": "metadata"},
        }}
        new_args, errors = resolve_from_step(
            {"from_step": 1, "roles": ["explicit"]}, history, schema)
        self.assertEqual(errors, [])
        self.assertEqual(new_args["roles"], ["explicit"])

        inferred, errors = resolve_from_step(
            {"from_step": 1}, history, schema)
        self.assertEqual(errors, [])
        self.assertNotIn("roles", inferred)

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
        from i18n import language_context
        history = self._hist("find_urls", {"ok": True, "entries": []})
        with language_context("it"):
            new_args, errors = resolve_from_step({"from_step": 5}, history)
        self.assertEqual(len(errors), 1)
        self.assertIn("non esiste", errors[0])

    def test_from_step_errors_follow_instance_language(self):
        """Gli errori standard non contengono piu' testo italiano nel codice."""
        from agent_runtime import resolve_from_step
        import i18n

        cases = (
            ({"from_step": "bad"}, [], "intero", "integer"),
            ({"from_step": 2}, self._hist("source", {}),
             "non esiste", "does not exist"),
            ({"from_step": 1}, self._hist("source", []),
             "risultato utilizzabile", "usable result"),
            ({"from_step": 1}, self._hist("source", {"ok": True}),
             "lista utilizzabile", "usable list"),
        )
        original = i18n._C.INSTANCE_LANG
        try:
            for args, history, expected_it, expected_en in cases:
                with self.subTest(args=args, language="it"):
                    i18n._C.INSTANCE_LANG = "it"
                    _new_args, errors = resolve_from_step(args, history)
                    self.assertEqual(len(errors), 1)
                    self.assertIn(expected_it, errors[0])
                with self.subTest(args=args, language="en"):
                    i18n._C.INSTANCE_LANG = "en"
                    _new_args, errors = resolve_from_step(args, history)
                    self.assertEqual(len(errors), 1)
                    self.assertIn(expected_en, errors[0])
        finally:
            i18n._C.INSTANCE_LANG = original

    def test_manifest_declared_target_wins_over_from_step(self):
        """La precedenza sicura deriva dal manifest, non da nomi nel runtime."""
        from agent_runtime import resolve_from_step
        schema = {
            "properties": {
                "record_key": {"type": "string"},
                "from_step": {"type": "integer"},
            },
            "from_step_alternatives": ["record_key"],
        }
        new_args, errors = resolve_from_step(
            {"record_key": "r-1", "from_step": 99}, [], schema)
        self.assertEqual(errors, [])
        self.assertEqual(new_args, {"record_key": "r-1"})

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

    def test_uniform_scalar_context_uses_the_same_projection_as_v3(self):
        from agent_runtime import resolve_from_step
        history = self._hist("read_messages", {
            "ok": True,
            "entries": [
                {"uid": "23", "account": "metnos_system", "folder": "INBOX"},
                {"uid": "22", "account": "metnos_system", "folder": "INBOX"},
            ],
        })
        consumer_schema = {
            "properties": {
                "message_ids": {
                    "type": "array", "from_entries_key": "uid"},
                "account": {
                    "type": "string", "from_entries_key": "account"},
                "src_folder": {
                    "type": "string", "from_entries_key": "folder"},
            },
        }
        new_args, errors = resolve_from_step(
            {"from_step": 1, "dst_folder": "Trash"}, history,
            consumer_schema=consumer_schema)
        self.assertEqual(errors, [])
        self.assertEqual(new_args["message_ids"], ["23", "22"])
        self.assertEqual(new_args["account"], "metnos_system")
        self.assertEqual(new_args["src_folder"], "INBOX")

    def test_required_mixed_scalar_context_is_reported(self):
        from agent_runtime import resolve_from_step
        history = self._hist("read_messages", {
            "ok": True,
            "entries": [
                {"uid": "1", "account": "work", "folder": "INBOX"},
                {"uid": "2", "account": "personal", "folder": "INBOX"},
            ],
        })
        schema = {
            "properties": {
                "message_ids": {
                    "type": "array", "from_entries_key": "uid"},
                "account": {
                    "type": "string", "from_entries_key": "account",
                    "from_entries_required": True},
            },
        }

        new_args, errors = resolve_from_step(
            {"from_step": 1}, history, consumer_schema=schema)

        self.assertEqual(new_args["message_ids"], ["1", "2"])
        self.assertNotIn("account", new_args)
        self.assertEqual(len(errors), 1)
        self.assertIn("account", errors[0])

    def test_complete_vector_projection_rejects_missing_identity(self):
        """Un consumer mutante non riceve un sottoinsieme risolto a meta'."""
        from agent_runtime import resolve_from_step
        history = self._hist("generic_resolver", {
            "ok": True,
            "entries": [
                {"resolved_id": "Vendor.One", "name": "one"},
                {"name": "ambiguous"},
            ],
        })
        schema = {
            "properties": {
                "targets": {
                    "type": "array",
                    "from_entries_key": "resolved_id",
                    "from_entries_complete": True,
                },
            },
        }

        new_args, errors = resolve_from_step(
            {"from_step": 1}, history, consumer_schema=schema)

        self.assertNotIn("targets", new_args)
        self.assertEqual(len(errors), 1)
        self.assertIn("targets", errors[0])

    def test_complete_vector_projection_accepts_all_identities(self):
        from agent_runtime import resolve_from_step
        history = self._hist("generic_resolver", {
            "ok": True,
            "entries": [
                {"resolved_id": "Vendor.One"},
                {"resolved_id": "Vendor.Two"},
            ],
        })
        schema = {
            "properties": {
                "targets": {
                    "type": "array",
                    "from_entries_key": "resolved_id",
                    "from_entries_complete": True,
                },
            },
        }

        new_args, errors = resolve_from_step(
            {"from_step": 1}, history, consumer_schema=schema)

        self.assertEqual(errors, [])
        self.assertEqual(new_args["targets"], ["Vendor.One", "Vendor.Two"])


if __name__ == "__main__":
    unittest.main()
