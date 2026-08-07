#!/usr/bin/env python3
"""Banco di prova end-to-end per la navigazione a obiettivo del dominio sites.

A che serve (7/8/2026). Provare a mano il flusso «accedi e mostrami X» costa
cinque comandi: turno, lettura del dialogo, approvazione, rilettura del turno,
lettura del registro. Con un ciclo di correzioni davanti, quel costo decide
quante iterazioni si fanno — quindi lo si toglie.

Che cosa fa: manda UNA richiesta, approva automaticamente i dialoghi di
consenso che arrivano (l'autorizzazione e' del proprietario, esplicita, per
questo lavoro), e stampa in modo compatto: passi eseguiti, esito, testo finale
e le ultime righe del registro d'audit del dominio.

    python3 internal/tools/e2e_sites_goal.py "accedi a booking.com e mostrami le mie prenotazioni"

Uscita 0 se il turno chiude con una risposta, 1 se chiude in errore: cosi' si
puo' mettere in un ciclo `until`.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

BASE = "http://127.0.0.1:8770"
CHIAVE = Path.home() / ".config" / "metnos" / "admin.key"
AUDIT = Path.home() / ".local/state/metnos/sites_audit.jsonl"
MAX_APPROVAZIONI = 4


def _curl(url: str, dati: str | None = None, form: bool = False) -> str:
    cmd = ["curl", "-s", "-m", "600", "-H",
           f"Authorization: Bearer {CHIAVE.read_text().strip()}"]
    if dati is not None:
        cmd += ["-X", "POST", "-H",
                ("Content-Type: application/x-www-form-urlencoded" if form
                 else "Content-Type: application/json"), "--data", dati]
    cmd.append(url)
    return subprocess.run(cmd, capture_output=True, text=True).stdout


def _turno(query: str) -> dict:
    return json.loads(_curl(f"{BASE}/agent/turn",
                            json.dumps({"query": query})) or "{}")


def _approva(marker: str) -> str:
    """Il marker della chat porta il percorso del form; il form porta l'azione."""
    percorso = marker.split("INLINE_FORM:", 1)[1].strip()
    modulo = _curl(f"{BASE}{percorso}")
    azione = re.search(r'action="([^"]+)"', modulo)
    if not azione:
        return ""
    return _curl(f"{BASE}{azione.group(1)}", "decision=approve", form=True)


def _audit(n: int = 8) -> list[str]:
    if not AUDIT.is_file():
        return []
    righe = [json.loads(r) for r in AUDIT.read_text().splitlines() if r.strip()]
    fuori = []
    for d in righe[-n:]:
        etichetta = (d.get("resolved_name") or d.get("phase")
                     or d.get("added_host") or d.get("reason") or "")
        fuori.append(f"  {d.get('ts','')} {d.get('event','')} | "
                     f"{str(etichetta)[:44]} | {str(d.get('url_after') or '')[:56]}")
    return fuori


def main() -> int:
    query = " ".join(sys.argv[1:]) or "accedi a booking.com e mostrami le mie prenotazioni"
    inizio = time.time()
    esito = _turno(query)
    testo = esito.get("final_message") or ""
    passi = [(s.get("tool"), s.get("ok"), s.get("error_class"))
             for s in esito.get("steps_summary") or []]

    approvazioni = 0
    while "INLINE_FORM:" in testo and approvazioni < MAX_APPROVAZIONI:
        approvazioni += 1
        print(f"[gate {approvazioni}] {testo.split('INLINE_FORM:')[0].strip()[:120]}")
        risposta = _approva(testo)
        blocco = re.search(r"<pre>(.*?)</pre>", risposta, re.S)
        testo = (blocco.group(1) if blocco else "").strip()

    print(f"query   : {query}")
    print(f"passi   : {passi}")
    print(f"gate    : {approvazioni}")
    print(f"secondi : {int(time.time() - inizio)}")
    print("audit   :")
    print("\n".join(_audit()))
    print("risposta:")
    print(testo[:1800] if testo else "(vuota)")
    fallito = (esito.get("final_kind") == "error"
               or any(ok is False for _t, ok, _e in passi))
    return 1 if fallito else 0


if __name__ == "__main__":
    raise SystemExit(main())
