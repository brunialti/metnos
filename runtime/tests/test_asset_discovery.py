"""Test per runtime/asset_discovery.py (Phase 5 ADR 0092).

Verifica:
  - discover_image_dirs filtra per min_files
  - skip dir nascoste/cache (.git, __pycache__, thumbcache, ...)
  - ordinamento per popolazione decrescente
  - scope_root inesistente → []
  - estensioni case-insensitive
  - file non-image ignorati
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))

from asset_discovery import (  # noqa: E402
    discover_image_dirs,
    discover_top_level_image_corpora,
    _discover_dirs_by_ext,
    IMAGE_EXTS,
)


def _touch(path: Path, n: int = 1) -> None:
    """Crea n file vuoti con il suffisso indicato dal path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix
    stem = path.stem
    for i in range(n):
        if n == 1:
            (path.parent / f"{stem}{suffix}").touch()
        else:
            (path.parent / f"{stem}_{i}{suffix}").touch()


class TestDiscoverImageDirs(unittest.TestCase):

    def test_min_files_filter(self):
        """3 dir: 10 image, 2 image, 0 image. Solo la prima passa min_files=5."""
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            _touch(tdp / "many" / "a.jpg", n=10)
            _touch(tdp / "few" / "b.jpg", n=2)
            (tdp / "empty").mkdir()
            (tdp / "empty" / "note.txt").touch()

            dirs = discover_image_dirs(tdp, min_files=5)
            paths_str = [str(d) for d in dirs]
            self.assertIn(str(tdp / "many"), paths_str)
            self.assertNotIn(str(tdp / "few"), paths_str)
            self.assertNotIn(str(tdp / "empty"), paths_str)

    def test_skip_cache_and_hidden_dirs(self):
        """Dir thumbcache/__pycache__/_history/_pending/.hidden saltate."""
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            # 6 image in skip set + 6 in dir valida
            _touch(tdp / "thumbcache" / "t.jpg", n=6)
            _touch(tdp / "__pycache__" / "p.png", n=6)
            _touch(tdp / "_history" / "h.jpg", n=6)
            _touch(tdp / ".git" / "g.png", n=6)
            _touch(tdp / "_pending" / "q.jpg", n=6)
            _touch(tdp / ".hidden" / "x.jpg", n=6)
            _touch(tdp / "valid" / "v.jpg", n=6)

            dirs = discover_image_dirs(tdp, min_files=5)
            paths_str = [str(d) for d in dirs]
            # Solo 'valid' deve passare
            self.assertEqual(len(dirs), 1, f"unexpected dirs={paths_str}")
            self.assertEqual(str(dirs[0]), str(tdp / "valid"))

    def test_ordered_by_population_desc(self):
        """Output ordinato per numero file desc."""
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            _touch(tdp / "small" / "a.jpg", n=5)
            _touch(tdp / "big" / "b.jpg", n=20)
            _touch(tdp / "medium" / "c.jpg", n=10)

            dirs = discover_image_dirs(tdp, min_files=5)
            self.assertEqual(len(dirs), 3)
            # Big (20) > medium (10) > small (5)
            self.assertEqual(dirs[0].name, "big")
            self.assertEqual(dirs[1].name, "medium")
            self.assertEqual(dirs[2].name, "small")

    def test_nonexistent_scope_returns_empty(self):
        """scope_root inesistente → []."""
        result = discover_image_dirs(Path("/tmp/this_does_not_exist_xyz_12345"))
        self.assertEqual(result, [])

    def test_case_insensitive_extensions(self):
        """`.JPG` e `.jpg` riconosciuti entrambi."""
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            # 3 .jpg + 3 .JPG + 1 .Png + 1 .HEIC = 8 image
            for i in range(3):
                (tdp / "mixed").mkdir(exist_ok=True)
                (tdp / "mixed" / f"a{i}.jpg").touch()
                (tdp / "mixed" / f"b{i}.JPG").touch()
            (tdp / "mixed" / "c.Png").touch()
            (tdp / "mixed" / "d.HEIC").touch()

            dirs = discover_image_dirs(tdp, min_files=5)
            self.assertEqual(len(dirs), 1)
            self.assertEqual(dirs[0].name, "mixed")

    def test_non_image_extensions_ignored(self):
        """File .txt/.pdf/.mp3/.zip non contati come image."""
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            (tdp / "docs").mkdir()
            for ext in (".txt", ".pdf", ".mp3", ".zip", ".csv", ".doc"):
                for i in range(5):
                    (tdp / "docs" / f"file{i}{ext}").touch()
            # Solo 1 image
            (tdp / "docs" / "single.jpg").touch()

            dirs = discover_image_dirs(tdp, min_files=5)
            # 1 image < 5, dir non passa
            self.assertEqual(dirs, [])

    def test_recursive_walk_finds_nested(self):
        """Walk ricorsivo: scopre dir popolate a profondita' qualsiasi."""
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            _touch(tdp / "year" / "2024" / "summer" / "a.jpg", n=8)
            _touch(tdp / "year" / "2025" / "vacation" / "b.png", n=10)

            dirs = discover_image_dirs(tdp, min_files=5)
            paths_str = {str(d) for d in dirs}
            self.assertIn(str(tdp / "year" / "2024" / "summer"), paths_str)
            self.assertIn(str(tdp / "year" / "2025" / "vacation"), paths_str)
            # Le dir intermedie (year, year/2024) non hanno foto direttamente
            self.assertNotIn(str(tdp / "year"), paths_str)
            self.assertNotIn(str(tdp / "year" / "2024"), paths_str)

    def test_default_min_files_5(self):
        """Default min_files=5: dir con 4 file non passa, con 5 sì."""
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            _touch(tdp / "four" / "a.jpg", n=4)
            _touch(tdp / "five" / "b.jpg", n=5)
            _touch(tdp / "six" / "c.jpg", n=6)

            dirs = discover_image_dirs(tdp)  # default min_files=5
            names = {d.name for d in dirs}
            self.assertNotIn("four", names)
            self.assertIn("five", names)
            self.assertIn("six", names)


