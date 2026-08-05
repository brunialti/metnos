"""Test schema migration v2 + latest-wins su DB i18n.sqlite (Layer 3).

Estensione ADR 0092 (6/5/2026):
- ALTER TABLE add `version_hash` + `source_text_hash` (idempotente).
- `i18n.set()` ricalcola version_hash + invalida latest-wins simmetrico.
- `align_messages()` detect divergenza source_text_hash + marca needs_translation.
"""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


def _h_full(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


class TestI18nMigrationV2(unittest.TestCase):
    """Migration v2: ALTER TABLE add version_hash + source_text_hash."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db_path = self.tmp / "i18n.sqlite"
        # Crea DB schema legacy (senza version_hash + source_text_hash).
        c = sqlite3.connect(str(self.db_path))
        c.executescript("""
        CREATE TABLE i18n (
            key TEXT NOT NULL,
            lang TEXT NOT NULL,
            text TEXT,
            needs_translation INTEGER NOT NULL DEFAULT 0,
            source_lang TEXT,
            source_hash TEXT,
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            PRIMARY KEY (key, lang)
        );
        """)
        c.execute("INSERT INTO i18n(key, lang, text) VALUES (?, ?, ?)",
                  ("greeting", "it", "Ciao"))
        c.execute("INSERT INTO i18n(key, lang, text) VALUES (?, ?, ?)",
                  ("greeting", "en", "Hi"))
        c.commit()
        c.close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_migration_adds_columns(self):
        from admin.i18n_migrate_v2 import migrate
        report = migrate(self.db_path)
        self.assertTrue(report["ok"])
        self.assertIn("version_hash", report["schema_after"])
        self.assertIn("source_text_hash", report["schema_after"])
        self.assertIn("version_hash", report["added_columns"])
        self.assertIn("source_text_hash", report["added_columns"])

    def test_migration_idempotent(self):
        from admin.i18n_migrate_v2 import migrate
        # Run once
        r1 = migrate(self.db_path)
        # Run again
        r2 = migrate(self.db_path)
        self.assertTrue(r2["ok"])
        self.assertEqual(r2["added_columns"], [])  # nothing new
        # rows_with_version_hash uguale tra le due run.
        self.assertEqual(r1["rows_with_version_hash"], r2["rows_with_version_hash"])

    def test_backfill_version_hash(self):
        from admin.i18n_migrate_v2 import migrate
        report = migrate(self.db_path)
        # backfilled = 2 (le 2 row di setUp).
        self.assertEqual(report["backfilled"], 2)
        # Verify hashes
        c = sqlite3.connect(str(self.db_path))
        rows = list(c.execute(
            "SELECT key, lang, text, version_hash FROM i18n ORDER BY key, lang"
        ))
        c.close()
        for key, lang, text, vhash in rows:
            self.assertEqual(vhash, _h_full(text),
                f"version_hash mismatch for {key}/{lang}: {vhash} vs expected {_h_full(text)}")

    def test_check_only_does_not_modify(self):
        from admin.i18n_migrate_v2 import migrate
        # check-only on legacy schema
        report = migrate(self.db_path, check_only=True)
        self.assertTrue(report["ok"])
        # Schema before == schema after (no ALTER applied).
        self.assertNotIn("version_hash", report["schema_after"])


class TestI18nSetVersionHash(unittest.TestCase):
    """i18n.set() ricalcola version_hash + invalida latest-wins simmetrico."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        # Patch i18n.DB_PATH per usare un DB ephemero.
        import i18n
        self._orig_db_path = i18n.DB_PATH
        self._orig_conn = i18n._conn
        i18n.DB_PATH = self.tmp / "i18n.sqlite"
        i18n._conn = None  # force reopen
        self.i18n = i18n

    def tearDown(self):
        if self.i18n._conn:
            self.i18n._conn.close()
        self.i18n._conn = None
        self.i18n.DB_PATH = self._orig_db_path
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_set_recomputes_version_hash(self):
        self.i18n.set("greeting", "it", "Ciao!")
        c = self.i18n._open()
        row = c.execute(
            "SELECT version_hash FROM i18n WHERE key=? AND lang=?",
            ("greeting", "it"),
        ).fetchone()
        self.assertEqual(row[0], _h_full("Ciao!"))

    def test_set_latest_wins_invalidates_others_symmetric(self):
        """Set su EN (NON IT) deve invalidare IT se source_text_hash diverge."""
        # Bootstrap: insert IT and EN as if translated long ago.
        self.i18n.set("greeting", "it", "Ciao!")
        self.i18n.set("greeting", "en", "Hi!")
        # set_translated marker: simulate translator setting source_text_hash.
        self.i18n.set_translated("greeting", "en", "Hi!")
        # Now edit EN (not IT) -> should invalidate IT.
        self.i18n.set("greeting", "en", "Hello!")
        c = self.i18n._open()
        row = c.execute(
            "SELECT needs_translation FROM i18n WHERE key=? AND lang=?",
            ("greeting", "it"),
        ).fetchone()
        self.assertEqual(row[0], 1,
            "IT should be marked needs_translation=1 when EN is edited (latest-wins symmetric)")

    def test_set_translated_records_source_text_hash(self):
        """set_translated salva source_text_hash full sha256."""
        self.i18n.set("greeting", "it", "Ciao!")
        self.i18n.set("greeting", "en", "Hi!", source_lang="it")
        self.i18n.set_translated("greeting", "en", "Hi!")
        c = self.i18n._open()
        row = c.execute(
            "SELECT source_text_hash, version_hash FROM i18n WHERE key=? AND lang=?",
            ("greeting", "en"),
        ).fetchone()
        self.assertEqual(row[0], _h_full("Ciao!"))
        self.assertEqual(row[1], _h_full("Hi!"))

    def test_register_bilingual_key_is_complete_not_pending(self):
        created = self.i18n.register_key_if_missing(
            "MSG_NEW", "Nuovo", "New",
        )
        self.assertTrue(created)
        c = self.i18n._open()
        rows = c.execute(
            "SELECT lang, needs_translation, source_lang, source_text_hash "
            "FROM i18n WHERE key='MSG_NEW' ORDER BY lang"
        ).fetchall()
        self.assertEqual(rows[0], ("en", 0, "it", _h_full("Nuovo")))
        self.assertEqual(rows[1], ("it", 0, None, None))
        self.assertEqual(self.i18n.count_pending(), 0)

    def test_register_single_language_creates_one_actionable_placeholder(self):
        self.i18n.register_key_if_missing("MSG_ONLY_IT", "Solo italiano")
        pending = self.i18n.list_pending(limit=10)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["key"], "MSG_ONLY_IT")
        self.assertEqual(pending[0]["source_lang"], "it")
        self.assertEqual(pending[0]["target_lang"], "en")

    def test_language_context_is_nested_and_does_not_mutate_instance_language(self):
        original_cache = self.i18n._lang_cache
        self.i18n._lang_cache = "it"
        try:
            self.assertEqual(self.i18n.current_lang(), "it")
            with self.i18n.language_context("EN_us"):
                self.assertEqual(self.i18n.current_lang(), "en-us")
                with self.i18n.language_context("fr"):
                    self.assertEqual(self.i18n.current_lang(), "fr")
                self.assertEqual(self.i18n.current_lang(), "en-us")
            self.assertEqual(self.i18n.current_lang(), "it")
        finally:
            self.i18n._lang_cache = original_cache

    def test_available_languages_comes_from_catalog(self):
        self.i18n.set_catalog_translations(
            "MSG_LANGS", {"it": "Italiano", "en": "English", "fr": "Français"},
        )
        self.assertEqual(self.i18n.available_languages(), ("en", "fr", "it"))

    def test_seed_merge_adds_only_missing_key_language_rows(self):
        self.i18n.set_catalog_translations(
            "MSG_LOCAL", {"it": "Testo scelto", "en": "Chosen text"},
        )
        seed = self.tmp / "mini-seed.sqlite"
        with sqlite3.connect(str(seed)) as conn:
            conn.execute(
                "CREATE TABLE i18n (key TEXT NOT NULL, lang TEXT NOT NULL, "
                "text TEXT, PRIMARY KEY (key, lang))"
            )
            conn.executemany(
                "INSERT INTO i18n(key, lang, text) VALUES (?, ?, ?)",
                [
                    ("MSG_LOCAL", "it", "Testo del seed"),
                    ("MSG_NEW_RELEASE", "it", "Nuova stringa"),
                    ("MSG_NEW_RELEASE", "fr", "Nouvelle chaîne"),
                ],
            )
        conn = self.i18n._open()
        inserted = self.i18n._merge_missing_seed_rows(conn, seed)
        conn.commit()

        self.assertEqual(inserted, 2)
        self.assertEqual(
            conn.execute(
                "SELECT text FROM i18n WHERE key='MSG_LOCAL' AND lang='it'"
            ).fetchone()[0],
            "Testo scelto",
        )
        self.assertEqual(
            conn.execute(
                "SELECT lang, text FROM i18n WHERE key='MSG_NEW_RELEASE' "
                "ORDER BY lang"
            ).fetchall(),
            [("fr", "Nouvelle chaîne"), ("it", "Nuova stringa")],
        )

    def test_mark_for_translation_requeues_existing_target_without_erasing_it(self):
        self.i18n.set_catalog_translations(
            "MSG_REQUEUE", {"it": "Versione nuova", "en": "Old version"},
        )
        self.i18n.mark_for_translation("MSG_REQUEUE", "en", "it")
        c = self.i18n._open()
        row = c.execute(
            "SELECT text, needs_translation, source_lang FROM i18n "
            "WHERE key='MSG_REQUEUE' AND lang='en'"
        ).fetchone()
        self.assertEqual(row, ("Old version", 1, "it"))
        self.assertEqual(self.i18n.count_pending(actionable_only=True), 1)

    def test_pending_queue_excludes_null_and_self_sources(self):
        c = self.i18n._open()
        c.execute(
            "INSERT INTO i18n(key,lang,text,needs_translation,source_lang) "
            "VALUES ('A_NULL','it','ciao',1,NULL),"
            "('B_SELF','it','ciao',1,'it'),"
            "('C_VALID','it','ciao',0,NULL),"
            "('C_VALID','en',NULL,1,'it')"
        )
        c.commit()
        self.assertEqual(self.i18n.count_pending(), 3)
        self.assertEqual(self.i18n.count_pending(actionable_only=True), 1)
        self.assertEqual(
            [row["key"] for row in self.i18n.list_pending(limit=10)],
            ["C_VALID"],
        )

    def test_repair_complete_pending_establishes_clean_baseline(self):
        self.i18n.set("MSG_LEGACY", "it", "Versione italiana")
        self.i18n.set("MSG_LEGACY", "en", "English version")
        c = self.i18n._open()
        c.execute(
            "UPDATE i18n SET needs_translation=1, source_lang=NULL, "
            "source_text_hash=NULL WHERE key='MSG_LEGACY'"
        )
        c.commit()
        report = self.i18n.repair_complete_pending()
        # Il runtime apre il DB effimero con la baseline distribuita: la
        # riparazione amministrativa normalizza tutte le chiavi complete, non
        # soltanto quella legacy creata dal test.
        self.assertGreaterEqual(report["keys"], 1)
        self.assertGreaterEqual(report["rows"], 2)
        repaired = c.execute(
            "SELECT lang, needs_translation, source_lang, source_text_hash "
            "FROM i18n WHERE key='MSG_LEGACY' ORDER BY lang"
        ).fetchall()
        self.assertEqual(len(repaired), 2)
        self.assertTrue(all(row[1] == 0 for row in repaired))
        self.assertEqual(self.i18n.count_pending(), 0)
        from i18n_translator import align_messages
        result = next(
            row for row in align_messages(target_langs=["it", "en"])
            if row["key"] == "MSG_LEGACY"
        )
        self.assertEqual(result["status"], "in_sync")

    def test_delete_keys_is_exact(self):
        self.i18n.set_catalog_translations("OLD", {"it": "x", "en": "x"})
        self.i18n.set_catalog_translations("OLD.EXTRA", {"it": "y", "en": "y"})
        report = self.i18n.delete_keys(["OLD"])
        self.assertEqual(report, {"keys": 1, "rows": 2})
        self.assertFalse(self.i18n.key_exists("OLD"))
        self.assertTrue(self.i18n.key_exists("OLD.EXTRA"))


