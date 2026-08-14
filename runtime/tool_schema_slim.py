"""tool_schema_slim — riduzione deterministica della description e dello
schema args dei tool esposti al PLANNER LLM (modello locale).

Razionale (sessione 19/5/2026 sera, continuum 19/5 §H0):
  Il giant prompt del PLANNER (15-25k tok input) e' dominato dalle
  description tool (56k chars su 58 executor) + dagli args_schema (76k
  chars). Il modello sceglie il tool e riempie gli args; non gli serve
  l'esempio multi-step nel doc, ne' la spiegazione truncation visibility.

  Lo slim e' applicato SOLO al rendering planner-facing
  (`agent_runtime.render_tools_for_provider`). I consumer collaterali
  (vaglio, synt cross-check, docs, http_render) continuano a vedere la
  description full (`Executor.description`).

  Reversibile via env `METNOS_TOOL_SCHEMA_FULL=1` (debug / regression).

Algoritmo description (§7.9 deterministico, no LLM):
  - la superficie principale usa `manifest_rules.render_heads_budgeted`, cioe'
    lo stesso budget aggregato del proposer;
  - `slim_description` resta disponibile per consumer storici/benchmark e usa
    prima frase + marker boundary con hard cap 220.

Algoritmo args (sull'intero pool):
  - slot base `required` 100 char, opzionale 80;
  - gli slot corti/risparmiati cedono budget alle descrizioni lunghe;
  - hard cap runtime 320 per arg, distinto dal target autore 180;
  - i boundary forti (omit/exact/wildcard/mutua esclusione) sopravvivono al
    taglio anche quando sono nella coda della description;
  - il totale non supera la somma dei vecchi slot + uno slack fisso di pool.
  - il campo `default` resta nello schema: puo' orientare il planner anche
    quando la description viene abbreviata.

Stima impatto (baseline 19/5 = 132k chars → ~33k tok):
  - desc: 56k → ~12k chars (-78%)
  - schema: 76k → ~40k chars (-47%)
  - totale: 132k → ~52k chars (-60%)
  - token planner-facing: 33k → ~13k (-60%)
"""
from __future__ import annotations

import os
import re

# Marker boundary §2.5 / §6 — semantica critica, preservata.
_BOUNDARY_MARKERS = (
    "USO CORRETTO", "NON CONFONDERE",
    "DEVI:", "NON DEVI:",
    "USE CORRECT", "DO NOT CONFUSE",
    "MUST:", "MUST NOT:",
)

_SENT_TERM_RE = re.compile(r"(?<=[\.\!\?])\s+|\n")

_DESC_HARD_CAP = 220
try:
    from manifest_rules import (
        ARG_RENDER_OPTIONAL as _ARG_DESC_CAP_OPTIONAL,
        ARG_RENDER_HARD_MAX as _ARG_DESC_HARD_CAP,
        ARG_RENDER_POOL_SLACK as _ARG_POOL_SLACK,
        ARG_RENDER_REQUIRED as _ARG_DESC_CAP_REQUIRED,
    )
except Exception:  # pragma: no cover - import standalone
    _ARG_DESC_CAP_REQUIRED = 100
    _ARG_DESC_CAP_OPTIONAL = 80
    _ARG_DESC_HARD_CAP = 320
    _ARG_POOL_SLACK = 180


