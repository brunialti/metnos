"""Differenziale sull'intent extractor: prima/dopo una modifica al prompt.

Il banco del prefilter misura `prefilter.rank`, non l'estrazione dell'intento:
per toccare il prompt dell'intento serve una misura sua. Temperatura 0 e seed
fisso rendono il confronto una differenza vera, non rumore.

    python3 misura_intent.py --dump prima.json
    python3 misura_intent.py --confronta prima.json
"""
import argparse
import json
import sys

sys.path.insert(0, "/opt/metnos/runtime")

from intent_extractor import extract_intent          # noqa: E402
from llm_router import LLMRouter                     # noqa: E402
from llm_workloads import tier_for                   # noqa: E402

# Le prime due sono il caso in esame; tutte le altre sono guardie: devono
# restare identiche, altrimenti la modifica ha spostato qualcosa che andava.
CASI = [
    "Vai sul sito booking.com fai login con il mio account e dimmi le prossime prenotazioni",
    "accedi a booking.com e mostrami le mie prenotazioni",
    "trova i file .md nella cartella /opt/metnos/decisions e leggili",
    "cerca le mail di Marco e mettile in un foglio",
    "trova i processi che usano piu' memoria e scrivi un report",
    "leggi le issue chiuse e mettile in un foglio di calcolo",
    "trova le foto del 2020, comprimile e inviamele",
    "apri booking.com e dimmi che ore sono",
    "leggi le mail di oggi e dimmi che impegni ho domani",
    "elenca i file in /tmp e cancellali",
    "scarica il report da https://s.it/r.pdf e salvalo in /tmp",
    "che impegni ho domani",
    "dimmi le prossime prenotazioni",
]


def _fast(sys_msg, user_msg, *, max_tokens=80, **kw):
    ck = {"max_tokens": max_tokens, "request_timeout_s": 90}
    if kw.get("grammar") is not None:
        ck["grammar"] = kw["grammar"]
    res = LLMRouter().provider(tier_for("intent.extract")).chat(
        sys_msg, user_msg, **ck)
    return (getattr(res, "text", res) or "").strip()


def _forma(intento) -> str:
    if not intento:
        return "-"
    azioni = intento.get("actions") or [
        {"verb": intento.get("verb"), "object": intento.get("object")}]
    return " | ".join(f"{a.get('verb')}/{a.get('object')}" for a in azioni)


def misura() -> dict:
    return {q: _forma(extract_intent(q, _fast)) for q in CASI}


ap = argparse.ArgumentParser()
ap.add_argument("--dump")
ap.add_argument("--confronta")
a = ap.parse_args()

ora = misura()
if a.dump:
    json.dump(ora, open(a.dump, "w"), ensure_ascii=False, indent=1)
    print(f"scritto {a.dump}")
if a.confronta:
    prima = json.load(open(a.confronta))
    cambi = 0
    for q, v in ora.items():
        p = prima.get(q, "?")
        if p != v:
            cambi += 1
            print(f"CAMBIA  {q}\n   prima: {p}\n   dopo : {v}")
    print(f"\ncambiate {cambi}/{len(ora)}")
else:
    for q, v in ora.items():
        print(f"{v:52} <- {q}")
