"""test_engine_v2.py — unit + integration test del nuovo runtime/engine/.

Light coverage: dispatcher orchestration, fastpath hash + cosine, autopath
promote logic, validator typecheck, executor placeholder resolve.

Mock everywhere — no live LLM, no live executor.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.types import (
    Intent, Framework, StepSpec, FillerSpec, RunResult, StepRun,
)
from engine import cluster as eng_cluster
from engine import executor as eng_executor
from engine import fastpath as eng_fastpath
from engine import autopath as eng_autopath
from engine.recovery import classify_error, is_recoverable
from engine.validator import Validator
from engine.terminator import SimpleTerminator


class TestCluster(unittest.TestCase):
    def test_normalize_query_strips_stopwords(self):
        self.assertEqual(
            eng_cluster.normalize_query("Dimmi gli appuntamenti di domani"),
            "appuntamenti domani")
        self.assertEqual(
            eng_cluster.normalize_query("Quali sono i miei appuntamenti"),
            "miei appuntamenti")
        self.assertEqual(
            eng_cluster.normalize_query("Show me the events"),
            "me events")  # 'show'/'the' stripped

    def test_normalize_hash_stable(self):
        h1 = eng_cluster.normalize_hash("dimmi appuntamenti")
        h2 = eng_cluster.normalize_hash("Dimmi gli appuntamenti")
        self.assertEqual(h1, h2)  # same after normalize


class TestExecutorPlaceholders(unittest.TestCase):
    def test_resolve_dotted_simple(self):
        from engine.executor import _resolve_dotted
        self.assertEqual(_resolve_dotted({"a": {"b": 42}}, "a.b"), 42)
        self.assertEqual(_resolve_dotted({"x": [1, 2, 3]}, "x.1"), 2)

    def test_resolve_dotted_projection(self):
        from engine.executor import _resolve_dotted
        obj = {"entries": [{"e": [{"p": "/a"}, {"p": "/b"}]}]}
        self.assertEqual(
            _resolve_dotted(obj, "entries.0.e.*.p"),
            ["/a", "/b"])

    def test_resolve_stepref_full_match_preserves_type(self):
        from engine.executor import _resolve_stepref, StepRun
        h = [StepRun(step_idx=1, tool="x", args={},
                       result={"entries": [{"path": "/a"}, {"path": "/b"}]},
                       ok=True, latency_ms=0)]
        v = _resolve_stepref("${step1.entries.0.path}", h)
        self.assertEqual(v, "/a")
        v2 = _resolve_stepref("paths=${step1.entries.0.path}", h)
        self.assertEqual(v2, "paths=/a")  # embedded stringified


class TestValidator(unittest.TestCase):
    def _catalog(self):
        E = type("E", (), {})
        e1 = E(); e1.name = "find_files"; e1.args_schema = {
            "properties": {"base_path": {"type": "string"},
                            "patterns": {"type": "array"}},
            "required": ["base_path"],
        }
        e2 = E(); e2.name = "describe_entries"; e2.args_schema = {
            "properties": {"from_step": {"type": "integer"}},
        }
        return [e1, e2]

    def test_validator_catches_unknown_tool(self):
        fw = Framework(steps=[
            StepSpec(tool="get_invented", args={}),
            StepSpec(tool="final_answer", args={}),
        ])
        v = Validator(self._catalog())
        res = v.check(fw)
        self.assertFalse(res.ok)
        self.assertTrue(any(e.code == "tool_unknown" for e in res.errors))

    def test_validator_passes_valid(self):
        fw = Framework(steps=[
            StepSpec(tool="find_files", args={"base_path": "/tmp"}),
            StepSpec(tool="describe_entries", args={"from_step": 1}),
            StepSpec(tool="final_answer", args={}),
        ])
        v = Validator(self._catalog())
        res = v.check(fw)
        self.assertTrue(res.ok)


class TestClassifyError(unittest.TestCase):
    def test_out_of_scope_on_no_steps(self):
        r = RunResult()
        self.assertEqual(classify_error(r), "out_of_scope")
        self.assertFalse(is_recoverable("out_of_scope"))

    def test_wrong_args_on_cap_steps(self):
        r = RunResult(aborted_reason="cap_steps 12")
        self.assertEqual(classify_error(r), "wrong_args")

    def test_uses_error_class_field(self):
        s = StepRun(step_idx=1, tool="x", args={},
                     result={"ok": False, "error_class": "wrong_tool"},
                     ok=False, latency_ms=10)
        r = RunResult(steps=[s])
        self.assertEqual(classify_error(r), "wrong_tool")


class TestFastpathRoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # Override db path
        self._orig = eng_fastpath._db_path
        eng_fastpath._db_path = lambda: Path(self.tmp) / "fastpaths.sqlite"
        eng_fastpath._DB_INIT_DONE = False

    def tearDown(self):
        eng_fastpath._db_path = self._orig
        eng_fastpath._DB_INIT_DONE = False

    def test_approve_and_lookup_hash(self):
        fw = Framework(steps=[
            StepSpec(tool="get_now", args={}),
            StepSpec(tool="final_answer", args={}),
        ], final_message="${step1.iso}")
        fp_id = eng_fastpath.approve("che ora è", fw, approved_by="host")
        self.assertGreater(fp_id, 0)
        # Same query → hit
        hit = eng_fastpath.lookup("che ora è")
        self.assertIsNotNone(hit)
        self.assertEqual(hit.match_kind, "hash")
        # Normalized variant → hit (stesso hash)
        hit2 = eng_fastpath.lookup("Che ora è?")
        self.assertIsNotNone(hit2)

    def test_lookup_miss_on_unrelated(self):
        fw = Framework(steps=[StepSpec(tool="get_now", args={})])
        eng_fastpath.approve("che ora è", fw)
        hit = eng_fastpath.lookup("trova foto Matteo")
        self.assertIsNone(hit)


class TestAutopathSchema(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig = eng_autopath._db_path
        eng_autopath._db_path = lambda: Path(self.tmp) / "autopath.sqlite"
        eng_autopath._DB_INIT_DONE = False

    def tearDown(self):
        eng_autopath._db_path = self._orig
        eng_autopath._DB_INIT_DONE = False

    def test_record_obs_then_feedback_ok_promotes(self):
        intent = Intent(verb="get", object="files", keywords=["count"])
        fw = Framework(steps=[
            StepSpec(tool="find_files", args={"base_path": "/tmp"}),
            StepSpec(tool="final_answer", args={}),
        ], final_message="${step1.@count}")
        # 2 observations + 2 ok feedback → promote
        for i in range(2):
            tid = f"turn_{i}"
            eng_autopath.record_observation(
                turn_id=tid, intent=intent, framework=fw,
                query=f"quanti file iteration {i}", latency_ms=100)
            res = eng_autopath.record_feedback(tid, "ok")
            self.assertTrue(res["ok"])
        # Second feedback → promote (need ≥2 observations same fw_hash)
        # Verify skill exists
        c = eng_autopath._conn()
        rows = c.execute("SELECT id, status FROM skills").fetchall()
        c.close()
        self.assertGreaterEqual(len(rows), 1)


class TestTerminator(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        from engine import terminator as t
        self._orig = t._db_path
        t._db_path = lambda: Path(self.tmp) / "terminator.sqlite"
        t._DB_INIT_DONE = False

    def tearDown(self):
        from engine import terminator as t
        t._db_path = self._orig
        t._DB_INIT_DONE = False

    def test_simple_terminator_returns_template(self):
        st = SimpleTerminator()
        intent = Intent(verb="read", object="files", keywords=[])
        resp = st.explain(
            query="leggi cosa che non esiste",
            intent=intent, failed_run=None,
            error_class="missing_input")
        self.assertIn("Non posso risolvere", resp.final_text)
        self.assertTrue(resp.lacuna_id)


if __name__ == "__main__":
    unittest.main()
