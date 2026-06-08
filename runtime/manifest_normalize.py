#!/usr/bin/env python3
"""manifest_normalize.py — normalizzatore SISTEMICO delle description manifest (§2.5).

Trasforma GENERALE, non ad-hoc: una description legacy verbosa -> SOLA TESTA a 4
capitoli (SCOPO/PATTERN/NON/OUT), IT+EN, riusando la macchina synt (stage-4
family). Il prompt `synt_description_normalize.j2` codifica la regola §2.5; il
driver fornisce SOLO contesto deterministico per-manifest (verbo, oggetto, args,
fratelli, shape §2.6) e lascia all'LLM-middle la compressione creativa. Niente
testo hardcoded per-manifest (§7.3/§8.3: si itera il prompt, non l'output).

Flusso per famiglia (routing-critical, §2.5):
  preview <names...>   -> mostra l'output LLM SENZA scrivere (review umano)
  apply   <names...>   -> scrive description + aggiorna lang_state + ri-firma
  --family <verb>      -> tutti i legacy di quella famiglia
  --list-legacy        -> elenca i manifest senza i 4 capitoli

Guard dopo ogni apply: `python3 bench/routing_subset_bench.py --baseline ...`.
Convergenza (§8.3): output debole = si corregge il PROMPT, non il file.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

_RT = Path(__file__).resolve().parent
if str(_RT) not in sys.path:
    sys.path.insert(0, str(_RT))

import prompt_loader  # noqa: E402
from manifest_rules import HEAD_MAX, DESC_MAX  # noqa: E402

try:
    from vocab import PRODUCER_VERBS, DESTRUCTIVE_VERBS  # noqa: E402
except Exception:
    PRODUCER_VERBS = frozenset({"read", "find", "list", "get", "filter", "sort",
                                "group", "classify", "compute", "compare",
                                "describe", "extract"})
    DESTRUCTIVE_VERBS = frozenset({"move", "delete", "send", "write", "create",
                                   "change", "order", "share", "reply", "set"})

_EXEC = _RT.parent / "executors"
_CHAPTERS = ("SCOPO:", "PATTERN:", "NON:", "OUT:")
# Qualifier di FORMATO (§2.2 Famiglia 1, SoT vocab). Un tool con questo qualifier
# e' scelto dal router PER FORMATO: lo SCOPO deve nominare il trigger concreto
# (estensione/tipo) altrimenti il wise-LLM confonde .html con PDF (regressione 7/6).
_FORMAT_QUALIFIERS = frozenset({
    "csv", "xlsx", "ocr", "zip", "pdf", "xml", "html", "json", "text",
    "gz", "tar", "video", "audio", "image", "hash", "spreadsheet", "doc"})
_UNIVERSAL_ARGS = frozenset({"from_step", "entries"})
# Marker di "coda implementativa" che NON deve sopravvivere dopo OUT: (smell).
_TAIL_SMELL = ("User-Agent", "backend:", "Backend:", "pypdf", "openpyxl",
               "Tesseract", "stdlib", "apt:", "RFC822", "IMAP4")


# -------------------------------------------------------------------------
# Catalogo deterministico
# -------------------------------------------------------------------------
def _desc_pair(m: dict) -> tuple[str, str]:
    d = m.get("description")
    if isinstance(d, dict):
        return (d.get("it") or d.get("en") or "", d.get("en") or d.get("it") or "")
    return (d or "", d or "")


def _first_sentence(s: str) -> str:
    s = (s or "").strip().replace("\n", " ")
    # se gia' a capitoli, lo SCOPO e' la testa naturale
    if s.startswith("SCOPO:"):
        cut = s.find("PATTERN:")
        return s[:cut].strip() if cut > 0 else s[:120]
    m = re.search(r"\.\s", s)
    return (s[:m.start() + 1] if m else s)[:140].strip()


def _builtin_tool_names() -> set:
    """Nomi dei tool BUILTIN runtime (non manifest in executors/): vanno noti al
    validator dead-ref, altrimenti un NON: che cita list_tasks/undo_last_turn ecc.
    (tool REALI) viene falsamente bocciato. Fonte: BUILTIN_INPROC_SPECS dei moduli
    registrati + universal helpers §11 (doctrina stabile)."""
    names = {"classify_entries", "extract_entries", "describe_entries",
             "undo_last_turn", "get_inputs"}
    for mod_name in ("recurring_tasks", "skill_admin"):
        try:
            mod = __import__(mod_name)
            for entry in getattr(mod, "BUILTIN_INPROC_SPECS", None) or []:
                if entry.get("name"):
                    names.add(entry["name"])
        except Exception:
            pass
    return names


def load_catalog() -> dict:
    cat = {}
    for mt in sorted(_EXEC.glob("*/manifest.toml")):
        try:
            m = tomllib.load(open(mt, "rb"))
        except Exception:
            continue
        name = m.get("name", mt.parent.name)
        it, en = _desc_pair(m)
        parts = name.split("_")
        cat[name] = {
            "name": name,
            "verb": parts[0],
            "object": parts[1] if len(parts) > 1 else "",
            "props": (m.get("args") or {}).get("properties") or {},
            "it": it, "en": en,
            "affinity": m.get("affinity") or [],
            "scopo1": _first_sentence(it),
            "has_chapters": all(c in it for c in _CHAPTERS),
        }
    return cat


def _arg_short(decl: dict) -> str:
    ad = decl.get("description") if isinstance(decl, dict) else ""
    if isinstance(ad, dict):
        ad = ad.get("it") or ad.get("en") or ""
    ad = (ad or "").replace("\n", " ").strip()
    return ad[:70]


def _args_block(props: dict) -> str:
    if not props:
        return "(nessun argomento)"
    out = []
    for an, decl in props.items():
        t = decl.get("type", "?") if isinstance(decl, dict) else "?"
        s = _arg_short(decl)
        out.append(f"{an}:{t}" + (f" — {s}" if s else ""))
    return "; ".join(out)


def _format_qualifier(name: str) -> str:
    """Ritorna il qualifier di formato (3° token) se nel famiglia-1 §2.2, else ''."""
    parts = name.split("_")
    return parts[2] if len(parts) >= 3 and parts[2] in _FORMAT_QUALIFIERS else ""


_PRODUCER_ORTHO_VERBS = ("get", "read", "find", "list", "filter")


def _verb_ortho(verb: str) -> str:
    """Boundary §2.2 ortogonale dei 5 verbi-produttori (SoT: vocab.ACTION_MAPPING).

    Iniettato SOLO per i produttori, dove la confusione get/read/find/list/filter
    e' il misroute classico (es. get_urls vs read_urls_html). Grounding generale,
    non per-manifest: la doctrina §2.2 vale per ogni dominio.
    """
    if verb not in _PRODUCER_ORTHO_VERBS:
        return ""
    try:
        from vocab import ACTION_MAPPING
    except Exception:
        return ("get=id noti o snapshot grezzo; read=id->contenuto estratto; "
                "find=pattern/query (discovery); list=enum container senza contenuto; "
                "filter=lista preesistente+predicato")
    out = []
    for v in _PRODUCER_ORTHO_VERBS:
        b = (ACTION_MAPPING.get(v) or {}).get("boundary", "")
        first = _first_sentence(b)
        if first:
            out.append(f"{v}: {first}")
    return " | ".join(out)


def _out_shape_hint(verb: str) -> str:
    if verb in (DESTRUCTIVE_VERBS - {"send"}) or verb in {"move", "write", "create",
                                                          "delete", "change", "order"}:
        return "results=[...] (verbo trasformativo §2.6); usa i campi reali dalla sorgente"
    if verb == "send":
        return "ok/results (invio); usa i campi reali dalla sorgente"
    return "entries=[...] (verbo producer §2.6); usa i campi reali dalla sorgente"


def _siblings_block(name: str, object_: str, cat: dict, cap: int = 6) -> str:
    """Fratelli per il capitolo NON:, ORDINATI per confondibilita'.

    I piu' confondibili sono le varianti STESSO-VERBO-STESSO-OGGETTO (qualifier
    diversi: read_files_csv vs read_files_xlsx vs read_files) — li mettiamo per
    primi. Poi i cross-verbo sullo stesso oggetto (boundary verbo §2.2). Cap
    basso: la disambiguazione utile e' 1-3 vicini, non l'elenco completo.
    """
    my_verb = name.split("_")[0]
    same_verb, cross_verb = [], []
    for n, meta in cat.items():
        if n == name or meta["object"] != object_:
            continue
        (same_verb if meta["verb"] == my_verb else cross_verb).append((n, meta["scopo1"]))
    same_verb.sort(); cross_verb.sort()
    sib = same_verb + cross_verb
    if not sib:
        return "(nessun fratello sullo stesso oggetto)"
    return " | ".join(f"{n}: {s}" for n, s in sib[:cap])


# -------------------------------------------------------------------------
# Validazione (hard = blocca; length = soft, spinge ma non blocca)
# -------------------------------------------------------------------------
def _pattern_call_args(desc: str, name: str) -> list[str]:
    start = desc.find("PATTERN:")
    end = desc.find("NON:", start) if start >= 0 else -1
    span = desc[start:end] if start >= 0 and end > start else desc
    args = []
    for m in re.finditer(rf"{re.escape(name)}\s*\(([^)]*)\)", span):
        args += re.findall(r"(?:^|[(,\s])([a-zA-Z_]\w*)\s*=(?!=)", m.group(1))
    return args


def validate_head(name: str, s: str, props: dict, catalog_names: set) -> list[str]:
    """Errori HARD che impediscono l'accettazione (retry con feedback)."""
    errs = []
    if not isinstance(s, str) or len(s) < 60:
        return [f"description troppo corta o non stringa (>=60 char)"]
    if "\n" in s:
        errs.append("contiene newline (deve essere stringa TOML monolinea)")
    pos = [(c, s.find(c)) for c in _CHAPTERS]
    missing = [c for c, p in pos if p < 0]
    if missing:
        errs.append(f"capitoli mancanti {missing} (attesi SCOPO/PATTERN/NON/OUT)")
    else:
        order = [p for _, p in pos]
        if order != sorted(order):
            errs.append("capitoli fuori ordine (SCOPO->PATTERN->NON->OUT)")
    # PATTERN usa solo arg dello schema (errore C_PATTERN_ARGS: l'LLM copierebbe un arg fantasma)
    allowed = set(props.keys()) | _UNIVERSAL_ARGS
    for a in _pattern_call_args(s, name):
        if a not in allowed:
            errs.append(f"il PATTERN usa l'arg '{a}' non nello schema {sorted(props.keys())}")
    # NON: cita solo tool ESISTENTI (riferimento morto -> confonde il planner).
    # Reuse euristica lint C_NON_REFS, ma qui HARD (retry corregge).
    ns = s.find("NON:")
    ne = s.find("OUT:", ns) if ns >= 0 else -1
    non_chap = s[ns:ne] if ns >= 0 and ne > ns else ""
    known_verbs = PRODUCER_VERBS | DESTRUCTIVE_VERBS
    for ref in re.findall(r"\b([a-z][a-z0-9]+_[a-z0-9_]+)\b", non_chap):
        if (ref != name and ref not in catalog_names
                and ref.split("_")[0] in known_verbs):
            errs.append(f"NON: cita '{ref}' inesistente nel catalogo (riferimento morto)")
    # coda implementativa sopravvissuta dopo OUT:
    oc = s.find("OUT:")
    tail = s[oc:] if oc >= 0 else ""
    smell = [w for w in _TAIL_SMELL if w in tail]
    if smell:
        errs.append(f"coda implementativa dopo OUT: {smell} -> va nel codice, togli")
    # length: hard solo se eccede MOLTO (ha tenuto la coda); altrimenti soft
    if len(s) > int(DESC_MAX * 1.7):
        errs.append(f"description {len(s)} char >> {DESC_MAX}: hai tenuto la coda, comprimi")
    return errs


