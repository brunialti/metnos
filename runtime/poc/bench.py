#!/usr/bin/env python3
"""Bench POC: UNA sola schema (METNOS_NLU_SCHEMA) su 5 lingue.

Dimostra il punto chiave: NON serve una grammatica per lingua. Lo schema
JSON vincola solo l'OUTPUT (frame semantico, lingua-indipendente); l'INPUT
in qualunque lingua lo capisce il modello (competenza multilingue appresa).
FR/ES/DE NON hanno alcun regex/grammar nel codice: se l'estrazione e'
corretta, la tesi e' provata. Misura accuratezza + latenza (cold/warm/p50/p90).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from nlu_extract import metnos_nlu  # noqa: E402

# (frase, lingua, atteso-subset). Verifichiamo solo i campi rilevanti per caso.
CASES = [
    # ordering
    ("ordina le mail per mittente in modo decrescente", "it",
     {"ordering": {"mode": "sort", "key": "mittente", "desc": True}}),
    ("sort the files by size", "en",
     {"ordering": {"mode": "sort", "desc": False}}),
    ("raggruppa i documenti per cartella", "it",
     {"ordering": {"mode": "group"}}),
    ("trie les photos par date, les plus récentes d'abord", "fr",
     {"ordering": {"mode": "sort", "desc": True}}),
    ("ordena los archivos por tamaño", "es",
     {"ordering": {"mode": "sort"}}),
    ("gruppiere die E-Mails nach Absender", "de",
     {"ordering": {"mode": "group"}}),
    # time_window
    ("mostrami le mail degli ultimi 3 giorni", "it",
     {"time_window": "last-3d", "visualize_intent": True}),
    ("emails from the last 24 hours", "en",
     {"time_window": "last-24h"}),
    ("les fichiers d'aujourd'hui", "fr",
     {"time_window": "today"}),
    # recurrence
    ("ricordami ogni 30 minuti di bere", "it",
     {"recurrence": {"every": "30m"}}),
    ("every day at 9:00 check the inbox", "en",
     {"recurrence": {"every": "1d", "at": "09:00"}}),
    ("recuérdame cada semana", "es",
     {"recurrence": {"every": "1w"}}),
    # count
    ("quante mail ho ricevuto oggi?", "it",
     {"count_intent": True, "time_window": "today"}),
    ("how many files are there?", "en",
     {"count_intent": True}),
    ("combien de photos ai-je?", "fr",
     {"count_intent": True}),
    # nessuno slot (deve restare vuoto)
    ("leggi la mail di Mario", "it",
     {"ordering": {"mode": "none"}, "count_intent": False,
      "time_window": ""}),
    ("delete the file /tmp/x.log", "en",
     {"ordering": {"mode": "none"}, "count_intent": False}),
]


def _subset_ok(got, exp):
    """True se ogni campo di exp combacia in got (ricorsivo per dict)."""
    if isinstance(exp, dict):
        if not isinstance(got, dict):
            return False
        return all(_subset_ok(got.get(k), v) for k, v in exp.items())
    if isinstance(exp, str) and isinstance(got, str):
        return exp.strip().lower() == got.strip().lower()
    return got == exp


def main():
    lat = []
    ok = 0
    print(f"{'L':3}{'ms':>6}  {'res':4}  frase")
    print("-" * 78)
    for i, (text, lang, exp) in enumerate(CASES):
        data, meta = metnos_nlu(text)
        lat.append(meta["latency_ms"])
        good = meta["ok"] and _subset_ok(data, exp)
        ok += good
        # salta la prima (cold) dal calcolo warm
        mark = "OK " if good else "XX "
        print(f"{lang:3}{meta['latency_ms']:6.0f}  {mark}  {text[:48]}")
        if not good:
            print(f"        exp={exp}")
            print(f"        got={data}")
    warm = sorted(lat[1:])
    p50 = warm[len(warm) // 2]
    p90 = warm[int(len(warm) * 0.9)]
    print("-" * 78)
    print(f"ACCURACY: {ok}/{len(CASES)} = {100*ok/len(CASES):.0f}%")
    print(f"LATENCY cold={lat[0]:.0f}ms  warm p50={p50:.0f}ms  p90={p90:.0f}ms  "
          f"max={max(warm):.0f}ms")
    by_lang = {}
    for (_, lang, _), m in zip(CASES, lat):
        by_lang.setdefault(lang, []).append(m)
    print("langs covered (UNA sola schema):", ", ".join(sorted(by_lang)))


if __name__ == "__main__":
    main()
