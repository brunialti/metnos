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
import threading
import unicodedata
from email.utils import parsedate_to_datetime

from executor_workers import assigned_workers, map_ordered
from llm_helpers import call_llm
from logging_setup import get_logger
from messages import get as _msg
from agentic_executor import (
    AgenticContext, AgenticLimits, AgenticProposal, run_bounded_sync,
)

log = get_logger(__name__)

# Normalizzazione campi temporali (euristica nome), SPLIT per granularità:
# - DATETIME: punto nel tempo → ISO 8601 con orario+tz (eventi: create_events).
# - DATE-ONLY: una data → "YYYY-MM-DD" SENZA orario (fatture/scadenze: «data,
#   non anche tempo» — Roberto 16/6). T00:00 spurio su una pura data è rumore.
_DATETIME_FIELD_RE = re.compile(
    r"(^|_)(start|end|datetime|when|inizio|fine|ora|begin|finish)($|_)",
    re.IGNORECASE)
_DATE_ONLY_FIELD_RE = re.compile(
    r"(^|_)(date|data|scadenza|due|deadline|emiss|issue|invoice)($|_)",
    re.IGNORECASE)

# Candidati campo-testo nelle entries d'ingresso, in ordine di preferenza.
_TEXT_FIELDS = ("body_text", "text", "content", "body", "description",
                "snippet", "summary", "title")

_MAX_INPUTS = 50          # cap sorgenti processate (cap superiore esplicito §2.1)
_MAX_SOURCES_LIMIT = 1000
_MAX_TEXT_CHARS = 12000   # tronca ogni testo sorgente (budget prompt)
_DEFAULT_MAX_PER_TEXT = 20
_MAX_BATCH_SIZE = 16
_BATCH_QUERY_CHARS = 48000
_MAX_INFERRED_FIELDS = 8
_FIELD_INFERENCE_TEXT_CHARS = 8000

_ORIGIN_FIELDS = frozenset({
    "origin", "source", "origine", "origine_file", "source_file", "file_path",
    "percorso", "path",
})
_HASH_FIELDS = frozenset({
    "hash", "content_hash", "content_sha256", "signature", "firma",
    "firma_contenuto",
})
_READABLE_FIELDS = frozenset({"readable", "leggibile", "file_leggibile"})
_FILE_TYPE_FIELDS = frozenset({"file_type", "tipo_file", "formato"})
_CONFIDENCE_FIELDS = frozenset({
    "confidence", "confidence_level", "livello_confidenza", "confidenza",
})
_DOMAIN_FIELDS = frozenset({"domain", "dominio", "source_domain"})
_DUPLICATE_FIELDS = frozenset({
    "duplicates", "duplicate_paths", "duplicati", "percorsi_duplicati",
})
_DIAGNOSTIC_FIELDS = frozenset({
    "diagnostic", "parse_diagnostic", "diagnostica", "errore_lettura",
})


def _source_domain(source: dict) -> str:
    if (source.get("message_id") is not None
            or (source.get("account") is not None
                and source.get("subject") is not None)):
        return "email"
    if (source.get("_calendar_id") is not None
            or (source.get("id") is not None
                and (source.get("start") is not None
                     or source.get("end") is not None))):
        return "calendar"
    if (isinstance(source.get("emails"), list)
            or isinstance(source.get("phones"), list)):
        return "contacts"
    if (source.get("path") is not None
            and (source.get("file_type") is not None
                 or source.get("readable") is not None
                 or source.get("content") is not None)):
        return "files"
    return ""


def _source_origin(source: dict) -> str:
    direct = (source.get("path") or source.get("url")
              or source.get("htmlLink"))
    if direct:
        return str(direct)
    domain = _source_domain(source)
    if domain == "email":
        account = str(source.get("account") or "mail")
        identifier = (source.get("message_id") or source.get("uid")
                      or source.get("subject") or "message")
        return f"email:{account}:{identifier}"
    if domain == "calendar":
        calendar = str(source.get("_calendar_id") or "primary")
        identifier = source.get("id") or source.get("summary") or "event"
        return f"calendar:{calendar}:{identifier}"
    if domain == "contacts":
        identifier = source.get("id") or source.get("name") or "contact"
        return f"contact:{identifier}"
    return str(source.get("name") or "")


def _source_date(value):
    """Normalize RFC mail dates while preserving already-ISO literals."""
    if not isinstance(value, str) or not value.strip():
        return value
    raw = value.strip()
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError, OverflowError):
        return raw
    return parsed.isoformat() if parsed is not None else raw


def _source_field_value(source: dict, field: str):
    """Deterministic aliases for facts already structured by producers."""
    key = _field_key(field)
    # For a calendar appointment the actionable date is its start. ``end``
    # may be days later and is exclusive for all-day Google events, so letting
    # the model choose it creates false deadlines.
    if (_source_domain(source) == "calendar"
            and key in {"scadenza", "deadline", "due_date"}):
        start = source.get("start")
        if isinstance(start, str) and start.strip():
            value = start.strip()
            return value[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", value) else value
    aliases = {
        "sender": ("from",), "mittente": ("from",),
        "from": ("from",), "subject": ("subject",),
        "oggetto": ("subject",), "body": ("body_preview", "body"),
        "corpo": ("body_preview", "body"),
        "summary": ("summary",), "titolo": ("summary", "subject"),
        "start": ("start",), "inizio": ("start",),
        "end": ("end",), "fine": ("end",),
        "location": ("location",), "luogo": ("location",),
        "description": ("description",), "descrizione": ("description",),
        "status": ("status",), "stato": ("status",),
        "attendees": ("attendees",), "partecipanti": ("attendees",),
        "responsabile": ("organizer", "attendees", "from"),
        "responsible": ("organizer", "attendees", "from"),
        "date": ("date",), "data": ("date",),
    }
    if key in _ORIGIN_FIELDS:
        return _source_origin(source)
    if key in _DOMAIN_FIELDS:
        return _source_domain(source)
    if key in _DUPLICATE_FIELDS:
        return source.get("duplicate_paths") or []
    if key in _DIAGNOSTIC_FIELDS:
        return source.get("parse_diagnostic") or ""
    for source_key in aliases.get(key, (field,)):
        value = source.get(source_key)
        if value not in (None, "", []):
            return _source_date(value) if key in {"date", "data"} else value
    return None


def _field_key(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value).casefold())
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "_", value).strip("_")


