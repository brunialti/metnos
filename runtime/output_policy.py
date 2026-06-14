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


# ── normalize_terminal: il runtime sceglie il TERMINALE, non il proposer ─────
# Matrice §5.5: describe_entries/header sono dettagli implementativi del modo
# scelto deterministicamente. Gated a monte da METNOS_OUTPUT_POLICY=1
# (engine.is_output_policy_enabled, default OFF).

# Helper in-memory su `entries`: NON cambiano il data_kind del producer.
_ENTRIES_HELPERS = frozenset({
    "describe_entries", "classify_entries", "filter_entries", "sort_entries",
    "group_entries", "compute_entries", "compare_entries", "extract_entries",
})

# Step che SCARICANO contenuto web (body_text): se già presenti a valle del
# producer, l'inserzione read_urls_html (matrice §5.4) è ridondante.
_WEB_CONTENT_READERS = frozenset({"read_urls_html", "read_urls_pdf", "get_urls"})

# Riferimenti fra step: ${stepN.x} e ${steps.N.x} (entrambe le forme
# supportate da engine/executor._STEPREF_RE) + from_step:int (gestito a parte).
_REF_PATTERNS = (
    re.compile(r"(\$\{step)(\d+)(\.)"),
    re.compile(r"(\$\{steps\.)(\d+)(\.)"),
)


def _refs_in(value) -> set:
    """Posizioni di step (1-based) referenziate da un valore args/template."""
    refs: set = set()

    def walk(v):
        if isinstance(v, str):
            for pat in _REF_PATTERNS:
                for m in pat.finditer(v):
                    refs.add(int(m.group(2)))
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, dict):
            fs = v.get("from_step")
            if isinstance(fs, int):
                refs.add(fs)
            for x in v.values():
                walk(x)

    walk(value)
    return refs


def _remap_value(value, mapping: dict):
    """Rinumera i riferimenti ${stepN.x}/${steps.N.x}/from_step secondo
    mapping {old_pos: new_pos}. Posizioni assenti dal mapping = invariate."""
    if isinstance(value, str):
        for pat in _REF_PATTERNS:
            value = pat.sub(
                lambda m: m.group(1)
                + str(mapping.get(int(m.group(2)), int(m.group(2))))
                + m.group(3),
                value)
        return value
    if isinstance(value, list):
        return [_remap_value(x, mapping) for x in value]
    if isinstance(value, dict):
        out = {k: _remap_value(x, mapping) for k, x in value.items()}
        fs = out.get("from_step")
        if isinstance(fs, int):
            out["from_step"] = mapping.get(fs, fs)
        return out
    return value


def _producer_pos(steps) -> int:
    """Posizione 1-based dell'ULTIMO producer (non helper, non final_answer);
    0 se assente. Matrice §6: presentazione = data_kind dell'ultimo producer."""
    pos = 0
    for i, s in enumerate(steps):
        t = getattr(s, "tool", "") or ""
        if t and t != "final_answer" and t not in _ENTRIES_HELPERS:
            pos = i + 1
    return pos


def normalize_terminal(framework, intent, query: str = ""):
    """Riscrive il TERMINALE di presentazione del framework secondo la matrice
    deterministica (resolve). Puro §7.9: zero LLM, zero I/O di stato; l'input
    NON è mutato. Ritorna (framework, info) — framework nuovo solo se cambia.

    - mode G (gallery) / S (scalar): droppa i describe_entries post-producer
      (gallery = attachments, count = numero; la prosa LLM è rumore) e fissa
      final_message deterministico (header con @shown / Totale con @count).
    - mode T con producer find_urls (web READ, matrice §5.4): inserisce
      read_urls_html(from_step=producer) prima della sintesi — le entries di
      find_urls sono metadata-only (url/title/snippet), describe fallirebbe
      con needs_content_fetch (round-trip recovery evitato).
    - altri modi: invariati (per ora).
    """
    info = {"mode": "", "data_kind": "", "intent_class": "", "action": "noop"}
    steps = list(getattr(framework, "steps", None) or [])
    if not steps:
        return framework, info
    ppos = _producer_pos(steps)
    if not ppos:
        return framework, info
    producer = steps[ppos - 1].tool
    verb = getattr(intent, "verb", None)
    if verb is None:
        verb = intent if isinstance(intent, str) else ""
    r = resolve(verb, producer, query)
    info.update(r)

    from engine.types import StepSpec, Framework  # lazy: evita import circolari

    mode = r["mode"]
    if mode in (G, S):
        # Drop describe_entries A VALLE del producer terminale.
        drop = {i + 1 for i, s in enumerate(steps)
                if i + 1 > ppos and s.tool == "describe_entries"}
        # Guard §2.8: se uno step SUPERSTITE (non final_answer) referenzia uno
        # step droppato, non si droppa nulla (il rewiring sarebbe lossy).
        if drop:
            for i, s in enumerate(steps):
                if i + 1 in drop or s.tool == "final_answer":
                    continue
                if _refs_in(s.args) & drop:
                    drop = set()
                    break
        mapping, new_pos = {}, 0
        for i in range(1, len(steps) + 1):
            if i in drop:
                continue
            new_pos += 1
            mapping[i] = new_pos
        new_steps = [
            StepSpec(tool=s.tool, args=_remap_value(dict(s.args or {}), mapping),
                     if_prev_entries_nonempty=s.if_prev_entries_nonempty)
            for i, s in enumerate(steps) if i + 1 not in drop
        ]
        # final_message deterministico: header gallery (count=@shown, gli
        # elementi MOSTRATI) oppure totale scalare (@count, cascata
        # available_total→ok_count→used→len). i18n DB (§11 messages).
        from messages import get as _msg
        k = mapping[ppos]
        if mode == G:
            final = _msg("MSG_GALLERY_HEADER", count=f"${{step{k}.@shown}}")
        else:
            final = _msg("MSG_COUNT_TOTAL", count=f"${{step{k}.@count}}")
        info["action"] = ("drop_describe+final" if drop else "final_only")
        return (Framework(steps=new_steps, fillers=framework.fillers,
                          final_message=final), info)

    if mode == T and producer == "find_urls":
        # Già presente un reader di contenuto a valle? Allora niente insert.
        if any(s.tool in _WEB_CONTENT_READERS for s in steps[ppos:]):
            return framework, info
        # Shift +1 per tutte le posizioni dopo il producer.
        mapping = {i: (i if i <= ppos else i + 1)
                   for i in range(1, len(steps) + 1)}
        read_pos = ppos + 1
        new_steps = []
        for i, s in enumerate(steps):
            args = _remap_value(dict(s.args or {}), mapping)
            # Ogni consumer di entries a valle che pescava dal producer va
            # ricablato sullo step di lettura (body_text, non snippet).
            if (i + 1 > ppos and s.tool in _ENTRIES_HELPERS
                    and args.get("from_step") == ppos):
                args["from_step"] = read_pos
            new_steps.append(StepSpec(
                tool=s.tool, args=args,
                if_prev_entries_nonempty=s.if_prev_entries_nonempty))
        new_steps.insert(ppos, StepSpec(tool="read_urls_html",
                                        args={"from_step": ppos}))
        info["action"] = "insert_read_urls_html"
        return (Framework(steps=new_steps, fillers=framework.fillers,
                          final_message=_remap_value(
                              framework.final_message or "", mapping)), info)

    return framework, info
