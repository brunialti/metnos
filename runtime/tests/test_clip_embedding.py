"""Test minimale per `runtime/clip_embedding.py` (SigLIP).

I test sono skip-friendly: se i modelli SigLIP non sono presenti
in `<install_root>/models/siglip/` (download via
`install/download_models.sh siglip`), tutti i test sono saltati con
messaggio chiaro. In CI senza modelli, suite resta verde.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


# ── Skip helper ────────────────────────────────────────────────────


def _models_present() -> bool:
    base = Path(__file__).resolve().parents[2] / "models/siglip"
    return all((base / f).exists() for f in (
        "text_model_quantized.onnx",
        "vision_model_quantized.onnx",
        "tokenizer.json",
    ))


def _tokenizers_present() -> bool:
    try:
        import tokenizers  # noqa: F401
        return True
    except ImportError:
        return False


@unittest.skipUnless(
    _models_present() and _tokenizers_present(),
    "SigLIP non scaricato o `tokenizers` mancante "
    "(esegui install/download_models.sh siglip + pip install tokenizers)",
)
class TestClipEngine(unittest.TestCase):
    """Smoke test: lazy init, embed_text shape, embed_images shape."""

    @classmethod
    def setUpClass(cls):
        # Forza un'istanza fresca per ogni test class (singleton sandbox).
        import clip_embedding
        clip_embedding._instance = None  # noqa: SLF001
        cls.engine = clip_embedding.get_clip_engine()

    def test_lazy_init_and_health(self):
        """Engine creato non scarica i modelli finche' non si chiama embed."""
        self.assertTrue(self.engine.available)
        h = self.engine.health()
        self.assertEqual(h["engine"], "clip_siglip")
        # Prima di embed: non caricato (lazy)
        # NB: l'health() interroga `available` non `loaded`; verifichiamo
        # che il dim sia 768 dal config.json senza aver fatto inferenza.
        self.assertEqual(h["dimension"], 768)

    def test_embed_text_shape(self):
        """embed_text → vettore (768,) L2-normalizzato."""
        import numpy as np
        v = self.engine.embed_text("una immagine di un gatto sul divano")
        self.assertEqual(v.shape, (768,))
        norm = float(np.linalg.norm(v))
        self.assertAlmostEqual(norm, 1.0, places=4)

    def test_embed_images_shape(self):
        """embed_images su 2 immagini sintetiche → (2, 768) L2-norm."""
        import numpy as np
        import tempfile
        from PIL import Image

        with tempfile.TemporaryDirectory() as td:
            paths = []
            # Immagine 1: tutta rossa
            p1 = Path(td) / "red.jpg"
            Image.new("RGB", (320, 240), (200, 30, 30)).save(p1)
            paths.append(p1)
            # Immagine 2: tutta blu
            p2 = Path(td) / "blue.jpg"
            Image.new("RGB", (320, 240), (30, 30, 200)).save(p2)
            paths.append(p2)

            emb = self.engine.embed_images(paths)
            self.assertEqual(emb.shape, (2, 768))
            norms = np.linalg.norm(emb, axis=1)
            for n in norms:
                self.assertAlmostEqual(float(n), 1.0, places=4)


if __name__ == "__main__":
    unittest.main()
