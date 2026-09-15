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

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")
_EXEC = _RUNTIME.parent / "executors" / "create_images_indices"
sys.path.insert(0, str(_EXEC))
from messages import get as _msg  # noqa: E402  # §11 i18n: assert language-independent


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
        self.assertEqual(out["error"], _msg(
            "ERR_PATH_NOT_FOUND", path="/tmp/metnos_does_not_exist_xyz_unified_test"))

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
    """Exercise real phase/storage code with synthetic local model responses."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="metnos_unified_test_"))
        # Isola lo storage indice
        self._old_idx_root = None
        import os
        self._old_idx_root = os.environ.get("METNOS_INDEX_ROOT")
        os.environ["METNOS_INDEX_ROOT"] = str(self.tmp / "index")
        self._generation = 0
        import numpy as np
        from types import SimpleNamespace
        self._models = mock.patch("virt.get_embedder", side_effect=lambda role: {
            "text": SimpleNamespace(name="text-fixture", embed_texts=lambda _texts: np.array([[1, 0]], dtype="float32")),
            "image": SimpleNamespace(name="image-fixture", available=True,
                                     embed_images=lambda *_args, **_kwargs: np.array([[1, 0]], dtype="float32")),
        }[role])
        self._identity = mock.patch("create_images_indices.analysis_identity", return_value="fixture-policy")
        self._models.start()
        self._identity.start()

    def _build(self, args):
        import create_images_indices as cii
        self._generation += 1
        common = {**args, "generation": f"test-{self._generation}"}
        discovery = cii.invoke({**common, "phase": "discover"})
        if not discovery["ok"]:
            return discovery
        receipts = []
        for group in discovery["entries"]:
            result = cii.invoke({**common, "phase": "analyze", "entries": [{
                "part": group["part"],
                "folder_contexts": {label: f"Photos: {label}." for label in group["folder_labels"]},
            }]})
            if not result["ok"]:
                return result
            receipts.extend(result["entries"])
        return cii.invoke({**common, "phase": "publish", "entries": receipts,
                           "expected_count": discovery["source_count"]})

    def tearDown(self):
        import os
        self._models.stop()
        self._identity.stop()
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
        out = self._build({"base_path": str(photos)})
        self.assertFalse(out["ok"])
        self.assertEqual(out["error_code"], "image_corpus_empty")

    def test_build_writes_unified_storage(self):
        import create_images_indices as cii
        photos = self.tmp / "photos"
        photos.mkdir()
        for i in range(2):
            _make_synthetic_image(photos / f"img_{i}.jpg")
        with mock.patch.object(cii, "_call_vlm", return_value=_VLM_MOCK_OUT), \
             mock.patch("face_embedding.get_face_engine",
                        return_value=_StubFaceEngine()):
            out = self._build({"base_path": str(photos)})
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
            out = self._build({"base_path": str(photos)})
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
            out1 = self._build({"base_path": str(photos)})
            self.assertEqual(out1["ok_count"], 1)
            # Re-build: nessuna modifica → resume skip VLM call
            out2 = self._build({"base_path": str(photos)})
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
            self._build({"base_path": str(photos)})
            out2 = self._build({"base_path": str(photos), "force": True})
        self.assertTrue(out2["ok"])
        self.assertEqual(out2["ok_count"], 1)

    def test_max_files_overflow_does_not_publish_a_partial_index(self):
        import create_images_indices as cii
        photos = self.tmp / "photos"
        photos.mkdir()
        for i in range(5):
            _make_synthetic_image(photos / f"img_{i}.jpg")
        with mock.patch.object(cii, "_call_vlm", return_value=_VLM_MOCK_OUT), \
             mock.patch("face_embedding.get_face_engine",
                        return_value=_StubFaceEngine()):
            out = self._build({"base_path": str(photos), "max_files": 2})
        self.assertFalse(out["ok"])
        self.assertFalse((cii._index_dir(photos) / "meta.json").exists())

    def test_atomic_write(self):
        import create_images_indices as cii
        photos = self.tmp / "photos"
        photos.mkdir()
        _make_synthetic_image(photos / "y.jpg")
        with mock.patch.object(cii, "_call_vlm", return_value=_VLM_MOCK_OUT), \
             mock.patch("face_embedding.get_face_engine",
                        return_value=_StubFaceEngine()):
            out = self._build({"base_path": str(photos)})
        idx_path = Path(out["index_path"])
        # Niente .tmp residui dopo build
        for tmp_pat in idx_path.glob("*.tmp"):
            self.fail(f"tmp leftover: {tmp_pat}")


class TestParseVlmText(unittest.TestCase):
    # _parse_vlm_text spostato in runtime/vlm_client.py (SoT VLM, §7.2 dedup
    # 1/7/2026): create_images_indices._call_vlm vi delega. Test seguono la fn.

    def test_parse_clean_json(self):
        import vlm_client
        text = '{"description": "x", "keywords": ["a"], "location_hint": "y", "activity_hint": "z"}'
        out = vlm_client._parse_vlm_text(text)
        self.assertEqual(out["description"], "x")
        self.assertEqual(out["keywords"], ["a"])
        self.assertEqual(out["location_hint"], "y")
        self.assertEqual(out["activity_hint"], "z")
        self.assertNotIn("_vlm_error", out)

    def test_parse_code_fenced_json(self):
        import vlm_client
        text = '```json\n{"description": "x", "keywords": []}\n```'
        out = vlm_client._parse_vlm_text(text)
        self.assertEqual(out["description"], "x")

    def test_parse_invalid_returns_error(self):
        import vlm_client
        out = vlm_client._parse_vlm_text("not json")
        self.assertIn("_vlm_error", out)
        self.assertEqual(out["description"], "")


if __name__ == "__main__":
    unittest.main()
