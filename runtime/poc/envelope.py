#!/usr/bin/env python3
"""POC envelope ESTESO: quanto possiamo ASSORBIRE/ARRICCHIRE in una sola call.

Misura latenza + correttezza di un frame "NLU envelope" che assorbe intent,
compound, undo, notify, provider, format, availability, propose, multi-step,
count-con-numero (§E.2), entita'/NER (§E.3). Confronto latenza vs il frame a
5 slot. Prompt in inglese (regola funzioni language-independent), pattern-
oriented (placeholder, non letterali). Deterministico (temp0+seed).
"""
import json
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from nlu_extract import extract, METNOS_NLU_SCHEMA, METNOS_NLU_INSTRUCTION  # noqa: E402

_S = {"type": "string"}
_B = {"type": "boolean"}
ENVELOPE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "actions": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"verb": _S, "object": _S},
            "required": ["verb", "object"]}},
        "ordering": {"type": "object", "additionalProperties": False,
            "properties": {"mode": {"type": "string",
                "enum": ["none", "sort", "group"]}, "field": _S, "desc": _B},
            "required": ["mode", "field", "desc"]},
        "time_window": _S,
        "recurrence": {"type": "object", "additionalProperties": False,
            "properties": {"every": _S, "at": _S}, "required": ["every", "at"]},
        "count": {"type": "object", "additionalProperties": False,
            "properties": {"requested": _B, "n": {"type": "integer"}, "of": _S},
            "required": ["requested", "n", "of"]},
        "visualize": _B,
        "notify": {"type": "object", "additionalProperties": False,
            "properties": {"requested": _B, "channel": {"type": "string",
                "enum": ["none", "email", "telegram"]}},
            "required": ["requested", "channel"]},
        "provider": {"type": "string",
            "enum": ["none", "github", "google_workspace"]},
        "output_format": _S,
        "availability_check": _B,
        "propose": {"type": "object", "additionalProperties": False,
            "properties": {"requested": _B, "n": {"type": "integer"}},
            "required": ["requested", "n"]},
        "multi_step": _B,
        "is_undo": _B,
        "entities": {"type": "object", "additionalProperties": False,
            "properties": {
                "persons": {"type": "array", "items": _S},
                "places": {"type": "array", "items": _S},
                "paths": {"type": "array", "items": _S},
                "repo": _S},
            "required": ["persons", "places", "paths", "repo"]},
        "negation": _B,
    },
    "required": ["actions", "ordering", "time_window", "recurrence", "count",
                 "visualize", "notify", "provider", "output_format",
                 "availability_check", "propose", "multi_step", "is_undo",
                 "entities", "negation"],
}

ENVELOPE_INSTRUCTION = (
    "Extract a full NLU envelope from a user phrase in ANY language. Explicit "
    "slots only; precision over recall; empty/none/false/0 when unsure. "
    "Output JSON only, no prose.\n"
    "actions: ordered list of {verb,object}; verb/object are canonical English "
    "(find|read|get|list|send|move|delete|create|write|set|filter|sort; "
    "messages|files|events|images|issues|persons|places|urls). Split compound "
    "requests (X 'and then' Y) into multiple actions.\n"
    "ordering.mode: sort|group|none (explicit sort/group verb only). "
    "ordering.field: canonical (sender|size|date|subject|name) or ''. "
    "ordering.desc: descending?\n"
    "time_window: today|last-<N>d|last-<N>h|next-<N>d|next-<N>h|"
    "YYYY-MM-DD..YYYY-MM-DD|'' (N from phrase; keep past/future).\n"
    "recurrence.every: <N>m|<N>h|<N>d|<N>w|''. recurrence.at: HH:MM|''.\n"
    "count.requested: explicit quantity/how-many ask? count.n: the number that "
    "modifies a COUNT NOUN (not a year/price/id); 0 if none. count.of: that "
    "noun (photos|files|emails) or ''.\n"
    "visualize: explicit show/display verb? notify.requested + notify.channel "
    "(email|telegram|none).\n"
    "provider: github|google_workspace|none. output_format: xlsx|pdf|csv|doc|"
    "json|''.\n"
    "availability_check: asks 'if free'/'if there is a slot'? "
    "propose.requested + propose.n (how many options asked).\n"
    "multi_step: more than one action? is_undo: cancel/undo last op? "
    "negation: excludes/except/not?\n"
    "entities.persons|places|paths: string arrays of names mentioned. "
    "entities.repo: 'owner/name' or ''."
)

PROBE = [
    ("trova 100 foto di Silvia e Carol al mare", "it",
     "count.n=100 of=photos, persons=[Silvia,Carol]"),
    ("annulla l'ultima operazione", "it", "is_undo=true"),
    ("elenca le issue aperte su github di brunialti/metnos", "it",
     "provider=github, repo=brunialti/metnos, actions find/issues"),
    ("metti i risultati in un excel e mandameli via email", "it",
     "output_format=xlsx, notify email, multi_step"),
    ("proponi 3 slot liberi la prossima settimana", "it",
     "propose n=3, availability/time next-7d"),
    ("trie les photos par date et envoie-les moi", "fr",
     "ordering sort/date, notify, multi_step"),
    ("ordina le mail per mittente tranne lo spam", "it",
     "ordering sort/sender, negation=true"),
    ("how many unread emails do I have today?", "en",
     "count of=emails, time today"),
    ("borra los archivos temporales en /tmp", "es",
     "actions delete/files, paths=[/tmp]"),
    ("leggi le ultime 5 mail e poi riassumile", "it",
     "count n=5 of=emails, multi_step, actions read+...")
]


def main():
    print("=== ENVELOPE ESTESO (15 campi) ===")
    lat = []
    for text, lang, note in PROBE:
        t = time.time()
        data, meta = extract(text, ENVELOPE_SCHEMA, ENVELOPE_INSTRUCTION,
                             max_tokens=420)
        ms = (time.time() - t) * 1000
        lat.append(ms)
        print(f"\n[{lang} {ms:.0f}ms ok={meta['ok']}] {text}")
        print(f"  atteso: {note}")
        print(f"  got: {json.dumps(data, ensure_ascii=False)}")
    warm = sorted(lat[1:])
    print(f"\nENVELOPE latency: cold={lat[0]:.0f} warm p50={warm[len(warm)//2]:.0f} "
          f"p90={warm[int(len(warm)*0.9)]:.0f} max={max(warm):.0f}ms")
    # confronto: stesso testo col frame a 5 slot
    print("\n=== confronto latenza: frame-5slot stesso input ===")
    lat5 = []
    for text, lang, _ in PROBE:
        t = time.time()
        extract(text, METNOS_NLU_SCHEMA, METNOS_NLU_INSTRUCTION, max_tokens=200)
        lat5.append((time.time() - t) * 1000)
    w5 = sorted(lat5[1:])
    print(f"frame-5slot latency: warm p50={w5[len(w5)//2]:.0f} "
          f"p90={w5[int(len(w5)*0.9)]:.0f}ms")
    print(f"\nΔ p50 (envelope - frame5) = "
          f"{warm[len(warm)//2]-w5[len(w5)//2]:.0f}ms per assorbire ~10 funzioni in piu'")


if __name__ == "__main__":
    main()