def length_warn(s: str) -> str | None:
    oc = s.find("OUT:")
    head = s[:oc] if oc > 0 else s
    w = []
    if len(head) > HEAD_MAX:
        w.append(f"testa {len(head)}>{HEAD_MAX}")
    if len(s) > DESC_MAX:
        w.append(f"desc {len(s)}>{DESC_MAX}")
    return ", ".join(w) if w else None


# -------------------------------------------------------------------------
# LLM (middle tier, come synt stage-4)
# -------------------------------------------------------------------------
def _make_llm():
    import os
    from llm_router import LLMRouter
    tier = os.environ.get("METNOS_NORMALIZE_TIER", "middle").strip()
    prov = LLMRouter().provider(tier)

    # METNOS_NORMALIZE_THINK: "0"/"false" -> think=False; intero -> reasoning_budget.
    # Sweet spot empirico (Roberto, 8/6): un po' di thinking aiuta, molto affonda
    # (1024 bruciava il token cap senza emettere JSON). Default 256.
    import os
    # Default "0" (think=False): A/B 8/6 -> piu' affidabile (8/8 vs 7/8), il 256
    # ogni tanto tronca su prompt lunghi. Overridabile per esperimenti.
    _tb = os.environ.get("METNOS_NORMALIZE_THINK", "0").strip().lower()
    _think = _tb not in ("0", "false", "no")
    _budget = int(_tb) if _tb.isdigit() else 256

    def call(user: str, max_tokens=1500):
        kw = {"think": True, "reasoning_budget": _budget} if _think else {"think": False}
        r = prov.chat("", user, max_tokens=max_tokens, temperature=0.0, **kw)
        return r.text or ""
    return call


