"""O16 - what `sign.py` actually does to a file's mode, as a repeatable test.

O14 originally claimed that `sign.py publish` touches an existing file the way
`open(path, 'w')` does.  That was wrong twice: the mechanism is different, and
the entry point named does not rewrite the file at all.  The correction was
right on the facts but its table had no script behind it, and one row omitted
the parameter that decides the outcome.  This is that script.

**What is executed and what is read.**  `_atomic_replace_bytes` is the function
that decides the mode, and it is exercised directly, with every parameter
written out - including `preserve_existing_mode`, whose default silently wins
over `new_mode` on an existing file.

The two public entry points are checked on their source instead of being run.
That is a deliberate limit, not an oversight: `sign_executor` passes through
`deny_legacy_signing_api` and takes the catalog admission lock, and
`publish_executor` needs the real contract store and signing keys.  Running
either would touch live state, which the whole diagnosis forbids.  What can be
asserted without running them is exactly what the claim needs: which mode
policy the authoring path passes, and that the store-only path never writes the
file at all.

Run:  python3 internal/tools/prova_modi_sign.py
"""
from __future__ import annotations

import ast
import atexit
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

RADICE = Path(__file__).resolve().parents[2]

# The user roots are redirected BEFORE `sign.py` is imported, not after.
# Importing it reaches `config`, which derives the log file from
# `PATH_USER_STATE`, and `logging_setup` then opens it: a test that only wanted
# to know what mode a replacement leaves behind was touching the live state
# directory of a running installation.  Only a read-only sandbox stopped it.
# The scratch is created here, private, and removed when the process ends.
_SCRATCH = Path(tempfile.mkdtemp(prefix="prova-modi-sign-"))
_SCRATCH.chmod(0o700)
atexit.register(shutil.rmtree, _SCRATCH, True)
for _nome in ("METNOS_USER_CONFIG", "METNOS_USER_STATE", "METNOS_USER_DATA"):
    _radice = _SCRATCH / _nome.split("_")[-1].lower()
    _radice.mkdir(mode=0o700, exist_ok=True)
    os.environ[_nome] = str(_radice)
os.environ["METNOS_LOG_FILE"] = str(_SCRATCH / "prova.log")

sys.path.insert(0, str(RADICE / "runtime"))

from sign import _atomic_replace_bytes  # noqa: E402

SORGENTE = RADICE / "runtime" / "sign.py"


def radici_isolate() -> list[str]:
    """The imported module must have resolved to the scratch, not to $HOME."""
    import config as C

    errori = []
    for attributo in ("PATH_USER_CONFIG", "PATH_USER_STATE", "PATH_USER_DATA",
                      "LOG_FILE"):
        valore = str(getattr(C, attributo, ""))
        if not valore.startswith(str(_SCRATCH)):
            errori.append(f"{attributo} = {valore}: fuori dallo scratch")
    vivo = Path.home() / ".local" / "state" / "metnos"
    for percorso in (C.PATH_USER_STATE, C.PATH_USER_CONFIG, C.PATH_USER_DATA):
        if vivo in Path(percorso).parents or Path(percorso) == vivo:
            errori.append(f"{percorso} punta allo stato vivo")
    return errori


def modo(percorso: Path) -> str:
    return oct(stat.S_IMODE(os.stat(percorso).st_mode))


# (nome, modo iniziale o None per file nuovo, argomenti espliciti, modo atteso)
CASI_ATOMICI = [
    ("esistente 664, preserve_existing_mode=True (difetto)",
     0o664, {"preserve_existing_mode": True}, "0o664"),
    ("esistente 644, new_mode=modo osservato (come sign_executor)",
     0o644, {"new_mode": 0o644}, "0o644"),
    ("esistente 664, new_mode=0600 SENZA preserve_existing_mode=False",
     0o664, {"new_mode": 0o600}, "0o664"),
    ("esistente 664, new_mode=0600, preserve_existing_mode=False",
     0o664, {"new_mode": 0o600, "preserve_existing_mode": False}, "0o600"),
    ("file NUOVO, new_mode=0644",
     None, {"new_mode": 0o644}, "0o644"),
    ("file NUOVO, modo di difetto",
     None, {}, "0o600"),
]


def prova_atomica() -> tuple[list[str], list[tuple[str, str]]]:
    errori: list[str] = []
    tabella: list[tuple[str, str]] = []
    vecchia = os.umask(0o002)          # the installation's umask, made explicit
    try:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for indice, (nome, iniziale, argomenti, atteso) in enumerate(CASI_ATOMICI):
                bersaglio = base / f"caso{indice}.bin"
                if iniziale is not None:
                    bersaglio.write_bytes(b"vecchio")
                    os.chmod(bersaglio, iniziale)
                _atomic_replace_bytes(bersaglio, b"nuovo", **argomenti)
                osservato = modo(bersaglio)
                tabella.append((nome, osservato))
                if osservato != atteso:
                    errori.append(f"{nome}: modo {osservato}, atteso {atteso}")
                if bersaglio.read_bytes() != b"nuovo":
                    errori.append(f"{nome}: il contenuto non e' stato sostituito")
                # no temporary sibling may survive a successful replacement
                residui = [p.name for p in base.iterdir()
                           if p.name.startswith(f"caso{indice}") and p != bersaglio]
                if residui:
                    errori.append(f"{nome}: temporanei residui {residui}")
    finally:
        os.umask(vecchia)
    return errori, tabella


