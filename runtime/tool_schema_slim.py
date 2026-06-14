"""tool_schema_slim — riduzione deterministica della description e dello
schema args dei tool esposti al PLANNER LLM (Gemma 4 26B).

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
  1. Estrae la prima frase (terminator `. ` / `! ` / `? ` / newline).
  2. Cerca marker boundary §2.5 ("USO CORRETTO:", "NON CONFONDERE",
     "DEVI:", "NON DEVI:", "OK:", "ERRORE:") e li concatena dopo la
     prima frase.
  3. Hard cap 220 chars complessivi (con ellipsis se troncato).

Algoritmo args (per ogni property):
  - `required`: keep desc, cap 100 chars.
  - opzionale con `default` esplicito: rimuove desc (default e' gia'
    visibile + nome autoesplicativo).
  - opzionale senza default: cap 80 chars.
  - tutti: rimuove campo `default` esposto nello schema (gia' visibile
    al planner via runtime injection di get_inputs/intent extractor).

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
_ARG_DESC_CAP_REQUIRED = 100
_ARG_DESC_CAP_OPTIONAL = 80


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


def _is_self_descriptive(name: str) -> bool:
    """Nome arg che il planner capisce senza description."""
    if not name:
        return False
    lname = name.lower()
    # Pattern comuni: recursive, include_dirs, case_sensitive, max_results
    if lname.startswith(("max_", "min_", "include_", "exclude_", "is_", "has_")):
        return True
    if lname in {"recursive", "verbose", "dry_run", "force", "overwrite",
                 "sort", "limit", "offset", "page", "size", "count",
                 "top_k", "threshold", "ids", "names"}:
        return True
    return False


def slim_args_schema(schema: dict) -> dict:
    """Comprimi schema args mantenendo la struttura ma riducendo descrizioni."""
    if not isinstance(schema, dict):
        return schema
    out = dict(schema)
    props = dict(out.get("properties") or {})
    required = set(out.get("required") or [])
    new_props: dict = {}
    for name, spec in props.items():
        if not isinstance(spec, dict):
            new_props[name] = spec
            continue
        s = dict(spec)
        desc = s.get("description", "")
        if isinstance(desc, dict):
            # Multilang dict (raro nello schema rendering — di solito gia' risolto)
            desc = desc.get("it") or desc.get("en") or ""
        if desc:
            sentences = _split_sentences(desc)
            first = sentences[0] if sentences else ""
            cap = _ARG_DESC_CAP_REQUIRED if name in required else _ARG_DESC_CAP_OPTIONAL
            has_default = "default" in s
            if name not in required and has_default and _is_self_descriptive(name):
                s.pop("description", None)
            else:
                slim = first if len(first) <= cap else first[: cap - 1].rstrip() + "…"
                s["description"] = slim
        new_props[name] = s
    out["properties"] = new_props
    return out


def slim_tool(tool: dict) -> dict:
    """Applica slim a un tool dict completo (formato Ollama/OpenAI)."""
    if not isinstance(tool, dict):
        return tool
    fn = tool.get("function") or {}
    if not fn:
        return tool
    new_fn = dict(fn)
    new_fn["description"] = slim_description(fn.get("description", ""))
    new_fn["parameters"] = slim_args_schema(fn.get("parameters") or {})
    out = dict(tool)
    out["function"] = new_fn
    return out


def is_slim_enabled() -> bool:
    """Default ON. Disable via METNOS_TOOL_SCHEMA_FULL=1."""
    return os.environ.get("METNOS_TOOL_SCHEMA_FULL", "0") != "1"
