"""virt.providers — implementazioni NON già presenti altrove.

Le classi locali (`BGEEmbeddingService`, `ClipEngine`) conformano già ai
Protocol: la factory le ritorna direttamente, qui non si duplica nulla. L'unico
provider nuovo è quello REMOTO (embedder dietro un endpoint OpenAI-compat) — la
controparte del «puntare un tier a un endpoint» del LLM. Massima semplicità.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np


class HttpEmbedder:
    """Embedder remoto via `POST <base_url>/v1/embeddings` (OpenAI-compat).

    Permette di virtualizzare l'embedding su un server esterno editando solo
    `embedding_tiers.toml` (`provider="http"`, `base_url=...`), senza codice.
    """

    def __init__(self, base_url: str, model: str = "local", timeout_s: int = 30):
        self.name = f"http:{model}"
        self._url = base_url.rstrip("/") + "/v1/embeddings"
        self._model = model
        self._timeout = timeout_s

    def embed_texts(self, texts: list[str]) -> "np.ndarray":
        import httpx
        import numpy as np
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        r = httpx.post(self._url, json={"model": self._model, "input": list(texts)},
                       timeout=self._timeout)
        r.raise_for_status()
        rows = [d["embedding"] for d in r.json().get("data", [])]
        return np.asarray(rows, dtype=np.float32)

    def embed_query(self, text: str) -> "np.ndarray":
        return self.embed_texts([text])[0]
