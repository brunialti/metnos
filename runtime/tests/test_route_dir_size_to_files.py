"""§7.9 size-misroute (turn 5cdf80d0): «quanto è grande la cartella X» = peso
dei file RICORSIVI (find_files+compute sum size), NON conteggio sottodir.

Il guard `_route_folder_size` ripara due modi del proposer:
(A) STRUTTURALE: find_dirs(recursive)→compute(sum,size) → find_files+key→size.
(B) INTENTO (concept fs.size_query, lessico IT+EN): find_dirs terminale da solo
    → find_files(recursive)+compute(sum,size).

Run: `python3 -m pytest runtime/tests/test_route_dir_size_to_files.py -xvs`.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


class _Cat:
    def __init__(self, name):
        self.name = name


_CATALOG = [_Cat(n) for n in ("find_dirs", "find_files", "list_dirs",
                              "compute_entries")]


class RouteFolderSizeTests(unittest.TestCase):
    def _run(self, steps, query="", catalog=_CATALOG):
        from engine.dispatch import _route_folder_size
        from engine.types import StepSpec, Framework
        fw = Framework(steps=[StepSpec(tool=t, args=dict(a)) for t, a in steps],
                       final_message="")
        out = _route_folder_size(fw, query, catalog)
        return [(s.tool, dict(s.args or {})) for s in out.steps]

    # ── (A) strutturale — trigger senza query ─────────────────────────────
    def test_A_sum_size_on_find_dirs_rewritten(self):
        out = self._run([
            ("find_dirs", {"base_path": "C:\\Users\\r\\Downloads",
                           "recursive": True}),
            ("compute_entries", {"from_step": 1, "op": "sum", "key": "size"}),
            ("final_answer", {}),
        ])
        self.assertEqual(out[0], ("find_files",
                                  {"base_path": "C:\\Users\\r\\Downloads",
                                   "recursive": True}))
        self.assertEqual(out[1], ("compute_entries",
                                  {"from_step": 1, "op": "sum", "key": "size"}))

    def test_A_implicit_chaining_no_from_step(self):
        # turn 5cdf80d0 (PC): il planner OMETTE from_step; l'engine concatena
        # implicitamente → il produttore è lo step precedente.
        out = self._run([
            ("find_dirs", {"base_path": "C:\\Users\\r\\Downloads",
                           "recursive": True}),
            ("compute_entries", {"op": "sum", "key": "size"}),
        ])
        self.assertEqual(out[0][0], "find_files")
        self.assertTrue(out[0][1]["recursive"])

    def test_A_total_bytes_key_normalized_to_size(self):
        out = self._run([
            ("find_dirs", {"base_path": "/data"}),
            ("compute_entries", {"from_step": 1, "op": "sum",
                                 "key": "total_bytes"}),
        ])
        self.assertEqual(out[0][0], "find_files")
        self.assertTrue(out[0][1]["recursive"])
        self.assertEqual(out[1][1]["key"], "size")

    def test_A_avg_size_also_triggers(self):
        out = self._run([
            ("find_dirs", {"base_path": "/data"}),
            ("compute_entries", {"from_step": 1, "op": "avg", "key": "size"}),
        ])
        self.assertEqual(out[0][0], "find_files")

    def test_A_count_op_untouched(self):
        out = self._run([
            ("find_dirs", {"base_path": "/data"}),
            ("compute_entries", {"from_step": 1, "op": "count", "key": "path"}),
        ])
        self.assertEqual(out[0][0], "find_dirs")

    def test_A_sum_non_size_key_untouched(self):
        out = self._run([
            ("find_dirs", {"base_path": "/data"}),
            ("compute_entries", {"from_step": 1, "op": "sum",
                                 "key": "file_count"}),
        ])
        self.assertEqual(out[0][0], "find_dirs")

    def test_A_idempotent_on_find_files(self):
        out = self._run([
            ("find_files", {"base_path": "/data", "recursive": True}),
            ("compute_entries", {"from_step": 1, "op": "sum", "key": "size"}),
        ])
        self.assertEqual(out[0][0], "find_files")

    # ── (B) intento — find_dirs terminale + query fs.size_query ───────────
    def test_B_lone_find_dirs_with_size_query(self):
        out = self._run(
            [("find_dirs", {"base_path": "/opt/x", "recursive": True}),
             ("final_answer", {})],
            query="quanto è grande la cartella /opt/x")
        self.assertEqual(out[0], ("find_files",
                                  {"base_path": "/opt/x", "recursive": True}))
        self.assertEqual(out[1], ("compute_entries",
                                  {"from_step": 1, "op": "sum", "key": "size"}))
        self.assertEqual(out[2][0], "final_answer")

    def test_B_lone_list_dirs_with_size_query(self):
        out = self._run(
            [("list_dirs", {"path": "/opt/x"}), ("final_answer", {})],
            query="dimensione della cartella /opt/x")
        self.assertEqual(out[0][0], "find_files")
        self.assertEqual(out[0][1]["base_path"], "/opt/x")
        self.assertTrue(out[0][1]["recursive"])
        self.assertEqual(out[1][1], {"from_step": 1, "op": "sum", "key": "size"})

    def test_B_english_how_big(self):
        out = self._run(
            [("find_dirs", {"base_path": "/opt/x"}), ("final_answer", {})],
            query="how big is the folder /opt/x")
        self.assertEqual(out[0][0], "find_files")
        self.assertEqual(out[1][0], "compute_entries")

    def test_B_no_size_query_untouched(self):
        # «quante cartelle ci sono» = conteggio dir → find_dirs LEGITTIMO.
        out = self._run(
            [("find_dirs", {"base_path": "/opt/x", "recursive": True}),
             ("final_answer", {})],
            query="quante cartelle ci sono in /opt/x")
        self.assertEqual(out[0][0], "find_dirs")
        self.assertEqual(len(out), 2)

    def test_B_consumed_producer_untouched(self):
        # find_dirs consumato da un altro step (non terminale) → il modo B non
        # rimodella (evita renumber rischioso); resta al modo A se applicabile.
        out = self._run(
            [("find_dirs", {"base_path": "/opt/x"}),
             ("filter_entries", {"from_step": 1}),
             ("final_answer", {})],
            query="quanto è grande la cartella /opt/x")
        self.assertEqual(out[0][0], "find_dirs")

    def test_B_missing_compute_catalog_noop(self):
        out = self._run(
            [("find_dirs", {"base_path": "/opt/x"}), ("final_answer", {})],
            query="quanto è grande la cartella /opt/x",
            catalog=[_Cat("find_dirs"), _Cat("find_files")])
        self.assertEqual(out[0][0], "find_dirs")

    # ── guardie generali ──────────────────────────────────────────────────
    def test_missing_find_files_noop(self):
        out = self._run(
            [("find_dirs", {"base_path": "/data"}),
             ("compute_entries", {"from_step": 1, "op": "sum", "key": "size"})],
            catalog=[_Cat("find_dirs"), _Cat("compute_entries")])
        self.assertEqual(out[0][0], "find_dirs")

    def test_A_baked_stale_entries_repiped(self):
        # cache L0: entries CONCRETE di find_dirs bake-ate in compute (bug PC
        # 5cdf80d0). Il rewrite scarta le stantie e ripipa dal produttore.
        out = self._run([
            ("find_dirs", {"base_path": "/data"}),
            ("compute_entries", {"op": "sum", "key": "size",
                                 "entries": [{"total_bytes": 1}]}),
        ])
        self.assertEqual(out[0][0], "find_files")
        self.assertNotIn("entries", out[1][1])          # bake-ate scartate
        self.assertEqual(out[1][1]["from_step"], 1)      # re-pipe live


if __name__ == "__main__":
    unittest.main()
