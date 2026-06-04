#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""livetest.py — simulatore client chat HTTP per Metnos (live, porta 8770).

Esegue una batteria di query MOLTO COMPLESSE multitool/multidominio contro il
daemon live (`POST /agent/turn`, da 127.0.0.1 → ruolo `user`, niente auth) e
registra gli esiti in un DB sqlite riusabile (verifica futura + nuove query).

error=0 = la query ARRIVA IN FONDO COME ATTESO, **indipendentemente dal tempo**
(il tempo e' performance, NON un criterio del test). FAIL = final_kind error,
final vuoto, uno step con ok=False, messaggio di resa/fallback (loop_break/
recovery), o routing sbagliato. NIENTE timeout che fa fallire: si attende il
completamento.

OGGETTI DI TEST ONLY: file in /tmp/metnos_livetest, email → mykleos@knowcastle.com,
repo github proprio brunialti/metnos. Niente dati reali toccati.

DB: e2e/livetest/livetest.sqlite
Uso:
  python3 livetest.py --round R [--only-failed] [--timeout S]
  python3 livetest.py --report
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
import urllib.request
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "livetest.sqlite"
ENDPOINT = "http://127.0.0.1:8770/agent/turn"
TESTDIR = "/tmp/metnos_livetest"
TEST_EMAIL = "mykleos@knowcastle.com"
TEST_REPO = "brunialti/metnos"

# Messaggi di RESA/FALLBACK runtime = la query NON e' arrivata in fondo come
# atteso (loop_break/recovery convertiti in final_kind=answer). = FAIL.
DEGRADATION_MARKERS = [
    "mi sono bloccato",
    "ricerca interrotta",
    "non sono riuscito a produrre",
    "riformula la richiesta",
    "non sono riuscito a",
]

# Batteria MOLTO complessa (≥3 tool / multidominio). expect_re = regex su UNO
# dei tool usati (il tool-chiave del dominio, anti-misroute).
QUERIES = [
    {"ord": 1, "domains": "files,time", "kind": "pipeline",
     "text": f"Trova tutti i file .txt in {TESTDIR}, conta le righe totali e scrivi un report con la data di oggi in {TESTDIR}/report.txt",
     "expect_re": r"write_files|create_files"},
    {"ord": 2, "domains": "files,spreadsheet", "kind": "pipeline",
     "text": f"Elenca i file dentro {TESTDIR} con la loro dimensione, ordinali per dimensione decrescente e salva un foglio di calcolo in {TESTDIR}/inventario.xlsx",
     "expect_re": r"create_files_spreadsheet|write_files_spreadsheet"},
    {"ord": 3, "domains": "github", "kind": "pipeline",
     "text": f"Trova le issue su {TEST_REPO} e dimmi quante sono aperte e quante chiuse",
     "expect_re": r"find_issues_github|read_issues_github"},
    {"ord": 4, "domains": "web,extract,files", "kind": "pipeline",
     "text": f"Cerca online cos'è RISC-V, estrai 3 punti chiave e salvali in {TESTDIR}/riscv.txt",
     "expect_re": r"find_urls"},
    {"ord": 5, "domains": "time,places", "kind": "multidomain",
     "text": "Che ore sono adesso e trova le farmacie vicino a Padova ordinate per distanza",
     "expect_re": r"find_places"},
    {"ord": 6, "domains": "mail", "kind": "pipeline",
     "text": "Riassumi le ultime mail ricevute su metnos raggruppate per mittente",
     "expect_re": r"read_messages|find_messages"},
    {"ord": 7, "domains": "processes", "kind": "pipeline",
     "text": "Trova i 5 processi che usano più memoria e dimmi quanta memoria totale occupano insieme",
     "expect_re": r"get_processes"},
    {"ord": 8, "domains": "images", "kind": "pipeline",
     "text": "Trova le foto di montagna nel corpus e dimmi quante sono",
     "expect_re": r"find_images_indices"},
    {"ord": 9, "domains": "files,compress", "kind": "pipeline",
     "text": f"Comprimi tutti i file .txt di {TESTDIR} in un archivio {TESTDIR}/backup.zip e dimmi quanti file contiene",
     "expect_re": r"compress_files"},
    {"ord": 10, "domains": "web,extract,spreadsheet,mail", "kind": "compound",
     "text": f"Cerca online le prossime conferenze sull'intelligenza artificiale, estrai nome e data di ciascuna, salvale in {TESTDIR}/conferenze.xlsx e mandami il foglio via mail a {TEST_EMAIL}",
     "expect_re": r"send_messages"},
    {"ord": 11, "domains": "calendar", "kind": "read",
     "text": "Quali eventi ho in calendario nei prossimi 7 giorni?",
     "expect_re": r"read_events"},
    {"ord": 12, "domains": "files,filter", "kind": "pipeline",
     "text": f"Nei file di testo dentro {TESTDIR} trova le righe che contengono 'todo' e dimmi quante sono",
     "expect_re": r"find_files|filter|read_files"},

    # --- BATTERIA 2 (4/6/2026): query PIU' lunghe/complesse, >=3 domini, git ---
    {"ord": 13, "domains": "github,web,files", "kind": "compound",
     "text": f"Trova le issue aperte su {TEST_REPO}, poi cerca online cos'è il progetto Metnos per dare contesto, e salva un riepilogo che unisce le issue e il contesto in {TESTDIR}/issues_brief.txt",
     "expect_re": r"find_issues_github|read_issues_github"},
    {"ord": 14, "domains": "github,spreadsheet,mail", "kind": "compound",
     "text": f"Elenca le pull request di {TEST_REPO} con il loro stato, salvale in un foglio di calcolo {TESTDIR}/pulls.xlsx e poi manda il foglio via mail a {TEST_EMAIL}",
     "expect_re": r"find_pulls_github|read_pulls_github"},
    {"ord": 15, "domains": "github,github,github", "kind": "git_lifecycle",
     "text": f"Crea una issue di test dal titolo '[livetest] ciclo' su {TEST_REPO}, poi leggi le issue aperte per confermare che esista, e infine chiudila",
     "expect_re": r"create_issues_github"},
    {"ord": 16, "domains": "files,compress,github", "kind": "compound",
     "text": f"Comprimi tutti i file .txt di {TESTDIR} in un archivio {TESTDIR}/snap.zip e poi dimmi quante issue aperte ci sono su {TEST_REPO}",
     "expect_re": r"compress_files"},
    {"ord": 17, "domains": "web,extract,mail", "kind": "compound",
     "text": f"Cerca online le prossime conferenze sull'intelligenza artificiale in Europa, estrai nome, data e città di ciascuna, e manda l'elenco via mail a {TEST_EMAIL}",
     "expect_re": r"send_messages"},
    {"ord": 18, "domains": "images,files,spreadsheet", "kind": "compound",
     "text": f"Trova le foto di mare nel corpus, prendi le prime 5 con la loro data di scatto, e salva un foglio {TESTDIR}/foto_mare.xlsx con nome file e data",
     "expect_re": r"find_images_indices"},
    {"ord": 19, "domains": "mail,filter,files", "kind": "compound",
     "text": f"Riassumi le ultime mail ricevute su metnos degli ultimi 7 giorni e salva il riepilogo con la data di oggi in {TESTDIR}/mail_recenti.txt",
     "expect_re": r"read_messages|find_messages"},
    {"ord": 20, "domains": "processes,compute,files", "kind": "compound",
     "text": f"Trova i 10 processi che consumano più memoria, calcola la memoria totale che occupano insieme, e scrivi un report con la data e ora in {TESTDIR}/top_mem.txt",
     "expect_re": r"get_processes"},
    {"ord": 21, "domains": "github,spreadsheet,time", "kind": "compound",
     "text": f"Prendi le issue chiuse di {TEST_REPO}, mettile in un foglio {TESTDIR}/closed.xlsx aggiungendo una colonna con la data di oggi",
     "expect_re": r"find_issues_github|read_issues_github"},
    {"ord": 22, "domains": "places,web,files", "kind": "compound",
     "text": f"Trova gli ospedali vicino a Padova, cerca online qual è il numero di emergenza sanitaria in Italia, e salva tutto in {TESTDIR}/emergenza.txt",
     "expect_re": r"find_places"},
    {"ord": 23, "domains": "files,github,mail", "kind": "compound",
     "text": f"Conta le righe totali dei file .txt in {TESTDIR}, crea una issue '[livetest] conteggio' su {TEST_REPO} con il numero, e manda conferma via mail a {TEST_EMAIL}",
     "expect_re": r"create_issues_github"},
    {"ord": 24, "domains": "calendar,web,mail", "kind": "compound",
     "text": f"Guarda i miei eventi dei prossimi 7 giorni, cerca online che tempo farà a Padova, e manda via mail a {TEST_EMAIL} un riepilogo che combina i due",
     "expect_re": r"send_messages"},
]


def ensure_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS queries(
        id INTEGER PRIMARY KEY, ord INT, text TEXT UNIQUE, domains TEXT,
        kind TEXT, expect_re TEXT, active INT DEFAULT 1, notes TEXT DEFAULT '')""")
    conn.execute("""CREATE TABLE IF NOT EXISTS runs(
        id INTEGER PRIMARY KEY, query_id INT, round TEXT, attempt INT, ts TEXT,
        ok INT, final_kind TEXT, tools TEXT, error TEXT, excerpt TEXT, ms INT)""")
    for q in QUERIES:
        conn.execute(
            "INSERT OR IGNORE INTO queries(ord,text,domains,kind,expect_re) VALUES(?,?,?,?,?)",
            (q["ord"], q["text"], q["domains"], q["kind"], q["expect_re"]))
    conn.commit()
    return conn


def call_turn(text: str, timeout: int) -> dict:
    body = json.dumps({"query": text, "actor": "roberto",
                       "conversation_id": "livetest"}).encode("utf-8")
    req = urllib.request.Request(ENDPOINT, data=body,
                                 headers={"Content-Type": "application/json",
                                          "Accept": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    data["_ms"] = int((time.time() - t0) * 1000)
    return data


def classify(resp: dict, expect_re: str | None) -> tuple[bool, str]:
    """error=0 = arrivata in fondo COME ATTESO. Tempo IRRILEVANTE."""
    fk = resp.get("final_kind") or ""
    final = (resp.get("final_message") or "").strip()
    steps = resp.get("steps_summary") or []
    tools = [s.get("tool") for s in steps if s.get("tool")]
    if fk == "error":
        return False, f"final_kind=error: {final[:100]}"
    if fk == "ask":
        return False, f"final_kind=ask (richiede input non atteso): {final[:80]}"
    if not final:
        return False, "final_message vuoto"
    # step di pipeline falliti = non completata come atteso
    bad = [s.get("tool") for s in steps if s.get("ok") is False]
    if bad:
        return False, f"step ok=False: {bad}"
    # messaggio di resa/fallback (loop_break/recovery)
    low = final.lower()
    for mk in DEGRADATION_MARKERS:
        if mk in low:
            return False, f"fallback/resa: '{mk}'"
    # routing del dominio (anti-misroute)
    if expect_re and not any(re.search(expect_re, t or "") for t in tools):
        return False, f"misroute: atteso /{expect_re}/, usati {tools}"
    return True, "ok"


def run(args):
    conn = ensure_db()
    rows = conn.execute(
        "SELECT id,ord,text,kind,expect_re,domains FROM queries WHERE active=1 ORDER BY ord").fetchall()
    if args.only_failed:
        keep = []
        for r in rows:
            row = conn.execute(
                "SELECT ok FROM runs WHERE query_id=? ORDER BY id DESC LIMIT 1",
                (r[0],)).fetchone()
            if not (row and row[0] == 1):
                keep.append(r)
        rows = keep
    total = len(rows)
    npass = 0
    print(f"=== LIVETEST round={args.round} queries={total} (timeout={args.timeout}s, tempo NON e' criterio) ===", flush=True)
    for i, (qid, ordn, text, kind, expect_re, domains) in enumerate(rows, 1):
        try:
            resp = call_turn(text, args.timeout)
            ok, reason = classify(resp, expect_re)
        except Exception as e:
            resp, ok, reason = {}, False, f"EXC {type(e).__name__}: {e}"
        tools = ",".join(s.get("tool") for s in (resp.get("steps_summary") or []) if s.get("tool"))
        conn.execute(
            "INSERT INTO runs(query_id,round,attempt,ts,ok,final_kind,tools,error,excerpt,ms) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (qid, args.round, 1, time.strftime("%H:%M:%S"), 1 if ok else 0,
             resp.get("final_kind", ""), tools, "" if ok else reason,
             (resp.get("final_message") or "")[:300], resp.get("_ms", 0)))
        conn.commit()
        npass += 1 if ok else 0
        st = "PASS" if ok else "FAIL"
        ms = resp.get("_ms", 0)
        print(f"[{i}/{total}] q{ordn} {st} ({kind}) {ms}ms {('· ' + reason) if not ok else ''} "
              f"| tools=[{tools}]", flush=True)
    print(f"=== LIVETEST round={args.round} RESULT: {npass}/{total} pass, {total-npass} FAIL ===",
          flush=True)
    return total - npass


def report():
    conn = ensure_db()
    rows = conn.execute("SELECT id,ord,text FROM queries WHERE active=1 ORDER BY ord").fetchall()
    for qid, ordn, text in rows:
        row = conn.execute(
            "SELECT ok,tools,error,final_kind,ms FROM runs WHERE query_id=? ORDER BY id DESC LIMIT 1",
            (qid,)).fetchone()
        if not row:
            print(f"{ordn:>3} | ---  | (mai eseguita) | {text[:55]}")
            continue
        ok, tools, error, fk, ms = row
        st = "PASS" if ok == 1 else "FAIL"
        print(f"{ordn:>3} | {st} | {ms}ms fk={fk} | {tools or ''} {('· ' + error) if error else ''} | {text[:45]}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--round", default="r1")
    p.add_argument("--timeout", type=int, default=1800)  # alto: il tempo non fa fallire
    p.add_argument("--only-failed", action="store_true")
    p.add_argument("--report", action="store_true")
    args = p.parse_args()
    if args.report:
        report()
        return
    raise SystemExit(0 if run(args) == 0 else 1)


if __name__ == "__main__":
    main()
