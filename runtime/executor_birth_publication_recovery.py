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

import ctypes
import errno
import json
import os
import stat
import sys
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
    """Proof that the authoring inventory claims this identity, on THIS store.

    The seal lives inside a closure that is never bound to a module attribute,
    so an importer cannot reach it and mint one: an authorization anyone can
    build is not an authorization.  It also carries the identity of the store
    root it was issued against, because the same key selects the same container
    in any root a caller might pass - binding the root is what makes "the
    caller does not name the target" true rather than merely stated.
    """

    contract_id: object
    storage_key: str
    radice_identita: tuple[int, int]
    _sigillo: object

    def __post_init__(self) -> None:
        if not _sigillo_valido(self._sigillo):
            raise RecuperoPubblicazioneError("autorizzazione_non_emessa")


@dataclass(frozen=True, slots=True)
class EsitoRecupero:
    """What the primitive observed and what it removed - never a hope."""

    contract_id: str
    storage_key: str
    rimosso: bool
    percorso: str


def _fabbrica_autorita():
    """The issuer closes over the seal, and nothing else can reach it.

    Exporting a mint - or a second door for fixtures - means an importer can
    build an authorization without passing the inventory, which is the whole
    property.  Only the validator leaves the closure; tests substitute the
    inventory and cross the same productive door.
    """
    segreto = object()

    def autorizza_dall_inventario(contract_id_value: str, *, store_root):
        """Issue an authorization only for a contract authoring declares.

        The store root is observed here and its identity travels in the
        authorization: the entry points refuse a root that is not the one the
        authorization was issued against.
        """
        from manifest_inventory import inventory_authoring_manifests

        inventario = inventory_authoring_manifests()
        if inventario.problems:
            raise RecuperoPubblicazioneError(
                "inventario_autoriale_con_problemi", str(len(inventario.problems))
            )
        for ref in inventario.manifests:
            if ref.contract_id.value == contract_id_value:
                return AutorizzazioneRecupero(
                    ref.contract_id, ref.contract_id.storage_key,
                    _identita_radice(store_root), segreto,
                )
        raise RecuperoPubblicazioneError(
            "contratto_non_inventariato", contract_id_value
        )

    def valido(candidato) -> bool:
        return candidato is segreto

    return autorizza_dall_inventario, valido


autorizza_dall_inventario, _sigillo_valido = _fabbrica_autorita()


def _identita_radice(store_root) -> tuple[int, int]:
    fd = _apri_directory(os.fspath(store_root))
    try:
        return _identita(fd)
    finally:
        os.close(fd)


RENAME_NOREPLACE = 1


def _rinomina_senza_sostituzione(
    origine: str, destinazione: str, *, dir_fd: int,
) -> None:
    """Rename relative to one parent descriptor, refusing to replace.

    ``os.rename`` replaces on POSIX, so checking that the destination is free
    and then renaming is a race: a directory created between the two steps is
    silently overwritten.  ``renameat2`` with ``RENAME_NOREPLACE`` makes the
    refusal atomic, and a collision leaves both objects untouched.
    """
    if not sys.platform.startswith("linux"):
        raise RecuperoPubblicazioneError("piattaforma_non_supportata", sys.platform)
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise RecuperoPubblicazioneError("rinomina_atomica_non_disponibile",
                                         "renameat2")
    renameat2.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                          ctypes.c_char_p, ctypes.c_uint)
    renameat2.restype = ctypes.c_int
    esito = renameat2(dir_fd, os.fsencode(origine), dir_fd,
                      os.fsencode(destinazione), RENAME_NOREPLACE)
    if esito != 0:
        numero = ctypes.get_errno()
        if numero == errno.EEXIST:
            raise RecuperoPubblicazioneError("nome_di_ritiro_occupato",
                                             destinazione)
        raise RecuperoPubblicazioneError("rinomina_fallita",
                                         os.strerror(numero))


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
        if _identita(fd_radice) != autorizzazione.radice_identita:
            raise RecuperoPubblicazioneError("radice_non_autorizzata",
                                             str(store_root))
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


RICEVUTE = "recovery-receipts-v1"


