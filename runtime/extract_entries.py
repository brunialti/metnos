"""runtime.extract_entries — builtin universale: TESTO → RECORD strutturati.

Terza categoria di executor (come describe_entries/classify_entries): vive nel
runtime, no manifest su disco, no subprocess. A differenza di classify (1:1,
etichetta) extract è 1:N: da OGNI testo sorgente estrae 0..N record tipizzati
secondo un set di `fields`. Universale §7.3 — risolve la classe «contenuto non
strutturato → entries strutturate» per QUALSIASI dominio:
  - web→eventi:   read_urls_html → extract_entries(fields=[summary,start,end]) → create_events
  - mail→tabella: read_messages  → extract_entries(fields=[mittente,oggetto,importo]) → *_spreadsheet
  - pdf→righe:    read_files_pdf → extract_entries(fields=[...]) → ...

Estensione di confine §2.2 (ratificata da Roberto 3/6): `extract` non è più
«solo archivi» ma «struttura embedded in un contenitore» (archivi + record da
testo). LLM tier `middle` di default (parsing+normalizzazione = compito medio).

Output: `entries` = lista PIATTA dei record estratti (può essere più lunga o più
corta della lista d'ingresso). I campi-data (`start/end/date/when/...`) sono
normalizzati a ISO 8601 con timezone quando possibile (così sono pronti per
create_events ecc.).
"""
from __future__ import annotations

import json
import re

from llm_helpers import call_llm
from logging_setup import get_logger

log = get_logger(__name__)

# Campi il cui valore va normalizzato a datetime ISO 8601 (euristica nome).
_DATE_FIELD_RE = re.compile(
    r"(^|_)(start|end|date|datetime|when|inizio|fine|data|ora|scadenza|due|"
    r"deadline|begin|finish)($|_)", re.IGNORECASE)

# Candidati campo-testo nelle entries d'ingresso, in ordine di preferenza.
_TEXT_FIELDS = ("body_text", "text", "content", "body", "description",
                "snippet", "summary", "title")

_MAX_INPUTS = 50          # cap sorgenti processate (cap superiore esplicito §2.1)
_MAX_TEXT_CHARS = 12000   # tronca ogni testo sorgente (budget prompt)
_DEFAULT_MAX_PER_TEXT = 20


