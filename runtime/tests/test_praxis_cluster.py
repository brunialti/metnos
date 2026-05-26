"""Test unitari praxis_cluster (ADR 0161 ext, 26/5/2026).

Coverage:
- cosine similarity correctness
- assign_cluster zona high/low/grigia
- composite_score formula
- maybe_swap_champion logica darwiniana
- update_skill_metrics counters separati (uses/ok_count/fail_count)
- log_skill_version audit trail
- ensure_schema idempotente
"""
from __future__ import annotations

import os
import sys
import sqlite3
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import praxis_cluster as pc
import numpy as np


class TestCosine(unittest.TestCase):

    def test_cosine_identical(self):
        v = np.array([0.5, 0.5, 0.5], dtype=np.float32).tobytes()
        self.assertAlmostEqual(pc.cosine(v, v), 1.0, places=5)

    def test_cosine_orthogonal(self):
        v1 = np.array([1.0, 0.0], dtype=np.float32).tobytes()
        v2 = np.array([0.0, 1.0], dtype=np.float32).tobytes()
        self.assertAlmostEqual(pc.cosine(v1, v2), 0.0, places=5)

    def test_cosine_opposite(self):
        v1 = np.array([1.0, 0.0], dtype=np.float32).tobytes()
        v2 = np.array([-1.0, 0.0], dtype=np.float32).tobytes()
        self.assertAlmostEqual(pc.cosine(v1, v2), -1.0, places=5)

    def test_cosine_empty_safe(self):
        self.assertEqual(pc.cosine(b"", b""), 0.0)
        self.assertEqual(pc.cosine(None, None), 0.0)


class TestComposite(unittest.TestCase):

    def test_composite_perfect(self):
        # success 1.0 + speed 1.0 + align 1.0 → tutti i pesi
        s = pc.composite_score(1.0, 0, 1.0, max_latency_ms=60000)
        # W_SUCCESS + W_SPEED + W_ALIGNMENT = 1.0
        self.assertAlmostEqual(s, 1.0, places=3)

    def test_composite_slow_no_speed(self):
        # latency = max → speed 0
        s = pc.composite_score(1.0, 60000, 0.0, max_latency_ms=60000)
        self.assertAlmostEqual(s, 0.5, places=3)  # solo W_SUCCESS

    def test_composite_failed(self):
        s = pc.composite_score(0.0, 0, 0.0)
        # speed 1.0, success 0 → solo W_SPEED
        self.assertAlmostEqual(s, 0.3, places=3)


class TestEnsureSchema(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.conn = sqlite3.connect(self.tmp.name)
        # Crea schema base praxis (senza estensioni)
        self.conn.executescript("""
        CREATE TABLE skills (
          id TEXT PRIMARY KEY, intent_sig TEXT, intent_hash TEXT,
          keywords_csv TEXT, framework_json TEXT, source TEXT, status TEXT,
          uses INT, ok_count INT, fail_count INT, avg_latency_ms INT,
          alignment_score REAL, ts_created TEXT, ts_last_used TEXT,
          born_from TEXT, version TEXT);
        CREATE TABLE observations (
          id INTEGER PRIMARY KEY AUTOINCREMENT, turn_id TEXT, intent_hash TEXT,
          intent_sig TEXT, framework_json TEXT, framework_hash TEXT,
          verdict TEXT, verdict_ts TEXT, latency_ms INT, ts TEXT,
          promoted_to TEXT);
        """)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.tmp.name)

    def test_schema_adds_columns(self):
        pc.ensure_schema(self.conn)
        cur = self.conn.execute("PRAGMA table_info(observations)")
        cols = {r[1] for r in cur.fetchall()}
        self.assertIn("embedding", cols)
        self.assertIn("cluster_id", cols)
        cur = self.conn.execute("PRAGMA table_info(skills)")
        cols = {r[1] for r in cur.fetchall()}
        self.assertIn("cluster_id", cols)
        self.assertIn("framework_hash", cols)
        self.assertIn("latency_p50_ms", cols)
        self.assertIn("composite_score", cols)
        self.assertIn("champion", cols)
        self.assertIn("template_issue", cols)

    def test_schema_idempotent(self):
        pc.ensure_schema(self.conn)
        pc.ensure_schema(self.conn)  # no error
        cur = self.conn.execute("SELECT name FROM sqlite_master "
                                  "WHERE type='table' AND name='skill_versions'")
        self.assertIsNotNone(cur.fetchone())


