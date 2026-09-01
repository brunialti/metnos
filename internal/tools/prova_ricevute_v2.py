#!/usr/bin/env python3
"""Proofs for the V2 admission receipts in the contract store.

WHAT THESE PROOFS COVER.  The store owns the filesystem protocol of a receipt:
where it lands, what makes a repeat idempotent, what makes a difference a
conflict, and what must never be touched.  It deliberately does not own the
cryptographic authentication, which the caller injects as ``verifier``; these
proofs therefore inject a deterministic verifier and exercise the store.

The fixture is a genuinely published generation, built with the same source
and key material as the portable certification suite, so the pointer, the
generation and the locks are real rather than simulated.

The stand-in for ``ContextSelectionV1`` is the same declared one used by the
Producer request proofs: agent A's module does not exist yet.  Composition is
proved by the acceptance proof of section 11 case 24, not here.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import types
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "runtime"))

MODULE = "executor_birth_context_selection"


@dataclass(frozen=True, slots=True)
class _StandInSelection:
    transition_id: str
    set_id: str
    admission_context_id: str
    context_epoch: int
    distribution: object = None


_stand_in = types.ModuleType(MODULE)
_stand_in.ContextSelectionV1 = _StandInSelection
sys.modules[MODULE] = _stand_in

import contract_store as C  # noqa: E402
import executor_birth_producer_context as P  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "cert_helpers", ROOT / "tests/portable/test_contract_store_certification.py",
)
cert = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cert)

CTX_A = "sha256:" + "a" * 64
CTX_B = "sha256:" + "b" * 64


def _selection(context: str = CTX_A, epoca: int = 2) -> _StandInSelection:
    return _StandInSelection(
        "sha256:" + "1" * 64, "sha256:" + "2" * 64, context, epoca,
    )


def _autorizzazione(context: str = CTX_A) -> C.BirthCommitAuthorization:
    return C.BirthCommitAuthorization(
        candidate_id="sha256:" + "c" * 64,
        semantic_core_id="sha256:" + "d" * 64,
        admission_context_id=context,
        predecessor_id=None,
        issuer=object(),
        verifier=object(),
    )


class _Ricevuta:
    def __init__(self, dati: dict) -> None:
        self.contract_id = dati["contract_id"]
        self.generation_id = dati["generation_id"]
        self.admission_context_id = dati["admission_context_id"]


def _verifica(encoded: bytes) -> _Ricevuta:
    return _Ricevuta(json.loads(encoded.decode("utf-8")))


def _corpo(ref, generation: str, context: str, nota: str = "") -> bytes:
    dati = {
        "contract_id": ref.contract_id.value,
        "generation_id": generation,
        "admission_context_id": context,
    }
    if nota:
        dati["nota"] = nota
    return json.dumps(dati, sort_keys=True).encode("utf-8")


class Negozio:
    def __init__(self, base: Path) -> None:
        ref, private = cert._make_source(base)
        self.ref = ref
        self.trusted = cert._trusted_from_raw((("author", cert._raw_public(private)),))
        self.root = base / "store"
        self.root.mkdir()
        risultato = C.publish_signed_source(
            ref, expected_generation_id=None, trusted_publics=self.trusted,
            store_root=self.root, lock_timeout=5.0,
        )
        self.generation = risultato.current_generation_id
        self.dir = self.root / C.contract_storage_key(ref.contract_id)

    def richiesta(self, context: str = CTX_A, epoca: int = 2):
        return P.build_producer_request_v2(
            _selection(context, epoca),
            contract_id=self.ref.contract_id,
            generation_id=self.generation,
        )

    def scrivi(self, *, context: str = CTX_A, nota: str = "", corpo: bytes | None = None,
               autorizzazione=None, richiesta=None) -> bytes:
        return C.persist_current_reattestation_receipt_v2(
            self.ref,
            corpo if corpo is not None else _corpo(self.ref, self.generation, context, nota),
            request=richiesta if richiesta is not None else self.richiesta(context),
            authorization=autorizzazione if autorizzazione is not None else _autorizzazione(context),
            verifier=_verifica,
            expected_bindings={
                "contract_id": self.ref.contract_id.value,
                "generation_id": self.generation,
            },
            trusted_publics=self.trusted, store_root=self.root, lock_timeout=5.0,
        )

    def leggi(self, *, context: str = CTX_A) -> bytes | None:
        return C.read_current_birth_receipt_v2(
            self.ref, request=self.richiesta(context),
            trusted_publics=self.trusted, store_root=self.root, lock_timeout=5.0,
        )

    def percorso_v2(self, context: str = CTX_A) -> Path:
        return C._birth_receipt_path_v2(self.dir, self.generation, context)

    def percorso_v1(self) -> Path:
        return C._birth_receipt_path(self.dir, self.generation)


ESITI: list[tuple[str, bool, str]] = []


def caso(nome):
    def wrap(fn):
        base = Path(tempfile.mkdtemp(prefix="prova-v2-"))
        try:
            fn(Negozio(base))
            ESITI.append((nome, True, ""))
        except Exception as exc:  # noqa: BLE001 - the runner reports every failure
            ESITI.append((nome, False, f"{type(exc).__name__}: {exc}"))
        finally:
            shutil.rmtree(base, ignore_errors=True)
        return fn
    return wrap


def _rifiuta(codice, fn):
    try:
        fn()
    except C.ContractStoreError as exc:
        assert exc.code == codice, f"atteso {codice}, ottenuto {exc.code}"
        return
    raise AssertionError(f"nessun rifiuto: atteso {codice}")


@caso("1 la ricevuta V2 sta in admission-receipts-v2/<gen>/<ctx>.json")
def _(n: Negozio) -> None:
    n.scrivi()
    p = n.percorso_v2()
    assert p.is_file(), "la ricevuta non e' stata scritta"
    assert p.parent.parent.name == "admission-receipts-v2"
    assert len(p.parent.name) == 64 and len(p.stem) == 64
    assert p.relative_to(n.dir).parts[0] == "admission-receipts-v2"


@caso("2 quello che si scrive si rilegge identico")
def _(n: Negozio) -> None:
    scritto = n.scrivi()
    assert n.leggi() == scritto


@caso("3 una ripetizione identica e' idempotente, non una seconda ricevuta")
def _(n: Negozio) -> None:
    primo = n.scrivi()
    secondo = n.scrivi()
    assert primo == secondo
    assert len(list(n.percorso_v2().parent.iterdir())) == 1


@caso("4 byte diversi sulla stessa tripla sono un conflitto, e il salvato resta")
def _(n: Negozio) -> None:
    primo = n.scrivi()
    _rifiuta("birth_reattestation_receipt_conflict", lambda: n.scrivi(nota="diversa"))
    assert n.leggi() == primo, "la ricevuta salvata e' stata alterata"


@caso("5 due contesti coesistono: triple distinte, nessuna sovrascrittura")
def _(n: Negozio) -> None:
    a = n.scrivi(context=CTX_A)
    b = n.scrivi(context=CTX_B)
    assert a != b
    assert n.leggi(context=CTX_A) == a
    assert n.leggi(context=CTX_B) == b
    assert n.percorso_v2(CTX_A).parent == n.percorso_v2(CTX_B).parent
    assert len(list(n.percorso_v2().parent.iterdir())) == 2


@caso("6 la ricevuta storica V1 non viene ne' letta ne' toccata")
def _(n: Negozio) -> None:
    v1 = n.percorso_v1()
    v1.parent.mkdir(mode=0o700, parents=True)
    v1.write_bytes(b"{\"storica\": true}")
    prima = v1.read_bytes()
    n.scrivi()
    assert v1.read_bytes() == prima, "la V1 e' stata riscritta"
    assert n.percorso_v2() != v1


@caso("7 nessun ripiego automatico da V2 a V1")
def _(n: Negozio) -> None:
    v1 = n.percorso_v1()
    v1.parent.mkdir(mode=0o700, parents=True)
    v1.write_bytes(b"{\"storica\": true}")
    assert n.leggi() is None, "la lettura V2 e' ripiegata sulla V1"


@caso("8 una richiesta forgiata non e' una richiesta")
def _(n: Negozio) -> None:
    @dataclass(frozen=True, slots=True)
    class Sosia:
        contract_id: str
        generation_id: str
        admission_context_id: str

    sosia = Sosia(n.ref.contract_id.value, n.generation, CTX_A)
    _rifiuta("birth_receipt_v2_request_untrusted", lambda: n.scrivi(richiesta=sosia))
    _rifiuta(
        "birth_receipt_v2_request_untrusted",
        lambda: C.read_current_birth_receipt_v2(
            n.ref, request=sosia, trusted_publics=n.trusted, store_root=n.root,
        ),
    )


@caso("9 una richiesta di un altro contratto e' rifiutata")
def _(n: Negozio) -> None:
    altra = P.build_producer_request_v2(
        _selection(), contract_id=type("X", (), {"value": "altro/manifest.toml"})(),
        generation_id=n.generation,
    )
    _rifiuta("birth_receipt_v2_request_mismatch", lambda: n.scrivi(richiesta=altra))


@caso("10 §11.13 un solo selettore: autorizzazione e richiesta devono concordare")
def _(n: Negozio) -> None:
    _rifiuta(
        "birth_receipt_v2_context_conflict",
        lambda: n.scrivi(context=CTX_A, autorizzazione=_autorizzazione(CTX_B)),
    )
    assert not n.percorso_v2(CTX_A).exists(), "ha scritto pur avendo rifiutato"


@caso("11 una ricevuta che dichiara un contesto diverso dal percorso e' rifiutata")
def _(n: Negozio) -> None:
    corpo = _corpo(n.ref, n.generation, CTX_B)
    _rifiuta(
        "birth_receipt_v2_context_conflict",
        lambda: n.scrivi(context=CTX_A, corpo=corpo),
    )


@caso("12 il puntatore corrente cambiato ferma scrittura e lettura")
def _(n: Negozio) -> None:
    n.scrivi()
    # A well formed pointer to another generation: the store must refuse
    # because the target moved, not because the file is unreadable.
    (n.dir / "current").write_text("sha256:" + "e" * 64 + "\n", encoding="utf-8")
    _rifiuta("birth_reattestation_current_changed", n.scrivi)
    _rifiuta("birth_reattestation_current_changed", n.leggi)


@caso("12-bis un puntatore corrente malformato ferma tutto prima")
def _(n: Negozio) -> None:
    n.scrivi()
    (n.dir / "current").write_text("sha256:" + "e" * 64, encoding="utf-8")
    _rifiuta("current_invalid", n.scrivi)
    _rifiuta("current_invalid", n.leggi)


@caso("13 un contesto non canonico non diventa un percorso")
def _(n: Negozio) -> None:
    for guasto in ("sha256:" + "A" * 64, "sha256:abc", "../fuga", "sha256:" + "g" * 64):
        _rifiuta(
            "admission_context_id_invalid",
            lambda g=guasto: C._birth_receipt_path_v2(n.dir, n.generation, g),
        )


@caso("14 un collegamento al posto della cartella delle ricevute blocca")
def _(n: Negozio) -> None:
    altrove = n.root.parent / "altrove"
    altrove.mkdir()
    (n.dir / "admission-receipts-v2").symlink_to(altrove, target_is_directory=True)
    _rifiuta("birth_receipt_store_invalid", n.scrivi)
    assert not any(altrove.iterdir()), "ha scritto in una cartella estranea"


@caso("15 la prova delle correnti e' ordinata, senza duplicati e senza buchi")
def _(n: Negozio) -> None:
    n.scrivi()
    prova = C.current_receipt_proof(
        [(n.ref, n.richiesta())], trusted_publics=n.trusted, store_root=n.root,
    )
    assert prova.admission_context_id == CTX_A
    assert len(prova.entries) == 1
    voce = prova.entries[0]
    assert voce.contract_id == n.ref.contract_id.value
    assert voce.generation_id == n.generation
    assert voce.receipt_hash == C.admission_receipt_hash(n.leggi())
    # A repeated pair is the same fact, not a duplicate entry.
    ripetuta = C.current_receipt_proof(
        [(n.ref, n.richiesta()), (n.ref, n.richiesta())],
        trusted_publics=n.trusted, store_root=n.root,
    )
    assert ripetuta.entries == prova.entries


@caso("16 §11.5 una generazione senza ricevuta non e' un successo vuoto")
def _(n: Negozio) -> None:
    _rifiuta(
        "birth_receipt_v2_missing",
        lambda: C.current_receipt_proof(
            [(n.ref, n.richiesta())], trusted_publics=n.trusted, store_root=n.root,
        ),
    )
    _rifiuta(
        "birth_receipt_v2_missing",
        lambda: C.current_receipt_proof(
            [], trusted_publics=n.trusted, store_root=n.root,
        ),
    )


@caso("17 la prova rifiuta due contesti diversi nello stesso passaggio")
def _(n: Negozio) -> None:
    n.scrivi(context=CTX_A)
    n.scrivi(context=CTX_B)
    _rifiuta(
        "birth_receipt_v2_context_conflict",
        lambda: C.current_receipt_proof(
            [(n.ref, n.richiesta(CTX_A)), (n.ref, n.richiesta(CTX_B))],
            trusted_publics=n.trusted, store_root=n.root,
        ),
    )


@caso("18 i permessi della ricevuta e delle sue cartelle sono ristretti")
def _(n: Negozio) -> None:
    n.scrivi()
    p = n.percorso_v2()
    assert p.stat().st_mode & 0o777 == 0o600, oct(p.stat().st_mode & 0o777)
    for d in (p.parent, p.parent.parent):
        assert d.stat().st_mode & 0o777 == 0o700, f"{d}: {oct(d.stat().st_mode & 0o777)}"


def main() -> int:
    for nome, ok, dettaglio in ESITI:
        print(f"  {'ok  ' if ok else 'ROSSO'}  {nome}")
        if not ok:
            print(f"          {dettaglio}")
    rossi = [n for n, ok, _ in ESITI if not ok]
    print(f"\nESITO: {'tutte verdi' if not rossi else f'{len(rossi)} rosse'}  ({len(ESITI)} casi)")
    return 1 if rossi else 0


if __name__ == "__main__":
    raise SystemExit(main())
