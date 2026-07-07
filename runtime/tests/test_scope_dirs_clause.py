"""§2.9 — «cancella i file E le directory NELLA cartella X» scopa la clausola
dirs ai CONTENUTI di X, non al contenitore (decisione Roberto 7/7).

Il proposer emette `delete_dirs(paths=[X], force)` sulla cartella-contenitore →
la rimuove RICORSIVAMENTE (over-deletion: sparisce X e i file annidati, turno
reale e6259280). Il guard `_scope_dirs_clause_to_contents` inserisce
`find_dirs(base_path=X)` e ripunta delete_dirs a from_step → colpisce le
SOTTODIR di X; X resta.

DISCRIMINANTE: scatta SOLO se delete_dirs bersaglia il contenitore di una
clausola FILE fratella. «cancella la cartella X» (senza clausola file) NON si
tocca — rimuovere X è ciò che l'utente chiede.

Run: `python3 -m pytest runtime/tests/test_scope_dirs_clause.py -xvs`.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


class _Cat:
    def __init__(self, name):
        self.name = name


_CATALOG = [_Cat(n) for n in
            ("find_dirs", "delete_dirs", "find_files", "delete_files")]


class ScopeDirsClauseTests(unittest.TestCase):
    def setUp(self):
        self._prev = os.environ.get("METNOS_ENGINE")
        os.environ["METNOS_ENGINE"] = "v3"

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("METNOS_ENGINE", None)
        else:
            os.environ["METNOS_ENGINE"] = self._prev

    def _run(self, steps):
        from engine.dispatch import _scope_dirs_clause_to_contents
        from engine.types import StepSpec, Framework
        fw = Framework(steps=[StepSpec(tool=t, args=a) for t, a in steps],
                       final_message="")
        out = _scope_dirs_clause_to_contents(fw, None, "q", _CATALOG)
        return [(s.tool, dict(s.args or {})) for s in out.steps]

    def test_glob_container_rewritten_to_find_dirs(self):
        # delete_files(X/*) + delete_dirs(paths=[X], force) → scoped ai subdir.
        out = self._run([
            ("delete_files", {"paths": ["/tmp/mdt/*"]}),
            ("delete_dirs", {"paths": ["/tmp/mdt"], "force": True}),
            ("final_answer", {}),
        ])
        self.assertEqual(out, [
            ("delete_files", {"paths": ["/tmp/mdt/*"]}),
            ("find_dirs", {"base_path": "/tmp/mdt"}),
            ("delete_dirs", {"from_step": 2, "force": True}),
            ("final_answer", {}),
        ])

    def test_find_files_base_container_rewritten(self):
        out = self._run([
            ("find_files", {"base_path": "/tmp/mdt"}),
            ("delete_files", {"from_step": 1}),
            ("delete_dirs", {"paths": ["/tmp/mdt"], "force": True}),
            ("final_answer", {}),
        ])
        # find_dirs prende posizione 3 → delete_dirs from_step=3
        self.assertEqual(out[2], ("find_dirs", {"base_path": "/tmp/mdt"}))
        self.assertEqual(out[3], ("delete_dirs", {"from_step": 3, "force": True}))

    def test_delete_folder_x_alone_untouched(self):
        # «cancella la cartella X»: nessuna clausola file → X va rimossa, intatto.
        out = self._run([
            ("delete_dirs", {"paths": ["/tmp/mdt"]}),
            ("final_answer", {}),
        ])
        self.assertEqual(out, [
            ("delete_dirs", {"paths": ["/tmp/mdt"]}),
            ("final_answer", {}),
        ])

    def test_named_subdir_untouched(self):
        # delete_dirs su una sottodir NOMINATA (non il contenitore) → intatto.
        out = self._run([
            ("delete_files", {"paths": ["/tmp/mdt/*"]}),
            ("delete_dirs", {"paths": ["/tmp/mdt/sub1"]}),
            ("final_answer", {}),
        ])
        self.assertEqual(out[1], ("delete_dirs", {"paths": ["/tmp/mdt/sub1"]}))

    def test_already_scoped_from_step_untouched(self):
        # delete_dirs già scopato a un produttore (from_step) → non ri-scrivere.
        out = self._run([
            ("find_dirs", {"base_path": "/tmp/mdt"}),
            ("delete_dirs", {"from_step": 1}),
            ("final_answer", {}),
        ])
        self.assertEqual(out, [
            ("find_dirs", {"base_path": "/tmp/mdt"}),
            ("delete_dirs", {"from_step": 1}),
            ("final_answer", {}),
        ])

    def test_windows_paths(self):
        # OS-agnostico: contenitore Windows col glob backslash.
        out = self._run([
            ("delete_files", {"paths": ["C:\\Users\\r\\Downloads\\*"]}),
            ("delete_dirs", {"paths": ["C:\\Users\\r\\Downloads"], "force": True}),
            ("final_answer", {}),
        ])
        self.assertEqual(out[1],
                         ("find_dirs", {"base_path": "C:\\Users\\r\\Downloads"}))
        self.assertEqual(out[2], ("delete_dirs",
                                  {"from_step": 2, "force": True}))

    def test_no_dirs_clause_noop(self):
        # solo file: nessun delete_dirs → no-op.
        out = self._run([
            ("delete_files", {"paths": ["/tmp/mdt/*"]}),
            ("final_answer", {}),
        ])
        self.assertEqual(out, [
            ("delete_files", {"paths": ["/tmp/mdt/*"]}),
            ("final_answer", {}),
        ])

    def test_missing_catalog_tools_noop(self):
        # catalogo privo di find_dirs → non riscrivere (fallback onesto).
        from engine.dispatch import _scope_dirs_clause_to_contents
        from engine.types import StepSpec, Framework
        fw = Framework(steps=[
            StepSpec(tool="delete_files", args={"paths": ["/tmp/mdt/*"]}),
            StepSpec(tool="delete_dirs", args={"paths": ["/tmp/mdt"]}),
            StepSpec(tool="final_answer", args={})], final_message="")
        out = _scope_dirs_clause_to_contents(fw, None, "q", [_Cat("delete_files")])
        self.assertEqual([s.tool for s in out.steps],
                         ["delete_files", "delete_dirs", "final_answer"])


if __name__ == "__main__":
    unittest.main()
