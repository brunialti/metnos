"""virt.interfaces — i contratti di virtualizzazione, ridotti all'osso.

Sottoinsieme MINIMO di `suprastructure.interfaces.{embedding,llm}`: solo i
metodi che Metnos usa davvero. Le classi concrete esistenti
(`BGEEmbeddingService`, `ClipEngine`, `LlamaCppProvider`) conformano già a
questi Protocol senza adapter — i Protocol sono contratto/typing, non una
gerarchia da ereditare. (Il VLM è virtualizzato a livello di CONFIG, vedi
`virt.get_vlm`: il calcolo immagine vive nell'executor, non qui.)
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    import numpy as np


class VirtError(Exception):
    """Base errori di virtualizzazione."""


class EmbeddingUnavailableError(VirtError):
    """Embedder non caricato / endpoint giù."""


class VLMUnavailableError(VirtError):
    """VLM :8081 non disponibile."""


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Testo (e, per SigLIP, immagine) → vettore L2-normalizzato (ndarray)."""

    def embed_texts(self, texts: list[str]) -> "np.ndarray": ...
    def embed_query(self, text: str) -> "np.ndarray": ...


@runtime_checkable
class LLMProvider(Protocol):
    """Completamento testo. Subset sincrono: `chat(system, user) -> result.text`."""

    def chat(self, system: str, user: str, **kwargs: Any) -> Any: ...
