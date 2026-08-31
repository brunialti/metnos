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


@caso("leader terminato DAVVERO con figlio vivo: gruppo chiuso e stato finale vero")
def _() -> list[str]:
    # Il leader genera un figlio che ignora TERM e poi esce lui stesso: e' il
    # caso che la prova precedente diceva di coprire e non copriva, perche' il
    # leader restava vivo con `sleep 60`.
    p = C.ProcessoControllato(
        ["sh", "-c", "sh -c \"trap '' TERM; while :; do sleep 0.2; done\" & exit 0"]
    ).avvia()
    time.sleep(0.6)
    errori = []
    if p._vivo():
        errori.append("il leader doveva essere gia' terminato")
    prima = C._membri_del_gruppo(p.pgid, escludi=p.pid)
    if not prima:
        errori.append("il figlio doveva essere vivo prima della chiusura")
    esito = p.chiudi(grazia=1.5)
    if not esito.uscita_spontanea:
        errori.append("l'uscita spontanea del leader non e' stata riconosciuta")
    dopo = C._membri_del_gruppo(p.pgid, escludi=p.pid)
    if dopo:
        errori.append(f"il gruppo ha ancora membri vivi: {dopo}")
    if esito.superstiti != dopo:
        errori.append(
            f"Esito.superstiti={esito.superstiti} non descrive lo stato finale {dopo}"
        )
    return errori


@caso("attendi() poi chiudi(): il numero resta riservato fra le due chiamate")
def _() -> list[str]:
    p = C.ProcessoControllato(["sh", "-c", "exit 5"]).avvia()
    pid = p.pid
    codice = p.attendi(5)
    errori = []
    if codice != 5:
        errori.append(f"attendi ha reso {codice}, atteso 5")
    # La proprieta' dichiarata: il figlio non e' raccolto, quindi il PID e'
    # ancora riservato e la maniglia e' ancora aperta.
    if p._pidfd is None:
        errori.append("attendi() ha chiuso il pidfd: ha raccolto in anticipo")
    if not os.path.exists(f"/proc/{pid}"):
        errori.append("attendi() ha raccolto il figlio: il PID e' stato liberato")
    esito = p.chiudi(grazia=1)
    if esito.codice != 5:
        errori.append(f"chiudi ha reso {esito.codice}, atteso 5")
    if esito.term_inviato or esito.kill_inviato:
        errori.append("segnali inviati a un processo gia' uscito")
    return errori


@caso("errore di chiusura: esce dal blocco with invece di essere inghiottito")
def _() -> list[str]:
    errori = []
    visto = False
    try:
        with C.ProcessoControllato(["sleep", "30"]) as p:
            # identita' corrotta: chiudi() deve rifiutare, e il rifiuto deve
            # attraversare __exit__ invece di sparire.
            uid, riga, avvio, pgid = p._identita
            p._identita = (uid, riga, avvio + 1, pgid)
            vero = (uid, riga, avvio, pgid)
    except C.ControlloreError:
        visto = True
    if not visto:
        errori.append("__exit__ ha inghiottito l'errore di pulizia")
    # ripulisci con l'identita' vera
    try:
        p._identita = vero
        p.chiudi(grazia=2)
    except Exception:
        pass
    return errori


@caso("errore nel corpo del with: l'errore di pulizia non lo sostituisce")
def _() -> list[str]:
    errori = []
    catturata = None
    try:
        with C.ProcessoControllato(["sleep", "30"]) as p:
            uid, riga, avvio, pgid = p._identita
            vero = (uid, riga, avvio, pgid)
            p._identita = (uid, riga, avvio + 1, pgid)
            raise ValueError("errore del corpo")
    except BaseException as exc:
        catturata = exc
    if not isinstance(catturata, C.ControlloreError):
        errori.append(f"attesa ControlloreError, arrivata {type(catturata).__name__}")
    elif not isinstance(catturata.__cause__, ValueError):
        errori.append("l'errore originale del corpo e' andato perso")
    try:
        p._identita = vero
        p.chiudi(grazia=2)
    except Exception:
        pass
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
    # A fine suite: nessun processo di prova deve essere sopravvissuto.
    residui = [
        int(v) for v in os.listdir("/proc")
        if v.isdigit() and _e_di_prova(int(v))
    ]
    if residui:
        print(f"ROSSO  nessun residuo a fine suite  -> processi rimasti: {residui}")
        fallimenti += 1
    else:
        print("verde  nessun residuo a fine suite")

    print()
    print("ESITO:", "tutte verdi" if not fallimenti else f"{fallimenti} PROVE ROSSE")
    return 1 if fallimenti else 0


def _e_di_prova(pid: int) -> bool:
    """A leftover of this suite, recognised by its own command line."""
    try:
        riga = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(
            "utf-8", "ignore"
        )
    except OSError:
        return False
    return "trap '' TERM; while :; do sleep 0.2; done" in riga


if __name__ == "__main__":
    sys.exit(main())
