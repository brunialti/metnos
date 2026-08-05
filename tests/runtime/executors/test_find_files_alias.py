"""find_files alias bilingue IT/EN + suggested_paths (D.1, 22/5/2026).

Test che verificano:
- _resolve_path_with_alias trova path esistenti via alias bilingue IT↔EN.
- _home_dir_suggestions ordina XDG dirs prima.
- find() ritorna suggested_paths quando ERR_PATH_NOT_FOUND.

Run: python3 -m pytest tests/runtime/executors/test_find_files_alias.py -v
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


class AliasResolverTests(unittest.TestCase):
    """`_resolve_path_with_alias` cross-lingua + fallback path."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        # Patch sia Path.home sia _candidate_roots per isolare dai mount
        # NAS/media presenti sul sistema reale (es. /tmp/nas_public/media).
        self._patches = [
            mock.patch("path_alias.Path.home", return_value=self.home),
            mock.patch("path_alias.candidate_roots",
                       return_value=[self.home]),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.tmp.cleanup()

    def test_existing_path_returns_unchanged(self):
        from path_alias import resolve_path_with_alias as _resolve_path_with_alias
        target = self.home / "real_dir"
        target.mkdir()
        resolved, note = _resolve_path_with_alias(str(target))
        self.assertEqual(resolved, target)
        self.assertIsNone(note)

    def test_italian_immagini_resolves_to_english_pictures(self):
        from path_alias import resolve_path_with_alias as _resolve_path_with_alias
        (self.home / "Pictures").mkdir()
        resolved, note = _resolve_path_with_alias(str(self.home / "Immagini"))
        self.assertEqual(resolved, self.home / "Pictures")
        self.assertIsNotNone(note)
        self.assertIn("alias bilingue", note)

    def test_english_pictures_resolves_to_italian_immagini(self):
        from path_alias import resolve_path_with_alias as _resolve_path_with_alias
        (self.home / "Immagini").mkdir()
        resolved, note = _resolve_path_with_alias(str(self.home / "Pictures"))
        self.assertEqual(resolved, self.home / "Immagini")
        self.assertIsNotNone(note)

    def test_no_alias_match_returns_original(self):
        from path_alias import resolve_path_with_alias as _resolve_path_with_alias
        # `Immagini` non esiste e nessun alias presente nella home tempdir
        target = self.home / "Immagini"
        resolved, note = _resolve_path_with_alias(str(target))
        self.assertEqual(resolved, target)
        self.assertIsNone(note)

    def test_lowercase_images_match(self):
        """Caso utente Roberto: ~/images lowercase (no XDG)."""
        from path_alias import resolve_path_with_alias as _resolve_path_with_alias
        (self.home / "images").mkdir()
        resolved, note = _resolve_path_with_alias(str(self.home / "Immagini"))
        self.assertEqual(resolved, self.home / "images")
        self.assertIsNotNone(note)

    def test_multi_root_picks_largest_when_multiple(self):
        """Quando piu' root contengono lo stesso alias, sceglie il piu' grande."""
        from path_alias import resolve_path_with_alias as _resolve_path_with_alias
        # Crea due roots con la stessa cartella Pictures, popolata diversamente.
        other_root = Path(tempfile.mkdtemp())
        try:
            (self.home / "Pictures").mkdir()
            # 2 file in home/Pictures
            for i in range(2):
                (self.home / "Pictures" / f"f{i}.txt").write_text("x")
            (other_root / "Pictures").mkdir()
            # 10 file in other_root/Pictures
            for i in range(10):
                (other_root / "Pictures" / f"f{i}.txt").write_text("x")
            # Patch roots a includere entrambi
            with mock.patch("path_alias.candidate_roots",
                            return_value=[self.home, other_root]):
                resolved, note = _resolve_path_with_alias(
                    str(self.home / "Immagini"))
            self.assertEqual(resolved, other_root / "Pictures")
            self.assertIn("trovati 2", note)
            self.assertIn("piu' grande", note)
        finally:
            import shutil
            shutil.rmtree(other_root, ignore_errors=True)


class HomeSuggestionsTests(unittest.TestCase):
    """`_home_dir_suggestions` ordina XDG prima, altre alfabetiche."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        # XDG dirs + dirs custom + hidden
        for name in ("Pictures", "Documents", "Music", "my_project",
                     "work_repo", ".hidden_dir"):
            (self.home / name).mkdir()
        self._patches = [
            mock.patch("path_alias.Path.home", return_value=self.home),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.tmp.cleanup()

    def test_xdg_dirs_first(self):
        from path_alias import home_dir_suggestions as _home_dir_suggestions
        out = _home_dir_suggestions("nothing")
        # I primi 3 devono essere XDG: Documents, Music, Pictures (alfabetico)
        names = [Path(p).name for p in out[:3]]
        self.assertIn("Pictures", names)
        self.assertIn("Documents", names)
        self.assertIn("Music", names)

    def test_hidden_dirs_excluded(self):
        from path_alias import home_dir_suggestions as _home_dir_suggestions
        out = _home_dir_suggestions("x")
        for p in out:
            self.assertFalse(Path(p).name.startswith("."))

    def test_limit_respected(self):
        from path_alias import home_dir_suggestions as _home_dir_suggestions
        out = _home_dir_suggestions("x", limit=2)
        self.assertEqual(len(out), 2)


class FindErrorPathTests(unittest.TestCase):
    """`find()` ritorna `suggested_paths` quando path non esiste."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        (self.home / "Documents").mkdir()
        self._patches = [
            mock.patch("path_alias.Path.home", return_value=self.home),
            mock.patch("path_alias.candidate_roots",
                       return_value=[self.home]),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.tmp.cleanup()

    def test_path_not_found_includes_suggestions(self):
        from backends.files import local
        r = local.find({
            "base_path": str(self.home / "Inesistente"),
            "patterns": ["*"],
        })
        self.assertFalse(r["ok"])
        self.assertEqual(r["error_code"], "ERR_PATH_NOT_FOUND")
        self.assertIn("suggested_paths", r)
        self.assertTrue(any("Documents" in p for p in r["suggested_paths"]))


class MutatingAmbiguityTests(unittest.TestCase):
    """D.3: check_mutating_path_ambiguity ritorna error con candidates."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.other = Path(tempfile.mkdtemp())
        (self.home / "Pictures").mkdir()
        (self.other / "Immagini").mkdir()
        self._patches = [
            mock.patch("path_alias.Path.home", return_value=self.home),
            mock.patch("path_alias.candidate_roots",
                       return_value=[self.home, self.other]),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.tmp.cleanup()
        import shutil
        shutil.rmtree(self.other, ignore_errors=True)

    def test_mutating_path_exists_returns_none(self):
        from path_alias import check_mutating_path_ambiguity
        target = self.home / "Pictures"
        r = check_mutating_path_ambiguity(str(target), target_must_exist=True)
        self.assertIsNone(r)

    def test_mutating_path_ambiguous_returns_error(self):
        from path_alias import check_mutating_path_ambiguity
        r = check_mutating_path_ambiguity(
            str(self.home / "Immagini"), target_must_exist=True)
        self.assertIsNotNone(r)
        self.assertEqual(r["error_code"], "ERR_AMBIGUOUS_PATH")
        self.assertGreaterEqual(len(r["candidates"]), 2)

    def test_mutating_write_blocks_on_ambiguous(self):
        from backends.files import local
        r = local.write({
            "path": str(self.home / "Immagini" / "nuovo.txt"),
            "content": "ciao",
        })
        self.assertFalse(r["ok"])
        self.assertEqual(r["error_code"], "ERR_AMBIGUOUS_PATH")
        self.assertIn("candidates", r)

    def test_mutating_create_dirs_blocks_on_ambiguous(self):
        from backends.files import local
        r = local.create_dirs({"paths": [str(self.home / "Immagini" / "nuova_sub")]})
        self.assertFalse(r["ok"])
        # nota: create_dirs ritorna a livello per-item, check su failed[0]
        self.assertEqual(r["fail_count"], 1)


class WorkspaceDefaultTests(unittest.TestCase):
    """Path relativi → workspace_default (~/.local/share/metnos)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        # Crea finta workspace
        (self.home / ".local" / "share" / "metnos").mkdir(parents=True)
        self._patches = [
            mock.patch("path_alias.Path.home", return_value=self.home),
            mock.patch("path_alias.candidate_roots",
                       return_value=[
                           self.home / ".local" / "share" / "metnos",
                           self.home,
                       ]),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.tmp.cleanup()

    def test_relative_path_normalized_to_workspace(self):
        from path_alias import normalize_input_path
        out = normalize_input_path("MyFolder")
        self.assertEqual(
            out, self.home / ".local" / "share" / "metnos" / "MyFolder")

    def test_absolute_path_unchanged(self):
        from path_alias import normalize_input_path
        out = normalize_input_path("/usr/bin")
        self.assertEqual(out, Path("/usr/bin"))

    def test_tilde_path_expanded(self):
        from path_alias import normalize_input_path
        out = normalize_input_path("~/foo")
        self.assertEqual(out, self.home / "foo")

    def test_mutating_relative_paths_share_workspace_root(self):
        from backends.files import local
        made = local.create_dirs({"paths": ["Documenti/Run"]})
        self.assertTrue(made["ok"])
        expected_dir = (self.home / ".local" / "share" / "metnos"
                        / "Documenti" / "Run")
        self.assertEqual(Path(made["results"][0]["path"]), expected_dir)

        written = local.write({
            "path": "Documenti/Run/report.md", "content": "ok",
            "mode": "fail_if_exists",
        })
        self.assertTrue(written["ok"])
        self.assertEqual(
            Path(written["results"][0]["path"]), expected_dir / "report.md")
        self.assertEqual((expected_dir / "report.md").read_text(), "ok")


if __name__ == "__main__":
    unittest.main()
