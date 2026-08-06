"""Estrae dal registro reale un corpus di piani ANONIMIZZATO e campionato."""
import json, os, re, sqlite3, sys, hashlib

# Chi rigira piani salvati NON e' traffico reale: senza questa riga il replay
# scrive nel contatore di esercizio delle guardie, cioe' nell'unico dato su cui
# si decide un ritiro. Successo il 6/8/2026 (1673 attraversamenti finti).
os.environ.setdefault("METNOS_GUARD_STATS", "0")
from pathlib import Path
from collections import defaultdict

SUB = [
    (re.compile(r"/home/roberto", re.I), "/home/utente"),
    (re.compile(r"[Bb]runialti"), "Rossi"),
    (re.compile(r"\broberto\b", re.I), "utente"),
    (re.compile(r"[\w.+-]+@[\w.-]+\.\w+"), "utente@example.com"),
    (re.compile(r"C:\\\\Users\\\\rober", re.I), r"C:\\\\Users\\\\utente"),
]
VIETATO = re.compile(r"analisi\s|medich|certificat|password|passwd|api[_-]?key", re.I)


def _stringhe(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for v in o.values():
            yield from _stringhe(v)
    elif isinstance(o, list):
        for v in o:
            yield from _stringhe(v)


def anonimizza(s: str) -> str:
    for rx, rep in SUB:
        s = rx.sub(rep, s)
    return s


def main(dest):
    db = sqlite3.connect(str(Path.home() / ".local/share/metnos/autopath.sqlite"))
    gruppi = defaultdict(list)
    for sig, fj in db.execute("select intent_sig, framework_json from observations"):
        fj = anonimizza(fj)
        if VIETATO.search(fj):
            continue
        try:
            d = json.loads(fj)
        except Exception:
            continue
        # I valori lunghi sono contenuto REALE bake-ato (corpi di mail, righe
        # di file): non servono al comportamento delle guardie e non possono
        # finire in un file versionato. Il piano che li porta si scarta.
        if any(isinstance(v, str) and len(v) > 120 for v in _stringhe(d)):
            continue
        forma = tuple(s.get("tool") for s in d.get("steps") or [])
        chiavi = tuple(sorted(k for s in (d.get("steps") or [])
                              for k in (s.get("args") or {})))
        gruppi[(sig, forma, chiavi)].append(d)
    casi = []
    for (sig, _forma, _k), piani in sorted(gruppi.items(), key=lambda kv: str(kv[0])):
        visti = set()
        for d in piani:
            h = hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()
            if h in visti:
                continue
            visti.add(h)
            casi.append({"sig": sig, "query": "", "plan": d})
            if len(visti) >= 2:      # al massimo due varianti di valori per forma
                break
    # I fastpath portano il TESTO della query: senza, le guardie che leggono
    # la query non sparano mai e il corpus non le copre.
    fp = sqlite3.connect(str(Path.home() / ".local/share/metnos/fastpaths.sqlite"))
    for txt, v, o, fj in fp.execute(
            "select canonical_text, intent_verb, intent_object, framework_json "
            "from fastpaths"):
        fj, txt = anonimizza(fj), anonimizza(txt or "")
        if VIETATO.search(fj) or VIETATO.search(txt):
            continue
        try:
            d = json.loads(fj)
        except Exception:
            continue
        if any(isinstance(x, str) and len(x) > 120 for x in _stringhe(d)):
            continue
        casi.append({"sig": f"{v or ''}|{o or ''}|", "query": txt, "plan": d})

    Path(dest).write_text(json.dumps(casi, ensure_ascii=False, sort_keys=True))
    print(f"casi: {len(casi)}  gruppi: {len(gruppi)}  "
          f"dimensione: {Path(dest).stat().st_size/1024:.0f} KB")


if __name__ == "__main__":
    main(sys.argv[1])
