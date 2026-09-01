"""Probe the Birth secure-filesystem roles across the V1/V2 transaction epochs.

Adding a second provisioning epoch means the closed role table now answers for
two prefixes. The one failure that matters is a downgrade: a position that is
confidential under V1 must never come back integrity-only under V2, where
integrity-only means world-readable. This probe compares the two epochs
position by position and fails on any downgrade, on any silently classified
key material, and on a header crossing between epochs.

Exit codes: 0 the epochs agree safely, 1 a downgrade or a crossing, 2 the probe
could not run and says why.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE / "runtime"))
os.environ.setdefault("METNOS_INSTALL_ROOT", str(RADICE))

try:
    import executor_birth_secure_fs as FS
except Exception as errore:  # noqa: BLE001
    print(f"filesystem sicuro non disponibile: {errore}")
    raise SystemExit(2)

for _nome in (
    "_TRANSACTION_PREFIX", "_TRANSACTION_PREFIX_V2", "_matching_rows",
    "_KEY_PREFIX", "_BirthObjectRole",
):
    if not hasattr(FS, _nome):
        print(f"il modulo non espone piu' {_nome}: sonda da riscrivere")
        raise SystemExit(2)

NONCE = "0" * 32
V1 = FS._TRANSACTION_PREFIX + NONCE
V2 = FS._TRANSACTION_PREFIX_V2 + NONCE
CONFIDENZIALE = FS._BirthObjectRole.birth_confidential
INTEGRITA = FS._BirthObjectRole.birth_integrity_only

# Positions inside one provisioning transaction, named without their epoch.
CODE = (
    (),
    ("author-root-v1",),
    ("author-root-v1", "keystore.json"),
    ("author-root-v1", "birth-keystore.lock"),
    ("author-root-v1", "private"),
    ("author-root-v1", "private", FS._KEY_PREFIX + "a" * 64 + ".key"),
    ("author-root-v1", "public"),
    ("author-root-v1", "public", FS._KEY_PREFIX + "a" * 64 + ".pub"),
    ("authority-set",),
    ("authority-set", "set.json"),
    ("checkpoints-v1",),
    ("checkpoints-v1", "0" * 19 + "1.json"),
    ("prepared-v1.json",),
    ("transaction-v1.json",),
    ("transaction-v2.json",),
    (".transaction-v1.pending." + NONCE,),
    (".transaction-v2.pending." + NONCE,),
)


def _ruoli(radice: str, coda: tuple[str, ...]) -> set:
    """Every role the closed table gives one position, or the empty set."""
    return {
        ruolo for _pattern, _kind, ruolo in FS._matching_rows((radice,) + coda)
    }


def _nome(insieme: set) -> str:
    if not insieme:
        return "nessuna"
    return "+".join(sorted(
        "confidenziale" if ruolo is CONFIDENZIALE else
        "integrita" if ruolo is INTEGRITA else str(ruolo)
        for ruolo in insieme
    ))


def principale() -> int:
    guasti: list[str] = []
    print(f"{'posizione':52} {'V1':16} {'V2':16}")
    for coda in CODE:
        uno, due = _ruoli(V1, coda), _ruoli(V2, coda)
        etichetta = "/".join(coda) if coda else "(radice)"
        print(f"{etichetta:52} {_nome(uno):16} {_nome(due):16}")
        if CONFIDENZIALE in uno and INTEGRITA in due:
            guasti.append(
                f"DECLASSAMENTO su {etichetta}: confidenziale in V1, "
                "leggibile da tutti in V2"
            )
        if coda and coda[-1].startswith(FS._KEY_PREFIX) and INTEGRITA in due:
            if not coda[-1].endswith(".pub"):
                guasti.append(
                    f"CHIAVE PRIVATA leggibile da tutti in V2: {etichetta}"
                )
    # A header must not be readable across its own epoch.
    incroci = (
        ("transaction-v2.json", V1, "un'intestazione V2 dentro una posa V1"),
        ("transaction-v1.json", V2, "un'intestazione V1 dentro una posa V2"),
        (".transaction-v2.pending." + NONCE, V1, "un pendente V2 dentro V1"),
        (".transaction-v1.pending." + NONCE, V2, "un pendente V1 dentro V2"),
    )
    print()
    for nome, radice, descrizione in incroci:
        ruoli = _ruoli(radice, (nome,))
        esito = "rifiutato" if not ruoli else f"ACCETTATO ({_nome(ruoli)})"
        print(f"  {descrizione:44} {esito}")
        if ruoli:
            guasti.append(f"INCROCIO DI EPOCA: {descrizione} viene classificato")

    print()
    if guasti:
        for guasto in guasti:
            print("ESITO:", guasto)
        return 1
    print("ESITO: nessun declassamento fra le due epoche, nessun incrocio.")
    print("Le posizioni che V2 non conosce non vengono classificate, e una")
    print("posizione non classificata e' rifiutata, non lasciata libera.")
    return 0


if __name__ == "__main__":
    raise SystemExit(principale())
