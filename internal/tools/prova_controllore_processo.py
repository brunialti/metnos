"""Targeted tests for the process controller.

The five cases are the ones the shell procedure could not survive: spontaneous
exit, ordinary termination, escalation against a process that ignores SIGTERM,
a recorded identity that no longer matches, and a child that outlives its
parent.  Nothing here touches Metnos: every subject is a short-lived shell.

Run:  python3 internal/tools/prova_controllore_processo.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import controllore_processo as C  # noqa: E402

CASI = []


def caso(nome):
    def deco(fn):
        CASI.append((nome, fn))
        return fn
    return deco


@caso("uscita spontanea: nessun segnale inviato")
def _() -> list[str]:
    p = C.ProcessoControllato(["sh", "-c", "exit 7"]).avvia()
    codice = p.attendi(5)
    esito = p.chiudi(grazia=2)
    errori = []
    if codice != 7:
        errori.append(f"attendi ha reso {codice}, atteso 7")
    if not esito.uscita_spontanea:
        errori.append("l'uscita spontanea non e' stata riconosciuta")
    if esito.term_inviato or esito.kill_inviato:
        errori.append("un segnale e' stato inviato a un processo gia' uscito")
    return errori


@caso("terminazione ordinaria: TERM basta, nessuna escalation")
def _() -> list[str]:
    p = C.ProcessoControllato(["sleep", "60"]).avvia()
    esito = p.chiudi(grazia=5)
    errori = []
    if not esito.term_inviato:
        errori.append("TERM non inviato")
    if esito.kill_inviato:
        errori.append("escalation non necessaria")
    if esito.codice != -15:
        errori.append(f"codice {esito.codice}, atteso -15")
    if esito.superstiti:
        errori.append(f"superstiti: {esito.superstiti}")
    return errori


@caso("escalation: un processo che ignora TERM viene chiuso comunque")
def _() -> list[str]:
    # trap '' TERM makes SIGTERM ignored; only SIGKILL ends it.
    p = C.ProcessoControllato(
        ["sh", "-c", "trap '' TERM; while :; do sleep 0.2; done"]
    ).avvia()
    time.sleep(0.3)
    esito = p.chiudi(grazia=1.5)
    errori = []
    if not esito.term_inviato:
        errori.append("TERM non inviato")
    if not esito.kill_inviato:
        errori.append("escalation non avvenuta su un processo che ignora TERM")
    if esito.codice != -9:
        errori.append(f"codice {esito.codice}, atteso -9")
    if esito.superstiti:
        errori.append(f"superstiti: {esito.superstiti}")
    return errori


@caso("identita' non piu' coerente: il controllore RIFIUTA di segnalare")
def _() -> list[str]:
    p = C.ProcessoControllato(["sleep", "60"]).avvia()
    errori = []
    # Simulate PID reuse by corrupting the recorded start time: the controller
    # must refuse rather than signal a process it can no longer recognise.
    uid, riga, avvio, pgid = p._identita
    p._identita = (uid, riga, avvio + 1, pgid)
    try:
        p.chiudi(grazia=1)
        errori.append("ha segnalato un processo che non riconosce piu'")
    except C.ControlloreError as exc:
        if "istante di avvio" not in str(exc):
            errori.append(f"motivo inatteso: {exc}")
    finally:
        # cleanup with the true identity
        p._identita = (uid, riga, avvio, pgid)
        p.chiudi(grazia=2)
    return errori


@caso("figlio ancora vivo: viene chiuso tutto il gruppo posseduto")
def _() -> list[str]:
    # The leader spawns a grandchild that ignores TERM and then exits itself:
    # the naive procedure would signal only the leader and leave the grandchild.
    p = C.ProcessoControllato(
        ["sh", "-c", "sh -c \"trap '' TERM; while :; do sleep 0.2; done\" & sleep 60"]
    ).avvia()
    time.sleep(0.5)
    membri = C._membri_del_gruppo(p.pgid)
    errori = []
    if len(membri) < 2:
        errori.append(f"il gruppo doveva avere almeno 2 membri, ne ha {len(membri)}")
    esito = p.chiudi(grazia=1.5)
    if esito.superstiti:
        errori.append(f"il gruppo ha superstiti: {esito.superstiti}")
    residui = C._membri_del_gruppo(p.pgid)
    if residui:
        errori.append(f"processi ancora vivi nel gruppo dopo chiudi(): {residui}")
    return errori


@caso("il gruppo e' esclusivo: nessun processo fuori dal gruppo viene toccato")
def _() -> list[str]:
    # A bystander in OUR group would be a defect; a bystander outside must be
    # untouched.  The controller only ever calls killpg on its own pgid.
    estraneo = C.ProcessoControllato(["sleep", "5"]).avvia()
    soggetto = C.ProcessoControllato(["sleep", "60"]).avvia()
    errori = []
    if estraneo.pgid == soggetto.pgid:
        errori.append("i due processi condividono il gruppo: prova non valida")
    soggetto.chiudi(grazia=2)
    if not estraneo._vivo():
        errori.append("il processo estraneo e' stato ucciso")
    estraneo.chiudi(grazia=2)
    return errori


def main() -> int:
    fallimenti = 0
    for nome, fn in CASI:
        try:
            errori = fn()
        except Exception as exc:  # noqa: BLE001
            errori = [f"eccezione {type(exc).__name__}: {exc}"]
        print(f"{'ROSSO' if errori else 'verde'}  {nome}"
              + ("  -> " + "; ".join(errori) if errori else ""))
        fallimenti += bool(errori)
    print()
    print("ESITO:", "tutte verdi" if not fallimenti else f"{fallimenti} PROVE ROSSE")
    return 1 if fallimenti else 0


if __name__ == "__main__":
    sys.exit(main())
