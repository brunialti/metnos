"""resolve_legacy_llm.py — LLM-based legacy tool resolution.

Universal §7.3: nessun hardcoded LEGACY_MAP. L'LLM (Qwen3.5-9B su :8082) mappa
ogni nome legacy → tool corrente nel registry, dato il catalog corrente.

Output cached in legacy_aliases_resolved.json per evitare ricalcoli.
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from registry import ExecutorRegistry

CACHE_FILE = Path(__file__).parent / "legacy_aliases_resolved.json"
LLM_HOST = "127.0.0.1:8082"


SYSTEM_PROMPT = """Sei un esperto Metnos vocabulary. Dato un nome di tool LEGACY (vecchio nome) e la lista di tool ATTUALI, identifica il singolo tool ATTUALE che meglio corrisponde semanticamente al legacy.

Regole:
1. Se nessun tool attuale corrisponde, rispondi "NONE".
2. Altrimenti rispondi UNICAMENTE il nome del tool attuale (es. "get_now").
3. NON spiegare. NON aggiungere prosa.

Esempi mapping comuni:
- legacy "time_read" (resource_verb) → current "get_now" (verb_resource, dove "now" è canonico per timestamp)
- legacy "fs_read" (resource_verb) → current "read_files"
- legacy "web_fetch" → current "get_urls"
- legacy "list_dir" → current "list_dirs"
"""


def _call_llm(legacy: str, current_names: list[str]) -> str:
    user = f"Tool LEGACY: {legacy}\n\nTool ATTUALI disponibili ({len(current_names)}):\n" + ", ".join(sorted(current_names))
    body = json.dumps({
        "model": "qwen3.5-9b",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        "max_tokens": 30,
        "temperature": 0.0,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }).encode()
    req = urllib.request.Request(
        f"http://{LLM_HOST}/v1/chat/completions",
        data=body, headers={"Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            ans = data["choices"][0]["message"]["content"].strip()
            # Strip <think> blocks
            import re
            ans = re.sub(r"<think>[\s\S]*?</think>", "", ans, flags=re.IGNORECASE).strip()
            # Take first non-empty line, strip quotes/spaces
            ans = ans.split("\n")[0].strip().strip('"').strip("'").strip("`").strip()
            return ans
    except Exception as ex:
        return "ERROR"


def resolve_all(legacy_names: list[str]) -> dict[str, str | None]:
    """Resolve every legacy name → current. Cache results."""
    cache = {}
    if CACHE_FILE.exists():
        try:
            cache = json.loads(CACHE_FILE.read_text())
        except Exception:
            cache = {}
    reg = ExecutorRegistry(json_dir=Path(__file__).parent / "typing_cache")
    current = sorted(reg.all_names())
    out = {}
    for legacy in legacy_names:
        if legacy in cache:
            out[legacy] = cache[legacy]
            continue
        if legacy in current:
            out[legacy] = legacy
            cache[legacy] = legacy
            continue
        ans = _call_llm(legacy, current)
        if ans == "NONE" or ans == "ERROR":
            resolved = None
        elif ans in current:
            resolved = ans
        else:
            # LLM hallucinated → try fuzzy match
            for n in current:
                if n.lower().startswith(ans.lower()[:6]):
                    resolved = n
                    break
            else:
                resolved = None
        out[legacy] = resolved
        cache[legacy] = resolved
        print(f"  {legacy} → {resolved}")
    CACHE_FILE.write_text(json.dumps(cache, indent=2, ensure_ascii=False))
    return out


def collect_legacy_names() -> set[str]:
    """Scan all turn logs, collect tool names NOT in current registry."""
    import glob, os
    reg = ExecutorRegistry(json_dir=Path(__file__).parent / "typing_cache")
    current = set(reg.all_names())
    legacy = set()
    for fn in sorted(glob.glob(os.path.expanduser(
        "~/.local/share/metnos/turns/*.jsonl"))):
        try:
            with open(fn) as f:
                for ln in f:
                    try:
                        d = json.loads(ln)
                        for s in (d.get("steps") or [])[:8]:
                            if isinstance(s, dict):
                                t = (s.get("tool") or s.get("chosen_tool")
                                      or s.get("name"))
                                if t and t not in ("final_answer", "undo_last_turn",
                                                    "request_new_executor"):
                                    if t not in current:
                                        legacy.add(t)
                    except Exception:
                        pass
        except Exception:
            pass
    return legacy


if __name__ == "__main__":
    legacy = collect_legacy_names()
    print(f"Found {len(legacy)} unique legacy tool names:")
    for n in sorted(legacy):
        print(f"  - {n}")
    print()
    print(f"Resolving via LLM (Qwen3.5-9B :8082)...")
    mapping = resolve_all(sorted(legacy))
    print()
    print("Summary:")
    print(f"  Resolved: {sum(1 for v in mapping.values() if v)}")
    print(f"  Unresolved: {sum(1 for v in mapping.values() if not v)}")