def _attach_source_provenance(record: dict, source: dict,
                              fields: list[str]) -> dict:
    """Preserva provenienza e riempie i campi tecnici non inferibili dal LLM."""
    out = dict(record)
    path = source.get("path")
    name = source.get("name") or source.get("subject") or source.get("summary")
    raw_hash = source.get("sha256") or source.get("signature")
    readable = source.get("readable")
    file_type = source.get("file_type")
    # Producer-owned structured facts win over LLM copies.  This both improves
    # fidelity (message sender/date, calendar start/end) and lets the model
    # spend its budget on genuinely semantic fields.
    for field in fields:
        value = _source_field_value(source, field)
        if value not in (None, "", []):
            out[field] = value
    # Canonical fact triples remain complete even when the model emits only
    # the entity or only its normalized/original spelling.  This is a lossless
    # fallback (copy of an observed value), not an inferred business fact.
    keyed_fields = {_field_key(field): field for field in fields}
    entity_field = next((keyed_fields[key] for key in ("entita", "entity")
                         if key in keyed_fields), None)
    normalized_field = next((keyed_fields[key] for key in (
        "valore_normalizzato", "normalized_value") if key in keyed_fields), None)
    original_field = next((keyed_fields[key] for key in (
        "valore_originale", "original_value") if key in keyed_fields), None)
    entity_value = out.get(entity_field) if entity_field else ""
    normalized_value = out.get(normalized_field) if normalized_field else ""
    original_value = out.get(original_field) if original_field else ""
    if normalized_field and normalized_value in (None, "", []):
        out[normalized_field] = original_value or entity_value or ""
        normalized_value = out[normalized_field]
    if original_field and original_value in (None, "", []):
        out[original_field] = normalized_value or entity_value or ""
    normalized_value = out.get(normalized_field) if normalized_field else ""
    original_value = out.get(original_field) if original_field else ""
    deadline_field = next((keyed_fields[key] for key in (
        "scadenza", "deadline", "due_date") if key in keyed_fields), None)
    deadline_value = out.get(deadline_field) if deadline_field else ""
    # A copied entity label is not a useful value when the same record carries
    # a normalized deadline. Prefer the typed fact instead of propagating an
    # LLM fallback such as Standup -> Standup.
    if deadline_value not in (None, "", []) and entity_value not in (None, "", []):
        if (normalized_field and isinstance(normalized_value, str)
                and normalized_value.casefold() == str(entity_value).casefold()):
            out[normalized_field] = deadline_value
        if (original_field and isinstance(original_value, str)
                and original_value.casefold() == str(entity_value).casefold()):
            out[original_field] = deadline_value
    # Calendar values are producer-owned occurrence facts: the normalized
    # value is the local start date and the original value is the exact start.
    if _source_domain(source) == "calendar" and deadline_field:
        start = source.get("start")
        if isinstance(start, str) and start.strip():
            if normalized_field:
                out[normalized_field] = start.strip()[:10]
            if original_field:
                out[original_field] = start.strip()
    substantive = [field for field in fields
                   if _field_key(field) not in (
                       _ORIGIN_FIELDS | _HASH_FIELDS | _READABLE_FIELDS
                       | _FILE_TYPE_FIELDS | _CONFIDENCE_FIELDS
                       | _DOMAIN_FIELDS | _DUPLICATE_FIELDS
                       | _DIAGNOSTIC_FIELDS)]
    completeness = (sum(1 for field in substantive
                        if out.get(field) not in (None, "", []))
                    / max(1, len(substantive)))
    for field in fields:
        key = _field_key(field)
        if key in _ORIGIN_FIELDS:
            out[field] = _source_origin(source)
        elif key in _HASH_FIELDS:
            out[field] = raw_hash or ""
        elif key in _READABLE_FIELDS and readable is not None:
            out[field] = bool(readable)
        elif key in _FILE_TYPE_FIELDS:
            out[field] = file_type or ""
        elif key in _CONFIDENCE_FIELDS:
            out[field] = (0.10 if readable is False else
                          0.95 if completeness >= 0.5 else 0.60)
        elif key in _DOMAIN_FIELDS:
            out[field] = _source_domain(source)
    private = {
        "_source_path": path,
        "_source_name": name,
        "_source_mtime": source.get("mtime") or source.get("mtime_epoch"),
        "_source_sha256": raw_hash,
        "_source_content_sha256": source.get("content_sha256"),
        "_source_readable": readable,
        "_source_file_type": file_type,
        "_source_domain": _source_domain(source),
        "_duplicate_paths": source.get("duplicate_paths"),
        "_parse_diagnostic": source.get("parse_diagnostic"),
    }
    out.update({key: value for key, value in private.items()
                if value not in (None, "", [])})
    return out


def _render_labeled_fields(entry: dict, keys: tuple[str, ...]) -> str:
    parts = []
    for key in keys:
        value = entry.get(key)
        if value in (None, "", []):
            continue
        rendered = (json.dumps(value, ensure_ascii=False)
                    if isinstance(value, (dict, list, tuple)) else str(value))
        parts.append(f"{key}: {rendered}")
    return "\n".join(parts)


