"""§7.9 move-enumeration: «sposta i file DA una cartella X a Y» → il proposer
passa X come singola dir-entry a move_files (safety-net la rifiuta, 0 spostati).
Il guard `_enrich_move_source_dir` inserisce find_files(base=X)→move(from_step).

Solo move_files (mai delete). Segnale: query matcha `fs.files_in_folder`; «sposta
la cartella X» (senza «file») NON matcha.

Run: `python3 -m pytest tests/runtime/executors/test_enrich_move_source_dir.py -xvs`.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_RT = (Path(__file__).resolve().parents[3] / "runtime")


class _Cat:
    def __init__(self, name):
        self.name = name


_CATALOG = [_Cat(n) for n in ("find_files", "move_files", "delete_files")]


class EnrichMoveSourceDirTests(unittest.TestCase):
    def setUp(self):
        self._prev = os.environ.get("METNOS_ENGINE")
        os.environ["METNOS_ENGINE"] = "v3"
        import detection_lexicon as dl
        dl.ensure_seeded()

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("METNOS_ENGINE", None)
        else:
            os.environ["METNOS_ENGINE"] = self._prev

    def _run(self, steps, query, catalog=_CATALOG):
        from engine.dispatch import _enrich_move_source_dir
        from engine.types import StepSpec, Framework
        fw = Framework(steps=[StepSpec(tool=t, args=dict(a)) for t, a in steps],
                       final_message="")
        out = _enrich_move_source_dir(fw, query, catalog)
        return [(s.tool, dict(s.args or {})) for s in out.steps]

    def test_move_files_from_folder_enumerated(self):
        out = self._run(
            [("move_files", {"entries": [{"path": "/tmp/a"}],
                             "dst_template": "/tmp/b/{name}"}),
             ("final_answer", {})],
            query="sposta i file da /tmp/a a /tmp/b")
        self.assertEqual(out[0], ("find_files", {"base_path": "/tmp/a"}))
        self.assertEqual(out[1][0], "move_files")
        self.assertEqual(out[1][1]["from_step"], 1)
        self.assertNotIn("entries", out[1][1])
        self.assertEqual(out[1][1]["dst_template"], "/tmp/b/{name}")

    def test_paths_form_also_enumerated(self):
        out = self._run(
            [("move_files", {"paths": ["/tmp/a"], "dst_template": "/tmp/b/{name}"})],
            query="move the files from /tmp/a to /tmp/b")
        self.assertEqual(out[0][0], "find_files")
        self.assertNotIn("paths", out[1][1])
        self.assertEqual(out[1][1]["from_step"], 1)

    def test_move_folder_itself_untouched(self):
        # «sposta la cartella X» (senza «file») NON matcha → X si sposta com'è.
        out = self._run(
            [("move_files", {"entries": [{"path": "/tmp/a"}],
                             "dst_template": "/tmp/b/{name}"})],
            query="sposta la cartella /tmp/a in /tmp/b")
        self.assertEqual(out[0][0], "move_files")
        self.assertEqual(len(out), 1)

    def test_delete_never_enumerated(self):
        # Solo move: un delete con dir-entry NON viene toccato (troppo rischioso).
        out = self._run(
            [("delete_files", {"entries": [{"path": "/tmp/a"}]})],
            query="cancella i file da /tmp/a")
        self.assertEqual(out[0][0], "delete_files")

    def test_glob_source_untouched(self):
        out = self._run(
            [("move_files", {"paths": ["/tmp/a/*"], "dst_template": "/tmp/b/{name}"})],
            query="sposta i file da /tmp/a a /tmp/b")
        self.assertEqual(out[0][0], "move_files")

    def test_already_from_step_untouched(self):
        out = self._run(
            [("find_files", {"base_path": "/tmp/a"}),
             ("move_files", {"from_step": 1, "dst_template": "/tmp/b/{name}"})],
            query="sposta i file da /tmp/a a /tmp/b")
        self.assertEqual([t for t, _ in out], ["find_files", "move_files"])

    def test_missing_find_files_noop(self):
        out = self._run(
            [("move_files", {"entries": [{"path": "/tmp/a"}]})],
            query="sposta i file da /tmp/a a /tmp/b",
            catalog=[_Cat("move_files")])
        self.assertEqual(out[0][0], "move_files")


if __name__ == "__main__":
    unittest.main()
