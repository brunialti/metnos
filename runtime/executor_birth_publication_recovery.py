"""Targeted recovery of one incomplete first-publication container.

An interrupted first publication leaves a container that owns nothing: no
binding, no current pointer, an empty ``generations/`` and the ``writer.lock``
the interrupted writer created.  The productive inventory reports it as a
problem and every census that demands ``problems == ()`` therefore blocks - as
it must, because a store the inventory does not fully own is not a store F4 can
reason about.

Removing it by hand is not an option: section 7.6 of the group-2 report forbids
removing a final root, and a path typed by a human is exactly the input this
module refuses to take.  So the recovery is targeted, and its safety comes from
what it will *not* accept:

* the caller names a **contract**, never a path.  The ``ContractId`` must come
  from the authoring inventory, and the container is then addressed by that
  identity's own storage key - so a container that no contract claims cannot be
  reached at all;
* the container is re-read under the catalog lock and the contract's writer
  lock, with ``lstat`` at every step: a link, an unexpected object, a
  ``binding.json``, a ``current``, a non-empty ``generations/`` or anything
  besides the ordinary ``writer.lock`` is a refusal, not a case to handle;
* the only admitted postcondition is the removal of that empty container and an
  ``fsync`` of the store root.  Nothing else is written, moved or created.

Using it against a live store is a separate operational decision: this module
provides the primitive and refuses the unsafe shapes, and it is deliberately not
part of any automatic F4 execution.
"""
from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

NOME_BINDING = "binding.json"
NOME_CORRENTE = "current"
NOME_GENERAZIONI = "generations"
NOME_LUCCHETTO = "writer.lock"
ATTESI = frozenset({NOME_GENERAZIONI, NOME_LUCCHETTO})


class RecuperoPubblicazioneError(RuntimeError):
    """The container is not the exact incomplete shape, so nothing is done."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class EsitoRecupero:
    """What the primitive observed and what it removed - never a hope."""

    contract_id: str
    storage_key: str
    rimosso: bool
    percorso: str


def _tipo(percorso: Path) -> str:
    """Ordinary kind of an object, reached without following any link."""
    try:
        modo = percorso.lstat().st_mode
    except FileNotFoundError:
        return "assente"
    except OSError as exc:
        raise RecuperoPubblicazioneError("lstat_fallita", str(exc)) from None
    if stat.S_ISLNK(modo):
        return "collegamento"
    if stat.S_ISDIR(modo):
        return "directory"
    if stat.S_ISREG(modo):
        return "file"
    return "altro"


def _verifica_contenitore(contenitore: Path) -> None:
    """Refuse anything that is not the exact incomplete shape."""
    if _tipo(contenitore) != "directory":
        raise RecuperoPubblicazioneError(
            "contenitore_non_ordinario", str(contenitore)
        )
    for nome in (NOME_BINDING, NOME_CORRENTE):
        if _tipo(contenitore / nome) != "assente":
            raise RecuperoPubblicazioneError("contenitore_non_incompleto", nome)

    voci = sorted(item.name for item in contenitore.iterdir())
    if set(voci) - ATTESI:
        raise RecuperoPubblicazioneError(
            "oggetti_inattesi", ",".join(sorted(set(voci) - ATTESI))
        )
    if _tipo(contenitore / NOME_GENERAZIONI) != "directory":
        raise RecuperoPubblicazioneError(
            "generazioni_non_ordinarie", NOME_GENERAZIONI
        )
    if any((contenitore / NOME_GENERAZIONI).iterdir()):
        raise RecuperoPubblicazioneError("generazioni_non_vuote", NOME_GENERAZIONI)
    if NOME_LUCCHETTO in voci and _tipo(contenitore / NOME_LUCCHETTO) != "file":
        raise RecuperoPubblicazioneError(
            "lucchetto_non_ordinario", NOME_LUCCHETTO
        )


def recupera_contenitore_incompleto(
    contract_id, *, store_root: Path | str, applica: bool = False,
) -> EsitoRecupero:
    """Inspect - and only on request remove - one incomplete container.

    ``applica=False`` is the default on purpose: the ordinary use is to observe
    and report, and removing anything is a decision the caller has to state.
    """
    from contract_store import ContractStoreError, _writer_lock, catalog_admission_lock
    from manifest_inventory import ContractId

    if not isinstance(contract_id, ContractId):
        raise RecuperoPubblicazioneError("identita_non_canonica", repr(contract_id))
    radice = Path(store_root)
    if _tipo(radice) != "directory":
        raise RecuperoPubblicazioneError("radice_non_ordinaria", str(radice))
    contenitore = radice / contract_id.storage_key

    # The shape is verified BEFORE any lock, and for a reason that is not
    # ordering hygiene: the productive writer lock creates the contract
    # directories it needs, so taking it first would have this primitive
    # fabricate a container for any contract and then remove what it had just
    # made.  A recovery that can create its own subject is not a recovery.
    _verifica_contenitore(contenitore)

    with catalog_admission_lock():
        try:
            with _writer_lock(contract_id, store_root=radice):
                # Re-read under the lock: what was true a moment ago has to be
                # true while nobody else can change it.
                _verifica_contenitore(contenitore)
                if not applica:
                    return EsitoRecupero(
                        contract_id.value, contract_id.storage_key, False,
                        str(contenitore),
                    )
                # Only postcondition: the empty container goes, and the store
                # root is made durable.  The lock file is inside the container
                # being removed, so it is released with it.
                (contenitore / NOME_GENERAZIONI).rmdir()
                lucchetto = contenitore / NOME_LUCCHETTO
                if _tipo(lucchetto) == "file":
                    lucchetto.unlink()
        except ContractStoreError as exc:
            raise RecuperoPubblicazioneError(
                "lucchetto_non_ottenuto", getattr(exc, "code", str(exc))
            ) from None
        if applica:
            contenitore.rmdir()
            descrittore = os.open(radice, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descrittore)
            finally:
                os.close(descrittore)
    return EsitoRecupero(
        contract_id.value, contract_id.storage_key, applica, str(contenitore)
    )
