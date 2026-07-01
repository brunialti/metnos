# SPDX-License-Identifier: AGPL-3.0-only
"""skill_admin — builtin in-process per AMMINISTRARE le skill da CHAT (asse 2).

Espone due tool builtin al PLANNER (gemello CLI `metnos-skills`):

- `list_skills`  → inventario skill (first-party + importate + core) con stato
  abilitato/dormiente e prerequisiti. Verbo `list` ∈ SAFE_VERBS → salta il
  vaglio (read-only, basso attrito).
- `set_skills`   → abilita/disabilita una skill per nome. Verbo `set` ∉
  SAFE_VERBS → passa il vaglio NORMALE (cambio di capacità = consenso), senza
  alcun special-case (§7.3). Persiste in `skill_enabled.json`; il loader
  invalida il catalog-cache sul mtime di quel file → effetto dal turno
  SUCCESSIVO (nessun restart).

Determinismo §7.9: lettura tabellare via `skill_registry` + conteggio executor
dal catalog live; nessun LLM. Gli executor first-party si auto-classificano via
`skills_catalog.skill_for_executor` (nessun elenco hard da mantenere).
"""
from __future__ import annotations

import sys
from pathlib import Path

_THIS = Path(__file__).resolve()
sys.path.insert(0, str(_THIS.parent))


# --- Tool specs (planner-facing prompt) ----------------------------------

LIST_SKILLS_TOOL = {
    "type": "function",
    "function": {
        "name": "list_skills",
        "description": (
            "Elenca le SKILL di Metnos (gruppi di capacità attivabili: foto, "
            "mail, web, geo, calendario, github, frontier + core) con stato "
            "ATTIVA/DISATTIVA/DORMIENTE e prerequisiti. "
            "USA per: 'che skill ho', 'quali capacità sono attive', 'lista "
            "skill', 'cosa sa fare Metnos', 'quali moduli posso abilitare'. "
            "NON CONFONDERE CON: `list_tasks` (task schedulati), "
            "`get_processes` (processi sistema)."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

SET_SKILLS_TOOL = {
    "type": "function",
    "function": {
        "name": "set_skills",
        "description": (
            "Abilita o disabilita una SKILL (gruppo di capacità) per nome. "
            "USA per: 'attiva la skill foto', 'disabilita github', 'spegni il "
            "web', 'riattiva mail', 'disattiva geo'. "
            "`enabled=true` abilita, `enabled=false` disabilita. La skill "
            "'core' non è disattivabile. Effetto dal turno successivo. "
            "NON CONFONDERE CON: `set_tasks` (pausa/fire di un task schedulato)."
        ),
        "parameters": {
            "type": "object",
            "required": ["name", "enabled"],
            "properties": {
                "name": {"type": "string",
                         "description": "Nome skill: photos|mail|web|geo|calendar|github|frontier."},
                "enabled": {"type": "boolean",
                            "description": "true=abilita, false=disabilita."},
            },
        },
    },
}

BUILTIN_INPROC_SPECS = [
    {"name": "list_skills", "tool_spec": LIST_SKILLS_TOOL,
     "affinity": ["skill", "skills", "capacità", "capability", "capabilities",
                  "moduli", "modules", "elenco", "lista", "list"]},
    {"name": "set_skills", "tool_spec": SET_SKILLS_TOOL,
     "affinity": ["skill", "skills", "abilita", "disabilita", "attiva",
                  "disattiva", "enable", "disable", "spegni", "accendi"]},
]


# --- Catalog enrichment (conteggio executor + dormancy reali) -------------

def _catalog_skill_counts() -> dict[str, dict]:
    """Mappa skill→{total, dormant} dal catalog live (best-effort, cached)."""
    out: dict[str, dict] = {}
    try:
        from loader import load_catalog
        from skills_catalog import skill_for_executor
        cat = load_catalog()
        for ex in cat:
            sk = skill_for_executor(getattr(ex, "name", "") or "")
            slot = out.setdefault(sk, {"total": 0, "dormant": 0})
            slot["total"] += 1
            if getattr(ex, "dormant", False):
                slot["dormant"] += 1
    except Exception:
        pass
    return out


def _skill_entry(info, counts: dict) -> dict:
    """SkillInfo → record pipeable per describe/output_format."""
    c = counts.get(info.name, {})
    n_exec = c.get("total") or info.n_executors
    n_dormant = c.get("dormant", 0)
    if not info.enabled:
        status = "disattiva"
    elif info.lang != "any":
        status = "attiva" if status_locale_ok(info) else "dormiente-locale"
    elif n_exec and n_dormant >= n_exec:
        status = "dormiente"  # abilitata ma tutti gli executor dormienti (no creds/backend)
    else:
        status = "attiva"
    kind = ("core" if info.name == "core"
            else "first-party" if getattr(info, "is_first_party", False)
            else "importata")
    return {
        "name": info.name,
        "status": status,
        "enabled": bool(info.enabled),
        "kind": kind,
        "executors": n_exec,
        "executors_dormienti": n_dormant,
        "requires": getattr(info, "requires", "") or "",
        "trust": info.trust,
    }


def status_locale_ok(info) -> bool:
    try:
        import config as _C
        return info.lang in ("any", (_C.DEFAULT_LANG or "").lower())
    except Exception:
        return True


# --- Handlers -------------------------------------------------------------

def handle_list_skills(args: dict, *, actor: str | None = None, **_) -> dict:
    """Inventario skill (read-only). Ritorna `entries` pipeable."""
    try:
        import skill_registry as _sr
        skills = _sr.list_skills()
    except Exception as e:
        return {"ok": False, "error": f"skill_registry: {e}"}
    counts = _catalog_skill_counts()
    # Dedup per nome (un nome può comparire come bundle-dir + first-party).
    seen: set[str] = set()
    entries: list[dict] = []
    for info in skills:
        if info.name in seen:
            continue
        seen.add(info.name)
        entries.append(_skill_entry(info, counts))
    entries.sort(key=lambda r: (r["kind"] != "core", r["name"]))
    n_on = sum(1 for r in entries if r["enabled"])
    return {"ok": True, "entries": entries, "count": len(entries),
            "enabled_count": n_on}


def handle_set_skills(args: dict, *, actor: str | None = None, **_) -> dict:
    """Abilita/disabilita una skill. Persiste; effetto dal turno successivo."""
    name = (args.get("name") or "").strip()
    enabled = args.get("enabled")
    if not name:
        return {"ok": False, "error": "missing required: name"}
    if not isinstance(enabled, bool):
        # Tolleranza NL→determinismo (§2.4): stringhe true/false/on/off.
        s = str(enabled).strip().lower()
        if s in ("true", "1", "on", "yes", "si", "sì", "attiva", "abilita"):
            enabled = True
        elif s in ("false", "0", "off", "no", "disattiva", "disabilita"):
            enabled = False
        else:
            return {"ok": False, "error": "param 'enabled' deve essere booleano"}
    if name.lower() == "core":
        return {"ok": False, "error": "la skill 'core' non è disattivabile",
                "name": "core"}
    try:
        import skill_registry as _sr
        known = {s.name for s in _sr.list_skills()}
        if name not in known:
            return {"ok": False, "error": f"skill sconosciuta: {name}",
                    "name": name, "available": sorted(known)}
        _sr.set_skill_enabled(name, enabled)
    except Exception as e:
        return {"ok": False, "error": f"skill_registry: {e}", "name": name}
    return {"ok": True, "name": name, "enabled": enabled,
            "results": [{"name": name, "enabled": enabled,
                         "effective": "prossimo turno"}],
            "note": "effetto dal turno successivo (catalog ricaricato)"}