def _split_sentences(text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    parts = _SENT_TERM_RE.split(text)
    return [p.strip() for p in parts if p and p.strip()]


def _extract_boundary_clauses(text: str) -> list[str]:
    """Restituisce le frasi che contengono un marker boundary §2.5."""
    out = []
    for sent in _split_sentences(text):
        upper = sent.upper()
        for marker in _BOUNDARY_MARKERS:
            if marker in upper:
                out.append(sent)
                break
    return out


def slim_description(desc: str) -> str:
    """Comprimi description del tool a (prima frase + boundary §2.5)."""
    desc = (desc or "").strip()
    if not desc:
        return ""
    sentences = _split_sentences(desc)
    if not sentences:
        return ""
    first = sentences[0]
    boundary = _extract_boundary_clauses(desc)
    # Dedup: non ripetere la prima frase se gia' boundary
    boundary = [b for b in boundary if b != first]
    parts = [first] + boundary
    out = " ".join(parts).strip()
    if len(out) > _DESC_HARD_CAP:
        out = out[: _DESC_HARD_CAP - 1].rstrip() + "…"
    return out


def _description_text(value) -> str:
    if isinstance(value, dict):
        value = value.get("it") or value.get("en") or ""
    return " ".join(str(value or "").strip().split())


def _cap_at_word(text: str, cap: int) -> str:
    if len(text) <= cap:
        return text
    head = text[:cap]
    if not text[cap].isspace() and not head[-1].isspace():
        head = head.rsplit(" ", 1)[0] if " " in head else ""
    return head.rstrip()


_ARG_STRONG_BOUNDARY_RE = re.compile(
    r"(?:NON\s+DEVI|MUST\s+NOT|MUTUAMENTE\s+ESCLUSIV\w*|"
    r"MUTUALLY\s+EXCLUSIVE|MATCH\s+ESATTO|EXACT\s+MATCH|WILDCARD|"
    r"OMETT\w*|\bOMIT\w*|SOLO\s+RUNTIME|RUNTIME\s+ONLY)",
    re.IGNORECASE,
)


def _boundary_kind(value: str) -> str:
    upper = value.upper()
    if "WILDCARD" in upper:
        return "wildcard"
    if "MATCH" in upper:
        return "exact"
    if "MUTUA" in upper:
        return "exclusive"
    if "RUNTIME" in upper:
        return "runtime"
    return "omit"


def _preserve_boundary_kinds(source: str, rendered: str, cap: int) -> str:
    """Append compact excerpts for strong constraints lost by a first cut."""
    source_matches = list(_ARG_STRONG_BOUNDARY_RE.finditer(source))
    rendered_kinds = {
        _boundary_kind(match.group(0))
        for match in _ARG_STRONG_BOUNDARY_RE.finditer(rendered)
    }
    supplements: list[str] = []
    for match in source_matches:
        kind = _boundary_kind(match.group(0))
        if kind in rendered_kinds:
            continue
        # Include a small amount of following context: for example
        # ``wildcard * / ? NON supportati`` is materially more useful than the
        # bare marker while remaining cheap.
        end = min(len(source), match.start() + 44)
        excerpt = _cap_at_word(source[match.start():end].strip(), 44)
        if excerpt and excerpt not in supplements:
            supplements.append(excerpt)
            rendered_kinds.add(kind)
    if not supplements:
        return rendered
    suffix = "; ".join(supplements)
    separator = " … "
    if len(suffix) + len(separator) >= cap:
        return _cap_at_word(suffix, cap)
    prefix = _cap_at_word(rendered, cap - len(separator) - len(suffix))
    return prefix + separator + suffix


def _cap_arg_description(text: str, cap: int) -> str:
    """Bound an arg description without dropping a strong trailing boundary.

    Prefix-only truncation hid constraints such as ``MATCH ESATTO`` and
    ``NON DEVI`` in real manifests.  Keep a compact purpose prefix plus the
    boundary tail.  The transformation only changes model-facing rendering;
    the signed manifest and executor schema remain intact.
    """
    if len(text) <= cap:
        return text
    matches = list(_ARG_STRONG_BOUNDARY_RE.finditer(text))
    if not matches or cap < 24:
        return _cap_at_word(text, cap)

    boundary_start = matches[0].start()
    intro = text[:boundary_start].strip(" ;:.-")
    boundary = text[boundary_start:].strip()
    if not intro:
        return _cap_at_word(boundary, cap)

    separator = " … "
    usable = max(1, cap - len(separator))
    # I boundary sono piu' importanti degli esempi intermedi: riservagli fino
    # al 72% dello slot, lasciando comunque un'introduzione comprensibile.
    boundary_cap = min(len(boundary), max(42, int(usable * 0.72)))
    intro_cap = usable - boundary_cap
    if intro_cap < 16:
        intro_cap = min(16, usable)
        boundary_cap = usable - intro_cap
    rendered_intro = _cap_at_word(intro, intro_cap)
    rendered_boundary = _cap_at_word(boundary, boundary_cap)
    if not rendered_intro:
        rendered = rendered_boundary
    elif not rendered_boundary:
        rendered = rendered_intro
    else:
        rendered = rendered_intro + separator + rendered_boundary
    return _preserve_boundary_kinds(text, rendered, cap)


def slim_args_schemas(schemas: list[dict], *, pool_slack: int | None = None
                      ) -> list[dict]:
    """Comprimi un pool di schemi con budget arg condiviso e bounded."""
    slack = _ARG_POOL_SLACK if pool_slack is None else max(0, pool_slack)
    rendered: list[dict] = []
    carriers: list[tuple[dict, str, str, int]] = []
    theoretical_budget = slack

    for schema in schemas:
        if not isinstance(schema, dict):
            rendered.append(schema)
            continue
        out = dict(schema)
        required = set(out.get("required") or [])
        new_props: dict = {}
        for name, spec in dict(out.get("properties") or {}).items():
            if not isinstance(spec, dict):
                new_props[name] = spec
                continue
            copied = dict(spec)
            desc = _description_text(copied.get("description"))
            slot = (_ARG_DESC_CAP_REQUIRED if name in required
                    else _ARG_DESC_CAP_OPTIONAL)
            if desc:
                theoretical_budget += slot
                carriers.append((copied, name, desc, slot))
            new_props[name] = copied
        out["properties"] = new_props
        rendered.append(out)

    caps = [min(len(desc), slot) for _spec, _name, desc, slot in carriers]
    remaining = max(0, min(
        sum(len(desc) for _spec, _name, desc, _slot in carriers),
        theoretical_budget,
    ) - sum(caps))
    active = [index for index, (_spec, _name, desc, _slot)
              in enumerate(carriers)
              if caps[index] < min(len(desc), _ARG_DESC_HARD_CAP)]
    while remaining and active:
        share = max(1, remaining // len(active))
        next_active: list[int] = []
        for position, index in enumerate(active):
            desc = carriers[index][2]
            ceiling = min(len(desc), _ARG_DESC_HARD_CAP)
            add = min(share, ceiling - caps[index], remaining)
            caps[index] += add
            remaining -= add
            if caps[index] < ceiling:
                next_active.append(index)
            if not remaining:
                next_active.extend(active[position + 1:])
                break
        active = next_active

    for (spec, _name, desc, _slot), cap in zip(carriers, caps):
        spec["description"] = _cap_arg_description(desc, cap)
    return rendered


def slim_args_schema(schema: dict) -> dict:
    """Compat: budget elastico entro un singolo schema, senza slack di pool."""
    return slim_args_schemas([schema], pool_slack=0)[0]


def is_slim_enabled() -> bool:
    """Default ON. Disable via METNOS_TOOL_SCHEMA_FULL=1."""
    return os.environ.get("METNOS_TOOL_SCHEMA_FULL", "0") != "1"
