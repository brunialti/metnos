"""Targeted recovery of one incomplete first-publication container.

An interrupted first publication leaves a container that owns nothing: no
binding, no current pointer, an empty ``generations/`` and the ``writer.lock``
the interrupted writer created.  The productive inventory reports it as a
problem, so every census that demands ``problems == ()`` blocks - as it must,
because a store the inventory does not fully own is not a store F4 can reason
about.  Removing it by hand is forbidden (group-2 report, section 7.6), and a
path typed by a caller is exactly the input this module refuses.

The safety of this primitive is entirely in what it will not accept, and the
first version got four of those wrong.  What it does now:

**The caller cannot name a target.**  It presents an authorization that the
authoring inventory produced (:class:`AutorizzazioneRecupero`); the container is
then addressed by that identity's own storage key.  A ``ContractId`` alone
proves syntax, not provenance - anyone can build one - so a bare identity is
refused.

**Observing does not write.**  Inspection never takes the writer lock, because
the productive lock *creates* the lock file, and a mode that reports "I removed
nothing" must not have created something.  Inspection and application are two
entry points with two postconditions.

**Names are resolved once.**  Every check and every removal goes through file
descriptors opened with ``O_NOFOLLOW``, relative to the parent, and the
container's identity ``(st_dev, st_ino)`` is compared before and after the
locks.  Checking with ``lstat`` and then operating by name let a synchronised
substitution make the removals land inside a foreign directory - which is worse
than doing nothing at all.

**There is one recoverable commit point.**  The container is first renamed,
without replacement, to a durable name derived from the authorization; only then
is it emptied and removed.  An interruption at any step leaves a shape the next
attempt recognises, instead of a half-removed container that the shape check
then refuses forever.

Using this against a live store is a separate operational decision: the module
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
PREFISSO_RITIRO = ".recupero-"


class RecuperoPubblicazioneError(RuntimeError):
    """The container is not the exact incomplete shape, so nothing is done."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class AutorizzazioneRecupero:
    """Proof that the authoring inventory claims this identity.

    Only :func:`autorizza_dall_inventario` builds one, and it does so by finding
    the contract in the productive authoring inventory.  A caller that could
    construct this freely would be back to naming its own target.
    """

    contract_id: object
    storage_key: str
    _token: object

    def __post_init__(self) -> None:
        if self._token is not _TOKEN:
            raise RecuperoPubblicazioneError("autorizzazione_non_emessa")


_TOKEN = object()


@dataclass(frozen=True, slots=True)
class EsitoRecupero:
    """What the primitive observed and what it removed - never a hope."""

    contract_id: str
    storage_key: str
    rimosso: bool
    percorso: str


def autorizza_dall_inventario(contract_id_value: str) -> AutorizzazioneRecupero:
    """Issue an authorization only for a contract authoring actually declares."""
    from manifest_inventory import inventory_authoring_manifests

    inventario = inventory_authoring_manifests()
    if inventario.problems:
        raise RecuperoPubblicazioneError(
            "inventario_autoriale_con_problemi", str(len(inventario.problems))
        )
    for ref in inventario.manifests:
        if ref.contract_id.value == contract_id_value:
            return AutorizzazioneRecupero(
                ref.contract_id, ref.contract_id.storage_key, _TOKEN
            )
    raise RecuperoPubblicazioneError(
        "contratto_non_inventariato", contract_id_value
    )


def _apri_directory(nome: str, *, dir_fd: int | None = None) -> int:
    """Open a directory without following a link, relative to a parent."""
    if not hasattr(os, "O_DIRECTORY"):
        raise RecuperoPubblicazioneError("piattaforma_non_supportata", os.name)
    try:
        return os.open(nome, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                       dir_fd=dir_fd)
    except FileNotFoundError:
        raise RecuperoPubblicazioneError("contenitore_assente", nome) from None
    except OSError as exc:
        # ELOOP arrives here when the name is a link: a refusal, not a case.
        raise RecuperoPubblicazioneError("contenitore_non_ordinario",
                                         f"{nome}: {exc.strerror}") from None


def _identita(fd: int) -> tuple[int, int]:
    valore = os.fstat(fd)
    return valore.st_dev, valore.st_ino


