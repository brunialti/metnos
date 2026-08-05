"""extract_typing.py — build executor_typing.json da manifest TOML.

Stage 1: heuristic args name + structural type → semantic type
Stage 2: LLM enrichment per output schema + args ambigui

Output: tests/simulator/typing_cache/<executor_name>.json
Cache busted su manifest mtime change.

Standalone — NON tocca executor/ in produzione.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import tomllib
from pathlib import Path
from typing import Optional

sys.path.insert(0, os.path.dirname(__file__))
from types_semantic import (
    infer_semantic_type, ATOMIC_TYPES, ENTRY_SHAPES,
)

EXEC_DIR = Path("/opt/metnos/executors")
CACHE_DIR = Path("/opt/metnos/tests/simulator/typing_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def load_manifest(exec_path: Path) -> Optional[dict]:
    mt = exec_path / "manifest.toml"
    if not mt.exists():
        return None
    try:
        return tomllib.loads(mt.read_text())
    except Exception as ex:
        print(f"  ✗ {exec_path.name}: parse error {ex}")
        return None


def stage1_heuristic(manifest: dict) -> dict:
    """Inferenza args via heuristic name+type. Output partial typing.

    Returns dict con:
      inputs: {arg_name: {type, required, default}}
      output: {type, schema} (heuristic best-effort, può essere overrided da LLM stage2)
      unresolved_args: [arg_names che heuristic non ha risolto]
    """
    args = manifest.get("args", {})
    props = args.get("properties", {})
    required = args.get("required") or []
    roo = args.get("requires_one_of") or []

    inputs = {}
    unresolved = []
    for arg_name, decl in props.items():
        st = decl.get("type", "string")
        semantic = infer_semantic_type(arg_name, st)
        item = {
            "structural_type": st,
            "required": arg_name in required,
            "default": decl.get("default"),
        }
        if semantic:
            item["semantic_type"] = semantic
        else:
            unresolved.append(arg_name)
            item["semantic_type"] = "free_text"  # fallback
        inputs[arg_name] = item

    return {
        "inputs": inputs,
        "requires_one_of": roo,
        "output": _stage1_output_heuristic(manifest),
        "unresolved_args": unresolved,
    }


def _stage1_output_heuristic(manifest: dict) -> dict:
    """Inferenza output da output.schema_inline / description prose."""
    output = manifest.get("output", {})
    schema_inline = output.get("schema_inline", "") or ""
    name = manifest.get("name", "")

    # Heuristic per executor producer canonical
    # (find_files/read_messages/get_events/find_persons_indices/...)
    if name.startswith("find_files") or name == "find_files":
        return {"type": "file_entry[]", "may_be_truncated": True}
    if name.startswith("find_dirs") or name == "find_dirs":
        return {"type": "dir_entry[]"}
    if name in ("read_messages", "find_messages"):
        return {"type": "message_entry[]", "may_be_truncated": True}
    if name == "read_events":
        return {"type": "event_entry[]"}
    if name == "get_persons":
        return {"type": "person_entry[]"}
    if name == "find_persons_indices":
        return {"type": "image_entry[]", "may_be_truncated": True}
    if name == "find_images_indices":
        return {"type": "image_entry[]", "may_be_truncated": True}
    if name == "find_images_web":
        return {"type": "url_entry[]"}
    if name == "find_urls":
        return {"type": "url_entry[]"}
    if name in ("read_urls_html", "read_urls_pdf"):
        return {"type": "free_text"}  # content extracted
    if name == "compute_entries":
        return {"type": "scalar_metric"}
    if name in ("describe_entries", "classify_entries"):
        return {"type": "free_text"}
    if name in ("filter_entries", "sort_entries", "group_entries"):
        return {"type": "entry_list"}  # generic: same shape as input
    if name == "get_processes":
        return {"type": "process_entry[]"}
    if name in ("get_now", "get_location"):
        return {"type": "scalar_metric"}  # singolo valore
    if name in ("list_tasks", "read_tasks"):
        return {"type": "task_entry[]"}
    if name.startswith(("send_", "move_", "delete_", "write_", "create_",
                         "set_", "change_", "share_")):
        return {"type": "scalar_metric"}  # ok_count + side-effect
    # Generic fallback
    return {"type": "json_object", "needs_llm": True}


def llm_call_wise(system: str, user: str, max_tokens: int = 1024) -> str:
    """LLM call via llama-server diretto (no router, no engine).

    enable_thinking=False: per extraction strutturata non serve CoT,
    risparmia tutti i token Gemma reasoning_budget per il content.
    """
    import urllib.request
    body = json.dumps({
        "model": "gemma-4-26b",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
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


def stage2_llm_enrich(manifest: dict, stage1: dict) -> dict:
    """LLM enrichment per:
      - args unresolved (semantic_type ambiguous)
      - output schema dove heuristic ha needs_llm=True
    """
    desc_it = manifest.get("description", {}).get("it", "") or ""
    name = manifest.get("name", "")
    # v2: SEMPRE run LLM Stage 2 per estrarre role + consumes (informazione
    # critica per constraint propagation in graph search). Anche se semantic
    # types già risolti via heuristic, role/consumes mancano.
    pass  # rimosso early-return needs_llm

    # Build prompt
    args_text = "\n".join(
        f"  - {n}: structural={d['structural_type']} required={d['required']}"
        f" desc=\"{(manifest.get('args', {}).get('properties', {}).get(n, {}).get('description', {}).get('it', '') or '')[:120]}\""
        for n, d in stage1["inputs"].items()
    )
    output_inline = manifest.get("output", {}).get("schema_inline", "")
    system = """Sei un type extractor. Dato un executor manifest TOML, ritorna SOLO JSON strict che descrive I/O tipizzato semanticamente + ruoli/constraint consumption.

