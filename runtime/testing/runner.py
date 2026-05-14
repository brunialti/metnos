#!/usr/bin/env python3
"""
runner.py — esegue test_cases dal DB, registra gli esiti.

Quattro test_kind supportati v1.1:

    'shell'    test_code = comando shell. setup/teardown idem. expected = optional, "exit_code:0".
               PASS se exit code corretto.

    'python'   test_code = sorgente Python eseguito in subprocess (per isolamento).
               setup/teardown shell opzionali. Il test PASSA se python termina con exit 0;
               failure se solleva un'eccezione (assert, Exception, ecc.).
               Il sorgente puo' fare 'import' dei moduli runtime/ con sys.path autoset.

    'birth'    test_code = JSON con {input, expect}, manifest_path nel campo expected per puntare
               al manifest dell'executor. Riusa il test_runner.py esistente.

    'e2e'      test_code = JSON con {query, model?, mode?, ...}.
               Esegue agent_runtime.run_turn(query) e verifica final_kind / final_message.

Scopes:
    run_module(name)         tutti i case del modulo
    run_cluster(name)        tutti i case dei moduli del cluster
    run_level(level)         tutti i case di quel livello
    run_all()                tutti i case
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from registry import Registry, TestCase

RUNTIME_DIR = Path("/opt/myclaw/runtime")
EXECUTORS_DIR = Path("/opt/myclaw/executors")


@dataclass
class RunResult:
    case: TestCase
    status: str           # 'pass' | 'fail' | 'error' | 'skipped'
    duration_ms: int
    output: str
    failure: str


# --- Esecutori per test_kind --------------------------------------------------

def _run_shell(case):
    """Esegue case.test_code come shell. PASS se exit code 0 (o altro definito in expected)."""
    if case.setup_code:
        s = subprocess.run(case.setup_code, shell=True, capture_output=True, text=True)
        if s.returncode != 0:
            return "error", f"SETUP failed rc={s.returncode}: {s.stderr.strip()}", ""
    out = subprocess.run(case.test_code, shell=True, capture_output=True, text=True, timeout=60)
    if case.teardown_code:
        subprocess.run(case.teardown_code, shell=True, capture_output=True, text=True)

    expected_rc = 0
    if case.expected:
        # formato semplice "exit_code:N"
        for tok in case.expected.split(";"):
            tok = tok.strip()
            if tok.startswith("exit_code:"):
                expected_rc = int(tok.split(":", 1)[1])
    if out.returncode == expected_rc:
        return "pass", out.stdout[:1000], ""
    return "fail", out.stdout[:1000], f"exit_code={out.returncode} expected={expected_rc}; stderr={out.stderr[:500]}"


def _run_python(case):
    """Esegue case.test_code come script Python in subprocess. Auto-setup di sys.path."""
    if case.setup_code:
        s = subprocess.run(case.setup_code, shell=True, capture_output=True, text=True)
        if s.returncode != 0:
            return "error", f"SETUP failed: {s.stderr.strip()}", ""

    preamble = f"""
