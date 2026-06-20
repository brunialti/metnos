"""test_store.py — store generico multi-backend (16/6/2026).

Il cuore: la STESSA suite CRUD gira su SqliteBackend E MemoryBackend → prova
che il contratto `Backend` non è sqlite-shaped (giunto multi-backend reale).
+ test del registro (disaccoppia query↔backend).
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from store import (Store, Schema, TEXT, INT, JSON, BLOB,
                   register, get_store, unregister, is_registered, registered)
from backends.datastore.memory import MemoryBackend
from backends.datastore.sqldatabase.sqlite import SqliteBackend

SCHEMA = Schema(
    "notes",
    {"id": TEXT, "body": TEXT, "n": INT, "meta": JSON, "raw": BLOB},
    primary_key=("id",),
    indexes=(("n",),),
)


class _CrudContract:
    """Mixin: stessi test, backend fornito dalla sottoclasse via _backend()."""

    def setUp(self):
        self.store = Store(SCHEMA, backend=self._backend())

    def tearDown(self):
        self.store.close()

    def test_write_then_find(self):
        self.store.write([{"id": "a", "body": "x", "n": 1},
                          {"id": "b", "body": "y", "n": 2}])
        rows = self.store.find(order=["id"])
        self.assertEqual([r["id"] for r in rows], ["a", "b"])
        self.assertEqual(rows[0]["body"], "x")

    def test_upsert_creates_then_updates(self):
        self.assertEqual(self.store.write({"id": "a", "n": 1}), 1)
        self.store.write({"id": "a", "n": 9, "body": "z"})   # upsert su pk
        rows = self.store.find()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["n"], 9)
        self.assertEqual(rows[0]["body"], "z")

    def test_upsert_preserves_absent_fields(self):
        """Upsert PARZIALE (clobber-preserve, 20/6): un campo ASSENTE nella riga
        in arrivo NON azzera il valore esistente; un campo presente lo aggiorna.
        Causa-radice del clobber del detect github (re-ingest senza status/reply
        riportava le issue a stato vuoto)."""
        self.store.write({"id": "a", "n": 1, "body": "keep"})
        self.store.write({"id": "a", "n": 9})              # body assente
        r = self.store.get(where={"id": "a"})
        self.assertEqual(r["n"], 9)                         # presente → aggiornato
        self.assertEqual(r["body"], "keep")                # assente → preservato

    def test_insert_defaults_only_on_new(self):
        """`insert_defaults` riempie i campi assenti SOLO sui record NUOVI; un
        re-ingest di un record esistente NON ripristina il default (es. detect
        che re-trova un'issue già 'answered' non la riporta a 'new')."""
        s = Store(Schema("deftest", {"id": TEXT, "body": TEXT, "n": INT},
                         primary_key=("id",)),
                  backend=self._backend(), insert_defaults={"body": "DEFAULT"})
        try:
            s.write({"id": "a", "n": 1})                   # nuovo → default
            self.assertEqual(s.get(where={"id": "a"})["body"], "DEFAULT")
            s.write({"id": "a", "body": "real"})           # valore reale
            s.write({"id": "a", "n": 2})                   # re-ingest senza body
            self.assertEqual(s.get(where={"id": "a"})["body"], "real")  # NON resettato
            s.write({"id": "b", "n": 1})                   # altro nuovo → default
            self.assertEqual(s.get(where={"id": "b"})["body"], "DEFAULT")
        finally:
            s.close()

    def test_find_where_eq_and_in(self):
        self.store.write([{"id": x, "n": i}
                          for i, x in enumerate(["a", "b", "c"])])
        self.assertEqual(len(self.store.find(where={"id": "b"})), 1)
        got = {r["id"] for r in self.store.find(where={"id": ["a", "c"]})}
        self.assertEqual(got, {"a", "c"})
        self.assertEqual(self.store.find(where={"id": []}), [])   # IN vuoto

    def test_order_and_limit(self):
        self.store.write([{"id": "a", "n": 3}, {"id": "b", "n": 1},
                          {"id": "c", "n": 2}])
        asc = [r["n"] for r in self.store.find(order=[("n", "asc")])]
        self.assertEqual(asc, [1, 2, 3])
        desc = [r["n"] for r in self.store.find(order=[("n", "desc")])]
        self.assertEqual(desc, [3, 2, 1])
        self.assertEqual(len(self.store.find(order=["n"], limit=2)), 2)

    def test_json_roundtrip(self):
        self.store.write({"id": "a", "meta": {"k": [1, 2], "z": "ç"}})
        meta = self.store.get(where={"id": "a"})["meta"]
        self.assertEqual(meta, {"k": [1, 2], "z": "ç"})   # dict, non stringa

    def test_blob_roundtrip(self):
        self.store.write({"id": "a", "raw": b"\x00\x01\xff"})
        self.assertEqual(self.store.get(where={"id": "a"})["raw"], b"\x00\x01\xff")

    def test_update(self):
        self.store.write([{"id": "a", "n": 1}, {"id": "b", "n": 1}])
        self.assertEqual(self.store.update({"n": 5}, where={"id": "a"}), 1)
        self.assertEqual(self.store.get(where={"id": "a"})["n"], 5)
        self.assertEqual(self.store.get(where={"id": "b"})["n"], 1)

    def test_delete(self):
        self.store.write([{"id": "a"}, {"id": "b"}])
        self.assertEqual(self.store.delete(where={"id": "a"}), 1)
        self.assertEqual([r["id"] for r in self.store.find()], ["b"])

    def test_get_and_count_and_empty(self):
        self.assertEqual(self.store.find(), [])
        self.assertIsNone(self.store.get(where={"id": "nope"}))
        self.store.write([{"id": "a"}, {"id": "b"}])
        self.assertEqual(self.store.count(), 2)
        self.assertEqual(self.store.get(where={"id": "a"})["id"], "a")

    def test_capabilities_json(self):
        self.assertIn("json", self.store.backend.capabilities)


class TestMemoryBackend(_CrudContract, unittest.TestCase):
    def _backend(self):
        return MemoryBackend()


class TestSqliteBackend(_CrudContract, unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        super().setUp()

    def _backend(self):
        return SqliteBackend(path=Path(self.tmp) / "t.sqlite")

    def test_persists_across_instances(self):
        """Proprietà SOLO-sqlite: i dati sopravvivono a una nuova istanza
        (stesso file)."""
        self.store.write({"id": "a", "n": 1})
        reopened = Store(SCHEMA, backend=SqliteBackend(path=Path(self.tmp) / "t.sqlite"))
        try:
            self.assertEqual(reopened.get(where={"id": "a"})["n"], 1)
        finally:
            reopened.close()

    def test_additive_migration(self):
        """Una colonna nuova nello schema viene ALTER-aggiunta a una tabella
        preesistente (migrazione additiva idempotente)."""
        self.store.write({"id": "a"})
        ext = Schema("notes", {**SCHEMA.columns, "extra": TEXT},
                     primary_key=("id",))
        s2 = Store(ext, backend=SqliteBackend(path=Path(self.tmp) / "t.sqlite"))
        try:
            s2.write({"id": "a", "extra": "v"})
            self.assertEqual(s2.get(where={"id": "a"})["extra"], "v")
        finally:
            s2.close()


class TestDefaultBackend(unittest.TestCase):
    def test_default_is_sqlite_at_path(self):
        tmp = tempfile.mkdtemp()
        st = Store(SCHEMA, path=Path(tmp) / "d.sqlite")    # no backend → sqlite
        try:
            st.write({"id": "a"})
            self.assertEqual(st.count(), 1)
            self.assertTrue((Path(tmp) / "d.sqlite").exists())
        finally:
            st.close()


class TestRegistry(unittest.TestCase):
    def tearDown(self):
        unregister("t_notes")

    def test_register_get_roundtrip(self):
        register(SCHEMA, name="t_notes", backend=MemoryBackend())
        self.assertTrue(is_registered("t_notes"))
        self.assertIn("t_notes", registered())
        st = get_store("t_notes")
        st.write({"id": "x", "n": 1})
        self.assertEqual(get_store("t_notes").count(), 1)   # stesso store

    def test_unknown_store_raises(self):
        with self.assertRaises(KeyError):
            get_store("nope_xyz_unregistered")


if __name__ == "__main__":
    unittest.main()
