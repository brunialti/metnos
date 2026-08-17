"""Quanto rumore entra nel fine, e quanto segnale ne esce.

Il banco del prefilter misura il routing, quello dell'intento misura verbo e
oggetto: nessuno dei due dice se le parole con cui si CHIEDE stanno finendo
dentro cio' che si CERCA. Questa misura risponde a quella domanda sola, sul
corpus di richieste reali, e serve a promuovere o bocciare una modifica alle
famiglie di rumore del lessico.

Per ogni richiesta si confrontano due giudizi indipendenti:
  - il filtro deterministico di oggi (`action_resolver.goal_tokens`), che dice
    quali parole restano;
  - il modello, che assegna a ogni parola numerata il suo ruolo — l'unica forma
    che regge (misurata: chiedergli di RISCRIVERE la richiesta produce
    riordini, traduzioni e omissioni; chiedergli una lettera per parola senza
    numerarle crolla, perche' non sa contare le parole).

RESIDUO  parola che il deterministico TIENE e il modello dice grammatica o
         verbo con cui si chiede -> rumore che entra nel fine.
PERDITA  parola che il deterministico BUTTA e il modello dice contenuto,
         letterale, possesso, quantita' o stato -> segnale che esce dal fine.

Il modello e' un giudice, non un oracolo: sbaglia sulle faccette («prossime»
letta come grammatica). Le due percentuali si leggono come una TENDENZA fra un
prima e un dopo sullo stesso corpus, mai come un voto assoluto.

    ./.venv/bin/python internal/tools/misura_residuo_fine.py [quante]
"""
import json
import random
import re
import sys

sys.path.insert(0, "/opt/metnos/runtime")
sys.path.insert(0, "/opt/metnos/runtime/playwright_sidecar")

from llm_router import LLMRouter          # noqa: E402
from llm_workloads import tier_for        # noqa: E402
import action_resolver as AR              # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 60
CORPUS = "/opt/metnos/data/prefilter_corpus_snapshot.jsonl"

# Prompt in inglese: la funzione e' indipendente dalla lingua della richiesta.
# Solo segnaposto negli esempi, mai forme copiabili.
ISTRUZIONE = (
    "DEVI assegnare un ruolo a ogni parola numerata della richiesta.\n"
    "Ruoli: C la parola dice CHE COSA si vuole; G articolo, preposizione, "
    "congiunzione, cortesia; R verbo con cui l'utente CHIEDE o AGISCE; P dice "
    "DI CHI sono gli elementi; Q dice QUANTI; S dice IN CHE STATO; L letterale "
    "(percorso, URL, indirizzo, data, numero, virgolettato).\n"
    "NON DEVI riscrivere, tradurre, riordinare o saltare una parola.\n"
    "Emetti una riga per parola: <numero> <ruolo>. Nient'altro.\n"
    "OK: 1 R / 2 G / 3 C\n"
    "ERRORE: 1 R <parola>\n"
    "I numeri dell'esempio sono segnaposto: non copiarli."
)
RUOLI = set("CGRPQSL")
TENUTI = set("CLPQS")
DOMINIO = re.compile(r"\b[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}\b", re.IGNORECASE)


def _verbi_del_vocabolario() -> set[str]:
    """Le forme di superficie dei verbi canonici, dal SoT multilingue."""
    try:
        from vocab import ACTION_MAPPING, LANGS
    except Exception:  # noqa: BLE001
        return set()
    forme = set()
    for spec in ACTION_MAPPING.values():
        if isinstance(spec, dict):
            for lang in LANGS:
                forme |= {AR.normalize(f) for f in spec.get(lang, ()) if f}
    return {f for f in forme if f}


def _chat(sys_msg, user_msg, *, max_tokens=400):
    res = LLMRouter().provider(tier_for("intent.extract")).chat(
        sys_msg, user_msg, max_tokens=max_tokens, request_timeout_s=120)
    return (getattr(res, "text", res) or "").strip()


