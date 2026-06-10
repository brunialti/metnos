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

    def _run_single(self, tool, args, args_schema):
        """Esegue UN executor via Engine con invoke fittizio che cattura gli
        args effettivamente passati. Ritorna gli args visti dall'executor."""
        from engine.executor import Executor
        from engine.types import Framework, StepSpec
        E = type("E", (), {})
        e = E(); e.name = tool; e.args_schema = args_schema
        seen = {}

        def _fake_invoke(name, a):
            seen.update(a)
            return {"ok": True, "entries": []}

        eng = Executor(invoke_executor=_fake_invoke, catalog=[e])
        fw = Framework(steps=[StepSpec(tool=tool, args=dict(args))])
        eng.run(fw, query="q")
        return seen

    def test_drop_optional_filler_unresolved(self):
        # ${FILLER:base_path} su arg OPZIONALE (required=[]) → dropped,
        # l'executor riceve gli altri arg (default = discovery).
        seen = self._run_single(
            "find_images_indices",
            {"query_text": "montagna", "base_path": "${FILLER:base_path}"},
            {"required": [], "properties": {
                "query_text": {"type": "string"},
                "base_path": {"type": "string"}}})
        self.assertIn("query_text", seen)
        self.assertNotIn("base_path", seen)

    def test_proposer_exclude_tools_removes_from_prompt(self):
        from engine.proposer import SimpleProposer
        from engine.types import Intent
        E = type("E", (), {})
        cat = []
        for nm in ("find_images_indices", "get_inputs", "send_messages"):
            e = E(); e.name = nm; e.description = f"SCOPO: {nm}. OUT: x"
            e.args_schema = {"properties": {}, "required": []}
            cat.append(e)
        seen = {}

        def _cap_llm(system, user, **kw):
            # Layout static_first (ottimizzazione A): il pool per-query vive
            # nel messaggio USER; il system e' la testa statica. Le asserzioni
            # guardano il prompt COMPLETO (system+user).
            seen["prompt"] = system + "\n" + user
            return '{"steps":[{"tool":"send_messages","args":{}}]}'

        def _pool_block(prompt):
            # isola la sezione "POOL TOOL DISPONIBILI" (il template menziona
            # get_inputs anche nelle REGOLE: l'esclusione tocca solo il pool).
            lo = prompt.find("POOL TOOL")
            hi = prompt.find("FRAMEWORK GIA")
            return prompt[lo:hi] if lo >= 0 and hi > lo else prompt

        p = SimpleProposer()
        # senza esclusione: get_inputs nel pool
        p.propose(query="q", intent=Intent(verb="send", object="messages"),
                  pool=[e.name for e in cat], excluded_hashes=set(),
                  llm_call=_cap_llm, catalog=cat)
        self.assertIn("get_inputs", _pool_block(seen["prompt"]))
        # con esclusione: get_inputs FUORI dal pool (→ fuori dalla grammar GBNF)
        p.propose(query="q", intent=Intent(verb="send", object="messages"),
                  pool=[e.name for e in cat], excluded_hashes=set(),
                  llm_call=_cap_llm, catalog=cat, exclude_tools=("get_inputs",))
        self.assertNotIn("get_inputs", _pool_block(seen["prompt"]))

    def test_proposer_excluded_signal_intelligible(self):
        # B15: il segnale di diversificazione nel prompt e' la FORMA dei
        # piani esclusi (tool + arg keys) + istruzione DEVI/NON DEVI — MAI
        # l'hash sha opaco (il modello lo ignorava → challenger identico).
        from engine.executor import compute_framework_hash
        from engine.proposer import SimpleProposer, _render_excluded_signal
        fw = Framework(steps=[
            StepSpec(tool="read_urls_html", args={"urls": ["x"]}),
            StepSpec(tool="final_answer", args={}),
        ], final_message="ok")
        h = compute_framework_hash(fw)  # popola il registry hash→forma
        # render diretto: forma + istruzione, niente sha
        sig = _render_excluded_signal({h}, "it")
        self.assertIn("read_urls_html(urls) → final_answer", sig)
        self.assertIn("DEVI", sig)
        self.assertNotIn(h, sig)
        # hash NON risolvibile (processo passato) → conteggio onesto
        sig_un = _render_excluded_signal({"feedfacefeedface"}, "it")
        self.assertIn("1 altri piani", sig_un)
        self.assertNotIn("feedface", sig_un)
        # vuoto → placeholder invariato per-lingua
        self.assertEqual(_render_excluded_signal(set(), "it"), "(nessuno)")
        self.assertEqual(_render_excluded_signal(set(), "en"), "(none)")
        # end-to-end: la sezione excluded del prompt reale usa la forma
        E = type("E", (), {})
        cat = []
        for nm in ("read_urls_html", "send_messages"):
            e = E(); e.name = nm; e.description = f"SCOPO: {nm}. OUT: x"
            e.args_schema = {"properties": {}, "required": []}
            cat.append(e)
        seen = {}

        def _cap_llm(system, user, **kw):
            # static_first: la sezione excluded per-query vive nello user.
            seen["prompt"] = system + "\n" + user
            return '{"steps":[{"tool":"send_messages","args":{}}]}'

        SimpleProposer().propose(
            query="q", intent=Intent(verb="read", object="urls"),
            pool=[e.name for e in cat], excluded_hashes={h},
            llm_call=_cap_llm, catalog=cat)
        self.assertIn("read_urls_html(urls) → final_answer", seen["prompt"])
        self.assertNotIn(h, seen["prompt"])

    def test_metis_compound_challenger_diversified(self):
        # B15: sul COMPOUND il challenger (call #2) e' diverso PER
        # COSTRUZIONE: il primo tool del campione esce dal pool della 2a
        # call (prompt + GBNF) e la sezione excluded mostra la FORMA del
        # campione, non l'hash.
        from engine.proposer_metis import MetisProposer
        E = type("E", (), {})
        cat = []
        for nm in ("find_issues_github", "read_issues_github",
                   "write_files_spreadsheet"):
            e = E(); e.name = nm; e.description = f"SCOPO: {nm}. OUT: x"
            e.args_schema = {"properties": {}, "required": []}
            cat.append(e)
        systems = []
        outs = [
            '{"steps":[{"tool":"find_issues_github","args":{"repo":"r"}},'
            '{"tool":"write_files_spreadsheet","args":{"from_step":1}},'
            '{"tool":"final_answer","args":{}}],"final_message":"a"}',
            '{"steps":[{"tool":"read_issues_github","args":{"repo":"r"}},'
            '{"tool":"write_files_spreadsheet","args":{"from_step":1}},'
            '{"tool":"final_answer","args":{}}],"final_message":"b"}',
        ]

        def _llm(system, user, **kw):
            # static_first: pool/excluded per-query nello user → cattura il
            # prompt completo (system+user) per le asserzioni sotto.
            systems.append(system + "\n" + user)
            return outs[min(len(systems), len(outs)) - 1]

        intent = Intent(verb="find", object="issues", actions=[
            {"verb": "find", "object": "issues"},
            {"verb": "write", "object": "files"}])
        fw = MetisProposer().propose(
            query="trova le issue e mettile in un foglio", intent=intent,
            pool=[e.name for e in cat], excluded_hashes=set(),
            llm_call=_llm, catalog=cat)
        self.assertIsNotNone(fw)
        self.assertEqual(len(systems), 2)  # N=2: campione + challenger

        def _pool_block(system):
            lo = system.find("POOL TOOL")
            hi = system.find("FRAMEWORK GIA")
            return system[lo:hi] if lo >= 0 and hi > lo else system

        # call #1: pool pieno, nessun escluso
        self.assertIn("find_issues_github", _pool_block(systems[0]))
        self.assertIn("(nessuno)", systems[0])
        # call #2 (challenger): primo tool del campione FUORI dal pool,
        # forma del campione nella sezione excluded
        self.assertNotIn("find_issues_github", _pool_block(systems[1]))
        self.assertIn("read_issues_github", _pool_block(systems[1]))
        self.assertIn("find_issues_github(repo)", systems[1])
        self.assertIn("DEVI", systems[1].split("FRAMEWORK GIA")[1][:600])

    def test_telos_rank_compound_stub_malus(self):
        # B15 difesa hedge: su intent compound (2 clausole) un piano MONCO
        # (1 solo step-executor) NON deve battere il campione completo
        # grazie al bonus-brevita'.
        from engine.proposer_metis import _heuristic_telos_score
        intent = Intent(verb="find", object="files", actions=[
            {"verb": "find", "object": "files"},
            {"verb": "delete", "object": "files"}])
        full = {"n_steps": 4, "verb": "find", "object": "files",
                "tools": ["find_files", "filter_entries", "delete_files",
                          "final_answer"]}
        stub = {"n_steps": 2, "verb": "find", "object": "files",
                "tools": ["find_pulls_github", "final_answer"]}
        self.assertGreater(_heuristic_telos_score(full, intent=intent),
                           _heuristic_telos_score(stub, intent=intent))
        # mono-azione: il malus NON scatta (piano 1-step legittimo §4.2)
        mono = Intent(verb="get", object="numbers", actions=[
            {"verb": "get", "object": "numbers"}])
        one = {"n_steps": 2, "verb": "get", "object": "numbers",
               "tools": ["get_now", "final_answer"]}
        self.assertGreater(_heuristic_telos_score(one, intent=mono), 1.0)

    def test_telos_rank_dup_malus_args_aware(self):
        # Il malus dup colpisce SOLO step consecutivi IDENTICI (tool+args);
        # stesso tool con args DIVERSI (no projection) = verboso ma legittimo.
        from engine.proposer_metis import _heuristic_telos_score
        base = {"n_steps": 3, "verb": "read", "object": "urls"}
        legit = dict(base, tools=["read_urls_html", "read_urls_html",
                                  "final_answer"],
                     steps_sig=[("read_urls_html", '{"urls": ["a"]}'),
                                ("read_urls_html", '{"urls": ["b"]}'),
                                ("final_answer", "{}")])
        broken = dict(base, tools=["read_urls_html", "read_urls_html",
                                   "final_answer"],
                      steps_sig=[("read_urls_html", '{"urls": ["a"]}'),
                                 ("read_urls_html", '{"urls": ["a"]}'),
                                 ("final_answer", "{}")])
        self.assertGreater(_heuristic_telos_score(legit, intent=None),
                           _heuristic_telos_score(broken, intent=None))

    def test_telos_rank_excluded_hedge_handicap(self):
        # B15: il challenger hard-escluso non vince per sola brevita'
        # (campione 5-step sano > challenger 3-step marcato), ma vince
        # quando il campione ha step duplicati IDENTICI (pipeline rotta).
        from engine.proposer_metis import MetisProposer
        intent = Intent(verb="find", object="urls", actions=[
            {"verb": "find", "object": "urls"},
            {"verb": "read", "object": "urls"}])
        mk = lambda tools_args: Framework(
            steps=[StepSpec(tool=t, args=a) for t, a in tools_args],
            final_message="x")
        champ = mk([("find_urls", {"q": "r"}),
                    ("read_urls_html", {"urls": ["a"]}),
                    ("read_urls_html", {"urls": ["b"]}),
                    ("read_urls_html", {"urls": ["c"]}),
                    ("final_answer", {})])
        chall = mk([("find_images_web", {"q": "r"}),
                    ("read_urls_html", {"from_step": 1}),
                    ("final_answer", {})])
        chall._metis_excluded_hedge = True
        mp = MetisProposer()
        ranked = mp._rank_by_telos([champ, chall], intent=intent, lang="it")
        self.assertIs(ranked[0], champ)  # brevita' non basta al challenger
        champ_broken = mk([("get_files", {"paths": ["x"]}),
                           ("get_files", {"paths": ["x"]}),
                           ("write_files_spreadsheet", {"from_step": 2}),
                           ("final_answer", {})])
        chall2 = mk([("read_files", {"paths": ["x"]}),
                     ("write_files_spreadsheet", {"from_step": 1}),
                     ("final_answer", {})])
        chall2._metis_excluded_hedge = True
        ranked2 = mp._rank_by_telos([champ_broken, chall2],
                                    intent=intent, lang="it")
        self.assertIs(ranked2[0], chall2)  # dup identico → challenger vince

    def test_get_inputs_misroute_detect(self):
        from engine.dispatch import _is_get_inputs_misroute
        from engine.types import Framework, StepSpec
        # sole step get_inputs → misroute
        self.assertTrue(_is_get_inputs_misroute(Framework(steps=[
            StepSpec(tool="get_inputs", args={}),
            StepSpec(tool="final_answer", args={})])))
        # get_inputs seguita da azione → NON misroute (uso legittimo)
        self.assertFalse(_is_get_inputs_misroute(Framework(steps=[
            StepSpec(tool="get_inputs", args={}),
            StepSpec(tool="create_events", args={})])))
        # pipeline reale → NON misroute
        self.assertFalse(_is_get_inputs_misroute(Framework(steps=[
            StepSpec(tool="find_images_indices", args={"query_text": "x"}),
            StepSpec(tool="send_messages", args={})])))

    def test_dropped_required_verbs(self):
        from engine.dispatch import _dropped_required_verbs
        from engine.types import Framework, StepSpec
        Q = ("cerca online le conferenze AMD ROCm, crea un evento per ciascuna "
             "e mandami una mail")
        # collasso a create_events-only → 'find' (producer) E 'send' droppati
        fw_bad = Framework(steps=[
            StepSpec(tool="create_events", args={}),
            StepSpec(tool="describe_entries", args={}),
            StepSpec(tool="final_answer", args={})])
        d = _dropped_required_verbs(fw_bad, Q)
        self.assertIn("find", d)
        self.assertIn("send", d)
        # find→create senza send → solo 'send' droppato (il bug "niente mail")
        fw_no_send = Framework(steps=[
            StepSpec(tool="find_urls", args={"query": "x"}),
            StepSpec(tool="create_events", args={"from_step": 1}),
            StepSpec(tool="final_answer", args={})])
        self.assertEqual(_dropped_required_verbs(fw_no_send, Q), {"send"})
        # pipeline completa → niente droppato
        fw_ok = Framework(steps=[
            StepSpec(tool="find_urls", args={"query": "x"}),
            StepSpec(tool="create_events", args={"from_step": 1}),
            StepSpec(tool="send_messages", args={}),
            StepSpec(tool="final_answer", args={})])
        self.assertEqual(_dropped_required_verbs(fw_ok, Q), set())
        # query mono-azione → mai scatta
        fw_single = Framework(steps=[
            StepSpec(tool="create_events", args={}),
            StepSpec(tool="final_answer", args={})])
        self.assertEqual(_dropped_required_verbs(fw_single, "crea un evento domani"), set())

    def test_keep_required_unresolved_errors(self):
        # Placeholder su arg REQUIRED → NON droppato (resta unresolved error,
        # executor non invocato).
        seen = self._run_single(
            "read_files",
            {"paths": "${FILLER:paths}"},
            {"required": ["paths"], "properties": {
                "paths": {"type": "array"}}})
        # executor non invocato → seen vuoto (lo step fallisce unresolved).
        self.assertEqual(seen, {})


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
