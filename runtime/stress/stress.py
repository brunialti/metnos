#!/usr/bin/env python3
"""
stress.py — verifica i limiti dimensionali delle scelte implementative del POC v1.1.

Quattro assi:
    D-cat   numero di executor nel catalogo
    D-tools numero di tools passati all'LLM (collegato a D-cat tramite top-K)
    D-step  profondita' del multistep (history accumulation)
    D-obs   dimensione di una singola observation

Per ogni asse: misuriamo latenza/quality a piu' livelli, individuiamo cliffs,
e per ogni cliff annotiamo: alternativa architetturale, alternativa adattiva
"""
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from generate_synthetic import generate as gen_synthetic
from llm_provider import OllamaProvider
from loader import load_catalog
from prefilter import rank


REPORT = []


def section(name):
    print(f"\n{'='*60}\n=== {name}\n{'='*60}")
    REPORT.append({"section": name, "rows": []})


def row(**kwargs):
    REPORT[-1]["rows"].append(kwargs)
    print("  " + "  ".join(f"{k}={v}" for k, v in kwargs.items()))


# =========================================================================
# D-cat — Scaling del catalogo
# =========================================================================

def stress_d_cat():
    section("D-cat — scaling catalogo: prefilter latency + LLM accuracy con top-K=10")
    sizes = [10, 30, 100, 300]
    base_dir = "/tmp/metnos_stress_executors"
    real_executors_dir = str(Path(__file__).resolve().parents[2] / "executors")

    provider = OllamaProvider(model="qwen3:8b", think=False)

    # Includere sempre i 4 executor reali (per avere ground truth: get_now/read_files/etc)
    for n_synth in sizes:
        # Setup: real + n synthetic
        gen_synthetic(base_dir, n_synth)
        all_dir = Path(tempfile.mkdtemp(prefix="metnos_cat_"))
        for src in Path(real_executors_dir).iterdir():
            if src.is_dir():
                shutil.copytree(src, all_dir / src.name)
        for src in Path(base_dir).iterdir():
            if src.is_dir():
                shutil.copytree(src, all_dir / src.name)

        # Carica catalogo
        t0 = time.time()
        cat = load_catalog(executors_dir=all_dir)
        load_ms = int((time.time() - t0) * 1000)
        cat_size = len(cat)

        # Pre-filter su query nota — il giusto e' get_now
        query = "che ora è a Tokyo?"
        t0 = time.time()
        candidates = rank(query, cat, k=10)
        prefilter_ms = int((time.time() - t0) * 1000)
        top1 = candidates[0].name if candidates else "(none)"
        top1_correct = (top1 == "get_now")

        # LLM call con top-K=10 come tools
        from agent_runtime import render_tools_for_provider, PLANNER_SYSTEM_NATIVE
        tools = render_tools_for_provider(candidates)
        t0 = time.time()
        try:
            r = provider.chat_with_tools(PLANNER_SYSTEM_NATIVE, query, tools, max_tokens=200, temperature=0)
            llm_ms = int((time.time() - t0) * 1000)
            tc = r.tool_calls[0] if r.tool_calls else None
            llm_choice = tc.name if tc else "(no tool_call)"
            llm_correct = (llm_choice == "get_now")
            in_toks = r.in_tokens
            out_toks = r.out_tokens
        except Exception as e:
            llm_ms = -1; llm_choice = f"ERR:{e}"; llm_correct = False; in_toks = 0; out_toks = 0

        row(N=cat_size, load_ms=load_ms, prefilter_ms=prefilter_ms,
            top1=top1, top1_correct=top1_correct,
            llm_ms=llm_ms, llm_in=in_toks, llm_choice=llm_choice, llm_correct=llm_correct)

        shutil.rmtree(all_dir)


# =========================================================================
# D-tools — Numero tools passati all'LLM (a parita' di catalogo top-K)
# =========================================================================

def stress_d_tools():
    section("D-tools — N tools passati all'LLM (Qwen3:8b)")
    base_dir = "/tmp/metnos_stress_executors"
    gen_synthetic(base_dir, 100)
    all_dir = Path(tempfile.mkdtemp(prefix="metnos_tools_"))
    for src in Path(__file__).resolve().parents[2] / "executors".iterdir():
        if src.is_dir():
            shutil.copytree(src, all_dir / src.name)
    for src in Path(base_dir).iterdir():
        if src.is_dir():
            shutil.copytree(src, all_dir / src.name)
    cat = load_catalog(executors_dir=all_dir)

    provider = OllamaProvider(model="qwen3:8b", think=False)
    from agent_runtime import render_tools_for_provider, PLANNER_SYSTEM_NATIVE

    # Per ogni K, prendiamo top-K dal pre-filter (cosi' get_now e' incluso)
    query = "che ora è a Tokyo?"
    for K in [4, 10, 20, 40, 80]:
        candidates = rank(query, cat, k=K)
        # Assicuriamoci che get_now sia tra i candidati
        names = [e.name for e in candidates]
        if "get_now" not in names:
            time_ex = cat.get("get_now")
            if time_ex:
                candidates.append(time_ex)
        tools = render_tools_for_provider(candidates)
        t0 = time.time()
        try:
            r = provider.chat_with_tools(PLANNER_SYSTEM_NATIVE, query, tools, max_tokens=200, temperature=0)
            llm_ms = int((time.time() - t0) * 1000)
            tc = r.tool_calls[0] if r.tool_calls else None
            choice = tc.name if tc else "(none)"
            correct = (choice == "get_now")
            in_toks = r.in_tokens
        except Exception as e:
            llm_ms = -1; choice = f"ERR:{e}"; correct = False; in_toks = 0
        row(K=len(tools), llm_in_toks=in_toks, llm_ms=llm_ms, choice=choice, correct=correct)

    shutil.rmtree(all_dir)


