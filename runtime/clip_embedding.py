"""SigLIP text+image embedding service (in-process, lazy-loaded).

Drop-in pattern modellato su `suprastructure.embedding.onnx_embedding`:
    - ONNX Runtime CPU per inferenza
    - Tokenizer HuggingFace (Rust-based) per la query testuale
    - PIL per la decodifica/resize delle immagini
    - L2-normalize → cosine similarity = dot product
    - Singleton `get_clip_engine()` per inizializzazione lazy

Modello atteso: SigLIP-base-patch16-224 (Xenova ONNX).
Path di default: `<install_root>/models/siglip/`. Override via env:
`METNOS_CLIP_MODEL_DIR`.

Preferenza filename: `text_model.onnx` / `vision_model.onnx` (fp32, ~441+372MB).
Fallback: `text_model_quantized.onnx` / `vision_model_quantized.onnx`
(int8, ~110+95MB) — degradano embedding space (text-text cos~0.79 fra
parole non correlate, image-text ~0.02-0.07 anche su match ovvi),
diagnosi 9/5/2026, sostituiti con fp32.

I file richiesti nella cartella sono:
    - text_model.onnx (fp32) o text_model_quantized.onnx (fallback)
    - vision_model.onnx (fp32) o vision_model_quantized.onnx (fallback)
    - tokenizer.json                 (tokenizer SentencePiece HF format)
    - preprocessor_config.json       (image mean/std, resize)
    - config.json                    (hidden_size=768)
    - spiece.model + special_tokens_map.json + tokenizer_config.json

Embedding dim: 768 (da config.json text_config.hidden_size).
Input image: 224x224, mean=[0.5,0.5,0.5], std=[0.5,0.5,0.5], rescale 1/255.

Niente HTTP server: tutto in-process.

NB: i nomi degli executor che useranno questo modulo sono in discussione e
non vengono fissati qui. Il backend espone API stabili indipendenti dal
naming finale.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Optional, Union

import numpy as np

logger = logging.getLogger(__name__)

__all__ = [
    "ClipEngine",
    "get_clip_engine",
]


def _default_model_dir() -> Path:
    """Risolve la dir del modello. Env > default rename-resilient."""
    env = os.environ.get("METNOS_CLIP_MODEL_DIR")
    if env:
        return Path(env)
    # ADR 0148: derive from PATH_ROOT.
    import config as _C
    return _C.PATH_ROOT / "models" / "siglip"


# ── Engine ──────────────────────────────────────────────────────────


class ClipEngine:
    """Embedding SigLIP cross-modale (testo + immagine).

    Lazy-init: il primo `embed_text` o `embed_images` carica i due ONNX
    e il tokenizer. Idempotente, thread-safe.

    Riferimento Protocol: API si avvicina a EmbeddingProvider di
    suprastructure ma con due output paralleli (text/image), entrambi
    nello stesso spazio 768 dim.
    """

    name = "clip_siglip"

    def __init__(self, model_dir: Optional[Union[str, Path]] = None):
        self._model_dir = Path(model_dir) if model_dir else _default_model_dir()
        self._dim: Optional[int] = None
        self._image_size: int = 224
        self._image_mean: np.ndarray = np.array([0.5, 0.5, 0.5], dtype=np.float32)
        self._image_std: np.ndarray = np.array([0.5, 0.5, 0.5], dtype=np.float32)
        self._rescale: float = 1.0 / 255.0
        self._text_session = None
        self._vision_session = None
        self._tokenizer = None
        self._max_text_len: int = 64
        self._available: Optional[bool] = None
        self._load_lock = threading.Lock()

    def _resolve_text_path(self) -> Path:
        """Preferenza fp32 (`text_model.onnx`) > quantized fallback.

        Il quantized SigLIP-base int8 ha collasso embedding space (text-text
        cos~0.79 fra parole non correlate, image-text ~0.02-0.07 anche su
        match ovvi). Diagnosi 9/5/2026. Default ora fp32, quantized solo
        come fallback.
        """
        fp32 = self._model_dir / "text_model.onnx"
        if fp32.exists():
            return fp32
        return self._model_dir / "text_model_quantized.onnx"

    def _resolve_vision_path(self) -> Path:
        fp32 = self._model_dir / "vision_model.onnx"
        if fp32.exists():
            return fp32
        return self._model_dir / "vision_model_quantized.onnx"

    @property
    def available(self) -> bool:
        """True se i due ONNX e il tokenizer esistono su disco."""
        if self._available is None:
            text_ok = self._resolve_text_path().exists()
            vis_ok = self._resolve_vision_path().exists()
            tok_ok = (self._model_dir / "tokenizer.json").exists()
            self._available = text_ok and vis_ok and tok_ok
            if not self._available:
                logger.warning(
                    "ClipEngine: modello SigLIP non completo in %s (text=%s vision=%s tok=%s)",
                    self._model_dir, text_ok, vis_ok, tok_ok,
                )
        return self._available

    @property
    def dimension(self) -> int:
        """Vector dimension (768 per SigLIP-base)."""
        if self._dim is None:
            cfg = self._model_dir / "config.json"
            if cfg.exists():
                with open(cfg, encoding="utf-8") as f:
                    data = json.load(f)
                self._dim = int(data.get("text_config", {}).get("hidden_size", 768))
            else:
                self._dim = 768
        return self._dim

    def _load(self) -> None:
        """Carica ONNX e tokenizer. Idempotente."""
        if self._text_session is not None:
            return
        with self._load_lock:
            if self._text_session is not None:
                return
            import onnxruntime as ort
            from tokenizers import Tokenizer

            opts = ort.SessionOptions()
            opts.inter_op_num_threads = 2
            opts.intra_op_num_threads = 2

            text_path = self._resolve_text_path()
            vis_path = self._resolve_vision_path()
            tok_path = self._model_dir / "tokenizer.json"
            for p in (text_path, vis_path, tok_path):
                if not p.exists():
                    raise FileNotFoundError(
                        f"ClipEngine: file mancante {p}. Esegui "
                        "<install_root>/install/download_models.sh siglip",
                    )
            # Logga la variante scelta (fp32 vs quantized) per audit
            text_variant = "fp32" if text_path.name == "text_model.onnx" else "quantized"
            vis_variant = "fp32" if vis_path.name == "vision_model.onnx" else "quantized"
            logger.info(
                "ClipEngine: text=%s (%s) vision=%s (%s)",
                text_path.name, text_variant, vis_path.name, vis_variant,
            )

            self._text_session = ort.InferenceSession(
                str(text_path), sess_options=opts,
                providers=["CPUExecutionProvider"],
            )
            self._vision_session = ort.InferenceSession(
                str(vis_path), sess_options=opts,
                providers=["CPUExecutionProvider"],
            )
            self._tokenizer = Tokenizer.from_file(str(tok_path))
            # SigLIP requires FIXED padding to max_length=64 (training-time
            # convention). With length=None (dynamic padding) the text encoder
            # produces degenerate embeddings: queries like "cat" or "birthday
            # party" yield cosine similarity ~0.05 (noise) instead of typical
            # 0.15-0.30 for valid matches. Test live 5/5/2026: bug discovered
            # after full-corpus build (30k photos) returned all-noise rankings.
            self._tokenizer.enable_padding(
                direction="right", pad_id=1, pad_token="</s>",
                length=self._max_text_len,
            )
            self._tokenizer.enable_truncation(max_length=self._max_text_len)

            # Aggiorna preprocessor da preprocessor_config.json se presente
            pp = self._model_dir / "preprocessor_config.json"
            if pp.exists():
                with open(pp, encoding="utf-8") as f:
                    data = json.load(f)
                size = data.get("size", {})
                self._image_size = int(size.get("height", 224))
                self._image_mean = np.array(
                    data.get("image_mean", [0.5, 0.5, 0.5]), dtype=np.float32,
                )
                self._image_std = np.array(
                    data.get("image_std", [0.5, 0.5, 0.5]), dtype=np.float32,
                )
                self._rescale = float(data.get("rescale_factor", 1.0 / 255.0))

            logger.info(
                "ClipEngine: loaded (dim=%d, image_size=%d)",
                self.dimension, self._image_size,
            )

    # ── API: testo ──────────────────────────────────────────────────

    def embed_text(self, text: str, *, normalize: bool = True) -> np.ndarray:
        """Embedding di una query testuale. Shape: `(dim,)`."""
        return self.embed_texts([text], normalize=normalize)[0]

    def embed_texts(
        self, texts: list[str], *, normalize: bool = True,
    ) -> np.ndarray:
        """Embedding batch di testi. Shape: `(N, dim)`."""
        if not texts:
            return np.zeros((0, self.dimension), dtype=np.float32)
        self._load()
        encoded = self._tokenizer.encode_batch(texts)
        input_ids = np.array([e.ids for e in encoded], dtype=np.int64)
        outputs = self._text_session.run(
            ["pooler_output"], {"input_ids": input_ids},
        )
        emb = outputs[0].astype(np.float32)
        if normalize:
            emb = self._l2_normalize(emb)
        return emb

    # ── API: immagini ───────────────────────────────────────────────

    def embed_images(
        self,
        paths: list[Union[str, Path]],
        *,
        batch_size: int = 8,
        normalize: bool = True,
    ) -> np.ndarray:
        """Embedding batch di immagini. Shape: `(N, dim)`.

        I path sono caricati e ridimensionati a 224x224 (bicubic),
        normalizzati con mean/std da preprocessor_config.json e dati in
        pasto al vision_model.
        """
        if not paths:
            return np.zeros((0, self.dimension), dtype=np.float32)
        self._load()
        all_emb: list[np.ndarray] = []
        for start in range(0, len(paths), batch_size):
            batch = paths[start:start + batch_size]
            pixel_values = self._preprocess_images(batch)
            outputs = self._vision_session.run(
                ["pooler_output"], {"pixel_values": pixel_values},
            )
            emb = outputs[0].astype(np.float32)
            if normalize:
                emb = self._l2_normalize(emb)
            all_emb.append(emb)
        return np.vstack(all_emb) if len(all_emb) > 1 else all_emb[0]

    # ── Similarita' ─────────────────────────────────────────────────

    @staticmethod
    def similarity(query: np.ndarray, candidates: np.ndarray) -> np.ndarray:
        """Cosine similarity. Assume vettori L2-normalizzati.

        - `query` shape `(dim,)` → ritorna `(N,)`.
        - `query` shape `(M, dim)` → ritorna `(M, N)`.
        """
        if candidates.ndim == 1:
            candidates = candidates.reshape(1, -1)
        if query.ndim == 1:
            return candidates @ query
        return query @ candidates.T

    # ── Preprocess ──────────────────────────────────────────────────

    def _preprocess_images(
        self, paths: list[Union[str, Path]],
    ) -> np.ndarray:
        """Carica + resize + normalize + reorder (NHWC → NCHW)."""
        from PIL import Image
        size = self._image_size
        out = np.zeros((len(paths), 3, size, size), dtype=np.float32)
        for i, p in enumerate(paths):
            img = Image.open(str(p)).convert("RGB")
            # bicubic resample (PIL.Image.BICUBIC == 3, matches preprocessor_config)
            img = img.resize((size, size), Image.BICUBIC)
            arr = np.asarray(img, dtype=np.float32) * self._rescale
            arr = (arr - self._image_mean) / self._image_std
            # HWC → CHW
            out[i] = np.transpose(arr, (2, 0, 1))
        return out

    @staticmethod
    def _l2_normalize(x: np.ndarray) -> np.ndarray:
        norms = np.linalg.norm(x, axis=-1, keepdims=True)
        norms = np.clip(norms, 1e-9, None)
        return x / norms

    def health(self) -> dict:
        text_p = self._resolve_text_path()
        vis_p = self._resolve_vision_path()
        text_variant = "fp32" if text_p.name == "text_model.onnx" else "quantized"
        vis_variant = "fp32" if vis_p.name == "vision_model.onnx" else "quantized"
        return {
            "available": self.available,
            "loaded": self._text_session is not None,
            "model_dir": str(self._model_dir),
            "dimension": self.dimension if self.available else None,
            "image_size": self._image_size,
            "engine": "clip_siglip",
            "text_variant": text_variant,
            "vision_variant": vis_variant,
        }


# ── Singleton ────────────────────────────────────────────────────────

_instance: Optional[ClipEngine] = None
_instance_lock = threading.Lock()


def get_clip_engine(
    model_dir: Optional[Union[str, Path]] = None,
) -> ClipEngine:
    """Singleton `ClipEngine`. Prima call fissa il model_dir."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = ClipEngine(model_dir)
    return _instance
