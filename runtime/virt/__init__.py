"""virt — casa UNICA, segregata e minimale della virtualizzazione modelli.

Tre facciate config-driven, stile `llm_router` (factory, NIENTE registry/DI):

    from virt import get_embedder, get_llm, get_vlm
    get_embedder("text").embed_texts([...])   # BGE-M3 (o SigLIP "image", o http)
    get_llm("middle").chat(system, user).text  # delega a llm_router
    get_vlm()                                  # spec config del VLM :8081

Cambiare modello = editare `~/.config/metnos/{embedding,vlm}_tiers.toml` (il LLM
ha già `llm_tiers.toml`). Mai il codice. I default uguagliano la realtà attuale.
"""
from __future__ import annotations

from . import tiers
from .interfaces import (  # noqa: F401
    EmbeddingProvider, LLMProvider,
    EmbeddingUnavailableError, VLMUnavailableError, VirtError,
)

__all__ = [
    "get_embedder", "get_local_embedder", "get_llm", "get_vlm", "ensure_vlm_up",
    "EmbeddingProvider", "LLMProvider",
    "EmbeddingUnavailableError", "VLMUnavailableError", "VirtError",
]

# Default baked-in = realtà attuale (cutover behavior-preserving).
DEFAULT_EMBEDDERS = {
    "text":  {"provider": "bge"},      # bge_embedding.BGEEmbeddingService (1024)
    "image": {"provider": "siglip"},   # clip_embedding.ClipEngine (768, text+image)
}
DEFAULT_VLM = {
    "default": {
        "provider": "llamacpp", "model": "qwen3vl-2b",
        "base_url": "http://127.0.0.1:8081",
        "timeout_s": 60, "max_edge": 1024, "max_tokens": 512,
        # Synchronous VLM work is deliberately bounded independently from
        # the number of entries produced by an upstream filesystem step.
        # This prevents a directory listing from turning into hundreds of
        # serial model calls while still allowing an operator to tune the
        # policy from the Virt administration surface.
        "max_images_per_request": 8,
        "request_budget_s": 45,
    },
}

_cache: dict = {}


def get_embedder(role: str = "text"):
    """EmbeddingProvider per ruolo: "text"=BGE-M3, "image"=SigLIP, o remoto
    ("http"). Istanza cachata/lazy. Le classi locali conformano già al Protocol."""
    ck = ("emb", role)
    if ck in _cache:
        return _cache[ck]
    s = tiers.spec("embedding", role, DEFAULT_EMBEDDERS)
    prov = (s.get("provider") or "bge").lower()
    if prov == "bge":
        from bge_embedding import BGEEmbeddingService
        obj = BGEEmbeddingService(s.get("model_dir"))
    elif prov == "qwen":
        from qwen_embedding import QwenEmbeddingService
        obj = QwenEmbeddingService(
            s.get("model_dir"), query_instruction=s.get("query_instruction"))
    elif prov == "siglip":
        from clip_embedding import get_clip_engine
        obj = get_clip_engine(s.get("model_dir"))
    elif prov in ("http", "openai", "remote"):
        from .providers import HttpEmbedder
        ep = s.get("endpoint") or s.get("base_url")
        if not ep:
            raise EmbeddingUnavailableError(f"embedding role {role!r}: manca base_url")
        obj = HttpEmbedder(ep, s.get("model", "local"), int(s.get("timeout_s", 30)))
    else:
        raise EmbeddingUnavailableError(f"provider embedding sconosciuto: {prov!r}")
    _cache[ck] = obj
    return obj


def get_local_embedder(role: str = "text"):
    """Return an in-process embedder, never an HTTP-configured backend.

    Read-only executors use this boundary when their signed contract declares
    local computation only.  Model-path options from a local ``bge`` or
    ``siglip`` tier are preserved; a remote tier is deliberately ignored
    instead of silently enlarging network authority.
    """
    ck = ("emb-local", role)
    if ck in _cache:
        return _cache[ck]
    spec = tiers.spec("embedding", role, DEFAULT_EMBEDDERS)
    if role == "text" and spec.get("provider") == "qwen":
        from qwen_embedding import QwenEmbeddingService
        obj = QwenEmbeddingService(
            spec.get("model_dir"),
            query_instruction=spec.get("query_instruction"),
        )
    elif role == "text":
        from bge_embedding import BGEEmbeddingService
        obj = BGEEmbeddingService(
            spec.get("model_dir") if spec.get("provider") == "bge" else None,
        )
    elif role == "image":
        from clip_embedding import get_clip_engine
        obj = get_clip_engine(
            spec.get("model_dir") if spec.get("provider") == "siglip" else None,
        )
    else:
        raise EmbeddingUnavailableError(f"local embedding role sconosciuto: {role!r}")
    _cache[ck] = obj
    return obj


