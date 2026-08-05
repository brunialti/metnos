"""extract_real_queries.py — extract real Metnos queries from turn logs.

Map legacy tool names to current §2.2 vocab, output as TEST_QUERIES format.
"""
from __future__ import annotations

import glob
import json
import os
from collections import Counter, defaultdict


# Legacy → current vocab mapping
# Universal §7.3: LLM-resolved mapping cached. Populate via resolve_legacy_llm.py.
# Zero hardcoded names — LLM identifies semantic equivalents from current registry.
_LEGACY_CACHE_FILE = os.path.join(os.path.dirname(__file__),
                                     "legacy_aliases_resolved.json")
_LEGACY_MAP_CACHE = None


def _load_legacy_map():
    global _LEGACY_MAP_CACHE
    if _LEGACY_MAP_CACHE is None:
        try:
            with open(_LEGACY_CACHE_FILE) as f:
                _LEGACY_MAP_CACHE = json.load(f)
        except Exception:
            _LEGACY_MAP_CACHE = {}
    return _LEGACY_MAP_CACHE


def normalize(tool: str) -> str | None:
    """Map legacy tool name → current. None if unmappable (catalog gap)."""
    cache = _load_legacy_map()
    if tool in cache:
        return cache[tool]  # may be None if LLM said NONE
    return tool  # not in cache = assume current (will be verified downstream)


def extract(min_freq: int = 2) -> list[dict]:
    q_to_paths: dict[str, list[tuple]] = defaultdict(list)
    q_status = defaultdict(set)
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
                        tools = []
                        bad = False
                        for s in steps[:8]:
                            if isinstance(s, dict):
                                t = (s.get("tool") or s.get("chosen_tool")
                                      or s.get("name"))
                                if t and t not in ("final_answer", "undo_last_turn"):
                                    norm = normalize(t)
                                    if norm is None and t != "request_new_executor":
                                        bad = True; break
                                    tools.append(norm if norm else t)
                        if tools and not bad:
                            q_to_paths[q].append(tuple(tools[:3]))
                        q_status[q].add(d.get("final_kind", ""))
                    except Exception:
                        pass
        except Exception:
            pass

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
        # Skip queries that always loop_break (always failed)
        if status == {"loop_break"} or status == {"cap_same_executor"}:
            continue
        # accepted_first = top first step + nearby alternatives
        accepted = {p[0] for p in c.keys() if p}
        # Catalog gap: queries that used request_new_executor as primary
        # — accept request_new_executor as legitimate response.
        if first == "request_new_executor":
            accepted.add("request_new_executor")
            # Also accept secondary tools observed (may be partially viable)
            for p in c.keys():
                if len(p) >= 2 and p[1] and p[1] != "request_new_executor":
                    accepted.add(p[1])
            out.append({
                "query": q,
                "expected_path": list(top),
                "accepted_first": sorted(accepted),
                "_freq": freq,
                "_status": ",".join(sorted(status)),
                "_catalog_gap": True,
            })
            continue
        if first.startswith(("read_", "find_", "get_", "list_")):
            for v in ("read", "find", "get", "list"):
                accepted.add(f"{v}_{first.split('_', 1)[1]}" if "_" in first else first)
        # Universal §7.3 semantic equivalence clusters:
        if "_places" in first or "_location" in first:
            accepted.update({"get_location", "get_places", "find_places",
                              "read_places", "list_places"})
        if "_persons" in first or "_contacts" in first:
            for base in ("persons", "contacts"):
                for v in ("read", "find", "get", "list"):
                    accepted.add(f"{v}_{base}")
        # urls cluster: read_urls_html ≡ get_urls (entrambi fetchano contenuto web)
        if "_urls" in first or first == "read_urls_html" or first == "read_urls_pdf":
            accepted.update({"read_urls_html", "read_urls_pdf", "get_urls",
                              "find_urls"})
        # tasks cluster: list_tasks ≡ read_tasks ≡ find_tasks (semantic equiv)
        if "_tasks" in first and first.split("_")[0] in ("list", "read", "find", "get"):
            for v in ("list", "read", "find", "get"):
                accepted.add(f"{v}_tasks")
        out.append({
            "query": q,
            "expected_path": list(top),
            "accepted_first": sorted(accepted),
            "_freq": freq,
            "_status": ",".join(sorted(status)),
            "_catalog_gap": False,
        })
    return out


if __name__ == "__main__":
    queries = extract(min_freq=2)
    print(f"Total: {len(queries)}")
    for q in queries[:5]:
        print(json.dumps(q, ensure_ascii=False))
    out_path = "/opt/metnos/tests/simulator/real_queries.json"
    with open(out_path, "w") as f:
        json.dump(queries, f, indent=2, ensure_ascii=False)
    print(f"Written to {out_path}")