def etichetta(q):
    parole = q.split()
    numerata = "\n".join(f"{i} {p}" for i, p in enumerate(parole, 1))
    out = _chat(ISTRUZIONE, numerata)
    etichette = {}
    for riga in out.splitlines():
        m = re.match(r"\s*(\d+)\s*[.:)-]?\s*([A-Z])\b", riga.strip())
        if m and m.group(2) in RUOLI:
            etichette[int(m.group(1))] = m.group(2)
    return parole, etichette


def main() -> int:
    righe = [json.loads(r)["query"] for r in open(CORPUS) if r.strip()]
    random.Random(42).shuffle(righe)
    righe = righe[:N]
    verbi_vocab = _verbi_del_vocabolario()

    residuo = {"R": 0, "G": 0}
    residuo_nel_vocabolario = 0
    perdita = {r: 0 for r in TENUTI}
    parole_tot = disallineate = 0
    esempi_residuo, esempi_perdita = [], []

    for q in righe:
        tenuti_det = set(AR.goal_tokens(q))
        try:
            parole, etichette = etichetta(q)
        except Exception:  # noqa: BLE001
            continue
        if len(etichette) != len(parole):
            disallineate += 1
            continue
        parole_tot += len(parole)
        for i, parola in enumerate(parole, 1):
            if DOMINIO.search(parola):
                continue                   # scartato per progetto dal resolver
            token = AR.normalize(parola).split()
            nel_fine = any(t in tenuti_det for t in token)
            ruolo = etichette[i]
            if nel_fine and ruolo in residuo:
                residuo[ruolo] += 1
                if any(t in verbi_vocab for t in token):
                    residuo_nel_vocabolario += 1
                if len(esempi_residuo) < 10:
                    esempi_residuo.append((parola, ruolo, q[:56]))
            elif not nel_fine and ruolo in TENUTI and token:
                perdita[ruolo] += 1
                # Tre esempi PER RUOLO, non i primi dieci: la perdita che conta
                # (contenuto, stato) e' rara, e un elenco in ordine di corpus la
                # nasconde dietro possessivi e quantificatori, che si scartano
                # per progetto e si ripristinano altrove.
                if sum(1 for _p, r, _q in esempi_perdita if r == ruolo) < 3:
                    esempi_perdita.append((parola, ruolo, q[:56]))

    tot_residuo, tot_perdita = sum(residuo.values()), sum(perdita.values())
    pct = lambda n: 100 * n / max(1, parole_tot)  # noqa: E731
    print(f"richieste misurate : {len(righe) - disallineate}/{len(righe)} "
          f"(disallineate {disallineate})")
    print(f"parole giudicate   : {parole_tot}")
    print(f"RESIDUO : {tot_residuo} ({pct(tot_residuo):.1f}%)  "
          f"verbi {residuo['R']} · grammatica {residuo['G']}")
    print(f"  di cui verbi gia' noti al vocabolario canonico: "
          f"{residuo_nel_vocabolario}")
    print(f"PERDITA : {tot_perdita} ({pct(tot_perdita):.1f}%)  "
          + " · ".join(f"{r} {n}" for r, n in perdita.items() if n))
    # Senza questa riga il totale si legge male: P e Q si scartano per progetto
    # (non nominano contenuto, e la navigazione li ripristina a parte), e gli
    # interrogativi il giudice li mette li' dentro. Solo C e S vanno guardati
    # uno per uno, e anche li' un «che»/«cosa» etichettato C e' un errore suo.
    print("  P e Q si scartano per progetto; guarda C e S, uno per uno.")
    print("\nesempi di RESIDUO:")
    for parola, ruolo, q in esempi_residuo:
        print(f"  {parola!r} -> {ruolo}   [{q}]")
    print("\nesempi di PERDITA:")
    for parola, ruolo, q in esempi_perdita:
        print(f"  {parola!r} -> {ruolo}   [{q}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
