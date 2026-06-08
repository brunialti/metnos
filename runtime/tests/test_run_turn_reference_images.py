"""Tests per il param `reference_images` di run_turn (ADR 0092, 5/5/2026).

Validano che le foto allegate vengano iniettate come step 0 virtuale
nel scratchpad/history e che il PLANNER possa raggiungerle via
`from_step=1`.

Approccio: NON facciamo girare il PLANNER LLM (lento + non deterministico).
Costruiamo direttamente la `history_for_refs` attraverso run_turn ma con
un PLANNER stub che chiama `final_answer` subito; ispezioniamo il TurnLog
per confermare che lo step 0 virtuale e' presente con le entries giuste.

Run: `python3 -m pytest runtime/tests/test_run_turn_reference_images.py -xvs`.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


class RunTurnReferenceImagesTests(unittest.TestCase):

    def setUp(self):
        # Isola HOME e altri path
        self._tmpdir = tempfile.TemporaryDirectory()
        td = Path(self._tmpdir.name)
        self._orig_home = os.environ.get("HOME")
        os.environ["HOME"] = str(td)
        os.environ["METNOS_HISTORY_DIR"] = str(td / "history")
        # Catalog reale caricato QUI (verify=False: l'isolamento HOME sposta
        # KEYS_DIR=~/.config/metnos/keys e farebbe fallire la verify firma).
        # Va iniettato in run_turn via mock: questo test verifica l'iniezione
        # dello step virtuale @uploaded, NON il catalog-load — che sotto i mock
        # provider + HOME isolato ricostruisce a 0 (flake order-dependent 8/6
        # quando un test synth/admission ha invalidato la catalog-cache).
        import loader
        self._catalog = loader.load_catalog(verify=False)
        # Forza fast PLANNER stub via env (non chiamiamo provider)

    def tearDown(self):
        self._tmpdir.cleanup()
        if self._orig_home is not None:
            os.environ["HOME"] = self._orig_home
        else:
            os.environ.pop("HOME", None)

    # --- 1. virtual step injection in scratchpad ---------------------------

    def test_resolve_from_step_with_virtual_upload(self):
        """Con history_for_refs[0] = @uploaded virtual, `from_step=1` espande
        in `entries: [...]` (path consumer fallback) o nell'arg singolare
        matchato (`reference_images` perche' singular=`reference_image`)."""
        from agent_runtime import resolve_from_step
        upload_entries = [
            {"path": "/tmp/u/a.jpg", "reference_image": "/tmp/u/a.jpg",
             "source": "upload"},
            {"path": "/tmp/u/b.jpg", "reference_image": "/tmp/u/b.jpg",
             "source": "upload"},
        ]
        history = [{
            "step": 1, "tool": "@uploaded",
            "args": {"source": "upload"},
            "observation": {"ok": True, "entries": upload_entries},
        }]
        # Schema find_images_indices: reference_images array
        consumer_schema = {
            "type": "object",
            "properties": {
                "reference_images": {"type": "array",
                                      "items": {"type": "string"}},
                "idx": {"type": "string"},
                "query_text": {"type": "string"},
            },
        }
        new_args, errors = resolve_from_step(
            {"from_step": 1, "idx": "scene"},
            history,
            consumer_schema=consumer_schema,
        )
        self.assertEqual(errors, [])
        # Auto-explode: reference_images deve contenere i path
        self.assertIn("reference_images", new_args)
        self.assertEqual(
            new_args["reference_images"],
            ["/tmp/u/a.jpg", "/tmp/u/b.jpg"],
        )
        self.assertNotIn("from_step", new_args)
        self.assertEqual(new_args["idx"], "scene")

    # --- 2. consumer-match adaptive_rerank picks find_images_indices ------

    def test_consumer_match_picks_find_images_indices(self):
        """Layer 3 consumer-match in adaptive_rerank deve includere
        find_images_indices (e simili executor con `reference_images` arg)
        quando entries hanno `reference_image` field. Verifica generale
        della convenzione naming Metnos (singolare di arg array consumer
        matcha campo entries) — non dipende dal catalog reale per evitare
        cross-test pollution (alcuni test cambiano DEFAULT_EXECUTORS_DIR
        o lifecycle filter)."""
        from adaptive_rerank import _produced_keys, consumer_match
        observation = {
            "ok": True,
            "entries": [
                {"path": "/tmp/u/a.jpg",
                 "reference_image": "/tmp/u/a.jpg",
                 "source": "upload"},
            ],
        }
        produced = _produced_keys(observation)
        # `reference_image` deriva da _norm_key('reference_images') (-s).
        self.assertIn("reference_image", produced)
        # Stub catalog con UN executor che ha reference_images (come fimi).
        class _StubExec:
            def __init__(self, name, args_schema):
                self.name = name
                self.args_schema = args_schema
        stub_catalog = [
            _StubExec("find_images_indices", {
                "type": "object",
                "properties": {
                    "reference_images": {"type": "array",
                                          "items": {"type": "string"}},
                    "idx": {"type": "string"},
                },
            }),
            _StubExec("read_files", {
                "type": "object",
                "properties": {
                    "paths": {"type": "array", "items": {"type": "string"}},
                },
            }),
        ]
        matched = consumer_match(
            catalog=stub_catalog, produced_keys=produced, exclude_names=set(),
        )
        names = [e.name for e in matched]
        self.assertIn("find_images_indices", names,
                      f"find_images_indices missing from match: {names}")
        # Anche read_files matcha (path → paths singolare).
        self.assertIn("read_files", names)

    # --- 3. run_turn injects virtual step (smoke con stub provider) -------

    def test_run_turn_injects_virtual_step_in_log(self):
        """run_turn con reference_images=[paths] deve aggiungere uno step
        con step_num=0 e chosen_tool='@uploaded' al log.steps, INDIPENDENTEMENTE
        dal comportamento del PLANNER (anche se quest'ultimo fallisce).

        Strategia: costruiamo un fake LLM provider che ritorna un final_answer
        immediato cosi' il loop esce subito."""
        # Stub providers cosi' run_turn non chiama LLM reali
        from unittest import mock
        import agent_runtime

        # Build minimal stub provider: chat() ritorna final_answer al primo step
        class StubResp:
            def __init__(self):
                self.text = ""
                self.thinking = ""
                self.in_tokens = 0
                self.out_tokens = 0
                self.latency_ms = 1
                # tool_calls deve contenere 1 call a final_answer
                from llm_provider import ToolCall
                self.tool_calls = [ToolCall(
                    call_id="c1", name="final_answer",
                    arguments={"final_answer": "test"},
                )]
                self.cost_usd = 0.0
                self.model = "stub"

        class StubProvider:
            name = "stub"
            def chat(self, system, user, **kwargs):
                return StubResp()
            def chat_with_tools(self, system, user, tools, history=None,
                                 **kwargs):
                return StubResp()

        # Patch make_provider_from_spec to return stub
        with mock.patch.object(agent_runtime, "make_provider_from_spec",
                                return_value=StubProvider()), \
             mock.patch.object(agent_runtime, "OllamaProvider",
                                return_value=StubProvider()), \
             mock.patch.object(agent_runtime, "rank_adaptive",
                                return_value=([], {"chosen_k": 0,
                                                    "confidence": 0.0,
                                                    "reason": "test"})), \
             mock.patch.object(agent_runtime, "load_catalog",
                                return_value=self._catalog), \
             mock.patch("loader.load_catalog", return_value=self._catalog):
            # Crea 2 file dummy
            tmp = Path(self._tmpdir.name)
            ref1 = tmp / "ref1.jpg"; ref1.write_bytes(b"\xff\xd8\xff\xe0")
            ref2 = tmp / "ref2.jpg"; ref2.write_bytes(b"\xff\xd8\xff\xe0")
            log_obj = agent_runtime.run_turn(
                "trova foto simili",
                reference_images=[str(ref1), str(ref2)],
            )
        # Lo step 0 virtuale deve essere presente
        steps_by_num = [s for s in log_obj.steps if s.step_num == 0]
        self.assertEqual(len(steps_by_num), 1,
                          f"step 0 virtuale mancante: steps={[s.step_num for s in log_obj.steps]}")
        v = steps_by_num[0]
        self.assertEqual(v.chosen_tool, "@uploaded")
        self.assertTrue(v.result.get("ok"))
        self.assertEqual(len(v.result.get("entries", [])), 2)
        self.assertEqual(v.result["entries"][0]["path"], str(ref1))
        self.assertEqual(v.result["entries"][0]["reference_image"], str(ref1))

    # --- 4. run_turn no-op senza reference_images -------------------------

    def test_run_turn_no_virtual_step_when_no_reference_images(self):
        """Senza reference_images, no virtual step (regression guard)."""
        from unittest import mock
        import agent_runtime

        class StubResp:
            def __init__(self):
                self.text = ""
                self.thinking = ""
                self.in_tokens = 0
                self.out_tokens = 0
                self.latency_ms = 1
                from llm_provider import ToolCall
                self.tool_calls = [ToolCall(
                    call_id="c1", name="final_answer",
                    arguments={"final_answer": "ok"},
                )]
                self.cost_usd = 0.0
                self.model = "stub"

        class StubProvider:
            name = "stub"
            def chat(self, *a, **kw):
                return StubResp()
            def chat_with_tools(self, *a, **kw):
                return StubResp()

        with mock.patch.object(agent_runtime, "make_provider_from_spec",
                                return_value=StubProvider()), \
             mock.patch.object(agent_runtime, "OllamaProvider",
                                return_value=StubProvider()), \
             mock.patch.object(agent_runtime, "rank_adaptive",
                                return_value=([], {"chosen_k": 0,
                                                    "confidence": 0.0,
                                                    "reason": "test"})):
            log_obj = agent_runtime.run_turn("query plain")
        zeroth = [s for s in log_obj.steps if s.step_num == 0]
        self.assertEqual(zeroth, [], "no virtual step expected without uploads")


if __name__ == "__main__":
    unittest.main()
