#!/usr/bin/env python3
"""skill_registry — registry runtime delle skill imported (ADR 0160).

Estende il modello skill (ADR 0123) con tre campi frontmatter opzionali
parsati da `SKILL.md`:

- `lang: <ISO 639-1>` (default `"any"`): filtro locale. Loader skip se
  `lang != "any"` e `lang != config.DEFAULT_LANG`.
- `trust: <metnos-official|community>` (default `"community"`): determina
  il regime di safety net (ADR 0159). `metnos-official` skip L6 LLM verify
  (codice trusted by Metnos team).
- `auto_enable: <bool>` (default `true`): se `false`, l'admin deve abilitarla
  esplicitamente via `metnos-skills enable <skill>`.

Lo stato enable/disable e' persistito in
`<USER_STATE>/skill_enabled.json` (mapping `{skill_name: bool}`). Skill
non presenti nel file: default = `auto_enable` dalla SKILL.md (default true).

Determinismo §7.9: pura lettura tabellare + filesystem scan.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

# Ensure runtime/ on path quando importato da subprocess / cli.
_THIS = Path(__file__).resolve()
sys.path.insert(0, str(_THIS.parent))

import config as _C  # noqa: E402
from skills_paths import iter_skill_dirs as _isd  # noqa: E402


# --- Frontmatter parser deterministico ------------------------------------
# SKILL.md ha frontmatter YAML semplice (key: value, no nested liste).
# Usiamo regex line-by-line per evitare dipendenza da PyYAML.


def _parse_skill_md(path: Path) -> dict[str, str]:
    """Parsa il frontmatter YAML di SKILL.md (campi piatti only).

    Ritorna dict {key: str_value}. Valori complessi (liste, nested) saltati
    silenziosamente — questo registry e' interessato a 3 campi piatti.
    """
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    # Split sul secondo "---".
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    block = parts[1]
    out: dict[str, str] = {}
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        # Nested/indented keys: skip (siamo solo top-level).
        if line.startswith(" ") or line.startswith("\t"):
            continue
        key, _, val = line.partition(":")
        val = val.strip().strip('"').strip("'")
        # Esclude valori liste/nested ([] {})
        if val.startswith("[") or val.startswith("{"):
            continue
        out[key.strip()] = val
    return out


@dataclass
class SkillInfo:
    name: str
    path: Path
    lang: str = "any"
    trust: str = "community"
    auto_enable: bool = True
    enabled: bool = True
    n_executors: int = 0
    is_imported: bool = True       # default: skill imported via ADR 0123
    is_builtin_repo: bool = False  # True se sotto <install>/executors/skills/
    is_first_party: bool = False   # True se skill-capacità first-party (skills_catalog, non un bundle-dir)
    requires: str = ""             # prerequisito esterno (backend/creds) per la dormancy/installer

    @property
    def is_metnos_official(self) -> bool:
        return self.trust == "metnos-official"


def _state_file() -> Path:
    return _C.PATH_USER_STATE / "skill_enabled.json"


def _load_state() -> dict[str, bool]:
    f = _state_file()
    if not f.is_file():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict[str, bool]) -> None:
    f = _state_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


# --- Public API ------------------------------------------------------------


def list_skills(lang: str | None = None) -> list[SkillInfo]:
    """Ritorna l'inventario delle skill (skills/ + legacy _imports/).

    Args:
        lang: se valorizzato, filtra le skill con `lang in {"any", lang}`.
              `None` (default) = nessun filtro.
    """
    state = _load_state()
    out: list[SkillInfo] = []
    for skill_dir in _isd():
        skill_md = skill_dir / "SKILL.md"
        fm = _parse_skill_md(skill_md)
        sk_lang = (fm.get("lang") or "any").lower()
        sk_trust = (fm.get("trust") or "community").lower()
        ae_raw = (fm.get("auto_enable") or "true").lower()
        sk_auto_enable = ae_raw in ("true", "yes", "1", "on")
        # Stato enabled: state file override > auto_enable default.
        if skill_dir.name in state:
            enabled = bool(state[skill_dir.name])
        else:
            enabled = sk_auto_enable
        # Conta executor (sub-dir con manifest.toml).
        n_exec = sum(
            1 for ex in skill_dir.iterdir()
            if ex.is_dir() and (ex / "manifest.toml").is_file()
        )
        is_builtin = str(_C.PATH_EXECUTORS) in str(skill_dir.resolve())
        info = SkillInfo(
            name=skill_dir.name,
            path=skill_dir,
            lang=sk_lang,
            trust=sk_trust,
            auto_enable=sk_auto_enable,
            enabled=enabled,
            n_executors=n_exec,
            is_imported=not is_builtin,
            is_builtin_repo=is_builtin,
        )
        if lang is not None and info.lang not in ("any", lang.lower()):
            continue
        out.append(info)
    # Skill-capacità FIRST-PARTY (asse 2): photos/mail/web/geo/calendar/github/
    # frontier + core. Non sono bundle-dir ma gruppi di executor (skills_catalog).
    # lang="any" → mai locale-gated; enabled = state override > auto_enable(True).
    try:
        from skills_catalog import FIRST_PARTY_SKILLS, _CORE
        seen = {s.name for s in out}
        for sk in list(FIRST_PARTY_SKILLS) + [_CORE]:
            nm = sk["name"]
            if nm in seen:
                continue
            ae = bool(sk.get("auto_enable", True))
            enabled = bool(state[nm]) if nm in state else ae
            out.append(SkillInfo(
                name=nm, path=None, lang="any", trust="metnos-official",
                auto_enable=ae, enabled=enabled, n_executors=0,
                is_imported=False, is_builtin_repo=True, is_first_party=True,
                requires=sk.get("requires", ""),
            ))
    except Exception as _e:  # pragma: no cover — first-party listing best-effort
        import logging as _lg
        _lg.getLogger(__name__).warning("first-party skills listing failed: %s", _e)
    out.sort(key=lambda s: s.name)
    return out


def get_skill_info(name: str) -> SkillInfo | None:
    for s in list_skills():
        if s.name == name:
            return s
    return None


def set_skill_enabled(name: str, enabled: bool) -> None:
    """Persiste lo stato enable/disable per `<name>` (LWW)."""
    state = _load_state()
    state[name] = bool(enabled)
    _save_state(state)


def is_skill_enabled(name: str) -> bool:
    """Ritorna True se la skill `<name>` e' abilitata.

    Default decisionale: legge lo state file; se mancante consulta
    `SKILL.md::auto_enable` (default True quando il campo manca).
    Locale gate (`lang`): skill non-`any` con lang != DEFAULT_LANG
    risultano DISABLED-by-locale (ritorna False).
    """
    info = get_skill_info(name)
    if info is None:
        # Skill sconosciuta: default True (loader fa comunque check su
        # `manifest.toml` presence). Niente "false-positive disable" per
        # skill non ancora installate via questo registry.
        return True
    # Locale gate.
    if info.lang != "any" and info.lang != _C.DEFAULT_LANG.lower():
        return False
    return info.enabled


def matches_locale(lang_field: str, runtime_lang: str | None = None) -> bool:
    """Helper: True se il campo `lang` di una skill matcha il runtime lang."""
    lf = (lang_field or "any").lower()
    if lf == "any":
        return True
    rt = (runtime_lang or _C.DEFAULT_LANG).lower()
    return lf == rt