def _funzione(albero: ast.Module, nome: str) -> ast.FunctionDef:
    for nodo in ast.walk(albero):
        if isinstance(nodo, ast.FunctionDef) and nodo.name == nome:
            return nodo
    raise AssertionError(f"funzione {nome} assente da {SORGENTE}")


def _chiamate(nodo: ast.AST, nome: str) -> list[ast.Call]:
    return [
        c for c in ast.walk(nodo)
        if isinstance(c, ast.Call)
        and isinstance(c.func, ast.Name) and c.func.id == nome
    ]


def prova_ingressi() -> list[str]:
    """Assert the mode policy of each public entry point on its own source."""
    albero = ast.parse(SORGENTE.read_text())
    errori: list[str] = []

    # --- sign_executor authoring -------------------------------------------
    autore = _funzione(albero, "_sign_executor_under_catalog_lock")
    chiamate = _chiamate(autore, "_atomic_replace_bytes")
    if not chiamate:
        errori.append(
            "il percorso authoring non chiama piu' _atomic_replace_bytes"
        )
    for chiamata in chiamate:
        parole = {k.arg for k in chiamata.keywords}
        if "new_mode" not in parole:
            errori.append(
                "una sostituzione del percorso authoring non passa new_mode: "
                "il modo osservato non e' piu' imposto"
            )
    # the observed mode must come from a stat of the existing manifest
    testo = ast.get_source_segment(SORGENTE.read_text(), autore) or ""
    if "stat.S_IMODE(manifest_path.stat().st_mode)" not in testo:
        errori.append(
            "il percorso authoring non deriva piu' il modo dal file esistente"
        )
    # and it must not truncate in place
    if _chiamate(autore, "open"):
        errori.append("il percorso authoring apre il file in scrittura")

    # --- publish_executor store-only ---------------------------------------
    pubblica = _funzione(albero, "publish_executor")
    if _chiamate(pubblica, "_atomic_replace_bytes"):
        errori.append(
            "publish_executor scrive il manifest: non e' piu' solo-negozio"
        )
    if _chiamate(pubblica, "open"):
        errori.append("publish_executor apre un file in scrittura")
    return errori


def prova_percorsi_toccati() -> list[str]:
    """The catalogue files live in runtime/; the signer works on executors/."""
    errori: list[str] = []
    testo = SORGENTE.read_text()
    autore = ast.get_source_segment(
        testo, _funzione(ast.parse(testo), "_sign_executor_under_catalog_lock")
    ) or ""
    for atteso in ("manifest_path", "sig_path"):
        if atteso not in autore:
            errori.append(f"il percorso authoring non nomina piu' {atteso}")
    if "manifest_dir" not in autore:
        errori.append("il percorso authoring non lavora piu' su manifest_dir")
    # nothing in the signer may name a runtime module of the context catalogue
    import ast as _ast
    catalogo = _ast.literal_eval(next(
        n.value for n in _ast.parse(
            (RADICE / "runtime" / "executor_birth_context_v1.py").read_text()
        ).body
        if isinstance(n, _ast.AnnAssign)
        and getattr(n.target, "id", "") == "CONTEXT_CATALOG_V1"
    ))
    nomi = sorted({f for _, _, files, _ in catalogo for f in files})
    citati = [n for n in nomi if n in testo]
    if citati:
        errori.append(f"sign.py nomina file del catalogo di contesto: {citati}")
    return errori


def main() -> int:
    fallimenti = 0

    errori, tabella = prova_atomica()
    print("== _atomic_replace_bytes, eseguita con umask 0002 ==")
    for nome, osservato in tabella:
        print(f"  {nome:62} -> {osservato}")
    print(f"{'ROSSO' if errori else 'verde'}  funzione atomica"
          + ("  -> " + "; ".join(errori) if errori else ""))
    fallimenti += bool(errori)

    for nome, fn in (
        ("radici utente isolate prima dell'import", radici_isolate),
        ("ingressi pubblici (authoring vs solo-negozio)", prova_ingressi),
        ("percorsi toccati dal firmatario", prova_percorsi_toccati),
    ):
        errori = fn()
        print(f"{'ROSSO' if errori else 'verde'}  {nome}"
              + ("  -> " + "; ".join(errori) if errori else ""))
        fallimenti += bool(errori)

    print()
    print("ESITO:", "tutte verdi" if not fallimenti else f"{fallimenti} PROVE ROSSE")
    return 1 if fallimenti else 0


if __name__ == "__main__":
    sys.exit(main())
