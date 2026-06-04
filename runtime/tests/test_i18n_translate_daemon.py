"""Test del task scheduler v2 `i18n_translate_pending` (jobs/i18n_translate_pending.py).

Copre:
1. Shape `RunResult` ritornato dal callback (ok + ok_count + error_count + metadata).
2. Idempotenza su re-run: una riga gia' tradotta con `source_hash` invariato
   viene saltata (no LLM call).
3. Cap N=20 enforced anche su 25 righe pending.
4. Audit log JSONL append-only nella dir corretta.
5. LLM crash (eccezione) → skip riga + error_count incrementato, no crash globale.
6. Migration colonna `translated_at_iso` / `translated_by` idempotente.
7. Registrazione callback `i18n_translate_pending` presente al boot scheduler v2.
8. Re-traduzione se il source cambia (hash diverso dal salvato).

Mock di `call_llm` per zero rete §7.9. Env vars `METNOS_I18N_DB` e
`METNOS_I18N_AUDIT_DIR` redirigono ogni scrittura a tmp_path.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


def _seed_i18n_db(db_path: Path, rows: list[tuple]) -> None:
    """Crea un DB i18n minimale con lo schema base (matching i18n.py).

    `rows` lista di tuple `(key, lang, text, needs_translation, source_lang)`.
    Il `source_hash` viene lasciato a NULL — la migration nel task lo aggiungera'.
    """
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS i18n (
            key TEXT NOT NULL,
            lang TEXT NOT NULL,
            text TEXT,
            needs_translation INTEGER NOT NULL DEFAULT 0,
            source_lang TEXT,
            source_hash TEXT,
            version_hash TEXT,
            source_text_hash TEXT,
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            PRIMARY KEY (key, lang)
        );
        """
    )
    for row in rows:
        conn.execute(
            "INSERT OR REPLACE INTO i18n(key, lang, text, needs_translation, source_lang) "
            "VALUES (?, ?, ?, ?, ?)",
            row,
        )
    conn.commit()
    conn.close()


def _fake_call_llm_factory(translations: dict[str, str]):
    """Fabbrica di un fake `call_llm` che ritorna JSON strict per ogni key.

    `translations`: mapping `key → traduzione`. Il fake estrae la chiave
    dalla "Frase IT: ..." del prompt e ritorna il JSON corrispondente.
    """
    def _fake(query, sys_prompt, *, tier="middle", max_tokens=600,
              temperature=0.0, think=False):
        # Il prompt e' la `query`; estraiamo il source text dopo "Frase IT: "
        source_text = ""
        if isinstance(query, str) and "Frase IT:" in query:
            source_text = query.split("Frase IT:", 1)[1].strip()
        # Cerca per source_text esatto nel dict (i test seedano hello/world).
        translation = translations.get(source_text, f"translated[{source_text}]")
        raw = json.dumps({"translation": translation}, ensure_ascii=False)
        return raw, {"model": "gemma-4-26B-test", "tier": tier,
                     "in_tokens": 10, "out_tokens": 5, "latency_ms": 1}
    return _fake


