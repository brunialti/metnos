"""test_align_foreign_producers_v3 — guard §7.9 _align_framework_objects, ramo
v3 «produttore-fantasma» (banco caso 3, 22/6).

Un PRODUTTORE (read/find/get/list) e' legittimo solo se il suo oggetto e' un
oggetto-PRODUTTORE dell'intent. Un produttore con oggetto preso SOLO da una
clausola CONSUMER — es. `read_files` per «salvali in un csv» (object=files dalla
clausola write) — e' un fantasma del proposer flaky:
  - se l'oggetto-produttore reale NON e' coperto → RIALLINEA (read_urls_html→…);
  - se e' gia' coperto da un produttore legittimo E lo step e' ORFANO → DROP.

Gating is_v3() (METNOS_ENGINE=v3): in v2/metis questo ramo NON gira (storico
byte-invariato). I test forzano l'env e lo ripristinano.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_RT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_RT))
from engine import dispatch  # noqa: E402
from engine.types import Framework, StepSpec  # noqa: E402


class _Cat:
    def __init__(self, name): self.name = name


_CATALOG = [_Cat(n) for n in (
    "read_messages", "find_messages", "read_files", "find_files",
    "read_urls_html", "extract_entries", "filter_entries",
    "write_files_spreadsheet", "create_files_spreadsheet", "final_answer")]


def _mk(tool, **args):
    return StepSpec(tool=tool, args=args)


def _align(actions, steps):
    class _I:
        pass
    _I.actions = actions
    fw = Framework(steps=steps, final_message="")
    out = dispatch._align_framework_objects(fw, _I(), _CATALOG)
    return out


class TestAlignForeignProducersV3(unittest.TestCase):
    def setUp(self):
        self._prev = os.environ.get("METNOS_ENGINE")
        os.environ["METNOS_ENGINE"] = "v3"

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("METNOS_ENGINE", None)
        else:
            os.environ["METNOS_ENGINE"] = self._prev

    # --- DROP: read_files orfano, read_messages copre gia' messages ---------
    def test_orphan_phantom_producer_dropped_and_rewired(self):
        out = _align(
            [{"verb": "find", "object": "messages"},
             {"verb": "extract", "object": "texts"},
             {"verb": "write", "object": "files"}],
            [_mk("read_files", paths=["/tmp"]),
             _mk("read_messages", account="all"),
             _mk("extract_entries", from_step=2),
             _mk("filter_entries", from_step=3),
             _mk("write_files_spreadsheet", from_step=4),
             _mk("final_answer")])
        tools = [s.tool for s in out.steps]
        self.assertNotIn("read_files", tools)
        self.assertEqual(tools,
                         ["read_messages", "extract_entries", "filter_entries",
                          "write_files_spreadsheet", "final_answer"])
        # from_step rimappati: extract 2→1, filter 3→2, write 4→3
        self.assertEqual(out.steps[1].args.get("from_step"), 1)
        self.assertEqual(out.steps[2].args.get("from_step"), 2)
        self.assertEqual(out.steps[3].args.get("from_step"), 3)

    # --- REALIGN: produttore-fantasma UNICO (nessun produttore legittimo) ----
    def test_sole_phantom_producer_realigned(self):
        out = _align(
            [{"verb": "find", "object": "messages"},
             {"verb": "extract", "object": "texts"},
             {"verb": "create", "object": "files"}],
            [_mk("read_urls_html", urls=["http://x"]),
             _mk("extract_entries", from_step=1),
             _mk("create_files_spreadsheet", from_step=2),
             _mk("final_answer")])
        tools = [s.tool for s in out.steps]
        self.assertEqual(tools[0], "read_messages")
        self.assertNotIn("urls", out.steps[0].args)  # args azzerati

    # --- NO-OP: files E' un oggetto-produttore (leggi-file-e-crea-foglio) -----
    def test_files_as_producer_object_untouched(self):
        steps = [_mk("read_files", paths=["/d"]),
                 _mk("extract_entries", from_step=1),
                 _mk("create_files_spreadsheet", from_step=2),
                 _mk("final_answer")]
        before = [s.tool for s in steps]
        out = _align(
            [{"verb": "read", "object": "files"},
             {"verb": "extract", "object": "texts"},
             {"verb": "create", "object": "files"}],
            steps)
        self.assertEqual([s.tool for s in out.steps], before)

    # --- CONSUMED: produttore-fantasma NON orfano → niente drop cieco --------
    def test_consumed_phantom_not_dropped(self):
        out = _align(
            [{"verb": "find", "object": "messages"},
             {"verb": "extract", "object": "texts"},
             {"verb": "write", "object": "files"}],
            [_mk("read_messages", account="all"),
             _mk("read_files", paths=["/tmp"]),
             _mk("extract_entries", from_step=2),   # consuma read_files
             _mk("write_files_spreadsheet", from_step=3),
             _mk("final_answer")])
        tools = [s.tool for s in out.steps]
        # read_files consumato da extract → resta (drop romperebbe la pipe)
        self.assertIn("read_files", tools)
        self.assertEqual(out.steps[2].args.get("from_step"), 2)

    # --- v2/metis: ramo NON attivo, comportamento storico (all_objs) ---------
    def test_v2_untouched_keeps_phantom(self):
        os.environ["METNOS_ENGINE"] = "metis"
        out = _align(
            [{"verb": "find", "object": "messages"},
             {"verb": "extract", "object": "texts"},
             {"verb": "write", "object": "files"}],
            [_mk("read_files", paths=["/tmp"]),
             _mk("read_messages", account="all"),
             _mk("extract_entries", from_step=2),
             _mk("write_files_spreadsheet", from_step=3),
             _mk("final_answer")])
        tools = [s.tool for s in out.steps]
        # v2 usa all_objs: files ∈ all_objs (clausola write) → read_files NON
        # e' considerato fantasma → resta (drop e' solo-v3).
        self.assertIn("read_files", tools)


if __name__ == "__main__":
    unittest.main()
