"""Probe whether the V2 Producer-request gates admit a subclass look-alike.

``ProducerRequestV2`` is safe only because one module-private constructor holds
its seal, and the seal is checked in ``__post_init__``. A subclass that defines
an empty ``__post_init__`` skips that check entirely and still satisfies
``isinstance``. Every gate that admits the type by ``isinstance`` therefore
admits a request nobody derived. This probe builds exactly that object — no
seal, a forged admission context — and offers it to each gate.

Exit codes: 0 every measured gate refuses it, 1 at least one admits it, 2 the
probe could not run and says why.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE / "runtime"))
os.environ.setdefault("METNOS_INSTALL_ROOT", str(RADICE))

try:
    import contract_store as DEPOSITO
    import executor_birth_operational as OPERATIVO
    import executor_birth_producer_store as PRODUTTORE
    import executor_birth_reattestation as RIATTESTA
    from executor_birth_identity import RevisionAuthor
    from executor_birth_producer_context import ProducerRequestV2
    from executor_birth_producer_table_v1 import executor_origin_v1
    from manifest_inventory import (
        ContractId, ManifestOrigin, ManifestRef, ManifestStatus,
    )
except Exception as errore:  # noqa: BLE001
    print(f"impalcatura non disponibile: {errore}")
    raise SystemExit(2)


def D(carattere: str) -> str:
    return "sha256:" + carattere * 64


class _SosiaPerEredita(ProducerRequestV2):
    """Same type by isinstance, and no validation at all."""

    def __post_init__(self) -> None:  # noqa: D401 - deliberately empty
        return None


CONTRATTO = "explicit:alpha/manifest.toml"
CONTESTO_INVENTATO = D("f")


def _falso() -> ProducerRequestV2:
    return _SosiaPerEredita(
        request_id=D("1"), objective_hash=D("2"), contract_id=CONTRATTO,
        generation_id=D("3"), admission_context_id=CONTESTO_INVENTATO,
        transition_id=D("4"), context_epoch=D("5"), set_id="6" * 64,
        candidate_source_id=D("7"), _seal=None,
    )


def _riferimento() -> ManifestRef:
    identificatore = ContractId(ManifestOrigin.EXPLICIT, "alpha/manifest.toml")
    return ManifestRef(
        identificatore, ManifestOrigin.EXPLICIT, ManifestStatus.ADMITTED,
        Path("/tmp"), Path("/tmp/alpha/manifest.toml"),
        "alpha/manifest.toml", (Path("/tmp/alpha"),),
    )


def _cancelli():
    """Every gate reachable without building a whole installation."""
    falso = _falso()
    legame = PRODUTTORE.ProducerReceiptBinding(
        falso.objective_hash, falso.candidate_source_id,
        executor_origin_v1(ManifestOrigin.EXPLICIT), RevisionAuthor.MODEL,
    )

    def deposito():
        DEPOSITO._sealed_v2_triple(_riferimento(), falso)

    def produttore():
        PRODUTTORE._sealed_request_v2(falso, legame)

    def postcondizione():
        # A correct gate refuses the request before it looks at the verifier.
        OPERATIVO.verify_reattestation_postcondition_v2(
            _riferimento(), request=falso, producer_receipt=b"",
            verify_admission=None, authenticate_terminal=None,
            registry=None, binding=legame, now=None,
            producer_db_path=Path("/nonexistent"), trusted_publics=(),
            store_root=None, lock_timeout=1,
        )

    def richiesta():
        RIATTESTA._sealed_reattestation_request_v2(
            None, b"", "attore", "motivo", legame, falso,
        )

    return (
        ("contract_store._sealed_v2_triple      ",
         "birth_receipt_v2_request_untrusted", deposito),
        ("producer_store._sealed_request_v2     ",
         "producer_request_v2_untrusted", produttore),
        ("operational.verify_postcondition_v2   ",
         "birth_postcondition_request_untrusted", postcondizione),
        ("reattestation._sealed_request_v2      ",
         "birth_reattestation_request_invalid", richiesta),
    )


NON_MISURATI = (
    ("executor_birth_commit_publisher.py", "isinstance(request, ProducerRequestV2)"),
    ("executor_birth_bootstrap.py", "isinstance(self.producer_request, ProducerRequestV2)"),
)


def principale() -> int:
    falso = _falso()
    print("sosia costruito senza sigillo e senza validazione:")
    print(f"  isinstance(ProducerRequestV2) = {isinstance(falso, ProducerRequestV2)}")
    print(f"  type(...) is ProducerRequestV2 = {type(falso) is ProducerRequestV2}")
    print(f"  contesto dichiarato            = {falso.admission_context_id[:23]}…")
    print()
    ammessi = []
    for nome, atteso, cancello in _cancelli():
        try:
            cancello()
        except Exception as errore:  # noqa: BLE001
            codice = getattr(errore, "code", "") or str(errore)
            if atteso in codice or atteso in str(errore):
                print(f"  {nome} RIFIUTA  {atteso}")
                continue
            # Refusing for another reason is not a refusal of the look-alike:
            # the gate never reached its own judgement.
            print(f"  {nome} rifiuta per ALTRO: {str(errore)[:46]}")
            ammessi.append(nome.strip())
            continue
        print(f"  {nome} AMMETTE")
        ammessi.append(nome.strip())
    print()
    print("non misurati qui, trovati leggendo (stessa forma, perimetro di A):")
    for percorso, frammento in NON_MISURATI:
        presente = frammento in (RADICE / "runtime" / percorso).read_text()
        print(f"  {percorso:38} {'ancora isinstance' if presente else 'corretto'}")
    print()
    if ammessi:
        print("ESITO: ammesso da:", ", ".join(ammessi))
        print("Un sosia per eredita' passa: isinstance non distingue una")
        print("sottoclasse, e una sottoclasse non esegue il sigillo.")
        return 1
    print("ESITO: ogni cancello misurato rifiuta il sosia per eredita'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(principale())
