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
invalidata dagli mtime/size dei file: costo per-poll = 8 stat.
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
    return {
        "executor_helpers.py": r / "executor_helpers.py",
        "worker_policy.py": r / "worker_policy.py",
        "messages.py": r / "device_shim" / "messages.py",
        # Repertorio i18n (en+it) bundleato: il device rende i messaggi
        # user-facing (§7.13) invece del codice grezzo. Generato dal DB
        # (device_shim/gen_i18n.py), guardia-drift in test_device_shim_i18n.
        "messages_i18n.json": r / "device_shim" / "messages_i18n.json",
        "path_alias.py": r / "path_alias.py",
        "backends/__init__.py": r / "backends" / "__init__.py",
        "backends/files/__init__.py": r / "backends" / "files" / "__init__.py",
        "backends/files/local.py": r / "backends" / "files" / "local.py",
        "platform_policy.py": r / "platform_policy.py",
        "config.py": r / "config.py",
    }


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
