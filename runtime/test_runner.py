#!/usr/bin/env python3
"""
Mini test runner per i test di nascita degli executor (Metnos v1.1 POC).

Uso: python3 test_runner.py <path/to/manifest.toml>

Ciclo per ogni test del manifest:
    setup (shell, opz) -> hint pre-check -> invoke executor con stdin JSON
        -> verifica matcher di expect -> teardown (shell, opz).

PSEUDO-SANDBOX (ciclo 2): prima di invocare un executor, il runner verifica
che il valore di args.path matchi almeno uno degli hint dichiarati nei
[[capabilities]] del manifest. Se no, ritorna esito canonico
{ok: false, error: "outside allowed scope: <path>"} senza invocare.
Questo simula l'enforcement che la sandbox vera del runtime applichera' a
livello kernel (bubblewrap+landlock). E' provvisorio.

Matcher supportati v1.1 (estesi nel ciclo 2):
    ok                    bool
    error_contains        string (substring)
    content_contains      string (substring)
    content_length_eq     int
    content_length_lte    int
    content_length_gte    int
    metadata_field_eq     {field: value}
    metadata_field_lte    {field: int}
    metadata_field_gte    {field: int}
"""
import fnmatch
import json
import os
import subprocess
import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[1]


def run_shell(cmd):
    if not cmd:
        return 0, "", ""
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr


def run_executor(executor_path, args, test_env=None):
    payload = json.dumps(args)
    # Stesso contratto env di agent_runtime.invoke_executor: gli executor
    # importano moduli runtime per nome (skill_wrapper, messages, ...) e
    # leggono METNOS_RUNTIME per il bootstrap sys.path. Senza questo i test
    # di nascita falliscono con ModuleNotFoundError pur con executor sani.
    runtime_path = str(Path(__file__).resolve().parent)
    env = os.environ.copy()
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        runtime_path if not existing_pp
        else f"{runtime_path}{os.pathsep}{existing_pp}"
    )
    env.setdefault("METNOS_RUNTIME", runtime_path)
    # Env per-test (opzionale): rende ERMETICO un test rispetto all'ambiente
    # del host. Valori con `{RUNTIME}` espanso al path runtime → nessun path
    # assoluto hardcoded (§7.11), portabile fra macchine. Uso tipico: puntare
    # METNOS_SKILL_HOME a una dir senza credenziali per esercitare il ramo
    # needs_inputs (OAuth assente) a prescindere dai token del host.
    if test_env:
        for k, v in test_env.items():
            env[str(k)] = str(v).replace("{RUNTIME}", runtime_path)
    result = subprocess.run(
        ["python3", str(executor_path)],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
    )
    try:
        parsed = json.loads(result.stdout)
        return result.returncode, parsed, result.stderr
    except json.JSONDecodeError:
        return result.returncode, None, f"NON-JSON STDOUT: {result.stdout!r}\nSTDERR: {result.stderr!r}"


def run_reference(reference, test_env=None):
    """Run a signed repository-local pytest reference.

    In-process builtins are modules, not stdin/stdout executables.  Their
    manifests therefore point at hermetic pytest evidence instead of
    pretending that the module can be invoked as a subprocess.  Keep this
    path explicit and repository-confined: a typo or an external path fails
    closed instead of executing an unrelated test file.
    """
    if not isinstance(reference, str) or not reference.strip():
        return 2, "", "reference must be a non-empty string"
    raw_path, separator, node_id = reference.strip().partition("::")
    candidate = (_REPO_ROOT / raw_path).resolve()
    try:
        candidate.relative_to(_REPO_ROOT)
    except ValueError:
        return 2, "", f"reference outside repository: {raw_path}"
    if not candidate.is_file():
        return 2, "", f"reference not found: {raw_path}"
    selector = str(candidate)
    if separator:
        if not node_id.strip():
            return 2, "", f"empty pytest node id: {reference}"
        selector = f"{selector}::{node_id.strip()}"

    env = os.environ.copy()
    runtime_path = str(Path(__file__).resolve().parent)
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        runtime_path if not existing_pp
        else f"{runtime_path}{os.pathsep}{existing_pp}"
    )
    env.setdefault("METNOS_RUNTIME", runtime_path)
    if test_env:
        for key, value in test_env.items():
            env[str(key)] = str(value).replace("{RUNTIME}", runtime_path)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", selector],
        cwd=_REPO_ROOT, capture_output=True, text=True, env=env,
    )
    return result.returncode, result.stdout, result.stderr


