# SPDX-License-Identifier: AGPL-3.0-only
"""runtime_settings.py — config persistente runtime.toml.

Fase 12 19/5/2026 v5: tuning flag runtime da env-only a
`~/.config/metnos/runtime.toml`. Env override resta supportato per deploy
diversi, ma il valore di default e' persistente fra restart del daemon.

Override hierarchy (in ordine di priorita' decrescente):

    1. variabile d'ambiente `METNOS_*` (se settata, vince sempre)
    2. valore in `~/.config/metnos/runtime.toml` (se sezione+chiave presente)
    3. default hardcoded (fallback)

Schema TOML:

    [telos]
    # vedi _DEFAULTS sotto
    [feedback]
    error_demote_threshold = 3            # METNOS_FEEDBACK_DEMOTE_THRESHOLD

Determinismo §7.9: niente LLM nella lettura. tomllib stdlib (Python 3.11+),
cache process-life, reload on file mtime change.
NB (11/6/2026): ritirate le sezioni [fast_path] (ADR 0149, matcher L1) e
[multi_tool_fast_path] (ADR 0150) — chiavi eventualmente presenti nel
runtime.toml vengono ignorate. L'env METNOS_CANONICAL_QUERY resta letto
DIRETTAMENTE da agent_runtime per il by-product `canonical_query` del
PLANNER (emissione prompt, default ON).
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any

_LOG = logging.getLogger(__name__)

try:
    import tomllib  # type: ignore[import-not-found]
except ImportError:
    tomllib = None  # type: ignore[assignment]
    _LOG.info("runtime_settings: tomllib non disponibile, solo env override")


import config as _C  # noqa: E402 — ADR 0148 rename-resilient

_TOML_PATH = _C.PATH_USER_CONFIG / "runtime.toml"


# ── Default values (override fallback hierarchy) ─────────────────────────────

_DEFAULTS: dict[str, Any] = {
    # Telos pipeline accept→synt_request (C.8 fase 2, 24/5/2026).
    # Filtri restrittivi a 3 livelli (utente puo' gestire poche proposte
    # alla volta; le filtrate riemergono nel tempo con score piu' alto).
    "telos.dashboard_min_alignment": 0.55,    # UI cutoff (era 0.30)
    "telos.dashboard_min_convergence": 2,     # almeno 2 lenti/notti
    "telos.dashboard_max_rows": 10,           # cap pagina (era 500)
    "telos.dashboard_strict_name_status": True,  # solo new_valid
    "telos.accept_hard_gate": 0.45,           # gate non bypassabile
    "telos.synth_daily_cap": 3,               # rate limit consumer
    # Feedback→demote (E12, 24/5/2026). Soglia di ✗ consecutive per uno
    # stesso tool synth (cross-query, LWW: un ✓ resetta) prima di
    # demotare l'executor a `deprecated`. Handcrafted/protected mai
    # demoted (ADR 0114 L3).
    "feedback.error_demote_threshold": 3,
}


# Mapping chiave config TOML → variabile d'ambiente.
_ENV_MAP: dict[str, str] = {
    "telos.dashboard_min_alignment": "METNOS_TELOS_DASHBOARD_MIN_ALIGNMENT",
    "telos.dashboard_min_convergence": "METNOS_TELOS_DASHBOARD_MIN_CONVERGENCE",
    "telos.dashboard_max_rows": "METNOS_TELOS_DASHBOARD_MAX_ROWS",
    "telos.dashboard_strict_name_status": "METNOS_TELOS_DASHBOARD_STRICT_NAME_STATUS",
    "telos.accept_hard_gate": "METNOS_TELOS_ACCEPT_HARD_GATE",
    "telos.synth_daily_cap": "METNOS_TELOS_SYNTH_DAILY_CAP",
    "feedback.error_demote_threshold": "METNOS_FEEDBACK_DEMOTE_THRESHOLD",
}


# ── Cache loader ────────────────────────────────────────────────────────────

_CACHE: dict[str, Any] = {}
_CACHE_MTIME: float = 0.0
_LOCK = threading.Lock()


def _load_toml() -> dict[str, Any]:
    """Carica runtime.toml flat-dictionary `<section>.<key>`. Reload on
    mtime change. Ritorna dict vuoto se file assente o tomllib non disponibile.
    """
    global _CACHE, _CACHE_MTIME
    if tomllib is None:
        return {}
    if not _TOML_PATH.is_file():
        return {}
    try:
        mtime = _TOML_PATH.stat().st_mtime
    except OSError:
        with _LOCK:
            return dict(_CACHE)
    # Tutta la lettura/scrittura della cache va sotto lock per evitare
    # race fra "thread A vede mtime stesso, thread B sta riscrivendo
    # _CACHE = {} mid-write".
    with _LOCK:
        if mtime == _CACHE_MTIME and _CACHE:
            return _CACHE
        try:
            data = tomllib.loads(_TOML_PATH.read_text(encoding="utf-8"))
        except Exception as ex:
            _LOG.warning("runtime_settings: parse %s fallito: %r",
                         _TOML_PATH, ex)
            _CACHE = {}
            _CACHE_MTIME = mtime
            return _CACHE
        flat: dict[str, Any] = {}
        for section, body in data.items():
            if isinstance(body, dict):
                for key, val in body.items():
                    flat[f"{section}.{key}"] = val
            else:
                flat[section] = body
        _CACHE = flat
        _CACHE_MTIME = mtime
        return _CACHE


# ── Public API ──────────────────────────────────────────────────────────────

def get_bool(key: str, default: bool | None = None) -> bool:
    """Boolean setting con hierarchy env > toml > default.

    Env values interpretati: "1"/"true"/"yes"/"on" (case-insensitive) → True.
    Tutto il resto → False.
    """
    env_name = _ENV_MAP.get(key)
    if env_name and env_name in os.environ:
        v = os.environ[env_name].strip().lower()
        return v in ("1", "true", "yes", "on")
    toml_data = _load_toml()
    if key in toml_data:
        v = toml_data[key]
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on")
        return bool(v)
    if default is not None:
        return default
    fallback = _DEFAULTS.get(key, False)
    return bool(fallback)


def get_int(key: str, default: int | None = None) -> int:
    """Integer setting con hierarchy env > toml > default."""
    env_name = _ENV_MAP.get(key)
    if env_name and env_name in os.environ:
        try:
            return int(os.environ[env_name])
        except (ValueError, TypeError):
            pass
    toml_data = _load_toml()
    if key in toml_data:
        v = toml_data[key]
        try:
            return int(v)
        except (ValueError, TypeError):
            pass
    if default is not None:
        return default
    fallback = _DEFAULTS.get(key, 0)
    try:
        return int(fallback)
    except (ValueError, TypeError):
        return 0


def get_float(key: str, default: float | None = None) -> float:
    """Float setting con hierarchy env > toml > default."""
    env_name = _ENV_MAP.get(key)
    if env_name and env_name in os.environ:
        try:
            return float(os.environ[env_name])
        except (ValueError, TypeError):
            pass
    toml_data = _load_toml()
    if key in toml_data:
        v = toml_data[key]
        try:
            return float(v)
        except (ValueError, TypeError):
            pass
    if default is not None:
        return default
    fallback = _DEFAULTS.get(key, 0.0)
    try:
        return float(fallback)
    except (ValueError, TypeError):
        return 0.0


# ── Typed accessors per i flag canonici (riducono boilerplate al caller) ───

def feedback_error_demote_threshold() -> int:
    return get_int("feedback.error_demote_threshold")


# ── Bootstrap helper: scrivi runtime.toml di default se assente ────────────

_DEFAULT_TOML_BODY = """\
# Metnos runtime configuration
# Generato automaticamente al primo boot. Modifica con cura.
# Override via variabile d'ambiente METNOS_* ha priorita' superiore.

[feedback]
# E12 (24/5/2026): demote di executor synth dopo N feedback ✗ consecutive
# (cross-query, LWW: un ✓ sullo stesso tool resetta il counter). Si applica
# SOLO a synth (ADR 0114 L3): handcrafted e PROTECTED_NAMES restano sempre
# attivi. La demotion setta `deprecated_at` in executor_stats e nasconde
# l'executor dal pool catalog al boot successivo.
error_demote_threshold = 3
"""


def ensure_default_config() -> bool:
    """Crea `~/.config/metnos/runtime.toml` con i default se assente.

    Idempotente: file esistente NON viene sovrascritto.

    Returns:
      True se il file e' stato creato in questa chiamata, False altrimenti.
    """
    if _TOML_PATH.is_file():
        return False
    try:
        _TOML_PATH.parent.mkdir(parents=True, exist_ok=True)
        _TOML_PATH.write_text(_DEFAULT_TOML_BODY, encoding="utf-8")
        _LOG.info("runtime_settings: creato %s con default", _TOML_PATH)
        return True
    except OSError as ex:
        _LOG.warning("runtime_settings: write %s fallito: %r", _TOML_PATH, ex)
        return False