def _parse_json(text: str) -> dict | None:
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
    a, b = text.find("{"), text.rfind("}")
    if a >= 0 and b > a:
        text = text[a:b + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def normalize_one(name: str, cat: dict, llm, *, max_retry=5) -> dict:
    meta = cat[name]
    names = set(cat.keys()) | _builtin_tool_names()
    sib_block = _siblings_block(name, meta["object"], cat)
    # nomi dei tool validi che il NON: puo' citare (grounding anti-allucinazione)
    valid_refs = sorted({n for n, m in cat.items()
                         if m["object"] == meta["object"] and n != name})
    feedback = ""
    last = None
    for attempt in range(max_retry):
        prompt = prompt_loader.get(
            "synt_description_normalize", "it",
            name=name, verb=meta["verb"], object=meta["object"],
            args_block=_args_block(meta["props"]),
            out_shape_hint=_out_shape_hint(meta["verb"]),
            siblings_block=sib_block,
            verb_ortho=_verb_ortho(meta["verb"]),
            fmt_qualifier=_format_qualifier(name),
            affinity_terms=", ".join(meta["affinity"][:14]),
            source_it=meta["it"], source_en=meta["en"],
            head_max=HEAD_MAX, desc_max=DESC_MAX,
        )
        if feedback:
            prompt += (f"\n\nCORREGGI questi errori del tentativo precedente: {feedback}\n"
                       f"Nel NON: cita SOLO tool da questa lista (o nessuno): {valid_refs}\n")
        raw = llm(prompt)
        out = _parse_json(raw)
        if not out or "it" not in out or "en" not in out:
            feedback = "output non era JSON {it, en} valido"
            last = {"ok": False, "error": feedback, "raw": raw[:300]}
            continue
        e_it = validate_head(name, out["it"], meta["props"], names)
        e_en = validate_head(name, out["en"], meta["props"], names)
        if e_it or e_en:
            feedback = "; ".join(["IT: " + x for x in e_it] + ["EN: " + x for x in e_en])
            last = {"ok": False, "error": feedback, "it": out.get("it"), "en": out.get("en")}
            continue
        return {"ok": True, "name": name, "it": out["it"], "en": out["en"],
                "warn": length_warn(out["it"]) or length_warn(out["en"]),
                "attempts": attempt + 1}
    return last or {"ok": False, "error": "max_retry exhausted"}


# -------------------------------------------------------------------------
# Scrittura: description + lang_state + re-sign
# -------------------------------------------------------------------------
def _set_description_block(name: str, it: str, en: str):
    p = _EXEC / name / "manifest.toml"
    txt = p.read_text()
    m = re.search(r'^\[description\][ \t]*\n', txt, re.M)
    if not m:
        raise SystemExit(f"{name}: nessuna sezione [description]")
    nxt = re.search(r'^\[', txt[m.end():], re.M)
    end = m.end() + nxt.start() if nxt else len(txt)
    block = ("[description]\n"
             f"it = {json.dumps(it, ensure_ascii=False)}\n"
             f"en = {json.dumps(en, ensure_ascii=False)}\n\n")
    new = txt[:m.start()] + block + txt[end:]
    parsed = tomllib.loads(new)  # valida
    assert parsed["description"]["it"] == it and parsed["description"]["en"] == en
    p.write_text(new)


def _update_lang_state(name: str, it: str, en: str):
    from migrate_manifest_descriptions import _sha256_str
    sp = _EXEC / name / "manifest.lang_state.json"
    state = {}
    if sp.is_file():
        try:
            state = json.loads(sp.read_text())
        except Exception:
            state = {}
    state["description"] = {
        "it": {"version_hash": _sha256_str(it), "source_lang": None, "source_hash": None},
        "en": {"version_hash": _sha256_str(en), "source_lang": "it",
               "source_hash": _sha256_str(it)},
    }
    sp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")


def apply_one(name: str, it: str, en: str) -> str:
    _set_description_block(name, it, en)
    _update_lang_state(name, it, en)
    from sign import sign_executor
    sign_executor(_EXEC / name)
    return "applied+signed"


# -------------------------------------------------------------------------
# CLI
# -------------------------------------------------------------------------
def _legacy_names(cat: dict) -> list[str]:
    return sorted(n for n, m in cat.items() if not m["has_chapters"])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("names", nargs="*", help="executor da normalizzare")
    ap.add_argument("--list-legacy", action="store_true")
    ap.add_argument("--family", help="tutti i legacy con questo verbo")
    ap.add_argument("--apply", action="store_true", help="scrive+firma (default: preview)")
    args = ap.parse_args()

    cat = load_catalog()
    if args.list_legacy:
        leg = _legacy_names(cat)
        from collections import Counter
        fam = Counter(n.split("_")[0] for n in leg)
        print(f"LEGACY {len(leg)}  famiglie={dict(fam)}")
        for n in leg:
            print(" ", n)
        return 0

    targets = list(args.names)
    if args.family:
        targets += [n for n in _legacy_names(cat) if n.split("_")[0] == args.family]
    targets = sorted(set(targets))
    if not targets:
        print("nessun target (usa names, --family, o --list-legacy)")
        return 1

    llm = _make_llm()
    ok = fail = 0
    for n in targets:
        if n not in cat:
            print(f"!! {n}: non nel catalogo"); fail += 1; continue
        res = normalize_one(n, cat, llm)
        if not res.get("ok"):
            print(f"\n✗ {n}: {res.get('error')}")
            if res.get("it"):
                print(f"    IT: {res['it']}")
            fail += 1
            continue
        ok += 1
        w = f"  [warn {res['warn']}]" if res.get("warn") else ""
        print(f"\n✓ {n}  (tentativi {res['attempts']}){w}")
        print(f"    IT: {res['it']}")
        print(f"    EN: {res['en']}")
        if args.apply:
            print("   ", apply_one(n, res["it"], res["en"]))
    print(f"\n=== normalize: {ok} ok, {fail} fail su {len(targets)} ===")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
