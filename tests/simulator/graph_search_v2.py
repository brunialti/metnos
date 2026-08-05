"""graph_search_v2.py — BFS con constraint propagation.

Differenze vs graph_search.py v1:
  - Usa Registry come pool
  - Tracking constraints_remaining nello state della search
  - Path valido SOLO se output type match AND tutte le constraint consumed
  - Annotated output: args binding pre-calcolato per ogni step

Output: list[AnnotatedPath] ranked.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Optional

sys.path.insert(0, os.path.dirname(__file__))
from registry import ExecutorRegistry, ExecutorTyping, Constraint
from types_semantic import is_compatible
from query_parser import QueryDescriptor

MAX_DEPTH = 5
MAX_CANDIDATES = 50
MAX_FRONTIER_SIZE = 1000   # safeguard contro esplosione BFS
MAX_TOTAL_ITERATIONS = 5000


@dataclass
class AnnotatedStep:
    tool: str
    args: dict = field(default_factory=dict)
    args_source: dict = field(default_factory=dict)
    output_type: str = ""
    consumes_constraints: list[str] = field(default_factory=list)


@dataclass
class AnnotatedPath:
    steps: list[AnnotatedStep] = field(default_factory=list)
    final_template: str = ""
    constraints_consumed: set[str] = field(default_factory=set)
    constraints_remaining: set[str] = field(default_factory=set)
    score: float = 0.0
    quality_pre: float = 0.0


# Classi semantiche di constraint keys (universal §7.3): membri della stessa
# classe sono bidirezionali equivalenti. Pattern matching expert #1.
_KEY_CLASSES = (
    {"sha256", "md5", "hash", "digest", "fingerprint", "checksum", "signature"},
    {"date", "datetime", "time", "when", "time_window", "mtime", "ctime",
     "modified", "modified_at", "iso_timestamp", "today", "tomorrow",
     "yesterday", "this_week", "next_week", "last_week"},
    {"size", "size_bytes", "bytes", "filesize", "len", "length"},
    {"name", "title", "label", "subject", "filename", "stem"},
    {"pattern", "glob_pattern", "glob", "ext", "extension", "suffix"},
    {"channel", "platform", "service"},
    {"limit", "top", "max", "count", "n"},
    {"author", "creator", "owner", "by"},
)
_KEY_CLASS_LOOKUP = {}
for _cls in _KEY_CLASSES:
    for _m in _cls:
        _KEY_CLASS_LOOKUP[_m] = _cls


def _same_class(a: str, b: str) -> bool:
    a, b = (a or "").lower(), (b or "").lower()
    if a == b:
        return True
    ca = _KEY_CLASS_LOOKUP.get(a)
    cb = _KEY_CLASS_LOOKUP.get(b)
    return ca is not None and ca is cb


# Mappa universale: constraint.key generico (LLM) → key canonico executor.
# §7.3 universal: principi generali per classe semantica, non query-specifici.
_CONSTRAINT_KEY_SYNONYMS = {
    # TEMPORAL → time_window | iso_timestamp
    "date": "time_window", "datetime": "time_window", "when": "time_window",
    "today": "time_window", "tomorrow": "time_window",
    "yesterday": "time_window", "this_week": "time_window",
    "next_week": "time_window", "last_week": "time_window",
    "time": "time_window",
    # COUNT
    "limit": "count", "top": "count", "max": "count", "n": "count",
    # PATTERN
    "pattern": "glob_pattern", "ext": "glob_pattern",
    "extension": "glob_pattern", "suffix": "glob_pattern",
    "filename": "glob_pattern",
    # NAME (subject/label/title → name)
    "title": "name", "label": "name", "subject": "name",
    # SORT key common synonyms
    "modified": "modified_at", "modified_at": "modified_at",
    "ctime": "modified_at", "mtime": "modified_at", "atime": "modified_at",
    # CHANNEL / source
    "channel": "channel", "platform": "channel", "service": "channel",
}


def _canonical_key(key: str) -> str:
    return _CONSTRAINT_KEY_SYNONYMS.get((key or "").lower(), key)


def _arg_consume_constraints(typing: ExecutorTyping,
                              args_bound: dict,
                              query: QueryDescriptor) -> list[str]:
    """Quali constraint query sono consumati da questo step (dati args bound).

    Logic:
      - typing.consumes dichiara constraint kinds che executor può consumare
      - args_bound dichiara constraint VALUES forniti come arg
      - intersezione = constraint effectively consumed
      - synonym mapping LLM→executor universale (§7.3)
    """
    consumed = []
    for c in query.constraints:
        kind_key = f"{c.kind}:{c.key}"
        canon_key = f"{c.kind}:{_canonical_key(c.key)}"
        for declared in typing.consumes:
            if declared == kind_key or declared == canon_key:
                consumed.append(kind_key); break
            if declared == f"{c.kind}:any":
                consumed.append(kind_key); break
    return consumed


def _bind_args(typing: ExecutorTyping, query: QueryDescriptor,
                upstream_path: list[AnnotatedStep]) -> tuple[dict, dict]:
    """Binding args concreti per uno step.

    Returns (args, args_source) where args_source documenta provenance.
    """
    args = {}
    args_source = {}
    # 1. Args required: bind from query.inputs by semantic_type match
    for arg_name, input_meta in typing.inputs.items():
        req_type = input_meta.semantic_type
        # Match input by type semantic
        for qi in query.inputs:
            if is_compatible(qi.semantic_type, req_type):
                args[arg_name] = qi.value
                args_source[arg_name] = f"literal_from_input:{qi.name}"
                break
        if arg_name in args:
            continue
        # Match constraint value (filter:key)
        for c in query.constraints:
            if input_meta.role == "filter":
                canon = _canonical_key(c.key)
                if c.key == arg_name or c.key in arg_name \
                        or canon == arg_name or canon in arg_name:
                    args[arg_name] = c.value
                    args_source[arg_name] = f"literal_from_constraint:{c.kind}:{c.key}"
                    break
        # Required + nessuna source → can't bind
        if arg_name in args:
            continue
        # Try upstream from_step
        if arg_name in ("from_step", "entries") and upstream_path:
            args["from_step"] = len(upstream_path)
            args_source["from_step"] = f"auto:upstream_step_{len(upstream_path)}"
    return args, args_source


def _target_compatible(output_type: str, target_type: str,
                        output_schema: dict = None) -> bool:
    """Compatibility relaxed: list/list scalar/scalar entry-family match.

    Es. target=event_entry[] è soddisfatto da:
      - event_entry[]                          (exact)
      - read_events output → describe_entries free_text (terminator narrative)
      - sort_entries → event_entry[] (passthrough)

    NUOVO: output_type=json_object con schema che contiene target_type fra
    i valori → compatibile (es. get_now.output=json_object con
    schema.content=iso_timestamp e target=iso_timestamp).
    """
    if not target_type:
        return True
    if is_compatible(output_type, target_type):
        return True
    out_is_list = output_type.endswith("[]") or "entry" in output_type
    tgt_is_list = target_type.endswith("[]") or "entry" in target_type
    if tgt_is_list and out_is_list:
        return True
    if tgt_is_list and output_type in ("free_text", "scalar_metric"):
        return True
    if target_type == "scalar_metric" and output_type in ("scalar_metric",
                                                            "count",
                                                            "iso_timestamp",
                                                            "free_text",
                                                            "bool"):
        return True
    if target_type == "free_text" and output_type in ("free_text", "scalar_metric"):
        return True
    # Recursive schema scan: target_type può essere a qualsiasi livello.
    def _schema_has_compat(s, target):
        if not isinstance(s, dict):
            return False
        for v in s.values():
            if isinstance(v, str) and is_compatible(v, target):
                return True
            if isinstance(v, dict):
                # Nested entry: check both as a type ('type' key) and recurse
                if "type" in v and isinstance(v["type"], str):
                    if is_compatible(v["type"], target):
                        return True
                if "schema" in v and isinstance(v["schema"], dict):
                    if _schema_has_compat(v["schema"], target):
                        return True
                if _schema_has_compat(v, target):
                    return True
        return False

    if output_schema and _schema_has_compat(output_schema, target_type):
        return True
    return False


_RUNTIME_AUTO_FILLABLE = {
    "dir_path": True,
    "file_path": False,
    "person_name": True,
    "time_window": True,
    "iso_timestamp": True,
    "url": False,
    "image_path": False,
    "email_address": False,
    "account_name": True,
}

# Coppia (role, semantic_type) auto-fillable: solo "name" è genuinamente
# derivabile dal contesto query (es. nome di task creato dal contenuto).
# "slug" NO: è ID di una risorsa specifica e va citato esplicitamente.
_RUNTIME_AUTO_FILLABLE_BY_ROLE = {
    ("source", "name"): True,
}


def _can_satisfy_required_args(typing: ExecutorTyping,
                                 query: QueryDescriptor,
                                 upstream_output_type: str = "") -> bool:
    """Verifica required args coperti da query.inputs o constraint o upstream
    o runtime-defaults (es. dir_path → cwd/home, person_name → actor)."""
    for arg_name, meta in typing.inputs.items():
        if not meta.required:
            continue
        # Skip from_step se upstream è disponibile
        if arg_name in ("from_step", "entries") and upstream_output_type:
            continue
        type_ok = any(is_compatible(qi.semantic_type, meta.semantic_type)
                       for qi in query.inputs)
        constraint_ok = (meta.role == "filter" and
                          any(c.key == arg_name or c.key in arg_name
                              for c in query.constraints))
        # Runtime-default fallback: per source args con tipi che hanno
        # un default conoscibile dal runtime (cwd, actor, ecc.).
        runtime_ok = (
            (meta.role in ("source", "filter")
             and _RUNTIME_AUTO_FILLABLE.get(meta.semantic_type, False))
            or _RUNTIME_AUTO_FILLABLE_BY_ROLE.get(
                (meta.role, meta.semantic_type), False)
        )
        if not (type_ok or constraint_ok or runtime_ok):
            return False
    # requires_one_of check
    for group in typing.requires_one_of or []:
        any_ok = False
        for arg in group:
            if arg in typing.inputs:
                meta = typing.inputs[arg]
                if any(is_compatible(qi.semantic_type, meta.semantic_type)
                       for qi in query.inputs):
                    any_ok = True; break
                if arg in ("from_step", "entries") and upstream_output_type:
                    any_ok = True; break
        if not any_ok:
            return False
    return True


def search(query: QueryDescriptor,
            registry: ExecutorRegistry) -> list[AnnotatedPath]:
    """BFS con constraint propagation. Returns ranked AnnotatedPath list."""
    if not query.target:
        return []
    target_type = query.target.semantic_type
    all_constraints = query.constraint_set()
    candidates: list[AnnotatedPath] = []

    # Find entry nodes STRICT (intent-driven, no combinatorial explosion).
    #
    # Layer 1 (preferito): exact match verb_object nel nome
    #   "find persons" → find_persons_indices, get_persons, set_persons
    # Layer 2 (fallback): match object SE Layer 1 vuoto
    # Layer 3 (skip): producer non semanticamente legato all'intent
    #
    # Required args coverable da query+upstream.
    entries = []
    verb = (query.intent_verb or "").lower()
    obj = (query.intent_object or "").lower()
    # Layer 1: tool con BOTH verb e object nel nome
    # Layer 1.5: verb in name + required input compat con query input
    # Layer 2: object in name only
    # Layer 3: verb + output_compat
    layer1 = []
    layer1_5 = []
    layer2 = []
    layer3 = []
    query_input_types = {qi.semantic_type for qi in query.inputs}
    for name in registry.all_names():
        t = registry.typing(name)
        if not t or t.is_terminator:
            continue
        nm = name.lower()
        v_match = bool(verb) and verb in nm
        o_match = bool(obj) and obj in nm
        out_compat = _target_compatible(t.output.type, target_type, t.output.schema)
        if not _can_satisfy_required_args(t, query):
            continue
        # Check input semantic_type compat: any required arg matches a query input
        input_query_match = False
        for arg_name, meta in t.inputs.items():
            if meta.role != "source":
                continue
            for qt in query_input_types:
                if is_compatible(qt, meta.semantic_type):
                    input_query_match = True; break
            if input_query_match:
                break
        if v_match and o_match:
            layer1.append(t)
        elif v_match and input_query_match and out_compat:
            layer1_5.append(t)
        elif o_match:
            layer2.append(t)
        elif v_match and out_compat:
            layer3.append(t)
    # Layer 0: CATALOG-AS-CORPUS retrieval (universal §7.3).
    # Score ogni executor del catalog rispetto alla query in modo DATA-DRIVEN,
    # INDIPENDENTE dall'intent_verb/object guess del LLM. La LLM ha 55%
    # error rate sull'intent → il catalog stesso è source-of-truth.
    #
    # Score features (tutte derivate dal typing JSON):
    #  f_input: required source-args match a query.inputs.semantic_type
    #  f_constraint: % constraint che l'executor puo' consumare (consumes +
    #               schema field + class-equivalent)
    #  f_target: compatibility output.type ↔ query.target.semantic_type
    #  f_name: query salient tokens che appaiono in name/inputs/schema
    #
    # Top-12 scores → Layer 0 entry candidates (orthogonal alla LLM-driven L1+).
    q_inputs_types = {qi.semantic_type for qi in query.inputs}
    q_cons_tokens = set()
    for c in query.constraints:
        q_cons_tokens.add((c.key or "").lower())
        q_cons_tokens.add(_canonical_key((c.key or "").lower()))
        q_cons_tokens.add(str(c.value or "").lower())
    q_cons_tokens.discard("")
    q_query_tokens = set()
    # raw query NL — token-matching catalog-as-corpus
    import re as _re
    raw_q = getattr(query, "raw_query", "") or ""
    for w in _re.findall(r"[a-zàèéìòù][a-zàèéìòù0-9]+", raw_q.lower()):
        if len(w) > 2:
            q_query_tokens.add(w)
    for qi in query.inputs:
        for w in str(qi.value or "").lower().split():
            if len(w) > 2:
                q_query_tokens.add(w)
    if verb: q_query_tokens.add(verb)
    if obj: q_query_tokens.add(obj)

    catalog_scores = []
    for name in registry.all_names():
        t = registry.typing(name)
        if not t or t.is_terminator:
            continue
        if not _can_satisfy_required_args(t, query):
            continue
        # f_input
        req_source = [m for m in t.inputs.values()
                        if m.required and m.role == "source"]
        if req_source:
            covered = sum(1 for m in req_source
                            if any(is_compatible(qit, m.semantic_type)
                                      for qit in q_inputs_types))
            f_input = covered / len(req_source)
        else:
            f_input = 0.5  # nessun required source = ambiguous
        # f_constraint
        schema_keys = set()
        if t.output and t.output.schema:
            for k in t.output.schema.keys():
                schema_keys.add(k.lower())
                schema_keys.add(_canonical_key(k).lower())
        consume_tokens = set()
        for c_decl in t.consumes:
            kind_key = c_decl.split(":")
            if len(kind_key) == 2:
                consume_tokens.add(kind_key[1].lower())
        cons_matches = 0
        for ct in q_cons_tokens:
            if ct in schema_keys or ct in consume_tokens:
                cons_matches += 1
            else:
                for sk in schema_keys | consume_tokens:
                    if _same_class(ct, sk):
                        cons_matches += 1
                        break
        f_constraint = cons_matches / max(1, len(query.constraints))
        # f_target
        f_target = 0.0
        if query.target:
            tgt = query.target.semantic_type
            if t.output.type == tgt:
                f_target = 1.0
            elif _target_compatible(t.output.type, tgt, t.output.schema):
                f_target = 0.5
        # f_name: query tokens in tool surface (name + inputs + schema)
        tool_surface = set(name.lower().split("_"))
        for arg, meta in t.inputs.items():
            tool_surface.update(arg.lower().split("_"))
        for k in schema_keys:
            tool_surface.update(k.split("_"))
        name_overlap = len(q_query_tokens & tool_surface)
        f_name = min(1.0, name_overlap / 3.0)
        # weighted sum
        total = 0.4 * f_constraint + 0.25 * f_input + 0.2 * f_target + 0.15 * f_name
        if total > 0.15:
            catalog_scores.append((total, t))
    catalog_scores.sort(key=lambda x: -x[0])
    import os as _os
    _l0_size = int(_os.environ.get("SIM_LAYER0_SIZE", "0"))
    layer0 = [t for _, t in catalog_scores[:_l0_size]]

    # Layer 0-dense BGE-M3 — gated SIM_DENSE_ENTRY
    import os as _os
    if _os.environ.get("SIM_DENSE_ENTRY", "0") != "1":
        layer0_dense_skip = True
    else:
        layer0_dense_skip = False
    try:
        if layer0_dense_skip:
            raise RuntimeError("dense entry disabled")
        import sim_dense
        if not hasattr(search, "_dense_cache"):
            search._dense_cache = sim_dense.build_or_load(
                list(registry.all_names()))
        raw_q = getattr(query, "raw_query", "") or ""
        if raw_q and search._dense_cache:
            dense_top = sim_dense.query_topk(
                raw_q, search._dense_cache, k=8, min_score=0.45)
            layer0_dense = []
            for nm, _score in dense_top:
                t = registry.typing(nm)
                if (t and not t.is_terminator
                        and _can_satisfy_required_args(t, query)):
                    layer0_dense.append(t)
            # Inserisci layer0_dense PRIMA di layer0 (cosine ha prior più alto)
            l0_names = {x.name for x in layer0}
            merged = []
            for t in layer0_dense:
                if t.name not in l0_names:
                    merged.append(t)
            layer0 = layer0_dense + [t for t in layer0
                                       if t.name not in {x.name for x in layer0_dense}]
            layer0 = layer0[:12]
    except Exception as e:
        pass

    # Layer 3 safety: producer verb-match indipendente da object.
    # Layer 3 safety: ordinato per RELEVANCE (output exact match > relaxed
     # match > target-in-schema). Universal §7.3.
    def _layer3_score(t):
        if not t.output: return 0
        out_t = t.output.type
        if out_t == target_type: return 3  # exact
        if is_compatible(out_t, target_type): return 2  # downcast
        if t.output.schema:
            for v in t.output.schema.values():
                if isinstance(v, str) and is_compatible(v, target_type):
                    return 1  # schema field match
        return 0
    layer3_safety = sorted(layer3, key=lambda t: -_layer3_score(t))[:3]

    # Layer SUB_ACTIONS (Google decomposition): per ogni sub_action {verb,obj},
    # includi executor verb+obj match come candidato entry.
    layer_sub = []
    sub_actions_q = getattr(query, "sub_actions", None) or []
    for sa_v, sa_o in sub_actions_q:
        sa_v_l, sa_o_l = sa_v.lower(), sa_o.lower()
        for name in registry.all_names():
            t = registry.typing(name)
            if not t or t.is_terminator:
                continue
            nm = name.lower()
            if sa_v_l in nm and sa_o_l in nm:
                if _can_satisfy_required_args(t, query):
                    layer_sub.append(t)

    seen = set()
    entries = []
    for layer in (layer0, layer1, layer2, layer1_5, layer_sub, layer3_safety):
        for t in layer:
            if t.name in seen:
                continue
            seen.add(t.name)
            entries.append(t)
    # Layer 4 emergency fallback: query con object non-catalog (numbers/texts)
    # ricadono su builtin universali introspettivi.
    if not entries:
        fallback_names = ("get_now", "get_processes", "get_location",
                            "read_persons", "find_urls", "find_files")
        for nm in fallback_names:
            t = registry.typing(nm)
            if t and not t.is_terminator and _can_satisfy_required_args(t, query):
                entries.append(t)
    # Layer 5 AFFINITY MATCH FORCE-INCLUDE (universal §7.3, manifest-derived).
    # Tool con affinity tokens fortemente sovrapposti a query (constraints +
    # intent + input values) sono inclusi anche se _can_satisfy_required_args
    # fallisce — Metnos può comporre la chiamata da query data + runtime defaults.
    # Es: query con constraint sha256 → compute_signatures affinity contiene "sha256"
    # → force-include nei candidates anche se output non match target.
    import os as _os_aff
    # Layer 4.5: ADMIN signals via config file (data-driven, not hardcoded code).
    # Carica admin_signals.json e force-include `admin` se segnali strutturali
    # corrispondono (UNC path, constraint keys, query keywords in raw_query).
    if _os_aff.environ.get("SIM_ADMIN_CONFIG", "1") == "1":
        if not hasattr(search, "_admin_signals"):
            import json as _json_adm
            from pathlib import Path as _PathAdm
            cfg = _PathAdm(__file__).parent / "admin_signals.json"
            search._admin_signals = (
                _json_adm.loads(cfg.read_text()) if cfg.exists() else {}
            )
        sig = search._admin_signals
        admin_trigger = False
        # (a) input value prefix match (UNC, smb, etc.)
        for qi in query.inputs:
            v = str(qi.value or "").lower()
            if any(v.startswith(p.lower()) for p in sig.get("input_value_prefixes", [])):
                admin_trigger = True
                break
        # (b) constraint key/value in admin keys
        if not admin_trigger:
            ck = set(sig.get("constraint_keys", []))
            for c in query.constraints:
                if ((c.key or "").lower() in ck
                        or (c.kind or "").lower() in ck
                        or (str(c.value or "")).lower() in ck):
                    admin_trigger = True
                    break
        # (c) raw_query keyword match
        if not admin_trigger:
            raw_q = (getattr(query, "raw_query", "") or "").lower()
            for kw in sig.get("query_keywords", []):
                if kw.lower() in raw_q:
                    admin_trigger = True
                    break
        if admin_trigger:
            t_admin = registry.typing("admin")
            if t_admin and t_admin.name not in {e.name for e in entries}:
                entries.append(t_admin)

    # Layer 4.6: CONSTRAINT-DRIVEN PROMOTION universal §7.3.
    # Quando query ha constraint con key in famiglie note (sig/loc/admin),
    # force-include il tool corrispondente nei candidates indipendentemente
    # da target_compat o required_args (Metnos può comporre).
    # Mappa derivata dal vocab §2.2 transforms — NON hardcoded patterns.
    CONSTRAINT_TOOL_MAP = {
        # signatures family (hash/checksum)
        "sha256": ["compute_signatures", "compute_files_loc"],
        "md5":    ["compute_signatures"],
        "sha1":   ["compute_signatures"],
        "checksum": ["compute_signatures", "compute_files_loc"],
        "signature": ["compute_signatures"],
        # lines-of-code / count
        "lines": ["compute_files_loc", "compute_entries"],
        "loc":   ["compute_files_loc"],
        "count": ["compute_entries", "compute_files_loc"],
        # system / admin
        "mount": ["admin"],
        "unmount": ["admin"],
        "port":  ["admin"],
        "ports": ["admin"],
    }
    if _os_aff.environ.get("SIM_CONSTRAINT_PROMOTE", "1") == "1":
        promoted_names = set()
        for c in query.constraints:
            key_l = (c.key or "").lower()
            val_l = str(c.value or "").lower()
            for k in (key_l, val_l):
                if k in CONSTRAINT_TOOL_MAP:
                    for tname in CONSTRAINT_TOOL_MAP[k]:
                        if tname in {e.name for e in entries}:
                            continue
                        t = registry.typing(tname)
                        if t and not t.is_terminator:
                            entries.append(t)
                            promoted_names.add(tname)
        # Mark as force-accepted (bypass target_compat) like affinity-forced
        if promoted_names:
            existing_forced = getattr(search, "_affinity_forced_names", set())
            search._affinity_forced_names = existing_forced | promoted_names

    if _os_aff.environ.get("SIM_AFFINITY_FALLBACK", "1") == "1":
        affinity_tokens_q = set()
        # Costituisci token set da query: constraint key/value + input value words
        for c in query.constraints:
            affinity_tokens_q.add((c.key or "").lower())
            affinity_tokens_q.add(str(c.value or "").lower())
            affinity_tokens_q.add((c.kind or "").lower())
        for qi in query.inputs:
            val_l = str(qi.value or "").lower()
            # Path/url with system prefix → admin signal
            if val_l.startswith(("\\\\", "smb://", "cifs://", "//", "nfs://")):
                affinity_tokens_q.add("mount")
                affinity_tokens_q.add("share")
                affinity_tokens_q.add("network")
            # Extract words from value (path components, etc.)
            import re as _re_aff
            for word in _re_aff.findall(r"[a-z][a-z0-9_-]+", val_l):
                affinity_tokens_q.add(word)
        affinity_tokens_q.discard("")
        # Load tool affinity (manifest-derived, cached lazy)
        if not hasattr(search, "_tool_affinity"):
            import tomllib as _tomllib
            from pathlib import Path as _PathAff
            ta = {}
            # Source 1: executors/*/manifest.toml
            _aff_dir = _PathAff("/opt/metnos/executors")
            if _aff_dir.exists():
                for d in _aff_dir.iterdir():
                    mp = d / "manifest.toml"
                    if not mp.exists():
                        continue
                    try:
                        m = _tomllib.loads(mp.read_text())
                        aff = m.get("affinity", []) or []
                        if aff:
                            ta[m.get("name", d.name)] = {
                                str(t).lower().strip() for t in aff if t
                            }
                    except Exception:
                        pass
            # Source 2: runtime/system/builtin_manifests.toml (system verbs)
            _builtin_mf = _PathAff("/opt/metnos/runtime/system/builtin_manifests.toml")
            if _builtin_mf.exists():
                try:
                    bm = _tomllib.loads(_builtin_mf.read_text())
                    for vname, vmeta in bm.items():
                        aff = vmeta.get("affinity", []) or []
                        if aff:
                            # Tokenize multi-word entries (e.g. "porte tcp")
                            tokens = set()
                            for t in aff:
                                s = str(t).lower().strip()
                                tokens.add(s)
                                for w in s.split():
                                    if len(w) > 2:
                                        tokens.add(w)
                            ta[vname] = tokens
                except Exception:
                    pass
            search._tool_affinity = ta
        # Also incorporate raw_query tokens (universal §7.3: query NL → tokens
        # complementary a constraint/input value matching).
        raw_q = (getattr(query, "raw_query", "") or "").lower()
        if raw_q:
            import re as _re_rq
            for word in _re_rq.findall(r"[a-zàèéìòù][a-zàèéìòù0-9_-]+", raw_q):
                affinity_tokens_q.add(word)
        # Build INVERSE TOKEN FREQUENCY: rare tokens = strong signal.
        # Universal §7.3: a token che appare in poche tool affinity → 1 hit
        # è sufficiente. Token comune → richiede più hit per evitare noise.
        if not hasattr(search, "_token_freq"):
            from collections import Counter as _Counter
            tf = _Counter()
            for tok_set in search._tool_affinity.values():
                for tk in tok_set:
                    tf[tk] += 1
            search._token_freq = dict(tf)
        # Force-include tools con strong affinity overlap (weighted by IDF).
        # Soglia in "weighted hits": somma di pesi token. Token raro (≤3 tools)
        # peso=2; token comune (>3) peso=1.
        AFFINITY_FORCE_WEIGHTED_THRESHOLD = 2.5
        existing_names = {e.name for e in entries}
        affinity_candidates = []
        for tname, tok_set in search._tool_affinity.items():
            if tname in existing_names:
                continue
            t = registry.typing(tname)
            if not t or t.is_terminator:
                continue
            matched = tok_set & affinity_tokens_q
            if not matched:
                continue
            score = 0.0
            for tk in matched:
                freq = search._token_freq.get(tk, 1)
                # Rare token (≤3 tool) → 2.0, common → 1.0
                score += 2.0 if freq <= 3 else 1.0
            if score >= AFFINITY_FORCE_WEIGHTED_THRESHOLD:
                affinity_candidates.append((score, t))
        # Add top-K affinity matches as fallback entries.
        # Was top-3, ora top-5 per coprire più sistema-verbs (admin, undo) +
        # tool con qualifier (compute_signatures, find_*_indices).
        affinity_candidates.sort(key=lambda x: -x[0])
        affinity_forced_names = set()
        if _os_aff.environ.get("SIM_AFFINITY_DEBUG"):
            import sys as _sys_dbg
            print(f"  [aff] tokens_q={sorted(affinity_tokens_q)[:15]}...", file=_sys_dbg.stderr)
            print(f"  [aff] candidates ({len(affinity_candidates)}): {[(c[0], c[1].name) for c in affinity_candidates[:8]]}", file=_sys_dbg.stderr)
            print(f"  [aff] existing entries: {sorted(existing_names)[:10]}", file=_sys_dbg.stderr)
        for _, t in affinity_candidates[:5]:
            if t.name not in existing_names:
                entries.append(t)
                existing_names.add(t.name)
                affinity_forced_names.add(t.name)
        # Memorize per la fase di accept (sotto): bypass target_compat
        search._affinity_forced_names = affinity_forced_names
    # Hard cap entry nodes: max 12
    entries = entries[:12]

    # Initial frontier: each entry as 1-step path
    frontier = []
    for e in entries:
        args, src = _bind_args(e, query, [])
        consumed = set(_arg_consume_constraints(e, args, query))
        step = AnnotatedStep(
            tool=e.name, args=args, args_source=src,
            output_type=e.output.type,
            consumes_constraints=list(consumed),
        )
        path = AnnotatedPath(
            steps=[step],
            constraints_consumed=consumed,
            constraints_remaining=all_constraints - consumed,
        )
        # Check accept
        if (_target_compatible(e.output.type, target_type, e.output.schema)
            and not path.constraints_remaining):
            candidates.append(path)
        # Universal §7.3 MUTATING verb exception: for mutating verbs, the side-
        # effect IS the outcome — accept 1-step path even if output.type not
        # strict-compatible (e.g. move_files outputs move_result[], target is
        # file_entry[] — semantically equivalent). Derived from §2.2 vocab.
        elif (verb in {"move", "delete", "send", "share", "create",
                          "write", "set", "compress", "change", "order"}
              and e.name.startswith(verb + "_")
              and not path.constraints_remaining):
            candidates.append(path)
        # Affinity-forced entries (via SIM_AFFINITY_FALLBACK): accept anche
        # se output non strict-compat (sono stati selezionati per forte
        # affinity match con query → semanticamente rilevanti anche se
        # target_type differisce — es. admin output=scalar_metric per query
        # con UNC path che cerca mount, semanticamente OK).
        elif e.name in getattr(search, "_affinity_forced_names", set()):
            candidates.append(path)
        frontier.append(path)

    # BFS expand con safeguard iterazioni totali
    iterations = 0
    while frontier and len(candidates) < MAX_CANDIDATES:
        iterations += 1
        if iterations > MAX_TOTAL_ITERATIONS:
            break
        if len(frontier) > MAX_FRONTIER_SIZE:
            # Prune frontier: tieni solo i path con consumed > 0 (più promettenti)
            pruned = [p for p in frontier if p.constraints_consumed][:MAX_FRONTIER_SIZE]
            if not pruned:
                # Tutti zero-consumed: tieni primi MAX_FRONTIER_SIZE per età
                pruned = frontier[:MAX_FRONTIER_SIZE]
            frontier = pruned
        if not frontier:
            break
        path = frontier.pop(0)
        if len(path.steps) >= MAX_DEPTH:
            continue
        last_step = path.steps[-1]
        last_output = last_step.output_type
        used_names = {s.tool for s in path.steps}
        # Find downstream nodes che accettano last_output OR via from_step
        for name in registry.all_names():
            if name in used_names:
                continue
            t = registry.typing(name)
            if not t:
                continue
            # Check compatibility: t accepts last_output via from_step o by type
            accepts = False
            for arg, meta in t.inputs.items():
                if arg in ("from_step", "entries"):
                    accepts = True; break
                if is_compatible(last_output, meta.semantic_type):
                    accepts = True; break
            if not accepts:
                continue
            # Bind args + compute consumed
            args, src = _bind_args(t, query, path.steps)
            new_consumed = set(_arg_consume_constraints(t, args, query))
            new_step = AnnotatedStep(
                tool=t.name, args=args, args_source=src,
                output_type=t.output.type,
                consumes_constraints=list(new_consumed),
            )
            new_path = AnnotatedPath(
                steps=path.steps + [new_step],
                constraints_consumed=path.constraints_consumed | new_consumed,
                constraints_remaining=path.constraints_remaining - new_consumed,
            )
            # Accept criteria relaxed: target_compatible.
            # Constraints remaining → penalty in ranking, no scarto.
            if _target_compatible(t.output.type, target_type, t.output.schema):
                candidates.append(new_path)
            # Continua expand (path lunghi possono coprire constraint mancanti)
            frontier.append(new_path)

    # Sequential merge backward search (target-driven, ortogonal a forward).
    # Universal §7.3: dedup tool seq, no quality filter (debug only).
    import os as _os
    if _os.environ.get("SIM_BACKWARD", "0") == "1":
        backward_cands = _search_backward(query, registry, target_type)
    else:
        backward_cands = []
    seen_paths = {tuple(s.tool for s in p.steps) for p in candidates}
    for bp in backward_cands:
        key = tuple(s.tool for s in bp.steps)
        if key in seen_paths:
            continue
        seen_paths.add(key)
        candidates.append(bp)
    for c in candidates:
        c.quality_pre = _quality_indicator(c, query)

    # PRUNING DETERMINISTICO §7.3: keep solo top-K candidates per quality_pre
    # prima del ranker (taglia Qwen embedding cost + rumore top-2).
    # Threshold: top-20 OR quality >= 0.40 (cosa è più permissivo).
    # Garantisce sempre almeno 12 entries.
    import os as _os_prune
    if _os_prune.environ.get("SIM_PRUNE", "1") == "1":
        candidates.sort(key=lambda c: -c.quality_pre)
        keep_n = max(12, min(30, len(candidates)))
        # Add anche tutti con quality_pre >= 0.40 (se top non li coprisse)
        keep_set = set(id(c) for c in candidates[:keep_n])
        for c in candidates:
            if c.quality_pre >= 0.40:
                keep_set.add(id(c))
        candidates = [c for c in candidates if id(c) in keep_set]

    for c in candidates:
        c.score = _rank(c, query)
    # SIM_QWEN_SIGNAL=1: add Qwen3-Emb FT classifier as ranker signal.
    # Boost candidate if first_tool object matches Qwen prediction.
    import os as _os
    if _os.environ.get("SIM_QWEN_SIGNAL", "0") == "1":
        try:
            from qwen_intent_signal import score_boost as _qwen_boost
            q_str = getattr(query, "raw_query", "") or ""
            if not q_str:
                parts = [query.intent_verb or "", query.intent_object or ""]
                parts += [str(i.value) for i in query.inputs if i.value]
                q_str = " ".join(p for p in parts if p)
            for c in candidates:
                if c.steps:
                    c.score += _qwen_boost(c.steps[0].tool, q_str,
                                            weight=float(_os.environ.get("SIM_QWEN_WEIGHT", "0.7")))
        except Exception as e:
            if _os.environ.get("SIM_QWEN_DEBUG"):
                import sys as _sys
                print(f"qwen signal err: {e}", file=_sys.stderr)
    if _os.environ.get("SIM_TIEBREAK", "1") == "1":
        candidates.sort(key=lambda c: (
            -c.score,
            sum(1 for s in c.steps if s.tool != "final_answer"),
            -len(c.constraints_consumed),
            len(c.steps[0].tool) if c.steps else 0,
            c.steps[0].tool if c.steps else "",
        ))
    else:
        candidates.sort(key=lambda c: -c.score)
    return candidates


def _quality_indicator(path: AnnotatedPath, query: QueryDescriptor) -> float:
    """Pre-ranker quality score deterministico (universal §7.3, no LLM).

    Indicatori veloci che filtrano path manifestamente deboli prima del
    rank completo. Range [0, 1].
    """
    if not path.steps:
        return 0.0
    score = 0.0
    n = sum(1 for s in path.steps if s.tool != "final_answer")
    # 1. Length: 1 step = 1.0, 2 = 0.85, 3 = 0.65, 4+ = 0.4
    if n == 1:
        score += 0.30
    elif n == 2:
        score += 0.25
    elif n == 3:
        score += 0.18
    else:
        score += 0.10
    # 2. Constraint coverage
    total_c = len(path.constraints_consumed) + len(path.constraints_remaining)
    if total_c == 0:
        score += 0.30
    else:
        score += 0.30 * (len(path.constraints_consumed) / total_c)
    # 3. Output→target compatibility (last step) - exact match
    last_step = path.steps[-1]
    target_type = (query.target.semantic_type if query.target else "")
    if last_step.output_type == target_type:
        score += 0.25
    elif target_type and is_compatible(last_step.output_type, target_type):
        score += 0.15
    else:
        score += 0.08  # relaxed compat
    # 4. No duplicate consecutive tools
    tools = [s.tool for s in path.steps]
    if len(tools) == len(set(tools)):
        score += 0.15
    return min(1.0, score)


def _search_backward(query: QueryDescriptor,
                       registry: ExecutorRegistry,
                       target_type: str) -> list[AnnotatedPath]:
    """Search backward: seed da producer del target → estendi a ritroso.

    Cattura path "right" anche quando LLM intent_verb/object è sbagliato.
    Universal §7.3: usa SOLO output_type ↔ target compatibility (oggettivo)
    e input_type ↔ query.inputs (oggettivo). No LLM-intent matching.

    Returns paths [entry → ... → final_producer] (forward orientation).
    """
    if not target_type:
        return []
    all_constraints = query.constraint_set()
    candidates: list[AnnotatedPath] = []

    # Step 1: SEED = executor il cui output è compatibile col target
    target_producers = []
    for name in registry.all_names():
        t = registry.typing(name)
        if not t or t.is_terminator:
            continue
        if _target_compatible(t.output.type, target_type, t.output.schema):
            target_producers.append(t)

    # Step 2: per ogni target producer, prova:
    # (a) atomic: required args coperti da query.inputs/constraints → singleton path
    # (b) 2-step: find upstream producer (output ⊃ T.required_input)
    # Limit producers to top 30 per evitare explosion (filtra per token match base)
    target_producers = target_producers[:30]

    for tp in target_producers:
        # (a) atomic path
        if _can_satisfy_required_args(tp, query):
            args, src = _bind_args(tp, query, [])
            consumed = set(_arg_consume_constraints(tp, args, query))
            step = AnnotatedStep(
                tool=tp.name, args=args, args_source=src,
                output_type=tp.output.type,
                consumes_constraints=list(consumed),
            )
            atomic_path = AnnotatedPath(
                steps=[step],
                constraints_consumed=consumed,
                constraints_remaining=all_constraints - consumed,
            )
            candidates.append(atomic_path)

        # (b) 2-step: backward chain
        # Find required piping/source args of tp
        for arg_name, meta in tp.inputs.items():
            if not meta.required:
                continue
            if meta.role not in ("piping", "source"):
                continue
            # Find executors whose output matches this required input type
            req_type = meta.semantic_type
            for cand_name in registry.all_names():
                upstream = registry.typing(cand_name)
                if not upstream or upstream.is_terminator:
                    continue
                if upstream.name == tp.name:
                    continue
                if not _can_satisfy_required_args(upstream, query):
                    continue
                # upstream.output.type compatible con req_type
                if not is_compatible(upstream.output.type, req_type):
                    continue
                # Build 2-step path: upstream → tp
                up_args, up_src = _bind_args(upstream, query, [])
                up_consumed = set(_arg_consume_constraints(upstream, up_args, query))
                up_step = AnnotatedStep(
                    tool=upstream.name, args=up_args, args_source=up_src,
                    output_type=upstream.output.type,
                    consumes_constraints=list(up_consumed),
                )
                # tp step with upstream binding
                tp_args, tp_src = _bind_args(tp, query, [up_step])
                tp_consumed = set(_arg_consume_constraints(tp, tp_args, query))
                tp_step = AnnotatedStep(
                    tool=tp.name, args=tp_args, args_source=tp_src,
                    output_type=tp.output.type,
                    consumes_constraints=list(tp_consumed),
                )
                total_consumed = up_consumed | tp_consumed
                two_step = AnnotatedPath(
                    steps=[up_step, tp_step],
                    constraints_consumed=total_consumed,
                    constraints_remaining=all_constraints - total_consumed,
                )
                candidates.append(two_step)
            break  # solo prima required arg → evita explosion

    # Cap a 50 backward candidates
    return candidates[:50]


import os as _os_rank
# Tunable weights (env override-able for grid search)
_W_VERB_MATCH = float(_os_rank.environ.get("RANK_VERB_MATCH", "0.5"))
_W_OBJ_MATCH = float(_os_rank.environ.get("RANK_OBJ_MATCH", "0.5"))
_W_VERB_PREFIX = float(_os_rank.environ.get("RANK_VERB_PREFIX", "0.4"))
_W_TARGET_EXACT = float(_os_rank.environ.get("RANK_TARGET_EXACT", "0.7"))
_W_TARGET_COMPAT = float(_os_rank.environ.get("RANK_TARGET_COMPAT", "0.4"))
_W_QUALIFIER_PENALTY = float(_os_rank.environ.get("RANK_QUALIFIER_PENALTY", "1.0"))
_W_QUALIFIER_BONUS = float(_os_rank.environ.get("RANK_QUALIFIER_BONUS", "0.3"))
_W_ATOMIC_BOOST = float(_os_rank.environ.get("RANK_ATOMIC_BOOST", "0.7"))
_W_MUTATING_TARGET = float(_os_rank.environ.get("RANK_MUTATING_TARGET", "0.5"))


def _rank(path: AnnotatedPath, query: QueryDescriptor) -> float:
    """Score multi-dim:
      - intent_match: verb AND object nel primo tool → big bonus
      - constraint coverage: % constraint consumate
      - length: prefer corti (telos.tempo)
      - canonical: bonus se finalizer giusto (describe per read, compute per count)
      - penalize: tool ripetuto, request_new_executor
    """
    if not path.steps:
        return 0.0
    score = 0.0
    first_tool = path.steps[0].tool.lower()
    verb = (query.intent_verb or "").lower()
    obj = (query.intent_object or "").lower()

    # 1. Intent match exact (verb + object nel primo tool)
    if verb and verb in first_tool:
        score += _W_VERB_MATCH
    if obj and obj in first_tool:
        score += _W_OBJ_MATCH
    if not ((verb and verb in first_tool) or (obj and obj in first_tool)):
        score -= 0.5

    # 2. Constraint coverage (fraction) + interaction con intent match
    total_constraints = len(path.constraints_consumed) + len(path.constraints_remaining)
    intent_match_frac = 0.0
    if verb and verb in first_tool: intent_match_frac += 0.5
    if obj and obj in first_tool: intent_match_frac += 0.5
    if total_constraints > 0:
        coverage = len(path.constraints_consumed) / total_constraints
        score += 0.5 * coverage  # main effect
        if path.constraints_remaining:
            score -= 0.3
        # Interaction term: coverage × intent_match (universal §7.3)
        # high coverage + high intent_match → qualitative bonus
        score += 0.4 * coverage * intent_match_frac

    # 3. Length penalty (corto = telos.tempo)
    n_real = sum(1 for s in path.steps if s.tool != "final_answer")
    score -= 0.15 * max(0, n_real - 2)  # ok fino a 2 step

    # 4. Finalizer match (intent.verb → expected finalizer)
    last_tool = path.steps[-1].tool.lower()
    expected_finalizers = {
        "compute": ("compute_entries",),
        "describe": ("describe_entries",),
        "send": ("send_messages",),
        "read": ("describe_entries", "read_messages", "read_events", "read_persons"),
        "find": ("describe_entries",),
        "list": ("describe_entries",),
        "get": tuple(),  # spesso single-step
    }
    if verb in expected_finalizers:
        if any(f in last_tool for f in expected_finalizers[verb]):
            score += 0.2

    # 4.5 Mutating intent → ultima azione DEVE essere verbo coerente.
    # Famiglie semantiche universali (§7.3): verbi che sostanzialmente
    # producono la stessa side-effect su un object.
    mutating_intent_targets = {
        "delete": ("delete_", "remove_"),
        "send": ("send_",),
        "move": ("move_",),
        "create": ("create_", "write_"),
        "write": ("write_", "create_", "set_"),
        "set": ("set_", "write_"),
        "share": ("share_",),
        "compress": ("compress_",),
        "change": ("change_",),
        "order": ("order_",),
    }
    if verb in mutating_intent_targets:
        targets = mutating_intent_targets[verb]
        if any(last_tool.startswith(t) for t in targets):
            score += 0.5
        else:
            score -= 0.3

    # 4.55 Single-step atomic boost: quando query fornisce TUTTI gli input
    # concreti per il verbo mutating, single-step is preferito su producer→mutating.
    # Es. "sposta /tmp/a.txt in /home" → move_files alone (path nell'input).
    if (verb in {"delete", "move", "create", "write", "send", "share",
                  "compress", "extract", "change", "set"}
        and len(path.steps) == 1
        and any(tool_segs[0] == verb if tool_segs else False
                for tool_segs in [first_tool.split("_")])):
        score += _W_ATOMIC_BOOST

    # 4.6 First step deve essere un PRODUCER (legge stato/discovery).
    # Eccezione: intent atomic (create/write/send/set/share/order) può essere
    # single-step se la query fornisce tutti gli input inline.
    producer_prefixes = ("find_", "read_", "get_", "list_", "filter_",
                          "sort_", "group_", "compute_", "classify_",
                          "describe_", "compare_", "render_", "extract_")
    atomic_intent_ok = {"create", "write", "send", "share", "change",
                          "set", "order", "compress", "delete", "move",
                          "extract"}
    if not any(first_tool.startswith(p) for p in producer_prefixes):
        if verb in atomic_intent_ok and len(path.steps) == 1:
            pass
        else:
            score -= 0.6

    # 5. Penalty: tool ripetuto consecutivo
    tools = [s.tool for s in path.steps]
    for i in range(1, len(tools)):
        if tools[i] == tools[i - 1]:
            score -= 0.5

    # 6. Penalty: request_new_executor (escape hatch)
    if any("request_new_executor" in t for t in tools):
        score -= 0.6

    # 8. READ-intent vs MUTATING-verb first-step (heavy penalty)
    # First-step di un path che soddisfa intent READ deve essere un PRODUCER
    # (find/read/get/list/filter/compute/classify/sort/group), MAI un mutating
    # come create/delete/move/send/set/write/share/change/compress/order.
    read_intents = {"read","list","get","find","compute","classify",
                     "filter","sort","group","describe","compare","render"}
    mutating_prefixes = ("create_","delete_","move_","send_","set_",
                          "write_","share_","change_","compress_","order_")
    if verb in read_intents:
        if any(first_tool.startswith(mp) for mp in mutating_prefixes):
            score -= 1.5

    # 9. find vs get bias §7.3 universal:
    #   - get_X = METADATA diretto (size, mtime, owner, ecc.) one-hop
    #   - find_X = CONTENT/discovery, poi serve un get_X per metadata
    # Quindi:
    #   - constraint contiene sort/filter su campo metadata → preferisci get_X
    #   - constraint contiene filter pattern/glob (no metadata) → preferisci find_X
    #   - no constraint → discovery → find_X bias
    metadata_field_keys = {
        "modified_at", "modified", "mtime", "ctime", "atime",
        "size", "owner", "mode", "permissions",
        "created_at", "accessed_at",
    }
    discovery_keys = {"glob_pattern", "pattern", "ext", "extension", "name"}
    cons_keys_canon = {_canonical_key((c.key or "")).lower() for c in query.constraints}
    has_sort = any(c.kind == "sort" for c in query.constraints)
    has_metadata_signal = has_sort or bool(cons_keys_canon & metadata_field_keys)
    has_discovery_signal = bool(cons_keys_canon & discovery_keys)
    if has_metadata_signal and not has_discovery_signal:
        if first_tool.startswith("get_"):
            score += 0.2
        elif first_tool.startswith("find_"):
            score -= 0.1
    elif has_discovery_signal:
        if first_tool.startswith("find_"):
            score += 0.2
        elif first_tool.startswith("get_"):
            score -= 0.1
    else:
        if not query.inputs:
            if first_tool.startswith("find_"):
                score += 0.15
            elif first_tool.startswith("get_"):
                score -= 0.05

    # 10. Producer-verb match con intent.verb: read→read_X, list→list_X|find_X|get_X
    producer_compat = {
        "read": ("read_",),
        "list": ("list_","find_","read_"),
        "find": ("find_","read_","get_"),
        "get": ("get_","find_","read_"),
        "compute": ("find_","get_","read_","list_"),
    }
    if verb in producer_compat:
        if any(first_tool.startswith(p) for p in producer_compat[verb]):
            score += 0.3

    # 10.5 INPUT COVERAGE RATIO — gated by SIM_RULE_10_5
    import os as _os
    try:
     if _os.environ.get("SIM_RULE_10_5", "1") == "1":
        first_step = path.steps[0]
        from registry import ExecutorRegistry as _R
        if not hasattr(_rank, "_reg_cache"):
            from pathlib import Path
            _rank._reg_cache = _R(
                json_dir=Path(__file__).parent / "typing_cache")
        _typ = _rank._reg_cache._by_name.get(first_step.tool)
        if _typ:
            req_source = [m for m in _typ.inputs.values()
                            if m.required and m.role == "source"]
            if req_source:
                covered = sum(
                    1 for m in req_source
                    if any(is_compatible(qi.semantic_type, m.semantic_type)
                              for qi in query.inputs))
                ratio = covered / len(req_source)
                score += 0.6 * ratio
                if ratio < 0.5:
                    score -= 0.3
    except Exception:
        pass

    import os as _os
    _enable_extras = _os.environ.get("SIM_RANKER_EXTRAS", "1") == "1"
    if _enable_extras:
        pass
    # 10.6 TRANSFORM CONSTRAINT — gated SIM_RULE_10_6
    try:
     if _os.environ.get("SIM_RULE_10_6", "1") == "1":
        for c in query.constraints:
            if c.kind != "transform":
                continue
            key_l = (c.key or "").lower()
            val_l = str(c.value or "").lower()
            satisfied = False
            for s in path.steps:
                t = _rank._reg_cache._by_name.get(s.tool) if hasattr(_rank, "_reg_cache") else None
                if not t or not t.output.schema:
                    continue
                schema_full = set()
                for k in t.output.schema.keys():
                    schema_full.add(k.lower())
                    schema_full.add(_canonical_key(k).lower())
                if key_l in schema_full or val_l in schema_full:
                    satisfied = True
                    break
                # Anche tool name include il transform
                if key_l in s.tool.lower() or val_l in s.tool.lower():
                    satisfied = True
                    break
            if not satisfied:
                score -= 0.8
    except Exception:
        pass

    # 10.7 RARE-TOKEN UNMATCHED — gated SIM_RULE_10_7
    try:
     if _os.environ.get("SIM_RULE_10_7", "1") == "1":
        if not hasattr(_rank, "_rare_tokens"):
            # Build rare-token set: tokens che appaiono in ≤3 executor descriptors
            from collections import Counter
            tok_count = Counter()
            for nm, t in _rank._reg_cache._by_name.items():
                tokens = set()
                tokens.update(nm.lower().split("_"))
                for arg, meta in t.inputs.items():
                    tokens.update(arg.lower().split("_"))
                    tokens.update(meta.semantic_type.lower().split("_"))
                for k in (t.output.schema or {}).keys():
                    tokens.update(str(k).lower().split("_"))
                for c in t.consumes:
                    tokens.update(c.lower().replace(":", "_").split("_"))
                for tk in tokens:
                    tok_count[tk] += 1
            _rank._rare_tokens = {tk for tk, cnt in tok_count.items() if 0 < cnt <= 3 and len(tk) > 2}
        # Estrai token salient dalla query
        query_text = " ".join([
            verb, obj,
            *[str(qi.value or "") for qi in query.inputs],
            *[str(c.key or "") + " " + str(c.value or "") for c in query.constraints],
        ]).lower()
        import re
        q_tokens = set(re.findall(r"[a-z][a-z0-9]+", query_text))
        salient = q_tokens & _rank._rare_tokens
        # Per ognuno: deve essere in qualche parte del first_step typing
        if salient and _typ:
            tool_corpus = set()
            tool_corpus.update(first_step.tool.lower().split("_"))
            for arg, meta in _typ.inputs.items():
                tool_corpus.update(arg.lower().split("_"))
                tool_corpus.update(meta.semantic_type.lower().split("_"))
            for k in (_typ.output.schema or {}).keys():
                tool_corpus.update(str(k).lower().split("_"))
            unmatched = salient - tool_corpus
            if unmatched:
                score -= 0.5 * len(unmatched)
    except Exception:
        pass

    # 11.0 Schema-field match: executor whose output.schema esponse il campo
    # citato dalla constraint (sort/filter su quel campo) → big bonus.
    # §7.3 universal: la struttura dei dati ritornata definisce l'idoneità.
    try:
        first_step = path.steps[0]
        from registry import ExecutorRegistry as _R
        if not hasattr(_rank, "_reg_cache"):
            from pathlib import Path
            _rank._reg_cache = _R(
                json_dir=Path(__file__).parent / "typing_cache")
        _typing = _rank._reg_cache._by_name.get(first_step.tool)
        if _typing and _typing.output and _typing.output.schema:
            schema_keys = set()
            for k in _typing.output.schema.keys():
                schema_keys.add(k)
                schema_keys.add(_canonical_key(k))
            # Schema-field match — gated SIM_RULE_SCHEMA
            if _os.environ.get("SIM_RULE_SCHEMA", "expanded") == "honest15":
                for c in query.constraints:
                    canon_q = _canonical_key(c.key)
                    if c.kind in ("sort", "filter") and (
                        c.key in schema_keys or canon_q in schema_keys
                    ):
                        score += 0.4
                        break
            else:
                schema_hits = 0
                for c in query.constraints:
                    key_l = (c.key or "").lower()
                    val_l = str(c.value or "").lower()
                    hit = False
                    for sk in schema_keys:
                        sk_l = sk.lower()
                        if (key_l == sk_l or val_l == sk_l
                            or _same_class(key_l, sk_l)
                            or _same_class(val_l, sk_l)):
                            hit = True; break
                    if hit:
                        schema_hits += 1
                if schema_hits:
                    score += 0.4 * min(schema_hits, 2)

            # Target match (universal §7.3, tiered priority):
            #   1. Exact output.type == target_type → strongest signal
            #   2. is_compatible(output.type, target) → downcast OK
            #   3. target appears in output.schema VALUES (top-level) → moderate
            #   4. target appears in nested schema → weak
            target_type = (query.target.semantic_type if query.target else "")
            if target_type:
                tt_l = target_type.lower()
                out_t_l = (_typing.output.type or "").lower()
                if out_t_l == tt_l:
                    score += 0.7  # exact
                elif is_compatible(_typing.output.type, target_type):
                    score += 0.4  # downcast
                else:
                    # Count target-type occurrences in schema (recursive).
                    # More matches = tool is "about" target_type (universal §7.3).
                    def _count_target_in_schema(s):
                        if not isinstance(s, dict): return 0
                        n = 0
                        for v in s.values():
                            if isinstance(v, str):
                                vl = v.lower()
                                if vl == tt_l or vl == tt_l.replace("[]", ""):
                                    n += 1
                            elif isinstance(v, dict):
                                n += _count_target_in_schema(v)
                        return n
                    n_matches = _count_target_in_schema(_typing.output.schema)
                    if n_matches >= 2:
                        score += 0.35  # multiple matches: tool is "about" this type
                    elif n_matches == 1:
                        # Top-level vs nested distinction
                        top_values = {v.lower() for v in _typing.output.schema.values()
                                       if isinstance(v, str)}
                        if tt_l in top_values or tt_l.replace("[]", "") in top_values:
                            score += 0.25
                        else:
                            score += 0.1

            # First-step verb prefix EXACT match (strong signal)
            # E.g. intent_verb=write, first_tool=write_files → +0.4 extra
            first_seg = first_tool.split("_")[0] if "_" in first_tool else first_tool
            if first_seg == verb:
                score += 0.4
    except Exception:
        pass

    # 10.9 QUALIFIER REQUIRES STRUCTURAL SIGNAL: qualifier come _indices
    # /_ocr/_hash/_empty/_gz richiedono uno specifico segnale strutturale
    # nella query. Senza segnale → penalty. §7.3 universal (no hardcoded
    # executor names — solo qualifier suffix).
    QUAL_SIGNAL_RULES = {
        "indices": lambda q: (
            any(qi.semantic_type in ("image_path", "person_name") for qi in q.inputs)
            or any("face" in str(c.key or "").lower() or
                     "embedding" in str(c.key or "").lower() or
                     "similar" in str(c.key or "").lower()
                     for c in q.constraints)
        ),
        "ocr": lambda q: (
            any(qi.semantic_type in ("image_path", "pdf_path") for qi in q.inputs)
            and (q.target.semantic_type in ("free_text",) if q.target else False)
        ),
        "hash": lambda q: any(c.kind == "transform" for c in q.constraints),
        "empty": lambda q: any(
            "empty" in str(c.key or "").lower() or
            "size" in str(c.key or "").lower() or
            "free" in str(c.value or "").lower()
            for c in q.constraints
        ),
        "gz": lambda q: q.intent_verb in ("compress", "extract"),
        "zip": lambda q: q.intent_verb in ("compress", "extract"),
        # _html: URL input + target free_text → web page read.
        # Universal §7.3: URL implica web content, html è naturale qualifier.
        "html": lambda q: (
            any(qi.semantic_type == "url" for qi in q.inputs)
            and q.intent_object == "urls"
        ),
        # _pdf: input pdf_path o URL pdf + target free_text
        "pdf": lambda q: (
            any(qi.semantic_type == "pdf_path" for qi in q.inputs)
            or (any(qi.semantic_type == "url"
                     and "pdf" in str(qi.value or "").lower()
                     for qi in q.inputs))
        ),
        # _doc/_spreadsheet/_csv/_xlsx: format hints
        "doc": lambda q: any("doc" in str(c.value or "").lower() or
                              "documento" in str(c.key or "").lower()
                              for c in q.constraints) or q.intent_verb in ("create", "write"),
        "spreadsheet": lambda q: any("foglio" in (q.raw_query or "").lower() or
                                         "spreadsheet" in (q.raw_query or "").lower() or
                                         "xlsx" in (q.raw_query or "").lower()
                                         for _ in [1]),
        "csv": lambda q: (
            "csv" in (q.raw_query or "").lower()
            or any(str(qi.value or "").lower().endswith(".csv") for qi in q.inputs)
        ),
        "xlsx": lambda q: any("foglio" in (q.raw_query or "").lower() or
                                  "xlsx" in (q.raw_query or "").lower() or
                                  "excel" in (q.raw_query or "").lower() for _ in [1]),
    }
    last_seg = first_tool.rsplit("_", 1)[-1] if "_" in first_tool else ""
    _qualifier_satisfied = False
    if _os.environ.get("SIM_RULE_10_9", "1") == "1" and last_seg in QUAL_SIGNAL_RULES:
        if QUAL_SIGNAL_RULES[last_seg](query):
            _qualifier_satisfied = True
            score += 0.3  # bonus quando segnale specifico c'è
        else:
            score -= 0.7

    # 11. Qualifier penalty: SKIP se rule 10.9 already satisfied. Altrimenti
    # token check fallback (universal §7.3).
    if _qualifier_satisfied:
        segs = []  # skip rule 11
    else:
        segs = first_tool.split("_")
    # producer verb_object = 2 segmenti. Extra = qualifier.
    if len(segs) > 2:
        qual_tokens = segs[2:]
        signal_corpus_parts = [
            verb, obj,
            *[str(qi.value or "").lower() for qi in query.inputs],
            *[str(c.key or "").lower() for c in query.constraints],
            *[str(c.value or "").lower() for c in query.constraints],
        ]
        signal_corpus = " ".join(signal_corpus_parts)
        unmatched = sum(1 for q in qual_tokens if q not in signal_corpus)
        if unmatched > 0:
            score -= 1.0 * unmatched

    # 12. CANONICAL BONUS: tool nome esattamente `verb_object` (no qualifier)
    # è il fall-back canonico. Bonus piccolo per favorire single-segment.
    canonical_names = {
        f"{p}_{obj}" for p in ("find","read","get","list","filter",
                                "create","delete","write","set","send",
                                "move","share","change","compress","order")
    }
    if first_tool in canonical_names:
        score += 0.2

    # 7. Bonus: constraint consumed EARLY (al primo step)
    if path.steps:
        first_consumed = len(path.steps[0].consumes_constraints)
        score += 0.1 * first_consumed

    # 12. DENSE COSINE BONUS: BGE-M3 similarity query↔first_step descriptor.
    # Pesato basso (0.3) per evitare displacement degli altri segnali.
    try:
        import os as _os
        if _os.environ.get("SIM_DENSE_BONUS", "1") == "1":
            import sim_dense
            raw_q = getattr(query, "raw_query", "") or ""
            if raw_q and hasattr(_rank, "_reg_cache"):
                if not hasattr(_rank, "_dense_cache"):
                    _rank._dense_cache = sim_dense.build_or_load(
                        list(_rank._reg_cache._by_name.keys()))
                cos = sim_dense.cosine(raw_q, first_tool, _rank._dense_cache)
                if cos > 0.5:  # solo top-half: dense_cosine cap baseline ~0.55
                    score += 0.5 * (cos - 0.5)  # max +0.25
    except Exception:
        pass

    # 13. SUB_ACTIONS COVERAGE (Google decomposition §intent extraction):
    # Path che copre TUTTE le azioni atomiche prende bonus. SOLO bonus, no
    # penalty (LLM può produrre sub_actions spurie, evitare regressione).
    sub_actions = getattr(query, "sub_actions", None) or []
    if sub_actions:
        tool_verbs_objs = set()
        for s in path.steps:
            t_segs = s.tool.split("_")
            if len(t_segs) >= 2:
                tool_verbs_objs.add((t_segs[0], t_segs[1]))
        covered = 0
        for sa_v, sa_o in sub_actions:
            sa_v_l, sa_o_l = sa_v.lower(), sa_o.lower()
            for tv, to in tool_verbs_objs:
                if tv == sa_v_l and to == sa_o_l:
                    covered += 1
                    break
        if covered:
            score += 0.6 * (covered / len(sub_actions))

    # 20. UNIVERSAL §7.3 — pattern fixes via PARSED STRUCTURE (constraints,
    # inputs, target, verb, object) — NO hardcoded query strings.
    import os as _os20
    if _os20.environ.get("SIM_RULE_20", "1") == "1":
        ft = first_tool

        # 20.a URL fetch type: HTML/markdown content fetch = read_urls_html;
        # binary blob fetch = get_urls. Discriminator: URL value semantic
        # signals (extension, path patterns) — universal §7.3 from input.value.
        if (query.intent_object or "") == "urls":
            url_inputs = [qi for qi in query.inputs if qi.semantic_type == "url"]
            HTML_TEXT_SUFFIXES = (".html", ".htm", ".md", ".markdown", ".txt",
                                    ".rst", ".asc", ".text")
            PDF_SUFFIXES = (".pdf",)
            # Patterns generici per URL "text/html content" (no domain hardcoded)
            HTML_PATH_PATTERNS = ("/blob/", "/raw/", "/tree/", "/wiki/",
                                    "/releases/", "/issues/", "/pulls/",
                                    "/commits/", "/discussions/")
            html_ext_url = False
            pdf_ext_url = False
            for qi in url_inputs:
                v = str(qi.value or "").lower()
                if any(v.endswith(s) for s in HTML_TEXT_SUFFIXES):
                    html_ext_url = True
                elif any(v.endswith(s) for s in PDF_SUFFIXES):
                    pdf_ext_url = True
                elif any(p in v for p in HTML_PATH_PATTERNS):
                    html_ext_url = True
            tgt = (query.target.semantic_type if query.target else "").lower()
            wants_text = (tgt in ("free_text", "text_entry[]")) or html_ext_url
            if html_ext_url:
                if ft.startswith("read_urls_html"):
                    score += 1.5  # strong boost: must overcome get_urls baseline
                elif ft.startswith("get_urls"):
                    score -= 0.8  # strong demote: get_urls returns raw bytes
            elif pdf_ext_url:
                if ft.startswith("read_urls_pdf"):
                    score += 1.5
                elif ft.startswith("get_urls"):
                    score -= 0.5
            elif wants_text:
                if ft.startswith("read_urls_html"):
                    score += 0.6
                elif ft.startswith("get_urls"):
                    score -= 0.3

        # 20.b Format-specific by extension on file_path inputs:
        # input.value endswith .csv → boost *_csv variants (universal §7.3).
        fmt_suffix_map = [
            (".csv", "_csv"), (".pdf", "_pdf"),
            (".xlsx", "_xlsx"), (".xls", "_xlsx"),
            (".html", "_html"), (".htm", "_html"),
            (".tar", "_tar"), (".gz", "_gz"), (".zip", "_zip"),
            (".json", "_json"), (".xml", "_xml"),
        ]
        # B: strong boost se _csv/_pdf/etc tool match input ext + demote tutti
        # tool files non-suffix per query con extension chiara.
        has_ext_signal = False
        matched_suffix = None
        for qi in query.inputs:
            if qi.semantic_type not in ("file_path", "dir_path", "url"):
                continue
            val = str(qi.value or "").lower()
            for ext, suffix in fmt_suffix_map:
                if val.endswith(ext):
                    has_ext_signal = True
                    matched_suffix = suffix
                    break
            if has_ext_signal:
                break
        if has_ext_signal and matched_suffix:
            if matched_suffix in ft:
                score += 1.0  # B: stronger boost
            elif ft.startswith(("read_files", "find_files", "get_files",
                                  "list_files")) and matched_suffix not in ft:
                score -= 0.8  # B: demote generic files quando ext specifica

        # 20.c Transform constraint → tool family suffix match.
        # constraints[].kind=='transform' with key in {sha256,md5,signatures}
        # → compute_signatures family.
        sig_kinds = {"sha256", "md5", "sha1", "checksum", "signature"}
        has_sig_transform = any(
            c.kind == "transform"
            and ((c.key or "").lower() in sig_kinds
                  or (str(c.value or "").lower() in sig_kinds))
            for c in query.constraints
        )
        if has_sig_transform:
            if ft.startswith(("compute_signatures", "compute_files")):
                score += 1.5  # B: strong boost
            else:
                score -= 1.5  # B: strong demote tutto il resto

        # 20.d Recurring/scheduled intent: constraints contain time-recurrence
        # signals → create_tasks family.
        recur_kinds = {"recurring", "interval", "schedule", "trigger", "every"}
        has_recurrence = any(
            (c.kind in recur_kinds)
            or ((c.key or "").lower() in recur_kinds)
            or ("every" in str(c.value or "").lower())
            or ("cron" in str(c.value or "").lower())
            for c in query.constraints
        )
        if has_recurrence:
            if ft.startswith("create_tasks"):
                score += 1.5  # B: strong boost
            else:
                score -= 1.0  # B: demote tutto non-task

        # 20.e Negative search: target shape "scalar"/"list" with intent_verb=find
        # + object events → look for find_events_empty family when constraints
        # indicate availability/free slot semantics.
        if (query.intent_object or "") == "events":
            slot_signals = {"empty", "free", "available", "slot", "proponi",
                             "propose", "duration"}
            wants_empty = any(
                (c.kind in slot_signals)
                or ((c.key or "").lower() in slot_signals)
                or (str(c.value or "").lower() in slot_signals)
                for c in query.constraints
            )
            if wants_empty:
                if "_empty" in ft:
                    score += 1.5  # B: strong
                else:
                    score -= 1.5  # B: demote tutto non-_empty

        # 20.f Single-step mutating with inline operands (verb→tool prefix).
        if len(path.steps) == 1 and verb in {"move", "delete", "send", "share"}:
            has_operands = any(qi.semantic_type in ("file_path", "dir_path",
                                                       "email_address", "url")
                                for qi in query.inputs)
            if has_operands:
                tool_prefix = ft.split("_")[0] if "_" in ft else ""
                if tool_prefix == verb:
                    score += 0.5

        # 20.g ADMIN — privileged system operations.
        # Universal §7.3 signals (parsed structure, not raw_query):
        #   (i)  input value pattern = network-mount URI (UNC, smb, cifs, nfs)
        #   (ii) constraint kind/key in PRIVILEGED_OPS set
        #   (iii) intent_verb=="admin" (system verb riservato §2.2)
        #   (iv) input.semantic_type indica risorsa di sistema privilegiata
        admin_boost = False
        # (i) network mount paths
        for qi in query.inputs:
            v = str(qi.value or "").lower()
            if v.startswith(("\\\\", "smb://", "cifs://", "//", "nfs://")):
                admin_boost = True
                break
        # (ii) constraint indicates privileged op
        PRIVILEGED_KEYS = {
            # mount/storage
            "mount", "unmount", "umount", "fstab",
            # network/firewall/ports
            "port", "ports", "firewall", "iptables", "ufw", "nftables",
            "listen", "tcp_open", "udp_open",
            # permissions / ownership
            "chmod", "chown", "permissions", "owner", "group",
            "setuid", "setgid", "sudo",
            # services/units
            "systemctl", "service", "daemon", "unit",
            # packages (sudo-required install/uninstall)
            "apt_install", "apt_remove", "dpkg",
            # disk/lvm
            "mkfs", "fdisk", "lvm", "raid",
            # routing/dns
            "iproute", "route", "ip_addr", "dns",
        }
        if not admin_boost:
            for c in query.constraints:
                key_l = (c.key or "").lower()
                kind_l = (c.kind or "").lower()
                val_l = str(c.value or "").lower()
                if (key_l in PRIVILEGED_KEYS or kind_l in PRIVILEGED_KEYS
                        or val_l in PRIVILEGED_KEYS):
                    admin_boost = True
                    break
        # (iii) intent_verb is system-verb 'admin' (riservato §2.2)
        if not admin_boost and verb == "admin":
            admin_boost = True
        # (iv) input semantic_type indicates privileged resource
        PRIVILEGED_TYPES = {"network_share", "port_spec", "system_command",
                             "service_unit", "device_path", "mount_point"}
        if not admin_boost:
            for qi in query.inputs:
                if qi.semantic_type in PRIVILEGED_TYPES:
                    admin_boost = True
                    break

        if admin_boost:
            if ft == "admin":
                score += 2.0  # B: very strong boost
            else:
                score -= 2.0  # B: very strong demote (UNC/sudo signal is critical)

    return score  # no floor: negative scores discriminate tied candidates