class TestDiscoverGeneric(unittest.TestCase):
    """Test della funzione interna `_discover_dirs_by_ext`."""

    def test_custom_ext_set(self):
        """Permette set di estensioni custom (per nuovi domini)."""
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            _touch(tdp / "logs" / "a.log", n=10)
            _touch(tdp / "txts" / "b.txt", n=10)

            # Ext set: solo `.log`
            dirs = _discover_dirs_by_ext(tdp, frozenset({".log"}), min_files=5)
            self.assertEqual(len(dirs), 1)
            self.assertEqual(dirs[0].name, "logs")

    def test_image_exts_constant(self):
        """IMAGE_EXTS contiene almeno i formati canonici."""
        for sfx in (".jpg", ".jpeg", ".png", ".heic", ".webp", ".tiff", ".tif"):
            self.assertIn(sfx, IMAGE_EXTS)


class TestDiscoverTopLevelImageCorpora(unittest.TestCase):
    """Top-level corpora discovery: ogni figlio diretto di scope_root con
    >= min_files immagini ricorsive = un corpus. NON enumera sub-anni."""

    def test_top_level_only_no_subtree_inflation(self):
        """Bug 8/5/2026: scope con 1 corpus che ha 654 sub-anni → ritorna 1, non 654."""
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            # Immagini/2008/, Immagini/2010/foto1/, ... 100 sub-cartelle con foto
            for year in range(2000, 2030):
                _touch(tdp / "Immagini" / str(year) / "a.jpg", n=10)
            top = discover_top_level_image_corpora(tdp, min_files=5)
            self.assertEqual(len(top), 1, top)
            self.assertEqual(top[0].name, "Immagini")

    def test_multiple_top_level_corpora(self):
        """Due corpora top-level distinti → entrambi ritornati."""
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            _touch(tdp / "Immagini" / "2024" / "a.jpg", n=10)
            _touch(tdp / "Photo" / "lavoro" / "b.jpg", n=10)
            top = discover_top_level_image_corpora(tdp, min_files=5)
            names = sorted(p.name for p in top)
            self.assertEqual(names, ["Immagini", "Photo"])

    def test_skip_service_dirs(self):
        """index/, .cache/ etc. saltati anche se contengono jpg."""
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            _touch(tdp / "Immagini" / "a.jpg", n=10)
            _touch(tdp / ".cache" / "b.jpg", n=10)
            _touch(tdp / "__pycache__" / "c.jpg", n=10)
            top = discover_top_level_image_corpora(tdp, min_files=5)
            self.assertEqual(len(top), 1)
            self.assertEqual(top[0].name, "Immagini")

    def test_below_min_files_excluded(self):
        """Corpus con <min_files immagini → escluso."""
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            _touch(tdp / "Sparse" / "a.jpg", n=2)  # < 5
            _touch(tdp / "Full" / "b.jpg", n=8)
            top = discover_top_level_image_corpora(tdp, min_files=5)
            self.assertEqual(len(top), 1)
            self.assertEqual(top[0].name, "Full")

    def test_sort_by_population_desc(self):
        """Sort: piu' popolato first, tie-break alfabetico."""
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            _touch(tdp / "Small" / "a.jpg", n=6)
            _touch(tdp / "Big" / "b.jpg", n=20)
            _touch(tdp / "Medium" / "c.jpg", n=10)
            top = discover_top_level_image_corpora(tdp, min_files=5)
            self.assertEqual([p.name for p in top], ["Big", "Medium", "Small"])

    def test_empty_scope(self):
        """Scope inesistente → []."""
        result = discover_top_level_image_corpora(Path("/tmp/this_does_not_exist_xyz_12345"))
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
