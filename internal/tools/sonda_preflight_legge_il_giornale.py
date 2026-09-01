"""Probe whether the standalone preflight can still read the runtime journal.

``executor_birth_admin_preflight`` carries its own copy of the coordinator
record schema, because it is a standalone gate: it must read the journal
without importing the runtime that wrote it. The two copies must therefore
agree exactly — the preflight demands set equality on the keys.

This probe encodes real records with the runtime and offers them to the
preflight decoder, then tampers one field at a time and requires the two
decoders to give the SAME verdict. Matching key sets are not enough: a gate
that accepts a field without checking it is worse than one that rejects it,
and a gate that is stricter than the runtime refuses journals the runtime
considers valid.

Exit codes: 0 the two agree everywhere, 1 they disagree, 2 the probe could not
run and says why.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE / "runtime"))
sys.path.insert(0, str(RADICE / "tests" / "portable"))
os.environ.setdefault("METNOS_INSTALL_ROOT", str(RADICE))

try:
    import executor_birth_admin_preflight as PREVOLO
    import executor_birth_ownership_coordinator as COORDINATORE
    import test_executor_birth_ownership_coordinator_v2 as AIUTI
    from executor_birth_ownership_coordinator import _RECORD_KEYS_V2
except Exception as errore:  # noqa: BLE001
    print(f"impalcatura non disponibile: {errore}")
    raise SystemExit(2)


def principale() -> int:
    scritte = set(_RECORD_KEYS_V2)
    accettate = set(PREVOLO._COORDINATOR_RECORD_KEYS_V2)
    print(f"chiavi che il runtime scrive:    {len(scritte)}")
    print(f"chiavi che il preflight accetta: {len(accettate)}")
    ignote = sorted(scritte - accettate)
    attese_a_vuoto = sorted(accettate - scritte)
    if ignote:
        print(f"\nscritte e non accettate ({len(ignote)}):")
        for chiave in ignote:
            print("   ", chiave)
    if attese_a_vuoto:
        print(f"\naccettate e mai scritte ({len(attese_a_vuoto)}):")
        for chiave in attese_a_vuoto:
            print("   ", chiave)

    rivendicazione = AIUTI.bound_claim(
        release_sequence=1, previous_head_id=None,
        closed_build_id=AIUTI.D("3"), source_id=AIUTI.D("2"),
        previous_closed_build_id=None, previous_cutover_id=None,
    )
    atti = AIUTI.transaction_records(
        rivendicazione, end_sequence=6, previous_closed_build_id=None,
        previous_cutover_id=None, cutover_id=AIUTI.D("4"),
        head_id=AIUTI.D("5"),
    )
    print()
    rifiutati = []
    for atto in atti:
        try:
            PREVOLO._decode_coordinator_record_v2(atto.encode())
        except Exception as errore:  # noqa: BLE001
            codice = getattr(errore, "code", type(errore).__name__)
            dettaglio = getattr(errore, "detail", "")
            print(f"  atto {atto.sequence}  RIFIUTATO  {codice} / {dettaglio}")
            rifiutati.append(atto.sequence)
            continue
        print(f"  atto {atto.sequence}  letto")
    # Same record, one field falsified at a time: the two must agree.
    import json

    print()
    print("stesso atto, un campo falsificato per volta:")
    discordi = []
    atto = atti[-1]
    valore = json.loads(atto.encode().decode("ascii"))
    for campo, falso in (
        ("target_set_id", "z" * 64),
        ("target_context_epoch", "non-un-digest"),
        ("provisioning_transaction_id", "0" * 31),
        ("current_inventory_hash", "sha256:" + "0" * 64),
        ("context_transition_id", "sha256:" + "e" * 64),
        ("previous_set_id", "1" * 63),
        ("target_admission_context_id", ""),
        ("previous_context_epoch", None),
    ):
        modificato = dict(valore)
        modificato[campo] = falso
        try:
            codificato = COORDINATORE._canonical(modificato)
        except Exception:  # noqa: BLE001 - a value the encoder cannot frame
            print(f"  {campo:30} non codificabile, salto")
            continue
        esiti = []
        for decodifica in (
            COORDINATORE._decode_record_v2, PREVOLO._decode_coordinator_record_v2,
        ):
            try:
                decodifica(codificato)
                esiti.append("accetta")
            except Exception:  # noqa: BLE001
                esiti.append("rifiuta")
        concordi = esiti[0] == esiti[1]
        print(f"  {campo:30} runtime {esiti[0]}, preflight {esiti[1]}"
              f"{'' if concordi else '   DISCORDI'}")
        if not concordi:
            discordi.append(campo)

    print()
    if discordi:
        print("ESITO: i due giudici non concordano su:", ", ".join(discordi))
        print("Il cancello autonomo deve dire quello che dice il runtime, non")
        print("di piu' e non di meno.")
        return 1
    if rifiutati:
        print(f"ESITO: il preflight rifiuta {len(rifiutati)} atti su {len(atti)}")
        print("che il runtime scrive davvero. Le due copie dello schema si")
        print("sono separate, e il cancello autonomo non legge piu' il")
        print("giornale che deve sorvegliare.")
        return 1
    print("ESITO: il preflight legge ogni atto che il runtime scrive.")
    return 0


if __name__ == "__main__":
    raise SystemExit(principale())
