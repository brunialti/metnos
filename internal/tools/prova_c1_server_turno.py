"""C1 - does the whole HTTP server start, and does it serve a real turn?

Every earlier round proved that `require_birth_runtime_before_workers()`
completes in an isolated process.  That is not the same claim, and the document
has said so from the first page: green suites do not say the service starts, and
the difference between "the code is correct" and "the service starts" cost ten
minutes of outage on 31 August.  This closes that gap - in copy.

Nothing here touches the installation:

* the distribution is the replica passed with `--replica`;
* the user roots are separate directories, so the server writes its state,
  its configuration and its admin key inside the scratch;
* the port is chosen free and may never be the production 8770;
* the server is started, waited for and closed with `ProcessoControllato`, so
  the whole process group goes down and a surviving child is reported instead
  of being left behind - which matters here more than anywhere else, because an
  HTTP server spawns children.

Exit codes:
    0  the server started, answered and served a turn; nothing survived
    1  the probe could not run (bad replica, no free port, missing interpreter)
    2  the server never became ready
    3  the server was ready but the turn did not succeed
    4  the run left processes behind

Usage:
    python3 internal/tools/prova_c1_server_turno.py --replica DIR --radici DIR
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from controllore_processo import ProcessoControllato  # noqa: E402

PORTA_PRODUZIONE = 8770

EXIT_OK = 0
EXIT_SELF = 1
EXIT_NON_PRONTO = 2
EXIT_TURNO = 3
EXIT_RESIDUI = 4


def porta_libera() -> int:
    """A port the kernel says is free, and never the production one."""
    for _ in range(20):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            porta = s.getsockname()[1]
        if porta != PORTA_PRODUZIONE:
            return porta
    raise RuntimeError("nessuna porta libera diversa da quella di produzione")


def curl(url: str, *, chiave: str | None = None, corpo: str | None = None,
         secondi: int = 60) -> tuple[int, str]:
    cmd = ["curl", "-s", "-o", "-", "-w", "\\n%{http_code}", "-m", str(secondi)]
    if chiave:
        cmd += ["-H", f"Authorization: Bearer {chiave}"]
    if corpo is not None:
        cmd += ["-X", "POST", "-H", "Content-Type: application/json",
                "--data", corpo]
    cmd.append(url)
    esito = subprocess.run(cmd, capture_output=True, text=True, timeout=secondi + 10)
    testo = esito.stdout.rsplit("\n", 1)
    if len(testo) != 2:
        return 0, esito.stdout
    corpo_risposta, codice = testo
    try:
        return int(codice), corpo_risposta
    except ValueError:
        return 0, esito.stdout


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--replica", required=True,
                    help="albero della distribuzione da provare (mai /opt/metnos)")
    ap.add_argument("--radici", required=True,
                    help="directory che contiene cfg/, state/, data/ isolate")
    ap.add_argument("--interprete", default="/opt/metnos/.venv/bin/python")
    ap.add_argument("--query", default="che ore sono")
    ap.add_argument("--attesa-avvio", type=float, default=180.0)
    ap.add_argument(
        "--outer-sandbox", action="store_true",
        help="the whole server already runs inside an isolated namespace",
    )
    args = ap.parse_args(argv)

    replica = Path(args.replica).resolve()
    radici = Path(args.radici).resolve()
    if replica == Path("/opt/metnos"):
        print("RIFIUTO: la replica non puo' essere l'installazione", file=sys.stderr)
        return EXIT_SELF
    for atteso in ("cfg", "state", "data"):
        if not (radici / atteso).is_dir():
            print(f"RIFIUTO: manca {radici / atteso}", file=sys.stderr)
            return EXIT_SELF
    if not Path(args.interprete).exists():
        print(f"RIFIUTO: interprete assente: {args.interprete}", file=sys.stderr)
        return EXIT_SELF

    porta = porta_libera()
    base = f"http://127.0.0.1:{porta}"
    ambiente = dict(os.environ)
    ambiente.update({
        "PYTHONPATH": str(replica),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "METNOS_INSTALL_ROOT": str(replica),
        "METNOS_USER_CONFIG": str(radici / "cfg"),
        "METNOS_USER_STATE": str(radici / "state"),
        "METNOS_USER_DATA": str(radici / "data"),
        "METNOS_WORKSPACE": str(radici / "workspace"),
        "METNOS_LOG_FILE": str(radici / "prova.log"),
        "METNOS_HTTP_HOST": "127.0.0.1",
        "METNOS_HTTP_PORT": str(porta),
        "METNOS_LANG": "it",
        "METNOS_ENGINE": "metis",
    })
    if args.outer_sandbox:
        # The enclosing namespace already isolates every server child. A
        # second user namespace is not available on every kernel and would
        # measure nesting support instead of the HTTP turn.
        ambiente["METNOS_SANDBOX"] = "0"

    print("== CHE COSA STO MISURANDO ==")
    print(f"  distribuzione : {replica}")
    print(f"  radici utente : {radici}/{{cfg,state,data}}")
    print(f"  porta         : {porta}  (produzione {PORTA_PRODUZIONE} esclusa)")
    print()

    uscita = radici / "server.out"
    with uscita.open("wb") as flusso:
        server = ProcessoControllato(
            [args.interprete, "-m", "runtime.metnos_http_server",
             "--host", "127.0.0.1", "--port", str(porta)],
            cwd=str(replica), env=ambiente, stdout=flusso,
            stderr=subprocess.STDOUT,
        ).avvia()
        print(f"server avviato: pid={server.pid} gruppo={server.pgid}")
        try:
            pronto = False
            scadenza = time.monotonic() + args.attesa_avvio
            while time.monotonic() < scadenza:
                if server.attendi(0.5) is not None:
                    print("ESITO: il server e' uscito da solo prima di rispondere")
                    break
                codice, corpo = curl(f"{base}/agent/health", secondi=5)
                if codice == 200:
                    pronto = True
                    print(f"pronto dopo {args.attesa_avvio - (scadenza - time.monotonic()):.1f}s"
                          f" -> {corpo[:120]}")
                    break
            if not pronto:
                print("ESITO: IL SERVER NON E' DIVENTATO PRONTO")
                print("--- ultime righe della sua uscita ---")
                print("\n".join(uscita.read_text(errors="ignore").splitlines()[-25:]))
                return EXIT_NON_PRONTO

            chiave_path = radici / "cfg" / "admin.key"
            chiave = chiave_path.read_text().strip() if chiave_path.exists() else None
            print(f"chiave amministrativa: {'presente' if chiave else 'ASSENTE'}")

            inizio = time.monotonic()
            codice, corpo = curl(
                f"{base}/agent/turn", chiave=chiave,
                corpo=json.dumps({"query": args.query}), secondi=300,
            )
            durata = time.monotonic() - inizio
            print(f"\n== TURNO REALE ==")
            print(f"  richiesta : {args.query!r}")
            print(f"  codice    : {codice}  in {durata:.1f}s")
            try:
                risposta = json.loads(corpo)
            except json.JSONDecodeError:
                risposta = None
            if codice != 200:
                print(f"  corpo     : {corpo[:300]}")
                print("ESITO: IL TURNO NON E' RIUSCITO (codice HTTP)")
                return EXIT_TURNO
            if risposta is None:
                print(f"  corpo     : {corpo[:300]}")
                print("ESITO: IL TURNO NON E' RIUSCITO (risposta non interpretabile)")
                return EXIT_TURNO
            # A 200 is not a successful turn.  The engine answers with
            # `final_message`, and an empty one means the turn produced
            # nothing: reporting that as green would be exactly the kind of
            # false success this document exists to avoid.
            messaggio = str(risposta.get("final_message") or "").strip()
            print(f"  esito     : {risposta.get('final_kind')}")
            print(f"  passi     : {str(risposta.get('steps_summary'))[:200]}")
            print(f"  risposta  : {messaggio[:300]}")
            if not messaggio:
                print("ESITO: IL TURNO NON HA PRODOTTO ALCUNA RISPOSTA")
                return EXIT_TURNO
            # Nor is a non-empty message a successful turn: the engine reports
            # its own failures in `final_kind`, and the first run of this probe
            # called "il catalogo degli executor e' vuoto" a green C1.
            if str(risposta.get("final_kind")) == "error":
                print("ESITO: IL MOTORE HA DICHIARATO UN ERRORE")
                return EXIT_TURNO
            steps = risposta.get("steps_summary")
            if not isinstance(steps, list) or not steps or any(
                not isinstance(step, dict) or step.get("ok") is not True
                for step in steps
            ):
                print("ESITO: ALMENO UN PASSO DEL TURNO NON E' RIUSCITO")
                return EXIT_TURNO
        finally:
            esito = server.chiudi(grazia=15)
            print(f"\nchiusura: term={esito.term_inviato} kill={esito.kill_inviato}"
                  f" codice={esito.codice} superstiti={esito.superstiti}")
            if esito.superstiti:
                print("ESITO: PROCESSI SOPRAVVISSUTI")
                return EXIT_RESIDUI

    print("\nC1 VERDE: il server e' partito, ha risposto e ha servito un turno reale.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
