"""Mutation bench, third revision.

Three defects found in its own history, each of which made it lie:
1. a mutation that broke the import read as "no test went red";
2. a mutation with an empty block was invalid, not undetected;
3. stale bytecode made one mutation's run report a previous one's result.

So it now: forbids bytecode, clears the cache between runs, requires the exact
expected number of cases to have executed, and prints the raw red set.
"""
import os, shutil, subprocess, sys
from pathlib import Path

PY = "/opt/metnos/.venv/bin/python"
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")


def esegui(mutazioni):
    print(f"{'mutazione':46} {'attese':>10}  esito")
    tutte = True
    for nome, file, pristino, vecchio, nuovo, prova, attesi, attese in mutazioni:
        F, P = Path(file), Path(pristino)
        testo = P.read_text()
        if testo.count(vecchio) != 1:
            print(f"{nome:46} {'':>10}  ANCORA NON UNICA ({testo.count(vecchio)})")
            tutte = False
            continue
        F.write_text(testo.replace(vecchio, nuovo))
        shutil.rmtree("runtime/__pycache__", ignore_errors=True)
        r = subprocess.run([PY, prova], capture_output=True, text=True, env=ENV)
        righe = [x for x in r.stdout.splitlines() if x.strip().startswith(("ok", "ROSSO"))]
        shutil.copy(P, F)
        if len(righe) != attesi:
            print(f"{nome:46} {'':>10}  INVALIDA: {len(righe)}/{attesi} casi eseguiti")
            tutte = False
            continue
        rosse = {x.split()[1] for x in righe if x.strip().startswith("ROSSO")}
        ok = rosse >= set(attese)
        tutte &= ok
        stato = ("RILEVATA da " + ",".join(sorted(rosse))) if ok else (
            "NON RILEVATA (" + (",".join(sorted(rosse)) or "nessuna") + ")")
        print(f"{nome:46} {','.join(sorted(attese)):>10}  {stato}")
    shutil.rmtree("runtime/__pycache__", ignore_errors=True)
    print("\n" + ("tutte rilevate" if tutte else "ATTENZIONE: qualcosa non regge"))
    return tutte
