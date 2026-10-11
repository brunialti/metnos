"""shim_manifest — SoT dei moduli runtime spediti ai device + sha del bundle.

Content-addressing dello shim (fase 7, 6/7/2026): lo shim è memoizzato UNA
volta per processo sul client (`runner.rs`), quindi un fix a un modulo runtime
NON raggiungeva i device fino al restart del daemon (ha morso 2 volte il 6/7:
glob §2.4 e campi strutturati assenti sul PC). Il server ora espone lo sha
corrente del bundle: (a) nella response di `/agent/shim`; (b) nell'ENVELOPE
del poll (`shim_sha256`, fuori dal payload firmato per-invocazione: i client
0.2.14 ricostruiscono i bytes firmati da lista fissa — un campo nuovo lì
romperebbe la verifica). Il client ≥0.2.15 confronta e ri-scarica.

Lo sha è calcolato sui CONTENUTI (nome + bytes, ordinato per nome) con cache
invalidata dagli mtime/size dei file: costo per-poll lineare nei pochi moduli
esplicitamente inclusi.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

_RUNTIME_DIR = Path(__file__).resolve().parent


def shim_sources() -> dict[str, Path]:
    """Mappa nome-wire → path sorgente. Separatore chiave '/' (formato wire).
    Lista ESPLICITA (§7.2, niente autodiscovery) — vedi il razionale storico
    in agent_server.shim_bundle."""
    r = _RUNTIME_DIR
    from executor_support import DEVICE_SUPPORT_FILES
    return {name: r / relative for name, relative in DEVICE_SUPPORT_FILES.items()}


_cache_stamp: tuple | None = None
_cache_sha: str = ""


def current_sha() -> str:
    """Sha256 del bundle shim corrente. Cache con invalidazione su
    (path, mtime_ns, size) — ricalcolo solo quando un sorgente cambia.
    Fail-open: errore → "" (il client tratta assenza = nessun confronto)."""
    global _cache_stamp, _cache_sha
    try:
        srcs = shim_sources()
        stamp = tuple(
            (name, p.stat().st_mtime_ns, p.stat().st_size)
            for name, p in sorted(srcs.items()))
        if stamp == _cache_stamp and _cache_sha:
            return _cache_sha
        h = hashlib.sha256()
        for name, p in sorted(srcs.items()):
            h.update(name.encode("utf-8"))
            h.update(b"\x00")
            h.update(p.read_bytes())
            h.update(b"\x00")
        _cache_stamp, _cache_sha = stamp, h.hexdigest()
        return _cache_sha
    except Exception:
        return ""