class TestI18nTranslateTask(unittest.TestCase):

    def setUp(self):
        # Tmp dir isolata per ciascun test.
        import tempfile
        self._tmpdir = Path(tempfile.mkdtemp(prefix="i18n_task_"))
        self._db = self._tmpdir / "i18n.sqlite"
        self._audit = self._tmpdir / "audit"
        # Patcha env: il modulo legge METNOS_I18N_DB / METNOS_I18N_AUDIT_DIR.
        self._env = mock.patch.dict("os.environ", {
            "METNOS_I18N_DB": str(self._db),
            "METNOS_I18N_AUDIT_DIR": str(self._audit),
            "METNOS_I18N_QUALITY": "wise",
        })
        self._env.start()

    def tearDown(self):
        self._env.stop()
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _import_task(self):
        """Re-import fresco del modulo per applicare le env del test."""
        # Necessario perche' il modulo legge `_DEFAULT_DB` a import-time
        # come fallback, ma `_db_path()` rilegge env ad ogni chiamata.
        if "jobs.i18n_translate_pending" in sys.modules:
            del sys.modules["jobs.i18n_translate_pending"]
        from jobs.i18n_translate_pending import task_i18n_translate_pending
        return task_i18n_translate_pending

    # ------- Test 1: shape RunResult ----------------------------------------
    def test_run_result_shape_ok(self):
        _seed_i18n_db(self._db, [
            ("MSG_HELLO", "it", "Ciao mondo", 0, None),
            ("MSG_HELLO", "en", None, 1, "it"),
        ])
        task = self._import_task()
        with mock.patch("llm_helpers.call_llm",
                        _fake_call_llm_factory({"Ciao mondo": "Hello world"})):
            result = task(payload={})
        self.assertIn("ok", result)
        self.assertTrue(result["ok"])
        self.assertEqual(result["ok_count"], 1)
        self.assertEqual(result["error_count"], 0)
        self.assertIn("metadata", result)
        meta = result["metadata"]
        for k in ("cap", "tier_used", "audit_path"):
            self.assertIn(k, meta)
        self.assertEqual(meta["cap"], 20)
        self.assertEqual(meta["tier_used"], "wise")

    # ------- Test 2: idempotency su re-run ---------------------------------
    def test_idempotent_skip_when_source_unchanged(self):
        _seed_i18n_db(self._db, [
            ("MSG_HI", "it", "Salve", 0, None),
            ("MSG_HI", "en", None, 1, "it"),
        ])
        task = self._import_task()
        call_count = {"n": 0}

        def _counting_fake(query, sys_prompt, **kw):
            call_count["n"] += 1
            return (json.dumps({"translation": "Hi"}), {"model": "x", "tier": "wise"})

        # Prima run: traduce.
        with mock.patch("llm_helpers.call_llm", _counting_fake):
            r1 = task(payload={})
        self.assertEqual(r1["ok_count"], 1)
        self.assertEqual(call_count["n"], 1)

        # Riapri DB, rimetti needs_translation=1 con source invariato per
        # simulare un re-fire della task; deve saltare via source_hash.
        conn = sqlite3.connect(str(self._db))
        conn.execute(
            "UPDATE i18n SET needs_translation=1 WHERE key='MSG_HI' AND lang='en'"
        )
        conn.commit()
        conn.close()

        # Seconda run: stesso source → skip idempotente.
        with mock.patch("llm_helpers.call_llm", _counting_fake):
            r2 = task(payload={})
        self.assertEqual(r2["ok_count"], 0)
        self.assertEqual(r2["error_count"], 0)
        # Nessuna nuova call LLM.
        self.assertEqual(call_count["n"], 1)

    # ------- Test 3: cap N=20 enforced --------------------------------------
    def test_cap_20_enforced(self):
        rows = []
        for i in range(25):
            rows.append((f"MSG_K{i:02d}", "it", f"Testo {i}", 0, None))
            rows.append((f"MSG_K{i:02d}", "en", None, 1, "it"))
        _seed_i18n_db(self._db, rows)

        task = self._import_task()
        with mock.patch("llm_helpers.call_llm", _fake_call_llm_factory({})):
            result = task(payload={})
        # Cap = 20: vede al massimo 20 righe pending, niente di piu'.
        self.assertEqual(result["ok_count"], 20)
        self.assertEqual(result["metadata"]["pending_seen"], 20)
        # 5 righe rimangono pending sul DB.
        conn = sqlite3.connect(str(self._db))
        n_pending = conn.execute(
            "SELECT COUNT(*) FROM i18n WHERE needs_translation=1"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(n_pending, 5)

    # ------- Test 4: audit log JSONL append-only ---------------------------
    def test_audit_jsonl_append(self):
        _seed_i18n_db(self._db, [
            ("MSG_A", "it", "Alfa", 0, None),
            ("MSG_A", "en", None, 1, "it"),
            ("MSG_B", "it", "Beta", 0, None),
            ("MSG_B", "en", None, 1, "it"),
        ])
        task = self._import_task()
        with mock.patch("llm_helpers.call_llm",
                        _fake_call_llm_factory({"Alfa": "Alpha", "Beta": "Bravo"})):
            result = task(payload={})
        audit_path = Path(result["metadata"]["audit_path"])
        self.assertTrue(audit_path.exists())
        # JSONL: due righe valide.
        lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 2)
        for line in lines:
            ev = json.loads(line)
            self.assertIn("key", ev)
            self.assertIn("status", ev)
            self.assertIn("ts", ev)
        # Re-run su stesso giorno: append (file cresce).
        # Reset un row per forzare la traduzione.
        conn = sqlite3.connect(str(self._db))
        conn.execute(
            "UPDATE i18n SET needs_translation=1, translated_at_iso=NULL, "
            "source_hash=NULL WHERE key='MSG_A' AND lang='en'"
        )
        conn.commit()
        conn.close()
        with mock.patch("llm_helpers.call_llm",
                        _fake_call_llm_factory({"Alfa": "Alpha2"})):
            task(payload={})
        lines2 = audit_path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines2), 3)

    # ------- Test 5: LLM crash → skip + continue ---------------------------
    def test_llm_crash_continues(self):
        _seed_i18n_db(self._db, [
            ("MSG_A", "it", "Alfa", 0, None),
            ("MSG_A", "en", None, 1, "it"),
            ("MSG_B", "it", "Beta", 0, None),
            ("MSG_B", "en", None, 1, "it"),
        ])
        task = self._import_task()
        crashed = {"on": True}

        def _flaky(query, sys_prompt, **kw):
            if crashed["on"]:
                crashed["on"] = False
                raise RuntimeError("provider down")
            return (json.dumps({"translation": "Bravo"}), {"model": "x"})

        with mock.patch("llm_helpers.call_llm", _flaky):
            result = task(payload={})
        # Una riga fallita, una ok. Task non crasha.
        self.assertTrue(result["ok"])
        self.assertEqual(result["ok_count"], 1)
        self.assertEqual(result["error_count"], 1)

    # ------- Test 6: migration colonne idempotente -------------------------
    def test_migration_idempotent(self):
        _seed_i18n_db(self._db, [
            ("MSG_X", "it", "Xenophon", 0, None),
            ("MSG_X", "en", None, 1, "it"),
        ])
        # Pre-condizione: schema base senza translated_at_iso / translated_by.
        conn = sqlite3.connect(str(self._db))
        cols = {r[1] for r in conn.execute("PRAGMA table_info(i18n)").fetchall()}
        conn.close()
        self.assertNotIn("translated_at_iso", cols)
        self.assertNotIn("translated_by", cols)

        task = self._import_task()
        with mock.patch("llm_helpers.call_llm",
                        _fake_call_llm_factory({"Xenophon": "Xenophon"})):
            r1 = task(payload={})
        self.assertIn("translated_at_iso", r1["metadata"]["schema_migration"])
        self.assertIn("translated_by", r1["metadata"]["schema_migration"])

        # Run 2: migration gia' applicata, ritorna [].
        conn = sqlite3.connect(str(self._db))
        conn.execute(
            "UPDATE i18n SET needs_translation=1 WHERE key='MSG_X' AND lang='en'"
        )
        conn.commit()
        conn.close()
        with mock.patch("llm_helpers.call_llm",
                        _fake_call_llm_factory({"Xenophon": "Xenophon"})):
            r2 = task(payload={})
        self.assertEqual(r2["metadata"]["schema_migration"], [])

    # ------- Test 7: callback registrato al boot scheduler v2 --------------
    def test_callback_registered_at_boot(self):
        # Costruisce un daemon temporaneo + applica install_default_callbacks
        # + verifica che la key e' presente.
        from scheduler_v2.daemon import SchedulerDaemon
        from scheduler_v2.builtin_callbacks import install_default_callbacks
        db_path = self._tmpdir / "scheduler_v2.sqlite"
        d = SchedulerDaemon(db_path)
        install_default_callbacks(d)
        info = d.callbacks.get("i18n_translate_pending")
        self.assertIsNotNone(info)
        self.assertEqual(info.key, "i18n_translate_pending")

    # ------- Test 8: re-traduzione se source cambia ------------------------
    def test_retranslate_when_source_changes(self):
        _seed_i18n_db(self._db, [
            ("MSG_C", "it", "Versione uno", 0, None),
            ("MSG_C", "en", None, 1, "it"),
        ])
        task = self._import_task()
        # Run 1: traduce.
        with mock.patch("llm_helpers.call_llm",
                        _fake_call_llm_factory({"Versione uno": "Version one"})):
            r1 = task(payload={})
        self.assertEqual(r1["ok_count"], 1)
        # Modifico il source IT; needs_translation=1 viene rimesso.
        conn = sqlite3.connect(str(self._db))
        conn.execute(
            "UPDATE i18n SET text='Versione due' WHERE key='MSG_C' AND lang='it'"
        )
        conn.execute(
            "UPDATE i18n SET needs_translation=1 WHERE key='MSG_C' AND lang='en'"
        )
        conn.commit()
        conn.close()
        # Run 2: source cambiato → ritraduce (no skip idempotente).
        with mock.patch("llm_helpers.call_llm",
                        _fake_call_llm_factory({"Versione due": "Version two"})):
            r2 = task(payload={})
        self.assertEqual(r2["ok_count"], 1)
        # Verifica testo aggiornato.
        conn = sqlite3.connect(str(self._db))
        text = conn.execute(
            "SELECT text FROM i18n WHERE key='MSG_C' AND lang='en'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(text, "Version two")

    # ------- Test 9: seed idempotente (_BUILTIN_JOBS unica entry) ----------
    def test_seed_idempotent_single_entry(self):
        """Seed via `install_default_jobs` non duplica `i18n_translate_pending`.

        Anti-regressione vs CLAUDE.md §13: il punto di seed e'
        `_BUILTIN_JOBS` + `install_default_jobs` (idempotent INSERT-OR-IGNORE).
        """
        from scheduler_v2.daemon import SchedulerDaemon
        from scheduler_v2.builtin_callbacks import _BUILTIN_JOBS, install_default_jobs
        db_path = self._tmpdir / "scheduler_seed.sqlite"
        d = SchedulerDaemon(db_path)
        install_default_jobs(d)
        install_default_jobs(d)
        entries = d.storage.list_all()
        matches = [e for e in entries if e.name == "i18n_translate_pending"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].callback_key, "i18n_translate_pending")
        # Trigger = quello dichiarato in _BUILTIN_JOBS (single source §7.3):
        # l'orario esatto e' un dettaglio di de-collisione (era daily@02:00,
        # oggi every_6h per ADR 0167) — l'invariante e' che il seed lo rispetti.
        spec = next(j for j in _BUILTIN_JOBS if j["name"] == "i18n_translate_pending")
        self.assertEqual(matches[0].trigger, spec["trigger"])
        self.assertTrue(matches[0].recurring)


if __name__ == "__main__":
    unittest.main()
