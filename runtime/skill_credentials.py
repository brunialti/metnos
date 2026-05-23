"""skill_credentials.py — detection dormancy degli executor importati.

ADR (15/5/2026, decisione A+A): gli executor importati via `metnos-skills
import` (ADR 0123) dichiarano dipendenza da credenziali esterne (OAuth
token, API key, ecc.). Quando le credenziali NON sono presenti/valide,
l'executor resta nel catalogo (visibile a `metnos-skills list` per
introspezione) MA viene marcato `dormant=True`: il prefilter lo filtra
dal pool top-K cosi' il PLANNER non lo vede tra le scelte.

Detection deterministica §7.9: niente LLM, niente network call. Solo
filesystem check su path canonici.

Mapping skill_name → check function:
  - `google-workspace`: file `<skill_root>/google_token.json` esistente +
    contenente almeno `refresh_token` (il refresh handler li riusa).

Plugin esterni futuri (ADR 0132): possono registrare il loro check
estendendo `_CHECKS` via entry-point. Default: skill SCONOSCIUTA → non
dormant (degrade graceful, non castrare executor di skill custom).
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Callable


_LOG = logging.getLogger(__name__)


import config as _C  # §7.11 — rispetta METNOS_USER_DATA
_SKILLS_ROOT = _C.PATH_USER_DATA / "skills"


def _check_google_workspace() -> tuple[bool, str]:
    """OAuth token presente + refresh_token valorizzato."""
    p = _SKILLS_ROOT / "google-workspace" / "google_token.json"
    if not p.is_file():
        return False, "google_token.json missing — run OAuth flow"
    try:
        data = json.loads(p.read_text())
    except Exception as e:
        return False, f"google_token.json invalid: {e!r}"
    if not data.get("refresh_token"):
        return False, "google_token.json missing refresh_token"
    return True, ""


def _check_github_pat() -> tuple[bool, str]:
    """GitHub PAT present:
    1) env METNOS_GITHUB_TOKEN (priorita') — copre runtime ad-hoc / test
    2) credentials store domain=github (ADR 0131)

    Validazione HTTP attiva (revoke/scope) lazy: troppo costosa al boot.
    Restituiamo ok=True se uno dei due e' presente; l'executor scopre
    l'auth_required al primo invoke se il PAT e' invalido (e ritorna
    `decision="needs_inputs"` al PLANNER).
    """
    import os
    if os.environ.get("METNOS_GITHUB_TOKEN", "").strip():
        return True, ""
    try:
        import sys as _sys
        from pathlib import Path as _Path
        runtime_dir = _Path(__file__).resolve().parent
        if str(runtime_dir) not in _sys.path:
            _sys.path.insert(0, str(runtime_dir))
        import credentials as _cred  # type: ignore
        if _cred._file_for("github").exists():
            return True, ""
    except Exception as e:
        _LOG.warning("github credentials probe failed: %r", e)
    return False, ("PAT mancante — esegui `metnos-cli credentials add github` "
                   "o esporta METNOS_GITHUB_TOKEN")


# Skill → (ok, reason). Estensibile da plugin.
_CHECKS: dict[str, Callable[[], tuple[bool, str]]] = {
    "google-workspace": _check_google_workspace,
    "github": _check_github_pat,
}


def is_credentials_available(skill_name: str) -> tuple[bool, str]:
    """Ritorna (ok, reason). ok=True se la skill SCONOSCIUTA (graceful
    default) o le credenziali sono presenti. reason e' user-facing per
    logging/admin (vuoto se ok)."""
    fn = _CHECKS.get(skill_name)
    if fn is None:
        return True, ""
    try:
        return fn()
    except Exception as e:
        _LOG.warning("skill_credentials check for %s failed: %r", skill_name, e)
        return True, ""  # fail-open: non castrare per errore di check


def parse_skill_from_provenance(provenance: dict[str, Any] | None) -> str | None:
    """Estrae lo skill_name da `[provenance].imported_from` del manifest.
    Formato canonico (ADR 0123): "<registry>/<scope>/<skill_name>"
    (es. "agentskills.io/local/google-workspace"). Ritorna None se non
    matcha il pattern.
    """
    if not isinstance(provenance, dict):
        return None
    src = provenance.get("imported_from")
    if not isinstance(src, str) or not src.strip():
        return None
    parts = src.strip().split("/")
    if len(parts) < 3:
        return None
    return parts[-1] or None


def compute_dormancy(provenance: dict[str, Any] | None
                       ) -> tuple[bool, str]:
    """Ritorna (dormant, reason). Dormant=True se l'executor e' importato
    da una skill nota e le credenziali sono assenti/invalide."""
    skill = parse_skill_from_provenance(provenance)
    if skill is None:
        return False, ""
    ok, reason = is_credentials_available(skill)
    return (not ok), (reason if not ok else "")


def reset_cache() -> None:
    """Placeholder per cache future. Oggi i check sono cheap (stat+JSON
    parse), non cached. Esiste per simmetria con `loader.invalidate_catalog_cache`
    quando si vorra' cache-are i check ad N secondi."""
    return
