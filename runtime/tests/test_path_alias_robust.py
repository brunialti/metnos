"""test_path_alias_robust.py — path_alias non deve MAI crashare per un accesso
fs negato/fallito (§2.8). Regressione live 8/7: sotto AppContainer (Windows) una
root NON granted (`~/.local/share/metnos`) faceva alzare PermissionError (WinError
5) da `candidate_roots().is_dir()` → l'executor list_dirs moriva con stdout vuoto
("output non-JSON") invece di dare un errore pulito.
"""
from __future__ import annotations

import os
import pathlib
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import path_alias as pa


class TestPathAliasDenialRobust(unittest.TestCase):
    def _raise_perm(self, *a, **k):
        raise PermissionError("[WinError 5] Accesso negato (simulato)")

    def test_safe_is_dir_swallows_oserror(self):
        orig = pathlib.Path.is_dir
        pathlib.Path.is_dir = self._raise_perm
        try:
            self.assertFalse(pa._safe_is_dir(pathlib.Path("/qualsiasi")))
        finally:
            pathlib.Path.is_dir = orig

    def test_safe_exists_swallows_oserror(self):
        orig = pathlib.Path.exists
        pathlib.Path.exists = self._raise_perm
        try:
            self.assertFalse(pa._safe_exists(pathlib.Path("/qualsiasi")))
        finally:
            pathlib.Path.exists = orig

    def test_candidate_roots_no_crash_on_denied(self):
        # Ogni is_dir alza (tutte le root negate) → lista vuota, NIENTE crash.
        orig = pathlib.Path.is_dir
        pathlib.Path.is_dir = self._raise_perm
        try:
            self.assertEqual(pa.candidate_roots(), [])
        finally:
            pathlib.Path.is_dir = orig

    def test_resolve_path_with_alias_no_crash_on_denied(self):
        # Scenario del bug: is_dir/exists negati ovunque → ritorna un path,
        # niente eccezione (list_dirs poi darà ERR_PATH_NOT_FOUND pulito).
        oid, oex = pathlib.Path.is_dir, pathlib.Path.exists
        pathlib.Path.is_dir = self._raise_perm
        pathlib.Path.exists = self._raise_perm
        try:
            resolved, note = pa.resolve_path_with_alias("Documenti")
            self.assertIsNotNone(resolved)
        finally:
            pathlib.Path.is_dir, pathlib.Path.exists = oid, oex

    def test_home_dir_suggestions_no_crash_on_denied(self):
        oid = pathlib.Path.is_dir
        pathlib.Path.is_dir = self._raise_perm
        try:
            self.assertEqual(pa.home_dir_suggestions("Documenti"), [])
        finally:
            pathlib.Path.is_dir = oid


if __name__ == "__main__":
    unittest.main()
