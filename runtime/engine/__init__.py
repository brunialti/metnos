"""runtime/engine — sistema engine v2 pluggable (ADR 0164, 26/5/2026).

Architettura a 4 layer (vedi engine/ARCHITECTURE.md):

  Layer 0 — fastpath        (auto-prodotto su turno-successo, hash + cosine BGE-M3)
  Layer 1 — autopath        (auto-promote da feedback ✓)
  Layer 2 — validator       (typecheck framework, opt-in)
  Layer 3 — engine          (proposer + recovery + terminator pluggable)
  Layer 3 shared — executor (deterministic, ${stepN.field}, from_step, fillers)

Selettore engine via env METNOS_ENGINE=simple|metis|frontier (default simple).

I 4 layer sono sequenziali: ogni request prova prima fastpath, poi autopath,
poi engine. Validator (se attivo) interviene fra propose ed execute.
Executor è SHARED — proposer diversi producono Framework JSON, l'executor
è uno solo.

§7.3: aggiungere nuovo proposer/recovery/terminator non richiede tocchi
agli altri layer. Interfaccia stabile via Protocol typing.
"""
from __future__ import annotations

import os
import logging

log = logging.getLogger(__name__)


def get_engine_name() -> str:
    """Ritorna nome engine attivo: simple | metis | frontier | v3."""
    name = (os.environ.get("METNOS_ENGINE") or "simple").lower()
    if name not in ("simple", "metis", "frontier", "v3"):
        log.warning("METNOS_ENGINE=%r ignoto, fallback simple", name)
        return "simple"
    return name


def is_v3() -> bool:
    """True se l'engine attivo e' la variante v3 (redesign proposer compound:
    provider-gating + scheletro vincolante + args tipizzati, ADR-pending).

    v3 e' un DROP-IN swappable con metis (v2): METNOS_ENGINE=v3 ↔ =metis e'
    un rollback istantaneo (la classe v2 resta intatta, §7.1). Consultato dai
    guard deterministici di dispatch che attivano un comportamento AGGIUNTIVO
    solo in v3 (es. reorder degli step su intent.actions) — in metis i guard
    restano byte-identici. Engine v3 = MetisV3Proposer (vedi proposer_v3)."""
    return get_engine_name() == "v3"


def is_fastpath_enabled() -> bool:
    return os.environ.get("METNOS_FASTPATH", "1") != "0"


def is_autopath_enabled() -> bool:
    return os.environ.get("METNOS_AUTOPATH", "1") != "0"


def is_validator_enabled() -> bool:
    # v2: default ON. Validator pre-execute catch typo args/tool unknown
    # → evita LLM call recovery spreco.
    return os.environ.get("METNOS_VALIDATOR", "1") == "1"


def is_output_policy_enabled() -> bool:
    # Output-policy deterministica (matrice intent×data_kind → modo,
    # output_policy.normalize_terminal). Default OFF: si abilita con
    # METNOS_OUTPUT_POLICY=1 dopo validazione live.
    return os.environ.get("METNOS_OUTPUT_POLICY", "0") == "1"


# Public API (caricata lazy per ogni layer)
__all__ = [
    "get_engine_name",
    "is_v3",
    "is_fastpath_enabled",
    "is_autopath_enabled",
    "is_validator_enabled",
    "is_output_policy_enabled",
]
