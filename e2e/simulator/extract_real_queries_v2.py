"""extract_real_queries_v2.py — Universal §7.3 extraction (no hardcoded LEGACY_MAP).

Derive current vocab from registry + vocab.py (source-of-truth Metnos).
"""
from __future__ import annotations

import glob
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from registry import ExecutorRegistry


def _load_registry_names() -> set[str]:
    reg = ExecutorRegistry(json_dir=Path(__file__).parent / "typing_cache")
    return set(reg.all_names())


def _load_vocab_action_mapping() -> dict[str, list[str]]:
    """Carica ACTION_MAPPING da Metnos vocab.py (source of truth)."""
    try:
        sys.path.insert(0, "/opt/metnos")
        from runtime.vocab import ACTION_MAPPING
        out = {}
        for verb, langs in ACTION_MAPPING.items():
            out[verb] = [w.lower() for lang_words in langs.values() for w in lang_words]
        return out
    except Exception:
        return {}


def derive_current_name(legacy: str, registry_names: set[str],
                         action_mapping: dict) -> str | None:
    """Try to derive current vocab name from legacy tool name.

    Universal §7.3: usa ONLY vocab.py + registry. No hardcoded mapping.

    Strategy:
    1. Direct check: legacy in registry → keep
    2. Pattern decomposition: tokens of legacy → match verb (via ACTION_MAPPING)
       + object → check verb_object in registry
    """
    if legacy in registry_names:
        return legacy
    parts = legacy.lower().split("_")
    # Try all (verb, obj) reorderings
    candidates = []
    for tok_v in parts:
        for tok_o in parts:
            if tok_v == tok_o:
                continue
            # Resolve verb via ACTION_MAPPING synonyms
            resolved_v = None
            for canon_verb, syns in action_mapping.items():
                if tok_v == canon_verb or tok_v in syns:
                    resolved_v = canon_verb
                    break
            if not resolved_v:
                continue
            # Try canonical name
            candidate = f"{resolved_v}_{tok_o}"
            if candidate in registry_names:
                return candidate
            # Try with plural
            if not tok_o.endswith("s"):
                candidate_plural = f"{resolved_v}_{tok_o}s"
                if candidate_plural in registry_names:
                    return candidate_plural
    return None


def extract(min_freq: int = 2) -> list[dict]:
    registry_names = _load_registry_names()
    action_mapping = _load_vocab_action_mapping()
    print(f"Registry size: {len(registry_names)}", file=sys.stderr)
    print(f"Vocab actions: {len(action_mapping)}", file=sys.stderr)

    q_to_paths: dict[str, list[tuple]] = defaultdict(list)
    q_status = defaultdict(set)
    dropped_unknown = 0

    for fn in sorted(glob.glob(os.path.expanduser(
        "~/.local/share/metnos/turns/*.jsonl"))):
        try:
            with open(fn) as f:
                for ln in f:
                    try:
                        d = json.loads(ln)
                        q = (d.get("user_query") or "").strip()
                        if not q or len(q) > 130:
                            continue
                        steps = d.get("steps") or []
                        tools_raw = []
                        for s in steps[:8]:
                            if isinstance(s, dict):
                                t = (s.get("tool") or s.get("chosen_tool")
                                      or s.get("name"))
                                if t and t not in ("final_answer", "undo_last_turn"):
                                    tools_raw.append(t)
                        # Resolve each tool via universal derive
                        tools = []
                        for t in tools_raw:
                            resolved = derive_current_name(
                                t, registry_names, action_mapping)
                            if resolved is None:
                                # Keep request_new_executor literal
                                if t == "request_new_executor":
                                    tools.append(t)
                                else:
                                    dropped_unknown += 1
                                    tools = None
                                    break
                            else:
                                tools.append(resolved)
                        if tools:
                            q_to_paths[q].append(tuple(tools[:3]))
                        q_status[q].add(d.get("final_kind", ""))
                    except Exception:
                        pass
        except Exception:
            pass

    print(f"Dropped queries (unknown tool): {dropped_unknown}", file=sys.stderr)

    out = []
    for q, paths in q_to_paths.items():
        if len(paths) < min_freq:
            continue
        c = Counter(paths)
        top, freq = c.most_common(1)[0]
        if not top:
            continue
        first = top[0]
        status = q_status[q]
        if status == {"loop_break"} or status == {"cap_same_executor"}:
            continue
        accepted = {p[0] for p in c.keys() if p}
        if first == "request_new_executor":
            accepted.add("request_new_executor")
            for p in c.keys():
                if len(p) >= 2 and p[1] and p[1] != "request_new_executor":
                    accepted.add(p[1])
            out.append({
                "query": q, "expected_path": list(top),
                "accepted_first": sorted(accepted),
                "_freq": freq, "_status": ",".join(sorted(status)),
                "_catalog_gap": True,
            })
            continue
        if first.startswith(("read_", "find_", "get_", "list_")):
            for v in ("read", "find", "get", "list"):
                accepted.add(f"{v}_{first.split('_', 1)[1]}" if "_" in first else first)
        out.append({
            "query": q, "expected_path": list(top),
            "accepted_first": sorted(accepted),
            "_freq": freq, "_status": ",".join(sorted(status)),
            "_catalog_gap": False,
        })
    return out


if __name__ == "__main__":
    queries = extract(min_freq=2)
    print(f"Total: {len(queries)}")
    out_path = "/opt/metnos/e2e/simulator/real_queries_v2.json"
    with open(out_path, "w") as f:
        json.dump(queries, f, indent=2, ensure_ascii=False)
    print(f"Written to {out_path}")
