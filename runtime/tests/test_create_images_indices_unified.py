"""Test del builder unified (ADR 0117).

Mock VLM via patch su `_call_vlm`. Mock face engine via classe stub.
Skip-friendly: se PIL/Pillow assente, alcuni test saltano.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_RUNTIME = Path(__file__).resolve().parent.parent
_EXEC = _RUNTIME.parent / "executors" / "create_images_indices"
sys.path.insert(0, str(_RUNTIME))
sys.path.insert(0, str(_EXEC))


def _pillow_present() -> bool:
    try:
        from PIL import Image  # noqa: F401
        return True
    except ImportError:
        return False


def _make_synthetic_image(path: Path, color="blue", size=(100, 100)) -> None:
    from PIL import Image
    Image.new("RGB", size, color=color).save(path)


class _StubFaceEngine:
    name = "stub"
    available = True
    dimension = 512
    _model_dir = Path("/tmp/stub")

    def detect_faces(self, p):
        # Ritorna 1 faccia sintetica con embedding random L2-normalized
        import numpy as np
        emb = np.array([0.1] * 512, dtype="float32")
        emb /= np.linalg.norm(emb)
        return [{
            "bbox": (10, 10, 80, 80),
            "score": 0.99,
            "landmarks": [(20, 20), (60, 20), (40, 50), (25, 80), (55, 80)],
            "embedding": emb,
        }]


class _StubFaceEngineEmpty:
    """Engine disponibile ma nessuna faccia rilevata."""
    name = "stub-empty"
    available = True
    dimension = 512
    _model_dir = Path("/tmp/stub")

    def detect_faces(self, p):
        return []


_VLM_MOCK_OUT = {
    "description": "Foto di test sintetica blu",
    "keywords": ["test", "blu", "sintetica"],
    "location_hint": "studio",
    "activity_hint": "",
}


class TestCreateUnifiedValidation(unittest.TestCase):

    def test_missing_base_path(self):
        import create_images_indices as cii
        out = cii.invoke({})
        self.assertFalse(out["ok"])
        self.assertIn("base_path", out["error"])

    def test_base_path_not_found(self):
        import create_images_indices as cii
        out = cii.invoke({"base_path": "/tmp/metnos_does_not_exist_xyz_unified_test"})
        self.assertFalse(out["ok"])
        self.assertIn("not found", out["error"])

    def test_idx_arg_ignored_no_crash(self):
        import create_images_indices as cii
        # Missing base_path, ma idx ignored prima dell'errore
        out = cii.invoke({"idx": "scene", "base_path": "/tmp/metnos_does_not_exist_xyz_unified_test"})
        # Errore base_path, ma idx non crasha
        self.assertFalse(out["ok"])

    def test_max_files_invalid(self):
        import create_images_indices as cii
        with tempfile.TemporaryDirectory() as tmp:
            out = cii.invoke({"base_path": tmp, "max_files": 0})
            self.assertFalse(out["ok"])
            self.assertIn("max_files", out["error"])


@unittest.skipUnless(_pillow_present(), "Pillow non disponibile")
class TestCreateUnifiedDryRun(unittest.TestCase):

    def test_dry_run_empty_dir(self):
        import create_images_indices as cii
        with tempfile.TemporaryDirectory() as tmp:
            out = cii.invoke({"base_path": tmp, "dry_run": True})
            self.assertTrue(out["ok"])
            self.assertTrue(out["dry_run"])
            self.assertEqual(out["would_index_count"], 0)
            self.assertEqual(out["schema_version"], 4)

    def test_dry_run_with_synthetic_images(self):
        import create_images_indices as cii
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(3):
                _make_synthetic_image(Path(tmp) / f"img_{i}.jpg")
            out = cii.invoke({"base_path": tmp, "dry_run": True})
            self.assertTrue(out["ok"])
            self.assertEqual(out["would_index_count"], 3)


@unittest.skipUnless(_pillow_present(), "Pillow non disponibile")
class TestCreateUnifiedBuilder(unittest.TestCase):
    """Test del builder reale, mockando VLM + face engine."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="metnos_unified_test_"))
        # Isola lo storage indice
        self._old_idx_root = None
        import os
        self._old_idx_root = os.environ.get("METNOS_INDEX_ROOT")
        os.environ["METNOS_INDEX_ROOT"] = str(self.tmp / "index")

    def tearDown(self):
        import os
        if self._old_idx_root is None:
            os.environ.pop("METNOS_INDEX_ROOT", None)
        else:
            os.environ["METNOS_INDEX_ROOT"] = self._old_idx_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_image_files_returns_error(self):
        import create_images_indices as cii
        photos = self.tmp / "photos"
        photos.mkdir()
        # Solo file non-image
        (photos / "doc.txt").write_text("hello")
        out = cii.invoke({"base_path": str(photos)})
        self.assertFalse(out["ok"])
        self.assertIn("immagini supportate", out["error"])

    def test_build_writes_unified_storage(self):
        import create_images_indices as cii
        photos = self.tmp / "photos"
        photos.mkdir()
        for i in range(2):
            _make_synthetic_image(photos / f"img_{i}.jpg")
        with mock.patch.object(cii, "_call_vlm", return_value=_VLM_MOCK_OUT), \
             mock.patch("face_embedding.get_face_engine",
                        return_value=_StubFaceEngine()):
            out = cii.invoke({"base_path": str(photos)})
        self.assertTrue(out["ok"], msg=out)
        self.assertEqual(out["schema_version"], 4)
        self.assertEqual(out["ok_count"], 2)
        # Verifica storage layout
        idx_path = Path(out["index_path"])
        self.assertTrue((idx_path / "entries.jsonl").exists())
        self.assertTrue((idx_path / "meta.json").exists())
        meta = json.loads((idx_path / "meta.json").read_text())
        self.assertEqual(meta["schema_version"], 4)
        self.assertEqual(meta["n_entries"], 2)
        self.assertGreaterEqual(meta["n_faces"], 0)

    def test_build_persists_vlm_fields(self):
        import create_images_indices as cii
        photos = self.tmp / "photos"
        photos.mkdir()
        _make_synthetic_image(photos / "test.jpg")
        with mock.patch.object(cii, "_call_vlm", return_value=_VLM_MOCK_OUT), \
             mock.patch("face_embedding.get_face_engine",
                        return_value=_StubFaceEngine()):
            out = cii.invoke({"base_path": str(photos)})
        self.assertTrue(out["ok"])
        idx_path = Path(out["index_path"])
        with (idx_path / "entries.jsonl").open() as fh:
            entry = json.loads(fh.readline())
        self.assertEqual(entry["description"], _VLM_MOCK_OUT["description"])
        self.assertEqual(entry["keywords"], _VLM_MOCK_OUT["keywords"])
        self.assertEqual(entry["location_hint"], _VLM_MOCK_OUT["location_hint"])

    def test_resume_skips_unchanged(self):
        import create_images_indices as cii
        photos = self.tmp / "photos"
        photos.mkdir()
        _make_synthetic_image(photos / "a.jpg")
        with mock.patch.object(cii, "_call_vlm", return_value=_VLM_MOCK_OUT), \
             mock.patch("face_embedding.get_face_engine",
                        return_value=_StubFaceEngine()):
            out1 = cii.invoke({"base_path": str(photos)})
            self.assertEqual(out1["ok_count"], 1)
            # Re-build: nessuna modifica → resume skip VLM call
            out2 = cii.invoke({"base_path": str(photos)})
        self.assertTrue(out2["ok"])
        self.assertEqual(out2["ok_count"], 1)
        self.assertEqual(out2["refreshed_count"], 0)

    def test_force_rebuild(self):
        import create_images_indices as cii
        photos = self.tmp / "photos"
        photos.mkdir()
        _make_synthetic_image(photos / "x.jpg")
        with mock.patch.object(cii, "_call_vlm", return_value=_VLM_MOCK_OUT), \
             mock.patch("face_embedding.get_face_engine",
                        return_value=_StubFaceEngine()):
            cii.invoke({"base_path": str(photos)})
            out2 = cii.invoke({"base_path": str(photos), "force": True})
        self.assertTrue(out2["ok"])
        self.assertEqual(out2["ok_count"], 1)

    def test_max_files_truncated(self):
        import create_images_indices as cii
        photos = self.tmp / "photos"
        photos.mkdir()
        for i in range(5):
            _make_synthetic_image(photos / f"img_{i}.jpg")
        with mock.patch.object(cii, "_call_vlm", return_value=_VLM_MOCK_OUT), \
             mock.patch("face_embedding.get_face_engine",
                        return_value=_StubFaceEngine()):
            out = cii.invoke({"base_path": str(photos), "max_files": 2})
        self.assertTrue(out["ok"])
        self.assertTrue(out.get("truncated"))
        self.assertEqual(out["cap_field"], "max_files")
        self.assertEqual(out["cap_value"], 2)

    def test_atomic_write(self):
        import create_images_indices as cii
        photos = self.tmp / "photos"
        photos.mkdir()
        _make_synthetic_image(photos / "y.jpg")
        with mock.patch.object(cii, "_call_vlm", return_value=_VLM_MOCK_OUT), \
             mock.patch("face_embedding.get_face_engine",
                        return_value=_StubFaceEngine()):
            out = cii.invoke({"base_path": str(photos)})
        idx_path = Path(out["index_path"])
        # Niente .tmp residui dopo build
        for tmp_pat in idx_path.glob("*.tmp"):
            self.fail(f"tmp leftover: {tmp_pat}")


class TestParseVlmText(unittest.TestCase):

    def test_parse_clean_json(self):
        import create_images_indices as cii
        text = '{"description": "x", "keywords": ["a"], "location_hint": "y", "activity_hint": "z"}'
        out = cii._parse_vlm_text(text)
        self.assertEqual(out["description"], "x")
        self.assertEqual(out["keywords"], ["a"])
        self.assertEqual(out["location_hint"], "y")
        self.assertEqual(out["activity_hint"], "z")
        self.assertNotIn("_vlm_error", out)

    def test_parse_code_fenced_json(self):
        import create_images_indices as cii
        text = '```json\n{"description": "x", "keywords": []}\n```'
        out = cii._parse_vlm_text(text)
        self.assertEqual(out["description"], "x")

    def test_parse_invalid_returns_error(self):
        import create_images_indices as cii
        out = cii._parse_vlm_text("not json")
        self.assertIn("_vlm_error", out)
        self.assertEqual(out["description"], "")


if __name__ == "__main__":
    unittest.main()