def _pick_text(entry) -> str:
    """Estrae il testo da un'entry (dict) o lo usa direttamente (str)."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        for k in _TEXT_FIELDS:
            v = entry.get(k)
            if isinstance(v, str) and v.strip():
                return v
        # fallback: concatena i valori stringa
        parts = [str(v) for v in entry.values() if isinstance(v, str) and v.strip()]
        return "\n".join(parts)
    return ""


def _build_prompt(fields, instruction, max_per_text) -> str:
    has_date = any(_DATE_FIELD_RE.search(f) for f in fields)
    lines = [
        "Sei un estrattore di dati strutturati. Dato un TESTO, estrai i record "
        "richiesti e restituisci SOLO un array JSON, niente prosa, niente "
        "markdown, niente <think>.",
        f"Ogni record è un oggetto con ESATTAMENTE questi campi: {fields}.",
        "Se un campo non è deducibile dal testo, usa stringa vuota \"\".",
        f"Estrai TUTTI i record pertinenti presenti nel testo (non fermarti ai "
        f"primi), fino a un massimo di {max_per_text}. Se non c'è nulla di "
        "pertinente, restituisci [].",
    ]
    if instruction:
        lines.append(f"COSA estrarre: {instruction}")
    if has_date:
        lines.append(
            "I campi di data/ora DEVI normalizzarli in ISO 8601 con timezone "
            "(es. \"2026-03-15T09:00:00+01:00\"); se manca l'orario usa T00:00; "
            "se manca la timezone usa +01:00 (Europe/Rome).")
    lines.append("Output: SOLO l'array JSON. Esempio: "
                 "[{\"" + (fields[0] if fields else "campo") + "\": \"...\"}]")
    return "\n".join(lines)


def _coerce_records(data, fields) -> list:
    """Normalizza i vari shape che l'LLM puo' produrre in lista di dict-record:
    array diretto, wrapper `{"records":[...]}`/`{"events":[...]}`, o singolo
    oggetto. Recall §2.8: non perdere record per una forma inattesa."""
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        # wrapper {"<chiave>": [ {...}, ... ]}: prendi la prima lista di dict.
        for v in data.values():
            if isinstance(v, list) and any(isinstance(x, dict) for x in v):
                return [r for r in v if isinstance(r, dict)]
        # singolo record: tienilo se ha almeno un field richiesto.
        if any(f in data for f in fields):
            return [data]
    return []


def _salvage_objects(s: str) -> list:
    """Recupero tollerante: ogni oggetto `{…}` top-level BILANCIATO che parsa
    da solo. Cosi' un array TRONCATO dal token-cap (l'ultimo oggetto incompleto)
    non azzera TUTTI i record gia' completi (recall §2.8). String-aware: ignora
    parentesi dentro le stringhe JSON."""
    out: list = []
    depth = 0
    start = None
    in_str = False
    esc = False
    for i, ch in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    obj = json.loads(s[start:i + 1])
                    if isinstance(obj, dict):
                        out.append(obj)
                except json.JSONDecodeError:
                    pass
                start = None
    return out


def _parse_records(raw: str, fields: list) -> list:
    """Estrae i record dict dall'output LLM, tollerante a fence/prosa/troncamento
    e a shape alternativi (array, wrapper, singolo oggetto). Un array tagliato
    dal token-cap NON azzera i record gia' completi (recall §2.8)."""
    if not raw:
        return []
    s = raw.strip()
    # togli eventuali fence ```json ... ```
    s = re.sub(r"^```[a-zA-Z]*", "", s).strip()
    s = re.sub(r"```$", "", s).strip()
    # 1) JSON intero ben formato (lista, wrapper, o singolo record).
    try:
        recs = _coerce_records(json.loads(s), fields)
        if recs:
            return recs
    except json.JSONDecodeError:
        pass
    # 2) Array embedded in prosa.
    m = re.search(r"\[[\s\S]*\]", s)
    if m:
        try:
            recs = _coerce_records(json.loads(m.group(0)), fields)
            if recs:
                return recs
        except json.JSONDecodeError:
            pass
    # 3) Recupero tollerante (troncamento/JSON malformato): oggetti top-level
    #    che parsano e contengono ≥1 field richiesto (esclude wrapper spuri).
    return [o for o in _salvage_objects(s) if any(f in o for f in fields)]


def _extract_max_tokens(max_per_text: int) -> int:
    """Budget output scalato col numero di record attesi (~120 tok/record +
    margine). Cap a 8192 per evitare runaway. Evita il troncamento a monte
    (causa #1 di recall=0: array tagliato a meta'), il parser tollerante e' la
    rete di sicurezza a valle."""
    return max(1200, min(8192, 512 + int(max_per_text) * 120))


