"""Test find_images_indices unified (ADR 0117)."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np


def _has_tokenizers() -> bool:
    try:
        import tokenizers  # noqa: F401
        return True
    except ImportError:
        return False


_TOKENIZERS_REASON = (
    "`tokenizers` mancante: pip install tokenizers (dep BGE-M3, ADR 0117)."
)

# Stub `bge_embedding` quando `tokenizers` non disponibile: il modulo
# originale lo importa al top-level e crasha l'import. Lo stub permette
# ai test di esercitare il dispatcher di `find_images_indices` con
# `mock.patch("bge_embedding.BGEEmbeddingService", ...)` senza dep BGE.
if not _has_tokenizers():
    import types as _types
    _stub = _types.ModuleType("bge_embedding")
    class _StubBGEMissing:
        def __init__(self, *a, **kw): pass
        def embed_texts(self, texts):  # placeholder, overridden by mock.patch
            return np.random.rand(len(texts), 384).astype("float32")
    _stub.BGEEmbeddingService = _StubBGEMissing
    sys.modules.setdefault("bge_embedding", _stub)

_RUNTIME = Path(__file__).resolve().parent.parent
_EXEC = _RUNTIME.parent / "executors" / "find_images_indices"
sys.path.insert(0, str(_RUNTIME))
sys.path.insert(0, str(_EXEC))
from messages import get as _msg  # noqa: E402  # §11 i18n: assert language-independent


def _build_unified_index(
    base_path: Path, idx_dir: Path,
    *, n_photos: int = 3, n_faces_per_photo: int = 1,
    descriptions: list[str] | None = None,
    keywords: list[list[str]] | None = None,
    gps: list[dict | None] | None = None,
    bbox_areas: list[int] | None = None,
):
    """Costruisce un unified index sintetico per test."""
    idx_dir.mkdir(parents=True, exist_ok=True)
    if descriptions is None:
        descriptions = [f"foto sintetica {i}" for i in range(n_photos)]
    if keywords is None:
        keywords = [[] for _ in range(n_photos)]
    if gps is None:
        gps = [None] * n_photos
    if bbox_areas is None:
        bbox_areas = [10000] * n_photos

    entries = []
    text_embs = []
    face_embs = []
    text_idx = 0
    face_idx = 0
    for i in range(n_photos):
        photo_path = str(base_path / f"img_{i}.jpg")
        # Text embedding
        v = np.random.rand(384).astype("float32")
        v /= np.linalg.norm(v)
        text_embs.append(v)
        # Faces
        faces = []
        for fj in range(n_faces_per_photo):
            fv = np.random.rand(512).astype("float32")
            fv /= np.linalg.norm(fv)
            face_embs.append(fv)
            # Compute bbox to match desired area
            area = bbox_areas[i]
            side = int(area ** 0.5)
            faces.append({
                "bbox": [10, 10, side, side],
                "detect_score": 0.95,
                "embedding_face_idx": face_idx,
            })
            face_idx += 1
        entry = {
            "path": photo_path,
            "sha256": f"sha{i}",
            "name": f"img_{i}.jpg",
            "mtime": 1000.0 + i,
            "size": 5000,
            "image_w": 100, "image_h": 100,
            "taken_at_iso": None,
            "exif_gps": gps[i],
            "description": descriptions[i],
            "keywords": keywords[i],
            "location_hint": "",
            "activity_hint": "",
            "faces": faces,
            "embedding_text_idx": text_idx,
        }
        text_idx += 1
        entries.append(entry)

    with (idx_dir / "entries.jsonl").open("w") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")
    np.save(str(idx_dir / "embeddings_text.npy"), np.stack(text_embs).astype("float32"))
    if face_embs:
        np.save(str(idx_dir / "embeddings_face.npy"), np.stack(face_embs).astype("float32"))
    (idx_dir / "meta.json").write_text(json.dumps({
        "schema_version": 4, "version": 4,
        "n_entries": n_photos, "n_faces": len(face_embs),
        "model_text": "stub", "dim_text": 384,
        "model_vlm": "stub", "model_face": "stub",
        "base_path": str(base_path),
    }))


class _StubBGE:
    def embed_texts(self, texts):
        return np.random.rand(len(texts), 384).astype("float32")


class TestFindImagesValidation(unittest.TestCase):

    def test_no_criterion_fails(self):
        import find_images_indices as fii
        out = fii.invoke({"base_path": "/tmp"})
        self.assertFalse(out["ok"])
        self.assertIn("criterion", out["error"])

    def test_idx_arg_ignored(self):
        import find_images_indices as fii
        # idx ignorato — ma manca query_text
        out = fii.invoke({"base_path": "/tmp", "idx": "scene"})
        self.assertFalse(out["ok"])

    def test_top_k_invalid(self):
        import find_images_indices as fii
        out = fii.invoke({"base_path": "/tmp", "top_k": 0, "query_text": "x"})
        self.assertFalse(out["ok"])

    def test_base_path_not_found(self):
        import find_images_indices as fii
        out = fii.invoke({
            "base_path": "/tmp/metnos_does_not_exist_xyz_unified_find",
            "query_text": "mare",
        })
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], _msg(
            "ERR_PATH_NOT_FOUND", path="/tmp/metnos_does_not_exist_xyz_unified_find"))


class TestFindImagesUnified(unittest.TestCase):

    def setUp(self):
        # §8.5 isolamento: `virt._cache` cacha l'embedder come SINGLETON di
        # modulo (`get_embedder("text")` → BGEEmbeddingService, una volta sola).
        # Se un test precedente lo riscalda, il nostro `mock.patch` su
        # `bge_embedding.BGEEmbeddingService` viene BYPASSATO (istanza già in
        # cache) → embedding reali → ranking diverso → flakiness order-dipendente.
        # Svuotiamo la cache PRIMA (no contaminazione in ingresso) e DOPO (no
        # leak dello stub ai test successivi).
        import virt
        virt._cache.clear()
        self.tmp = Path(tempfile.mkdtemp(prefix="metnos_find_unified_test_"))
        self._old = os.environ.get("METNOS_INDEX_ROOT")
        os.environ["METNOS_INDEX_ROOT"] = str(self.tmp / "index")
        # Simula corpus dir
        self.corpus = self.tmp / "photos"
        self.corpus.mkdir()
        # Calcola idx_dir
        import find_images_indices as fii
        self.idx_dir = fii._index_dir(self.corpus)

    def tearDown(self):
        import virt
        virt._cache.clear()  # non lasciare lo stub embedder in cache (vedi setUp)
        if self._old is None:
            os.environ.pop("METNOS_INDEX_ROOT", None)
        else:
            os.environ["METNOS_INDEX_ROOT"] = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_index_missing_triggers_lazy_build(self):
        import find_images_indices as fii
        # §7.3 lazy index: base_path esplicito con corpus ma SENZA indice →
        # lazy build async (status=indexing_started), NON hard-error.
        # Mock dello spawn: evita di lanciare un subprocess reale build_runner.
        sentinel = {"ok": True, "status": "indexing_started", "entries": [],
                    "job_id": "test_job", "est_minutes": 1,
                    "base_path": str(self.corpus)}
        with mock.patch.object(fii, "_spawn_index_build",
                               return_value=sentinel) as sp:
            out = fii.invoke({"base_path": str(self.corpus), "query_text": "mare"})
        sp.assert_called_once()
        self.assertTrue(out["ok"])
        self.assertEqual(out["status"], "indexing_started")
        self.assertIn("job_id", out)

    def test_schema_too_old(self):
        import find_images_indices as fii
        # Crea legacy v3 dir invece di unified
        legacy_persons = self.idx_dir.parent / "persons"
        legacy_persons.mkdir(parents=True)
        (legacy_persons / "meta.json").write_text(json.dumps({
            "schema_version": 2, "idx": "persons",
        }))
        out = fii.invoke({"base_path": str(self.corpus), "query_text": "mare"})
        self.assertFalse(out["ok"])
        self.assertEqual(out["error_class"], "schema_too_old")

    def test_query_text_returns_entries(self):
        import find_images_indices as fii
        _build_unified_index(
            self.corpus, self.idx_dir,
            n_photos=3,
            descriptions=["foto del mare azzurro", "montagna", "interno cucina"],
            keywords=[["mare", "spiaggia"], ["montagna"], ["cucina"]],
        )
        with mock.patch("bge_embedding.BGEEmbeddingService", return_value=_StubBGE()):
            out = fii.invoke({"base_path": str(self.corpus), "query_text": "mare"})
        self.assertTrue(out["ok"], msg=out)
        self.assertEqual(out["schema_version"], 4)
        # Almeno la foto del mare deve essere in entries (BM25 sui keywords)
        paths = [e["path"] for e in out["entries"]]
        self.assertTrue(any("img_0" in p for p in paths))

    def test_paths_filter_empty_returns_error_class(self):
        import find_images_indices as fii
        _build_unified_index(self.corpus, self.idx_dir, n_photos=2)
        out = fii.invoke({
            "base_path": str(self.corpus),
            "query_text": "x",
            "paths_filter": ["/nonexistent/foo.jpg"],
        })
        # Convenzione Metnos §2.8: error_class implica ok=True + entries=[]
        # (la "non-trovata" e' una risposta valida, non un crash).
        self.assertTrue(out["ok"])
        self.assertEqual(out["error_class"], "paths_filter_empty")
        self.assertEqual(out["entries"], [])

    def test_paths_filter_intersection(self):
        import find_images_indices as fii
        _build_unified_index(self.corpus, self.idx_dir, n_photos=3)
        target = str(self.corpus / "img_1.jpg")
        with mock.patch("bge_embedding.BGEEmbeddingService", return_value=_StubBGE()):
            out = fii.invoke({
                "base_path": str(self.corpus),
                "query_text": "test",
                "paths_filter": [target],
            })
        # OK con applied_paths_filter=1 (1 intersected)
        self.assertEqual(out.get("applied_paths_filter"), 1)

    def test_min_face_pixels_above_threshold(self):
        import find_images_indices as fii
        # foto 0 ha bbox 100x100=10000, foto 1 ha 200x200=40000
        _build_unified_index(
            self.corpus, self.idx_dir, n_photos=2,
            bbox_areas=[10000, 40000],
        )
        out = fii.invoke({
            "base_path": str(self.corpus),
            "min_face_pixels": 30000,
        })
        self.assertTrue(out["ok"], msg=out)
        # Solo foto 1
        self.assertEqual(len(out["entries"]), 1)
        self.assertIn("img_1", out["entries"][0]["path"])

    def test_min_face_pixels_no_match(self):
        import find_images_indices as fii
        _build_unified_index(
            self.corpus, self.idx_dir, n_photos=2,
            bbox_areas=[1000, 2000],
        )
        out = fii.invoke({
            "base_path": str(self.corpus),
            "min_face_pixels": 50000,
        })
        # error_class scenario → ok=True + entries=[] (§2.8)
        self.assertTrue(out["ok"])
        self.assertEqual(out["error_class"], "no_faces_above_size_threshold")
        self.assertEqual(out["entries"], [])

    def test_min_face_count(self):
        import find_images_indices as fii
        # foto 0 ha 1 faccia, foto 1 ha 1 faccia → min=2 ne tiene 0
        _build_unified_index(self.corpus, self.idx_dir, n_photos=2, n_faces_per_photo=1)
        out = fii.invoke({
            "base_path": str(self.corpus),
            "min_face_count": 2,
        })
        # Pass criterion (paths_filter or face filter); 0 results
        self.assertTrue(out["ok"])
        self.assertEqual(len(out["entries"]), 0)

    def test_max_face_count(self):
        import find_images_indices as fii
        _build_unified_index(self.corpus, self.idx_dir, n_photos=2, n_faces_per_photo=1)
        out = fii.invoke({
            "base_path": str(self.corpus),
            "max_face_count": 1,
        })
        self.assertTrue(out["ok"])

    def test_gps_filter(self):
        import find_images_indices as fii
        _build_unified_index(
            self.corpus, self.idx_dir, n_photos=2,
            gps=[{"lat": 44.4, "lon": 8.9}, {"lat": 50.0, "lon": 10.0}],
        )
        out = fii.invoke({
            "base_path": str(self.corpus),
            "near_lat": 44.4, "near_lon": 8.9, "radius_km": 100,
        })
        self.assertTrue(out["ok"])
        # Solo foto 0 vicina
        self.assertEqual(len(out["entries"]), 1)
        self.assertIn("img_0", out["entries"][0]["path"])

    def test_gps_no_match(self):
        import find_images_indices as fii
        _build_unified_index(
            self.corpus, self.idx_dir, n_photos=2,
            gps=[{"lat": 44.4, "lon": 8.9}, None],
        )
        out = fii.invoke({
            "base_path": str(self.corpus),
            "near_lat": 0.0, "near_lon": 0.0, "radius_km": 1,
        })
        self.assertTrue(out["ok"])
        self.assertEqual(len(out["entries"]), 0)

    def test_top_k_limit(self):
        import find_images_indices as fii
        _build_unified_index(self.corpus, self.idx_dir, n_photos=10)
        with mock.patch("bge_embedding.BGEEmbeddingService", return_value=_StubBGE()):
            out = fii.invoke({
                "base_path": str(self.corpus),
                "query_text": "test",
                "top_k": 3,
            })
        self.assertTrue(out["ok"])
        self.assertLessEqual(len(out["entries"]), 3)

    def test_top_k_max_cap(self):
        import find_images_indices as fii
        _build_unified_index(self.corpus, self.idx_dir, n_photos=5)
        with mock.patch("bge_embedding.BGEEmbeddingService", return_value=_StubBGE()):
            out = fii.invoke({
                "base_path": str(self.corpus),
                "query_text": "test",
                "top_k": 99999,  # oltre _TOP_K_MAX
            })
        self.assertTrue(out["ok"])

    def test_combo_query_text_and_face_count(self):
        import find_images_indices as fii
        _build_unified_index(
            self.corpus, self.idx_dir, n_photos=3,
            descriptions=["mare", "montagna", "casa"],
            keywords=[["mare"], ["montagna"], ["casa"]],
        )
        with mock.patch("bge_embedding.BGEEmbeddingService", return_value=_StubBGE()):
            out = fii.invoke({
                "base_path": str(self.corpus),
                "query_text": "mare",
                "max_face_count": 1,
            })
        self.assertTrue(out["ok"])
        # Sola foto 0 (con keyword "mare")
        paths = [e["path"] for e in out["entries"]]
        self.assertTrue(any("img_0" in p for p in paths))

    def test_empty_index_returns_empty(self):
        import find_images_indices as fii
        # Crea un indice vuoto
        self.idx_dir.mkdir(parents=True)
        (self.idx_dir / "meta.json").write_text(json.dumps({
            "schema_version": 4, "n_entries": 0,
        }))
        with mock.patch("bge_embedding.BGEEmbeddingService", return_value=_StubBGE()):
            out = fii.invoke({"base_path": str(self.corpus), "query_text": "x"})
        self.assertTrue(out["ok"])
        self.assertEqual(out["entries"], [])

    def test_time_window_year_filter(self):
        import find_images_indices as fii
        # Mtime in due anni diversi
        from datetime import datetime
        m_2024 = datetime(2024, 6, 15).timestamp()
        m_2023 = datetime(2023, 6, 15).timestamp()
        idx_dir = self.idx_dir
        idx_dir.mkdir(parents=True)
        entry_2024 = {
            "path": str(self.corpus / "y24.jpg"), "name": "y24.jpg",
            "mtime": m_2024, "size": 100, "image_w": 10, "image_h": 10,
            # time_window data le foto da taken_at_iso (EXIF)/path, NON da mtime
            # (data di modifica, mente sull'età foto §2.8). Datiamo via taken_at_iso.
            "taken_at_iso": "2024-06-15T12:00:00", "exif_gps": None,
            "description": "x", "keywords": [], "location_hint": "",
            "activity_hint": "", "faces": [], "embedding_text_idx": 0,
        }
        entry_2023 = dict(entry_2024)
        entry_2023["path"] = str(self.corpus / "y23.jpg")
        entry_2023["mtime"] = m_2023
        entry_2023["taken_at_iso"] = "2023-06-15T12:00:00"
        entry_2023["embedding_text_idx"] = 1
        with (idx_dir / "entries.jsonl").open("w") as fh:
            fh.write(json.dumps(entry_2024) + "\n")
            fh.write(json.dumps(entry_2023) + "\n")
        np.save(str(idx_dir / "embeddings_text.npy"),
                np.random.rand(2, 384).astype("float32"))
        (idx_dir / "meta.json").write_text(json.dumps({
            "schema_version": 4, "n_entries": 2,
        }))
        # Criterio non-text: paths_filter accetta entrambi, time_window 2024
        # restringe a y24. (Test isolato del time_window filter, no text scoring.)
        with mock.patch("bge_embedding.BGEEmbeddingService", return_value=_StubBGE()):
            out = fii.invoke({
                "base_path": str(self.corpus),
                "paths_filter": [str(self.corpus / "y24.jpg"),
                                 str(self.corpus / "y23.jpg")],
                "time_window": "2024",
            })
        self.assertTrue(out["ok"])
        # Solo y24 nel range 2024
        paths = [e["path"] for e in out["entries"]]
        self.assertTrue(any("y24" in p for p in paths))
        self.assertFalse(any("y23" in p for p in paths))

    def test_response_pattern_2_7_truncated(self):
        import find_images_indices as fii
        _build_unified_index(self.corpus, self.idx_dir, n_photos=20)
        with mock.patch("bge_embedding.BGEEmbeddingService", return_value=_StubBGE()):
            out = fii.invoke({
                "base_path": str(self.corpus),
                "query_text": "test",
                "top_k": 5,
            })
        if out.get("truncated"):
            self.assertEqual(out["truncated_what"], "entries")
            self.assertEqual(out["cap_field"], "top_k")
            self.assertEqual(out["cap_value"], 5)

    def test_match_type_text(self):
        import find_images_indices as fii
        _build_unified_index(
            self.corpus, self.idx_dir, n_photos=2,
            descriptions=["mare blu", "altro"],
            keywords=[["mare"], []],
        )
        with mock.patch("bge_embedding.BGEEmbeddingService", return_value=_StubBGE()):
            out = fii.invoke({"base_path": str(self.corpus), "query_text": "mare"})
        self.assertTrue(out["ok"])
        if out["entries"]:
            self.assertIn(out["entries"][0]["match_type"], ("text", "compose"))

    def test_match_type_gps(self):
        import find_images_indices as fii
        _build_unified_index(
            self.corpus, self.idx_dir, n_photos=1,
            gps=[{"lat": 44.4, "lon": 8.9}],
        )
        out = fii.invoke({
            "base_path": str(self.corpus),
            "near_lat": 44.4, "near_lon": 8.9, "radius_km": 100,
        })
        self.assertTrue(out["ok"])
        if out["entries"]:
            self.assertEqual(out["entries"][0]["match_type"], "gps")


class TestFindImagesResolve(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="metnos_find_resolve_test_"))
        self._old_root = os.environ.get("METNOS_INDEX_ROOT")
        self._old_data = os.environ.get("METNOS_USER_DATA")
        os.environ["METNOS_INDEX_ROOT"] = str(self.tmp / "index")
        os.environ["METNOS_USER_DATA"] = str(self.tmp / "data")
        (self.tmp / "data").mkdir(parents=True)

    def tearDown(self):
        if self._old_root is None:
            os.environ.pop("METNOS_INDEX_ROOT", None)
        else:
            os.environ["METNOS_INDEX_ROOT"] = self._old_root
        if self._old_data is None:
            os.environ.pop("METNOS_USER_DATA", None)
        else:
            os.environ["METNOS_USER_DATA"] = self._old_data
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_omitted_no_indexed_dirs(self):
        import find_images_indices as fii
        out = fii.invoke({"query_text": "mare"})
        self.assertFalse(out["ok"])
        self.assertEqual(out["error_class"], "no_workspace")

    def test_symbolic_match(self):
        import find_images_indices as fii
        photos = self.tmp / "data" / "Immagini"
        photos.mkdir()
        idx_dir = fii._index_dir(photos)
        _build_unified_index(photos, idx_dir, n_photos=1)
        with mock.patch("bge_embedding.BGEEmbeddingService", return_value=_StubBGE()):
            out = fii.invoke({"base_path": "Immagini", "query_text": "x"})
        self.assertTrue(out["ok"])


if __name__ == "__main__":
    unittest.main()
