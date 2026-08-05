"""Test di get_images_indices (ADR 0086, 4/5/2026).

Introspection-only: niente modelli ML necessari. Si costruisce un indice
fittizio e si verifica che il status corrisponda.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")
_EXEC = _RUNTIME.parent / "executors" / "get_images_indices"
sys.path.insert(0, str(_EXEC))


def _seed_one(base_dir: Path, idx: str, n_entries: int) -> Path:
    digest = hashlib.sha256(str(base_dir.resolve()).encode("utf-8")).hexdigest()
    configured = os.environ.get("METNOS_INDEX_ROOT")
    image_root = (
        Path(configured) / "image" if configured
        else Path.home() / ".local" / "share" / "metnos" / "index" / "image"
    )
    d = image_root / digest[:16] / idx
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

    def test_all_reports_active_unified_index(self):
        """`all` means the single active v4 unified index."""
        import get_images_indices as gii
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            unified_dir = _seed_one(tdp, "unified", n_entries=5)
            try:
                out = gii.invoke({"base_path": str(tdp), "idx": "all"})
                self.assertTrue(out["ok"], out)
                by_idx = {e["idx"]: e for e in out["entries"]}
                self.assertEqual(set(by_idx), {"unified"})
                self.assertTrue(by_idx["unified"]["exists"])
                self.assertEqual(by_idx["unified"]["n_entries"], 5)
                self.assertEqual(by_idx["unified"]["dim"], 768)
                self.assertEqual(out["indexed_entries_total"], 5)
            finally:
                shutil.rmtree(unified_dir.parent, ignore_errors=True)

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
        self.assertEqual(len(out["entries"]), 1)
        for e in out["entries"]:
            self.assertFalse(e["exists"])

    def test_no_base_path_discovers_unified_indexes(self):
        import get_images_indices as gii
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            old = os.environ.get("METNOS_INDEX_ROOT")
            os.environ["METNOS_INDEX_ROOT"] = str(tdp / "index")
            corpus = tdp / "photos"
            corpus.mkdir()
            seeded = _seed_one(corpus, "unified", n_entries=7)
            try:
                out = gii.invoke({})
                self.assertTrue(out["ok"], out)
                self.assertEqual(out["indexed_entries_total"], 7)
                self.assertEqual(out["entries"][0]["idx"], "unified")
            finally:
                shutil.rmtree(seeded.parent, ignore_errors=True)
                if old is None:
                    os.environ.pop("METNOS_INDEX_ROOT", None)
                else:
                    os.environ["METNOS_INDEX_ROOT"] = old

    def test_explicit_offline_target_matches_logical_symlink_index_metadata(self):
        import get_images_indices as gii
        from index_schema import canonical_corpus_path

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = root / "data"
            target = root / "nas" / "photos"
            data.mkdir()
            target.mkdir(parents=True)
            logical = data / "Immagini"
            logical.symlink_to(target, target_is_directory=True)
            env = {
                "METNOS_INDEX_ROOT": str(root / "index"),
                "METNOS_USER_DATA": str(data),
            }
            with mock.patch.dict(os.environ, env, clear=False):
                digest = hashlib.sha256(
                    canonical_corpus_path(logical).encode("utf-8")).hexdigest()
                idx_dir = root / "index" / "image" / digest[:16] / "unified"
                idx_dir.mkdir(parents=True)
                (idx_dir / "meta.json").write_text(json.dumps({
                    "schema_version": 4, "n_entries": 9,
                    "base_path": str(target),
                }), encoding="utf-8")
                logical.unlink()

                out = gii.invoke({
                    "base_path": str(target), "idx": "all",
                })

            self.assertTrue(out["ok"], out)
            self.assertTrue(out["entries"][0]["exists"])
            self.assertEqual(out["indexed_entries_total"], 9)
            self.assertEqual(out["index_root"], str(idx_dir.parent))


if __name__ == "__main__":
    unittest.main()
