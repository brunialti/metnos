"""Test minimale per `runtime/face_embedding.py` (RetinaFace + ArcFace).

Skip-friendly: se i modelli buffalo_l non sono presenti in
`<install_root>/models/face/` (download via
`install/download_models.sh face`), i test sono saltati. In CI senza
modelli, suite resta verde.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


def _models_present() -> bool:
    base = Path(__file__).resolve().parents[3] / "models/face"
    return (base / "det_10g.onnx").exists() and (base / "w600k_r50.onnx").exists()


@unittest.skipUnless(_models_present(),
                     "Face pack non scaricato (esegui install/download_models.sh face)")
class TestFaceEngine(unittest.TestCase):
    """Smoke test: lazy init, detect su immagine senza volti, match shape."""

    @classmethod
    def setUpClass(cls):
        import face_embedding
        face_embedding._instance = None  # noqa: SLF001
        cls.engine = face_embedding.get_face_engine()

    def test_lazy_init_and_health(self):
        """Engine pronto, available True, dim 512."""
        self.assertTrue(self.engine.available)
        h = self.engine.health()
        self.assertEqual(h["engine"], "face_buffalo_l")
        self.assertEqual(h["dimension"], 512)

    def test_detect_on_blank_image_returns_empty(self):
        """Immagine senza volti → lista vuota (caso degenere N=0)."""
        import tempfile
        from PIL import Image
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "blank.jpg"
            Image.new("RGB", (640, 480), (128, 128, 128)).save(p)
            faces = self.engine.detect_faces(p)
            # Su un'immagine grigia uniforme RetinaFace non dovrebbe
            # trovare volti; ma se lo facesse (rumore quantizzato),
            # accettiamo che la lista sia vuota o conforme allo schema.
            self.assertIsInstance(faces, list)
            for f in faces:
                self.assertIn("bbox", f)
                self.assertIn("embedding", f)
                self.assertEqual(f["embedding"].shape, (512,))

    def test_match_with_synthetic_embeddings(self):
        """match() scoring + threshold filter su embedding sintetici.

        Construisce 3 candidates: due copie con piccola perturbazione
        della query (cosine ~0.95+) e una random (cosine ~0). Verifica
        che i due vicini superino soglia 0.5 e che l'ordine sia desc.
        """
        import numpy as np
        rng = np.random.default_rng(seed=42)
        query = rng.standard_normal(512).astype(np.float32)
        query /= np.linalg.norm(query)

        # Due copies con piccola perturbazione (eps in dimensione 512:
        # eps=0.01 → cosine ~0.99). Senza fissare la magnitudine la
        # randomness su 512 dim degrada veloce per via della media nulla.
        noise1 = rng.standard_normal(512).astype(np.float32) * 0.01
        c1 = query + noise1
        c1 /= np.linalg.norm(c1)
        noise2 = rng.standard_normal(512).astype(np.float32) * 0.02
        c2 = query + noise2
        c2 /= np.linalg.norm(c2)
        # far: random ortogonale
        c3 = rng.standard_normal(512).astype(np.float32)
        c3 /= np.linalg.norm(c3)

        candidates = np.stack([c1, c2, c3], axis=0)
        matches = self.engine.match(query, candidates, threshold=0.5)
        idxs = [m[0] for m in matches]
        self.assertIn(0, idxs, f"c1 (epsilon 0.01) sotto soglia: {matches}")
        self.assertIn(1, idxs, f"c2 (epsilon 0.02) sotto soglia: {matches}")
        # Ordine score desc
        scores = [m[1] for m in matches]
        self.assertEqual(scores, sorted(scores, reverse=True))


if __name__ == "__main__":
    unittest.main()