Vocabolario tipi semantici (chiuso):
  Atomici: file_path, dir_path, image_path, pdf_path, audio_path, video_path, text_path,
           url, email_address, phone, person_name, slug, account_name,
           time_window, iso_timestamp, duration_secs,
           glob_pattern, regex_pattern,
           count, percent, size_bytes, scalar_metric,
           free_text, json_object, bool, name
  Compositi (list with []): file_entry[], image_entry[], message_entry[], event_entry[],
           person_entry[], url_entry[], dir_entry[], process_entry[], task_entry[]

Per ogni input dichiara ROLE:
  filter  → arg che VINCOLA l'output (es. name="Matteo" filtra entries)
  source  → arg che LOCALIZZA dove cercare (es. base_path WHERE, non WHAT)
  meta    → parametro tecnico (timeout, max_results, batch_size)
  piping  → from_step, entries (data flow upstream)

Per output, dichiara CONSUMES: lista di constraint kinds che questo executor consuma.
  Esempi:
    find_persons_indices.consumes = ["filter:person_name"] (perché name filtra output)
    filter_entries.consumes = ["filter:any"] (filter generico via where_field)
    compute_entries.consumes = ["aggregate:count"] (op=count)
    sort_entries.consumes = ["sort:any"]

Output JSON strict:
{
  "inputs": {
    "<arg_name>": {"semantic_type": "...", "role": "filter|source|meta|piping", "format_hint": "..."}
  },
  "output": {
    "type": "...",
    "schema": {"<field>": "<semantic_type>", ...}
  },
  "consumes": ["filter:person_name", "filter:time_window", ...],
  "propagates_constraints": false
}
NIENTE prosa, NIENTE think blocks."""
    user = f"""EXECUTOR: {name}
DESCRIPTION: {desc_it[:600]}

ARGS:
{args_text}

OUTPUT INLINE: {output_inline[:300]}