import sys
sys.path.insert(0, {str(RUNTIME_DIR)!r})
sys.path.insert(0, {str(RUNTIME_DIR / 'testing')!r})
"""
    full_code = preamble + "\n" + case.test_code

    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(full_code)
        script = f.name
    try:
        out = subprocess.run(["python3", script], capture_output=True, text=True, timeout=120)
    finally:
        os.unlink(script)

    if case.teardown_code:
        subprocess.run(case.teardown_code, shell=True, capture_output=True, text=True)

    if out.returncode == 0:
        return "pass", out.stdout[:1000], ""
    return "fail", out.stdout[:1000], f"rc={out.returncode}; stderr:\n{out.stderr[:1500]}"


def _run_birth(case):
    """case.test_code = path al manifest. expected = nome del singolo test (opzionale)."""
    manifest_path = case.test_code.strip()
    if not Path(manifest_path).exists():
        return "error", "", f"manifest non trovato: {manifest_path}"
    test_name_filter = case.expected.strip() or None
    out = subprocess.run(
        ["python3", str(RUNTIME_DIR / "test_runner.py"), manifest_path],
        capture_output=True, text=True, timeout=120,
    )
    output = out.stdout
    if test_name_filter is None:
        # Tutti i birth tests devono passare
        if out.returncode == 0:
            return "pass", output[:1500], ""
        return "fail", output[:1500], f"rc={out.returncode}"
    # Cerca la riga del singolo test nel formato "  v <name>" o "  X <name>"
    for line in output.splitlines():
        ls = line.strip()
        if ls.startswith("v ") and ls[2:] == test_name_filter:
            return "pass", line.strip(), ""
        if ls.startswith("X ") and ls[2:] == test_name_filter:
            # Trova le righe di failure dopo
            return "fail", output[:1500], f"birth test '{test_name_filter}' fallito"
    return "error", output[:1500], f"birth test '{test_name_filter}' non trovato nell'output"


def _run_e2e(case):
    """case.test_code = JSON con {query, model?, mode?, k?, expect_kind?, expect_substring?}."""
    try:
        spec = json.loads(case.test_code)
    except json.JSONDecodeError as e:
        return "error", "", f"test_code non e' JSON: {e}"

    query = spec.get("query", "")
    if not query:
        return "error", "", "manca 'query' in test_code"

    # Setup (shell) — bug fix 26/4 sera: e2e ora supporta setup come gli altri kind
    if case.setup_code:
        s = subprocess.run(case.setup_code, shell=True, capture_output=True, text=True)
        if s.returncode != 0:
            return "error", "", f"SETUP failed rc={s.returncode}: {s.stderr.strip()}"

    expect_kind = spec.get("expect_kind", "answer")
    expect_substring = spec.get("expect_substring", "")
    expect_executor = spec.get("expect_executor")

    cmd = ["python3", str(RUNTIME_DIR / "agent_runtime.py")]
    if spec.get("model"):
        cmd += ["--model", spec["model"]]
    if spec.get("mode"):
        cmd += ["--mode", spec["mode"]]
    if spec.get("k"):
        cmd += ["--k", str(spec["k"])]
    if spec.get("verbose", True):
        cmd += ["-v"]
    cmd.append(query)

    out = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    output = out.stdout

    # Teardown (shell) — bug fix 26/4 sera
    if case.teardown_code:
        subprocess.run(case.teardown_code, shell=True, capture_output=True, text=True)

    # final_message viene stampato dopo ">>> " nello stdout
    final_message = ""
    for line in output.splitlines():
        if line.startswith(">>> "):
            final_message = line[4:].strip()
            break

    failures = []
    if expect_substring and expect_substring.lower() not in (final_message + "\n" + output).lower():
        failures.append(f"expected_substring '{expect_substring}' non trovato in output (case-insensitive)")
    if expect_executor:
        if f"exec {expect_executor}(" not in output and f"tool_call: {expect_executor}(" not in output:
            failures.append(f"expected_executor '{expect_executor}' non invocato")
    if expect_kind == "answer" and "kind=answer" not in output:
        # alcuni esiti non finiscono con kind=answer (es cap_steps); soft-check
        pass

    if failures:
        return "fail", final_message[:300], "; ".join(failures) + f"\n--- output:\n{output[:1500]}"
    return "pass", final_message[:500], ""


KIND_HANDLERS = {
    "shell": _run_shell,
    "python": _run_python,
    "birth": _run_birth,
    "e2e": _run_e2e,
}


# --- Loop principale ---------------------------------------------------------

def run_case(case, registry, triggered_by="manual", verbose=False):
    handler = KIND_HANDLERS.get(case.test_kind)
    if not handler:
        result = RunResult(case, "error", 0, "", f"test_kind sconosciuto: {case.test_kind}")
        registry.record_run(case.id, "error", 0, "", result.failure, triggered_by)
        return result
    t0 = time.time()
    try:
        status, output, failure = handler(case)
    except subprocess.TimeoutExpired:
        status, output, failure = "error", "", "timeout"
    except Exception as e:
        status, output, failure = "error", "", f"runner exception: {type(e).__name__}: {e}"
    duration_ms = int((time.time() - t0) * 1000)
    registry.record_run(case.id, status, duration_ms, output, failure, triggered_by)
    if verbose:
        marker = {"pass": "v", "fail": "X", "error": "!", "skipped": "-"}.get(status, "?")
        line = f"  {marker} [{case.level:7s}|{case.module_name:14s}] {case.name}  ({duration_ms}ms)"
        print(line)
        if status != "pass" and failure:
            for fl in failure.split("\n")[:3]:
                print(f"      {fl}")
    return RunResult(case, status, duration_ms, output, failure)


def run_cases(cases, registry, triggered_by, verbose=False):
    results = [run_case(c, registry, triggered_by, verbose) for c in cases]
    by_status = {}
    for r in results:
        by_status[r.status] = by_status.get(r.status, 0) + 1
    return results, by_status


def run_module(module_name, registry, verbose=False):
    cases = registry.cases_for_module(module_name)
    if verbose:
        print(f"=== Module '{module_name}' — {len(cases)} test case(s) ===")
    return run_cases(cases, registry, f"module:{module_name}", verbose)


def run_cluster(module_name, registry, verbose=False):
    cases = registry.cases_for_cluster(module_name)
    cluster = registry.cluster_of(module_name)
    if verbose:
        print(f"=== Cluster of '{module_name}' = {{{', '.join(sorted(cluster))}}} — {len(cases)} test case(s) ===")
    return run_cases(cases, registry, f"cluster:{module_name}", verbose)


def run_level(level, registry, verbose=False):
    cases = registry.cases_at_level(level)
    if verbose:
        print(f"=== Level '{level}' — {len(cases)} test case(s) ===")
    return run_cases(cases, registry, f"level:{level}", verbose)


def run_all(registry, verbose=False):
    cases = registry.all_cases()
    if verbose:
        print(f"=== ALL — {len(cases)} test case(s) ===")
    return run_cases(cases, registry, "all", verbose)


# --- CLI -------------------------------------------------------------------

def main():
    import argparse
    ap = argparse.ArgumentParser(description="Metnos test runner")
    sub = ap.add_subparsers(dest="cmd")

    p_all = sub.add_parser("all", help="esegue tutti i test")
    p_mod = sub.add_parser("module", help="esegue test del modulo")
    p_mod.add_argument("name")
    p_clu = sub.add_parser("cluster", help="esegue test del cluster del modulo")
    p_clu.add_argument("name")
    p_lev = sub.add_parser("level", help="esegue test di un livello")
    p_lev.add_argument("level", choices=["module", "cluster", "system"])
    p_sum = sub.add_parser("summary", help="riepilogo del DB")

    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args()

    r = Registry.open()
    verbose = not args.quiet

    if args.cmd == "all":
        _, by_status = run_all(r, verbose)
    elif args.cmd == "module":
        _, by_status = run_module(args.name, r, verbose)
    elif args.cmd == "cluster":
        _, by_status = run_cluster(args.name, r, verbose)
    elif args.cmd == "level":
        _, by_status = run_level(args.level, r, verbose)
    elif args.cmd == "summary":
        print(json.dumps(r.summary(), indent=2, ensure_ascii=False))
        return
    else:
        ap.print_help()
        return

    print(f"\n=== {sum(by_status.values())} run, " + " ".join(f"{k}={v}" for k, v in by_status.items()) + " ===")
    sys.exit(0 if by_status.get("fail", 0) + by_status.get("error", 0) == 0 else 1)


if __name__ == "__main__":
    main()
