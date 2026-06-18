"""test_store_entries.py — skill builtin store (find/write/delete_entries) +
gate di dormienza (16/6/2026)."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import store as _store
from store import Schema, TEXT, INT
from backends.datastore.memory import MemoryBackend
import store_entries as se
from engine.routing_pool import _gate_store_skill, _STORE_SKILL_TOOLS

SCHEMA = Schema("spese", {"id": TEXT, "importo": INT}, primary_key=("id",))


class TestStoreEntriesHandlers(unittest.TestCase):
    def setUp(self):
        _store.register(SCHEMA, name="spese", backend=MemoryBackend())

    def tearDown(self):
        _store.unregister("spese")

    def test_write_then_find(self):
        r = se.handle_write_entries(
            {"store": "spese", "entries": [{"id": "a", "importo": 10},
                                           {"id": "b", "importo": 20}]})
        self.assertTrue(r["ok"])
        self.assertEqual(r["n_written"], 2)
        f = se.handle_find_entries({"store": "spese", "order": ["id"]})
        self.assertTrue(f["ok"])
        self.assertEqual([e["id"] for e in f["entries"]], ["a", "b"])

    def test_find_where(self):
        se.handle_write_entries(
            {"store": "spese", "entries": [{"id": "a", "importo": 10},
                                           {"id": "b", "importo": 20}]})
        f = se.handle_find_entries({"store": "spese", "where": {"id": "a"}})
        self.assertEqual(len(f["entries"]), 1)

    def test_delete(self):
        se.handle_write_entries({"store": "spese", "entries": [{"id": "a"}]})
        d = se.handle_delete_entries({"store": "spese", "where": {"id": "a"}})
        self.assertTrue(d["ok"])
        self.assertEqual(d["n_deleted"], 1)
        self.assertEqual(se.handle_find_entries({"store": "spese"})["entries"], [])

    def test_write_set_fields_override(self):
        # set_fields applica un override DETERMINISTICO a OGNI entry prima
        # dell'upsert (FASE 3 "aggiorna a posted"): risolve il §2.8 silent
        # failure (prima set_fields/fields erano ignorati → stato non aggiornato).
        se.handle_write_entries(
            {"store": "spese", "entries": [{"id": "a", "importo": 10},
                                           {"id": "b", "importo": 20}]})
        r = se.handle_write_entries(
            {"store": "spese",
             "entries": [{"id": "a", "importo": 10}, {"id": "b", "importo": 20}],
             "key": ["id"], "set_fields": {"importo": 99}})
        self.assertTrue(r["ok"])
        f = se.handle_find_entries({"store": "spese", "order": ["id"]})
        self.assertEqual([e["importo"] for e in f["entries"]], [99, 99])

    def test_write_fields_alias(self):
        # 'fields' = alias di set_fields (il modello emette entrambe le forme).
        se.handle_write_entries(
            {"store": "spese", "entries": [{"id": "a", "importo": 10}],
             "key": ["id"], "fields": {"importo": 7}})
        f = se.handle_find_entries({"store": "spese", "where": {"id": "a"}})
        self.assertEqual(f["entries"][0]["importo"], 7)

    def test_unregistered_store_honest_error(self):
        r = se.handle_find_entries({"store": "nope_unreg"})
        self.assertFalse(r["ok"])
        self.assertEqual(r["error_class"], "missing_input")
        self.assertIn("non registrato", r["error"])

    def test_missing_store_arg(self):
        r = se.handle_write_entries({"entries": [{"id": "a"}]})
        self.assertFalse(r["ok"])
        self.assertEqual(r["error_class"], "invalid_args")


class TestDormancyGate(unittest.TestCase):
    """Registro vuoto → i 3 store-tool fuori dal pool; ≥1 store → dentro."""

    def tearDown(self):
        for n in ("spese", "altro"):
            if _store.is_registered(n):
                _store.unregister(n)

    def test_excluded_when_registry_empty(self):
        # nessuno store registrato in questo test
        pool = _gate_store_skill(["find_files", "find_entries",
                                  "write_entries", "delete_entries"])
        self.assertEqual(pool, ["find_files"])
        for t in _STORE_SKILL_TOOLS:
            self.assertNotIn(t, pool)

    def test_included_when_a_store_registered(self):
        _store.register(SCHEMA, name="spese", backend=MemoryBackend())
        pool = _gate_store_skill(["find_files", "find_entries", "write_entries"])
        self.assertIn("find_entries", pool)
        self.assertIn("write_entries", pool)
        self.assertIn("find_files", pool)


if __name__ == "__main__":
    unittest.main()