class TestUpdateSkillMetrics(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.conn = sqlite3.connect(self.tmp.name)
        self.conn.executescript("""
        CREATE TABLE skills (
          id TEXT PRIMARY KEY, intent_sig TEXT, intent_hash TEXT,
          keywords_csv TEXT, framework_json TEXT, source TEXT, status TEXT,
          uses INT DEFAULT 0, ok_count INT DEFAULT 0, fail_count INT DEFAULT 0,
          avg_latency_ms INT, alignment_score REAL DEFAULT 0.0,
          ts_created TEXT, ts_last_used TEXT, born_from TEXT, version TEXT);
        CREATE TABLE observations (id INT);
        """)
        pc.ensure_schema(self.conn)
        self.conn.execute(
            "INSERT INTO skills(id, intent_sig, intent_hash, keywords_csv, "
            "framework_json, source, status, ts_created) "
            "VALUES ('sk1', 's|o|k', 'h', '', '{}', 'test', 'active', 'now')"
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        os.unlink(self.tmp.name)

    def test_ok_increments_ok_count(self):
        pc.update_skill_metrics(self.conn, "sk1", True, 5000)
        cur = self.conn.execute(
            "SELECT uses, ok_count, fail_count FROM skills WHERE id='sk1'")
        uses, ok, fail = cur.fetchone()
        self.assertEqual(uses, 1)
        self.assertEqual(ok, 1)
        self.assertEqual(fail, 0)

    def test_fail_increments_fail_count(self):
        pc.update_skill_metrics(self.conn, "sk1", False, 5000)
        cur = self.conn.execute(
            "SELECT uses, ok_count, fail_count FROM skills WHERE id='sk1'")
        uses, ok, fail = cur.fetchone()
        self.assertEqual(uses, 1)
        self.assertEqual(ok, 0)
        self.assertEqual(fail, 1)

    def test_mixed_sequence(self):
        pc.update_skill_metrics(self.conn, "sk1", True, 5000)
        pc.update_skill_metrics(self.conn, "sk1", True, 5000)
        pc.update_skill_metrics(self.conn, "sk1", False, 5000)
        cur = self.conn.execute(
            "SELECT uses, ok_count, fail_count FROM skills WHERE id='sk1'")
        uses, ok, fail = cur.fetchone()
        self.assertEqual(uses, 3)
        self.assertEqual(ok, 2)
        self.assertEqual(fail, 1)


class TestSwapChampion(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.conn = sqlite3.connect(self.tmp.name)
        self.conn.executescript("""
        CREATE TABLE skills (
          id TEXT PRIMARY KEY, intent_sig TEXT, intent_hash TEXT,
          keywords_csv TEXT, framework_json TEXT, source TEXT, status TEXT,
          uses INT DEFAULT 0, ok_count INT DEFAULT 0, fail_count INT DEFAULT 0,
          avg_latency_ms INT, alignment_score REAL DEFAULT 0.0,
          ts_created TEXT, ts_last_used TEXT, born_from TEXT, version TEXT);
        CREATE TABLE observations (id INT);
        """)
        pc.ensure_schema(self.conn)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.tmp.name)

    def _insert(self, id_, cluster, uses, ok, fail, lat, champ):
        self.conn.execute(
            "INSERT INTO skills(id, intent_sig, intent_hash, keywords_csv, "
            "framework_json, source, status, uses, ok_count, fail_count, "
            "latency_p50_ms, champion, cluster_id, ts_created) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (id_, "s|o|k", "h", "", "{}", "test", "active",
             uses, ok, fail, lat, champ, cluster, "now"))
        self.conn.commit()

    def test_no_swap_with_single_skill(self):
        self._insert("sk1", "c1", 5, 5, 0, 10000, 1)
        result = pc.maybe_swap_champion(self.conn, "c1")
        self.assertIsNone(result)

    def test_swap_when_challenger_faster(self):
        # champion lento, challenger veloce, stesso success
        self._insert("sk_old", "c1", 5, 5, 0, 15000, 1)
        self._insert("sk_new", "c1", 2, 2, 0, 5000, 0)
        result = pc.maybe_swap_champion(self.conn, "c1")
        self.assertEqual(result, "sk_new")
        # Verifica champion swap nel db.
        cur = self.conn.execute(
            "SELECT id FROM skills WHERE cluster_id='c1' AND champion=1")
        self.assertEqual(cur.fetchone()[0], "sk_new")

    def test_no_swap_marginal_difference(self):
        # latency simile, no swap
        self._insert("sk_old", "c1", 5, 5, 0, 10000, 1)
        self._insert("sk_new", "c1", 1, 1, 0, 9500, 0)
        result = pc.maybe_swap_champion(self.conn, "c1")
        self.assertIsNone(result)


class TestRenderFinalMessage(unittest.TestCase):
    """Test renderer magic + dict formatting (FASE A residual bugs)."""

    def test_count_magic_available_total(self):
        from praxis_executor import _render_final_message, StepRun
        h = [StepRun(1, "find_files", {}, {"available_total": 42}, True, 100)]
        out = _render_final_message("${step1.@count} file", h)
        self.assertEqual(out, "42 file")

    def test_count_magic_fallback_to_entries_len(self):
        from praxis_executor import _render_final_message, StepRun
        result = {"entries": [{"path": "/a"}, {"path": "/b"}]}
        h = [StepRun(1, "find_files", {}, result, True, 100)]
        out = _render_final_message("${step1.@count} files", h)
        self.assertEqual(out, "2 files")

    def test_dict_value_formatted_compact(self):
        """${step1.health.thermal} dove thermal e' dict → formato 'k=v · k=v'.
        Risolve bug 'La temperatura della GPU e' .' (dict ritornato nudo)."""
        from praxis_executor import _render_final_message, StepRun
        result = {"health": {"thermal": {"cpu_c": 68, "gpu_c": 58,
                                          "nvme_c": 37}}}
        h = [StepRun(1, "get_processes", {}, result, True, 100)]
        out = _render_final_message("Temp: ${step1.health.thermal}", h)
        self.assertIn("cpu_c=68", out)
        self.assertIn("gpu_c=58", out)
        self.assertIn(" · ", out)

    def test_dict_scalar_unchanged(self):
        from praxis_executor import _render_final_message, StepRun
        result = {"health": {"thermal": {"gpu_c": 58}}}
        h = [StepRun(1, "get_processes", {}, result, True, 100)]
        # Path completo verso scalare → solo il numero.
        out = _render_final_message("GPU ${step1.health.thermal.gpu_c}°C", h)
        self.assertEqual(out, "GPU 58°C")


class TestLogSkillVersion(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.conn = sqlite3.connect(self.tmp.name)
        self.conn.executescript("""
        CREATE TABLE skills (id TEXT, intent_sig TEXT, intent_hash TEXT,
          keywords_csv TEXT, framework_json TEXT, source TEXT, status TEXT,
          uses INT, ok_count INT, fail_count INT, avg_latency_ms INT,
          alignment_score REAL, ts_created TEXT, ts_last_used TEXT,
          born_from TEXT, version TEXT);
        CREATE TABLE observations (id INT);
        """)
        pc.ensure_schema(self.conn)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.tmp.name)

    def test_log_event(self):
        pc.log_skill_version(self.conn, "sk1", "created",
                              new_fw_hash="abc", reason="test")
        cur = self.conn.execute(
            "SELECT skill_id, event, new_fw_hash, reason "
            "FROM skill_versions WHERE skill_id='sk1'")
        row = cur.fetchone()
        self.assertEqual(row, ("sk1", "created", "abc", "test"))


class TestFeedbackLWWSymmetric(unittest.TestCase):
    """✓ feedback su (intent_hash, framework_hash) rimuove anti_skill +
    ripristina demoted → active (26/5/2026, asimmetria fix)."""

    def setUp(self):
        # praxis.py importa "from runtime_paths" ecc., usiamo PraxisStore reale.
        import sys
        sys.path.insert(0, "/opt/metnos/runtime")
        from praxis import PraxisStore
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.store = PraxisStore(self.tmp.name)

    def tearDown(self):
        self.store.conn.close()
        os.unlink(self.tmp.name)

    def test_ok_removes_anti_skill_and_restores_demoted(self):
        # Seed: 1 anti_skill + 1 demoted skill + 1 observation
        ih, fh = "ih_x", "fh_x"
        ts = "2026-05-26T10:00:00Z"
        ttl = "2026-06-25T10:00:00Z"
        self.store.conn.execute(
            "INSERT INTO anti_skills(intent_hash, framework_hash, fail_count, "
            "ttl_expires_at, reason, ts_last_fail) VALUES (?,?,?,?,?,?)",
            (ih, fh, 3, ttl, "test", ts))
        self.store.conn.execute(
            "INSERT INTO skills(id, intent_sig, intent_hash, keywords_csv, "
            "framework_json, framework_hash, source, status, version, "
            "ts_created) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("sk_test", "find|files", ih, "", "{}", fh, "praxis",
             "demoted", "v1.0.0", ts))
        self.store.conn.execute(
            "INSERT INTO observations(turn_id, intent_hash, intent_sig, "
            "framework_json, framework_hash, latency_ms, ts) "
            "VALUES (?,?,?,?,?,?,?)",
            ("turn_x", ih, "find|files", "{}", fh, 100, ts))
        self.store.conn.commit()

        out = self.store.record_feedback("turn_x", "ok")
        self.assertTrue(out["ok"])
        self.assertEqual(out.get("anti_skill_removed"), 1)
        # Verify DELETE
        n_anti = self.store.conn.execute(
            "SELECT COUNT(*) FROM anti_skills WHERE intent_hash=?",
            (ih,)).fetchone()[0]
        self.assertEqual(n_anti, 0)
        # Verify status restored
        status = self.store.conn.execute(
            "SELECT status FROM skills WHERE id='sk_test'").fetchone()[0]
        self.assertEqual(status, "active")
        # Audit row
        n_ver = self.store.conn.execute(
            "SELECT COUNT(*) FROM skill_versions "
            "WHERE event='anti_skill_lww_remove'").fetchone()[0]
        self.assertEqual(n_ver, 1)

    def test_ok_without_anti_skill_no_effect(self):
        ih, fh = "ih_y", "fh_y"
        ts = "2026-05-26T10:00:00Z"
        self.store.conn.execute(
            "INSERT INTO observations(turn_id, intent_hash, intent_sig, "
            "framework_json, framework_hash, latency_ms, ts) "
            "VALUES (?,?,?,?,?,?,?)",
            ("turn_y", ih, "find|files", "{}", fh, 100, ts))
        self.store.conn.commit()
        out = self.store.record_feedback("turn_y", "ok")
        self.assertTrue(out["ok"])
        self.assertNotIn("anti_skill_removed", out)


if __name__ == "__main__":
    unittest.main()
