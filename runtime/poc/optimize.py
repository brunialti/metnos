#!/usr/bin/env python3
"""Test di OTTIMIZZAZIONE completo (2 fasi) per il task NLU-extract core-5.

Leve scorate su ACCURATEZZA *e* latenza *e* token *e* JSON-invalidi.
NB temp=0 => i sampler top_k/top_p/min_p/repeat_penalty sono INERTI (greedy):
l'unica leva extra reale e' enable_thinking + reasoning_budget (free-parse,
perche' json_schema blocca i token di thinking). max_tokens irrilevante (EOS).

FASE 1 (gold-24): accuratezza assoluta per ogni variante (anchor).
FASE 2 (corpus, env SAMPLE=N, default 0=skip): variante vs baseline-A su un
campione GRANDE a passo fisso del corpus reale → accordo-vs-A + latenza + tok.
A e' il riferimento perche' e' la config validata-accurata (8/9 gold).
Server scarico. Deterministico (temp0+seed).
"""
import copy
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from llm_router import tier_endpoint  # noqa: E402

URL = tier_endpoint("fast").rstrip("/") + "/v1/chat/completions"

_NEST = {
    "ordering": {"type": "object", "additionalProperties": False, "properties": {
        "mode": {"type": "string", "enum": ["none", "sort", "group"]},
        "key": {"type": "string"}, "desc": {"type": "boolean"}}},
    "time_window": {"type": "string"},
    "recurrence": {"type": "object", "additionalProperties": False, "properties": {
        "every": {"type": "string"}, "at": {"type": "string"}}},
    "count_intent": {"type": "boolean"}, "visualize_intent": {"type": "boolean"}}
_PF = copy.deepcopy(_NEST)
_PF["ordering"]["required"] = ["mode", "key", "desc"]
_PF["recurrence"]["required"] = ["every", "at"]
SCHEMA_FULL = {"type": "object", "additionalProperties": False, "properties": _PF,
               "required": list(_NEST)}
SCHEMA_LEAN = {"type": "object", "additionalProperties": False,
               "properties": copy.deepcopy(_NEST), "required": []}
SCHEMA_FLAT = {"type": "object", "additionalProperties": False, "properties": {
    "ordering_mode": {"type": "string", "enum": ["none", "sort", "group"]},
    "ordering_key": {"type": "string"}, "ordering_desc": {"type": "boolean"},
    "time_window": {"type": "string"}, "recurrence_every": {"type": "string"},
    "recurrence_at": {"type": "string"}, "count_intent": {"type": "boolean"},
    "visualize_intent": {"type": "boolean"}}, "required": [
    "ordering_mode", "ordering_key", "ordering_desc", "time_window",
    "recurrence_every", "recurrence_at", "count_intent", "visualize_intent"]}

INSTR = (
    "Extract NLU slots from a user phrase in ANY language. Explicit slots only; "
    "precision over recall; empty/none/false when unsure. Output JSON only.\n"
    "ordering.mode: 'sort' ONLY on an explicit sort verb (ordina|sort|order|"
    "trie|ordena|sortieren); 'group' ONLY on an explicit group verb (raggruppa|"
    "group by|gruppiere|agrupa); else 'none'. Never infer from adjectives. "
    "ordering.key: field or ''. ordering.desc: descending?\n"
    "time_window: explicit time only; today|last-<N>d|last-<N>h|next-<N>d|"
    "next-<N>h|YYYY-MM-DD..YYYY-MM-DD; else ''.\n"
    "recurrence.every: explicit repetition <N>m|<N>h|<N>d|<N>w; else ''. "
    "recurrence.at: HH:MM|''.\n"
    "count_intent: explicit quantity ask (quanti|how many|conta). "
    "visualize_intent: explicit show verb (mostra|show|display); not list/read.")
INSTR_FLAT = (INSTR.replace("ordering.mode", "ordering_mode")
              .replace("ordering.key", "ordering_key")
              .replace("ordering.desc", "ordering_desc")
              .replace("recurrence.every", "recurrence_every")
              .replace("recurrence.at", "recurrence_at"))
INSTR_LEAN = INSTR + " OMIT every absent/empty/none/false slot."
INSTR_MIN = ("Extract NLU slots (any language). JSON only. ordering.mode "
             "sort|group|none (explicit verb). time_window today|last-<N>d|''. "
             "recurrence.every <N>m|<N>d|''. count_intent, visualize_intent.")
