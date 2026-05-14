"""Test migration v3 → v4 (ADR 0117)."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


def _build_legacy_corpus(corpus_dir: Path,
                          *, with_scene: bool = True,
                          with_persons: bool = True,
                          with_gps: bool = True,
                          paths: list[str] | None = None) -> None:
    """Costruisce un corpus legacy v3 a 3 idx separati per i test."""
    paths = paths or ["/photos/img_a.jpg", "/photos/img_b.jpg"]

    if with_scene:
        scene_dir = corpus_dir / "scene"
        scene_dir.mkdir(parents=True, exist_ok=True)
        entries = [{
            "path": p, "name": Path(p).name, "mtime": 1000.0 + i,
            "size": 5000 + i * 100,
        } for i, p in enumerate(paths)]
        with (scene_dir / "entries.jsonl").open("w") as fh:
            for e in entries:
                fh.write(json.dumps(e) + "\n")
        vecs = np.random.rand(len(paths), 768).astype("float32")
        np.save(str(scene_dir / "vectors.npy"), vecs)
        (scene_dir / "meta.json").write_text(json.dumps({
            "schema_version": 2, "version": 2, "idx": "scene",
            "model": "siglip", "dim": 768, "n_entries": len(paths),
        }))

    if with_persons:
        persons_dir = corpus_dir / "persons"
        persons_dir.mkdir(parents=True, exist_ok=True)
        # 1 entry per faccia, qui 2 facce per la prima foto
        entries = [
            {"path": paths[0], "name": Path(paths[0]).name, "mtime": 1000.0,
             "size": 5000, "face_idx": 0, "bbox": [10, 10, 50, 50],
             "score": 0.95, "landmarks": [[20, 20], [40, 20], [30, 35], [22, 45], [38, 45]]},
            {"path": paths[0], "name": Path(paths[0]).name, "mtime": 1000.0,
             "size": 5000, "face_idx": 1, "bbox": [60, 10, 50, 50],
             "score": 0.92, "landmarks": [[70, 20], [90, 20], [80, 35], [72, 45], [88, 45]]},
        ]
        with (persons_dir / "entries.jsonl").open("w") as fh:
            for e in entries:
                fh.write(json.dumps(e) + "\n")
        vecs = np.random.rand(len(entries), 512).astype("float32")
        np.save(str(persons_dir / "vectors.npy"), vecs)
        (persons_dir / "meta.json").write_text(json.dumps({
            "schema_version": 2, "version": 2, "idx": "persons",
            "model": "arcface", "dim": 512, "n_entries": len(entries),
        }))

    if with_gps:
        gps_dir = corpus_dir / "gps"
        gps_dir.mkdir(parents=True, exist_ok=True)
        entries = [{
            "path": paths[0], "name": Path(paths[0]).name, "mtime": 1000.0,
            "size": 5000, "lat": 44.4, "lon": 8.9, "alt": 50.0,
        }]
        with (gps_dir / "entries.jsonl").open("w") as fh:
            for e in entries:
                fh.write(json.dumps(e) + "\n")
        (gps_dir / "meta.json").write_text(json.dumps({
            "schema_version": 2, "version": 2, "idx": "gps",
            "model": "exif_gps", "dim": 0, "n_entries": len(entries),
        }))


_VLM_MOCK = {
    "description": "Foto migrata da legacy",
    "keywords": ["test", "migrato"],
    "location_hint": "outdoor",
    "activity_hint": "",
}


class _StubTextEmbedder:
    def embed_texts(self, texts):
        return np.random.rand(len(texts), 1024).astype("float32")


class TestNeedsMigration(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="metnos_migrate_test_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_legacy_no_migration(self):
        import index_schema_upgrade_v4 as up
        d = self.tmp / "corpus"
        d.mkdir()
        self.assertFalse(up.needs_v4_migration(d))

    def test_with_legacy_needs_migration(self):
        import index_schema_upgrade_v4 as up
        d = self.tmp / "corpus"
        d.mkdir()
        _build_legacy_corpus(d)
        self.assertTrue(up.needs_v4_migration(d))

    def test_unified_already_present_no_migration(self):
        import index_schema_upgrade_v4 as up
        d = self.tmp / "corpus"
        d.mkdir()
        _build_legacy_corpus(d)
        # Crea unified
        (d / "unified").mkdir()
        (d / "unified" / "meta.json").write_text(json.dumps({
            "schema_version": 4, "n_entries": 1,
        }))
        self.assertFalse(up.needs_v4_migration(d))


class TestMigrateOne(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="metnos_migrate_test_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_migrate_dry_run(self):
        import index_schema_upgrade_v4 as up
        d = self.tmp / "corpus"
        d.mkdir()
        _build_legacy_corpus(d)
        out = up.migrate_one(d, dry_run=True)
        self.assertTrue(out["ok"])
        self.assertEqual(out["dry_run"], True)
        self.assertGreater(out["n_paths_aggregated"], 0)

    def test_migrate_one_basic(self):
        import index_schema_upgrade_v4 as up
        d = self.tmp / "corpus"
        d.mkdir()
        _build_legacy_corpus(d, paths=["/photos/img_a.jpg", "/photos/img_b.jpg"])
        out = up.migrate_one(
            d,
            vlm_caller=lambda p: dict(_VLM_MOCK),
            text_embedder=_StubTextEmbedder(),
        )
        self.assertTrue(out["ok"], msg=out)
        # Verifica unified
        unified = d / "unified"
        self.assertTrue((unified / "entries.jsonl").exists())
        self.assertTrue((unified / "meta.json").exists())
        meta = json.loads((unified / "meta.json").read_text())
        self.assertEqual(meta["schema_version"], 4)
        # Legacy NON cancellati
        self.assertTrue((d / "scene").exists())
        self.assertTrue((d / "persons").exists())
        self.assertTrue((d / "gps").exists())

    def test_migrate_idempotent(self):
        import index_schema_upgrade_v4 as up
        d = self.tmp / "corpus"
        d.mkdir()
        _build_legacy_corpus(d)
        up.migrate_one(d, vlm_caller=lambda p: dict(_VLM_MOCK), text_embedder=_StubTextEmbedder())
        # Re-run: skip
        out = up.migrate_one(d, vlm_caller=lambda p: dict(_VLM_MOCK), text_embedder=_StubTextEmbedder())
        self.assertTrue(out["ok"])
        self.assertTrue(out.get("skipped"))

    def test_migrate_aggregates_persons_to_faces(self):
        import index_schema_upgrade_v4 as up
        d = self.tmp / "corpus"
        d.mkdir()
        _build_legacy_corpus(d, paths=["/photos/img_a.jpg"])
        out = up.migrate_one(
            d, vlm_caller=lambda p: dict(_VLM_MOCK),
            text_embedder=_StubTextEmbedder(),
        )
        self.assertTrue(out["ok"])
        with (d / "unified" / "entries.jsonl").open() as fh:
            entry = json.loads(fh.readline())
        # 2 facce nella legacy persons → 2 facce in faces[]
        self.assertEqual(len(entry["faces"]), 2)
        self.assertIn("embedding_face_idx", entry["faces"][0])

    def test_migrate_preserves_gps(self):
        import index_schema_upgrade_v4 as up
        d = self.tmp / "corpus"
        d.mkdir()
        _build_legacy_corpus(d, paths=["/photos/img_a.jpg"])
        out = up.migrate_one(
            d, vlm_caller=lambda p: dict(_VLM_MOCK),
            text_embedder=_StubTextEmbedder(),
        )
        self.assertTrue(out["ok"])
        with (d / "unified" / "entries.jsonl").open() as fh:
            entry = json.loads(fh.readline())
        self.assertIsNotNone(entry["exif_gps"])
        self.assertAlmostEqual(entry["exif_gps"]["lat"], 44.4)

    def test_migrate_no_vlm_caller_degrades(self):
        import index_schema_upgrade_v4 as up
        d = self.tmp / "corpus"
        d.mkdir()
        _build_legacy_corpus(d, paths=["/photos/img_a.jpg"])
        out = up.migrate_one(
            d, vlm_caller=None, text_embedder=None,
        )
        self.assertTrue(out["ok"])
        with (d / "unified" / "entries.jsonl").open() as fh:
            entry = json.loads(fh.readline())
        # description vuota (degraded)
        self.assertEqual(entry["description"], "")

    def test_migrate_no_legacy_returns_error(self):
        import index_schema_upgrade_v4 as up
        d = self.tmp / "corpus"
        d.mkdir()
        out = up.migrate_one(
            d, vlm_caller=lambda p: dict(_VLM_MOCK),
            text_embedder=_StubTextEmbedder(),
        )
        self.assertFalse(out["ok"])
        self.assertIn("no legacy indices found", out["error"])


class TestMigrateBoot(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="metnos_boot_test_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_boot_no_dirs(self):
        import index_schema_upgrade_v4 as up
        # base inesistente
        out = up.migrate_existing_indices_at_boot(base=self.tmp / "missing")
        self.assertEqual(out["found"], 0)
        self.assertEqual(out["needing_migration"], 0)


if __name__ == "__main__":
    unittest.main()
