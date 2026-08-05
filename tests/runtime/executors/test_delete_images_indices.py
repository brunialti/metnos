"""Test di delete_images_indices (ADR 0086, 4/5/2026).

Niente dipendenza da modelli ML: si costruisce un indice "fittizio" sul
filesystem e si verifica che delete lo cancelli correttamente.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")
_EXEC = _RUNTIME.parent / "executors" / "delete_images_indices"
sys.path.insert(0, str(_EXEC))


def _seed_index(base_dir: Path, idx_kinds: list[str]) -> Path:
    """Crea indici fittizi con 1 entry ciascuno per testare la cancellazione."""
    digest = hashlib.sha256(str(base_dir.resolve()).encode("utf-8")).hexdigest()
    root = Path.home() / ".local" / "share" / "metnos" / "index" / "image" / digest[:16]
    for k in idx_kinds:
        d = root / k
        d.mkdir(parents=True, exist_ok=True)
        (d / "entries.jsonl").write_text(
            json.dumps({"path": str(base_dir / "x.jpg"), "name": "x.jpg"}) + "\n",
            encoding="utf-8",
        )
        (d / "meta.json").write_text(
            json.dumps({"version": 1, "idx": k, "n_entries": 1}),
            encoding="utf-8",
        )
    return root


class TestDeleteIndicesImage(unittest.TestCase):

    def test_delete_all_kinds(self):
        import delete_images_indices as dii
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            root = _seed_index(tdp, ["unified"])
            try:
                out = dii.invoke({"base_path": str(tdp), "idx": "all"})
                self.assertTrue(out["ok"], out)
                self.assertEqual(len(out["deleted"]), 1)
                self.assertGreater(out["freed_bytes"], 0)
                self.assertFalse((root / "unified").exists())
            finally:
                shutil.rmtree(root, ignore_errors=True)

    def test_delete_single_kind(self):
        import delete_images_indices as dii
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            root = _seed_index(tdp, ["unified", "scene"])
            try:
                out = dii.invoke({"base_path": str(tdp), "idx": "unified"})
                self.assertTrue(out["ok"], out)
                self.assertEqual(len(out["deleted"]), 1)
                self.assertFalse((root / "unified").exists())
                # La directory legacy non viene presentata come indice attivo.
                self.assertTrue((root / "scene").exists())
            finally:
                shutil.rmtree(root, ignore_errors=True)

    def test_dry_run_does_not_delete(self):
        import delete_images_indices as dii
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            root = _seed_index(tdp, ["unified"])
            try:
                out = dii.invoke({
                    "base_path": str(tdp), "idx": "unified", "dry_run": True,
                })
                self.assertTrue(out["ok"], out)
                self.assertTrue(out["dry_run"])
                # File ancora presenti
                self.assertTrue((root / "unified").exists())
                self.assertTrue((root / "unified" / "entries.jsonl").exists())
            finally:
                shutil.rmtree(root, ignore_errors=True)

    def test_idx_invalid_fails(self):
        import delete_images_indices as dii
        out = dii.invoke({"base_path": "/tmp", "idx": "pippo"})
        self.assertFalse(out["ok"])
        self.assertIn("idx", out["error"])

    def test_legacy_idx_is_not_accepted_as_active(self):
        import delete_images_indices as dii
        out = dii.invoke({"base_path": "/tmp", "idx": "scene"})
        self.assertFalse(out["ok"])

    def test_delete_nonexistent_index_ok_empty(self):
        """Cancellare un indice che non c'e' = no-op success."""
        import delete_images_indices as dii
        out = dii.invoke({
            "base_path": "/tmp/metnos_does_not_exist_xyz_del",
            "idx": "all",
        })
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["deleted"], [])
        self.assertEqual(out["freed_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