# =========================================================================
# D-step — Profondita' multistep, history accumulation
# =========================================================================

def stress_d_step():
    section("D-step — history injection: profondita' del multistep")
    cat = load_catalog()
    candidates = list(cat)
    provider = OllamaProvider(model="qwen3:8b", think=False)
    from agent_runtime import render_tools_for_provider, PLANNER_SYSTEM_NATIVE

    tools = render_tools_for_provider(candidates)
    user_query = "leggi /tmp/metnos_stress_demo.txt"

    # Crea il file
    Path("/tmp/metnos_stress_demo.txt").write_text("contenuto demo")

    for depth in [0, 3, 6, 10]:
        # Sintetizza una history di profondita' depth con observation realistiche
        history = []
        for i in range(depth):
            tcid = f"call_synth_{i}"
            history.append({
                "role": "assistant",
                "tool_calls": [{"id": tcid, "type": "function",
                                "function": {"name": "read_files", "arguments": {"path": f"/tmp/foo_{i}.txt"}}}]
            })
            obs = {"ok": True, "content": f"observation di prova {i} " * 30, "metadata": {"path": f"/tmp/foo_{i}.txt", "bytes": 600}}
            obs_str = json.dumps(obs, ensure_ascii=False)
            if len(obs_str) > 1500:
                obs_str = obs_str[:1500] + "...[troncato]"
            history.append({"role": "tool", "tool_call_id": tcid, "name": "read_files", "content": obs_str})

        prompt_size_chars = sum(len(json.dumps(m, ensure_ascii=False)) for m in history)

        t0 = time.time()
        try:
            r = provider.chat_with_tools(PLANNER_SYSTEM_NATIVE, user_query, tools, history=history, max_tokens=300, temperature=0)
            llm_ms = int((time.time() - t0) * 1000)
            in_toks = r.in_tokens
            out_toks = r.out_tokens
            has_tc = bool(r.tool_calls)
            choice = r.tool_calls[0].name if has_tc else "(text)"
            correct = (choice == "read_files")
        except Exception as e:
            llm_ms = -1; in_toks = 0; out_toks = 0; choice = f"ERR:{e}"; correct = False

        row(depth=depth, hist_chars=prompt_size_chars, llm_in_toks=in_toks, llm_out_toks=out_toks,
            llm_ms=llm_ms, choice=choice, correct=correct)

    Path("/tmp/metnos_stress_demo.txt").unlink()


# =========================================================================
# D-obs — Dimensione di una singola observation
# =========================================================================

def stress_d_obs():
    section("D-obs — read_files su file di varie dimensioni")
    cat = load_catalog()
    read_files = cat.get("read_files")

    # /tmp/** e' nel hint
    for size_kb in [1, 100, 1024, 5120]:
        path = "/tmp/metnos_stress_obs.txt"
        # Genera contenuto di dim approssimata (line-base)
        line = "x" * 100 + "\n"
        n_lines = (size_kb * 1024) // len(line) + 1
        with open(path, "w") as f:
            for _ in range(n_lines):
                f.write(line)
        actual_size = os.path.getsize(path)

        # Invoca read_files
        from agent_runtime import invoke_executor
        t0 = time.time()
        result = invoke_executor(read_files, {"path": path}, timeout_s=60)
        exec_ms = int((time.time() - t0) * 1000)

        ok = result.get("ok")
        content_len = len(result.get("content", "")) if ok else 0

        # Ora simulo: se questa observation va in history, quanto pesa?
        obs_str = json.dumps(result, ensure_ascii=False)
        # Truncamento a 1500 char (default agent_runtime)
        truncated = (len(obs_str) > 1500)
        truncated_size = min(len(obs_str), 1500)

        row(size_kb=size_kb, actual_bytes=actual_size, exec_ms=exec_ms,
            ok=ok, content_chars=content_len, obs_full_chars=len(obs_str),
            obs_truncated=truncated, obs_used_chars=truncated_size)

        os.unlink(path)


# =========================================================================
# Reporter finale
# =========================================================================

def write_report():
    out = Path("/tmp/metnos_stress_report.json")
    out.write_text(json.dumps(REPORT, indent=2, ensure_ascii=False))
    print(f"\n--- report JSON salvato in {out} ---")


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["cat", "tools", "step", "obs"], default=None)
    args = ap.parse_args()

    suite = {"cat": stress_d_cat, "tools": stress_d_tools, "step": stress_d_step, "obs": stress_d_obs}
    if args.only:
        suite[args.only]()
    else:
        for fn in suite.values():
            fn()
    write_report()


if __name__ == "__main__":
    main()