def handle_extract_entries(args, *, verbose: bool = False) -> dict:
    a = args or {}
    entries = a.get("entries")
    if entries is None and isinstance(a.get("texts"), list):
        entries = a["texts"]
    if not isinstance(entries, list):
        return {"ok": False,
                "error": "missing or invalid 'entries' (must be a list); "
                         "passa from_step=N del producer di testo",
                "error_class": "invalid_args", "entries": []}

    fields = a.get("fields")
    if isinstance(fields, str):
        fields = [f.strip() for f in fields.split(",") if f.strip()]
    if not (isinstance(fields, list) and fields
            and all(isinstance(f, str) for f in fields)):
        return {"ok": False,
                "error": "missing 'fields' (list[str]): i campi di ogni record "
                         "da estrarre, es. fields=[\"summary\",\"start\",\"end\"]",
                "error_class": "invalid_args", "entries": []}

    instruction = a.get("instruction") or a.get("what") or ""
    max_per_text = int(a.get("max_per_text") or _DEFAULT_MAX_PER_TEXT)
    max_total = int(a.get("max_total") or 0)  # 0 = no limit (§2.1 placeholder)
    tier = a.get("tier") or "middle"

    sources = entries[:_MAX_INPUTS]
    truncated_inputs = len(entries) > _MAX_INPUTS
    prompt = _build_prompt(fields, instruction, max_per_text)

    mt = _extract_max_tokens(max_per_text)
    out: list = []
    in_tok = out_tok = lat = 0
    failed = 0
    out_truncated = 0  # sorgenti il cui output ha (probabilmente) toccato il cap
    for entry in sources:
        text = _pick_text(entry)[:_MAX_TEXT_CHARS]
        if not text.strip():
            continue
        try:
            raw, meta = call_llm(text, prompt, tier=tier,
                                 max_tokens=mt, think=False)
            in_tok += int(meta.get("in_tokens") or 0)
            _ot = int(meta.get("out_tokens") or 0)
            out_tok += _ot
            lat += int(meta.get("latency_ms") or 0)
            if _ot >= mt - 16:  # euristica: output tagliato dal token-cap
                out_truncated += 1
        except Exception as ex:
            failed += 1
            log.warning("extract_entries: LLM call failed: %r", ex)
            continue
        for rec in _parse_records(raw, fields)[:max_per_text]:
            # normalizza: tieni solo i fields richiesti, riempi i mancanti.
            norm = {f: rec.get(f, "") for f in fields}
            out.append(norm)
            if max_total and len(out) >= max_total:
                break
        if max_total and len(out) >= max_total:
            break

    res = {
        "ok": True,
        "entries": out,
        "used": len(out),
        "available_total": len(out),
        "n_sources": len(sources),
        "fields": fields,
        "in_tokens": in_tok, "out_tokens": out_tok, "latency_ms": lat,
    }
    if failed:
        res["failed_sources"] = failed
    if out_truncated:
        # §2.7 visibility: l'output LLM ha toccato il token-cap su ≥1 sorgente →
        # qualche record oltre il cap puo' mancare (il parser tollerante ha
        # salvato i completi). Alza max_per_text o spezza la sorgente.
        res["output_truncated_sources"] = out_truncated
        res["cap_field"] = "max_per_text"
        res["cap_value"] = max_per_text
    if truncated_inputs:
        # §2.7 visibility
        res["truncated"] = True
        res["truncated_what"] = "input_sources"
        res["available_input_total"] = len(entries)
        res["cap_field"] = "n_sources"
        res["cap_value"] = _MAX_INPUTS
    return res


EXTRACT_ENTRIES_TOOL = {
    "type": "function",
    "function": {
        "name": "extract_entries",
        "description": (
            "SCOPO: estrae RECORD strutturati da entries di TESTO non "
            "strutturato (pagine web, mail, pdf), una o più per sorgente. "
            "PATTERN: producer-di-testo allo step N (read_urls_html, "
            "read_messages, read_files_pdf) poi extract_entries("
            "from_step=N, fields=[\"summary\",\"start\",\"end\"], "
            "instruction=\"conferenze con data\"). NON: usare per ETICHETTARE "
            "(classify_entries) né per filtrare campi di entries GIÀ "
            "strutturate (get/filter_entries); non è per archivi (extract_files). "
            "I campi data/ora escono in ISO 8601. OUT: entries=[{fields...}] "
            "lista piatta dei record, pipeable verso create_events/"
            "*_spreadsheet/send."
        ),
        "parameters": {
            "type": "object",
            "required": ["from_step", "fields"],
            "properties": {
                "from_step": {
                    "type": "integer",
                    "description": "Numero dello step (in questo turno) che ha "
                                   "prodotto le entries di testo da cui estrarre "
                                   "(es. read_urls_html al passo 2 → from_step=2).",
                    "minimum": 1,
                },
                "fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Campi di OGNI record da estrarre. Es. eventi: "
                                   "[\"summary\",\"start\",\"end\"]; spesa: "
                                   "[\"data\",\"descrizione\",\"importo\"].",
                },
                "instruction": {
                    "type": "string",
                    "description": "Cosa estrarre, in linguaggio naturale. Es. "
                                   "\"le prossime conferenze con la loro data\".",
                },
                "max_per_text": {
                    "type": "integer",
                    "description": "Max record per singola sorgente (default 20).",
                },
                "max_total": {
                    "type": "integer",
                    "description": "Cap totale record (0 = nessun limite).",
                },
            },
        },
    },
}
