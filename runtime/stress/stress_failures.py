#!/usr/bin/env python3
"""
stress_failures.py — verifica come il sistema reagisce ai failure modes.

Cinque scenari:
    F1  ollama irraggiungibile (endpoint sbagliato) -> ProviderError gestito
    F2  executor che crasha (uncaught exception)    -> non-JSON output, runtime cattura
    F3  manifest con codice modificato post-firma    -> rifiutato dal loader
    F4  executor con stdout vuoto o timeout         -> runtime ritorna errore
    F5  catalog vuoto                                -> turno termina pulito
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from llm_provider import OllamaProvider, ProviderError
from loader import load_catalog
from agent_runtime import run_turn, invoke_executor
from sign import sign_executor


def section(name):
    print(f"\n{'='*60}\n=== {name}\n{'='*60}")


def F1_ollama_down():
    section("F1 - Ollama irraggiungibile (endpoint sbagliato)")
    p = OllamaProvider(model="qwen3:8b", endpoint="http://localhost:11999")
    try:
        r = p.chat("Sei un assistente.", "OK")
        print(f"  INASPETTATO: ha risposto: {r.text!r}")
    except ProviderError as e:
        print(f"  OK: ProviderError sollevato come previsto")
        print(f"      messaggio: {str(e)[:100]}")
    except Exception as e:
        print(f"  WARN: eccezione non-ProviderError: {type(e).__name__}: {e}")


def F2_executor_crash():
    section("F2 - Executor che crasha (uncaught exception in Python)")
    tmp = Path(tempfile.mkdtemp())
    crash_dir = tmp / "crash_exec"
    crash_dir.mkdir()
    (crash_dir / "main.py").write_text(
        "import sys, json\n"
        "raise RuntimeError('boom from executor')\n"
    )
    manifest = """manifest_format = "1.0"
name = "crash_exec"
version = "0.1.0"
author = "stress"
description = "Executor che crasha per stress test."
affinity = ["crash"]
[code]
files = ["main.py"]
digest = "sha256:PENDING"
[args]
type = "object"
[[capabilities]]
name = "fs:read"
hint = []
[[tests]]
name = "smoke"
input = {}
expect = { ok = true }
"""
    (crash_dir / "manifest.toml").write_text(manifest)
    sign_executor(crash_dir, key_name="author")
    cat = load_catalog(executors_dir=tmp)
    if "crash_exec" not in cat.executors:
        print(f"  ERROR: executor non caricato (rejected: {cat.rejected})")
        shutil.rmtree(tmp); return
    ex = cat.get("crash_exec")
    result = invoke_executor(ex, {})
    print(f"  output: {json.dumps(result, ensure_ascii=False)[:250]}")
    if result.get("ok") is False and "non-JSON" in result.get("error", ""):
        print(f"  OK: runtime ha catturato l'eccezione e restituito ok=False")
    elif result.get("ok") is False:
        print(f"  OK: runtime ha restituito ok=False")
    else:
        print(f"  WARN: ok=True inatteso")
    shutil.rmtree(tmp)


def F3_manifest_tampered_post_sign():
    section("F3 - Codice modificato post-firma -> loader rifiuta")
    tmp = Path(tempfile.mkdtemp())
    src = Path(__file__).resolve().parents[2] / "executors/read_files"
    dst = tmp / "read_files"
    shutil.copytree(src, dst)
    # Modifica il codice senza ri-firmare
    code = dst / "read_files.py"
    code.write_text(code.read_text() + "\n# TAMPERED\n")
    cat = load_catalog(executors_dir=tmp)
    if len(cat) == 0 and len(cat.rejected) == 1:
        path, reason = cat.rejected[0]
        print(f"  OK: rifiutato. Reason: {reason[:200]}")
    else:
        print(f"  WARN: caricato={len(cat)}, rejected={cat.rejected}")
    shutil.rmtree(tmp)


def F4_executor_stdout_invalido():
    section("F4 - Executor che stampa stdout non-JSON -> runtime restituisce error chiaro")
    tmp = Path(tempfile.mkdtemp())
    bad_dir = tmp / "bad_exec"
    bad_dir.mkdir()
    (bad_dir / "main.py").write_text(
        "import sys\n"
        "sys.stdout.write('this is not json at all just text')\n"
    )
    manifest = """manifest_format = "1.0"
name = "bad_exec"
version = "0.1.0"
author = "stress"
description = "Executor che stampa testo invece di JSON."
affinity = ["bad"]
[code]
files = ["main.py"]
digest = "sha256:PENDING"
[args]
type = "object"
[[capabilities]]
name = "fs:read"
hint = []
[[tests]]
name = "smoke"
input = {}
expect = { ok = true }
"""
    (bad_dir / "manifest.toml").write_text(manifest)
    sign_executor(bad_dir, key_name="author")
    cat = load_catalog(executors_dir=tmp)
    if "bad_exec" in cat.executors:
        ex = cat.get("bad_exec")
        result = invoke_executor(ex, {})
        print(f"  output: {json.dumps(result, ensure_ascii=False)[:250]}")
        if result.get("ok") is False and ("non-JSON" in result.get("error", "") or "JSON" in result.get("error", "")):
            print(f"  OK: runtime ha rilevato output non-JSON")
        else:
            print(f"  WARN: comportamento inatteso")
    shutil.rmtree(tmp)


def F5_catalog_vuoto():
    section("F5 - Catalog vuoto (executors_dir vuota) -> turno termina pulito")
    tmp = Path(tempfile.mkdtemp())
    cat = load_catalog(executors_dir=tmp)
    print(f"  catalog size: {len(cat)}, rejected: {len(cat.rejected)}")
    # Simulare run_turn con catalog vuoto
    import agent_runtime as ar
    original_load = ar.load_catalog
    ar.load_catalog = lambda *a, **kw: cat
    try:
        log = run_turn("che ora e?", model="qwen3:8b", cap_steps=2)
        print(f"  log.final_kind={log.final_kind}, msg={log.final_message[:120]}")
        if log.final_kind == "error" and "vuoto" in log.final_message:
            print(f"  OK: terminazione pulita con messaggio chiaro")
    finally:
        ar.load_catalog = original_load
    shutil.rmtree(tmp)


def main():
    F1_ollama_down()
    F2_executor_crash()
    F3_manifest_tampered_post_sign()
    F4_executor_stdout_invalido()
    F5_catalog_vuoto()


if __name__ == "__main__":
    main()
