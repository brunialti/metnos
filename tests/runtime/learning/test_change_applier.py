"""change_applier — test handler per ogni kind.

Run: `python3 -m pytest tests/runtime/learning/test_change_applier.py -v`.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


def _reset_modules():
    """Ricarica `change_applier`, MAI `change_intents`.

    `change_intents` risolve `C.DB_CHANGE_INTENTS` dentro `_conn()`, cioe' a
    ogni chiamata: l'override di config nel setUp funziona senza toglierlo da
    `sys.modules`. Toglierlo invece spezza l'identita' della classe, perche'
    chi lo aveva gia' importato (`change_intent_adapters`, caricato al primo
    import di `change_intents`) resta legato alla classe VECCHIA mentre il
    reimport ne crea una NUOVA: `isinstance` diventa falso fra oggetti che il
    codice tratta come identici, e il guasto si vede solo in certi ordini di
    esecuzione (`iter_telos` in test_change_intent_adapters)."""
    for m in list(sys.modules):
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

    def test_materialize_pipeline_runs_query_once(self):
        """2/7/2026: accept di una pipeline = ESEGUILA una volta come turno
        reale (scheduled scope); L0/L1 imparano dal turno vero."""
        from types import SimpleNamespace
        from unittest import mock
        id_ = self._make_accepted(
            self.ci_mod.KIND_MATERIALIZE_PIPELINE,
            "create_events",
            {"suggested_query": "leggi le scadenze dei file e crea eventi",
             "tools_sequence": ["get_files", "create_events"]},
        )
        fake_log = SimpleNamespace(
            final_kind="answer", turn_id="t-run1",
            final_message="fatto: 2 eventi creati",
            steps=[SimpleNamespace(chosen_tool="get_files"),
                   SimpleNamespace(chosen_tool="create_events")])
        calls = {}
        def _fake_run_turn(query, **kw):
            calls["query"] = query
            calls["kw"] = kw
            return fake_log
        with mock.patch("agent_runtime.run_turn", new=_fake_run_turn):
            report = self.ca.task_change_applier()
        self.assertEqual(report["applied"], 1, msg=repr(report))
        self.assertIn("scadenze", calls["query"])
        ci = self.ci_mod.get_intent(id_)
        self.assertEqual(ci.state, "applied")
        self.assertEqual(ci.applied_effect.get("final_kind"), "answer")
        self.assertEqual(ci.applied_effect.get("turn_id"), "t-run1")

    def test_materialize_pipeline_error_run_marks_failed(self):
        from types import SimpleNamespace
        from unittest import mock
        id_ = self._make_accepted(
            self.ci_mod.KIND_MATERIALIZE_PIPELINE,
            "create_events",
            {"suggested_query": "pipeline che fallisce"},
        )
        fake_log = SimpleNamespace(final_kind="error", turn_id="t-ko",
                                   final_message="non ho la capacita'",
                                   steps=[])
        with mock.patch("agent_runtime.run_turn",
                        new=lambda q, **kw: fake_log):
            report = self.ca.task_change_applier()
        self.assertEqual(report["failed"], 1, msg=repr(report))
        ci = self.ci_mod.get_intent(id_)
        self.assertEqual(ci.state, "failed")

    def test_materialize_pipeline_without_query_fails_early(self):
        id_ = self._make_accepted(
            self.ci_mod.KIND_MATERIALIZE_PIPELINE, "x", {})
        # intent_summary "t" fa da fallback → per forzare il fail-early
        # svuota anche il summary
        import config as C
        cn = sqlite3.connect(str(C.DB_CHANGE_INTENTS))
        cn.execute("UPDATE change_intents SET intent_summary='' WHERE id=?",
                   (id_,))
        cn.commit(); cn.close()
        report = self.ca.task_change_applier()
        self.assertEqual(report["failed"], 1)

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
        # La provenienza si registra dal proprietario dello stato di ciclo di
        # vita, non costruendo a mano la sua tabella privata: l'applier ora
        # legge e scrive da quello stesso proprietario.
        import executor_aging
        original = executor_aging.DB_PATH
        executor_aging.DB_PATH = self.ca.C.PATH_USER_STATE / "executor_stats.db"
        self.addCleanup(lambda: setattr(executor_aging, "DB_PATH", original))
        executor_aging.register("list_processes", source="synth:reactive")
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

    def test_dedupe_refuses_unprotected_handcrafted_executor(self):
        id_ = self._make_accepted(
            self.ci_mod.KIND_DEDUPE_EXECUTORS,
            "delete_persons",
            {"a": "delete_entries", "b": "delete_persons"},
        )
        report = self.ca.task_change_applier()
        self.assertEqual(report["failed"], 1, msg=repr(report))
        ci = self.ci_mod.get_intent(id_)
        self.assertIn("handcrafted executor", ci.failed_reason)

    def test_dedupe_executors_never_deprecates_protected_seed(self):
        id_ = self._make_accepted(
            self.ci_mod.KIND_DEDUPE_EXECUTORS,
            "read_messages",
            {"a": "read_messages_v2", "b": "read_messages"},
        )
        report = self.ca.task_change_applier()
        self.assertEqual(report["failed"], 1, msg=repr(report))
        ci = self.ci_mod.get_intent(id_)
        self.assertEqual(ci.state, "failed")
        self.assertIn("protected executor", ci.failed_reason)
        self.assertFalse((self.ca.C.PATH_USER_DATA /
                          "executor_aliases.json").exists())

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
