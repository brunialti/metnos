"""Sonda di avvio: esercita i cancelli di nascita su radici DICHIARATE."""
import os, sys, json

ROOT = os.environ["METNOS_INSTALL_ROOT"]
sys.path.insert(0, os.path.join(ROOT, "runtime"))

import config as C
print("== CHE COSA STO MISURANDO ==")
print("  PATH_ROOT        =", C.PATH_ROOT)
print("  PATH_RUNTIME     =", C.PATH_RUNTIME, "mode=%o" % (os.stat(C.PATH_RUNTIME).st_mode & 0o7777))
print("  PATH_USER_CONFIG =", C.PATH_USER_CONFIG)
print("  PATH_USER_STATE  =", C.PATH_USER_STATE)
print()

def show(label, fn):
    print(f"-- {label}")
    try:
        v = fn()
        print("   ESITO: ok", ("-> " + repr(v)[:120]) if v is not None else "")
        return True
    except BaseException as exc:
        code = getattr(exc, "code", None)
        cause = getattr(exc, "_internal_cause", None)
        print(f"   ESITO: FALLITO  {type(exc).__name__}  code={code}")
        if cause is not None:
            print(f"   causa interna: {type(cause).__name__} code={getattr(cause,'code',None)} {cause}")
        return False

from executor_birth_bootstrap import birth_authority_is_prepared_v1
from executor_birth_prepared_root import (
    open_prepared_root_session_v1, open_distribution_sources_v1, read_prepared_set_v1,
)
print("insieme preparato rilevato:", birth_authority_is_prepared_v1())
print()

show("cancello 1: apertura radice di nascita preparata", lambda: (open_prepared_root_session_v1().close() or "sessione aperta e chiusa"))
ok_sources = show("cancello 2: apertura della distribuzione installata (PATH_RUNTIME)",
                  lambda: (open_distribution_sources_v1().close() or "sessione aperta e chiusa"))

if ok_sources:
    # quale file del catalogo fallisce per primo
    import executor_birth_context_v1 as ctx
    from executor_birth_secure_fs import BirthSecureFSError
    print("-- cancello 2b: lettura dei file del catalogo, uno per uno")
    s = open_distribution_sources_v1()
    primo = None
    letti = 0
    try:
        for name, version, files, enf in ctx.CONTEXT_CATALOG_V1:
            for label in files:
                try:
                    s.read_file((label,), maximum=ctx.MAXIMUM_CONTEXT_SOURCE_BYTES_V1, exact_private=False)
                    letti += 1
                except BirthSecureFSError as exc:
                    if primo is None:
                        primo = (name, label, exc.code)
                    print(f"   RIFIUTATO {label:45} code={exc.code}  mode=%o" % (os.stat(os.path.join(str(C.PATH_RUNTIME), label)).st_mode & 0o7777))
    finally:
        s.close()
    print(f"   letti con successo: {letti}; primo rifiuto: {primo}")

print()
show("cancello 3: read_prepared_set_v1 (materiale + confronto impronte)", read_prepared_set_v1)