def get_llm(role: str = "middle"):
    """LLMProvider per tier ("fast"/"middle"/"wise"/"frontier"). Delega a
    `llm_router` — che È già la factory config-driven da `llm_tiers.toml`."""
    from llm_router import LLMRouter
    return LLMRouter().provider(role)


def get_vlm(role: str = "default") -> dict:
    """Spec config del VLM da ``vlm_tiers.toml``.

    Oltre al binding e ai parametri del modello, la spec contiene i limiti del
    lavoro sincrono (``max_images_per_request`` e ``request_budget_s``). Il
    calcolo immagine vive nei consumatori; qui si virtualizza soltanto la
    configurazione, così modello, endpoint e limiti cambiano senza codice.
    """
    return tiers.spec("vlm", role, DEFAULT_VLM)


# Lifecycle VLM (lazy-start + health), una sola volta per processo. Centralizzata
# qui — non dentro un executor — cosi' OGNI consumatore del VLM la condivide
# (Metnos possiede l'up del modello, non un effetto collaterale di un executor).
_vlm_started: dict = {}


def ensure_vlm_up(role: str = "default", *, wait_s: float = 35,
                  deadline_at: float | None = None) -> bool:
    """Avvia il server VLM via `scripts/vlm_server.sh` se non gia' in piedi e
    non gia' tentato in questo processo. Ritorna True se l'endpoint risponde
    /health entro `wait_s`, False altrimenti (il chiamante decide il fallback).

    Idempotente per (processo, role): un solo tentativo di start; le chiamate
    successive ritornano lo stato dell'health corrente. Endpoint e path-script
    sono config-driven: base_url da `get_vlm(role)`, override script via env
    `METNOS_VLM_SERVER_SH`. Se ``deadline_at`` è fornito, start, health check
    e attesa condividono quel deadline monotono: il lazy start non può quindi
    oltrepassare il budget del chiamante. Deterministico, no LLM."""
    import os
    import time
    import urllib.error as _ue
    import urllib.request as _u
    from pathlib import Path

    spec = get_vlm(role)
    base_url = (spec.get("base_url") or "http://127.0.0.1:8081").rstrip("/")
    health_url = base_url + "/health"

    def _remaining(cap_s: float) -> float:
        cap = max(0.0, float(cap_s))
        if deadline_at is None:
            return cap
        return max(0.0, min(cap, float(deadline_at) - time.monotonic()))

    def _health_ok(timeout: float = 2.0) -> bool:
        bounded_timeout = _remaining(timeout)
        if bounded_timeout <= 0:
            return False
        try:
            with _u.urlopen(health_url, timeout=bounded_timeout) as h:
                return h.status == 200
        except (_ue.URLError, _ue.HTTPError, OSError, TimeoutError):
            return False

    # Gia' su: nessun start necessario.
    if _health_ok():
        return True
    # Gia' tentato in questo processo: non ritentare lo spawn (fallback hard).
    if _vlm_started.get(role):
        return _health_ok()
    _vlm_started[role] = True

    helper = os.environ.get("METNOS_VLM_SERVER_SH") or str(
        Path(__file__).resolve().parents[1].parent / "scripts" / "vlm_server.sh")
    if not os.path.exists(helper):
        return False
    import subprocess
    start_timeout = _remaining(45)
    if start_timeout <= 0:
        return False
    try:
        r = subprocess.run([helper, "start", "--auto-stop-idle", "600"],
                           timeout=start_timeout,
                           capture_output=True, text=True)
        if r.returncode != 0:
            return False
    except (subprocess.TimeoutExpired, OSError):
        return False
    wait_deadline = time.monotonic() + max(0.0, float(wait_s))
    if deadline_at is not None:
        wait_deadline = min(wait_deadline, float(deadline_at))
    while time.monotonic() < wait_deadline:
        if _health_ok():
            return True
        sleep_s = min(1.0, wait_deadline - time.monotonic())
        if sleep_s > 0:
            time.sleep(sleep_s)
    return False