def expand_hint(hint):
    """Espande ~ nel hint contro l'utente che esegue il runner."""
    return os.path.expanduser(hint)


def match_hint(target_path, hint_pattern):
    """
    Match minimale per pattern di tipo glob con ** terminale.
    Supporta: '<prefix>/**' (matcha qualsiasi cosa sotto prefix), o glob fnmatch standard.
    Non gestisce ** in mezzo al pattern (limite v1.1 noto).
    """
    expanded = expand_hint(hint_pattern)
    target = os.path.abspath(os.path.expanduser(target_path))
    if expanded.endswith("/**"):
        prefix = expanded[:-3]
        return target == prefix or target.startswith(prefix + os.sep)
    return fnmatch.fnmatchcase(target, expanded)


def match_host(target_host, hint_pattern):
    """Match host pattern: 'example.com' esatto o '*.example.com' wildcard di sub."""
    if hint_pattern.startswith("*."):
        suffix = hint_pattern[1:]  # ".example.com"
        return target_host.endswith(suffix) or target_host == suffix[1:]
    return target_host == hint_pattern


def check_hints(args, capabilities, actor="host"):
    """
    Pseudo-sandbox: per ogni capability con hint, se l'arg corrispondente
    e' fuori scope (o matcha un exclude utente) ritorna messaggio canonico,
    altrimenti None.

    Mappature implementate:
      capability `fs:*`        -> args.path o args.base_path matcha scope, niente exclude (path_glob)
      capability `network:*`   -> host estratto da args.url matcha scope, niente exclude (host)

    Lo scope effettivo e' l'unione hint manifest + user scope dichiarato in
    `~/.config/metnos/workspace_policy.toml` (sezione [<actor>.<famiglia>.<azione>]);
    in piu' i pattern in `excludes` rifiutano anche se in scope.
    """
    if not isinstance(args, dict):
        # Root-shape validation belongs to the executor contract; authority
        # checks cannot extract a path or host from a non-object input.
        return None

    from urllib.parse import urlparse
    try:
        from workspace_policy import effective_hints
    except ImportError:
        def effective_hints(_actor, _name, hints):  # type: ignore[no-redef]
            return list(hints), []

    for cap in capabilities:
        name = cap.get("name", "")
        manifest_hints = cap.get("hint", [])
        if not manifest_hints:
            continue
        scope, excludes = effective_hints(actor, name, manifest_hints)

        if name.startswith("fs:"):
            semantic_args = {
                hint[4:] for hint in manifest_hints
                if isinstance(hint, str) and hint.startswith("arg:") and hint[4:]
            }
            if semantic_args:
                # ``arg:<name>`` is a signed exact-input authority.  Other
                # path-looking arguments (for example an index corpus key)
                # do not inherit filesystem access from it.
                for arg_name in semantic_args:
                    raw = args.get(arg_name)
                    values = raw if isinstance(raw, list) else [raw]
                    for path in values:
                        if not isinstance(path, str) or not path:
                            continue
                        for ex in excludes:
                            if match_hint(path, ex):
                                return (
                                    f"outside allowed scope: {path} matches exclude {ex}"
                                )
                continue
            # Estrai i path da varie convenzioni di arg: scalari, liste, entries+template.
            # Tutti i path presenti devono essere in scope e non matchare excludes.
            path_candidates = []
            # scalari (legacy + correnti)
            for k in ("path", "base_path", "src", "dst"):
                v = args.get(k)
                if isinstance(v, str) and v:
                    path_candidates.append(v)
            # liste di path
            for k in ("paths", "base_paths", "srcs", "dsts"):
                v = args.get(k)
                if isinstance(v, list):
                    path_candidates.extend(p for p in v if isinstance(p, str) and p)
            # entries: list[{path?, src?, dst?}]
            entries = args.get("entries")
            if isinstance(entries, list):
                for e in entries:
                    if not isinstance(e, dict):
                        continue
                    for ek in ("path", "src", "dst"):
                        ev = e.get(ek)
                        if isinstance(ev, str) and ev:
                            path_candidates.append(ev)
            # dst_template: controlla solo il prefisso fisso prima del primo placeholder.
            dt = args.get("dst_template")
            if isinstance(dt, str) and dt:
                idx = dt.find("{")
                fixed = dt if idx < 0 else dt[:idx]
                if fixed:
                    path_candidates.append(fixed)
            if not path_candidates:
                continue
            for p in path_candidates:
                if not any(match_hint(p, h) for h in scope):
                    return f"outside allowed scope: {p} not in any of {scope}"
                for ex in excludes:
                    if match_hint(p, ex):
                        return f"outside allowed scope: {p} matches exclude {ex}"

        elif name.startswith("network:"):
            url_candidates = []
            v = args.get("url")
            if isinstance(v, str) and v:
                url_candidates.append(v)
            v = args.get("urls")
            if isinstance(v, list):
                url_candidates.extend(u for u in v if isinstance(u, str) and u)
            if not url_candidates:
                continue
            for url in url_candidates:
                try:
                    host = urlparse(url).hostname or ""
                except Exception:
                    return f"outside allowed scope: invalid url {url}"
                if not any(match_host(host, h) for h in scope):
                    return f"outside allowed scope: host {host} not in any of {scope}"
                for ex in excludes:
                    if match_host(host, ex):
                        return f"outside allowed scope: host {host} matches exclude {ex}"
    return None