def _pick_text(entry) -> str:
    """Estrae il testo da un'entry (dict/str/riga) per il ramo LLM."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        # Structured message/event producers already expose valuable facts as
        # separate fields.  Returning only ``summary`` discarded calendar
        # start/end/location; unlabeled concatenation made mail dates and
        # senders ambiguous.  Preserve labels for those two producer families.
        domain = _source_domain(entry)
        if domain:
            keys = (("from", "subject", "date", "body_preview", "account",
                     "folder", "uid", "message_id") if domain == "email" else
                    ("summary", "start", "end", "location", "description",
                     "status", "attendees", "id"))
            rendered = _render_labeled_fields(entry, keys)
            if rendered:
                return rendered
        for k in _TEXT_FIELDS:
            v = entry.get(k)
            if isinstance(v, str) and v.strip():
                return v
        # fallback: concatena i valori stringa
        parts = [str(v) for v in entry.values() if isinstance(v, str) and v.strip()]
        return "\n".join(parts)
    # RIGA di uno spreadsheet (list[list] da read_spreadsheet §2.6): unisci le
    # celle a una linea di testo cosi' l'LLM puo' estrarne i campi (fogli
    # headerless). Deterministico > LLM resta preferito via la proiezione.
    if isinstance(entry, (list, tuple)):
        return " ".join(str(v) for v in entry if v not in (None, ""))
    return ""


def _pick_model_text(entry) -> str:
    """Return semantic source text without opaque transport identifiers.

    Account, folder, UID, message-id and calendar-id remain on the source and
    are attached deterministically to provenance/output.  Sending them to the
    LLM only consumes context and can make extraction depend on identifiers
    with no business meaning.
    """
    if not isinstance(entry, dict):
        return _pick_text(entry)
    domain = _source_domain(entry)
    if domain == "email":
        return _render_labeled_fields(
            entry, ("from", "subject", "date", "body_preview"))
    if domain == "calendar":
        return _render_labeled_fields(
            entry, ("summary", "start", "end", "location", "description",
                    "status", "attendees"))
    return _pick_text(entry)


_RELEVANCE_DEFAULT_FIELDS = frozenset({
    "entita", "entity", "persona", "person", "progetto", "project",
    "organizzazione", "organization", "email", "telefono", "phone",
})
_RELEVANCE_GENERIC_TOKENS = frozenset({
    "documenti", "documents", "document", "progetto", "project",
    "programma", "program", "cartella", "folder", "report", "visita",
    "visit", "policlinico", "hospital",
})


def _match_text(value) -> str:
    """Forma Unicode/case/punctuation-insensitive per il prefilter."""
    normalized = unicodedata.normalize("NFKD", str(value or "").casefold())
    normalized = "".join(
        char for char in normalized if not unicodedata.combining(char))
    return " ".join(re.findall(r"[a-z0-9@.+_-]+", normalized))


def _relevance_terms(explicit, reference_entries, reference_fields) -> list[str]:
    """Deriva ancore ad alta precisione da record già osservati.

    Le frasi vengono mantenute intere.  Per persone usiamo anche il cognome,
    per scope/progetti i token non generici: evita che iniziali o nomi propri
    troppo deboli (es. ``Roberto B.``) selezionino intere mailbox.
    """
    terms: list[str] = []
    seen: set[str] = set()

    def add(value, *, allow_short: bool = False) -> None:
        term = _match_text(value)
        if (len(term) < (2 if allow_short else 4) or term in seen):
            return
        if re.fullmatch(r"[\d\s.,:+-]+", term):
            return
        seen.add(term)
        terms.append(term)

    raw_explicit = explicit if isinstance(explicit, list) else []
    for value in raw_explicit:
        if not isinstance(value, str):
            continue
        # A short all-caps token explicitly written by the user is normally
        # an acronym (for example TSA), not a weak inferred name fragment.
        # Keep it with word-boundary matching; short values derived from
        # records remain rejected by the stricter default below.
        explicit_acronym = bool(re.fullmatch(
            r"[A-ZÀ-ÖØ-Þ0-9][A-ZÀ-ÖØ-Þ0-9._+-]{1,4}", value.strip()))
        add(value, allow_short=explicit_acronym)
        for token in _match_text(value).split():
            if len(token) >= 4 and token not in _RELEVANCE_GENERIC_TOKENS:
                add(token)

    wanted = ({_field_key(field) for field in reference_fields}
              if isinstance(reference_fields, list) and reference_fields
              else _RELEVANCE_DEFAULT_FIELDS)
    refs = reference_entries if isinstance(reference_entries, list) else []
    for record in refs:
        if not isinstance(record, dict):
            continue
        record_type = _match_text(
            record.get("tipo") or record.get("type") or "")
        for field, value in record.items():
            field_key = _field_key(field)
            if field_key not in wanted or field_key in {"tipo", "type", "kind"}:
                continue
            values = value if isinstance(value, list) else [value]
            for observed in values:
                if not isinstance(observed, (str, int, float)):
                    continue
                normalized = _match_text(observed)
                tokens = re.findall(r"[a-z0-9]+", normalized)
                if field_key in {"email", "telefono", "phone"}:
                    add(observed)
                elif record_type in {
                        "persona", "person", "contact", "contatto"}:
                    # Una frase con iniziali è troppo debole come match.
                    if (len(tokens) <= 1
                            or all(len(token) >= 2 for token in tokens)):
                        add(observed)
                    if tokens and len(tokens[-1]) >= 4:
                        add(tokens[-1])
                elif record_type in {
                        "organizzazione", "organization", "azienda",
                        "company", "fornitore", "supplier"}:
                    add(observed)
                elif field_key in {
                        "progetto", "project", "organizzazione",
                        "organization"}:
                    add(observed)
    # Bounded anche quando l'upstream contiene migliaia di record.
    return terms[:256]


def _matches_relevance(text, term: str) -> bool:
    """Match di parola/frase, non substring interna (``neri`` ≠ pionieri)."""
    normalized = _match_text(text)
    return f" {term} " in f" {normalized} "


_CLOCK_MENTION_RE = re.compile(
    r"(?<!\d)([01]?\d|2[0-3])[:.]([0-5]\d)(?!\d)")


def _clock_mentions(text: str) -> list[str]:
    """Return stable HH:MM facts observed in one record/source.

    The values are private reconciliation evidence, never public extracted
    facts.  Keeping secondary mentions matters for sources such as an event
    whose description says "13:30, formally 17:10": a peer source can be
    linked through 17:10 while the public 13:30 value still surfaces as the
    conflict.  No date/time interpretation is attempted here.
    """
    values: list[str] = []
    seen: set[str] = set()
    for hour, minute in _CLOCK_MENTION_RE.findall(str(text or "")):
        value = f"{int(hour):02d}:{minute}"
        if value not in seen:
            seen.add(value)
            values.append(value)
    return values[:32]


def _runtime_evidence_text(record: dict, source: dict | None,
                           source_text: str, record_count: int) -> str:
    """Bound evidence to the record when a container has many records.

    Mail digests can mention several unrelated appointments.  Copying every
    source-level anchor/time to every extracted row would create false joins.
    Full source evidence is therefore admitted only for a 1:1 extraction;
    multi-record sources use the extracted observed values plus their title.
    """
    parts = []
    for key, value in record.items():
        if key.startswith("_") or value in (None, "", []):
            continue
        if isinstance(value, (dict, list, tuple)):
            rendered = json.dumps(value, ensure_ascii=False)
        else:
            rendered = str(value)
        parts.append(f"{key}: {rendered}")
    if record_count == 1 and isinstance(source, dict):
        name = source.get("name") or source.get("subject") or source.get("summary")
        if name not in (None, "", []):
            parts.append(f"source_name: {name}")
    if record_count == 1 and source_text.strip():
        parts.append(source_text)
    return "\n".join(parts)


def _attach_runtime_evidence(record: dict, source: dict | None,
                             source_text: str, record_count: int,
                             relevance_terms: list[str],
                             state_markers: dict[str, list[str]]) -> dict:
    """Attach deterministic, private evidence and recover explicit states.

    Relevance anchors and clock mentions are runtime-owned provenance used by
    an opt-in reconciler.  ``state_markers`` is also opt-in: it fills an empty
    requested state/status field only when a caller-declared phrase is
    literally observed.  It never overwrites producer or model facts.
    """
    out = dict(record)
    evidence = _runtime_evidence_text(
        out, source, source_text, max(1, int(record_count or 1)))
    anchors = [term for term in relevance_terms
               if _matches_relevance(evidence, term)]
    if anchors:
        out["_relevance_anchors"] = anchors
    times = _clock_mentions(evidence)
    if times:
        out["_source_time_mentions"] = times

    state_fields = [
        field for field in out
        if _field_key(field) in {"stato", "status", "state"}
    ]
    if state_fields and all(out.get(field) in (None, "", [])
                            for field in state_fields):
        for normalized_state, markers in state_markers.items():
            if any(_matches_relevance(evidence, marker) for marker in markers):
                out[state_fields[0]] = normalized_state
                break
    return out


def _looks_like_header_row(row) -> bool:
    """Riga-0 di uno spreadsheet = HEADER se ogni cella e' un'ETICHETTA non
    vuota e NON puramente numerica/data. Deterministico §7.9 (niente LLM)."""
    if not (isinstance(row, (list, tuple)) and row):
        return False
    cells = [str(c).strip() for c in row]
    if not all(cells):
        return False
    return not any(re.fullmatch(r"[\d.,/:\-\s]+", c) for c in cells)


def _rows_to_records(entries: list) -> list:
    """list[list] (righe di read_spreadsheet §2.6) → list[dict] SE la riga-0 e'
    un header di etichette: le colonne diventano chiavi → il ramo di PROIEZIONE
    deterministica (o l'LLM su chiavi reali) vede i `fields` richiesti. Fogli
    HEADERLESS → invariati (il ramo LLM unisce le celle via `_pick_text`).

    Chiave §7.9: la conversione vive nel CONSUMER (extract_entries), NON in
    read_spreadsheet — cosi' l'output di read_spreadsheet (list[list]) resta
    invariato per describe/filter/matcher (nessun ripple). No-op se `entries` non
    e' uniformemente righe."""
    rows = [e for e in entries if isinstance(e, (list, tuple))]
    if not rows or len(rows) != len(entries):
        return entries                      # non uniformemente righe → invariato
    if not _looks_like_header_row(rows[0]):
        return entries                      # headerless → ramo LLM su celle unite
    cols = [str(c).strip() for c in rows[0]]
    return [dict(zip(cols, r)) for r in rows[1:]]


def _build_prompt(fields, instruction, max_per_text) -> str:
    date_time = [f for f in fields if _DATETIME_FIELD_RE.search(f)]
    date_only = [f for f in fields
                 if _DATE_ONLY_FIELD_RE.search(f) and f not in date_time]
    import i18n
    import prompt_loader
    return prompt_loader.get(
        "extract_entries", i18n.current_lang(),
        fields_json=json.dumps(fields, ensure_ascii=False),
        instruction=instruction,
        max_per_text=max_per_text,
        datetime_fields=", ".join(date_time),
        date_only_fields=", ".join(date_only),
        example_field=(fields[0] if fields else "field"),
    )


def _build_batch_prompt(fields, instruction, max_per_text) -> str:
    """Prompt whose input/output contract is explicitly multi-source.

    Reusing the scalar prompt made the local model interpret the JSON bundle
    as one opaque text and return ``[]``.  Keep this contract separate: the
    model sees the carrier field as structural, while callers still receive
    exactly the public ``fields`` schema after parsing.
    """
    date_time = [field for field in fields
                 if _DATETIME_FIELD_RE.search(field)]
    date_only = [field for field in fields
                 if (_DATE_ONLY_FIELD_RE.search(field)
                     and field not in date_time)]
    import i18n
    import prompt_loader
    return prompt_loader.get(
        "extract_entries_batch", i18n.current_lang(),
        fields_json=json.dumps(["source_index", *fields],
                               ensure_ascii=False),
        example_row=json.dumps([0, *(["..."] * len(fields))],
                               ensure_ascii=False),
        field_count=len(fields) + 1,
        instruction=instruction,
        max_per_text=max_per_text,
        datetime_fields=", ".join(date_time),
        date_only_fields=", ".join(date_only),
    )


def _normalize_inferred_field(value: str) -> str:
    """Normalizza una label proposta dal modello in una chiave JSON stabile."""
    value = unicodedata.normalize("NFKD", value.strip().casefold())
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^\w]+", "_", value, flags=re.UNICODE).strip("_")
    value = re.sub(r"^\d+_?", "", value)
    return value[:48].rstrip("_")


