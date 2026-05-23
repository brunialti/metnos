"""change_applier — test handler per ogni kind.

Run: `python3 -m pytest runtime/tests/test_change_applier.py -v`.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))


def _reset_modules():
    for m in list(sys.modules):
        if m.startswith("runtime.change_intents") or m == "change_intents":
            del sys.modules[m]
        if m.startswith("runtime.change_applier") or m == "change_applier":
            del sys.modules[m]


class TestApplierHandlers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.tmp.name)
        import config as C
        C.DB_CHANGE_INTENTS = self.tmpdir / "ci.sqlite"
        C.PATH_USER_DATA = self.tmpdir / "share"
        C.PATH_USER_STATE = self.tmpdir / "state"
        C.PATH_USER_DATA.mkdir(parents=True)
        C.PATH_USER_STATE.mkdir(parents=True)
        # Setup multi_tool_paths sqlite minimal
        C.DB_MULTI_TOOL_PATHS = C.PATH_USER_DATA / "multi_tool_paths.sqlite"
        cn = sqlite3.connect(str(C.DB_MULTI_TOOL_PATHS))
        cn.execute("""CREATE TABLE multi_tool_paths (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_query TEXT NOT NULL,
            tools_sequence TEXT NOT NULL,
            args_shape TEXT NOT NULL,
            path_shape_hash TEXT NOT NULL,
            uses INTEGER NOT NULL DEFAULT 1,
            ok_count INTEGER NOT NULL DEFAULT 0,
            fail_count INTEGER NOT NULL DEFAULT 0,
            ts_first TEXT NOT NULL,
            ts_last TEXT NOT NULL,
            last_used_active_day INTEGER NOT NULL,
            state TEXT NOT NULL DEFAULT 'candidate'
        )""")
        cn.execute("""INSERT INTO multi_tool_paths
            (canonical_query, tools_sequence, args_shape, path_shape_hash,
             ts_first, ts_last, last_used_active_day, state)
            VALUES ('q1', '["a","b"]', '[]', 'abc123hash', '2026-05-22', '2026-05-22', 1, 'candidate')""")
        cn.commit()
        cn.close()
        # Setup mnest sqlite minimal
        C.DB_MNESTOMA = C.PATH_USER_DATA / "mnest.sqlite"
        C.DB_MNESTOMA.parent.mkdir(parents=True, exist_ok=True)
        cn = sqlite3.connect(str(C.DB_MNESTOMA))
        cn.execute("""CREATE TABLE canonical_query_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_query TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            args_shape TEXT NOT NULL,
            uses INTEGER NOT NULL DEFAULT 1,
            ok_count INTEGER NOT NULL DEFAULT 0,
            fail_count INTEGER NOT NULL DEFAULT 0,
            ts_first TEXT NOT NULL,
            ts_last TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'candidate'
        )""")
        cn.execute("""INSERT INTO canonical_query_log
            (canonical_query, tool_name, args_shape, ts_first, ts_last, state)
            VALUES ('elenca file in /tmp', 'list_dirs', '{}', '2026-05-22', '2026-05-22', 'candidate')""")
        cn.commit()
        cn.close()
        _reset_modules()
        import change_intents as ci_mod
        import change_applier as ca
        ci_mod.init_db()
        self.ci_mod = ci_mod
        self.ca = ca

    def tearDown(self):
        self.tmp.cleanup()

    def _make_accepted(self, kind, target, body):
        ci = self.ci_mod.ChangeIntent.new(
            origin_family="user", origin_module="t",
            intent_kind=kind, intent_target=target, intent_summary="t",
            intent_body=body,
        )
        id_ = self.ci_mod.upsert_intent(ci)
        self.ci_mod.apply_decision(id_, action="accept", by="test")
        return id_

    def test_materialize_pipeline_promotes_to_active(self):
        id_ = self._make_accepted(
            self.ci_mod.KIND_MATERIALIZE_PIPELINE,
            "a→b",
            {"tools_sequence": ["a", "b"], "path_shape_hash": "abc123hash"},
        )
        report = self.ca.task_change_applier()
        self.assertEqual(report["applied"], 1)
        self.assertEqual(report["failed"], 0)
        ci = self.ci_mod.get_intent(id_)
        self.assertEqual(ci.state, "applied")
        # Verify DB state changed
        import config as C
        cn = sqlite3.connect(str(C.DB_MULTI_TOOL_PATHS))
        state = cn.execute(
            "SELECT state FROM multi_tool_paths WHERE path_shape_hash=?",
            ("abc123hash",),
        ).fetchone()[0]
        cn.close()
        self.assertEqual(state, "active")

    def test_cache_pattern_promotes_to_active(self):
        id_ = self._make_accepted(
            self.ci_mod.KIND_CACHE_PATTERN,
            "list_dirs",
            {"canonical_query": "elenca file in /tmp", "tool_name": "list_dirs"},
        )
        report = self.ca.task_change_applier()
        self.assertEqual(report["applied"], 1, msg=repr(report))
        ci = self.ci_mod.get_intent(id_)
        self.assertEqual(ci.state, "applied")
        import config as C
        cn = sqlite3.connect(str(C.DB_MNESTOMA))
        state = cn.execute(
            "SELECT state FROM canonical_query_log WHERE canonical_query=?",
            ("elenca file in /tmp",),
        ).fetchone()[0]
        cn.close()
        self.assertEqual(state, "active")

    def test_reject_pattern_appends_jsonl(self):
        id_ = self._make_accepted(
            self.ci_mod.KIND_REJECT_PATTERN,
            "quanto consuma gpu",
            {"canonical_query": "quanto consuma gpu", "tools_sequence": [], "n_rejections": 6},
        )
        report = self.ca.task_change_applier()
        self.assertEqual(report["applied"], 1)
        import config as C
        rp = C.PATH_USER_DATA / "rejected_patterns.jsonl"
        self.assertTrue(rp.exists())
        content = rp.read_text()
        self.assertIn("quanto consuma gpu", content)

    def test_reject_pattern_dedup_at_file_level(self):
        # Stessa canonical_query da due fingerprint diversi (n_rejections diverso) →
        # 2 intent distinti applicati ma 1 sola riga jsonl
        body1 = {"canonical_query": "x", "tools_sequence": ["t1"], "n_rejections": 3}
        body2 = {"canonical_query": "x", "tools_sequence": ["t1"], "n_rejections": 8}
        # body1 e body2 hanno stesso fingerprint (canonical+tools determinanti) →
        # devono produrre 2 chiamate apply_handler ma 1 sola row jsonl.
        # Aspetto: nel handler, secondo accept vede record gia' in jsonl → skip
        id1 = self._make_accepted(self.ci_mod.KIND_REJECT_PATTERN, "x", body1)
        self.ca.task_change_applier()
        # Forza un secondo round: invece di nuovo intent, riapplico handler diretto
        ci2 = self.ci_mod.ChangeIntent.new(
            origin_family="user", origin_module="t",
            intent_kind=self.ci_mod.KIND_REJECT_PATTERN,
            intent_target="x", intent_summary="t",
            intent_body=body2,
        )
        # Chiama handler direttamente (bypass dedup fingerprint)
        effect = self.ca.apply_reject_pattern(ci2)
        self.assertTrue(effect.get("already_present"),
                        f"second apply su stesso canonical+tools deve trovare already_present, got {effect}")
        import config as C
        rp = C.PATH_USER_DATA / "rejected_patterns.jsonl"
        n_lines = sum(1 for _ in rp.open())
        self.assertEqual(n_lines, 1, "dedup deve impedire duplicato in jsonl")

    def test_dedupe_executors_creates_alias(self):
        id_ = self._make_accepted(
            self.ci_mod.KIND_DEDUPE_EXECUTORS,
            "list_processes",
            {"a": "get_processes", "b": "list_processes"},
        )
        report = self.ca.task_change_applier()
        self.assertEqual(report["applied"], 1, msg=repr(report))
        import config as C
        aliases = json.loads((C.PATH_USER_DATA / "executor_aliases.json").read_text())
        self.assertEqual(aliases["list_processes"], "get_processes")

    def test_extend_executor_fails_if_target_missing(self):
        # Target deliberatamente inesistente → handler fallisce con
        # manifest-not-found. Non usiamo nomi reali (find_files/get_processes)
        # perche' PATH_EXECUTORS punta al filesystem live e troverebbe l'executor.
        id_ = self._make_accepted(
            self.ci_mod.KIND_EXTEND_EXECUTOR,
            "nonexistent_executor_for_test_99999",
            {"arg_name": "kind"},
        )
        report = self.ca.task_change_applier()
        ci = self.ci_mod.get_intent(id_)
        self.assertEqual(ci.state, "failed")
        self.assertIn("manifest", (ci.failed_reason or "").lower())

    def test_no_accepted_returns_empty(self):
        report = self.ca.task_change_applier()
        self.assertEqual(report["n_accepted"], 0)
        self.assertEqual(report["applied"], 0)
        self.assertEqual(report["failed"], 0)


if __name__ == "__main__":
    unittest.main()
