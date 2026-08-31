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
        if self._pidfd is None:
            return False
        try:
            info = os.waitid(
                os.P_PIDFD, self._pidfd,
                os.WEXITED | os.WNOHANG | os.WNOWAIT,
            )
        except ChildProcessError:
            return False
        return info is None

    def attendi(self, secondi: float) -> int | None:
        """Wait for spontaneous exit; return the code, or None if still alive."""
        scadenza = time.monotonic() + secondi
        while time.monotonic() < scadenza:
            if not self._vivo():
                return self._raccogli()
            time.sleep(0.05)
        return None

    def _raccogli(self) -> int | None:
        if self._proc is None:
            return None
        codice = self._proc.wait()
        if self._pidfd is not None:
            os.close(self._pidfd)
            self._pidfd = None
        return codice

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
            esito.uscita_spontanea = True
            esito.codice = self._raccogli()
            esito.superstiti = _membri_del_gruppo(self.pgid, escludi=self.pid)
            if esito.superstiti:
                esito.note.append(
                    "il leader era gia' uscito ma il gruppo aveva superstiti"
                )
                os.killpg(self.pgid, signal.SIGKILL)
                esito.kill_inviato = True
            return esito

        self._verifica_identita()
        os.killpg(self.pgid, signal.SIGTERM)
        esito.term_inviato = True

        scadenza = time.monotonic() + grazia
        while time.monotonic() < scadenza:
            if not self._vivo() and not _membri_del_gruppo(self.pgid,
                                                           escludi=self.pid):
                break
            time.sleep(0.05)

        # The leader is still unreaped here, so the group id is still ours.
        superstiti = _membri_del_gruppo(self.pgid, escludi=self.pid)
        if self._vivo() or superstiti:
            self._verifica_identita()
            os.killpg(self.pgid, signal.SIGKILL)
            esito.kill_inviato = True
            esito.note.append("terminazione ordinaria non sufficiente")
            scadenza = time.monotonic() + 5
            while (time.monotonic() < scadenza
                   and _membri_del_gruppo(self.pgid, escludi=self.pid)):
                time.sleep(0.05)

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

    def __exit__(self, *_exc) -> None:
        try:
            self.chiudi()
        except ControlloreError:
            pass
