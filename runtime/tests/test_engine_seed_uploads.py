"""Engine seed-state / foto-allegate (ADR 0177 M1).

Verifica il meccanismo di ASSORBIMENTO del path PLANNER legacy (ADR 0092) nel
motore v3: un seed-state `@uploaded` (foto allegate come step-0 virtuale) viene
consumato dal primo step reale via `from_step=1` + consumer-match, SENZA LLM.

Test deterministici (niente Proposer/llm): costruiamo il Framework a mano e uno
`invoke` stub che registra gli args ricevuti da ogni tool.

Run: `python3 -m pytest runtime/tests/test_engine_seed_uploads.py -xvs`.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))

from engine.executor import Executor                      # noqa: E402
from engine.types import Framework, StepSpec, StepRun     # noqa: E402


# Schema minimale di find_images_indices: l'arg-lista consumer è
# `reference_images` (singolare `reference_image` = campo nelle entries seed).
_FIMI_SCHEMA = {
    "type": "object",
    "properties": {
        "reference_images": {"type": "array", "items": {"type": "string"}},
        "query_text": {"type": "string"},
    },
}


class _StubExec:
    def __init__(self, name, args_schema):
        self.name = name
        self.args_schema = args_schema


def _seed():
    """Seed-state come lo costruisce agent_runtime._try_engine_v2 per le foto."""
    obs = {
        "ok": True,
        "entries": [
            {"path": "/u/a.jpg", "reference_image": "/u/a.jpg",
             "source": "upload"},
            {"path": "/u/b.jpg", "reference_image": "/u/b.jpg",
             "source": "upload"},
        ],
        "_virtual": True, "_kind": "uploaded_reference_images", "n": 2,
    }
    return [StepRun(step_idx=0, tool="@uploaded",
                    args={"source": "upload", "n": 2},
                    result=obs, ok=True, latency_ms=0)]


class EngineSeedUploadsTests(unittest.TestCase):

    def _run(self, framework, seed, catalog):
        seen = {}

        def invoke(tool, args):
            seen[tool] = dict(args)
            return {"ok": True, "entries": [{"path": "/r/x.jpg", "score": 0.9}]}

        ex = Executor(invoke_executor=invoke, seed_steps=seed, catalog=catalog)
        run = ex.run(framework, query="trova foto simili")
        return seen, run

    # 1. find_images_indices senza sorgente → riceve reference_images dal seed.
    def test_find_images_indices_gets_reference_images_from_seed(self):
        fw = Framework(steps=[
            StepSpec(tool="find_images_indices", args={}),
            StepSpec(tool="final_answer", args={}),
        ])
        seen, _ = self._run(fw, _seed(),
                            [_StubExec("find_images_indices", _FIMI_SCHEMA)])
        self.assertIn("find_images_indices", seen)
        self.assertEqual(seen["find_images_indices"].get("reference_images"),
                         ["/u/a.jpg", "/u/b.jpg"])
        self.assertNotIn("from_step", seen["find_images_indices"])

    # 2. PRECEDENZA: il proposer ha messo query_text, ma le foto allegate
    #    VINCONO (reference_images iniettato), query_text preservato.
    def test_uploaded_photos_win_over_query_text(self):
        fw = Framework(steps=[
            StepSpec(tool="find_images_indices",
                     args={"query_text": "foto simili"}),
            StepSpec(tool="final_answer", args={}),
        ])
        seen, _ = self._run(fw, _seed(),
                            [_StubExec("find_images_indices", _FIMI_SCHEMA)])
        self.assertEqual(seen["find_images_indices"].get("reference_images"),
                         ["/u/a.jpg", "/u/b.jpg"])
        self.assertEqual(seen["find_images_indices"].get("query_text"),
                         "foto simili")

    # 2.bis CASO REALE (e2e 23/6): il proposer Mētis, ignaro del seed, emette
    #    `reference_images="${step0.entries.*.path}"` — placeholder 0-index che
    #    NON risolve (stepref è 1-index). Il seed-wiring lo droppa e instrada
    #    from_step=1 → reference_images = i path del seed.
    def test_proposer_step0_placeholder_rewired_from_seed(self):
        fw = Framework(steps=[
            StepSpec(tool="find_images_indices",
                     args={"reference_images": "${step0.entries.*.path}",
                           "top_k": 100}),
            StepSpec(tool="final_answer", args={}),
        ])
        seen, _ = self._run(fw, _seed(),
                            [_StubExec("find_images_indices", _FIMI_SCHEMA)])
        self.assertEqual(seen["find_images_indices"].get("reference_images"),
                         ["/u/a.jpg", "/u/b.jpg"])
        self.assertEqual(seen["find_images_indices"].get("top_k"), 100)

    # 2.ter Variante 1-index `${step1...}` (risolverebbe già): comunque
    #    canonicalizzato a reference_images dai path del seed.
    def test_proposer_step1_placeholder_rewired_from_seed(self):
        fw = Framework(steps=[
            StepSpec(tool="find_images_indices",
                     args={"reference_images": "${step1.entries.*.path}"}),
            StepSpec(tool="final_answer", args={}),
        ])
        seen, _ = self._run(fw, _seed(),
                            [_StubExec("find_images_indices", _FIMI_SCHEMA)])
        self.assertEqual(seen["find_images_indices"].get("reference_images"),
                         ["/u/a.jpg", "/u/b.jpg"])

    # 3. from_step esplicito del proposer non viene toccato (idempotente).
    def test_explicit_from_step_preserved(self):
        fw = Framework(steps=[
            StepSpec(tool="find_images_indices", args={"from_step": 1}),
            StepSpec(tool="final_answer", args={}),
        ])
        seen, _ = self._run(fw, _seed(),
                            [_StubExec("find_images_indices", _FIMI_SCHEMA)])
        self.assertEqual(seen["find_images_indices"].get("reference_images"),
                         ["/u/a.jpg", "/u/b.jpg"])

    # 4. describe_entries (entries-consumer) eredita le entries del seed.
    def test_describe_entries_inherits_seed_entries(self):
        fw = Framework(steps=[
            StepSpec(tool="describe_entries", args={}),
            StepSpec(tool="final_answer", args={}),
        ])
        cat = [_StubExec("describe_entries",
                         {"type": "object",
                          "properties": {"entries": {"type": "array"}}})]
        seen, _ = self._run(fw, _seed(), cat)
        self.assertIn("describe_entries", seen)
        self.assertEqual(len(seen["describe_entries"].get("entries") or []), 2)

    # 5. REGRESSIONE: senza seed nessun wiring (find_images resta text-only).
    def test_no_seed_no_wiring(self):
        fw = Framework(steps=[
            StepSpec(tool="find_images_indices", args={"query_text": "x"}),
            StepSpec(tool="final_answer", args={}),
        ])
        seen, _ = self._run(fw, [],
                            [_StubExec("find_images_indices", _FIMI_SCHEMA)])
        self.assertNotIn("reference_images", seen.get("find_images_indices", {}))
        self.assertNotIn("from_step", seen.get("find_images_indices", {}))

    # 6. Uno step che NON può consumare il seed (no consumer-arg, non
    #    entries-consumer) non viene cablato (no falso from_step).
    def test_non_consumer_first_step_not_wired(self):
        fw = Framework(steps=[
            StepSpec(tool="get_now", args={}),
            StepSpec(tool="final_answer", args={}),
        ])
        cat = [_StubExec("get_now", {"type": "object", "properties": {}})]
        seen, _ = self._run(fw, _seed(), cat)
        self.assertNotIn("from_step", seen.get("get_now", {}))
        self.assertNotIn("reference_images", seen.get("get_now", {}))


class EngineSeedDoneDedupTests(unittest.TestCase):
    """Guardia dedup «semina» kind="done" (ADR 0177 M1): continuazione dialogo.
    Uno step seminato come GIÀ ESEGUITO non va ri-eseguito se il proposer lo
    ri-emette; gli step a valle lo referenziano via from_step."""

    def _run(self, framework, seed, catalog=None):
        seen = []

        def invoke(tool, args):
            seen.append(tool)
            return {"ok": True, "entries": [{"id": len(seen), "v": tool}]}

        ex = Executor(invoke_executor=invoke, seed_steps=seed,
                      catalog=catalog or [])
        run = ex.run(framework, query="continua")
        return seen, run

    def _done_seed(self, tool="find_events_empty", args=None):
        """Seed kind='done': uno step prodotto in un turno precedente."""
        return [StepRun(
            step_idx=1, tool=tool, args=args or {"time_window": "this-week"},
            result={"ok": True, "entries": [
                {"start": "2026-06-24T10:00", "end": "2026-06-24T11:00"},
                {"start": "2026-06-24T15:00", "end": "2026-06-24T16:00"}]},
            ok=True, latency_ms=5, kind="done")]

    # 1. Il proposer ri-emette il producer già fatto → SALTATO (no re-run).
    def test_reemitted_done_step_skipped(self):
        fw = Framework(steps=[
            StepSpec(tool="find_events_empty", args={"time_window": "this-week"}),
            StepSpec(tool="create_events", args={"from_step": 1}),
            StepSpec(tool="final_answer", args={}),
        ])
        seen, run = self._run(fw, self._done_seed())
        # find_events_empty NON ri-eseguito; create_events sì.
        self.assertNotIn("find_events_empty", seen)
        self.assertIn("create_events", seen)

    # 2. create_events(from_step=1) referenzia il seed done (già in result.steps).
    def test_downstream_step_references_done_seed(self):
        captured = {}

        def invoke(tool, args):
            captured[tool] = dict(args)
            return {"ok": True, "results": [{"id": "evt1"}]}

        ex = Executor(invoke_executor=invoke, seed_steps=self._done_seed(),
                      catalog=[])
        fw = Framework(steps=[
            StepSpec(tool="find_events_empty", args={"time_window": "this-week"}),
            StepSpec(tool="create_events", args={"from_step": 1}),
            StepSpec(tool="final_answer", args={}),
        ])
        ex.run(fw, query="prenota")
        # create_events ha ricevuto le entries del seed done via from_step=1.
        self.assertEqual(len(captured["create_events"].get("entries") or []), 2)

    # 3. kind="done" NON è un input da consumare: il primo step reale non
    #    riceve from_step automatico (distinzione input vs done).
    def test_done_seed_is_not_consumable_input(self):
        captured = {}

        def invoke(tool, args):
            captured[tool] = dict(args)
            return {"ok": True, "entries": []}

        # find_images_indices NON è nel done-set → non skippato; ma il seed è
        # done (non input) → niente from_step=1 auto-iniettato.
        ex = Executor(invoke_executor=invoke, seed_steps=self._done_seed(),
                      catalog=[_StubExec("find_images_indices", _FIMI_SCHEMA)])
        fw = Framework(steps=[
            StepSpec(tool="find_images_indices", args={"query_text": "x"}),
            StepSpec(tool="final_answer", args={}),
        ])
        ex.run(fw, query="y")
        self.assertNotIn("from_step", captured.get("find_images_indices", {}))
        self.assertNotIn("reference_images",
                         captured.get("find_images_indices", {}))

    # 4. Dedup per NOME-TOOL: il proposer del turno di ripresa rigenera lo
    #    stesso produttore con CHIAVI-ARG DIVERSE (time_window→time_windows+size,
    #    scelta dell'LLM) — è la stessa ri-esecuzione, va saltata comunque.
    #    (Caso reale e2e 23/6: senza questo, find_events_empty ri-girava.)
    def test_dedup_matches_by_tool_name_despite_different_args(self):
        fw = Framework(steps=[
            # ri-emesso con args COMPLETAMENTE diversi: stesso tool → skip
            StepSpec(tool="find_events_empty",
                     args={"time_windows": ["next-week"], "size": "1hour"}),
            StepSpec(tool="final_answer", args={}),
        ])
        seen, _ = self._run(fw, self._done_seed(
            args={"time_window": "this-week"}))
        self.assertNotIn("find_events_empty", seen)

    # 5. REGRESSIONE: senza done-seed nessun dedup (tutti gli step girano).
    def test_no_done_seed_no_dedup(self):
        fw = Framework(steps=[
            StepSpec(tool="find_events_empty", args={"time_window": "this-week"}),
            StepSpec(tool="final_answer", args={}),
        ])
        seen, _ = self._run(fw, [])  # nessun seed
        self.assertIn("find_events_empty", seen)

    # 6. Un tool DIVERSO dal done-seed NON è dedup-ato (gira normalmente).
    def test_different_tool_not_deduped(self):
        fw = Framework(steps=[
            StepSpec(tool="create_events", args={"from_step": 1}),
            StepSpec(tool="final_answer", args={}),
        ])
        seen, _ = self._run(fw, self._done_seed(tool="find_events_empty"))
        self.assertIn("create_events", seen)


class ResumeSeedBuildTests(unittest.TestCase):
    """Costruzione del seed di RIPRESA dialogo (ADR 0177 M1): i marker di
    dialogo (get_inputs/get_approval/@uploaded) NON entrano nel seed (non sono
    produttori, romperebbero l'allineamento from_step); i produttori sì, come
    kind="done". Test sul comportamento osservabile via Executor (no LLM)."""

    def test_dialog_markers_filtered_producers_kept(self):
        # Replica la logica di _try_engine_v2 (resume_steps → seed kind=done).
        from engine.types import StepRun
        resume_steps = [
            {"step": 1, "tool": "read_messages", "args": {"folder": "INBOX"},
             "observation": {"ok": True, "entries": [{"subject": "x"}]}},
            {"step": 2, "tool": "get_inputs", "args": {"dialog": "<e>"},
             "observation": {"ok": True, "decision": "completed",
                             "values": {"c": "1"}}},
        ]
        _markers = {"get_inputs", "get_approval", "@uploaded"}
        producers = [s for s in resume_steps
                     if (s.get("tool") or "") not in _markers]
        seed = [StepRun(step_idx=i, tool=s["tool"], args=s["args"],
                        result=s["observation"], ok=True, latency_ms=0,
                        kind="done")
                for i, s in enumerate(producers, start=1)]
        # Solo read_messages nel seed (get_inputs filtrato).
        self.assertEqual([s.tool for s in seed], ["read_messages"])
        # È un done-tool → dedup-ato se ri-emesso.
        from engine.executor import _seed_done_tools
        self.assertEqual(_seed_done_tools(seed), {"read_messages"})

    def test_done_producer_deduped_downstream_resolves(self):
        # Seed: read_messages done. Piano del proposer ri-emette read_messages
        # (skip) + send_messages(from_step=1) → send riceve le entries del seed.
        from engine.types import StepRun
        seed = [StepRun(step_idx=1, tool="read_messages", args={"folder": "INBOX"},
                        result={"ok": True, "entries": [{"subject": "A"},
                                                        {"subject": "B"}]},
                        ok=True, latency_ms=0, kind="done")]
        captured = {}

        def invoke(tool, args):
            captured[tool] = dict(args)
            return {"ok": True, "results": [{"ok": True}]}

        ex = Executor(invoke_executor=invoke, seed_steps=seed, catalog=[])
        fw = Framework(steps=[
            StepSpec(tool="read_messages", args={"account": "all"}),  # ri-emesso
            StepSpec(tool="send_messages", args={"from_step": 1}),
            StepSpec(tool="final_answer", args={}),
        ])
        ex.run(fw, query="manda riassunto")
        self.assertNotIn("read_messages", captured)  # dedup
        self.assertIn("send_messages", captured)
        # send ha ricevuto le 2 mail del seed via from_step=1.
        self.assertEqual(len(captured["send_messages"].get("entries") or []), 2)


class RenderPriorStepsTests(unittest.TestCase):
    """_render_prior_steps: blocco «FATTO FINORA» per il proposer (ADR 0177 M1).
    Solo kind="done"; vuoto (byte-identico) altrimenti; deterministico §7.9."""

    def _done(self, idx, tool, n):
        return StepRun(step_idx=idx, tool=tool, args={},
                       result={"ok": True, "entries": [{} for _ in range(n)]},
                       ok=True, latency_ms=0, kind="done")

    def test_empty_when_no_prior(self):
        from engine.proposer import _render_prior_steps
        self.assertEqual(_render_prior_steps([], "it"), "")
        self.assertEqual(_render_prior_steps(None, "en"), "")

    def test_empty_when_only_input_kind(self):
        # foto @uploaded (kind="input") NON è «fatto» → niente blocco.
        from engine.proposer import _render_prior_steps
        inp = StepRun(step_idx=0, tool="@uploaded", args={},
                      result={"ok": True, "entries": [{"path": "/a.jpg"}]},
                      ok=True, latency_ms=0, kind="input")
        self.assertEqual(_render_prior_steps([inp], "it"), "")

    def test_renders_done_steps_it(self):
        from engine.proposer import _render_prior_steps
        out = _render_prior_steps([self._done(1, "find_events_empty", 2)], "it")
        self.assertIn("FATTO FINORA", out)
        self.assertIn("find_events_empty", out)
        self.assertIn("2 risultati", out)
        self.assertTrue(out.startswith("\n"))  # inline interpolation

    def test_renders_done_steps_en(self):
        from engine.proposer import _render_prior_steps
        out = _render_prior_steps([self._done(1, "find_events_empty", 3)], "en")
        self.assertIn("DONE SO FAR", out)
        self.assertIn("3 results", out)

    def test_deterministic(self):
        from engine.proposer import _render_prior_steps
        seed = [self._done(1, "find_events_empty", 2),
                self._done(2, "read_messages", 5)]
        self.assertEqual(_render_prior_steps(seed, "it"),
                         _render_prior_steps(seed, "it"))


if __name__ == "__main__":
    unittest.main()
