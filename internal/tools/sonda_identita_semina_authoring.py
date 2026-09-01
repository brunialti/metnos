#!/usr/bin/env python3
"""Measure which identity decides where repository authoring is seeded.

This historical diagnostic exposed the missing service-state binding in the
F4 candidate reviewed by agent B. Its individual checks deliberately describe
that older defect; after the fix, the caller and binding checks become red.

The probe is read-only. It touches no service, production tree, or Birth root.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

# SONDA_ROOT lets the mutation harness point the probe at a mutated copy.
_ROOT_OVERRIDE = os.environ.get("SONDA_ROOT")
ROOT = (
    Path(_ROOT_OVERRIDE).resolve()
    if _ROOT_OVERRIDE
    else Path(__file__).resolve().parents[2]
)
RUNTIME = ROOT / "runtime"

failures: list[str] = []


def check(name: str, condition: bool, detail: str) -> None:
    print(f"  [{'ok' if condition else 'ROSSO'}] {name}: {detail}")
    if not condition:
        failures.append(name)


def _seed_function_source() -> str:
    """Return the source of the helper that materializes the seed root."""
    tree = ast.parse((RUNTIME / "contract_store.py").read_text())
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "_seed_repository_authoring_locked_v1"
        ):
            return ast.get_source_segment(
                (RUNTIME / "contract_store.py").read_text(), node
            ) or ""
    raise SystemExit("helper del seme non trovato: la sonda e' obsoleta")


def _state_path_under(home: str) -> str:
    """Resolve PATH_USER_STATE as a process with the given HOME would."""
    code = "import sys; sys.path.insert(0, %r); import config; print(config.PATH_USER_STATE)" % str(RUNTIME)
    env = {
        k: v for k, v in os.environ.items()
        if not k.startswith("METNOS_USER_")
    }
    env["HOME"] = home
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
        env=env, timeout=90,
    )
    if done.returncode != 0:
        raise SystemExit(f"config non importabile: {done.stderr[-400:]}")
    return done.stdout.strip()


def _owner_defaults_to_none() -> bool:
    """Return whether the seed helper defaults ``authoring_owner`` to None."""
    text = (RUNTIME / "contract_store.py").read_text()
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.FunctionDef)
            and node.name == "_seed_repository_authoring_locked_v1"
        ):
            continue
        args = node.args
        names = [a.arg for a in args.kwonlyargs]
        if "authoring_owner" not in names:
            return False
        default = args.kw_defaults[names.index("authoring_owner")]
        return isinstance(default, ast.Constant) and default.value is None
    return False


def _product_callers(symbol: str) -> int:
    """Count product call sites of ``symbol``, excluding its definition."""
    found = 0
    for directory in ("runtime", "install", "scripts"):
        base = ROOT / directory
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            for line in path.read_text(errors="ignore").splitlines():
                if f"{symbol}(" in line and not line.lstrip().startswith("def "):
                    found += 1
    return found


def main() -> int:
    print("== 1. dove atterra il seme ==")
    source = _seed_function_source()
    check(
        "radice dichiarata",
        'PATH_USER_STATE / "contract-authoring" / "v1"' in source
        or ("PATH_USER_STATE" in source and "contract-authoring" in source),
        "il seme e' materializzato sotto PATH_USER_STATE/contract-authoring/v1",
    )

    print("== 2. la radice segue il processo, non il servizio ==")
    import tempfile
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        here = _state_path_under(a)
        other = _state_path_under(b)
    check(
        "radice mobile",
        here != other,
        f"due HOME distinte -> due radici distinte ({Path(here).name} sotto ciascuna)",
    )

    print("== 3. il seme consulta mai l'identita' del servizio? ==")
    named = [
        token for token in ("service_user", "pwd.", "getpwnam", "pw_dir",
                            "geteuid", "getuid")
        if token in source
    ]
    check(
        "il seme non risolve l'identita'",
        not named,
        ("dopo d96958ef il seme ACCETTA un proprietario, ma non lo "
         "deriva: non nomina service_user, pwd, pw_dir, uid")
        if not named else f"trovati: {named}",
    )

    print("== 4. il legame identita'->stato esiste gia' altrove ==")
    migration = (RUNTIME / "stack_migration.py").read_text()
    check(
        "meccanismo presente",
        "METNOS_USER_STATE" in migration and "pw_dir" in migration,
        "stack_migration.py deriva METNOS_USER_STATE da identity.pw_dir",
    )
    users = 0
    for name in ("contract_store.py", "executor_birth_bootstrap.py"):
        users += (RUNTIME / name).read_text().count("stack_migration")
    provisioner = ROOT / "install" / "birth_authority_provisioner.py"
    users += provisioner.read_text().count("stack_migration")
    check(
        "meccanismo non collegato",
        users == 0,
        f"il percorso di transizione lo richiama {users} volte",
    )

    print("== 5. il legame arriva al percorso che l'installer percorre? ==")
    provisioner = ROOT / "install" / "birth_authority_provisioner.py"
    phase3 = ROOT / "install" / "phases" / "phase3_code.py"
    check(
        "il proprietario e' un parametro facoltativo",
        _owner_defaults_to_none(),
        "authoring_owner e' opzionale e vale None se il chiamante tace",
    )
    passers = [
        path.relative_to(ROOT).as_posix()
        for path in (provisioner, phase3)
        if "authoring_owner" in path.read_text()
    ]
    check(
        "solo il cutover lo passa",
        passers == ["install/birth_authority_provisioner.py"],
        f"lo passa {passers or 'nessuno'}; il chiamante vivo (phase3_code.py) tace",
    )
    check(
        "e il cutover non ha chiamanti di prodotto",
        _product_callers("complete_transition_cutover_v2") == 0,
        "complete_transition_cutover_v2 e' invocato solo dalle prove",
    )

    print()
    if failures:
        print(f"ROSSO su {len(failures)}: {', '.join(failures)}")
        return 1
    print("tutte le misure confermate: il seme e' legato al processo, non al servizio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
