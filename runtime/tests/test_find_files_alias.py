"""find_files alias bilingue IT/EN + suggested_paths (D.1, 22/5/2026).

Test che verificano:
- _resolve_path_with_alias trova path esistenti via alias bilingue IT↔EN.
- _home_dir_suggestions ordina XDG dirs prima.
- find() ritorna suggested_paths quando ERR_PATH_NOT_FOUND.

Run: python3 -m pytest runtime/tests/test_find_files_alias.py -v
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_RUNTIME = Path(__file__).resolve().parent.parent
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))


class AliasResolverTests(unittest.TestCase):
    """`_resolve_path_with_alias` cross-lingua + fallback path."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_existing_path_returns_unchanged(self):
        from backends.files.local import _resolve_path_with_alias
        target = self.home / "real_dir"
        target.mkdir()
        resolved, note = _resolve_path_with_alias(str(target))
        self.assertEqual(resolved, target)
        self.assertIsNone(note)

    def test_italian_immagini_resolves_to_english_pictures(self):
        from backends.files.local import _resolve_path_with_alias
        with mock.patch("backends.files.local.Path.home", return_value=self.home):
            (self.home / "Pictures").mkdir()
            resolved, note = _resolve_path_with_alias(str(self.home / "Immagini"))
            self.assertEqual(resolved, self.home / "Pictures")
            self.assertIsNotNone(note)
            self.assertIn("alias bilingue", note)

    def test_english_pictures_resolves_to_italian_immagini(self):
        from backends.files.local import _resolve_path_with_alias
        with mock.patch("backends.files.local.Path.home", return_value=self.home):
            (self.home / "Immagini").mkdir()
            resolved, note = _resolve_path_with_alias(str(self.home / "Pictures"))
            self.assertEqual(resolved, self.home / "Immagini")
            self.assertIsNotNone(note)

    def test_no_alias_match_returns_original(self):
        from backends.files.local import _resolve_path_with_alias
        # `Immagini` non esiste e nessun alias presente nella home tempdir
        with mock.patch("backends.files.local.Path.home", return_value=self.home):
            target = self.home / "Immagini"
            resolved, note = _resolve_path_with_alias(str(target))
            self.assertEqual(resolved, target)
            self.assertIsNone(note)

    def test_lowercase_images_match(self):
        """Caso utente Roberto: ~/images lowercase (no XDG)."""
        from backends.files.local import _resolve_path_with_alias
        with mock.patch("backends.files.local.Path.home", return_value=self.home):
            (self.home / "images").mkdir()
            resolved, note = _resolve_path_with_alias(str(self.home / "Immagini"))
            self.assertEqual(resolved, self.home / "images")
            self.assertIsNotNone(note)


class HomeSuggestionsTests(unittest.TestCase):
    """`_home_dir_suggestions` ordina XDG prima, altre alfabetiche."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        # XDG dirs + dirs custom + hidden
        for name in ("Pictures", "Documents", "Music", "my_project",
                     "work_repo", ".hidden_dir"):
            (self.home / name).mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_xdg_dirs_first(self):
        from backends.files.local import _home_dir_suggestions
        with mock.patch("backends.files.local.Path.home", return_value=self.home):
            out = _home_dir_suggestions("nothing")
        # I primi 3 devono essere XDG: Documents, Music, Pictures (alfabetico)
        names = [Path(p).name for p in out[:3]]
        self.assertIn("Pictures", names)
        self.assertIn("Documents", names)
        self.assertIn("Music", names)

    def test_hidden_dirs_excluded(self):
        from backends.files.local import _home_dir_suggestions
        with mock.patch("backends.files.local.Path.home", return_value=self.home):
            out = _home_dir_suggestions("x")
        for p in out:
            self.assertFalse(Path(p).name.startswith("."))

    def test_limit_respected(self):
        from backends.files.local import _home_dir_suggestions
        with mock.patch("backends.files.local.Path.home", return_value=self.home):
            out = _home_dir_suggestions("x", limit=2)
        self.assertEqual(len(out), 2)


class FindErrorPathTests(unittest.TestCase):
    """`find()` ritorna `suggested_paths` quando path non esiste."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        (self.home / "Documents").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_path_not_found_includes_suggestions(self):
        from backends.files import local
        with mock.patch("backends.files.local.Path.home", return_value=self.home):
            r = local.find({
                "base_path": str(self.home / "Inesistente"),
                "patterns": ["*"],
            })
        self.assertFalse(r["ok"])
        self.assertEqual(r["error_code"], "ERR_PATH_NOT_FOUND")
        self.assertIn("suggested_paths", r)
        self.assertTrue(any("Documents" in p for p in r["suggested_paths"]))


if __name__ == "__main__":
    unittest.main()
