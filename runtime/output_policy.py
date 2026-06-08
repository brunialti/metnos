# SPDX-License-Identifier: AGPL-3.0-only
"""output_policy.py — modalità di presentazione DETERMINISTICA.

La modalità di output NON è scelta dall'LLM-proposer: è una funzione pura di
  (intent_class, data_kind)
dove:
  - intent_class deriva da intent.verb + marker COUNT/VISUALIZE sulla query;
  - data_kind deriva dal NOME del producer terminale (segmento oggetto, vocab).

Sorgente di verità: internal/reports/output_presentation_matrix_2026-05-31.md
(decisioni Roberto 31/5/2026). §7.3 generale, §7.9 deterministico (zero LLM).

Modi:
  S scalar · G gallery · T text_summary · TG text+gallery · L list/table ·
  W web_results · M geo · R action_receipt · F file_delivery · D dialog
"""
from __future__ import annotations

import re

# ── Modi canonici ───────────────────────────────────────────────────────────
S, G, T, TG, L, W, M, R, F, D = (
    "scalar", "gallery", "text_summary", "text_gallery", "list",
    "web_results", "geo", "action_receipt", "file_delivery", "dialog",
)

# ── Classi di intent (output-rilevanti) ──────────────────────────────────────
COUNT, VISUALIZE, READ, ENUMERATE, TRANSFORM, MUTATE, PACKAGE = (
    "count", "visualize", "read", "enumerate", "transform", "mutate", "package",
)

# Marker deterministici (IT+EN). COUNT ha priorità su VISUALIZE su verbo.
_COUNT_MARKERS = re.compile(
    r"\b(quant[io]|quante|numero di|conta|count|how many|how much)\b", re.I)
_VISUALIZE_MARKERS = re.compile(
    r"\b(mostra|mostrami|fammi vedere|vedi|visualizz\w*|guarda|"
    r"show|show me|display|view|let me see)\b", re.I)

_READ_VERBS = frozenset({"read", "describe"})
_ENUM_VERBS = frozenset({"find", "list", "get"})
_TRANSFORM_VERBS = frozenset({"filter", "sort", "group", "classify", "compare"})
_MUTATE_VERBS = frozenset({"move", "delete", "send", "write", "create",
                            "set", "share", "change", "order"})
_PACKAGE_VERBS = frozenset({"compress", "extract"})


def intent_class(intent_verb: str, query: str = "") -> str:
    """Classe di intent deterministica. COUNT/VISUALIZE marker > verbo."""
    q = query or ""
    v = (intent_verb or "").lower().strip()
    if _COUNT_MARKERS.search(q) or v == "compute":
        return COUNT
    if _VISUALIZE_MARKERS.search(q) or v == "render":
        return VISUALIZE
    if v in _READ_VERBS:
        return READ
    if v in _ENUM_VERBS:
        return ENUMERATE
    if v in _TRANSFORM_VERBS:
        return TRANSFORM
    if v in _MUTATE_VERBS:
        return MUTATE
    if v in _PACKAGE_VERBS:
        return PACKAGE
    return ENUMERATE  # default produttore


# ── data_kind dal nome del producer ──────────────────────────────────────────
# Oggetto canonico = segmento del nome presente in vocab.OBJECTS.
def data_kind_of(executor_name: str) -> str:
    """Estrae il data_kind (oggetto canonico) dal nome `verbo_oggetto[_qual]`.

    Es: find_images_indices→images, read_messages→messages, find_urls→urls,
    get_processes→processes, find_files→files, find_places→places.
    Fallback builtin noti (get_location→places, get_now→time).
    """
    name = (executor_name or "").lower()
    try:
        from vocab import OBJECTS as _OBJ
    except Exception:
        _OBJ = frozenset({
            "files", "dirs", "packages", "messages", "events", "contacts",
            "places", "processes", "urls", "numbers", "images", "signatures",
            "texts", "proposals", "persons", "tasks", "inputs", "credentials",
            "entries",
        })
    for seg in name.split("_"):
        if seg in _OBJ:
            return seg
    # builtin senza oggetto canonico nel nome
    if "location" in name:
        return "places"
    if name in ("get_now",):
        return "time"
    return "entries"


