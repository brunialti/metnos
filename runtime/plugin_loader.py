"""runtime/plugin_loader.py — discovery dei backend plugin esterni
(ADR 0132, 14/5/2026).

Pattern: `~/.local/share/metnos/plugins/<plugin_name>/plugin.toml` +
`<object>.py` per ognuno degli OBJECTS §2.2 implementati.

API:
- `load_plugins(object_canonical) -> dict[provider_name, module]`
- `list_installed_plugins() -> list[dict]`
- `invalidate_cache()`

Trust gate: `enabled = true` nel manifest. Plugin con `enabled=false`
scartati silenziosamente. Precedenza builtin > plugin (caller decide
via `_HANDLERS.update(load_plugins(...), conflict='skip')`).

Determinismo §7.9: nessun LLM, nessun network. Solo filesystem scan +
TOML parse + dynamic import.
"""
from __future__ import annotations

import importlib.util
import logging
import sys
import tomllib
from pathlib import Path
from types import ModuleType

log = logging.getLogger(__name__)

PLUGINS_ROOT = Path.home() / ".local" / "share" / "metnos" / "plugins"

# Cache: (object, frozen_root) → {provider_name: module}
_CACHE: dict[tuple[str, str], dict[str, ModuleType]] = {}


def _plugins_root() -> Path:
    """Override testabile via env (ADR 0132): METNOS_PLUGINS_ROOT."""
    import os
    env = os.environ.get("METNOS_PLUGINS_ROOT")
    if env:
        return Path(env)
    return PLUGINS_ROOT


def _parse_manifest(toml_path: Path) -> dict | None:
    if not toml_path.is_file():
        return None
    try:
        with open(toml_path, "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as ex:
        log.warning("plugin manifest unreadable %s: %s", toml_path, ex)
        return None


def _validate_manifest(manifest: dict, plugin_dir: Path) -> tuple[bool, str]:
    """Valida schema minimo del manifest. Ritorna (ok, reason)."""
    if not isinstance(manifest, dict):
        return False, "manifest non e' dict"
    if not manifest.get("name"):
        return False, "manifest manca 'name'"
    if not manifest.get("provider"):
        return False, "manifest manca 'provider'"
    if not manifest.get("enabled", True):
        return False, "enabled=false (trust gate)"
    backends = manifest.get("backends") or []
    if not isinstance(backends, list) or not backends:
        return False, "manifest manca [[backends]]"
    for b in backends:
        if not isinstance(b, dict):
            return False, "[[backends]] entry non e' table"
        if not b.get("object"):
            return False, "[[backends]] entry manca 'object'"
        if not b.get("file"):
            return False, "[[backends]] entry manca 'file'"
        if not (plugin_dir / b["file"]).is_file():
            return False, f"file plugin mancante: {b['file']}"
    return True, ""


def _import_plugin_file(plugin_name: str, provider: str, file_path: Path
                         ) -> ModuleType | None:
    """Dynamic import del file Python plugin. Modulo namespaced come
    `plugin.<plugin_name>.<file_stem>` per evitare collisioni."""
    if not file_path.is_file():
        return None
    mod_name = f"plugin.{plugin_name}.{file_path.stem}"
    try:
        spec = importlib.util.spec_from_file_location(mod_name, str(file_path))
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        return mod
    except Exception as ex:
        log.warning("plugin import fallito %s/%s: %s",
                    plugin_name, file_path.name, ex)
        return None


def load_plugins(object_canonical: str) -> dict[str, ModuleType]:
    """Ritorna `{provider_name: module}` per i plugin che implementano
    `object_canonical` (es. 'events', 'messages', 'files', 'urls').
    Cached per (object, root_path).

    Manifest scartati silenziosamente se: file mancanti, enabled=false,
    parse error. Logged a WARN level.
    """
    root = _plugins_root()
    cache_key = (object_canonical, str(root))
    if cache_key in _CACHE:
        return _CACHE[cache_key]
    out: dict[str, ModuleType] = {}
    if not root.is_dir():
        _CACHE[cache_key] = out
        return out

    for plugin_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        manifest = _parse_manifest(plugin_dir / "plugin.toml")
        if manifest is None:
            continue
        ok, reason = _validate_manifest(manifest, plugin_dir)
        if not ok:
            log.info("plugin %s skip: %s", plugin_dir.name, reason)
            continue
        for b in (manifest.get("backends") or []):
            if b.get("object") != object_canonical:
                continue
            provider = manifest["provider"]
            if provider in out:
                log.warning("plugin %s: provider %r duplicato per object %r",
                            plugin_dir.name, provider, object_canonical)
                continue
            mod = _import_plugin_file(plugin_dir.name, provider,
                                       plugin_dir / b["file"])
            if mod is not None:
                out[provider] = mod

    _CACHE[cache_key] = out
    return out


def list_installed_plugins() -> list[dict]:
    """Inventario diagnostico: lista plugin scoperti + stato. Usato
    da CLI (`metnos-cli plugins list`) e admin dashboard."""
    root = _plugins_root()
    out: list[dict] = []
    if not root.is_dir():
        return out
    for plugin_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        manifest = _parse_manifest(plugin_dir / "plugin.toml")
        if manifest is None:
            out.append({"name": plugin_dir.name, "ok": False,
                         "reason": "no plugin.toml"})
            continue
        ok, reason = _validate_manifest(manifest, plugin_dir)
        out.append({
            "name":     plugin_dir.name,
            "ok":       ok,
            "reason":   reason if not ok else "",
            "provider": manifest.get("provider", ""),
            "version":  manifest.get("version", ""),
            "enabled":  manifest.get("enabled", True),
            "backends": [
                (b.get("object"), b.get("file"))
                for b in (manifest.get("backends") or [])
            ],
        })
    return out


def invalidate_cache() -> None:
    """Forza re-scan al prossimo `load_plugins(...)`. Usato dai test."""
    _CACHE.clear()


# Helper per i dispatcher canonical (opzionale, da chiamare in
# `backends/<object>/__init__.py` quando un plugin loader sara' wirato
# attivamente). Per ora gli executor canonical NON usano questo helper:
# l'attivazione e' condizionata al primo plugin reale + ADR pending
# per il sandboxing.

def merge_with_builtins(builtins: dict[str, ModuleType],
                         object_canonical: str,
                         *, conflict: str = "skip"
                         ) -> dict[str, ModuleType]:
    """Merge `builtins` con i plugin di `object_canonical`. Builtin
    hanno precedenza (Layer 3 di ADR 0132): `conflict="skip"` non
    sovrascrive; `conflict="raise"` solleva su collisione."""
    plugins = load_plugins(object_canonical)
    merged = dict(builtins)
    for name, mod in plugins.items():
        if name in merged:
            if conflict == "raise":
                raise ValueError(
                    f"plugin provider {name!r} collides with builtin "
                    f"for object {object_canonical!r}"
                )
            continue
        merged[name] = mod
    return merged
