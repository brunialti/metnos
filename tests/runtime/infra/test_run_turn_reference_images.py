"""Tests per il param `reference_images` di run_turn (ADR 0092, 5/5/2026).

Validano che le foto allegate vengano iniettate come step 0 virtuale
nel scratchpad/history e che il PLANNER possa raggiungerle via
`from_step=1`.

Approccio: NON facciamo girare il PLANNER LLM (lento + non deterministico).
Costruiamo direttamente la `history_for_refs` attraverso run_turn ma con
un PLANNER stub che chiama `final_answer` subito; ispezioniamo il TurnLog
per confermare che lo step 0 virtuale e' presente con le entries giuste.

Run: `python3 -m pytest tests/runtime/infra/test_run_turn_reference_images.py -xvs`.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


class RunTurnReferenceImagesTests(unittest.TestCase):

    def setUp(self):
        # Isola HOME e altri path
        self._tmpdir = tempfile.TemporaryDirectory()
        td = Path(self._tmpdir.name)
        self._orig_home = os.environ.get("HOME")
        os.environ["HOME"] = str(td)
        os.environ["METNOS_HISTORY_DIR"] = str(td / "history")
        # ADR 0177 M1: di default le foto allegate vanno all'ENGINE. Questo test
        # valida l'iniezione `@uploaded` del path PLANNER legacy (fallback,
        # ancora vivo): pin a 0 per restare deterministico+veloce. Il path engine
        # ha il suo oracolo: tests/test_engine_seed_uploads.py.
        self._orig_eng_up = os.environ.get("METNOS_ENGINE_UPLOADS")
        os.environ["METNOS_ENGINE_UPLOADS"] = "0"
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
        if self._orig_eng_up is not None:
            os.environ["METNOS_ENGINE_UPLOADS"] = self._orig_eng_up
        else:
            os.environ.pop("METNOS_ENGINE_UPLOADS", None)

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
        """Un declino del motore sugli upload dà errore onesto e non riattiva
        il vecchio step virtuale del planner ritirato."""
        from unittest import mock
        import agent_runtime

        with mock.patch.object(agent_runtime, "_run_engine",
                               return_value=None), \
             mock.patch.object(agent_runtime, "try_fast_path",
                               return_value=None), \
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
        # NUOVO CONTRATTO (ADR 0181-ext): il PLANNER legacy è stato neutralizzato.
        # Con l'engine mockato che DECLINA (StubProvider → nessun framework
        # valido), un turno-upload non ricade più nel ReAct legacy (che iniettava
        # lo step-0 `@uploaded` in log.steps) — ora dà un esito ONESTO (§2.8).
        # Gli upload REALI passano dall'engine via seed_state `@uploaded`
        # (_run_engine, ADR 0177 M1); questo test mockava l'engine a vuoto,
        # quindi esercitava SOLO il vecchio fallback legacy, ora rimosso.
        self.assertEqual(log_obj.final_kind, "error",
                         f"engine declinato su upload → errore onesto atteso, "
                         f"non fallback legacy (final_kind={log_obj.final_kind})")
        self.assertNotIn("@uploaded", [s.chosen_tool for s in log_obj.steps],
                         "il ReAct legacy (step-0 @uploaded) non deve più girare")

    # --- 4. run_turn no-op senza reference_images -------------------------

    def test_run_turn_no_virtual_step_when_no_reference_images(self):
        """Senza reference_images, no virtual step (regression guard)."""
        from unittest import mock
        import agent_runtime

        with mock.patch.object(agent_runtime, "_run_engine",
                               return_value=None), \
             mock.patch.object(agent_runtime, "try_fast_path",
                               return_value=None):
            log_obj = agent_runtime.run_turn("query plain")
        zeroth = [s for s in log_obj.steps if s.step_num == 0]
        self.assertEqual(zeroth, [], "no virtual step expected without uploads")


if __name__ == "__main__":
    unittest.main()
