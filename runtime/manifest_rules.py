"""manifest_rules.py — SoT delle regole universali manifest (il "DNA" di Metnos).

Single-source consumato da TUTTE le fasi di vita dell'executor:
  - engine/proposer._render_tool_pool  → budget RENDER (cosa vede l'LLM che sceglie)
  - manifest_lint                      → cap FISICI (testa / description / arg)
  - synt stage-4 + importer            → i numeri iniettati nel prompt di generazione

PRINCIPIO (§2.5, 7/6/2026): la `description` e' SOLO la testa §2.5
(SCOPO/PATTERN/NON/OUT) — l'unico testo che la macchina legge. NESSUNA coda di
prosa implementativa: comportamento -> codice (.py), uso arg -> [args].description,
razionale -> ADR (§9.1 codice=verita'). Cap stretti, niente debordo.

Tutte le dimensioni sono PARAMETRIZZABILI via env (upside futuro) con default
stretti. Cambiare un default = rilanciare `bench/routing_subset_bench.py` (la
modifica del budget tocca TUTTI i manifest → rischio regressione di massa).
"""
from __future__ import annotations
import os
import re


def _env_int(name: str, default: int) -> int:
    try:
        v = os.environ.get(name)
        return int(v) if v else default
    except ValueError:
        return default


# ── Budget RENDER (cosa il proposer mostra all'LLM) ──────────────────────────
# Manifest a capitoli (§2.5): testa fino a "OUT:" (escluso), cap RENDER_BUDGET.
RENDER_BUDGET = _env_int("METNOS_MANIFEST_RENDER_BUDGET", 260)
# Manifest legacy (senza capitoli, in attesa di bonifica): prima frase robusta,
# cap RENDER_LEGACY_MAX. Alzato dal vecchio 120 fragile a un valore accettabile.
# NB: allargare troppo DISTRAE l'LLM su description verbose (test 7/6: 260 ->
# get_urls invece di read_urls_html). Tenere moderato finche' i manifest non
# sono bonificati a sola-testa.
RENDER_LEGACY_MAX = _env_int("METNOS_MANIFEST_LEGACY_MAX", 180)

# ── Cap FISICI (validati dal linter) ─────────────────────────────────────────
HEAD_MAX = _env_int("METNOS_MANIFEST_HEAD_MAX", 240)      # testa: inizio -> OUT: (escluso)
DESC_MAX = _env_int("METNOS_MANIFEST_DESC_MAX", 280)      # description intera (testa + OUT:)
ARG_DESC_MAX = _env_int("METNOS_MANIFEST_ARG_DESC_MAX", 160)  # ogni [args.<arg>].description

# ── Capitoli §2.5 ────────────────────────────────────────────────────────────
CHAPTERS = ("SCOPO:", "PATTERN:", "NON:", "OUT:")


def _first_sentence(desc: str) -> str:
    """Prima frase robusta: spezza solo a '. ' (punto + spazio) o a fine stringa.
    NON spezza ad acronimi/estensioni con punto interno (es. '.html', '.py')."""
    m = re.search(r"\.\s", desc)
    return desc[: m.start()] if m else desc


def render_head(desc: str) -> str:
    """Testa renderizzata per il proposer (SoT del troncamento, usata anche dal
    linter per coerenza). Manifest a capitoli: fino a 'OUT:' cap RENDER_BUDGET.
    Legacy: prima frase robusta cap RENDER_LEGACY_MAX."""
    desc = (desc or "").strip().replace("\n", " ")
    if "PATTERN:" in desc:
        cut = desc.find("OUT:")
        return (desc[:cut] if cut > 0 else desc)[:RENDER_BUDGET].strip()
    return _first_sentence(desc)[:RENDER_LEGACY_MAX].strip()
