#!/usr/bin/env python3
"""Tuning parametri LLM per il task NLU-extract (core-5).

Confronta varianti su latenza + COMPLETION_TOKENS (usage) + accuratezza:
  A baseline  : tutti i campi required (output sempre pieno), max_tokens 200
  B lean      : campi OPZIONALI, istruzione "ometti slot assenti" -> output
                minimo nel caso comune (nessuno slot)
  C lean+cap  : come B con max_tokens stretto
Ipotesi: B/C tagliano la latenza del caso comune (la maggioranza delle query
non ha ordering/recurrence) riducendo i token di scaffolding.
Server scarico richiesto. Deterministico (temp0+seed).
"""
import json
import sys
import time
import urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from llm_router import tier_endpoint  # noqa: E402

URL = tier_endpoint("fast").rstrip("/") + "/v1/chat/completions"

_PROPS = {
    "ordering": {"type": "object", "additionalProperties": False, "properties": {
        "mode": {"type": "string", "enum": ["none", "sort", "group"]},
        "key": {"type": "string"}, "desc": {"type": "boolean"}}},
    "time_window": {"type": "string"},
    "recurrence": {"type": "object", "additionalProperties": False, "properties": {
        "every": {"type": "string"}, "at": {"type": "string"}}},
    "count_intent": {"type": "boolean"},
    "visualize_intent": {"type": "boolean"},
}
ALL = list(_PROPS)
import copy
_PF = copy.deepcopy(_PROPS)
_PF["ordering"]["required"] = ["mode", "key", "desc"]
_PF["recurrence"]["required"] = ["every", "at"]
SCHEMA_FULL = {"type": "object", "additionalProperties": False,
               "properties": _PF, "required": ALL}
SCHEMA_LEAN = {"type": "object", "additionalProperties": False,
               "properties": _PROPS, "required": []}

INSTR_BASE = (
    "Extract NLU slots from a user phrase in ANY language. Explicit slots only; "
    "precision over recall. ordering.mode sort|group on explicit sort/group "
    "verb else none. time_window today|last-<N>d|next-<N>h|'' . recurrence.every "
    "<N>m|<N>h|<N>d|<N>w|'' . count_intent / visualize_intent explicit only. "
    "Output JSON only.")
INSTR_LEAN = INSTR_BASE + (" OMIT every slot that is absent/empty/none/false "
                           "(do not emit empty fields).")

CASES = [
    "ordina le mail per mittente in modo decrescente",
    "raggruppa i documenti per cartella",
    "mostrami le mail degli ultimi 3 giorni",
    "ricordami ogni 30 minuti di bere",
    "quante mail ho ricevuto oggi?",
    # ---- caso COMUNE: nessuno slot (la maggioranza del corpus) ----
    "leggi la mail di Mario",
    "elenca i file in /tmp",
    "trova foto simili",
    "cancella il file /tmp/x.log",
    "che ore sono?",
    "invia una mail a Mario",
]


def call(text, schema, instr, max_tokens):
    payload = {"model": "local", "temperature": 0, "seed": 42,
               "max_tokens": max_tokens,
               "chat_template_kwargs": {"enable_thinking": False},
               "response_format": {"type": "json_schema",
                   "json_schema": {"name": "nlu", "schema": schema, "strict": True}},
               "messages": [{"role": "system", "content": instr},
                            {"role": "user", "content": text}]}
    t = time.time()
    req = urllib.request.Request(URL, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    r = json.load(urllib.request.urlopen(req, timeout=60))
    ms = (time.time() - t) * 1000
    ct = (r.get("usage") or {}).get("completion_tokens", -1)
    return ms, ct, r["choices"][0]["message"]["content"]


def run(label, schema, instr, max_tokens):
    lat, toks = [], []
    for c in CASES:
        ms, ct, _ = call(c, schema, instr, max_tokens)
        lat.append(ms); toks.append(ct)
    warm = sorted(lat[1:])
    common = sorted(lat[6:])  # solo i casi "nessuno slot"
    print(f"{label:14} p50={warm[len(warm)//2]:5.0f}ms  "
          f"tok_avg={sum(toks)/len(toks):4.0f}  "
          f"p50_comune={common[len(common)//2]:5.0f}ms  "
          f"tok_comune={sum(toks[6:])/len(toks[6:]):4.0f}")


def main():
    # warm-up (carica il prompt in cache)
    call(CASES[0], SCHEMA_FULL, INSTR_BASE, 200)
    print(f"{'variante':14} {'latenza/accur':>0}")
    run("A full/200", SCHEMA_FULL, INSTR_BASE, 200)
    run("B lean/200", SCHEMA_LEAN, INSTR_LEAN, 200)
    run("C lean/96", SCHEMA_LEAN, INSTR_LEAN, 96)


if __name__ == "__main__":
    main()
