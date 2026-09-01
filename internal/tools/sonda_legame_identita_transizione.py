#!/usr/bin/env python3
"""Guard the service-identity binding of the one-shot Birth transition.

RM-0008 F4. Agent A closed the binding reported in §20 with `1d22a19a`. The
fix is correct, but a mutation showed that removing the location half of it
leaves every transition test green: the twelve cases in
`test_executor_birth_transition_entry.py` and
`test_executor_birth_transition_cutover.py` still pass with
``METNOS_USER_STATE`` deleted from the service environment. In production the
three-root equality would then reject, so the failure is fail-closed -- but it
would surface as an opaque rejection during a live cutover instead of a red
test.

This probe asserts the five structural facts the binding rests on, so that
removing any one of them turns something red before the cutover does:

  1. ``complete_transition_cutover_v2`` requires ``service_state_root``;
  2. the chosen, configured and signed roots must be equal;
  3. that equality precedes the durable edge reservation;
  4. the service environment derives ``METNOS_USER_STATE`` from the account;
  5. the seed owner comes from the signed descriptor, not from the caller.

Read-only. Touches no service, production tree, or Birth root.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

_ROOT_OVERRIDE = os.environ.get("SONDA_ROOT")
ROOT = (
    Path(_ROOT_OVERRIDE).resolve()
    if _ROOT_OVERRIDE
    else Path(__file__).resolve().parents[2]
)
PROVISIONER = ROOT / "install" / "birth_authority_provisioner.py"
ENTRY = ROOT / "install" / "executor_birth_transition.py"

failures: list[str] = []


def check(name: str, condition: bool, detail: str) -> None:
    print(f"  [{'ok' if condition else 'ROSSO'}] {name}: {detail}")
    if not condition:
        failures.append(name)


def _function(path: Path, name: str) -> ast.FunctionDef:
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise SystemExit(f"{name} non trovata in {path.name}: la sonda e' obsoleta")


def _line_of(node: ast.AST, needle: str, source: str) -> int:
    """Return the line where ``needle`` appears inside ``node``'s span."""
    lines = source.splitlines()
    for index in range(node.lineno - 1, (node.end_lineno or node.lineno)):
        if needle in lines[index]:
            return index + 1
    return -1


def main() -> int:
    provisioner_source = PROVISIONER.read_text()
    cutover = _function(PROVISIONER, "complete_transition_cutover_v2")

    print("== 1. la radice di stato e' obbligatoria ==")
    names = [argument.arg for argument in cutover.args.kwonlyargs]
    index = names.index("service_state_root") if "service_state_root" in names else -1
    check(
        "parametro obbligatorio",
        index >= 0 and cutover.args.kw_defaults[index] is None,
        "service_state_root e' keyword-only e senza valore predefinito",
    )

    print("== 2. le tre radici devono coincidere ==")
    equality = _line_of(
        cutover,
        "selected_state_root == signed_state_root == configured_state_root",
        provisioner_source,
    )
    check(
        "uguaglianza a tre",
        equality > 0,
        "scelta == firmata == configurata, altrimenti rifiuto",
    )

    print("== 3. il confronto precede l'arco durevole ==")
    reservation = _line_of(
        cutover, "_reserve_transition_edge_locked_v2(", provisioner_source,
    )
    check(
        "ordine corretto",
        equality > 0 and reservation > 0 and equality < reservation,
        f"confronto a riga {equality}, prenotazione a riga {reservation}",
    )

    print("== 4. l'ambiente deriva lo stato dall'account ==")
    environment = _function(ENTRY, "_service_environment_v1")
    body = ast.get_source_segment(ENTRY.read_text(), environment) or ""
    check(
        "stato legato all'account",
        "METNOS_USER_STATE" in body and "pw_dir" in body,
        "METNOS_USER_STATE deriva da account.pw_dir",
    )

    print("== 5. il proprietario viene dal descrittore firmato ==")
    owner = _line_of(cutover, "authoring_owner=(", provisioner_source)
    following = provisioner_source.splitlines()[owner:owner + 2] if owner > 0 else []
    check(
        "proprietario firmato",
        owner > 0 and any("descriptor.service_uid" in line for line in following),
        "authoring_owner = (descriptor.service_uid, descriptor.service_gid)",
    )

    print()
    if failures:
        print(f"ROSSO su {len(failures)}: {', '.join(failures)}")
        return 1
    print("legame di identita' integro: posizione e proprieta' entrambe sorvegliate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
