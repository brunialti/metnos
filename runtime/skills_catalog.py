# SPDX-License-Identifier: AGPL-3.0-only
"""runtime/skills_catalog — tassonomia delle SKILL first-party (asse 2 rilascio pubblico).

NUOVA visione skill (vedi [[project-public-release-initiative]]): una **skill** è un
GRUPPO DI CAPACITÀ legato a un BACKEND/SERVIZIO/CREDENZIALE esterno, attivabile e
**dormant se non configurato**. Il **CORE** = tutto ciò che NON ha dipendenze
esterne (planner/engine, file locali, processi, tempo, scheduler locale, helper
scratchpad, persone/contatti locali, credenziali, firme, proposte).

Classificazione DETERMINISTICA per pattern sul nome executor (§7.3 generale: un
executor nuovo si auto-classifica; nessun elenco hard da mantenere a mano). Le
skill importate (es. github da agentskills.io) hanno la PROPRIA provenance e sono
gestite dallo skill_registry — qui si dichiarano solo i confini first-party.

`auto_enable: True` su tutte → di default l'AMBIENTE IN ESERCIZIO ha il pool
INVARIATO; il gating nasconde una skill solo se l'utente la disattiva
esplicitamente (skill_enabled.json).
"""
from __future__ import annotations

import re

# Ordine SIGNIFICATIVO: il primo match vince → i più specifici PRIMA (github
# prima di mail, perché `send_messages_github` contiene `_messages`).
FIRST_PARTY_SKILLS: list[dict] = [
    {"name": "github", "match": r"_github\b",
     "requires": "a GitHub Personal Access Token",
     "desc": "GitHub: issues, pull request, workflow.", "auto_enable": True},
    {"name": "photos", "match": r"(images_indices|persons_indices|find_images_web)",
     "requires": "image models (SigLIP/ArcFace via AI backend) + an image index",
     "desc": "Photo search & management: semantico, volti, EXIF/GPS sul tuo corpus foto.",
     "auto_enable": True},
    {"name": "mail", "match": r"_messages(\b|_)",
     "requires": "one or more IMAP/SMTP accounts",
     "desc": "Email: leggi/cerca/invia/sposta/rispondi via IMAP/SMTP.",
     "auto_enable": True},
    {"name": "web", "match": r"(find_urls|get_urls|read_urls_|login_session)",
     "requires": "a SearXNG instance (web search) + outbound HTTP",
     "desc": "Web search & lettura pagine (SearXNG + crawler).", "auto_enable": True},
    {"name": "geo", "match": r"(find_places|get_places|get_location)",
     "requires": "a Photon/Nominatim geocoder endpoint",
     "desc": "Geolocalizzazione e luoghi (Photon/Nominatim).", "auto_enable": True},
    {"name": "calendar", "match": r"(_events\b|_calendars\b)",
     "requires": "a calendar backend (Google Workspace / CalDAV / local)",
     "desc": "Calendario: eventi e calendari.", "auto_enable": True},
    {"name": "frontier", "match": r"consult_frontier",
     "requires": "a frontier LLM API key (Anthropic/OpenAI), opt-in",
     "desc": "Escalation a un LLM frontier cloud quando il locale non basta.",
     "auto_enable": True},
    # Amministrazione del sistema host: shell/sudo validati + esecuzione
    # privilegiata, SEMPRE sotto consenso esplicito (vaglio). Disattivabile per
    # togliere a Metnos qualunque accesso al sistema (guadagno di sicurezza).
    {"name": "system", "match": r"^admin$",
     "requires": "a sudoers configuration for privileged host operations "
                 "(shell, sudo, package install, network mounts)",
     "desc": "Amministrazione host: comandi shell/sudo validati, eseguiti solo "
             "dopo consenso esplicito (vaglio) per ogni azione.",
     "auto_enable": True},
]

_CORE = {
    "name": "core",
    "tier": "core",  # ADR 0170: core | first_party | imported
    "requires": "nessuna dipendenza esterna",
    "desc": "Capacità sempre disponibili: file locali, dir, processi, tempo, "
            "scheduler locale, persone/contatti, credenziali, firme, proposte, "
            "helper scratchpad (filter/sort/group/compute/describe).",
    "auto_enable": True,
}


def skill_for_executor(name: str) -> str:
    """Skill first-party di appartenenza di un executor (o 'core'). Deterministico."""
    if not isinstance(name, str):
        return "core"
    for s in FIRST_PARTY_SKILLS:
        if re.search(s["match"], name):
            return s["name"]
    return "core"


def first_party_skill_names() -> list[str]:
    return [s["name"] for s in FIRST_PARTY_SKILLS]


def skill_meta(name: str) -> dict | None:
    if name == "core":
        return dict(_CORE)
    for s in FIRST_PARTY_SKILLS:
        if s["name"] == name:
            # ADR 0170: tutte le skill di questo catalogo sono Tier 2
            # (first-party builtin). Le skill di Tier 3 (imported) sono
            # tracciate altrove (skill_registry/provenance), non qui.
            meta = {k: v for k, v in s.items() if k != "match"}
            meta.setdefault("tier", "first_party")
            return meta
    return None


def _bundle_tier(name: str) -> str | None:
    """Tier dichiarato nel SKILL.md di un bundle in-repo `executors/skills/<name>/`.

    I bundle (es. google-workspace) sono provider/capacità con script propri,
    distinti dalle skill per-capacità classificate per pattern. Un bundle
    versionato nel repo con `tier: first_party` E' first-party (ADR 0170).
    None se il bundle non esiste o non dichiara tier."""
    try:
        import config as _C  # §7.11
        skill_md = _C.PATH_EXECUTORS / "skills" / name / "SKILL.md"
        if not skill_md.is_file():
            return None
        # Scan del frontmatter (prime righe) per `tier: <valore>`.
        for line in skill_md.read_text(encoding="utf-8").splitlines()[:30]:
            s = line.strip()
            if s.startswith("tier:"):
                return s.split(":", 1)[1].strip() or None
    except Exception:
        return None
    return None


def skill_tier(name: str) -> str:
    """Tier ADR 0170 della skill: 'core' | 'first_party' | 'imported'.

    Ordine: (1) catalogo per-capacita' (core + first-party classificate);
    (2) bundle in-repo `executors/skills/<name>/SKILL.md` (provider versionati,
    es. google-workspace); (3) altrimenti 'imported' (Tier 3, sandbox/provenance)."""
    m = skill_meta(name)
    if m and m.get("tier"):
        return m["tier"]
    bt = _bundle_tier(name)
    if bt:
        return bt
    return "imported"
