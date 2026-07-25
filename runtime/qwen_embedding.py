"""Qwen3-Embedding-0.6B come servizio embedding testo locale.

API simmetrica a `bge_embedding.BGEEmbeddingService`:

  emb = QwenEmbeddingService()
  M   = emb.embed_texts(["documento", ...])   # (N, 1024) L2-normalized
  q   = emb.embed_query("domanda")            # (1024,)  L2-normalized

Differenza sostanziale rispetto a BGE: il modello è instruction-aware e
ASIMMETRICO — il documento si codifica nudo, la QUERY riceve un prefisso di
istruzione. Per questo `embed_query` non è un alias di `embed_texts`: è il
punto dove l'asimmetria vive. L'istruzione è in inglese e indipendente dalla
lingua della query (il modello è multilingue; la convenzione è del modello,
non un testo utente).

Pooling last-token, padding e template sono quelli dichiarati dal checkout
sentence-transformers del modello: si usa quel runtime invece di replicarli a
mano su ONNX. Import pigri: il processo che resta su BGE non paga torch.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

_MODEL_ID = "Qwen/Qwen3-Embedding-0.6B"
_QUERY_INSTRUCTION = (
    "Instruct: Given a question about the Metnos assistant, retrieve the "
    "documentation passage that answers it\nQuery: "
)


class QwenEmbeddingService:
    """Wrapper sottile e cache-friendly sul checkout sentence-transformers."""

    def __init__(self, model_dir: str | None = None,
                 query_instruction: str | None = None,
                 max_length: int = 1024) -> None:
        from sentence_transformers import SentenceTransformer

        self._source = str(model_dir) if model_dir else _MODEL_ID
        self._instruction = (query_instruction if query_instruction is not None
                             else _QUERY_INSTRUCTION)
        self._model = SentenceTransformer(
            self._source, device="cpu", local_files_only=True)
        self._model.max_seq_length = max_length

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        """Lato documento: testi nudi. Ritorna (N, D) L2-normalized."""

        if not texts:
            dimension = int(self._model.get_sentence_embedding_dimension())
            return np.zeros((0, dimension), dtype=np.float32)
        matrix = np.asarray(self._model.encode(
            list(texts), batch_size=8, show_progress_bar=False,
        ), dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True).clip(min=1e-9)
        return (matrix / norms).astype(np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        """Lato query: prefisso di istruzione, poi la domanda."""

        return self.embed_texts([self._instruction + str(text)])[0]


def resolved_model_files(model_dir: str | None = None) -> tuple[Path, ...]:
    """File che identificano il modello per il fingerprint del catalogo.

    Deve restare leggera (niente torch): il fingerprint gira a ogni accesso
    al catalogo. Con un `model_dir` locale usa quello; altrimenti risolve il
    checkout già in cache HF senza rete (`local_files_only`).
    """

    if model_dir:
        root = Path(model_dir)
    else:
        from huggingface_hub import snapshot_download

        root = Path(snapshot_download(_MODEL_ID, local_files_only=True))
    return (
        root / "config.json",
        root / "model.safetensors",
        root / "tokenizer.json",
    )
