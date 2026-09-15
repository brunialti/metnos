"""test_engine_core.py — unit + integration test del nuovo runtime/engine/.

Light coverage: dispatcher orchestration, fastpath hash + cosine, autopath
promote logic, validator typecheck, executor placeholder resolve.

Mock everywhere — no live LLM, no live executor.
"""
from __future__ import annotations

import json
import os
import inspect
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


from engine.types import (
    Intent, Framework, StepSpec, FillerSpec, RunResult, StepRun,
)
from engine import cluster as eng_cluster
from engine import executor as eng_executor
from engine import fastpath as eng_fastpath
from engine import autopath as eng_autopath
from engine import dispatch as eng_dispatch
from engine.recovery import classify_error, is_recoverable
from engine.validator import Validator
from engine.terminator import SimpleTerminator


class TestDurableAdmissionBoundary(unittest.TestCase):
    def setUp(self):
        self.framework = Framework(steps=[StepSpec("long_fixture", {})])

    def _admit(self, callback):
        return eng_dispatch._admit_finalized_long_work(
            self.framework,
            callback,
            started_at=time.time(),
        )

    def test_short_work_falls_through(self):
        self.assertIsNone(self._admit(lambda _framework: None))

    def test_acceptance_and_rejection_are_terminal(self):
        accepted = self._admit(lambda _framework: {
            "ok": True,
            "final_message_hint": "queued",
        })
        rejected = self._admit(lambda _framework: {
            "ok": False,
            "error": "not admitted",
            "error_class": "dependency_unavailable",
        })

        self.assertEqual((accepted.final_kind, accepted.match_source),
                         ("answer", "lre"))
        self.assertEqual(accepted.final_text, "queued")
        self.assertEqual((rejected.final_kind, rejected.match_source),
                         ("error", "lre"))
        self.assertEqual(rejected.error_class, "dependency_unavailable")

    @mock.patch("messages.get", return_value="safe failure")
    def test_callback_exception_and_malformed_result_fail_closed(self, _message):
        raised = self._admit(
            lambda _framework: (_ for _ in ()).throw(RuntimeError("fixture")),
        )
        malformed = self._admit(lambda _framework: {"decision": "accepted"})

        self.assertEqual(raised.final_kind, "error")
        self.assertEqual(raised.final_text, "safe failure")
        self.assertEqual(malformed.final_kind, "error")
        self.assertEqual(malformed.error_class, "contract_violation")

    def test_every_inline_execution_leg_has_the_same_last_chance_boundary(self):
        source = inspect.getsource(eng_dispatch.run_turn)

        self.assertEqual(source.count("executor.run("), 1)
        self.assertEqual(source.count("_admit_finalized_long_work("), 1)
        self.assertEqual(source.count("_execute_with_lre_boundary("), 9)

    def test_explicit_start_lre_requires_closed_technical_contract(self):
        catalog = [type("ExecutorFixture", (), {
            "name": "start_lre",
            "args_schema": {
                "type": "object",
                "properties": {
                    "profile": {"enum": [
                        "images.questions.v1", "other.profile.v1",
                    ]},
                },
            },
        })()]

        framework = eng_dispatch._explicit_start_lre_framework(
            "Esegui start_lre con images.questions.v1 su "
            "/tmp/cert/images.",
            catalog,
            runtime_ctx={"channel": "web", "actor": "host"},
        )

        self.assertIsNotNone(framework)
        self.assertEqual(
            [step.tool for step in framework.steps],
            ["get_approval", "final_answer"],
        )
        self.assertEqual(
            framework.steps[0].args["on_approve"],
            {
                "tool": "start_lre",
                "args": {
                    "profile": "images.questions.v1",
                    "paths": ["/tmp/cert/images"],
                },
            },
        )

    def test_explicit_start_lre_rejects_missing_or_ambiguous_fields(self):
        catalog = [type("ExecutorFixture", (), {
            "name": "start_lre",
            "args_schema": {
                "properties": {"profile": {"enum": [
                    "images.questions.v1", "other.profile.v1",
                ]}},
            },
        })()]

        rejected = (
            "avvia images.questions.v1 su /tmp/images",
            "start_lre su /tmp/images",
            "start_lre images.questions.v1 su images",
            "start_lre images.questions.v1 e other.profile.v1 su /tmp/images",
        )
        for query in rejected:
            with self.subTest(query=query):
                self.assertIsNone(
                    eng_dispatch._explicit_start_lre_framework(query, catalog),
                )

    def test_l3_acceptance_returns_receipt_without_inline_invocation(self):
        proposed = Framework(steps=[
            StepSpec("find_files_hash", {"base_path": "/fixture"}),
            StepSpec("final_answer", {}),
        ])
        proposer = mock.Mock()
        proposer.propose.return_value = proposed
        invoked = []
        admitted = []
        catalog = [type("ExecutorFixture", (), {
            "name": "find_files_hash",
            "args_schema": {
                "type": "object",
                "properties": {"base_path": {"type": "string"}},
            },
            "capabilities": (),
        })()]
        environment = {
            "METNOS_ENGINE": "simple",
            "METNOS_FASTPATH": "0",
            "METNOS_AUTOPATH": "0",
            "METNOS_VALIDATOR": "0",
        }

        def accept(framework):
            admitted.append(framework)
            return {"ok": True, "final_message_hint": "queued"}

        with mock.patch.dict(os.environ, environment), \
             mock.patch("engine.proposer.get_proposer", return_value=proposer), \
             mock.patch("engine.cluster.embed", return_value=None):
            result = eng_dispatch.run_turn(
                query="trova tutti i duplicati",
                intent=Intent(verb="find", object="files"),
                catalog=catalog,
                invoke_executor_cb=lambda name, args: invoked.append((name, args)),
                admit_long_work_cb=accept,
                turn_id="turn-auto-l3",
            )

        self.assertEqual(result.match_source, "lre")
        self.assertEqual(result.final_text, "queued")
        self.assertEqual(result.durable_admission,
                         {"ok": True, "final_message_hint": "queued"})
        self.assertEqual(invoked, [])
        self.assertEqual(len(admitted), 1)


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

    def test_explicit_multilist_does_not_autowire_or_mutate_framework(self):
        """Il framework resta uno skeleton anche dopo l'esecuzione.

        group_entries ha già due sorgenti esplicite: aggiungere anche le
        entries dell'ultimo producer è ridondante, gonfia l'invoke e rende il
        piano dipendente dai dati del turno.
        """
        from engine.executor import Executor

        seen = {}

        def _fake_invoke(name, args):
            if name == "source_a":
                return {"ok": True, "entries": [{"date": "2026-06-05"}]}
            if name == "source_b":
                return {"ok": True, "entries": [{"date": "2026-06-06"}]}
            if name == "group_entries":
                seen.update(args)
                return {"ok": True, "entries": []}
            return {"ok": True}

        group_args = {
            "entries_lists": [
                "${step1.entries}", "${step2.entries}",
            ],
            "dedup_key": ["date"],
        }
        fw = Framework(steps=[
            StepSpec("source_a", {}),
            StepSpec("source_b", {}),
            StepSpec("group_entries", group_args),
            StepSpec("final_answer", {}),
        ])
        Executor(invoke_executor=_fake_invoke).run(fw, query="unisci")

        self.assertNotIn("entries", seen)
        self.assertEqual(seen["entries_lists"], [
            [{"date": "2026-06-05"}], [{"date": "2026-06-06"}],
        ])
        self.assertEqual(fw.steps[2].args, group_args)
        self.assertFalse(eng_fastpath._has_absolute_temporal_literal(fw))

    def test_empty_helper_payload_uses_scalar_reader_content(self):
        """A scalar read followed by an empty helper is not a zero result."""
        from engine.executor import Executor

        seen = {}

        def _fake_invoke(name, args):
            if name == "read_scalar":
                return {
                    "ok": True,
                    "content": "line one\nline two\n",
                    "metadata": {"path": "/tmp/note.txt"},
                }
            if name == "describe_entries":
                seen.update(args)
                return {"ok": True, "summary": "line one line two"}
            return {"ok": True}

        fw = Framework(steps=[
            StepSpec("read_scalar", {}),
            StepSpec("describe_entries", {"entries": []}),
        ])
        Executor(invoke_executor=_fake_invoke).run(fw, query="read note")

        self.assertEqual(seen["entries"], [{
            "content": "line one\nline two\n",
            "path": "/tmp/note.txt",
        }])

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
        from engine.types import Framework, Intent, StepSpec
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
        # query mono-azione correttamente coperta → niente drop
        fw_single = Framework(steps=[
            StepSpec(tool="create_events", args={}),
            StepSpec(tool="final_answer", args={})])
        self.assertEqual(_dropped_required_verbs(fw_single, "crea un evento domani"), set())

        # Una mutazione singola non può degradare a un lookup read-only.
        # 17/8/2026: `install` è verbo canonico (ADR 0209, parte A). Prima
        # l'intento di «installa X» si scriveva `write` perché il verbo non
        # esisteva; ora si scrive per quello che è, e il gate lo nomina.
        install_intent = Intent(verb="install", object="packages", actions=[
            {"verb": "install", "object": "packages"},
        ])
        check_only = Framework(steps=[
            StepSpec(tool="find_packages", args={"package_name": "tool-x"}),
            StepSpec(tool="final_answer", args={}),
        ])
        self.assertEqual(
            _dropped_required_verbs(
                check_only, "installa tool-x", install_intent),
            {"install"},
        )
        admin_plan = Framework(steps=[
            StepSpec(tool="admin", args={"intent": "install package tool-x"}),
            StepSpec(tool="final_answer", args={}),
        ])
        self.assertEqual(
            _dropped_required_verbs(
                admin_plan, "installa tool-x", install_intent), set())

        # Il live extractor usa il campo primario per una singola azione e può
        # lasciare actions vuoto. Il gate deve leggere entrambe le forme.
        install_primary_only = Intent(
            verb="install", object="packages", actions=[])
        self.assertEqual(
            _dropped_required_verbs(
                check_only, "installa tool-x", install_primary_only),
            {"install"},
        )

        # La query nomina il verbo anche quando l'intento arriva vuoto: il
        # gate legge la query, non solo il campo. Serve perché l'estrattore
        # può fallire e il piano di ripiego non deve passare inosservato.
        self.assertEqual(
            _dropped_required_verbs(check_only, "installa tool-x", None),
            {"install"},
        )

        # RM-0006 C3: ``email`` is the field used by a filter predicate, not
        # an implicit send action.  The whole-query lexical bag used to invent
        # a missing send step and reject an otherwise complete pipeline.
        contacts_intent = Intent(verb="find", object="contacts", actions=[
            {"verb": "find", "object": "contacts"},
            {"verb": "filter", "object": "entries"},
        ])
        contacts_plan = Framework(steps=[
            StepSpec(tool="find_contacts", args={}),
            StepSpec(tool="filter_entries", args={"from_step": 1}),
            StepSpec(tool="final_answer", args={}),
        ])
        contacts_query = (
            "Trova tutti i contatti e filtra i risultati mantenendo soltanto "
            "quelli con un indirizzo email"
        )
        self.assertEqual(
            _dropped_required_verbs(
                contacts_plan, contacts_query, contacts_intent), set())

        create_file_intent = Intent(
            verb="create", object="files", actions=[])
        write_file_plan = Framework(steps=[
            StepSpec(tool="write_files", args={
                "path": "/tmp/new.txt", "content": "new",
            }),
            StepSpec(tool="final_answer", args={}),
        ])
        self.assertEqual(
            _dropped_required_verbs(
                write_file_plan, "crea /tmp/new.txt con testo new",
                create_file_intent), set())
        write_primary_for_create = Intent(
            verb="write", object="files", actions=[])
        self.assertEqual(
            _dropped_required_verbs(
                write_file_plan, "crea /tmp/new.txt con testo new",
                write_primary_for_create), set())

    def test_populated_create_satisfies_spurious_same_object_write_action(self):
        """Initial population is part of create, not a second artifact."""
        from engine.dispatch import _dropped_required_verbs
        from engine.types import Framework, Intent, StepSpec

        intent = Intent(verb="create", object="files", actions=[
            {"verb": "create", "object": "files"},
            {"verb": "write", "object": "files"},
        ])
        populated = Framework(steps=[
            StepSpec(tool="create_files_spreadsheet", args={
                "columns": ["voce", "stato"],
                "values": [["alpha", "aperto"]],
            }),
            StepSpec(tool="final_answer", args={}),
        ])
        assert _dropped_required_verbs(
            populated, "crea un foglio con colonne e righe", intent) == set()

        # Regressione live 888dd534: il lessico vede ``metti`` come write,
        # mentre l'intent object-aware vede un unico create(files). Il create
        # popolato tramite from_step realizza già entrambe le facce del sink.
        spreadsheet_intent = Intent(verb="find", object="images", actions=[
            {"verb": "find", "object": "images"},
            {"verb": "create", "object": "files"},
        ])
        spreadsheet = Framework(steps=[
            StepSpec(tool="find_files_hash", args={"base_path": "/images"}),
            StepSpec(tool="create_files_spreadsheet", args={
                "columns": ["Cartella Originale", "Cartella Duplicato"],
                "from_step": 1,
            }),
            StepSpec(tool="final_answer", args={}),
        ])
        spreadsheet_query = (
            "Trova i file immagine duplicati nella cartella Immagini del "
            "server e metti in uno spreadsheet due colonne con solo il path "
            "delle cartelle contenenti i file originali e duplicati"
        )
        assert _dropped_required_verbs(
            spreadsheet, spreadsheet_query, spreadsheet_intent) == set()

        empty = Framework(steps=[
            StepSpec(tool="create_files_spreadsheet", args={"title": "vuoto"}),
            StepSpec(tool="final_answer", args={}),
        ])
        assert _dropped_required_verbs(
            empty, "crea un foglio e poi scrivilo", intent) == {"write"}

        two_writes = Intent(verb="create", object="files", actions=[
            {"verb": "create", "object": "files"},
            {"verb": "write", "object": "files"},
            {"verb": "write", "object": "files"},
        ])
        assert _dropped_required_verbs(
            populated, "crea un foglio e scrivi due artefatti", two_writes
        ) == {"write"}

    def _run_enrollment_plan(self, describe_args):
        """get_persons → describe_entries(describe_args) → final_answer con
        invoke fittizio: get_persons ritorna entries + final_message_hint
        (presentazione canonica), describe_entries (se invocato) un summary
        LLM-mock. Ritorna (run, describe_invocato)."""
        from engine.executor import Executor
        from engine.types import Framework, StepSpec
        hint = ("Persone enrolled (2):\n- Alice (4 esempi)\n"
                "- Bob (2 esempi)")
        called = {"describe": False}

        def _fake_invoke(name, a):
            if name == "get_persons":
                return {"ok": True,
                        "entries": [{"name": "Alice", "n_examples": 4},
                                     {"name": "Bob", "n_examples": 2}],
                        "n_entries": 2,
                        "final_message_hint": hint}
            if name == "describe_entries":
                called["describe"] = True
                return {"ok": True, "summary": "riassunto LLM"}
            return {"ok": True}

        eng = Executor(invoke_executor=_fake_invoke)
        fw = Framework(
            steps=[StepSpec(tool="get_persons", args={}),
                   StepSpec(tool="describe_entries", args=dict(describe_args)),
                   StepSpec(tool="final_answer", args={})],
            final_message="${step2.summary}")
        run = eng.run(fw, query="chi e' enrollato")
        return run, called["describe"], hint

    def test_describe_skip_on_final_message_hint(self):
        # Bug live 12/6/2026 T1/T2: l'enumerazione del registro persone
        # finiva nel describe by_importance → «4 scartate come rumore».
        # Producer con final_message_hint → describe default SKIPPATO,
        # il hint diventa summary e il template ${step2.summary} si risolve.
        run, describe_called, hint = self._run_enrollment_plan(
            {"from_step": 1})
        self.assertFalse(describe_called,
                         "describe_entries invocato nonostante il hint")
        self.assertEqual(run.steps[1].result.get("skipped"),
                         "final_message_hint_present")
        self.assertEqual(run.final_text, hint)
        self.assertEqual(run.final_kind, "answer")

    def test_describe_runs_with_explicit_directives(self):
        # Direttive esplicite (style/context/group_by) = sintesi LLM
        # richiesta dalla query → NIENTE skip.
        run, describe_called, _ = self._run_enrollment_plan(
            {"from_step": 1, "style": "by_relevance",
             "context": "riassumi il registro"})
        self.assertTrue(describe_called,
                        "describe_entries con style esplicito skippato")
        self.assertEqual(run.final_text, "riassunto LLM")

    def test_accepted_receipt_stops_later_planner_steps(self):
        """A durable async receipt owns the tail of the requested work."""

        from engine.executor import Executor

        called = []

        def _fake_invoke(name, _args):
            called.append(name)
            if name == "deferred_submitter":
                return {
                    "ok": True,
                    "decision": "accepted",
                    "final_message_hint": "Attività registrata.",
                }
            raise AssertionError("a step after an accepted receipt must not run")

        framework = Framework(
            steps=[
                StepSpec(tool="deferred_submitter", args={}),
                StepSpec(tool="unrelated_follow_up", args={}),
                StepSpec(tool="final_answer", args={}),
            ],
            final_message="This planner text must not replace the receipt.",
        )
        run = Executor(invoke_executor=_fake_invoke).run(framework)

        self.assertEqual(called, ["deferred_submitter"])
        self.assertEqual(run.final_kind, "answer")
        self.assertEqual(run.final_text, "Attività registrata.")

    def test_approval_required_stops_later_planner_steps(self):
        """Una proposta non esegue la coda prima della decisione umana."""

        from engine.executor import Executor

        called = []

        def _fake_invoke(name, _args):
            called.append(name)
            if name == "privileged_builtin":
                return {
                    "ok": True,
                    "decision": "approval_required",
                    "approval_required": True,
                    "final_message_hint": "Approvi questa operazione?",
                    "expandable_caps": [{"kind": "admin_approval"}],
                }
            raise AssertionError("la coda non deve partire prima del consenso")

        framework = Framework(steps=[
            StepSpec(tool="privileged_builtin", args={}),
            StepSpec(tool="unrelated_follow_up", args={}),
        ])
        run = Executor(invoke_executor=_fake_invoke).run(framework)

        self.assertEqual(called, ["privileged_builtin"])
        self.assertEqual(run.final_kind, "ask")
        self.assertEqual(run.final_text, "Approvi questa operazione?")

    def test_malformed_accepted_receipt_does_not_hide_a_later_error(self):
        from engine.executor import Executor

        called = []

        def _fake_invoke(name, _args):
            called.append(name)
            if name == "bad_submitter":
                return {"ok": True, "decision": "accepted"}
            return {"ok": False, "error_class": "invalid_args"}

        framework = Framework(steps=[
            StepSpec(tool="bad_submitter", args={}),
            StepSpec(tool="follow_up", args={}),
        ])
        run = Executor(invoke_executor=_fake_invoke).run(framework)

        self.assertEqual(called, ["bad_submitter", "follow_up"])
        self.assertEqual(run.final_kind, "error")

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

    def test_nested_network_failure_is_not_replanned_as_wrong_args(self):
        s = StepRun(
            step_idx=1, tool="read_urls_html", args={}, ok=False,
            latency_ms=10,
            result={"ok": False, "entries": [], "failed": [{
                "url": "https://example.test/x",
                "error": "temporary DNS failure",
                "error_class": "network",
            }]},
        )
        r = RunResult(steps=[s])
        self.assertEqual(classify_error(r), "out_of_scope")
        self.assertFalse(is_recoverable(classify_error(r)))

    def test_materialized_index_missing_is_operational_not_wrong_args(self):
        s = StepRun(
            step_idx=1, tool="find_images_indices", args={}, ok=False,
            latency_ms=10,
            result={
                "ok": False, "entries": [],
                "error_class": "index_missing",
                "error_code": "image_index_missing",
                "error": "index unavailable",
                "recommended_action": {
                    "executor": "create_images_indices", "args": {},
                },
            },
        )
        r = RunResult(steps=[s])
        self.assertEqual(classify_error(r), "out_of_scope")
        self.assertFalse(is_recoverable(classify_error(r)))

    def test_sites_navigation_failure_is_operational_not_replayed(self):
        s = StepRun(
            step_idx=1, tool="act_sites", args={}, ok=False, latency_ms=10,
            result={"ok": False, "error_class": "navigation_failed"},
        )
        r = RunResult(steps=[s])
        self.assertEqual(classify_error(r), "out_of_scope")
        self.assertFalse(is_recoverable(classify_error(r)))

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
        # Verify autopath exists
        c = eng_autopath._conn()
        rows = c.execute("SELECT id, status FROM autopaths").fetchall()
        c.close()
        self.assertGreaterEqual(len(rows), 1)

    def test_promote_new_framework_same_intent_no_id_collision(self):
        """Bug 1/7/2026: id = sig[:40]_v1.0.0 non dipendeva dal framework →
        stesso intent con framework NUOVO collideva sulla PRIMARY KEY e
        l'INSERT OR IGNORE lo scartava in silenzio (promoted_autopath_id falso
        §2.8, il piano nuovo non diventava mai autopath)."""
        intent = Intent(verb="read", object="messages", keywords=["inbox"])
        fw_a = Framework(steps=[
            StepSpec(tool="read_messages", args={"account": "all"}),
            StepSpec(tool="final_answer", args={})], final_message="a")
        fw_b = Framework(steps=[
            StepSpec(tool="read_messages", args={"account": "all"}),
            StepSpec(tool="describe_entries", args={"from_step": 1}),
            StepSpec(tool="final_answer", args={})], final_message="b")
        for i, fw in enumerate((fw_a, fw_b)):
            tid = f"turn_{i}"
            eng_autopath.record_observation(
                turn_id=tid, intent=intent, framework=fw,
                query=f"leggi la posta variante {i}", latency_ms=10)
            res = eng_autopath.record_feedback(tid, "ok")
            self.assertTrue(res["ok"])
            self.assertIn("promoted_autopath_id", res)
        c = eng_autopath._conn()
        rows = c.execute(
            "SELECT id, framework_hash FROM autopaths").fetchall()
        c.close()
        # DUE autopath distinte (id univoci), una per framework_hash
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({r[0] for r in rows}), 2)
        self.assertEqual(len({r[1] for r in rows}), 2)

    def test_lookup_object_boundary_no_cross_object_serve(self):
        """Regression turn 9805fb61/af045d18/1175b2f8: il match cluster (path 1,
        cosine sul TESTO) NON deve servire un autopath di object DIVERSO. Un
        piano `find|files` non serve una query `find|messages` cosine-vicina."""
        from unittest import mock
        import json as _json
        files_intent = Intent(verb="find", object="files", keywords=[])
        sig, ihash = eng_autopath._compute_intent_sig(files_intent)
        fw = _json.dumps({"steps": [
            {"tool": "find_files", "args": {"base_path": "/tmp"}},
            {"tool": "final_answer", "args": {}}], "final_message": "ok"})
        c = eng_autopath._conn()
        c.execute(
            "INSERT INTO observations(turn_id,intent_hash,intent_sig,"
            "framework_json,framework_hash,cluster_id,embedding,latency_ms,ts) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ("t1", ihash, sig, fw, "fh1", "cl_x", b"\x00" * 8, 1, "2026-01-01T00:00:00Z"))
        c.execute(
            "INSERT INTO autopaths(id,intent_sig,intent_hash,cluster_id,"
            "framework_json,framework_hash,status,champion,ts_created) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ("ap_files", sig, ihash, "cl_x", fw, "fh1", "active", 1, "2026-01-01T00:00:00Z"))
        c.commit()
        c.close()
        with mock.patch("engine.cluster.embed", return_value=b"\x00" * 8), \
             mock.patch("engine.cluster.cosine", return_value=1.0):
            # object DIVERSO (messages): cosine alta ma confine di categoria →
            # NON deve servire il piano files (prima del fix: misroute).
            miss = eng_autopath.lookup(
                "cerca le fatture sulla mail",
                Intent(verb="find", object="messages", keywords=[]))
            self.assertIsNone(miss)
            # stesso object (files): controllo positivo → DEVE servire.
            hit = eng_autopath.lookup("conta i file in tmp", files_intent)
            self.assertIsNotNone(hit)


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

    def test_surfaces_nested_operational_error(self):
        s = StepRun(
            step_idx=1, tool="read_urls_html", args={}, ok=False,
            latency_ms=10,
            result={"ok": False, "entries": [], "failed": [{
                "url": "https://example.test/x",
                "error": "temporary DNS failure",
                "error_class": "network",
            }]},
        )
        response = SimpleTerminator().explain(
            query="leggi il sito", intent=Intent(
                verb="read", object="urls", keywords=[]),
            failed_run=RunResult(steps=[s]), error_class="out_of_scope")
        self.assertIn("temporary DNS failure", response.final_text)
        self.assertNotIn("Pipeline malformata", response.final_text)
        self.assertNotIn("percorso, nome, periodo", response.final_text)

    def test_path_not_found_uses_specific_action(self):
        s = StepRun(
            step_idx=1, tool="find_files", args={
                "base_path": "/Documenti/Progetto Atlas"}, ok=False,
            latency_ms=10,
            result={
                "ok": False,
                "error_class": "not_found",
                "error_code": "ERR_PATH_NOT_FOUND",
                "error": "Percorso non trovato: /Documenti/Progetto Atlas.",
            },
        )

        response = SimpleTerminator().explain(
            query=("trova i PDF modificati negli ultimi 60 giorni in "
                   "/Documenti/Progetto Atlas"),
            intent=Intent(verb="find", object="files", keywords=[]),
            failed_run=RunResult(steps=[s]), error_class="wrong_args")

        self.assertIn("Percorso non trovato", response.final_text)
        self.assertIn("Verifica che il percorso esista", response.final_text)
        self.assertNotIn("percorso, nome, periodo", response.final_text)

    def test_capability_missing_does_not_expose_provider_traceback(self):
        s = StepRun(
            step_idx=1, tool="find_messages_provider", args={}, ok=False,
            latency_ms=10,
            result={
                "ok": False,
                "error_class": "capability_missing",
                "error": "Traceback (most recent call last): provider details",
            },
        )

        response = SimpleTerminator().explain(
            query="cerca messaggi", intent=Intent(
                verb="find", object="messages", keywords=[]),
            failed_run=RunResult(steps=[s]), error_class="out_of_scope")

        self.assertNotIn("Traceback", response.final_text)
        self.assertNotIn("provider details", response.final_text)


class TestMutatingInputEmptyAutoSkip(unittest.TestCase):
    """Auto-skip strutturale (§2.8/§7.3) di un mutante che consuma input VUOTO:
    niente artefatto vuoto (turn 36a40c35/e591854e — spreadsheet da 0 fatture).
    Lo standalone create (nessun input-lista) NON viene saltato."""

    def _hist(self, *results):
        return [StepRun(step_idx=i + 1, tool="t%d" % i, args={}, result=r,
                        ok=True, latency_ms=1) for i, r in enumerate(results)]

    def test_create_from_empty_upstream_is_skipped(self):
        step = StepSpec(tool="create_files_spreadsheet", args={"from_step": 1})
        hist = self._hist({"ok": True, "entries": []})
        self.assertTrue(eng_executor._mutating_input_is_empty(step, hist))
        self.assertFalse(eng_executor._step_condition_passes(step, hist))

    def test_create_from_nonempty_upstream_runs(self):
        step = StepSpec(tool="create_files_spreadsheet", args={"from_step": 1})
        hist = self._hist({"ok": True, "entries": [{"x": 1}]})
        self.assertFalse(eng_executor._mutating_input_is_empty(step, hist))
        self.assertTrue(eng_executor._step_condition_passes(step, hist))

    def test_opt_in_condition_uses_explicit_producer_not_interposed_step(self):
        step = StepSpec(
            tool="write_files", args={"from_step": 1, "path": "/tmp/x"},
            if_prev_entries_nonempty=True)
        hist = self._hist(
            {"ok": True, "entries": [{"x": 1}]},
            {"ok": True, "results": [{"path": "/tmp/out"}]},
        )
        self.assertTrue(eng_executor._step_condition_passes(step, hist))

        empty = self._hist(
            {"ok": True, "entries": []},
            {"ok": True, "results": [{"path": "/tmp/out"}]},
        )
        self.assertFalse(eng_executor._step_condition_passes(step, empty))

    def test_inline_empty_entries_is_skipped(self):
        step = StepSpec(tool="create_files_spreadsheet", args={"entries": []})
        self.assertTrue(eng_executor._mutating_input_is_empty(step, []))

    def test_standalone_create_not_skipped(self):
        """create senza input-lista (crea cartella X) NON e' un noop su vuoto."""
        step = StepSpec(tool="create_dirs", args={"paths": ["/tmp/x"]})
        self.assertFalse(eng_executor._mutating_input_is_empty(step, []))
        self.assertTrue(eng_executor._step_condition_passes(step, []))

    def test_producer_on_empty_not_skipped(self):
        """un producer (find/read) a 0 input NON e' soggetto all'auto-skip."""
        step = StepSpec(tool="read_files", args={"from_step": 1})
        hist = self._hist({"ok": True, "entries": []})
        self.assertFalse(eng_executor._mutating_input_is_empty(step, hist))


class TestQuerySpecificPaths(unittest.TestCase):
    """Regression turn 9805fb61: un path FILE concreto bakeizzato (spesso
    allucinato) rende il piano query-specific → non promuovibile a L1 ne'
    servibile via cosine 0b. `base_path` (radice di ricerca) resta generale."""

    def _fwjson(self, tool, args):
        import json
        return json.dumps({"steps": [
            {"tool": tool, "args": args},
            {"tool": "final_answer", "args": {}}]})

    def test_literal_paths_is_query_specific(self):
        fj = self._fwjson("read_files_csv", {"paths": ["/home/anthropic/fatture.csv"]})
        self.assertTrue(eng_executor.is_query_specific(fj))

    def test_literal_path_is_query_specific(self):
        fj = self._fwjson("read_files", {"path": "/tmp/x.txt"})
        self.assertTrue(eng_executor.is_query_specific(fj))

    def test_base_path_not_query_specific(self):
        """radice di ricerca riusabile in un cluster → NON query-specific."""
        fj = self._fwjson("find_files", {"base_path": "/tmp"})
        self.assertFalse(eng_executor.is_query_specific(fj))

    def test_paths_from_step_not_query_specific(self):
        """paths da from_step (pipeline) NON e' un literal → generale."""
        fj = self._fwjson("delete_files", {"from_step": 1})
        self.assertFalse(eng_executor.is_query_specific(fj))


if __name__ == "__main__":
    unittest.main()
