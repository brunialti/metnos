#!/usr/bin/env python3
"""Offline probe for deriving atom ids and edge output slots.

No network and no live candidate output.  The compact form removes atom_id
from every atom and output_index from every edge.  Array order supplies atom
identity; the frozen relation signature and the source binding supply the
output slot.  A source ordinal is retained because it carries real graph
semantics and is not generally derivable.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any


RUNNER = Path("/tmp/metnos_v2641_typed_phase1_runner.py")
FIXTURES = Path("/tmp/metnos_v264_independent_graph_probe.py")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


v = load("metnos_v2641_atom_probe_target", RUNNER)
p = load("metnos_v2641_atom_probe_fixtures", FIXTURES)


def output_slot(atom: dict[str, Any]) -> int:
    output_kinds = {"output"} if atom["atom_kind"] == "dependency" else {"unknown"}
    matches = [
        index for index, binding in enumerate(atom["arguments"], 1)
        if binding["kind"] in output_kinds
    ]
    if len(matches) != 1:
        raise ValueError("source atom does not expose exactly one derivable output")
    return matches[0]


def compact_atoms(atoms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compacted = copy.deepcopy(atoms)
    by_id = {atom["atom_id"]: atom for atom in atoms}
    if [atom["atom_id"] for atom in atoms] != list(range(1, len(atoms) + 1)):
        raise ValueError("array is not already topological")
    for atom in compacted:
        atom.pop("atom_id")
        for binding in atom["arguments"]:
            if binding["kind"] != "from_atom_output":
                continue
            source_ordinal = binding.pop("atom_id")
            declared_index = binding.pop("output_index")
            if declared_index != output_slot(by_id[source_ordinal]):
                raise ValueError("declared output index is not registry/binding-derived")
            binding["kind"] = "from_prior_atom"
            binding["source_ordinal"] = source_ordinal
    return compacted


def expand_atoms(atoms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    expanded = copy.deepcopy(atoms)
    for ordinal, atom in enumerate(expanded, 1):
        atom["atom_id"] = ordinal
    for ordinal, atom in enumerate(expanded, 1):
        for binding in atom["arguments"]:
            if binding["kind"] != "from_prior_atom":
                continue
            source_ordinal = binding.pop("source_ordinal")
            if not 1 <= source_ordinal < ordinal:
                raise ValueError("source ordinal is not a prior array position")
            binding["kind"] = "from_atom_output"
            binding["atom_id"] = source_ordinal
            binding["output_index"] = output_slot(expanded[source_ordinal - 1])
    return expanded


def map_graphs(frame: dict[str, Any], mapper: Any) -> dict[str, Any]:
    changed = copy.deepcopy(frame)
    if changed["status"] == "supported":
        changed["atoms"] = mapper(changed["atoms"])
    elif changed["status"] == "typed_ambiguity":
        for alternative in changed["alternatives"]:
            alternative["atoms"] = mapper(alternative["atoms"])
    return changed


def valid(frame: dict[str, Any]) -> bool:
    return v.validate_frame(frame, p.SEGMENTS)["valid"]


def erase_source_pointer(frame: dict[str, Any]) -> dict[str, Any]:
    changed = copy.deepcopy(frame)
    graphs = [changed.get("atoms", [])]
    graphs.extend(item["atoms"] for item in changed.get("alternatives", []))
    for atoms in graphs:
        for atom in atoms:
            for binding in atom["arguments"]:
                if binding["kind"] == "from_prior_atom":
                    binding.pop("source_ordinal")
    return changed


def encoded_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


def run() -> dict[str, Any]:
    direct = p.frame(p.direct_atom())
    dependency = p.frame(p.location_dependency(), p.near_projection())
    fanout = p.frame(
        p.location_dependency(), p.near_projection(),
        p.send_projection(3, 2, 4, 1), p.move_projection(4, 3, 7, 1),
    )
    direct_feed = p.frame(p.direct_atom(), p.send_projection(2, 2, 4, 1))
    quoted_main = p.frame(
        p.location_dependency(),
        p.send_projection(2, 1, 1, 1, role="quoted_content", speech="none"),
        p.direct_atom(3, 2, 4),
    )
    independent = p.frame(p.filesystem_query(1, 1, 1), p.runtime_query(2, 2, 4))
    mixed = p.frame(p.direct_atom(), unsupported=[p.unsupported_clause(2, 4)])
    ambiguity = p.ambiguity(
        [p.polar_location(role="main_assertion", speech="assertion"), p.move_projection(2, 2, 4)],
        [p.polar_location(), p.move_projection(2, 2, 4)],
    )
    second_location = p.projection(
        2, 2, 4, 6, "spatial.located_at",
        [p.bound("person.explicit", 4), p.unknown(), p.bound("time.current", 4)],
    )
    source_one = p.frame(
        p.direct_atom(), second_location, p.send_projection(3, 3, 7, 1),
    )
    source_two = copy.deepcopy(source_one)
    source_two["atoms"][2]["arguments"][0]["atom_id"] = 2

    cases = {
        "direct": direct,
        "dependency": dependency,
        "fanout_multi_action": fanout,
        "projection_output_consumed": direct_feed,
        "quoted_plus_main": quoted_main,
        "independent_multi_domain": independent,
        "mixed_supported_unsupported": mixed,
        "typed_ambiguity": ambiguity,
        "two_compatible_sources": source_one,
    }
    tests: list[dict[str, Any]] = []
    total_full = 0
    total_compact = 0
    for name, frame in cases.items():
        compact = map_graphs(frame, compact_atoms)
        expanded = map_graphs(compact, expand_atoms)
        full_size = encoded_size(frame)
        compact_size = encoded_size(compact)
        total_full += full_size
        total_compact += compact_size
        tests.append({
            "id": f"roundtrip:{name}",
            "pass": valid(frame) and expanded == frame and valid(expanded),
            "full_bytes": full_size,
            "compact_bytes": compact_size,
        })

    compact_one = map_graphs(source_one, compact_atoms)
    compact_two = map_graphs(source_two, compact_atoms)
    pointer_is_semantic = (
        valid(source_one)
        and valid(source_two)
        and source_one != source_two
        and compact_one != compact_two
        and erase_source_pointer(compact_one) == erase_source_pointer(compact_two)
    )
    tests.append({
        "id": "source_pointer_is_irreducible_semantics",
        "pass": pointer_is_semantic,
        "two_valid_graphs_differ_only_by_source": pointer_is_semantic,
    })

    bad_slot = copy.deepcopy(dependency)
    bad_slot["atoms"][1]["arguments"][1]["output_index"] = 1
    rejected = False
    try:
        map_graphs(bad_slot, compact_atoms)
    except ValueError:
        rejected = True
    tests.append({
        "id": "wrong_output_index_rejected_before_compaction",
        "pass": rejected,
        "fail_closed": rejected,
    })

    return {
        "version": "metnos.v26.4.1-atom-edge-derivation-probe/0.1",
        "network_calls": 0,
        "candidate_outputs_read": 0,
        "runner_sha256": sha(RUNNER),
        "fixtures_sha256": sha(FIXTURES),
        "summary": {
            "tests": len(tests),
            "passed": sum(item["pass"] for item in tests),
            "failed": sum(not item["pass"] for item in tests),
            "full_bytes": total_full,
            "compact_bytes": total_compact,
            "byte_reduction_percent": round(100 * (total_full - total_compact) / total_full, 2),
        },
        "conclusion": {
            "atom_id_field": "derive from topological array position",
            "edge_output_index": "derive from source relation and its unique output/unknown binding",
            "edge_source": "retain one prior-atom pointer; two valid graphs can otherwise collapse to the same representation",
        },
        "tests": tests,
    }


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, sort_keys=True))