# ── Tabella PRESENT[data_kind][intent_class] = modo ──────────────────────────
# Default per-data_kind nella chiave "_". Vedi matrice §3+§6.
_DEFAULT = {COUNT: S, VISUALIZE: L, READ: T, ENUMERATE: L,
            TRANSFORM: L, MUTATE: R, PACKAGE: F}

PRESENT: dict[str, dict[str, str]] = {
    "images":     {COUNT: S, VISUALIZE: G, READ: G, ENUMERATE: G, TRANSFORM: G, MUTATE: R, "_": G},
    "urls":       {COUNT: S, VISUALIZE: W, READ: T, ENUMERATE: W, TRANSFORM: W, MUTATE: R, "_": W},
    "messages":   {COUNT: S, VISUALIZE: T, READ: T, ENUMERATE: L, TRANSFORM: L, MUTATE: R, "_": L},
    "files":      {COUNT: S, VISUALIZE: L, READ: T, ENUMERATE: L, TRANSFORM: L, MUTATE: R, PACKAGE: F, "_": L},
    "dirs":       {COUNT: S, ENUMERATE: L, TRANSFORM: L, MUTATE: R, PACKAGE: F, "_": L},
    "events":     {COUNT: S, VISUALIZE: L, READ: L, ENUMERATE: L, TRANSFORM: L, MUTATE: R, "_": L},
    "persons":    {COUNT: S, VISUALIZE: G, READ: TG, ENUMERATE: L, TRANSFORM: L, MUTATE: R, "_": L},
    "contacts":   {COUNT: S, VISUALIZE: G, READ: T, ENUMERATE: L, TRANSFORM: L, MUTATE: R, "_": L},
    "places":     {COUNT: S, READ: M, ENUMERATE: M, TRANSFORM: M, MUTATE: R, "_": M},
    "processes":  {COUNT: S, READ: L, ENUMERATE: L, TRANSFORM: L, MUTATE: R, "_": L},  # L=tabella
    "texts":      {COUNT: S, READ: T, ENUMERATE: L, TRANSFORM: L, MUTATE: R, "_": T},
    "numbers":    {COUNT: S, READ: S, ENUMERATE: S, "_": S},
    "signatures": {COUNT: S, READ: T, ENUMERATE: L, MUTATE: R, "_": L},
    "packages":   {COUNT: S, READ: T, ENUMERATE: L, MUTATE: R, PACKAGE: F, "_": L},
    "proposals":  {COUNT: S, READ: T, ENUMERATE: L, MUTATE: R, "_": L},
    "tasks":      {COUNT: S, READ: T, ENUMERATE: L, MUTATE: R, "_": L},
    "credentials":{COUNT: S, READ: L, ENUMERATE: L, MUTATE: R, "_": L},
    "time":       {"_": S},
}


def presentation_mode(intent_cls: str, data_kind: str) -> str:
    """Modo di presentazione deterministico per (intent_class, data_kind)."""
    table = PRESENT.get(data_kind)
    if table is None:
        return _DEFAULT.get(intent_cls, L)
    if intent_cls in table:
        return table[intent_cls]
    return table.get("_", _DEFAULT.get(intent_cls, L))


def resolve(intent_verb: str, producer_name: str, query: str = "") -> dict:
    """Risolutore completo. Ritorna {intent_class, data_kind, mode}."""
    ic = intent_class(intent_verb, query)
    dk = data_kind_of(producer_name)
    return {"intent_class": ic, "data_kind": dk,
            "mode": presentation_mode(ic, dk)}


# ── Modi a ranking (no notify-then-ask "allargo?") ───────────────────────────
# G/W sono ricerche ranked: il top-K È la risposta, il totale è solo info.
RANKED_MODES = frozenset({G, W, TG})
