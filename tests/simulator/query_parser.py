"""query_parser.py — LLM medium parse query NL → QueryDescriptor.

Output: input_data + target_output + constraints (struttura per graph search).

GBNF strict per output (no hallucination, no parse fail).
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

sys.path.insert(0, os.path.dirname(__file__))
from registry import Constraint
from query_parser_prompt import SYSTEM_PROMPT as _UNIVERSAL_SYSTEM_PROMPT


@dataclass
class TypedValue:
    name: str            # arg suggested name
    semantic_type: str   # vocab semantic
    value: object        # actual token from query


@dataclass
class TypedTarget:
    semantic_type: str
    shape: str           # list | scalar | dict


@dataclass
class QueryDescriptor:
    intent_verb: str
    intent_object: str
    inputs: list[TypedValue] = field(default_factory=list)
    target: TypedTarget = None
    constraints: list[Constraint] = field(default_factory=list)
    confidence: float = 1.0
    raw_llm_output: str = ""
    raw_query: str = ""
    # Decomposition (Google research blog 2024): list of atomic (verb, object)
    # actions implied by the query, per multi-step ranker scoring.
    sub_actions: list[tuple] = field(default_factory=list)

    def constraint_set(self) -> set[str]:
        return {f"{c.kind}:{c.key}" for c in self.constraints}


SYSTEM_PROMPT = _UNIVERSAL_SYSTEM_PROMPT


_PARSE_CACHE_DIR = None


def _get_cache_dir():
    global _PARSE_CACHE_DIR
    if _PARSE_CACHE_DIR is None:
        from pathlib import Path
        _PARSE_CACHE_DIR = Path("/opt/metnos/tests/simulator/parse_cache")
        _PARSE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _PARSE_CACHE_DIR


def _cache_key(query: str, system_prompt: str) -> str:
    """Hash di query + prompt → cache key. Cambio prompt → cache miss."""
    import hashlib
    h = hashlib.sha256()
    h.update(system_prompt.encode("utf-8"))
    h.update(b"\x00")
    h.update(query.encode("utf-8"))
    return h.hexdigest()[:16]


def parse_query(query: str, *, llm_call=None,
                 use_cache: bool = True) -> Optional[QueryDescriptor]:
    """Parse query NL → QueryDescriptor via LLM medium.

    Cache disk-based per determinismo run-to-run: stessa (query, prompt) →
    stesso output. Invalida automaticamente su cambio prompt (key contiene
    prompt hash). Cancella manualmente parse_cache/ per forzare re-LLM.
    """
    if not query or not query.strip():
        return None
    if llm_call is None:
        llm_call = _default_llm_call
    raw = None
    cache_file = None
    if use_cache:
        key = _cache_key(query, SYSTEM_PROMPT)
        cache_file = _get_cache_dir() / f"{key}.json"
        if cache_file.exists():
            try:
                cached = json.loads(cache_file.read_text())
                raw = cached.get("raw", "")
            except Exception:
                raw = None
    if not raw:
        raw = llm_call(SYSTEM_PROMPT, query)
        if raw and cache_file is not None:
            try:
                cache_file.write_text(json.dumps({"raw": raw}, ensure_ascii=False))
            except Exception:
                pass
    if not raw:
        return None
    # Strip think blocks
    raw = re.sub(r"<think>[\s\S]*?</think>", "", raw, flags=re.IGNORECASE)
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return None
    try:
        parsed = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    parsed["__raw_query__"] = query
    return _dict_to_descriptor(parsed, raw)


# Coerce input→object solo per SUBJECT-PRIMARY types (entità centrale, non
# parametro). dir_path/person_name/file_path/time_window NON triggherano coerce.
_SUBJECT_PRIMARY_TYPES = {
    "url": "urls",
    "image_path": "images",
    "pdf_path": "files",
    "email_address": "messages",
}


def _coerce_object_from_inputs(parsed: dict) -> None:
    """Universal §7.3: input semantic_type più specifico di LLM-object refine.

    Pattern matching expert #4: LLM spesso outputa intent_object generico
    ("files", "entries") quando input ha un sotto-tipo specifico (image_path,
    pdf_path). Downcasting deterministico.
    """
    inputs = parsed.get("inputs") or []
    obj_curr = parsed.get("intent_object", "")
    # Refinement table — universal types specifici → object specifico
    INPUT_REFINE = {
        "url": "urls",
        "image_path": "images",
        "pdf_path": "files",     # pdf è file
        "email_address": "messages",
        "person_name": "persons",
    }
    # Solo refine se LLM-object è GENERICO ("files", "entries", "items").
    GENERIC_OBJ = {"files", "entries", "items"}
    primary = []
    for i in inputs:
        if not isinstance(i, dict):
            continue
        st = i.get("semantic_type", "")
        ref = INPUT_REFINE.get(st)
        if ref:
            primary.append(ref)
    # SUBJECT_PRIMARY (caso originale): un solo input specialistico → switch
    sp_matches = [_SUBJECT_PRIMARY_TYPES.get(i.get("semantic_type", ""))
                    for i in inputs if isinstance(i, dict)]
    sp_matches = [m for m in sp_matches if m]
    if len(sp_matches) == 1 and sp_matches[0] != obj_curr:
        parsed["intent_object"] = sp_matches[0]
        return
    # Refine generic objects when input has specific type
    if obj_curr in GENERIC_OBJ and primary:
        parsed["intent_object"] = primary[0]


def _dict_to_descriptor(d: dict, raw: str) -> QueryDescriptor:
    _coerce_object_from_inputs(d)
    inputs = []
    for i in (d.get("inputs") or []):
        if not isinstance(i, dict):
            continue
        inputs.append(TypedValue(
            name=i.get("name", ""),
            semantic_type=i.get("semantic_type", "free_text"),
            value=i.get("value"),
        ))
    target_raw = d.get("target") or {}
    target = TypedTarget(
        semantic_type=target_raw.get("semantic_type", ""),
        shape=target_raw.get("shape", "scalar"),
    )
    constraints = []
    for c in (d.get("constraints") or []):
        if not isinstance(c, dict):
            continue
        constraints.append(Constraint(
            kind=c.get("kind", "filter"),
            key=c.get("key", ""),
            value=c.get("value"),
        ))
    sub_actions = []
    for sa in (d.get("sub_actions") or []):
        if isinstance(sa, dict) and sa.get("verb") and sa.get("object"):
            sub_actions.append((sa["verb"], sa["object"]))
    return QueryDescriptor(
        intent_verb=d.get("intent_verb", ""),
        intent_object=d.get("intent_object", ""),
        inputs=inputs,
        target=target,
        constraints=constraints,
        confidence=float(d.get("confidence", 1.0)),
        raw_llm_output=raw[:1000],
        raw_query=d.get("__raw_query__", ""),
        sub_actions=sub_actions,
    )


_QUERY_DESCRIPTOR_SCHEMA = {
    "type": "object",
    "required": ["intent_verb", "intent_object", "target"],
    "properties": {
        "intent_verb": {
            "type": "string",
            "enum": ["read","write","move","delete","create","find","list","filter",
                     "sort","group","classify","get","set","send","describe","render",
                     "extract","compress","compute","compare","change","order","share"]
        },
        "intent_object": {
            "type": "string",
            "enum": ["files","dirs","packages","messages","events","contacts","places",
                     "processes","urls","numbers","images","signatures","texts",
                     "proposals","persons","tasks","inputs","credentials","entries"]
        },
        "inputs": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name","semantic_type","value"],
                "properties": {
                    "name": {"type": "string"},
                    "semantic_type": {
                        "type": "string",
                        "enum": ["file_path","dir_path","image_path","pdf_path",
                                  "url","email_address","phone","person_name","slug",
                                  "account_name","time_window","iso_timestamp",
                                  "glob_pattern","regex_pattern","count","scalar_metric",
                                  "size_bytes","free_text","json_object","bool","name"]
                    },
                    "value": {"type": ["string","number","boolean","null"]}
                }
            }
        },
        "target": {
            "type": "object",
            "required": ["semantic_type","shape"],
            "properties": {
                "semantic_type": {
                    "type": "string",
                    "enum": ["scalar_metric","free_text","file_entry[]","image_entry[]",
                              "message_entry[]","event_entry[]","person_entry[]",
                              "url_entry[]","dir_entry[]","process_entry[]",
                              "task_entry[]","iso_timestamp"]
                },
                "shape": {"type": "string", "enum": ["list","scalar","dict"]}
            }
        },
        "constraints": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["kind","key"],
                "properties": {
                    "kind": {"type": "string", "enum": ["filter","sort","aggregate","transform"]},
                    "key": {"type": "string"},
                    "value": {"type": ["string","number","boolean","null"]}
                }
            }
        },
        "sub_actions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["verb","object"],
                "properties": {
                    "verb": {"type": "string"},
                    "object": {"type": "string"}
                }
            }
        }
    }
}


def _default_llm_call(system: str, user: str) -> str:
    body = json.dumps({
        "model": "gemma-4-26b",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": 600,
        "temperature": 0.0,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "QueryDescriptor", "schema": _QUERY_DESCRIPTOR_SCHEMA, "strict": True}
        },
    }).encode()
    req = urllib.request.Request(
        "http://localhost:8080/v1/chat/completions",
        data=body, headers={"Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
            return data["choices"][0]["message"]["content"]
    except Exception as ex:
        return ""