Estrai semantic_type per ogni input + output type/schema."""
    raw = llm_call_wise(system, user, max_tokens=1024)
    # Parse JSON
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return stage1
    try:
        enriched = json.loads(m.group(0))
    except json.JSONDecodeError:
        return stage1
    # Merge enriched into stage1
    for arg, meta in (enriched.get("inputs") or {}).items():
        if arg in stage1["inputs"]:
            if meta.get("semantic_type"):
                stage1["inputs"][arg]["semantic_type"] = meta["semantic_type"]
            if meta.get("role"):
                stage1["inputs"][arg]["role"] = meta["role"]
            if meta.get("format_hint"):
                stage1["inputs"][arg]["format_hint"] = meta["format_hint"]
    if enriched.get("output"):
        stage1["output"] = enriched["output"]
        stage1["output"].pop("needs_llm", None)
    if enriched.get("consumes"):
        stage1["consumes"] = enriched["consumes"]
    if "propagates_constraints" in enriched:
        stage1["propagates_constraints"] = enriched["propagates_constraints"]
    return stage1


def build_for_executor(exec_path: Path, *, use_llm: bool = True) -> Optional[dict]:
    manifest = load_manifest(exec_path)
    if not manifest:
        return None
    name = manifest.get("name") or exec_path.name
    s1 = stage1_heuristic(manifest)
    # v2: SEMPRE LLM Stage 2 per role + consumes (anche se semantic_types
    # già risolti da heuristic). Critico per constraint propagation.
    if use_llm:
        s1 = stage2_llm_enrich(manifest, s1)
    return {
        "name": name,
        "manifest_mtime": int((exec_path / "manifest.toml").stat().st_mtime),
        **s1,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-llm", action="store_true",
                         help="Solo heuristic stage 1, no LLM")
    parser.add_argument("--filter", help="Match nome executor (regex)")
    parser.add_argument("--force", action="store_true",
                         help="Forza rebuild cache anche se aggiornata")
    args = parser.parse_args()

    pattern = re.compile(args.filter) if args.filter else None
    total = 0
    new = 0
    skipped = 0
    failed = 0
    stage2_calls = 0
    t0 = time.time()

    for exec_path in sorted(EXEC_DIR.iterdir()):
        if not exec_path.is_dir():
            continue
        if exec_path.name.startswith((".", "_", "skills")):
            continue
        if pattern and not pattern.search(exec_path.name):
            continue
        total += 1
        cache_path = CACHE_DIR / f"{exec_path.name}.json"
        mt = exec_path / "manifest.toml"
        if not mt.exists():
            failed += 1
            continue
        # Check cache freshness
        if cache_path.exists() and not args.force:
            try:
                cached = json.loads(cache_path.read_text())
                if cached.get("manifest_mtime") == int(mt.stat().st_mtime):
                    skipped += 1
                    continue
            except Exception:
                pass

        typing = build_for_executor(exec_path, use_llm=not args.no_llm)
        if typing is None:
            failed += 1
            continue
        if typing.get("unresolved_args") or "needs_llm" in str(typing):
            stage2_calls += 1
        cache_path.write_text(json.dumps(typing, indent=2, ensure_ascii=False))
        new += 1
        print(f"  ✓ {exec_path.name}: "
               f"inputs={len(typing['inputs'])} "
               f"out={typing['output'].get('type','?')}"
               f"{' [LLM]' if 'needs_llm' in str(typing) else ''}",
               flush=True)

    elapsed = time.time() - t0
    print(f"\n=== BUILD COMPLETE ===")
    print(f"Total scanned: {total}")
    print(f"New/rebuilt: {new}")
    print(f"Cached (skipped): {skipped}")
    print(f"Failed: {failed}")
    print(f"Stage2 LLM calls: ~{stage2_calls}")
    print(f"Elapsed: {elapsed:.1f}s")
    print(f"Cache dir: {CACHE_DIR}")


if __name__ == "__main__":
    main()
