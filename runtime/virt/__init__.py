"""virt — casa UNICA, segregata e minimale della virtualizzazione modelli.

Tre facciate config-driven, stile `llm_router` (factory, NIENTE registry/DI):

    from virt import get_embedder, get_llm, get_vlm
    get_embedder("text").embed_texts([...])   # BGE-M3 (o SigLIP "image", o http)
    get_llm("fast", level="procedural").chat(system, user).text
    get_vlm()                                  # spec config del VLM :8081

LLM e VLM si configurano dalla pagina Modelli; il backend di embedding non si
cambia dalla UI: richiede una migrazione verificata e la ricostruzione degli
indici. I default uguagliano la realtà attuale.
"""
from __future__ import annotations

from . import tiers
from .local_models import (
    DEFAULT_EMBEDDERS, local_embedding_spec, projected_embedding_spec,
)
from .interfaces import (  # noqa: F401
    EmbeddingProvider, LLMProvider,
    EmbeddingUnavailableError, VLMUnavailableError, VirtError,
)

__all__ = [
    "get_embedder", "get_local_embedder", "get_llm", "get_vlm", "ensure_vlm_up",
    "EmbeddingProvider", "LLMProvider",
    "EmbeddingUnavailableError", "VLMUnavailableError", "VirtError",
]

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
    s = (projected_embedding_spec(role)
         or tiers.spec("embedding", role, DEFAULT_EMBEDDERS))
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
    local computation only. Local BGE/Qwen/SigLIP options are preserved,
    including the sandbox's non-sensitive projection. A remote tier is
    deliberately ignored instead of silently enlarging network authority.
    """
    ck = ("emb-local", role)
    if ck in _cache:
        return _cache[ck]
    spec = local_embedding_spec(role)
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


def get_llm(role: str = "fast", *, level: str | None = None):
    """LLMProvider for a canonical tier and optional ``fast`` level.

    The closed vocabulary and concrete config-driven binding are owned by
    :mod:`llm_router`; callers normally select it through ``llm_workloads``.
    """
    from llm_router import LLMRouter
    return LLMRouter().provider(role, level=level)


def get_vlm(role: str = "default") -> dict:
    """Spec config del VLM da ``vlm_tiers.toml``.

    Oltre al binding e ai parametri del modello, la spec contiene i limiti del
    lavoro sincrono (``max_images_per_request`` e ``request_budget_s``). Il
    calcolo immagine vive nei consumatori; qui si virtualizza soltanto la
    configurazione, così modello, endpoint e limiti cambiano senza codice.
    """
    return tiers.spec("vlm", role, DEFAULT_VLM)


def ensure_vlm_up(role: str = "default", *, wait_s: float = 35,
                  deadline_at: float | None = None) -> bool:
    """Request local readiness and renew the idle-use signal within one budget.

    An instance-wide process lock serializes startup across HTTP and worker
    lanes. A launcher that lost a stopping process can be retried without
    renewing the caller's deadline or the original post-start wait. Every
    local request counts as use, including an already healthy model. No model
    inference or download is performed here.
    """
    import hashlib
    import sys
    import os
    import time
    import urllib.error as _ue
    import urllib.request as _u
    from pathlib import Path
    from urllib.parse import urlsplit, urlunsplit
    import config
    from process_lock import ProcessLock

    spec = get_vlm(role)
    endpoint = urlsplit(os.environ.get("METNOS_VLM_URL") or spec.get("endpoint")
                        or spec.get("base_url") or DEFAULT_VLM["default"]["base_url"])
    health_url = urlunsplit((endpoint.scheme, endpoint.netloc, "/health", "", ""))
    stop_at = (float(deadline_at) if deadline_at is not None
               else time.monotonic() + 45 + max(0.0, float(wait_s)))

    def _remaining(cap_s: float) -> float:
        return max(0.0, min(max(0.0, float(cap_s)), stop_at - time.monotonic()))

    def _health_ok(timeout: float = 2.0) -> bool:
        bounded_timeout = _remaining(timeout)
        if bounded_timeout <= 0:
            return False
        try:
            with _u.urlopen(health_url, timeout=bounded_timeout) as h:
                return h.status == 200
        except (_ue.URLError, _ue.HTTPError, OSError, TimeoutError):
            return False

    managed_local = (
        sys.platform.startswith("linux")
        and endpoint.hostname in {"localhost", "127.0.0.1", "::1"}
        and (spec.get("provider") or DEFAULT_VLM["default"]["provider"]) == "llamacpp"
    )
    if managed_local:
        import fcntl

        try:
            activity = config.PATH_USER_STATE / "vlm_last_use"
            activity.parent.mkdir(parents=True, exist_ok=True)
            # Share the watchdog's lock so an accepted use cannot race its
            # final idle check and TERM. Release it before health/startup.
            with (activity.parent / "vlm_lifecycle.lock").open("a") as lifecycle:
                while _remaining(1) > 0:
                    try:
                        fcntl.flock(lifecycle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        time.sleep(_remaining(0.05))
                else:
                    return False
                activity.touch(exist_ok=True)
                if not activity.is_file():
                    raise OSError("VLM activity path is not a regular file")
        except OSError as exc:
            from logging_setup import get_logger

            get_logger(__name__).warning("cannot record VLM readiness activity: %s", exc)
            return False
    if _health_ok():
        return True
    if not managed_local:
        return False
    key = hashlib.sha256(health_url.encode("utf-8")).hexdigest()
    lock = ProcessLock(config.PATH_USER_STATE / "model-start" / (key + ".lock"),
                       owner="local model startup")
    try:
        while _remaining(1) > 0:
            try:
                lock.acquire()
                break
            except RuntimeError as exc:
                if not isinstance(exc.__cause__, BlockingIOError):
                    raise
                time.sleep(_remaining(0.05))
        else:
            return False
        if _health_ok():
            return True
        helper = os.environ.get("METNOS_VLM_SERVER_SH") or str(
            Path(__file__).resolve().parents[1].parent / "scripts" / "vlm_server.sh")
        if not os.path.exists(helper):
            return False
        import subprocess
        from .startup import vlm_startup_environment

        environment = dict(vlm_startup_environment(role))
        environment["METNOS_USER_STATE"] = str(config.PATH_USER_STATE)
        environment["METNOS_VLM_PORT"] = str(
            endpoint.port or (443 if endpoint.scheme == "https" else 80))
        wait_deadline = None
        while True:
            start_timeout = _remaining(45)
            if wait_deadline is not None:
                start_timeout = min(start_timeout, max(0.0, wait_deadline - time.monotonic()))
            if start_timeout <= 0:
                return False
            environment["METNOS_VLM_START_WAIT_S"] = str(min(30.0, start_timeout))
            try:
                result = subprocess.run([helper, "start", "--auto-stop-idle", "600"],
                                        timeout=start_timeout, capture_output=True, text=True,
                                        env=environment)
            except (subprocess.TimeoutExpired, OSError):
                return False
            if result.returncode not in {0, 2}:
                return False
            if wait_deadline is None:
                wait_deadline = min(stop_at, time.monotonic() + max(0.0, float(wait_s)))
            # A live but stopping process must not consume the entire wait:
            # ask the serialized launcher again after a failed health probe.
            if _health_ok(min(2.0, max(0.0, wait_deadline - time.monotonic()))):
                return True
            remaining_wait = min(stop_at, wait_deadline) - time.monotonic()
            if remaining_wait <= 0:
                return False
            time.sleep(min(1.0, remaining_wait))
    except OSError:
        return False
    finally:
        lock.release()