def _verifica_forma(fd_contenitore: int) -> None:
    """Refuse anything that is not the exact incomplete shape.

    Every question is asked of the open descriptor or relative to it, so no
    answer can be about a different object than the one that will be modified.
    """
    for nome in (NOME_BINDING, NOME_CORRENTE):
        try:
            os.lstat(nome, dir_fd=fd_contenitore)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise RecuperoPubblicazioneError("lstat_fallita", str(exc)) from None
        raise RecuperoPubblicazioneError("contenitore_non_incompleto", nome)

    voci = set(os.listdir(fd_contenitore))
    inattesi = voci - ATTESI
    if inattesi:
        raise RecuperoPubblicazioneError("oggetti_inattesi",
                                         ",".join(sorted(inattesi)))
    modo = os.lstat(NOME_GENERAZIONI, dir_fd=fd_contenitore).st_mode
    if not stat.S_ISDIR(modo) or stat.S_ISLNK(modo):
        raise RecuperoPubblicazioneError("generazioni_non_ordinarie",
                                         NOME_GENERAZIONI)
    fd_generazioni = _apri_directory(NOME_GENERAZIONI, dir_fd=fd_contenitore)
    try:
        if os.listdir(fd_generazioni):
            raise RecuperoPubblicazioneError("generazioni_non_vuote",
                                             NOME_GENERAZIONI)
    finally:
        os.close(fd_generazioni)
    if NOME_LUCCHETTO in voci:
        modo = os.lstat(NOME_LUCCHETTO, dir_fd=fd_contenitore).st_mode
        if not stat.S_ISREG(modo):
            raise RecuperoPubblicazioneError("lucchetto_non_ordinario",
                                             NOME_LUCCHETTO)


def ispeziona_contenitore_incompleto(
    autorizzazione: AutorizzazioneRecupero, *, store_root: Path | str,
) -> EsitoRecupero:
    """Read-only inspection: it takes no lock and writes nothing.

    The writer lock is deliberately not taken here.  The productive lock creates
    the lock file, so an inspection that took it would report "nothing removed"
    after having created something - which is not an inspection.
    """
    if not isinstance(autorizzazione, AutorizzazioneRecupero):
        raise RecuperoPubblicazioneError("autorizzazione_assente",
                                         type(autorizzazione).__name__)
    fd_radice = _apri_directory(os.fspath(store_root))
    try:
        fd_contenitore = _apri_directory(autorizzazione.storage_key,
                                         dir_fd=fd_radice)
        try:
            _verifica_forma(fd_contenitore)
        finally:
            os.close(fd_contenitore)
    finally:
        os.close(fd_radice)
    return EsitoRecupero(
        autorizzazione.contract_id.value, autorizzazione.storage_key, False,
        str(Path(store_root) / autorizzazione.storage_key),
    )


def rimuovi_contenitore_incompleto(
    autorizzazione: AutorizzazioneRecupero, *, store_root: Path | str,
) -> EsitoRecupero:
    """Remove the container, with one recoverable commit point."""
    from contract_store import ContractStoreError, catalog_admission_lock

    if not isinstance(autorizzazione, AutorizzazioneRecupero):
        raise RecuperoPubblicazioneError("autorizzazione_assente",
                                         type(autorizzazione).__name__)
    radice = Path(store_root)
    # Verified before any lock: the productive writer lock creates the contract
    # directories it needs, so locking first would let this primitive fabricate
    # a container and then remove what it had just made.
    prima = ispeziona_contenitore_incompleto(autorizzazione, store_root=radice)

    fd_radice = _apri_directory(os.fspath(radice))
    try:
        # The same root goes to both locks: the global lock and the contract
        # lock have to serialize the SAME store, and passing it to only one of
        # them left the global lock on whatever store the configuration named.
        try:
            gestore = catalog_admission_lock(store_root=radice)
        except ContractStoreError as exc:
            raise RecuperoPubblicazioneError(
                "lucchetto_non_ottenuto", getattr(exc, "code", str(exc))
            ) from None
        with gestore:
            fd_contenitore = _apri_directory(autorizzazione.storage_key,
                                             dir_fd=fd_radice)
            try:
                _verifica_forma(fd_contenitore)
                identita = _identita(fd_contenitore)
                ritirato = PREFISSO_RITIRO + autorizzazione.storage_key
                try:
                    os.lstat(ritirato, dir_fd=fd_radice)
                except FileNotFoundError:
                    pass
                else:
                    raise RecuperoPubblicazioneError(
                        "nome_di_ritiro_occupato", ritirato)
                # The single commit point: after this rename the container is
                # no longer reachable under its own name, and an interruption
                # leaves a shape the next attempt recognises instead of a
                # half-emptied container the shape check would refuse forever.
                os.rename(autorizzazione.storage_key, ritirato,
                          src_dir_fd=fd_radice, dst_dir_fd=fd_radice)
                if _identita(fd_contenitore) != identita:
                    raise RecuperoPubblicazioneError("identita_cambiata",
                                                     autorizzazione.storage_key)
            finally:
                os.close(fd_contenitore)

            fd_ritirato = _apri_directory(ritirato, dir_fd=fd_radice)
            try:
                if _identita(fd_ritirato) != identita:
                    raise RecuperoPubblicazioneError("identita_cambiata", ritirato)
                for nome in sorted(os.listdir(fd_ritirato)):
                    if nome == NOME_GENERAZIONI:
                        os.rmdir(nome, dir_fd=fd_ritirato)
                    else:
                        os.unlink(nome, dir_fd=fd_ritirato)
            finally:
                os.close(fd_ritirato)
            os.rmdir(ritirato, dir_fd=fd_radice)
            os.fsync(fd_radice)
    finally:
        os.close(fd_radice)
    return EsitoRecupero(prima.contract_id, prima.storage_key, True,
                         prima.percorso)
