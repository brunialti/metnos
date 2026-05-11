"""Test di get_images_indices (ADR 0086, 4/5/2026).

Introspection-only: niente modelli ML necessari. Si costruisce un indice
fittizio e si verifica che il status corrisponda.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
_EXEC = _RUNTIME.parent / "executors" / "get_images_indices"
sys.path.insert(0, str(_RUNTIME))
sys.path.insert(0, str(_EXEC))


def _seed_one(base_dir: Path, idx: str, n_entries: int) -> Path:
    digest = hashlib.sha256(str(base_dir.resolve()).encode("utf-8")).hexdigest()
    d = Path.home() / ".local" / "share" / "metnos" / "index" / "image" / digest[:16] / idx
    d.mkdir(parents=True, exist_ok=True)
    (d / "entries.jsonl").write_text(
        "\n".join(json.dumps({"path": f"/x/{i}.jpg"}) for i in range(n_entries)) + "\n",
        encoding="utf-8",
    )
    (d / "meta.json").write_text(json.dumps({
        "version": 1, "idx": idx, "n_entries": n_entries,
        "model": "dummy_model", "dim": 768, "last_refresh_at": time.time(),
    }), encoding="utf-8")
    return d


class TestGetIndicesImage(unittest.TestCase):

    def test_all_kinds_present_and_missing(self):
        """Indice scene presente, persons+gps assenti."""
        import get_images_indices as gii
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            scene_dir = _seed_one(tdp, "scene", n_entries=5)
            try:
                out = gii.invoke({"base_path": str(tdp), "idx": "all"})
                self.assertTrue(out["ok"], out)
                by_idx = {e["idx"]: e for e in out["entries"]}
                self.assertTrue(by_idx["scene"]["exists"])
                self.assertEqual(by_idx["scene"]["n_entries"], 5)
                self.assertEqual(by_idx["scene"]["dim"], 768)
                self.assertFalse(by_idx["persons"]["exists"])
                self.assertFalse(by_idx["gps"]["exists"])
            finally:
                shutil.rmtree(scene_dir.parent, ignore_errors=True)

    def test_single_idx(self):
        import get_images_indices as gii
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            d = _seed_one(tdp, "gps", n_entries=10)
            try:
                out = gii.invoke({"base_path": str(tdp), "idx": "gps"})
                self.assertTrue(out["ok"], out)
                self.assertEqual(len(out["entries"]), 1)
                e = out["entries"][0]
                self.assertEqual(e["idx"], "gps")
                self.assertTrue(e["exists"])
                self.assertEqual(e["n_entries"], 10)
            finally:
                shutil.rmtree(d.parent, ignore_errors=True)

    def test_idx_invalid(self):
        import get_images_indices as gii
        out = gii.invoke({"base_path": "/tmp", "idx": "pippo"})
        self.assertFalse(out["ok"])

    def test_no_index_returns_all_missing(self):
        import get_images_indices as gii
        out = gii.invoke({
            "base_path": "/tmp/metnos_does_not_exist_xyz_get_idx",
            "idx": "all",
        })
        self.assertTrue(out["ok"], out)
        for e in out["entries"]:
            self.assertFalse(e["exists"])


if __name__ == "__main__":
    unittest.main()
