"""graph_search.py — BFS pruned su grafo executor tipizzato.

Input: typing_cache/*.json + query_input + query_target
Output: list[candidate_path] ranked

Standalone, deterministic, zero LLM nel critical path.

§7.9 ideale.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, os.path.dirname(__file__))
from types_semantic import is_compatible, SemanticType

CACHE_DIR = Path("/opt/metnos/tests/simulator/typing_cache")
MAX_DEPTH = 5
MAX_CANDIDATES = 100


@dataclass
class ExecutorNode:
    """Nodo grafo: un executor con I/O tipizzato."""
    name: str
    inputs: dict  # arg_name → {semantic_type, required, default}
    output_type: str
    output_schema: dict = field(default_factory=dict)
    requires_one_of: list = field(default_factory=list)

    @property
    def required_args(self) -> list[str]:
        return [k for k, v in self.inputs.items() if v.get("required")]


@dataclass
class QueryInput:
    """Dati estratti dalla query."""
    intent_verb: str = ""
    intent_object: str = ""
    inputs: list[dict] = field(default_factory=list)
    # Each input: {name, semantic_type, value}
    target_type: str = ""
    target_shape: str = "scalar"


@dataclass
class PathCandidate:
    """Path nel grafo: sequenza di executor con args."""
    steps: list[dict] = field(default_factory=list)
    # Each step: {tool, args, output_type}
    score: float = 0.0


def load_typing() -> dict[str, ExecutorNode]:
    """Carica typing_cache/*.json → registry."""
    registry: dict[str, ExecutorNode] = {}
    for f in sorted(CACHE_DIR.glob("*.json")):
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        node = ExecutorNode(
            name=d.get("name", f.stem),
            inputs=d.get("inputs", {}),
            output_type=d.get("output", {}).get("type", "json_object"),
            output_schema=d.get("output", {}).get("schema", {}),
            requires_one_of=d.get("requires_one_of") or [],
        )
        registry[node.name] = node
    return registry


def find_entry_nodes(query: QueryInput,
                      registry: dict[str, ExecutorNode]) -> list[ExecutorNode]:
    """Match executor che accettano gli input estratti dalla query.

    Criterio: required_args dell'executor coperti da query.inputs (per
    semantic_type). Se executor ha requires_one_of, almeno un membro
    coperto.
    """
    available_types = {inp["semantic_type"] for inp in query.inputs}
    entries: list[ExecutorNode] = []
    for node in registry.values():
        # Match verb-object filter (canonical): nome executor contains verb_object
        if query.intent_verb and query.intent_object:
            if not (query.intent_verb in node.name and query.intent_object in node.name):
                # Soft match: se intent verb è in {find,get,read,list} → accetta producer
                if query.intent_verb in ("find", "get", "read", "list", "compute"):
                    if not any(query.intent_object in node.name for _ in [0]):
                        continue  # filtro se non match object
                else:
                    continue
        # Check required args coverable
        req = node.required_args
        if req:
            satisfied = all(
                any(is_compatible(at, node.inputs[r]["semantic_type"])
                    for at in available_types)
                for r in req
            )
            if not satisfied:
                # Maybe requires_one_of soddisfa?
                if node.requires_one_of:
                    roo_ok = False
                    for group in node.requires_one_of:
                        if any(
                            grpopt in node.inputs and any(
                                is_compatible(at, node.inputs[grpopt]["semantic_type"])
                                for at in available_types
                            )
                            for grpopt in group
                        ):
                            roo_ok = True
                            break
                    if not roo_ok:
                        continue
                else:
                    continue
        entries.append(node)
    return entries


def find_downstream(current_output: str, current_path: list,
                     registry: dict[str, ExecutorNode]) -> list[ExecutorNode]:
    """Nodi che accettano current_output come input.

    Special handling:
      - arg `from_step` o `entries`: accetta QUALSIASI entry list upstream
      - arg `reference_images`: accetta image_path[] o file_entry[] con images
      - arg `paths` con execor che richiede image: downstream must accept current
    """
    used_names = {s["tool"] for s in current_path}
    is_list_output = current_output.endswith("[]") or "entry" in current_output
    out: list[ExecutorNode] = []
    for node in registry.values():
        if node.name in used_names:
            continue
        accepted = False
        for arg_name, arg_meta in node.inputs.items():
            req_type = arg_meta.get("semantic_type", "")
            # Special arg names che accettano any entry list upstream
            if arg_name in ("from_step", "entries") and is_list_output:
                accepted = True
                break
            # Match diretto
            if is_compatible(current_output, req_type):
                accepted = True
                break
            # Projection downstream: current output is entry list with examples field
            if (arg_name == "reference_images"
                and current_output in ("person_entry[]", "image_entry[]",
                                        "file_entry[]")):
                accepted = True
                break
        if accepted:
            out.append(node)
    return out


def satisfies_target(node: ExecutorNode, query: QueryInput) -> bool:
    """Output node è compatibile con target richiesto?"""
    if not query.target_type:
        return True  # no target → accetta qualsiasi terminale
    return is_compatible(node.output_type, query.target_type)


def search(query: QueryInput,
            registry: dict[str, ExecutorNode]) -> list[PathCandidate]:
    """BFS pruned. Returns ranked candidates."""
    entries = find_entry_nodes(query, registry)
    candidates: list[PathCandidate] = []
    queue: list[tuple[ExecutorNode, list[dict]]] = []
    for e in entries:
        first_step = {
            "tool": e.name,
            "args": {},  # args concreti popolati post-search via query.inputs
            "output_type": e.output_type,
        }
        if satisfies_target(e, query):
            candidates.append(PathCandidate(steps=[first_step]))
        queue.append((e, [first_step]))

    while queue and len(candidates) < MAX_CANDIDATES:
        node, path = queue.pop(0)
        if len(path) >= MAX_DEPTH:
            continue
        for downstream in find_downstream(node.output_type, path, registry):
            new_step = {
                "tool": downstream.name,
                "args": {},
                "output_type": downstream.output_type,
            }
            new_path = path + [new_step]
            if satisfies_target(downstream, query):
                candidates.append(PathCandidate(steps=new_path))
            else:
                queue.append((downstream, new_path))

    # Rank
    for c in candidates:
        c.score = _rank_score(c, query)
    candidates.sort(key=lambda c: -c.score)
    return candidates


def _rank_score(candidate: PathCandidate, query: QueryInput) -> float:
    """Score: shorter path + canonical producer + telos heuristic."""
    n = len(candidate.steps)
    score = 1.0 - 0.1 * (n - 1)  # penalize > 1 step lineare
    # Bonus first tool canonical (verb in nome)
    if candidate.steps and query.intent_verb:
        if query.intent_verb in candidate.steps[0]["tool"]:
            score += 0.3
    # Penalty consecutive same tool
    tools = [s["tool"] for s in candidate.steps]
    for i in range(1, len(tools)):
        if tools[i] == tools[i - 1]:
            score -= 0.5
    return max(0.0, score)