class TestAlignMessagesLayer3(unittest.TestCase):
    """align_messages() pattern latest-wins simmetrico cross-lang."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        import i18n
        self._orig_db_path = i18n.DB_PATH
        self._orig_conn = i18n._conn
        i18n.DB_PATH = self.tmp / "i18n.sqlite"
        i18n._conn = None
        self.i18n = i18n

    def tearDown(self):
        if self.i18n._conn:
            self.i18n._conn.close()
        self.i18n._conn = None
        self.i18n.DB_PATH = self._orig_db_path
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_align_messages_marks_divergent(self):
        """IT updated + source_text_hash su EN vecchio → EN deve essere marcata."""
        # Bootstrap: insert IT (old) + EN (translated da IT old).
        self.i18n.set("greeting", "it", "Ciao vecchio")
        self.i18n.set("greeting", "en", "Hi old", source_lang="it")
        self.i18n.set_translated("greeting", "en", "Hi old")
        # Ora IT cambia (latest-wins set lo segna gia', ma simuliamo
        # un cambio diretto via UPDATE che bypass set()):
        c = self.i18n._open()
        # Update IT directamente — bypass i18n.set() per simulare edit out-of-band.
        time.sleep(0.01)
        c.execute(
            "UPDATE i18n SET text=?, version_hash=?, "
            "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now', '+1 second') "
            "WHERE key=? AND lang=?",
            ("Ciao nuovo", _h_full("Ciao nuovo"), "greeting", "it"),
        )
        c.commit()
        # Reset needs_translation flag su EN (per simular state pulito prima
        # dell'align).
        c.execute("UPDATE i18n SET needs_translation=0 WHERE key=? AND lang=?",
                  ("greeting", "en"))
        c.commit()
        # align_messages should detect that IT version_hash != EN source_text_hash.
        from i18n_translator import align_messages
        results = align_messages(target_langs=["it", "en"])
        # Check EN now needs_translation=1
        row = c.execute(
            "SELECT needs_translation FROM i18n WHERE key=? AND lang=?",
            ("greeting", "en"),
        ).fetchone()
        self.assertEqual(row[0], 1,
            f"EN should be marked needs_translation=1; results={results}")

    def test_align_messages_in_sync_no_op(self):
        """IT + EN allineati → align_messages no-op."""
        self.i18n.set("greeting", "it", "Ciao")
        self.i18n.set("greeting", "en", "Hi", source_lang="it")
        self.i18n.set_translated("greeting", "en", "Hi")
        from i18n_translator import align_messages
        results = align_messages(target_langs=["it", "en"])
        for r in results:
            if r.get("key") == "greeting":
                self.assertEqual(r.get("status"), "in_sync")

    def test_align_messages_dry_run_no_modify(self):
        """dry_run=True: niente UPDATE."""
        self.i18n.set("greeting", "it", "Ciao")
        self.i18n.set("greeting", "en", "Hi", source_lang="it")
        # Bypass i18n.set's invalidation by directly writing source_text_hash.
        c = self.i18n._open()
        c.execute(
            "UPDATE i18n SET source_text_hash=?, needs_translation=0 "
            "WHERE key=? AND lang=?",
            (_h_full("VECCHIO"), "greeting", "en"),
        )
        c.commit()
        # Verify divergence is present
        from i18n_translator import align_messages
        results = align_messages(target_langs=["it", "en"], dry_run=True)
        # dry_run: needs_translation must remain 0
        row = c.execute(
            "SELECT needs_translation FROM i18n WHERE key=? AND lang=?",
            ("greeting", "en"),
        ).fetchone()
        self.assertEqual(row[0], 0, "dry_run should not modify needs_translation")


if __name__ == "__main__":
    unittest.main()
