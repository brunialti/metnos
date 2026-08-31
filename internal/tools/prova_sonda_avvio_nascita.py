"""Targeted tests for the boot probe's exit contract.

The probe is a diagnostic whose *status* other procedures rely on, so what
needs testing is the contract, not the Birth machinery: each gate's refusal
must map to its own code, a gate whose prerequisite is red must not run, and
the diagnostics printed inside an error branch must never replace the original
refusal.  Stub modules give every outcome deterministically, with no keys, no
prepared set and no filesystem role model involved.

Run:  python3 internal/tools/prova_sonda_avvio_nascita.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

PROBE = Path(__file__).resolve().parent / "sonda_avvio_nascita.py"

STUB_SECURE_FS = '''
class BirthSecureFSError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)
'''

STUB_CONFIG = '''
import os
PATH_ROOT = os.environ["METNOS_INSTALL_ROOT"]
PATH_RUNTIME = os.path.join(PATH_ROOT, "runtime")
PATH_USER_CONFIG = os.environ.get("METNOS_USER_CONFIG", "/nonexistent")
PATH_USER_STATE = os.environ.get("METNOS_USER_STATE", "/nonexistent")
'''

STUB_BOOTSTRAP = '''
def birth_authority_is_prepared_v1():
    return True
'''

# One knob per gate, read from the environment so a single stub set covers
# every case the contract has to distinguish.
STUB_PREPARED_ROOT = '''
import os
from executor_birth_secure_fs import BirthSecureFSError


class PreparedRootError(Exception):
    def __init__(self, code):
        self.code = code
        self._internal_cause = BirthSecureFSError(code)
        super().__init__(code)


class _Session:
    def close(self):
        pass

    def read_file(self, components, *, maximum, exact_private):
        label = components[0]
        if label in os.environ.get("PROVA_RIFIUTA_FILE", "").split(","):
            raise BirthSecureFSError("birth_provisioning_acl_unsafe")
        return b"x"


def open_prepared_root_session_v1():
    if os.environ.get("PROVA_CANCELLO1") == "rosso":
        raise PreparedRootError("birth_provisioning_acl_unsafe")
    return _Session()


def open_distribution_sources_v1():
    if os.environ.get("PROVA_CANCELLO2") == "rosso":
        raise PreparedRootError("birth_provisioning_acl_unsafe")
    return _Session()


def read_prepared_set_v1():
    if os.environ.get("PROVA_CANCELLO3") == "rosso":
        raise PreparedRootError("birth_prepared_set_mismatch")

    class P:
        set_id = "finto"

    return P()
'''

STUB_CONTEXT = '''
CONTEXT_CATALOG_V1 = (
    ("standard", "1", ("alpha.py", "beta.py"), "productive"),
)
MAXIMUM_CONTEXT_SOURCE_BYTES_V1 = 1024
'''


def build_root(base: Path) -> Path:
    runtime = base / "runtime"
    runtime.mkdir(parents=True)
    for name, body in (
        ("config.py", STUB_CONFIG),
        ("executor_birth_bootstrap.py", STUB_BOOTSTRAP),
        ("executor_birth_prepared_root.py", STUB_PREPARED_ROOT),
        ("executor_birth_context_v1.py", STUB_CONTEXT),
        ("executor_birth_secure_fs.py", STUB_SECURE_FS),
    ):
        (runtime / name).write_text(body)
    (runtime / "alpha.py").write_text("a = 1\n")
    (runtime / "beta.py").write_text("b = 1\n")
    return base


def run(root: Path, **env_extra) -> tuple[int, str]:
    env = dict(os.environ)
    env["METNOS_INSTALL_ROOT"] = str(root)
    env.pop("PROVA_CANCELLO1", None)
    env.pop("PROVA_CANCELLO2", None)
    env.pop("PROVA_CANCELLO3", None)
    env.pop("PROVA_RIFIUTA_FILE", None)
    env.update({k: v for k, v in env_extra.items()})
    proc = subprocess.run(
        [sys.executable, str(PROBE)], env=env, capture_output=True, text=True,
        timeout=60,
    )
    return proc.returncode, proc.stdout + proc.stderr


CASI: list[tuple[str, dict, int, list[str], list[str]]] = [
    # nome, ambiente, uscita attesa, testo atteso, testo VIETATO
    ("tutti verdi", {}, 0, ["TUTTI I CANCELLI VERDI"], []),
    ("cancello 1 rosso", {"PROVA_CANCELLO1": "rosso"}, 2,
     ["cancelli 2, 2b, 3: NON ESEGUITI"], ["cancello 2:", "TUTTI I CANCELLI VERDI"]),
    ("cancello 2 rosso", {"PROVA_CANCELLO2": "rosso"}, 3,
     ["cancelli 2b, 3: NON ESEGUITI"], ["cancello 3:", "TUTTI I CANCELLI VERDI"]),
    ("cancello 2b rosso", {"PROVA_RIFIUTA_FILE": "beta.py"}, 4,
     ["RIFIUTATO beta.py", "cancello 3: NON ESEGUITO"], ["TUTTI I CANCELLI VERDI"]),
    ("cancello 3 rosso", {"PROVA_CANCELLO3": "rosso"}, 5,
     ["birth_prepared_set_mismatch"], ["TUTTI I CANCELLI VERDI"]),
]


def main() -> int:
    fallimenti = 0
    with tempfile.TemporaryDirectory() as tmp:
        root = build_root(Path(tmp) / "radice")

        for nome, env, atteso, deve, vietato in CASI:
            rc, out = run(root, **env)
            errori = []
            if rc != atteso:
                errori.append(f"uscita {rc}, attesa {atteso}")
            for frammento in deve:
                if frammento not in out:
                    errori.append(f"manca {frammento!r}")
            for frammento in vietato:
                if frammento in out:
                    errori.append(f"presente ma vietato {frammento!r}")
            print(f"{'ROSSO' if errori else 'verde'}  {nome}"
                  + ("  -> " + "; ".join(errori) if errori else ""))
            fallimenti += bool(errori)

        # A missing catalogue file must keep its own refusal: the mode helper
        # runs inside the error branch and must degrade, never raise.
        (root / "runtime" / "beta.py").unlink()
        rc, out = run(root, PROVA_RIFIUTA_FILE="beta.py")
        errori = []
        if rc != 4:
            errori.append(f"uscita {rc}, attesa 4")
        if "birth_provisioning_acl_unsafe" not in out:
            errori.append("l'errore originale e' andato perso")
        if "modo non leggibile" not in out:
            errori.append("la diagnostica del modo non e' degradata")
        print(f"{'ROSSO' if errori else 'verde'}  file assente: l'errore originale sopravvive"
              + ("  -> " + "; ".join(errori) if errori else ""))
        fallimenti += bool(errori)

        # The probe itself refuses to run rather than reporting a false green.
        for nome, env, atteso in (
            ("senza METNOS_INSTALL_ROOT", {"_drop": "1"}, 1),
            ("radice inesistente", {"_root": "/percorso/inesistente"}, 1),
        ):
            e = dict(os.environ)
            if env.get("_drop"):
                e.pop("METNOS_INSTALL_ROOT", None)
            else:
                e["METNOS_INSTALL_ROOT"] = env["_root"]
            proc = subprocess.run([sys.executable, str(PROBE)], env=e,
                                  capture_output=True, text=True, timeout=60)
            ok = proc.returncode == atteso
            print(f"{'verde' if ok else 'ROSSO'}  {nome}"
                  + ("" if ok else f"  -> uscita {proc.returncode}, attesa {atteso}"))
            fallimenti += not ok

    print()
    print("ESITO:", "tutte verdi" if not fallimenti else f"{fallimenti} PROVE ROSSE")
    return 1 if fallimenti else 0


if __name__ == "__main__":
    sys.exit(main())