def _parse_inferred_fields(raw: str) -> list[str]:
    """Parsa il piccolo contratto JSON dell'inferenza schema.

    Accetta l'array diretto o ``{"fields": [...]}``; fence/prosa sono tollerati
    come negli altri parser LLM, ma le chiavi sono normalizzate, deduplicate e
    limitate. Nessuna conoscenza di dominio entra in questo confine.
    """
    if not raw:
        return []
    text = raw.strip()
    text = re.sub(r"^```[a-zA-Z]*", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    data = None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\[[^\[\]]*\]", text)
        if match:
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                data = None
    if isinstance(data, dict):
        data = data.get("fields")
    if not isinstance(data, list):
        return []
    out: list[str] = []
    for item in data:
        if not isinstance(item, str):
            continue
        field = _normalize_inferred_field(item)
        if field and field not in out:
            out.append(field)
        if len(out) >= _MAX_INFERRED_FIELDS:
            break
    return out


def _build_field_inference_prompt(instruction: str) -> str:
    import i18n
    import prompt_loader
    return prompt_loader.get(
        "extract_entries_schema", i18n.current_lang(),
        instruction=instruction,
        max_fields=_MAX_INFERRED_FIELDS,
    )


def _field_inference_sample(entries: list) -> str:
    chunks: list[str] = []
    used = 0
    for entry in entries[:_MAX_INPUTS]:
        text = _pick_model_text(entry).strip()
        if not text:
            continue
        remaining = _FIELD_INFERENCE_TEXT_CHARS - used
        if remaining <= 0:
            break
        piece = text[:remaining]
        chunks.append(piece)
        used += len(piece)
    return "\n\n".join(chunks)


def _infer_fields(entries: list, instruction: str, tier: str) -> tuple[list[str], dict]:
    """Inferisce una sola volta lo schema condiviso da tutte le sorgenti."""
    sample = _field_inference_sample(entries)
    context = AgenticContext(
        goal={"operation": "infer_record_schema"},
        observed={"sample": sample},
        constraints={"max_fields": _MAX_INFERRED_FIELDS},
    )

    def propose(_ctx):
        raw, meta = call_llm(
            sample, _build_field_inference_prompt(instruction),
            tier=tier, max_tokens=256, think=False,
        )
        fields = _parse_inferred_fields(raw)
        return AgenticProposal(fields, evidence=meta) if fields else None

    def execute(proposal, _ctx):
        return list(proposal.action), dict(proposal.evidence or {})

    outcome = run_bounded_sync(
        context=context, propose=propose, execute=execute,
        validate=lambda proposal, _ctx: (
            isinstance(proposal.action, list)
            and 0 < len(proposal.action) <= _MAX_INFERRED_FIELDS
            and all(isinstance(field, str) and field
                    for field in proposal.action)),
        limits=AgenticLimits(max_attempts=1),
        postcondition=lambda result, _ctx: bool(result and result[0]),
    )
    return outcome.result if outcome.status == "completed" else ([], {})


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


def _source_batches(items: list[tuple[int, dict, str]], batch_size: int):
    """Yield stable, character-bounded batches without splitting a source."""
    current: list[tuple[int, dict, str]] = []
    current_chars = 0
    for item in items:
        text_chars = len(item[2])
        if current and (len(current) >= batch_size
                        or current_chars + text_chars > _BATCH_QUERY_CHARS):
            yield current
            current = []
            current_chars = 0
        current.append(item)
        current_chars += text_chars
    if current:
        yield current


_AUDIT_FIELD_ALIASES = {
    "fornitore": ("fornitore", "supplier", "vendor"),
    "supplier": ("fornitore", "supplier", "vendor"),
    "vendor": ("fornitore", "supplier", "vendor"),
    "stato": ("stato", "status", "state"),
    "status": ("stato", "status", "state"),
    "state": ("stato", "status", "state"),
}


def _extract_labeled_audit_values(text: str, audit_fields: list[str]) -> dict:
    """Extract source-level ``Label [qualifier]: value`` facts.

    This pass is deterministic and cardinality-neutral.  Qualifiers allow
    forms such as ``Fornitore alternativo proposto: Vega`` while the closed
    bilingual aliases keep unrelated prose from becoming an audit fact.
    """
    lines = str(text or "").splitlines()
    out: dict[str, str] = {}
    for field in audit_fields:
        key = _field_key(field)
        aliases = _AUDIT_FIELD_ALIASES.get(key, (key.replace("_", " "),))
        label = "|".join(re.escape(alias) for alias in aliases if alias)
        if not label:
            continue
        pattern = re.compile(
            rf"^\s*(?:{label})(?:\s+[^:\r\n]{{0,48}})?\s*:\s*(.+?)\s*$",
            re.IGNORECASE,
        )
        values = []
        for line in lines:
            match = pattern.match(line)
            if not match:
                continue
            value = match.group(1).strip().rstrip(".;")
            if value and value.casefold() not in {
                    existing.casefold() for existing in values}:
                values.append(value)
        if values:
            out[field] = ", ".join(values)
    return out


# ── Drill-down: segui i link se i campi richiesti non sono nel testo ─────────
_URL_RE = re.compile(r'https?://[^\s"\'<>)]+')
_DRILL_TEXT_CHARS = 16000   # budget testo pagina drillata (oltre _MAX_TEXT_CHARS)


def _entry_links(entry) -> list:
    """URL http(s) candidati per il drill: campo `links` dell'entry (es. da
    read_messages) o, in fallback, URL trovati nel testo. Generale §7.3."""
    if isinstance(entry, dict):
        ls = entry.get("links")
        if isinstance(ls, list):
            return [u for u in ls
                    if isinstance(u, str) and u.startswith(("http://", "https://"))]
    return _URL_RE.findall(_pick_text(entry))[:5]


def _empty_fields(records, fields) -> int:
    """Quanti valori-campo richiesti sono vuoti (0 record = massimo incompleto)."""
    if not records:
        return len(fields)
    return sum(1 for r in records for f in fields
               if not str(r.get(f, "")).strip())


def _drill_fetch(urls, max_links) -> str:
    """Scarica fino a `max_links` URL e ritorna il testo concatenato, riusando
    read_urls_html (fetch + html2text + fallback js_render/sidecar per le SPA).
    Solleva se la capacita' web-fetch non e' installata (→ drill degrada)."""
    import sys
    import config as _C
    p = str(_C.PATH_EXECUTORS / "read_urls_html")
    if p not in sys.path:
        sys.path.insert(0, p)
    import read_urls_html as _rh
    res = _rh.invoke({"urls": list(urls)[:max_links], "js_render": True})
    parts = []
    for e in (res.get("entries") or []):
        t = e.get("body_text") or ""
        if t.strip():
            parts.append(t)
    return "\n\n".join(parts)


def handle_extract_entries(args, *, verbose: bool = False) -> dict:
    a = args or {}
    entries = a.get("entries")
    if entries is None and isinstance(a.get("texts"), list):
        entries = a["texts"]
    if not isinstance(entries, list):
        return {"ok": False,
                "error": _msg("ERR_ARG_NOT_LIST", arg="entries"),
                "error_class": "invalid_args", "entries": []}

    instruction = a.get("instruction") or a.get("what") or ""
    tier = a.get("tier") or "middle"
    fields = a.get("fields")
    infer_fields = fields in (None, "", [])
    if isinstance(fields, str) and fields.strip():
        fields = [f.strip() for f in fields.split(",") if f.strip()]
    if not infer_fields and not (isinstance(fields, list) and fields
                                 and all(isinstance(f, str) and f.strip()
                                         for f in fields)):
        return {"ok": False,
                "error": _msg("ERR_ARG_INVALID", arg="fields",
                              reason="list[str]"),
                "error_class": "invalid_args", "entries": []}
    if not infer_fields:
        fields = [field.strip() for field in fields]

    # Generic deterministic adapter for producers that already expose
    # structured records under a different field vocabulary.  A mapping is
    # target-field -> source-field (or ordered fallback source-fields); scalar
    # defaults fill producer-owned constants.  This keeps schema adaptation in
    # the same universal boundary as ordinary structured projection, without
    # spending LLM calls to rename keys or copy observed values.
    structured_map = a.get("structured_map")
    structured_defaults = a.get("structured_defaults")
    if (structured_map is not None or structured_defaults is not None) \
            and infer_fields:
        return {"ok": False, "entries": [],
                "error": _msg("ERR_ARG_INVALID", arg="fields",
                              reason="required with structured mapping"),
                "error_class": "invalid_args"}
    if structured_map is not None:
        if not isinstance(structured_map, dict):
            return {"ok": False, "entries": [],
                    "error": _msg("ERR_ARG_INVALID", arg="structured_map",
                                  reason="object target -> source field(s)"),
                    "error_class": "invalid_args"}
        for target, sources_spec in structured_map.items():
            if target not in fields or not (
                    isinstance(sources_spec, str)
                    or (isinstance(sources_spec, list)
                        and sources_spec
                        and all(isinstance(source, str) and source
                                for source in sources_spec))):
                return {"ok": False, "entries": [],
                        "error": _msg(
                            "ERR_ARG_INVALID", arg="structured_map",
                            reason=("targets must be requested fields and "
                                    "sources strings or non-empty string lists")),
                        "error_class": "invalid_args"}
    if structured_defaults is not None:
        if (not isinstance(structured_defaults, dict)
                or any(target not in fields
                       for target in structured_defaults)):
            return {"ok": False, "entries": [],
                    "error": _msg(
                        "ERR_ARG_INVALID", arg="structured_defaults",
                        reason="object whose keys are requested fields"),
                    "error_class": "invalid_args"}

    audit_fields = a.get("audit_fields") or []
    if isinstance(audit_fields, str):
        audit_fields = [field.strip() for field in audit_fields.split(",")
                        if field.strip()]
    if not (isinstance(audit_fields, list)
            and all(isinstance(field, str) and field.strip()
                    for field in audit_fields)):
        return {"ok": False,
                "error": _msg("ERR_ARG_INVALID", arg="audit_fields",
                              reason="list[str]"),
                "error_class": "invalid_args", "entries": []}
    # The audit pass is deliberately small and cannot replace or expand the
    # primary record schema.  Duplicates are removed while preserving order.
    audit_fields = list(dict.fromkeys(
        field.strip() for field in audit_fields
        if field.strip() and field.strip() not in fields))[:8]

    schema_meta: dict = {}
    if infer_fields:
        try:
            fields, schema_meta = _infer_fields(entries, instruction, tier)
        except Exception as ex:
            log.warning("extract_entries: field inference failed: %r", ex)
            return {
                "ok": False, "entries": [],
                "error": _msg("ERR_LLM_UNAVAILABLE"),
                "error_code": "ERR_LLM_UNAVAILABLE",
                "error_class": "field_inference_failed",
            }
        if not fields:
            return {
                "ok": False, "entries": [],
                "error": _msg("ERR_LLM_UNAVAILABLE"),
                "error_code": "ERR_LLM_UNAVAILABLE",
                "error_class": "field_inference_failed",
                "in_tokens": int(schema_meta.get("in_tokens") or 0),
                "out_tokens": int(schema_meta.get("out_tokens") or 0),
                "latency_ms": int(schema_meta.get("latency_ms") or 0),
            }

    # list[list] (righe di read_spreadsheet §2.6) → list[dict] header-aware, cosi'
    # la proiezione deterministica e l'LLM vedono i `fields` (no ripple su read).
    if entries and any(isinstance(e, (list, tuple)) for e in entries):
        entries = _rows_to_records(entries)

    try:
        max_per_text = max(1, int(
            a.get("max_per_text") or _DEFAULT_MAX_PER_TEXT))
        max_total = max(0, int(a.get("max_total") or 0))
        max_sources = max(1, min(
            _MAX_SOURCES_LIMIT, int(a.get("max_sources") or _MAX_INPUTS)))
        batch_size = max(1, min(
            _MAX_BATCH_SIZE, int(a.get("batch_size") or 1)))
    except (TypeError, ValueError):
        return {"ok": False, "entries": [],
                "error": _msg("ERR_ARG_INVALID", arg="limits",
                              reason="must be integers"),
                "error_class": "invalid_args"}

    sources = entries[:max_sources]
    truncated_inputs = len(entries) > max_sources
    input_source_total = len(sources)
    reference_entries = a.get("relevance_entries")
    reference_fields = a.get("relevance_fields") or []
    explicit_relevance_terms = a.get("relevance_terms") or []
    raw_state_markers = a.get("state_markers") or {}
    if reference_entries is not None and not isinstance(reference_entries, list):
        return {"ok": False, "entries": [],
                "error": _msg("ERR_ARG_INVALID", arg="relevance_entries",
                              reason="list[object]"),
                "error_class": "invalid_args"}
    if not (isinstance(reference_fields, list)
            and all(isinstance(field, str) and field
                    for field in reference_fields)):
        return {"ok": False, "entries": [],
                "error": _msg("ERR_ARG_INVALID", arg="relevance_fields",
                              reason="list[str]"),
                "error_class": "invalid_args"}
    if not (isinstance(explicit_relevance_terms, list)
            and all(isinstance(term, str) and term.strip()
                    for term in explicit_relevance_terms)):
        return {"ok": False, "entries": [],
                "error": _msg("ERR_ARG_INVALID", arg="relevance_terms",
                              reason="list[str]"),
                "error_class": "invalid_args"}
    if not (isinstance(raw_state_markers, dict)
            and all(isinstance(state, str) and state.strip()
                    and isinstance(markers, list) and markers
                    and all(isinstance(marker, str) and marker.strip()
                            for marker in markers)
                    for state, markers in raw_state_markers.items())):
        return {"ok": False, "entries": [],
                "error": _msg("ERR_ARG_INVALID", arg="state_markers",
                              reason="object state -> non-empty list[str]"),
                "error_class": "invalid_args"}
    state_markers = {
        state.strip(): list(dict.fromkeys(
            marker for raw in markers
            if (marker := _match_text(raw))))
        for state, markers in raw_state_markers.items()
    }
    relevance_terms = _relevance_terms(
        explicit_relevance_terms, reference_entries, reference_fields)
    if relevance_terms:
        sources = [source for source in sources
                   if any(_matches_relevance(_pick_text(source), term)
                          for term in relevance_terms)]
    filtered_source_total = input_source_total - len(sources)
    # Provenance/confidence/domain and other producer-owned facts are attached
    # deterministically below.  Asking the model to repeat them in every row
    # consumed a material share of generation time and could only lower their
    # fidelity.  Keep the original public schema at the boundary.
    runtime_owned_keys = (
        _ORIGIN_FIELDS | _HASH_FIELDS | _READABLE_FIELDS
        | _FILE_TYPE_FIELDS | _CONFIDENCE_FIELDS | _DOMAIN_FIELDS
        | _DUPLICATE_FIELDS | _DIAGNOSTIC_FIELDS
    )
    model_fields = [field for field in fields
                    if _field_key(field) not in runtime_owned_keys]
    if not model_fields:
        model_fields = list(fields)
    prompt = _build_prompt(model_fields, instruction, max_per_text)

    # drill_down: default ON (sempre attivo se la capacita' web-fetch e'
    # installata; degrada onesto se assente). Roberto 16/6.
    drill_down = a.get("drill_down", True)
    drill_max_links = int(a.get("drill_max_links") or 3)

    if structured_map is not None or structured_defaults is not None:
        projected: list[dict] = []

        def _mapped_value(source: dict, source_spec):
            source_fields = ([source_spec] if isinstance(source_spec, str)
                             else list(source_spec or []))
            for source_field in source_fields:
                value = source.get(source_field)
                if value in (None, "", []):
                    continue
                if isinstance(value, (list, tuple, set)):
                    observed = []
                    seen = set()
                    for item in value:
                        if item in (None, ""):
                            continue
                        rendered = str(item)
                        folded = rendered.casefold()
                        if folded not in seen:
                            seen.add(folded)
                            observed.append(rendered)
                    return "; ".join(observed)
                return value
            return ""

        for source in sources:
            if not isinstance(source, dict):
                continue
            record = {field: "" for field in fields}
            for target, source_spec in (structured_map or {}).items():
                record[target] = _mapped_value(source, source_spec)
            for target, value in (structured_defaults or {}).items():
                if record.get(target) in (None, "", []):
                    record[target] = value
            projected_record = _attach_source_provenance(
                record, source, fields)
            projected_record = _attach_runtime_evidence(
                projected_record, source, _pick_text(source), 1,
                relevance_terms, state_markers)
            # A mapped field is copied from an already structured producer;
            # missing fields are inapplicable, not uncertainty about the
            # observed values.  Confidence therefore describes provenance
            # fidelity rather than the density of this cross-domain schema.
            for field in fields:
                if _field_key(field) in _CONFIDENCE_FIELDS:
                    projected_record[field] = 0.95
            projected.append(projected_record)
            if max_total and len(projected) >= max_total:
                break
        result = {
            "ok": True,
            "entries": projected,
            "used": len(projected),
            "available_total": len(sources),
            "n_sources": len(sources),
            "fields": fields,
            "fields_inferred": infer_fields,
            "source": "structured_map",
            "meta": {
                "deterministic": True,
                "mode": "structured_map",
                "schema_source": "explicit",
            },
            "in_tokens": 0,
            "out_tokens": 0,
            "latency_ms": 0,
            "input_source_total": input_source_total,
            "selected_source_total": len(sources),
            "filtered_source_total": filtered_source_total,
        }
        if truncated_inputs:
            result.update({
                "truncated": True,
                "truncated_what": _msg("MSG_OBJECT_SOURCES"),
                "available_input_total": len(entries),
                "cap_field": "n_sources",
                "cap_value": max_sources,
            })
        if max_total and len(projected) >= max_total < len(sources):
            result.update({
                "truncated": True,
                "truncated_intentional": True,
                "truncated_what": _msg("MSG_OBJECT_ENTRIES"),
                "cap_field": "max_total",
                "cap_value": max_total,
            })
        return result

    mt = _extract_max_tokens(max_per_text)
    out: list = []
    in_tok = int(schema_meta.get("in_tokens") or 0)
    out_tok = int(schema_meta.get("out_tokens") or 0)
    lat = int(schema_meta.get("latency_ms") or 0)
    failed = 0
    out_truncated = 0  # sorgenti il cui output ha (probabilmente) toccato il cap
    drilled_sources = 0
    drill_unavailable = False
    batch_calls = 0
    batch_fallback_sources = 0
    batch_circuit_open = False
    llm_metrics_lock = threading.Lock()

    def _record_llm_meta(meta: dict, token_cap: int) -> None:
        nonlocal in_tok, out_tok, lat, out_truncated
        emitted = int(meta.get("out_tokens") or 0)
        with llm_metrics_lock:
            in_tok += int(meta.get("in_tokens") or 0)
            out_tok += emitted
            lat += int(meta.get("latency_ms") or 0)
            if emitted >= token_cap - 16:
                out_truncated += 1

    def _llm_extract(src_text):
        raw, meta = call_llm(src_text, prompt, tier=tier, max_tokens=mt,
                             think=False)
        _record_llm_meta(meta, mt)
        parsed = [{f: rec.get(f, "") for f in model_fields}
                  for rec in _parse_records(
                      raw, model_fields)[:max_per_text]]
        context = AgenticContext(
            goal={"operation": "extract_records", "fields": model_fields},
            observed={"source_char_count": len(src_text)},
            constraints={"max_records": max_per_text},
        )
        outcome = run_bounded_sync(
            context=context,
            propose=lambda _ctx: AgenticProposal(parsed) if parsed else None,
            execute=lambda proposal, _ctx: proposal.action,
            validate=lambda proposal, _ctx: (
                isinstance(proposal.action, list)
                and len(proposal.action) <= max_per_text
                and all(isinstance(record, dict)
                        and set(record) == set(model_fields)
                        for record in proposal.action)),
            limits=AgenticLimits(max_attempts=1),
            postcondition=lambda result, _ctx: (
                isinstance(result, list) and bool(result)),
        )
        return outcome.result if outcome.status == "completed" else []

    def _llm_extract_batch(batch):
        """Extract several independent sources in one LLM request.

        Every record carries a runtime-only source index.  Invalid or missing
        indexes are not guessed: the caller re-runs only those sources through
        the established serial path, preserving recall and provenance.
        """
        nonlocal batch_calls
        internal_field = "source_index"
        batch_fields = [internal_field, *model_fields]
        payload = [
            {internal_field: source_index, "text": text}
            for source_index, _entry, text in batch
        ]
        record_cap = min(64, max_per_text * len(batch))
        raw, meta = call_llm(
            payload,
            _build_batch_prompt(model_fields, instruction, max_per_text),
            tier=tier, max_tokens=_extract_max_tokens(record_cap), think=False,
            max_query_chars=_BATCH_QUERY_CHARS + 4096,
        )
        with llm_metrics_lock:
            batch_calls += 1
        _record_llm_meta(meta, _extract_max_tokens(record_cap))
        valid_indexes = {source_index for source_index, _entry, _text in batch}
        grouped: dict[int, list[dict]] = {}
        parsed_records: list[tuple[object, dict]] = []
        try:
            decoded = json.loads(raw.strip())
        except (json.JSONDecodeError, AttributeError):
            decoded = None
        # Compact positional protocol: [source_index, field1, field2, ...].
        # It avoids regenerating the same JSON keys for every entity.  Object
        # rows remain accepted for compatibility and safe model drift.
        decoded_rows = decoded
        if isinstance(decoded, dict):
            seen_indexes = decoded.get("seen_source_indexes")
            if isinstance(seen_indexes, list):
                for raw_index in seen_indexes:
                    try:
                        seen_index = int(raw_index)
                    except (TypeError, ValueError):
                        continue
                    if seen_index in valid_indexes:
                        grouped.setdefault(seen_index, [])
            decoded_rows = decoded.get("records")
        if isinstance(decoded_rows, list):
            for row in decoded_rows:
                if (isinstance(row, list)
                        and len(row) == len(model_fields) + 1):
                    parsed_records.append((
                        row[0], dict(zip(model_fields, row[1:]))))
                elif isinstance(row, dict):
                    parsed_records.append((
                        row.get(internal_field),
                        {field: row.get(field, "")
                         for field in model_fields}))
        if not parsed_records:
            parsed_records = [
                (record.get(internal_field),
                 {field: record.get(field, "") for field in model_fields})
                for record in _parse_records(raw, batch_fields)
            ]
        for raw_index, normalized in parsed_records:
            try:
                source_index = int(raw_index)
            except (TypeError, ValueError):
                continue
            if source_index not in valid_indexes:
                continue
            bucket = grouped.setdefault(source_index, [])
            if len(bucket) < max_per_text:
                bucket.append(normalized)
        return grouped

    # §2.2/§7.3/§7.9 — proiezione deterministica su entries GIA' strutturate:
    # se OGNI sorgente e' un dict che contiene GIA' i `fields` richiesti come
    # chiavi, «estrarre» = PROIETTARE quei campi (niente LLM). extract-da-testo
    # su input strutturato e' inutile e FALLISCE: es. read_events -> evento
    # {summary,start,...}, `_pick_text` vede solo il summary, l'LLM non trova lo
    # `start` -> 0 record -> create_files_spreadsheet saltato -> §2.8 falsa
    # mutazione («creato il foglio» senza file). Deterministico > LLM. Scatta
    # solo se l'input e' UNIFORMEMENTE strutturato coi campi richiesti.
    def _has_all_fields(e):
        return isinstance(e, dict) and all(f in e for f in fields)
    if sources and all(_has_all_fields(e) for e in sources):
        proj = []
        for source in sources:
            projected = _attach_source_provenance(
                {field: source.get(field) for field in fields}, source, fields)
            proj.append(_attach_runtime_evidence(
                projected, source, _pick_text(source), 1,
                relevance_terms, state_markers))
        if max_total and len(proj) > max_total:
            proj = proj[:max_total]
        projected_result = {
            "ok": True, "entries": proj, "used": len(proj),
            "available_total": len(sources), "n_sources": len(sources),
            "fields": fields, "source": "structured_projection",
            "fields_inferred": infer_fields,
            "meta": {"deterministic": not infer_fields,
                     "mode": "structured_projection",
                     "schema_source": ("inferred" if infer_fields
                                       else "explicit")},
            "in_tokens": in_tok, "out_tokens": out_tok, "latency_ms": lat,
            "input_source_total": input_source_total,
            "selected_source_total": len(sources),
            "filtered_source_total": filtered_source_total,
        }
        if relevance_terms:
            projected_result["relevance_filter"] = True
            projected_result["relevance_term_count"] = len(relevance_terms)
        return projected_result

    total_capped = False

    def _append_records(entry, text, records) -> None:
        nonlocal total_capped
        audit_values = (_extract_labeled_audit_values(text, audit_fields)
                        if audit_fields and records and text.strip() else {})
        for extracted in records:
            norm = {field: extracted.get(field, "") for field in fields}
            if audit_values:
                norm.update(audit_values)
            if isinstance(entry, dict):
                norm = _attach_source_provenance(norm, entry, fields)
            norm = _attach_runtime_evidence(
                norm, entry if isinstance(entry, dict) else None, text,
                len(records), relevance_terms, state_markers)
            # Provenienza privata riservata al runtime. Nelle estrazioni
            # confinate conserva il link osservato senza aprirlo.
            if drill_down is False and isinstance(entry, dict):
                source_url = entry.get("url") or entry.get("htmlLink")
                if isinstance(source_url, str) and source_url.startswith(
                        ("http://", "https://")):
                    norm["_source_url"] = source_url
                    if isinstance(entry.get("title"), str):
                        norm["_source_title"] = entry["title"]
            out.append(norm)
            if max_total and len(out) >= max_total:
                total_capped = True
                break

    # Opt-in batch path.  It is deliberately restricted to no-drill
    # extraction: network drill-down is source-specific and remains serial.
    # The runtime-assigned budget may process independent batches concurrently
    # when the signed executor class and startup LLM profile both allow it.
    # Recomposition, fallback and output emission remain source ordered.
    if batch_size > 1 and drill_down is False:
        pending: list[tuple[int, dict, str]] = []
        direct: dict[int, tuple[dict, str, list[dict]]] = {}
        for source_index, entry in enumerate(sources):
            if isinstance(entry, dict) and entry.get("readable") is False:
                direct[source_index] = (
                    entry, "", [{field: "" for field in fields}])
                continue
            text_value = _pick_model_text(entry)[:_MAX_TEXT_CHARS]
            if text_value.strip():
                pending.append((source_index, entry, text_value))

        records_by_index: dict[int, list[dict]] = {}
        processed = 0
        batches = list(_source_batches(pending, batch_size))
        empty_batch_streak = 0
        worker_count = assigned_workers(item_count=len(batches))

        def _run_batch(item):
            batch_number, batch, attempted_batch = item
            try:
                if attempted_batch:
                    grouped = _llm_extract_batch(batch)
                elif len(batch) == 1:
                    grouped = {batch[0][0]: _llm_extract(batch[0][2])}
                else:
                    # Circuit opened after repeated unusable batch responses:
                    # stop paying the batch overhead and use the established
                    # serial path for the remaining sources.
                    grouped = {
                        source_index: _llm_extract(text_value)
                        for source_index, _entry, text_value in batch
                    }
            except Exception as ex:
                log.warning("extract_entries: batch LLM call failed: %r", ex)
                grouped = {}
            return batch_number, batch, attempted_batch, grouped

        next_batch = 0
        while next_batch < len(batches):
            # Once repeated unusable batch responses open the circuit, keep
            # the established per-source fallback serial for all later work.
            wave_size = 1 if batch_circuit_open else worker_count
            wave = []
            for offset, batch in enumerate(
                    batches[next_batch:next_batch + wave_size]):
                batch_number = next_batch + offset + 1
                wave.append((
                    batch_number, batch,
                    len(batch) > 1 and not batch_circuit_open,
                ))
            completed, _skipped = map_ordered(_run_batch, wave)
            for _wave_index, result in completed:
                batch_number, batch, attempted_batch, grouped = result
                if attempted_batch:
                    if grouped:
                        empty_batch_streak = 0
                    else:
                        empty_batch_streak += 1
                        if empty_batch_streak >= 2:
                            batch_circuit_open = True
                # Missing source indexes are retried through the established
                # serial path. A formatting error therefore cannot drop a
                # source, even when its first batch ran concurrently.
                for source_index, _entry, text_value in batch:
                    records = grouped.get(source_index)
                    if records is None:
                        batch_fallback_sources += 1
                        try:
                            records = _llm_extract(text_value)
                        except Exception as ex:
                            failed += 1
                            log.warning(
                                "extract_entries: batch fallback failed: %r",
                                ex)
                            records = []
                    records_by_index[source_index] = records
                processed += len(batch)
                try:
                    from executor_progress import update as _progress_update
                    _progress_update(
                        f"extract_entries: {processed}/{len(pending)} sorgenti "
                        f"(batch {batch_number}/{len(batches)})")
                except Exception:
                    pass
            next_batch += len(wave)

        pending_map = {
            source_index: (entry, text_value)
            for source_index, entry, text_value in pending
        }
        for source_index, entry in enumerate(sources):
            if source_index in direct:
                direct_entry, text_value, records = direct[source_index]
                _append_records(direct_entry, text_value, records)
            elif source_index in pending_map:
                pending_entry, text_value = pending_map[source_index]
                _append_records(
                    pending_entry, text_value,
                    records_by_index.get(source_index, []))
            if total_capped:
                break

    else:
        for entry in sources:
            if isinstance(entry, dict) and entry.get("readable") is False:
                # I metadata/path non sono contenuto estraibile: evita che il
                # fallback testuale li mandi al modello e conserva direttamente
                # l'evidenza del parse fallito.
                _append_records(
                    entry, "", [{field: "" for field in fields}])
                if max_total and len(out) >= max_total:
                    total_capped = True
                    break
                continue
            nonlocal_drill = drill_down and bool(_entry_links(entry))
            text = _pick_model_text(entry)[:_MAX_TEXT_CHARS]
            if not text.strip() and not nonlocal_drill:
                # Un documento osservato ma non decodificabile non scompare
                # dalla pipeline: il ramo unreadable sopra conserva i parse
                # falliti; input davvero vuoti non generano record inventati.
                continue
            try:
                records = _llm_extract(text) if text.strip() else []
            except Exception as ex:
                failed += 1
                log.warning("extract_entries: LLM call failed: %r", ex)
                continue
            # Drill-down §7.3: campi richiesti vuoti + link disponibili →
            # segui i link, ri-estrai sul testo+pagina, tieni il risultato piu'
            # completo.
            if nonlocal_drill and _empty_fields(records, fields) > 0:
                try:
                    drilled = _drill_fetch(
                        _entry_links(entry), drill_max_links)
                except Exception:
                    drill_unavailable = True
                    drilled = ""
                if drilled.strip():
                    try:
                        records2 = _llm_extract(
                            (text + "\n\n" + drilled)[:_DRILL_TEXT_CHARS])
                    except Exception:
                        records2 = []
                    if records2 and (_empty_fields(records2, fields)
                                     < _empty_fields(records, fields)):
                        records = records2
                        drilled_sources += 1
            _append_records(entry, text, records)
            if total_capped:
                break

    res = {
        "ok": True,
        "entries": out,
        "used": len(out),
        "available_total": len(out),
        "n_sources": len(sources),
        "fields": fields,
        "fields_inferred": infer_fields,
        "in_tokens": in_tok, "out_tokens": out_tok, "latency_ms": lat,
        "input_source_total": input_source_total,
        "selected_source_total": len(sources),
        "filtered_source_total": filtered_source_total,
    }
    if relevance_terms:
        res["relevance_filter"] = True
        res["relevance_term_count"] = len(relevance_terms)
    if failed:
        res["failed_sources"] = failed
    if audit_fields:
        res["audit_fields"] = audit_fields
    if batch_calls:
        res["batch_calls"] = batch_calls
        res["batch_size"] = batch_size
        res["batch_fallback_sources"] = batch_fallback_sources
        if batch_circuit_open:
            res["batch_circuit_open"] = True
    if drilled_sources:
        res["drilled_sources"] = drilled_sources  # campi riempiti seguendo link
    if drill_unavailable:
        # §2.8 onesto: campi mancanti + link presenti, ma la capacita' web-fetch
        # non e' installata → non ho potuto drillare.
        res["drill_unavailable"] = True
    if out_truncated:
        # §2.7/§2.11 visibility: l'output LLM ha toccato il token-cap su ≥1
        # sorgente → qualche record oltre il cap puo' mancare (il parser tollerante
        # ha salvato i completi). `truncated:True` fa scattare la NOTIFICA del
        # runtime (mai silenzio §2.8). Cura: alza max_per_text / spezza la sorgente.
        res["truncated"] = True
        res["truncated_what"] = _msg("MSG_OBJECT_ENTRIES")
        res["output_truncated_sources"] = out_truncated
        res["cap_field"] = "max_per_text"
        res["cap_value"] = max_per_text
    if truncated_inputs:
        # §2.7 visibility
        res["truncated"] = True
        res["truncated_what"] = _msg("MSG_OBJECT_SOURCES")
        res["available_input_total"] = len(entries)
        res["cap_field"] = "n_sources"
        res["cap_value"] = max_sources
    if total_capped:
        # §2.7/§2.8 (ADR 0062): cap max_total RICHIESTO dall'utente raggiunto →
        # visibile ma truncated_intentional (il runtime NON propone allargamento).
        # Il totale reale non è noto (loop interrotto): `used` = record mostrati.
        res["truncated"] = True
        res["truncated_intentional"] = True
        res["truncated_what"] = _msg("MSG_OBJECT_ENTRIES")
        res["cap_field"] = "max_total"
        res["cap_value"] = max_total
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
            "from_step=N, instruction=\"record richiesti\"). `fields` e' "
            "opzionale: se assente, l'executor inferisce un piccolo schema "
            "dallo scopo e dal testo; se presente, lo applica esattamente. "
            "NON: usare per ETICHETTARE "
            "(classify_entries) né per filtrare campi di entries GIÀ "
            "strutturate (get/filter_entries); non è per archivi (extract_files). "
            "I campi data/ora escono in ISO 8601. OUT: entries=[{fields...}] "
            "lista piatta dei record, pipeable verso create_events/"
            "*_spreadsheet/send."
        ),
        "parameters": {
            "type": "object",
            "required": ["from_step"],
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
                    "description": "Campi di OGNI record da estrarre. Se "
                                   "omesso, viene inferito un set bounded dallo "
                                   "scopo e dal testo osservato.",
                },
                "audit_fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Campi source-level etichettati estratti "
                                   "deterministicamente e associati a ogni "
                                   "record della stessa sorgente senza "
                                   "modificarne il numero.",
                },
                "structured_map": {
                    "type": "object",
                    "description": "Adattatore deterministico opzionale per "
                                   "entries gia' strutturate con nomi-campo "
                                   "diversi: output_field -> source_field o "
                                   "lista ordinata di fallback. Evita chiamate "
                                   "LLM per semplici rinomine/copied facts.",
                },
                "structured_defaults": {
                    "type": "object",
                    "description": "Valori scalari predefiniti per i campi "
                                   "non valorizzati da structured_map.",
                },
                "relevance_entries": {
                    "type": "array",
                    "description": "Record osservati da una sorgente-ancora; "
                                   "le loro entità selezionano deterministicamente "
                                   "le sole sorgenti testuali pertinenti prima "
                                   "delle chiamate LLM.",
                },
                "relevance_fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Campi di relevance_entries dai quali "
                                   "derivare le ancore di pertinenza.",
                },
                "relevance_terms": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Ancore testuali esplicite aggiuntive per "
                                   "il prefilter di pertinenza.",
                },
                "state_markers": {
                    "type": "object",
                    "description": "Mappa opzionale stato normalizzato -> "
                                   "frasi sorgente osservabili. Riempie un "
                                   "campo stato/status vuoto senza "
                                   "sovrascrivere fatti esistenti.",
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
                "max_sources": {
                    "type": "integer",
                    "description": "Cap esplicito delle sorgenti da elaborare "
                                   "(default 50, massimo 1000).",
                },
                "batch_size": {
                    "type": "integer",
                    "description": "Numero di sorgenti indipendenti per "
                                   "richiesta LLM (default 1, massimo 16). "
                                   "Usare solo con drill_down=false; ordine e "
                                   "provenienza sono preservati con fallback "
                                   "seriale per sorgente.",
                },
                "drill_down": {
                    "type": "boolean",
                    "description": "Se true può leggere i link presenti nelle "
                                   "sorgenti per completare campi mancanti. "
                                   "Default true; usare false per pagine di "
                                   "sessione autenticate.",
                },
            },
        },
    },
}


BUILTIN_INPROC_SPECS = [{
    "name": "extract_entries", "tool_spec": EXTRACT_ENTRIES_TOOL,
    "affinity": ["estrai", "ricava", "extract", "parse", "entries"],
}]