def check_expect(actual, expected):
    failures = []
    for matcher, value in expected.items():
        if matcher == "ok":
            if actual.get("ok") != value:
                failures.append(f"ok: atteso {value}, ottenuto {actual.get('ok')}")
        elif matcher == "error_contains":
            # cerca sia in actual.error (top-level) che in actual.failed[].error
            # (pattern best-effort vettoriale: l'errore puo' essere per-entry)
            parts = [actual.get("error") or ""]
            for f in actual.get("failed") or []:
                if isinstance(f, dict):
                    parts.append(str(f.get("error") or ""))
            haystack = " | ".join(p for p in parts if p)
            if value not in haystack:
                failures.append(f"error_contains: '{value}' non in '{haystack}'")
        elif matcher == "content_contains":
            content = actual.get("content") or ""
            if value not in content:
                failures.append(f"content_contains: '{value}' non in content")
        elif matcher == "content_length_eq":
            content = actual.get("content") or ""
            if len(content) != value:
                failures.append(f"content_length_eq: atteso {value}, ottenuto {len(content)}")
        elif matcher == "content_length_lte":
            content = actual.get("content") or ""
            if len(content) > value:
                failures.append(f"content_length_lte: atteso <= {value}, ottenuto {len(content)}")
        elif matcher == "content_length_gte":
            content = actual.get("content") or ""
            if len(content) < value:
                failures.append(f"content_length_gte: atteso >= {value}, ottenuto {len(content)}")
        elif matcher == "metadata_field_eq":
            # Fallback chain (2.6 schema vettoriale): metadata → results[0] → top-level.
            # I test pre-refactor vettoriale cercano in `metadata`; gli executor
            # transformativi mettono i campi per-entry in `results[]`.
            md = actual.get("metadata") or {}
            results = actual.get("results") or []
            r0 = results[0] if (len(results) == 1 and isinstance(results[0], dict)) else {}
            for field, want in value.items():
                got = md.get(field, r0.get(field, actual.get(field)))
                if got != want:
                    failures.append(f"metadata.{field}: atteso {want}, ottenuto {got}")
        elif matcher == "metadata_field_lte":
            md = actual.get("metadata") or {}
            for field, want in value.items():
                got = md.get(field)
                if not isinstance(got, (int, float)) or got > want:
                    failures.append(f"metadata.{field}: atteso <= {want}, ottenuto {got}")
        elif matcher == "metadata_field_gte":
            md = actual.get("metadata") or {}
            for field, want in value.items():
                got = md.get(field)
                if not isinstance(got, (int, float)) or got < want:
                    failures.append(f"metadata.{field}: atteso >= {want}, ottenuto {got}")
        elif matcher == "matches_contains":
            entries = actual.get("matches") or []
            if not any(value in (e or "") for e in entries):
                failures.append(f"matches_contains: '{value}' non in alcuna delle {len(entries)} match")
        elif matcher == "matches_count_eq":
            entries = actual.get("matches") or []
            if len(entries) != value:
                failures.append(f"matches_count_eq: atteso {value}, ottenuto {len(entries)}")
        elif matcher == "field_eq":
            # Top-level field equality. Es: field_eq: {value: 9}.
            for field, want in value.items():
                got = actual.get(field)
                if got != want:
                    failures.append(f"field.{field}: atteso {want}, ottenuto {got}")
        elif matcher == "entries_field_eq":
            # Per-entry field check: entries_field_eq: {"0": {name: "b"}, "1": ...}.
            # Indice come stringa (TOML non supporta key int).
            entries = actual.get("entries") or []
            for idx_str, fields in value.items():
                try:
                    idx = int(idx_str)
                except ValueError:
                    failures.append(f"entries_field_eq: indice non int '{idx_str}'")
                    continue
                if idx >= len(entries):
                    failures.append(f"entries_field_eq[{idx}]: out of range (len={len(entries)})")
                    continue
                e = entries[idx] or {}
                for field, want in fields.items():
                    got = e.get(field)
                    if got != want:
                        failures.append(f"entries[{idx}].{field}: atteso {want}, ottenuto {got}")
        elif matcher == "ok_count_eq":
            if actual.get("ok_count") != value:
                failures.append(f"ok_count_eq: atteso {value}, ottenuto {actual.get('ok_count')}")
        elif matcher == "fail_count_eq":
            if actual.get("fail_count") != value:
                failures.append(f"fail_count_eq: atteso {value}, ottenuto {actual.get('fail_count')}")
        elif matcher in {"decision", "used", "error_class", "error_code"}:
            if actual.get(matcher) != value:
                failures.append(
                    f"{matcher}: atteso {value!r}, ottenuto "
                    f"{actual.get(matcher)!r}")
        elif matcher == "has_field":
            # Il campo `value` deve essere presente e non-None nel risultato.
            if actual.get(value) is None:
                failures.append(f"has_field: campo '{value}' assente/None")
        elif matcher == "entries_min":
            n = len(actual.get("entries") or [])
            if n < value:
                failures.append(f"entries_min: atteso >= {value}, ottenuto {n}")
        elif matcher == "entries_max":
            n = len(actual.get("entries") or [])
            if n > value:
                failures.append(f"entries_max: atteso <= {value}, ottenuto {n}")
        elif matcher == "ok_or_err_class":
            # Passa se l'op e' andata (ok:True) OPPURE e' fallita con la classe
            # d'errore attesa (es. missing_credentials in ambienti senza creds).
            if not (actual.get("ok") is True or actual.get("error_class") == value):
                failures.append(
                    f"ok_or_err_class: atteso ok:true o error_class='{value}', "
                    f"ottenuto ok={actual.get('ok')} err_class={actual.get('error_class')}")
        else:
            failures.append(f"matcher sconosciuto: {matcher}")
    return failures


