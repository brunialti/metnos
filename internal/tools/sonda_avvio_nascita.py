"""Boot probe for the Birth gates, with a stable exit contract.

The probe exercises the gates a real start-up crosses, in order, against roots
it prints before measuring anything.  Printing the roots is not decoration: the
diagnosis this tool serves was wrong twice because the measurement ran against
a tree nobody had checked.

Exit contract (a wrapper may rely on the status, never on the free text):

    0  every required gate is green
    2  gate 1 refused: the prepared Birth root cannot be opened
    3  gate 2 refused: the installed distribution cannot be opened
    4  gate 2b refused: at least one catalogue file cannot be read
    5  gate 3 refused: the prepared set does not match the distribution
    1  the probe itself could not run (bad environment, missing import)

A gate whose prerequisite is red is reported as "non eseguito" and never run:
a dependent gate that cannot have a meaningful answer must not produce one.
"""
from __future__ import annotations

import os
import stat
import sys

EXIT_OK = 0
EXIT_SELF = 1
EXIT_GATE1 = 2
EXIT_GATE2 = 3
EXIT_GATE2B = 4
EXIT_GATE3 = 5


def _fail_self(message: str) -> int:
    print(f"SONDA NON ESEGUIBILE: {message}", file=sys.stderr)
    return EXIT_SELF


def _describe(exc: Exception) -> str:
    """Name an exception by its stable code when it carries one."""
    code = getattr(exc, "code", None)
    text = f"{type(exc).__name__} code={code}" if code else type(exc).__name__
    cause = getattr(exc, "_internal_cause", None)
    if cause is not None:
        inner = getattr(cause, "code", None)
        text += f" | causa interna: {type(cause).__name__}"
        if inner:
            text += f" code={inner}"
    return text


def _mode_of(path: str) -> str:
    """Best-effort mode for diagnostics.

    This runs inside an error branch, so it must never raise: a missing file or
    a dangling link would otherwise replace the original refusal with a second,
    unrelated error and destroy the diagnosis.
    """
    try:
        return "%o" % (os.lstat(path).st_mode & 0o7777)
    except OSError as exc:
        return f"modo non leggibile ({exc.__class__.__name__})"


def main() -> int:
    root = os.environ.get("METNOS_INSTALL_ROOT")
    if not root:
        return _fail_self("METNOS_INSTALL_ROOT non e' impostata")
    runtime_dir = os.path.join(root, "runtime")
    if not os.path.isdir(runtime_dir):
        return _fail_self(f"{runtime_dir} non e' una directory")
    sys.path.insert(0, runtime_dir)

    try:
        import config as C
        from executor_birth_bootstrap import birth_authority_is_prepared_v1
        from executor_birth_prepared_root import (
            open_distribution_sources_v1, open_prepared_root_session_v1,
            read_prepared_set_v1,
        )
        import executor_birth_context_v1 as ctx
        from executor_birth_secure_fs import BirthSecureFSError
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        return _fail_self(f"import fallito: {type(exc).__name__}: {exc}")

    print("== CHE COSA STO MISURANDO ==")
    print("  PATH_ROOT        =", C.PATH_ROOT)
    print("  PATH_RUNTIME     =", C.PATH_RUNTIME,
          "mode=" + _mode_of(str(C.PATH_RUNTIME)))
    print("  PATH_USER_CONFIG =", C.PATH_USER_CONFIG)
    print("  PATH_USER_STATE  =", C.PATH_USER_STATE)
    print()
    print("insieme preparato rilevato:", birth_authority_is_prepared_v1())
    print()

    # Gate 1 - the prepared Birth root.
    print("-- cancello 1: apertura radice di nascita preparata")
    try:
        session = open_prepared_root_session_v1()
        session.close()
        print("   ESITO: ok")
    except Exception as exc:  # noqa: BLE001
        print("   ESITO: FALLITO ", _describe(exc))
        print("-- cancelli 2, 2b, 3: NON ESEGUITI (prerequisito rosso)")
        return EXIT_GATE1

    # Gate 2 - the installed distribution.
    print("-- cancello 2: apertura della distribuzione installata (PATH_RUNTIME)")
    try:
        open_distribution_sources_v1().close()
        print("   ESITO: ok")
    except Exception as exc:  # noqa: BLE001
        print("   ESITO: FALLITO ", _describe(exc))
        print("-- cancelli 2b, 3: NON ESEGUITI (prerequisito rosso)")
        return EXIT_GATE2

    # Gate 2b - every catalogue file, one at a time, to name the first refusal.
    print("-- cancello 2b: lettura dei file del catalogo, uno per uno")
    sources = open_distribution_sources_v1()
    rifiutati: list[tuple[str, str]] = []
    letti = 0
    try:
        for name, _version, files, _enforcement in ctx.CONTEXT_CATALOG_V1:
            for label in files:
                try:
                    sources.read_file(
                        (label,),
                        maximum=ctx.MAXIMUM_CONTEXT_SOURCE_BYTES_V1,
                        exact_private=False,
                    )
                    letti += 1
                except BirthSecureFSError as exc:
                    rifiutati.append((label, getattr(exc, "code", "?")))
                    print(f"   RIFIUTATO {label:45} code={getattr(exc,'code','?')}"
                          f"  mode={_mode_of(os.path.join(str(C.PATH_RUNTIME), label))}")
    finally:
        sources.close()
    print(f"   letti con successo: {letti}; rifiutati: {len(rifiutati)}")
    if rifiutati:
        print(f"   primo rifiuto: {rifiutati[0][0]} ({rifiutati[0][1]})")
        print("-- cancello 3: NON ESEGUITO (prerequisito rosso)")
        return EXIT_GATE2B

    # Gate 3 - the prepared set against the rebuilt material.
    print("-- cancello 3: read_prepared_set_v1 (materiale + confronto impronte)")
    try:
        prepared = read_prepared_set_v1()
        print("   ESITO: ok -> set_id=" + getattr(prepared, "set_id", "?"))
    except Exception as exc:  # noqa: BLE001
        print("   ESITO: FALLITO ", _describe(exc))
        return EXIT_GATE3

    print()
    print("TUTTI I CANCELLI VERDI")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
