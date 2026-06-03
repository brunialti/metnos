"""synth_rare_objects.py — Sintetizza query per classi rare via Gemma 4 26B locale.

Per ogni oggetto under-represented (<20 pair), genera 15 query plausibili IT.
Append a /tmp/training_data_v2.jsonl come /tmp/training_data_v3.jsonl.
"""
from __future__ import annotations
import json
import re
import time
import urllib.request
from collections import Counter
from pathlib import Path

OBJ19 = ["files","dirs","packages","messages","events","contacts","places",
         "processes","urls","numbers","images","signatures","texts",
         "proposals","persons","tasks","inputs","credentials","entries"]

ANCHORS_IT = {
    "files": "file, documenti pdf/csv/txt, leggi/scrivi file",
    "dirs": "cartelle, directory, alberatura, subdir",
    "packages": "pacchetti apt/pip/deb installati, dipendenze software",
    "messages": "email/mail/messaggi, inbox, gmail, mittente",
    "events": "eventi calendario, appuntamenti, riunioni",
    "contacts": "rubrica contatti, indirizzi email salvati",
    "places": "luoghi geografici, posti vicini, ristoranti/farmacie",
    "processes": "processi systemd, ram/cpu/disk/uptime",
    "urls": "url, siti web, ricerche google",
    "numbers": "numeri, calcoli matematici, data/ora/timezone",
    "images": "immagini, foto, fotografie",
    "signatures": "hash SHA-256/MD5, firme digitali, checksum",
    "texts": "estratti testuali, righe di file, paragrafi",
    "proposals": "proposte di sistema (synth/introvertiva)",
    "persons": "persone individuali (chi e' X), profili biometrici",
    "tasks": "task scheduler v2, promemoria, timer, ricorrenze",
    "inputs": "valori input dialog form UI",
    "credentials": "password credenziali oauth account",
    "entries": "voci/elementi/record di liste interne",
}


def synth(obj: str, n: int = 15) -> list[str]:
    anchor = ANCHORS_IT[obj]
    other_objs = [o for o in OBJ19 if o != obj]
    prompt = f"""Sei un generatore di query NL per assistente personale italiano.

Oggetto target: **{obj}**
Significato: {anchor}

Altri oggetti possibili (NON includere ambiguità con questi): {", ".join(other_objs[:8])}

Genera {n} query naturali IT che un utente userebbe per parlare di "{obj}".
Lunghezza 3-8 parole. Vari registri (formale, colloquiale, breve).
Una per riga. NIENTE numerazione, NIENTE virgolette, NIENTE prefisso.

QUERY:"""

    body = json.dumps({
        "model": "gemma",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1200,
        "temperature": 0.7,
    }).encode()
    req = urllib.request.Request(
        "http://localhost:8080/v1/chat/completions",
        data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        data = json.loads(r.read())
    msg = data["choices"][0]["message"]
    # Gemma "thinking" mode: content vuoto, output in reasoning_content
    text = msg.get("content") or msg.get("reasoning_content") or ""
    # Extract IT phrases bounded by quotes/dashes in reasoning trace
    # Heuristic: prendi righe italiane plausibili (no draft/refine/topic/format meta)
    META_TOKENS = ("Topic:", "Quantity:", "Format:", "Language:", "Draft:",
                    "Refinement:", "Refined:", "Plan:", "Query ", "TARGET:",
                    "Considerations:", "Step", "User", "Output:", "Italian:",
                    "Italian -", "italiano:")
    lines = []
    seen = set()
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw: continue
        # Skip meta lines
        if any(raw.startswith(m) for m in META_TOKENS): continue
        if "Considerazioni" in raw or "Categoria" in raw[:15]: continue
        # Extract content between quotes if present
        m = re.search(r'["«]([^"»]{4,80})["»]', raw)
        if m:
            cand = m.group(1).strip()
        else:
            cand = re.sub(r"^[-*•\d\.\)\s>]+", "", raw).strip()
            cand = re.sub(r"^Italian[:\s\-]+", "", cand, flags=re.I).strip()
            cand = re.sub(r"^Query \d+[:\.\)]?\s*", "", cand).strip()
            cand = re.sub(r"^.*?[:\-]\s*", "", cand).strip() if (":" in cand and len(cand) > 50) else cand
        # Filter: italian-like and not meta
        if not (3 <= len(cand) <= 100): continue
        if cand.lower() in {"query:", obj, "italian", "draft"}: continue
        # Must contain at least one ITA letter
        if not re.search(r"[aeiou]", cand.lower()): continue
        # Skip lines that are pure English (no italian articles/prepositions)
        if not re.search(r"\b(il|lo|la|i|le|un|una|di|del|delle|che|per|con|su|in|da|al|alla|ai|alle|dal|dalla|sui|come|cosa|quanto|quale|quali|quanti|quante|ora|oggi|domani)\b", cand.lower()):
            # allow short commands with verbs typical italian
            if not re.search(r"\b(mostra|cerca|trova|crea|leggi|scrivi|cancella|elimina|calcola|fai|installa|aggiungi|imposta|paga|invia|salva|aggiorna|visualizza|filtra|conta|estrai|riassumi|apri|chiudi|verifica|controlla|ricorda|programma|schedula|ricordami|dimmi|che|chi|come|quale|quanti|quante|quanto)\b", cand.lower()):
                continue
        # Dedup case-insensitive
        key = cand.lower()
        if key in seen: continue
        seen.add(key)
        lines.append(cand)
    return lines[:n]


def main():
    # Load existing
    existing = []
    for ln in open("/tmp/training_data_v2.jsonl"):
        existing.append(json.loads(ln))
    counts = Counter(d["object"] for d in existing)

    target_min = 20  # ogni classe almeno 20 pair
    rare = sorted([o for o in OBJ19 if counts.get(o, 0) < target_min])
    print(f"Rare objects (< {target_min} pair): {rare}")
    print()

    new_pairs = []
    for obj in rare:
        cur = counts.get(obj, 0)
        need = target_min - cur
        print(f"  {obj}: have {cur}, need {need}, synthesizing...", flush=True)
        t0 = time.time()
        try:
            queries = synth(obj, n=need + 3)  # margin per dedup
            existing_q = {d["query"].lower() for d in existing if d["object"] == obj}
            added = 0
            for q in queries:
                if added >= need: break
                if q.lower() in existing_q: continue
                new_pairs.append({"query": q, "object": obj, "synth": True})
                added += 1
            print(f"    +{added} new in {time.time()-t0:.1f}s")
        except Exception as e:
            print(f"    FAIL: {e}")

    # Write merged
    out = "/tmp/training_data_v3.jsonl"
    with open(out, "w") as f:
        for d in existing + new_pairs:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    print(f"\nWritten: {out}")
    print(f"  Existing: {len(existing)}, Synthesized: {len(new_pairs)}, Total: {len(existing)+len(new_pairs)}")


if __name__ == "__main__":
    main()
