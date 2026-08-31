"""Start, wait for and stop a process we own, without ever signalling a stranger.

The shell procedure this replaces claimed to have eliminated PID reuse and had
not: it recorded the command line and the UID, re-checked only the command line
before the final signal, never recorded the start time, and signalled the leader
alone even though it had created a whole process group.  Every one of those is a
way to kill somebody else's process, and the procedure was meant for an agent
that would follow it literally.

Two properties make this one safe, and they are properties, not intentions:

**The identifier cannot be reused.**  The child is never reaped until the stop
sequence is over.  An unreaped process keeps its PID, and because the child is
also its own group leader (``start_new_session``), the group id is that same PID
and is therefore reserved too.  Signalling the group cannot reach a stranger,
because the number cannot have been handed to one.

**Liveness is read from a stable handle.**  ``pidfd`` refers to the process
itself, not to a number, so ``waitid(P_PIDFD, ..., WNOWAIT)`` answers "is it
still running" without reaping it and without a race.

Residual window, stated rather than hidden: between reading the group's members
from ``/proc`` and signalling the group, a member other than the leader may exit
and its PID be reused *by a process the leader itself spawned into the group* —
signalling the group still reaches only members of our own group, so this is a
window in the report, not in the target.  Nothing outside the group is ever
signalled.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path


class ControlloreError(RuntimeError):
    """The controller refuses to act because identity is not confirmed."""


def _identita(pid: int) -> tuple[int, str, int, int] | None:
    """(uid, command line, start time, group) from /proc, or None if gone.

    The start time is what makes the tuple unforgeable by a later process: two
    processes can share a PID over time, never a PID and a boot-relative start.
    """
    try:
        stat_path = Path(f"/proc/{pid}/stat")
        uid = os.stat(f"/proc/{pid}").st_uid
        raw = stat_path.read_text()
        # comm may contain spaces and parentheses: everything after the last
        # ')' is positional and safe to split.
        coda = raw[raw.rindex(")") + 1:].split()
        pgrp = int(coda[2])          # field 5 overall
        starttime = int(coda[19])    # field 22 overall
        riga = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(
            "utf-8", "ignore"
        ).strip()
        return uid, riga, starttime, pgrp
    except (OSError, ValueError, IndexError):
        return None


def _e_zombi(pid: int) -> bool:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
        return raw[raw.rindex(")") + 1:].split()[0] == "Z"
    except (OSError, ValueError, IndexError):
        return False


def _membri_del_gruppo(pgid: int, *, escludi: int | None = None) -> list[int]:
    """Live processes in our group.

    A zombie is deliberately not a member here: the leader we keep unreaped is
    the very mechanism that reserves the group id, so counting it as a survivor
    would make every ordinary termination look like a failure and escalate to
    SIGKILL for nothing.
    """
    membri = []
    for voce in os.listdir("/proc"):
        if not voce.isdigit():
            continue
        pid = int(voce)
        if pid == escludi or _e_zombi(pid):
            continue
        ident = _identita(pid)
        if ident is not None and ident[3] == pgid:
            membri.append(pid)
    return sorted(membri)


@dataclass
class Esito:
    """What the stop sequence actually did, never what it hoped to do."""

    uscita_spontanea: bool = False
    codice: int | None = None
    term_inviato: bool = False
    kill_inviato: bool = False
    superstiti: list[int] = field(default_factory=list)
    note: list[str] = field(default_factory=list)


class ProcessoControllato:
    """One process, its group, and the only two gestures we need."""

    def __init__(self, argv: list[str], **kwargs) -> None:
        self._argv = list(argv)
        self._kwargs = kwargs
        self._proc: subprocess.Popen | None = None
        self._pidfd: int | None = None
        self._identita: tuple[int, str, int, int] | None = None
        self._codice_raccolto: int | None = None
        self.pid: int | None = None
        self.pgid: int | None = None

    def avvia(self) -> "ProcessoControllato":
        # start_new_session makes the child its own group and session leader,
        # so pgid == pid and the group is ours alone.
        self._proc = subprocess.Popen(
            self._argv, start_new_session=True, **self._kwargs
        )
        self.pid = self._proc.pid
        try:
            self._pidfd = os.pidfd_open(self.pid)
        except (OSError, AttributeError) as exc:
            self.chiudi_forzato_senza_handle()
            raise ControlloreError(f"pidfd non disponibile: {exc}") from None
        self._identita = _identita(self.pid)
        if self._identita is None:
            # The child may have exited instantly; that is legal and is not a
            # reason to signal anything.
            self._identita = (os.getuid(), " ".join(self._argv), -1, self.pid)
        self.pgid = self.pid
        return self

    def chiudi_forzato_senza_handle(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.kill()
            self._proc.wait()

    def _vivo(self) -> bool:
        """Ask the handle, not the number, and do not reap."""
        return self._pidfd is not None and self._stato_uscita() is None

    def attendi(self, secondi: float) -> int | None:
        """Observe the exit WITHOUT reaping; return the code, or None.

        Reaping here would destroy the one property this class is built on: an
        unreaped child keeps its PID, and with it the group id.  The first
        version called ``_raccogli()`` from this method, so any caller that did
        ``attendi()`` and then ``chiudi()`` was signalling a group id that had
        already been released - exactly the reuse the docstring claimed to have
        made impossible.  ``WNOWAIT`` reads the status and leaves the child in
        place; only ``chiudi()`` reaps.
        """
        scadenza = time.monotonic() + secondi
        while time.monotonic() < scadenza:
            stato = self._stato_uscita()
            if stato is not None:
                return stato
            time.sleep(0.05)
        return None

    def _stato_uscita(self) -> int | None:
        """The exit status of an exited-but-unreaped child, or None if alive."""
        if self._pidfd is None:
            return self._codice_raccolto
        try:
            info = os.waitid(
                os.P_PIDFD, self._pidfd,
                os.WEXITED | os.WNOHANG | os.WNOWAIT,
            )
        except ChildProcessError:
            return self._codice_raccolto
        if info is None:
            return None
        if info.si_code == os.CLD_EXITED:
            return info.si_status
        return -info.si_status

    def _raccogli(self) -> int | None:
        """The single place that reaps and releases the handle.

        It runs only after the stop sequence has finished with the group, so
        the group id is ours for the whole time we are signalling it.
        """
        if self._proc is None:
            return None
        codice = self._proc.wait()
        self._codice_raccolto = codice
        if self._pidfd is not None:
            os.close(self._pidfd)
            self._pidfd = None
        return codice

    def _attendi_gruppo_vuoto(self, secondi: float) -> list[int]:
        """Wait, with a limit, until nothing but our unreaped leader is left."""
        scadenza = time.monotonic() + secondi
        while time.monotonic() < scadenza:
            superstiti = _membri_del_gruppo(self.pgid, escludi=self.pid)
            if not superstiti and self._stato_uscita() is not None:
                return []
            time.sleep(0.05)
        return _membri_del_gruppo(self.pgid, escludi=self.pid)

    def _verifica_identita(self) -> None:
        """Refuse to signal if the recorded identity no longer matches."""
        if self._identita is None or self.pid is None:
            raise ControlloreError("nessuna identita' registrata")
        attuale = _identita(self.pid)
        if attuale is None:
            return  # gone: nothing to signal, and nothing to get wrong
        if attuale[0] != self._identita[0] or attuale[2] != self._identita[2]:
            raise ControlloreError(
                "il PID non e' piu' lo stesso processo (uid o istante di avvio "
                "cambiati): non invio alcun segnale"
            )

    def chiudi(self, grazia: float = 10.0) -> Esito:
        """Ordinary termination of the whole group, escalation only if needed."""
        esito = Esito()
        if self._proc is None or self.pgid is None:
            raise ControlloreError("processo mai avviato")

        if not self._vivo():
            # The leader has exited but is not reaped, so the group id is still
            # reserved and its remaining members are still reachable.
            esito.uscita_spontanea = True
            rimasti = _membri_del_gruppo(self.pgid, escludi=self.pid)
            if rimasti:
                esito.note.append(
                    "il leader era gia' uscito ma il gruppo aveva superstiti"
                )
                os.killpg(self.pgid, signal.SIGTERM)
                esito.term_inviato = True
                rimasti = self._attendi_gruppo_vuoto(grazia)
                if rimasti:
                    os.killpg(self.pgid, signal.SIGKILL)
                    esito.kill_inviato = True
                    rimasti = self._attendi_gruppo_vuoto(5)
            esito.codice = self._raccogli()
            # Recensus AFTER the wait: the first version returned the list it
            # had read before signalling, so Esito described the state the
            # sequence started from, not the one it produced.
            esito.superstiti = _membri_del_gruppo(self.pgid, escludi=self.pid)
            if esito.superstiti:
                esito.note.append(
                    f"ATTENZIONE: {len(esito.superstiti)} processi del gruppo "
                    "sono sopravvissuti anche a SIGKILL"
                )
            return esito

        self._verifica_identita()
        os.killpg(self.pgid, signal.SIGTERM)
        esito.term_inviato = True

        # The leader is still unreaped throughout, so the group id is ours.
        superstiti = self._attendi_gruppo_vuoto(grazia)
        if self._vivo() or superstiti:
            self._verifica_identita()
            os.killpg(self.pgid, signal.SIGKILL)
            esito.kill_inviato = True
            esito.note.append("terminazione ordinaria non sufficiente")
            self._attendi_gruppo_vuoto(5)

        esito.codice = self._raccogli()
        esito.superstiti = _membri_del_gruppo(self.pgid, escludi=self.pid)
        if esito.superstiti:
            esito.note.append(
                f"ATTENZIONE: {len(esito.superstiti)} processi del gruppo "
                "sono sopravvissuti anche a SIGKILL"
            )
        return esito

    def __enter__(self) -> "ProcessoControllato":
        return self.avvia()

    def __exit__(self, exc_type, exc, traceback) -> bool:
        """Close, and let a cleanup failure reach the caller.

        Swallowing ``ControlloreError`` here meant that a refused close - the
        very case where a process may still be alive - left the block silently,
        and the agent following the procedure never learned it.  If the body
        already raised, the cleanup failure is attached to it rather than
        replacing it.
        """
        try:
            self.chiudi()
        except ControlloreError as pulizia:
            if exc is None:
                raise
            raise pulizia from exc
        return False