def check_parallel_equivalence(
        executor_path, args, test_env, baseline, runs: int) -> list[str]:
    """Compare admitted concurrent runs with the sequential result.

    Equality is structural after JSON parsing.  No field is ignored: an
    executor with timestamps, unstable ordering, collisions or missing
    provenance is not safe to reorder until its public contract explicitly
    stabilizes them. Non-read-only admission has additional isolation and
    postcondition gates in the Executor Standard.
    """
    if (not isinstance(runs, int) or isinstance(runs, bool)
            or not 2 <= runs <= 8):
        return ["equivalence_runs: atteso intero 2..8"]
    with ThreadPoolExecutor(
            max_workers=runs, thread_name_prefix="birth_equivalence") as pool:
        futures = [
            pool.submit(run_executor, executor_path, args, test_env)
            for _ in range(runs)
        ]
    failures = []
    for index, future in enumerate(futures, start=1):
        rc, actual, stderr = future.result()
        if actual is None:
            failures.append(
                f"parallel_equivalence[{index}]: invoke rc={rc} {stderr[:120]}")
        elif actual != baseline:
            failures.append(
                f"parallel_equivalence[{index}]: risultato diverso dal baseline")
    return failures


def main():
    if len(sys.argv) != 2:
        print("Usage: test_runner.py <manifest.toml>", file=sys.stderr)
        sys.exit(2)

    manifest_path = Path(sys.argv[1]).resolve()
    manifest_dir = manifest_path.parent
    with open(manifest_path, "rb") as f:
        manifest = tomllib.load(f)

    name = manifest.get("name", "?")
    code_files = manifest.get("code", {}).get("files", [])
    if not code_files:
        print("ERROR: manifest senza [code].files", file=sys.stderr)
        sys.exit(2)
    executor_path = manifest_dir / code_files[0]
    if not executor_path.exists():
        print(f"ERROR: codice non trovato: {executor_path}", file=sys.stderr)
        sys.exit(2)

    capabilities = manifest.get("capabilities", [])
    tests = manifest.get("tests", [])
    if not tests:
        print(f"NO TESTS per '{name}'")
        sys.exit(0)

    print(f"=== Test di nascita executor '{name}' ({len(tests)} test) ===\n")

    passed = failed = 0
    for test in tests:
        tname = test.get("name", "?")
        reference = test.get("reference")
        setup = test.get("setup", "")
        teardown = test.get("teardown", "")
        args = test.get("input", {})
        expected = test.get("expect", {})
        test_env = test.get("env", {})
        equivalence_runs = test.get("equivalence_runs")

        if reference is not None:
            if any(key in test for key in ("input", "expect", "setup", "teardown")):
                print(f"  X {tname}")
                print("      REFERENCE FAIL: reference tests cannot also "
                      "declare input/expect/setup/teardown")
                failed += 1
                continue
            rc, stdout, stderr = run_reference(reference, test_env)
            if rc != 0:
                print(f"  X {tname}")
                detail = (stdout + stderr).strip()
                print(f"      REFERENCE FAIL rc={rc} {detail[-2000:]}")
                failed += 1
            else:
                print(f"  v {tname}")
                passed += 1
            # ``equivalence_runs`` on a reference identifies the reviewed
            # equivalence evidence implemented by that test.  The referenced
            # test itself owns the serial/parallel executions; running pytest
            # concurrently would test pytest isolation, not executor semantics.
            continue

        setup_rc, _, setup_err = run_shell(setup)
        if setup_rc != 0:
            print(f"  X {tname}")
            print(f"      SETUP FAIL rc={setup_rc} {setup_err.strip()}")
            failed += 1
            continue

        # Pseudo-sandbox: pre-check hint
        scope_violation = check_hints(args, capabilities)
        if scope_violation:
            # Mirror del contratto canonico del runtime (local.py: violazione
            # di scope = ERR_PERMISSION_DENIED). Il pre-check e' test-only ma
            # deve emettere la stessa shape, cosi' i test possono asserire
            # error_code in modo uniforme (oltre a error_contains).
            actual = {"ok": False, "error": scope_violation,
                      "error_code": "ERR_PERMISSION_DENIED"}
        else:
            rc, actual, stderr = run_executor(executor_path, args, test_env)
            if actual is None:
                print(f"  X {tname}")
                print(f"      INVOKE FAIL rc={rc} {stderr.strip()}")
                run_shell(teardown)
                failed += 1
                continue

        failures = check_expect(actual, expected)
        if equivalence_runs is not None:
            if scope_violation:
                failures.append(
                    "parallel_equivalence: il caso non deve fallire il controllo scope")
            else:
                failures.extend(check_parallel_equivalence(
                    executor_path, args, test_env, actual, equivalence_runs))
        run_shell(teardown)

        if failures:
            print(f"  X {tname}")
            for f in failures:
                print(f"      {f}")
            preview = json.dumps(actual, ensure_ascii=False)[:200]
            print(f"      actual: {preview}")
            failed += 1
        else:
            print(f"  v {tname}")
            passed += 1

    print(f"\n=== {passed}/{passed+failed} passati ===")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
