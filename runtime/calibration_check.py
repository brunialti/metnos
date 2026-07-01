"""calibration_check — 3-level lookup per i threshold del planner_split
(ADR 0151, #H0e 19/5/2026 v3).

`ensure_calibration(lang)` ritorna SEMPRE un dict valido (mai None) con
la calibrazione attiva per la lingua richiesta. Cerca in ordine:

  1. **User override**: `~/.config/metnos/planner_split_calibration.json`
     (se `lang` matcha e non e' stale).
  2. **Library pre-baked**: `runtime/calibration_sets/<lang>.json`
     (committata nel repo).
  3. **Conservative default in-memory**: threshold_default=0.80, no
     per-verb override, rank_distance_min=0.15. Il caller PUO' (futuro)
     schedulare il task one-shot `calibrate_planner_split(lang=<lang>)`
     idempotency-key `calibration_<lang>` per generare la calibration
     reale in background.

Non chiama mai il LLM. Pure I/O + JSON parsing. §7.9 deterministico.

Wire-in deferred: il `chat_with_tools_split` in planner_split.py NON
consulta ancora questa funzione. Sara' wirato in #H0e fase 2 (gate
proattivo split vs monolithic basato su verb + intent.confidence +
rank_distance dal prefilter).

Usage:
    from calibration_check import ensure_calibration
    cal = ensure_calibration("it")
    # threshold per il verb corrente
    thr = cal.get("threshold_by_verb", {}).get(verb, cal["threshold_default"])
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

import config as _C  # §7.11

log = logging.getLogger(__name__)


# Conservative defaults — usati quando nessun file e' disponibile.
_DEFAULT_CONSERVATIVE: dict[str, Any] = {
    "version": "1.0",
    "lang": "_default",
    "generated_at": "",
    "metnos_commit": "",
    "model": "",
    "corpus_size": 0,
    "corpus_hash": "",
    "threshold_default": 0.80,
    "threshold_by_verb": {
        # Verbi safety-critical (mutating) → soglia massima = effetto split
        # bloccato finche' calibration reale non lo abilita esplicitamente.
        "delete": 1.0,
        "send": 1.0,
        "move": 1.0,
        "share": 1.0,
        "write": 0.95,
        "set": 0.95,
        "create": 0.90,
        "change": 0.90,
        "extract": 0.85,
        "compress": 0.85,
        "render": 0.85,
        "order": 0.85,
    },
    "rank_distance_min": 0.15,
    "notes": "in-memory conservative default — no calibration file disponibile",
}


# Lookup paths.
def _user_override_path() -> Path:
    return _C.PATH_USER_CONFIG / "planner_split_calibration.json"


def _library_path(lang: str) -> Path:
    return Path(__file__).resolve().parent / "calibration_sets" / f"{lang}.json"


# --- Staleness check (placeholder per ora) ------------------------------

def _is_stale(data: dict[str, Any]) -> bool:
    """True se il calibration set e' obsoleto.

    Per ora (v1 scaffold) sempre False: lo staleness check richiede
    confronto `metnos_commit` vs `git log --since=30d` e check pool tool
    delta. Sara' implementato in #H0e fase 2 (task scheduler v2
    callback `calibrate_planner_split` che si auto-schedula a 90gg).
    """
    return False


def _read_json(path: Path) -> Optional[dict[str, Any]]:
    """Legge un file JSON. Ritorna None se non esiste o malformato.
    Errori loggati a debug (non WARNING: file mancante e' normale)."""
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            log.debug("calibration file %s not a dict, ignoring", path)
            return None
        return data
    except (json.JSONDecodeError, OSError) as e:
        log.warning("calibration file %s malformed: %s", path, e)
        return None


# --- Public API ---------------------------------------------------------

def ensure_calibration(lang: str) -> dict[str, Any]:
    """Ritorna il calibration set attivo per `lang`.

    Mai None: in worst case ritorna `_DEFAULT_CONSERVATIVE` (con `lang`
    sovrascritto al valore richiesto).

    Args:
      lang: ISO 639-1 code, normalizzato a lowercase.

    Returns:
      Dict con almeno: `version`, `lang`, `threshold_default`,
      `threshold_by_verb`, `rank_distance_min`.
    """
    lang = (lang or "_default").lower().strip()

    # Step 1: user override.
    user_path = _user_override_path()
    user_data = _read_json(user_path)
    if user_data and user_data.get("lang") == lang and not _is_stale(user_data):
        log.debug("calibration: using user override %s for lang=%s", user_path, lang)
        return _validate_and_fill(user_data)

    # Step 2: library pre-baked.
    lib_path = _library_path(lang)
    lib_data = _read_json(lib_path)
    if lib_data and lib_data.get("lang") == lang:
        log.debug("calibration: using library %s for lang=%s", lib_path, lang)
        return _validate_and_fill(lib_data)

    # Step 3: conservative default. Schedulare un task one-shot per
    # generare la calibration reale (TODO #H0e fase 2).
    log.info("calibration: lang=%s no file found, using conservative defaults", lang)
    out = dict(_DEFAULT_CONSERVATIVE)
    out["lang"] = lang
    return _validate_and_fill(out)


def _validate_and_fill(data: dict[str, Any]) -> dict[str, Any]:
    """Garantisce che il dict abbia tutti i campi required. Riempie
    quelli mancanti con i conservative default. Non solleva mai."""
    out = dict(_DEFAULT_CONSERVATIVE)
    out.update(data)
    # threshold_by_verb merge: user overrides default keys ma puo'
    # ometterne. Defalcamento: prima il dict default, poi i verb dell'utente
    # lo sovrascrivono.
    base_verbs = dict(_DEFAULT_CONSERVATIVE.get("threshold_by_verb") or {})
    user_verbs = data.get("threshold_by_verb")
    if isinstance(user_verbs, dict):
        base_verbs.update(user_verbs)
        out["threshold_by_verb"] = base_verbs
    return out


def threshold_for(calibration: dict[str, Any], verb: str) -> float:
    """Lookup helper: ritorna la soglia per `verb`, o `threshold_default`."""
    if not verb:
        return float(calibration.get("threshold_default", 0.80))
    return float(
        (calibration.get("threshold_by_verb") or {})
        .get(verb, calibration.get("threshold_default", 0.80))
    )


# --- CLI dev tool -------------------------------------------------------

def _cli():
    """python3 -m runtime.calibration_check [lang...]
    Stampa la calibration risolta per ogni lingua sul stdout (JSON pretty)."""
    import sys
    langs = sys.argv[1:] or ["it", "en", "es", "_default"]
    for lang in langs:
        cal = ensure_calibration(lang)
        print(f"=== lang={lang} ===")
        print(json.dumps(cal, indent=2, ensure_ascii=False))
        print()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli())
