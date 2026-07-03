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

    # --- n_new / was_new (fix bug live 3/7, §2.8) --------------------------
    # Bug reale: "trova issue GitHub, salva quelle nuove, dimmi quante nuove"
    # rilanciato due volte diceva "1 nuova" ENTRAMBE le volte — la pipeline
    # contava n_written (un upsert conta comunque), mai un confronto col
    # "prima". Qui il gemello minimale del bug: stesso record riscritto.

    def test_first_write_all_new(self):
        r = se.handle_write_entries(
            {"store": "spese", "key": ["id"],
             "entries": [{"id": "a", "importo": 10}, {"id": "b", "importo": 20}]})
        self.assertEqual(r["n_written"], 2)
        self.assertEqual(r["n_new"], 2)
        self.assertEqual(r["n_updated"], 0)
        self.assertEqual([x["was_new"] for x in r["results"]], [True, True])

    def test_second_write_same_record_zero_new(self):
        # ESATTAMENTE il bug live: stessa entry riscritta -> n_written=1
        # (upsert avvenuto) ma n_new DEVE essere 0 (era gia' presente).
        se.handle_write_entries(
            {"store": "spese", "key": ["id"],
             "entries": [{"id": "a", "importo": 10}]})
        r2 = se.handle_write_entries(
            {"store": "spese", "key": ["id"],
             "entries": [{"id": "a", "importo": 10}]})
        self.assertEqual(r2["n_written"], 1)
        self.assertEqual(r2["n_new"], 0)
        self.assertEqual(r2["n_updated"], 1)
        self.assertEqual(r2["results"], [{"written": True, "was_new": False}])

    def test_mixed_new_and_existing(self):
        se.handle_write_entries(
            {"store": "spese", "key": ["id"], "entries": [{"id": "a", "importo": 1}]})
        r = se.handle_write_entries(
            {"store": "spese", "key": ["id"],
             "entries": [{"id": "a", "importo": 99}, {"id": "c", "importo": 5}]})
        self.assertEqual(r["n_written"], 2)
        self.assertEqual(r["n_new"], 1)   # solo "c" e' nuovo
        self.assertEqual(r["n_updated"], 1)

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