FEWSHOT = [
    ("ordina i file per dimensione, i piu' grandi prima",
     {"ordering": {"mode": "sort", "key": "size", "desc": True},
      "time_window": "", "recurrence": {"every": "", "at": ""},
      "count_intent": False, "visualize_intent": False}),
    ("leggi la mail di Mario",
     {"ordering": {"mode": "none", "key": "", "desc": False}, "time_window": "",
      "recurrence": {"every": "", "at": ""}, "count_intent": False,
      "visualize_intent": False})]

NN = {"ordering.mode": "none", "count_intent": False, "visualize_intent": False,
      "time_window": ""}
GOLD = [
  ("ordina le mail per mittente in modo decrescente", {"ordering.mode": "sort", "ordering.desc": True}),
  ("sort the files by size", {"ordering.mode": "sort"}),
  ("raggruppa i documenti per cartella", {"ordering.mode": "group"}),
  ("trie les photos par date decroissante", {"ordering.mode": "sort", "ordering.desc": True}),
  ("ordena los archivos por tamano", {"ordering.mode": "sort"}),
  ("gruppiere die E-Mails nach Absender", {"ordering.mode": "group"}),
  ("mostrami le mail degli ultimi 3 giorni", {"time_window": "last-3d", "visualize_intent": True}),
  ("emails from the last 24 hours", {"time_window": {"last-24h", "last-1d"}}),
  ("les fichiers d'aujourd'hui", {"time_window": "today"}),
  ("ricordami ogni 30 minuti di bere", {"recurrence.every": "30m"}),
  ("every day at 9:00 check the inbox", {"recurrence.every": "1d", "recurrence.at": "09:00"}),
  ("recuerdame cada semana", {"recurrence.every": "1w"}),
  ("quante mail ho ricevuto oggi?", {"count_intent": True, "time_window": "today"}),
  ("how many files are there?", {"count_intent": True}),
  ("combien de photos ai-je?", {"count_intent": True}),
  ("mostra le foto della vacanza", {"visualize_intent": True}),
  ("show me the inbox", {"visualize_intent": True}),
  ("leggi la mail di Mario", NN), ("cancella il file /tmp/x.log", NN),
  ("che ore sono?", NN), ("trova foto simili", NN),
  ("invia una mail a Mario", NN), ("elenca i file in /tmp", NN),
  ("scarica https://httpbin.org/get", NN)]


def canon_nested(d):
    o = d.get("ordering") or {}; r = d.get("recurrence") or {}
    return {"ordering.mode": o.get("mode", "none"),
            "ordering.desc": bool(o.get("desc", False)),
            "time_window": d.get("time_window", "") or "",
            "recurrence.every": (r.get("every") or ""),
            "recurrence.at": (r.get("at") or ""),
            "count_intent": bool(d.get("count_intent", False)),
            "visualize_intent": bool(d.get("visualize_intent", False))}

def canon_flat(d):
    return {"ordering.mode": d.get("ordering_mode", "none"),
            "ordering.desc": bool(d.get("ordering_desc", False)),
            "time_window": d.get("time_window", "") or "",
            "recurrence.every": d.get("recurrence_every", ""),
            "recurrence.at": d.get("recurrence_at", ""),
            "count_intent": bool(d.get("count_intent", False)),
            "visualize_intent": bool(d.get("visualize_intent", False))}


def match(got, exp):
    for k, v in exp.items():
        g = got.get(k)
        if callable(v):
            if not v(g): return False
        elif isinstance(v, set):
            if g not in v: return False
        elif isinstance(v, str) and isinstance(g, str):
            if g.strip().lower() != v.strip().lower(): return False
        elif g != v:
            return False
    return True

# 6 campi booleani/categorici per l'accordo-vs-A su corpus (== bench_corpus)
def six(c):
    return (c["ordering.mode"], c["ordering.desc"], bool(c["time_window"]),
            bool(c["recurrence.every"]), c["count_intent"], c["visualize_intent"])