def _percorso_ricevuta(store_root, storage_key: str,
                       radice_identita: tuple[int, int]) -> Path:
    """Receipts live OUTSIDE the store root, and are named per store.

    Outside, because an immediate object of the store root that the inventory
    does not own is exactly what the census blocks on: a receipt written inside
    would make this primitive create the anomaly it exists to remove.

    Per store, because a name built from the storage key alone is shared by
    every store that holds that contract - two of them would read each other's
    provenance, which is how a retired container with no receipt of its own
    still looked resumable.
    """
    dispositivo, inode = radice_identita
    return (Path(store_root).parent / RICEVUTE
            / f"{dispositivo:x}-{inode:x}" / f"{storage_key}.json")


def _scrivi_ricevuta(percorso: Path, documento: dict) -> None:
    percorso.parent.mkdir(parents=True, exist_ok=True)
    temporaneo = percorso.with_suffix(".json.parziale")
    byte = json.dumps(documento, sort_keys=True,
                      separators=(",", ":")).encode("ascii")
    descrittore = os.open(temporaneo, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(descrittore, byte)
        os.fsync(descrittore)
    finally:
        os.close(descrittore)
    os.replace(temporaneo, percorso)
    fd_cartella = os.open(percorso.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd_cartella)
    finally:
        os.close(fd_cartella)


def _leggi_ricevuta(percorso: Path) -> dict | None:
    try:
        return json.loads(percorso.read_bytes())
    except (OSError, json.JSONDecodeError):
        return None


def _pulisci_e_rimuovi(fd_radice: int, ritirato: str, identita: tuple[int, int],
                       autorizzazione: AutorizzazioneRecupero) -> None:
    """Empty and remove the retired container, naming only what is expected.

    Iterating and deleting everything that is not ``generations`` removed a file
    that appeared after the shape was verified, and reported success.  The
    container is re-verified through the same descriptor, and only the two
    admitted names are touched: anything else blocks and stays intact.
    """
    fd_ritirato = _apri_directory(ritirato, dir_fd=fd_radice)
    try:
        if _identita(fd_ritirato) != identita:
            raise RecuperoPubblicazioneError("identita_cambiata", ritirato)
        # After the commit point the shape check no longer applies: cleanup is
        # monotone, so the reachable states are the full shape, the shape
        # without the lock, and the empty container.  Demanding `generations`
        # here made an interruption between the two removals unrecoverable -
        # the opposite of the property this design claims.
        voci = set(os.listdir(fd_ritirato))
        if voci not in ({NOME_GENERAZIONI, NOME_LUCCHETTO},
                        {NOME_GENERAZIONI}, set()):
            raise RecuperoPubblicazioneError(
                "stato_di_pulizia_non_raggiungibile", ",".join(sorted(voci)))
        if NOME_LUCCHETTO in voci:
            os.unlink(NOME_LUCCHETTO, dir_fd=fd_ritirato)
        if NOME_GENERAZIONI in voci:
            fd_generazioni = _apri_directory(NOME_GENERAZIONI,
                                             dir_fd=fd_ritirato)
            try:
                if os.listdir(fd_generazioni):
                    raise RecuperoPubblicazioneError("generazioni_non_vuote",
                                                     NOME_GENERAZIONI)
            finally:
                os.close(fd_generazioni)
            os.rmdir(NOME_GENERAZIONI, dir_fd=fd_ritirato)
    finally:
        os.close(fd_ritirato)
    os.rmdir(ritirato, dir_fd=fd_radice)
    os.fsync(fd_radice)


def rimuovi_contenitore_incompleto(
    autorizzazione: AutorizzazioneRecupero, *, store_root: Path | str,
) -> EsitoRecupero:
    """Remove the container through one durable, resumable commit point.

    The states this has to survive are enumerated, not hoped for: original
    only, retired only, both, neither, and a collision on the retired name.
    """
    from contract_store import ContractStoreError, catalog_admission_lock

    if not isinstance(autorizzazione, AutorizzazioneRecupero):
        raise RecuperoPubblicazioneError("autorizzazione_assente",
                                         type(autorizzazione).__name__)
    radice = Path(store_root)
    ritirato = PREFISSO_RITIRO + autorizzazione.storage_key
    ricevuta = _percorso_ricevuta(radice, autorizzazione.storage_key,
                                  autorizzazione.radice_identita)
    def documento(stato: str, contenitore: tuple[int, int]) -> dict:
        # The container's own identity is durable BEFORE the rename: without it
        # a receipt prepared for the original authorizes removing whatever
        # different container happens to occupy the retired name.
        return {
            "schema_version": 2,
            "stato": stato,
            "contract_id": autorizzazione.contract_id.value,
            "storage_key": autorizzazione.storage_key,
            "radice": list(autorizzazione.radice_identita),
            "contenitore": list(contenitore),
        }

    def concorda(registrata, stato: str, contenitore=None) -> bool:
        if not isinstance(registrata, dict) or registrata.get("stato") != stato:
            return False
        base = {k: registrata.get(k) for k in
                ("contract_id", "storage_key", "radice")}
        se_uguale = (base == {"contract_id": autorizzazione.contract_id.value,
                              "storage_key": autorizzazione.storage_key,
                              "radice": list(autorizzazione.radice_identita)})
        if contenitore is None:
            return se_uguale
        return se_uguale and registrata.get("contenitore") == list(contenitore)

    fd_radice = _apri_directory(os.fspath(radice))
    try:
        if _identita(fd_radice) != autorizzazione.radice_identita:
            raise RecuperoPubblicazioneError("radice_non_autorizzata", str(radice))
        try:
            gestore = catalog_admission_lock(store_root=radice)
        except ContractStoreError as exc:
            raise RecuperoPubblicazioneError(
                "lucchetto_non_ottenuto", getattr(exc, "code", str(exc))
            ) from None
        with gestore:
            presente = _esiste(autorizzazione.storage_key, fd_radice)
            in_ritiro = _esiste(ritirato, fd_radice)
            registrata = _leggi_ricevuta(ricevuta)

            # neither, and a receipt: the work is already done, and saying so
            # is not the same as saying "it never existed".
            if not presente and not in_ritiro:
                # Idempotent only from `committed`.  A `prepared` receipt with
                # both names gone means a rename that FAILED, and calling that
                # success is how a failure becomes a completed recovery.
                if concorda(registrata, "committed"):
                    # The previous attempt may have removed the retired name
                    # and stopped before syncing the root: returning success
                    # here would inherit a removal that is not yet durable.
                    # A failing sync fails the round, which stays repeatable.
                    os.fsync(fd_radice)
                    return EsitoRecupero(
                        autorizzazione.contract_id.value,
                        autorizzazione.storage_key, True,
                        str(radice / autorizzazione.storage_key),
                    )
                raise RecuperoPubblicazioneError(
                    "contenitore_assente", autorizzazione.storage_key)

            # both: an interruption between the receipt and the rename, or a
            # collision.  Either way the original is not ours to touch twice.
            if presente and in_ritiro:
                raise RecuperoPubblicazioneError("nome_di_ritiro_occupato",
                                                 ritirato)

            if presente:
                fd_contenitore = _apri_directory(autorizzazione.storage_key,
                                                 dir_fd=fd_radice)
                try:
                    _verifica_forma(fd_contenitore)
                    identita = _identita(fd_contenitore)
                finally:
                    os.close(fd_contenitore)
                # The receipt is durable BEFORE the commit point, so an
                # interruption immediately after the rename still leaves the
                # provenance that lets the next attempt resume.
                _scrivi_ricevuta(ricevuta, documento("prepared", identita))
                _rinomina_senza_sostituzione(
                    autorizzazione.storage_key, ritirato, dir_fd=fd_radice)
                os.fsync(fd_radice)
                _scrivi_ricevuta(ricevuta, documento("committed", identita))
            else:
                # retired only: resume, but only with complete provenance.  A
                # deterministic name proves nothing by itself.
                fd_ritirato = _apri_directory(ritirato, dir_fd=fd_radice)
                try:
                    identita = _identita(fd_ritirato)
                finally:
                    os.close(fd_ritirato)
                # The retired container is resumed only when the receipt claims
                # THIS inode: a deterministic name proves nothing, and a
                # receipt without the identity would authorize removing
                # whatever occupied that name.
                if not (concorda(registrata, "prepared", identita)
                        or concorda(registrata, "committed", identita)):
                    raise RecuperoPubblicazioneError(
                        "ritirato_senza_provenienza", ritirato)
                if concorda(registrata, "prepared", identita):
                    _scrivi_ricevuta(ricevuta, documento("committed", identita))

            _pulisci_e_rimuovi(fd_radice, ritirato, identita, autorizzazione)
    finally:
        os.close(fd_radice)
    return EsitoRecupero(
        autorizzazione.contract_id.value, autorizzazione.storage_key, True,
        str(radice / autorizzazione.storage_key),
    )


def _esiste(nome: str, fd_radice: int) -> bool:
    try:
        os.lstat(nome, dir_fd=fd_radice)
    except FileNotFoundError:
        return False
    return True