def call(text, schema, instr, think, fewshot):
    msgs = [{"role": "system", "content": instr}]
    for q, j in (fewshot or []):
        msgs += [{"role": "user", "content": q},
                 {"role": "assistant", "content": json.dumps(j, ensure_ascii=False)}]
    msgs.append({"role": "user", "content": text})
    payload = {"model": "local", "temperature": 0, "seed": 42, "max_tokens": 256,
               "chat_template_kwargs": {"enable_thinking": think}, "messages": msgs}
    if schema is not None:
        payload["response_format"] = {"type": "json_schema",
            "json_schema": {"name": "nlu", "schema": schema, "strict": True}}
    if think:
        payload["reasoning_budget"] = think if isinstance(think, int) else 256
        payload["chat_template_kwargs"]["enable_thinking"] = True
    t = time.time()
    req = urllib.request.Request(URL, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    r = json.load(urllib.request.urlopen(req, timeout=90))
    ms = (time.time() - t) * 1000
    ct = (r.get("usage") or {}).get("completion_tokens", -1)
    return ms, ct, r["choices"][0]["message"]["content"]


def parse(raw):
    try:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        return json.loads(m.group(0) if m else raw)
    except Exception:
        return None


# variante = (label, schema, instr, canon, think, fewshot)
VARIANTS = [
    ("A full req 0shot", SCHEMA_FULL, INSTR, canon_nested, False, None),
    ("B flat req 0shot", SCHEMA_FLAT, INSTR_FLAT, canon_flat, False, None),
    ("C full req 2shot", SCHEMA_FULL, INSTR, canon_nested, False, FEWSHOT),
    ("D full min-instr", SCHEMA_FULL, INSTR_MIN, canon_nested, False, None),
    ("E lean opt", SCHEMA_LEAN, INSTR_LEAN, canon_nested, False, None),
    ("F think free b128", None, INSTR, canon_nested, 128, None),
    ("G think free b256", None, INSTR, canon_nested, 256, None),
    ("H think free b512", None, INSTR, canon_nested, 512, None),
]


def phase1():
    call(GOLD[0][0], SCHEMA_FULL, INSTR, False, None)  # warm-up
    print("== FASE 1: accuratezza assoluta (gold-24) ==")
    print(f"{'variante':20} {'acc':>7} {'p50':>7} {'tok':>5} {'inval':>5}")
    res = {}
    for label, sc, ins, cn, th, fs in VARIANTS:
        lat, toks, ok, bad = [], [], 0, 0
        for text, exp in GOLD:
            try:
                ms, ct, raw = call(text, sc, ins, th, fs)
            except Exception:
                bad += 1; continue
            d = parse(raw)
            if d is None:
                bad += 1; continue
            lat.append(ms); toks.append(ct)
            ok += match(cn(d), exp)
        warm = sorted(lat[1:]) or [0]
        res[label] = ok
        print(f"{label:20} {ok:3}/{len(GOLD)} {warm[len(warm)//2]:6.0f}ms "
              f"{sum(toks)/max(1,len(toks)):5.0f} {bad:5}")
    return res


def phase2(n, keep):
    qs = [l.strip() for l in open("/tmp/metnos_queries_uniq.txt", errors="replace")
          if l.strip()]
    stride = max(1, len(qs) // n)
    sample = qs[::stride][:n]
    print(f"\n== FASE 2: corpus grande N={len(sample)} (accordo-vs-A) ==")
    # baseline A su tutto il campione
    ref = {}
    A = VARIANTS[0]
    for q in sample:
        try:
            _, _, raw = call(q, A[1], A[2], A[4], A[5])
            d = parse(raw)
            ref[q] = six(A[3](d)) if d else None
        except Exception:
            ref[q] = None
    print(f"{'variante':20} {'agree-vsA':>9} {'p50':>7} {'p90':>7} {'tok':>5} {'inval':>5}")
    for label, sc, ins, cn, th, fs in VARIANTS:
        if label not in keep:
            continue
        lat, toks, agree, tot, bad = [], [], 0, 0, 0
        for q in sample:
            if ref.get(q) is None:
                continue
            try:
                ms, ct, raw = call(q, sc, ins, th, fs)
            except Exception:
                bad += 1; continue
            d = parse(raw)
            if d is None:
                bad += 1; continue
            lat.append(ms); toks.append(ct); tot += 1
            agree += (six(cn(d)) == ref[q])
        warm = sorted(lat[1:]) or [0]
        print(f"{label:20} {100*agree/max(1,tot):8.1f}% "
              f"{warm[len(warm)//2]:6.0f}ms {warm[int(len(warm)*0.9)]:6.0f}ms "
              f"{sum(toks)/max(1,len(toks)):5.0f} {bad:5}")


def main():
    acc = phase1()
    n = int(os.environ.get("SAMPLE", "0"))
    if n > 0:
        # Fase 2 su corpus grande: solo varianti json-schema che reggono
        # l'accuratezza (>=80% gold). Le thinking (F/G/H) sono lente e gia'
        # caratterizzate in Fase 1 → escluse dal campione grande.
        keep = {k for k, v in acc.items()
                if v >= 0.8 * len(GOLD) and not k.split()[0] in ("F", "G", "H")}
        keep.add("A full req 0shot")
        phase2(n, keep)


if __name__ == "__main__":
    main()
