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
testo). Usa il workload `entries.extract` (→ middle).

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
from uuid import uuid4
from datetime import date

from executor_workers import assigned_workers, map_ordered
from llm_helpers import call_llm
from logging_setup import get_logger
from messages import get as _msg
from agentic_executor import (
    AgenticContext, AgenticLimits, AgenticProposal, run_bounded_sync,
)
import detection_lexicon_seed_reconciliation as _reconciliation_lex
from urllib.parse import urldefrag, urlsplit
from time_window_parser import normalize_observed_date
from tabular_records import merge_source_metadata

log = get_logger(__name__)

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

_RUNTIME_OWNED_FIELD_IDS = frozenset({
    "origin", "content_hash", "readable", "file_type", "confidence",
    "domain", "duplicates", "diagnostic",
})
_DEFAULT_RELEVANCE_FIELD_IDS = frozenset({
    "entity", "person", "project", "organization", "email", "phone",
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
    # Producers that follow redirects expose both the requested origin and
    # final URL.  Provenance means what the user supplied, not merely where
    # the transport ended up.
    direct = (source.get("origin") or source.get("requested_url")
              or source.get("path") or source.get("url")
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
    """Normalize source dates with the shared observed-date parser."""
    return normalize_observed_date(value)


# One definition of the mark, shared with whoever has to compare these values
# as dates (`filter_entries`, `sort_entries`): the mark states that the year
# was assumed rather than read, and a comparator that took it literally would
# drop exactly the rows the mark exists to save.
from executor_helpers import ASSUMED_YEAR_MARK as _ASSUMED_YEAR_MARK


def _normalize_extracted_date(field: str, value, *, lexicon=None):
    """Normalise complete observed dates without inventing missing parts."""
    if not isinstance(value, str) or not value.strip():
        return value
    if lexicon is None:
        lexicon = _reconciliation_lex.load()
    if _field_temporal_kind(field, lexicon=lexicon) is None:
        return value
    return normalize_observed_date(value)


_WEB_AUDIT_DERIVED_FIELDS = frozenset({
    "is_redirect", "is_timeout", "is_unreadable", "is_empty",
    "has_iframe", "needs_js_render",
})


def _source_field_value(source: dict, field: str, *, lexicon=None):
    """Deterministic aliases for facts already structured by producers."""
    if lexicon is None:
        lexicon = _reconciliation_lex.load()
    field_identity = (
        lexicon.canonical_field(field) if lexicon is not None else None
    )
    source_identity = (
        lexicon.source_identity(field) if lexicon is not None else None
    )
    derived_key = {
        "redirect": "is_redirect",
        "iframe": "has_iframe",
        "javascript_required": "needs_js_render",
    }.get(source_identity, _field_key(field))
    # Web readers already expose enough transport metadata to answer these
    # audit booleans exactly.  They are projections/derivations of observed
    # producer facts, not semantic extraction: never spend an LLM call merely
    # to rename ``redirected`` or compare ``text_length`` with zero.
    if derived_key == "is_redirect":
        if "redirected" in source:
            return bool(source.get("redirected"))
        origin = source.get("origin") or source.get("requested_url")
        final = source.get("final_url") or source.get("url")
        return bool(origin and final and str(origin) != str(final))
    if derived_key == "is_timeout":
        observed = " ".join(str(source.get(name) or "") for name in (
            "status", "error", "error_code", "error_class"))
        return "timeout" in observed.casefold()
    if derived_key == "is_unreadable":
        status = str(source.get("status") or "").strip().casefold()
        return bool(status and status not in {"ok", "success"})
    if derived_key == "is_empty":
        if "text_length" in source:
            try:
                return int(source.get("text_length") or 0) == 0
            except (TypeError, ValueError):
                return not bool(str(source.get("body_text") or "").strip())
        return not bool(str(source.get("body_text") or "").strip())
    if derived_key == "has_iframe":
        if "iframe_count" in source:
            try:
                return int(source.get("iframe_count") or 0) > 0
            except (TypeError, ValueError):
                return bool(source.get("iframe_count"))
        return bool(source.get("iframe_urls"))
    if derived_key == "needs_js_render":
        return bool(source.get("js_required"))
    # For a calendar appointment the actionable date is its start. ``end``
    # may be days later and is exclusive for all-day Google events, so letting
    # the model choose it creates false deadlines.
    if (_source_domain(source) == "calendar"
            and field_identity == "deadline"):
        start = source.get("start")
        if isinstance(start, str) and start.strip():
            value = start.strip()
            return value[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", value) else value
    if field_identity == "origin":
        return _source_origin(source)
    if field_identity == "domain":
        # ``domain`` is overloaded historically: producer family
        # (email/calendar/files) or an explicit web hostname.  An observed
        # producer value is more specific and must never be erased.
        return (_observed_canonical_value(source, "domain", lexicon)
                or _source_domain(source))
    if field_identity == "duplicates":
        return source.get("duplicate_paths") or []
    if field_identity == "diagnostic":
        return source.get("parse_diagnostic") or ""
    source_keys = lexicon.source_keys(field) if lexicon is not None else None
    if source_keys is None:
        return None
    for source_key in source_keys:
        value = source.get(source_key)
        if value not in (None, "", []):
            return _source_date(value) if source_identity == "date" else value
    return None


def _source_field_is_observed(source: dict, field: str, *, lexicon=None) -> bool:
    """Whether a producer exposed a field, including an observed empty value.

    Optional facts such as a page language or publication date are allowed to
    be absent.  Presence of their producer key is still sufficient for a
    lossless structured projection; falling back to an LLM would turn an
    honest absence into latency and possible invention.
    """
    if lexicon is None:
        lexicon = _reconciliation_lex.load()
    if field in source:
        return True
    field_identity = (
        lexicon.canonical_field(field) if lexicon is not None else None
    )
    source_identity = (
        lexicon.source_identity(field) if lexicon is not None else None
    )
    derived_key = {
        "redirect": "is_redirect",
        "iframe": "has_iframe",
        "javascript_required": "needs_js_render",
    }.get(source_identity, _field_key(field))
    if derived_key in _WEB_AUDIT_DERIVED_FIELDS:
        evidence = {
            "is_redirect": ("redirected", "origin", "requested_url",
                            "final_url", "url"),
            "is_timeout": ("status", "error", "error_code", "error_class"),
            "is_unreadable": ("status",),
            "is_empty": ("text_length", "body_text"),
            "has_iframe": ("iframe_count", "iframe_urls"),
            "needs_js_render": ("js_required",),
        }
        return any(name in source for name in evidence[derived_key])
    if (_source_domain(source) == "calendar"
            and field_identity == "deadline"):
        return "start" in source
    source_keys = lexicon.source_keys(field) if lexicon is not None else None
    return bool(source_keys) and any(
        source_key in source for source_key in source_keys
    )


def _field_key(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value).casefold())
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = "".join(
        character if character.isalnum() else "_" for character in value
    )
    return re.sub(r"_+", "_", value).strip("_")


def _field_identity(field: str, *, lexicon=None) -> str | None:
    if lexicon is not None:
        return lexicon.canonical_field(field)
    technical = str(field)
    if technical in (_RUNTIME_OWNED_FIELD_IDS
                     | _DEFAULT_RELEVANCE_FIELD_IDS
                     | {"deadline", "normalized_date", "normalized_time",
                        "type"}):
        return technical
    return None


def _is_runtime_owned_field(field: str, *, lexicon=None) -> bool:
    return _field_identity(field, lexicon=lexicon) in _RUNTIME_OWNED_FIELD_IDS


def _field_temporal_kind(field: str, *, lexicon=None) -> str | None:
    identity = _field_identity(field, lexicon=lexicon)
    if identity in {"deadline", "normalized_date"}:
        return "date_only"
    if identity == "normalized_time":
        return "datetime"
    if lexicon is None:
        return None
    key = _field_key(field)
    if lexicon.matches_gate("date_only_field", key):
        return "date_only"
    if lexicon.matches_gate("datetime_field", key):
        return "datetime"
    return None


def _observed_canonical_value(source: dict, canonical: str, lexicon):
    """Return a producer value whose field has one admitted identity."""
    for key, value in source.items():
        identity = (
            lexicon.canonical_field(key) if lexicon is not None
            else (key if key == canonical else None)
        )
        if identity == canonical and value not in (None, "", []):
            return value
    return None


def _attach_source_provenance(record: dict, source: dict,
                              fields: list[str], *, lexicon=None) -> dict:
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
    if lexicon is None:
        lexicon = _reconciliation_lex.load()
    for field in fields:
        value = _source_field_value(source, field, lexicon=lexicon)
        if value not in (None, "", []):
            out[field] = value
    # Canonical fact triples remain complete even when the model emits only
    # the entity or only its normalized/original spelling.  This is a lossless
    # fallback (copy of an observed value), not an inferred business fact.
    canonical_fields = {
        canonical: field for field in fields
        if lexicon is not None
        for canonical in (lexicon.canonical_field(field),)
        if canonical is not None
    }
    entity_field = canonical_fields.get("entity")
    normalized_field = canonical_fields.get("normalized_value")
    original_field = canonical_fields.get("original_value")
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
    deadline_field = canonical_fields.get("deadline")
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
    substantive = [
        field for field in fields
        if not _is_runtime_owned_field(field, lexicon=lexicon)
    ]
    completeness = (sum(1 for field in substantive
                        if out.get(field) not in (None, "", []))
                    / max(1, len(substantive)))
    for field in fields:
        identity = _field_identity(field, lexicon=lexicon)
        if identity == "origin":
            out[field] = _source_origin(source)
        elif identity == "content_hash":
            out[field] = raw_hash or ""
        elif identity == "readable" and readable is not None:
            out[field] = bool(readable)
        elif identity == "file_type":
            out[field] = file_type or ""
        elif identity == "confidence":
            observed_confidence = _observed_canonical_value(
                source, "confidence", lexicon,
            )
            out[field] = (observed_confidence
                          if observed_confidence not in (None, "") else
                          0.10 if readable is False else
                          0.95 if completeness >= 0.5 else 0.60)
        elif identity == "domain":
            out[field] = (
                _observed_canonical_value(source, "domain", lexicon)
                or _source_domain(source)
            )
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
        "_source_scope_label": source.get("_source_scope_label"),
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


def _match_text(value) -> str:
    """Forma Unicode/case/punctuation-insensitive per il prefilter."""
    normalized = unicodedata.normalize("NFKD", str(value or "").casefold())
    normalized = "".join(
        char for char in normalized if not unicodedata.combining(char))
    normalized = "".join(
        char if char.isalnum() or char in "@.+_-" else " "
        for char in normalized
    )
    return " ".join(normalized.split())


def _relevance_terms(explicit, reference_entries, reference_fields,
                     *, lexicon=None) -> list[str]:
    """Deriva ancore ad alta precisione da record già osservati.

    Le frasi vengono mantenute intere.  Per persone usiamo anche il cognome,
    per scope/progetti i token non generici: evita che iniziali o nomi propri
    troppo deboli (es. ``Roberto B.``) selezionino intere mailbox.
    """
    terms: list[str] = []
    seen: set[str] = set()
    if lexicon is None:
        lexicon = _reconciliation_lex.load()

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
        for token in (_match_text(value).split() if lexicon is not None else []):
            if (len(token) >= 4
                    and _field_key(token)
                    not in lexicon.relevance_generic_tokens):
                add(token)

    if lexicon is None:
        return terms[:256]
    wanted = (
        {
            canonical for field in reference_fields
            if (canonical := lexicon.canonical_field(field)) is not None
        }
        if isinstance(reference_fields, list) and reference_fields
        else set(_DEFAULT_RELEVANCE_FIELD_IDS)
    )
    refs = reference_entries if isinstance(reference_entries, list) else []
    for record in refs:
        if not isinstance(record, dict):
            continue
        record_type = ""
        for field, value in record.items():
            if lexicon.canonical_field(field) == "type":
                record_type = _field_key(value)
                break
        for field, value in record.items():
            field_identity = lexicon.canonical_field(field)
            if field_identity not in wanted or field_identity == "type":
                continue
            values = value if isinstance(value, list) else [value]
            for observed in values:
                if not isinstance(observed, (str, int, float)):
                    continue
                normalized = _match_text(observed)
                tokens = re.findall(r"[a-z0-9]+", normalized)
                if field_identity in {"email", "phone"}:
                    add(observed)
                elif record_type in lexicon.relevance_person_types:
                    # Una frase con iniziali è troppo debole come match.
                    if (len(tokens) <= 1
                            or all(len(token) >= 2 for token in tokens)):
                        add(observed)
                    if tokens and len(tokens[-1]) >= 4:
                        add(tokens[-1])
                elif record_type in lexicon.relevance_organization_types:
                    add(observed)
                elif field_identity in {"project", "organization"}:
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


def _build_prompt(fields, instruction, max_per_text, *, lexicon=None) -> str:
    # A name that states its GRANULARITY wins over one that states its ROLE.
    # `data_inizio` and `checkin_date` name the same thing, but the first also
    # matched the datetime pattern (through "inizio") and so took the
    # datetime branch, where the date-only rules — the assumed-year fallback
    # among them — never applied. Same page, same request, two different
    # answers depending on which language the model happened to name the
    # column in (turns 558b0e9f and 771d5a78, 2026-08-07).
    if lexicon is None:
        lexicon = _reconciliation_lex.load()
    date_only = [
        field for field in fields
        if _field_temporal_kind(field, lexicon=lexicon) == "date_only"
    ]
    date_time = [
        field for field in fields
        if _field_temporal_kind(field, lexicon=lexicon) == "datetime"
    ]
    import i18n
    import prompt_loader
    return prompt_loader.get(
        "extract_entries", i18n.current_lang(),
        fields_json=json.dumps(fields, ensure_ascii=False),
        instruction=instruction,
        max_per_text=max_per_text,
        datetime_fields=", ".join(date_time),
        date_only_fields=", ".join(date_only),
        current_year=date.today().year,
        example_field=(fields[0] if fields else "field"),
    )


def _build_batch_prompt(fields, instruction, max_per_text,
                        *, lexicon=None) -> str:
    """Prompt whose input/output contract is explicitly multi-source.

    Reusing the scalar prompt made the local model interpret the JSON bundle
    as one opaque text and return ``[]``.  Keep this contract separate: the
    model sees the carrier field as structural, while callers still receive
    exactly the public ``fields`` schema after parsing.
    """
    if lexicon is None:
        lexicon = _reconciliation_lex.load()
    date_only = [
        field for field in fields
        if _field_temporal_kind(field, lexicon=lexicon) == "date_only"
    ]
    date_time = [
        field for field in fields
        if _field_temporal_kind(field, lexicon=lexicon) == "datetime"
    ]
    import i18n
    import prompt_loader
    return prompt_loader.get(
        "extract_entries_batch", i18n.current_lang(),
        fields_json=json.dumps(["source_index", *fields, "_identity_facts"],
                               ensure_ascii=False),
        example_row=json.dumps([0, *(["..."] * len(fields)), []],
                               ensure_ascii=False),
        field_count=len(fields) + 2,
        instruction=instruction,
        max_per_text=max_per_text,
        datetime_fields=", ".join(date_time),
        date_only_fields=", ".join(date_only),
        current_year=date.today().year,
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
            tier=tier, max_tokens=256,
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
        return [r for r in data if isinstance(r, dict)
                and any(field in r for field in fields)]
    if isinstance(data, dict):
        # Public field presence admits a record, including 0/False. Its
        # private annotation lists must not be mistaken for wrapper rows.
        if any(f in data for f in fields):
            return [data]
        nested = [value for key, value in data.items()
                  if not key.startswith("_") and isinstance(value, (dict, list))]
        # A list of records comes before a nested object: a summary or a
        # metadata block that carries a requested field is not the result.
        for value in sorted(nested, key=lambda value: not isinstance(value, list)):
            records = _coerce_records(value, fields)
            if records:
                return records
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
        # A readable empty/metadata-only result is final. Re-parsing its
        # nested arrays could turn private fact annotations into public rows.
        return _coerce_records(json.loads(s), fields)
    except json.JSONDecodeError:
        pass
    # 2) Recover complete outer objects before considering their arrays, so
    # private annotation lists keep their enclosing record's meaning.
    objects = _salvage_objects(s)
    if objects:
        return [record for obj in objects
                for record in _coerce_records(obj, fields)]
    # 3) An unfinished wrapper may still contain a complete public array.
    m = re.search(r"\[[\s\S]*\]", s)
    if m:
        # Keep its enclosing objects, including escaped/private keys. A
        # detached array loses whether its items were records or annotations.
        # The wrapper is the outermost brace still open before the array;
        # braces of earlier text that closed again do not enclose it.
        prefix = re.sub(r'"(?:[^"\\]|\\.)*"', lambda q: " " * len(q.group()), s[:m.start()])
        opened = []
        for index, char in enumerate(prefix):
            if char == "{":
                opened.append(index)
            elif char == "}" and opened:
                opened.pop()
        start = opened[0] if opened else -1
        candidate = s[start:m.end()] if start >= 0 else m.group(0)
        structure = re.sub(r'"(?:[^"\\]|\\.)*"', '""', candidate)
        candidate += "}" * (structure.count("{") - structure.count("}"))
        try:
            return _coerce_records(json.loads(candidate), fields)
        except json.JSONDecodeError:
            pass
    return []


# Output tokens of one record with its identity annotation and literal quote
# (21/10/2026: about 290 measured on a ten-invoice list; the former 120, set
# before the annotations, cut the tenth invoice at the limit).
_TOKENS_PER_RECORD = 320


# Headings labelled in one request; more distinct ones are read as before.
_MAX_HEADINGS = 64
_HEADING_LABELS = frozenset({"same", "other", "unclear"})


def _section_heading(entry) -> str:
    """The title a page lists its rows under: the last segment of its address
    and the page's own title (its first line when it has none). It declares
    the class of those rows. A detail opened from a row of a list inherits
    that row's class: it has no heading of its own here."""
    if not isinstance(entry, dict) or isinstance(entry.get("_source_record_view"), dict):
        return ""
    url = entry.get("url") if isinstance(entry.get("url"), str) else ""
    if not url.startswith(("http://", "https://")):
        # Only a web page lists its rows under a title; a message or a file
        # has no heading class.
        return ""
    slug = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    title = entry.get("title").strip() if isinstance(entry.get("title"), str) else ""
    if not title:
        title = next((line.strip() for line in _pick_text(entry).splitlines() if line.strip()), "")
    return " | ".join(part for part in (re.sub(r"[-_]+", " ", slug).strip(), title[:160]) if part)


def _heading_labels(headings: list[str], instruction: str, tier: str) -> dict[str, str]:
    """Headings declaring another class of items than the requested one
    (Telepass 21/10/2026: «Riepiloghi annuali», rows shaped like invoices).
    The model labels numbered headings; a heading is excluded only beside one
    of the requested class. Any doubt or failure excludes none."""
    import i18n
    import prompt_loader
    distinct = list(dict.fromkeys(h for h in headings if h))[:_MAX_HEADINGS]
    if len(distinct) < 2 or not instruction:
        return {}
    try:
        raw, _meta = call_llm(
            json.dumps({"request": instruction,
                        "headings": [{"n": n, "text": h} for n, h in enumerate(distinct, 1)]},
                       ensure_ascii=False),
            prompt_loader.get("extract_entries_headings", i18n.current_lang()),
            tier=tier, max_tokens=64 + 8 * len(distinct))
        found = re.search(r"\{.*\}", str(raw or ""), re.S)
        labels = json.loads(found.group()).get("labels") if found else None
    except Exception:  # noqa: BLE001 -- a failed labelling reads every page
        return {}
    if (not isinstance(labels, list) or len(labels) != len(distinct)
            or any(label not in _HEADING_LABELS for label in labels)):
        return {}
    return dict(zip(distinct, labels))


def _row_sources(row: dict, page_of: dict) -> set:
    """The pages a row was read from: a snapshot's page, or the snapshot."""
    return {page_of.get(ref) or ref for ref in [row.get("_record_source")] + [
        w.get("_record_source") for w in row.get("_record_provenance") or () if isinstance(w, dict)]
        if ref is not None}


def _resolve_list_conflicts(rows: list[dict], outcomes: dict | None = None,
                            page_of: dict | None = None) -> int:
    """A field whose values disagree takes the value the list shows: the
    value of the only source that also gives this field to other rows
    (Roberto, 22/10/2026: «data come nell'elenco»; real runs en25c and enel8,
    Enel: the list shows the due date, a detail another date 17–20 days
    earlier). The row stays valid; every other value stays a witness, shown in
    the Notes. Values that agree form one group, supported by its best
    witness; snapshots of one page are one source (`page_of`: snapshot to page;
    real run a7, ENI: the list read again after each detail). Without one such
    source the contradiction stays declared. Returns fields resolved;
    `outcomes` counts each closed outcome (diagnostics)."""
    from record_identity import _comparison, _most_informative

    outcomes = outcomes if outcomes is not None else {}
    page_of = page_of or {}

    def note(outcome):
        outcomes[outcome] = outcomes.get(outcome, 0) + 1

    resolved = 0
    for row in rows:
        conflicts = row.get("_record_conflicts")
        witnesses = [w for w in row.get("_record_provenance") or () if isinstance(w, dict)]
        if not isinstance(conflicts, list) or len(witnesses) < 2:
            continue
        for field in list(conflicts):
            seen = [w for w in witnesses if w.get(field) not in (None, "")]
            if len(seen) < 2:
                note("no_values")
                continue
            support = [sum(1 for other in rows if other is not row
                           and other.get(field) not in (None, "")
                           and (page_of.get(w.get("_record_source")) or w.get("_record_source"))
                           in _row_sources(other, page_of)) for w in seen]
            groups: list[list[int]] = []
            for n, w in enumerate(seen):
                group = next((g for g in groups
                              if _comparison(seen[g[0]][field], w[field])[0] != "conflict"), None)
                if group is None:
                    groups.append([n])
                else:
                    group.append(n)
            group_support = [max(support[n] for n in group) for group in groups]
            best = max(group_support)
            if not best or group_support.count(best) != 1:
                note("no_list_source")
                continue
            row[field] = _most_informative([seen[n][field] for n in groups[group_support.index(best)]])
            conflicts.remove(field)
            resolved += 1
            note("resolved")
        if not conflicts:
            row.pop("_record_conflicts", None)
    return resolved


def _text_lines(text: str) -> list[str]:
    return [" ".join(line.split()) for line in text.splitlines() if line.strip()]


def _shown_within(inner: list[str], outer: list[str]) -> bool:
    """Every line of `inner`, in its order, is in `outer` with at most one
    contiguous run of lines added: the same page before «show more» or a
    scroll added rows, or before a row was expanded."""
    if len(inner) > len(outer):
        return False
    prefix = 0
    while prefix < len(inner) and inner[prefix] == outer[prefix]:
        prefix += 1
    rest = len(inner) - prefix
    return not rest or inner[prefix:] == outer[-rest:]


def _page_key(entry) -> tuple | None:
    """One web page in one context, whatever snapshot of it: address without
    fragment, title, context, partition and identity scope. None for detail
    views and other sources, each its own."""
    url = entry.get("url") if isinstance(entry, dict) else None
    if (not isinstance(url, str) or not url.startswith(("http://", "https://"))
            or isinstance(entry.get("_source_record_view"), dict)):
        return None
    return (urldefrag(url)[0], *(json.dumps(entry.get(name), sort_keys=True, default=str)
                                 for name in ("title", "_source_context_label",
                                              "_source_partition", "_source_identity_scope")))


def _redundant_snapshots(sources: list) -> set[int]:
    """Indexes of web page snapshots that another snapshot of the same page,
    title and context shows whole (Fastweb 21/10/2026: six snapshots of one
    list growing after each «show more», every invoice extracted four or
    five times). Of two identical snapshots the later one is kept. Detail
    views, unreadable sources and sources other than web pages are always
    read."""
    keyed = []
    for index, entry in enumerate(sources):
        key = _page_key(entry)
        if key is None or entry.get("readable") is False:
            continue
        lines = _text_lines(_pick_model_text(entry)[:_MAX_TEXT_CHARS])
        if lines:
            keyed.append((index, key, lines))
    shown_in = {index: {other for other, other_key, other_lines in keyed
                        if other != index and other_key == key
                        and (len(other_lines) > len(lines)
                             or (other_lines == lines and other > index))
                        and _shown_within(lines, other_lines)}
                for index, key, lines in keyed}
    # Only a snapshot that no other one shows whole stands in for others.
    kept = {index for index, containers in shown_in.items() if not containers}
    return {index for index, containers in shown_in.items() if containers & kept}


def _extract_max_tokens(max_per_text: int) -> int:
    """Output budget scaled with the expected records, plus a margin; capped
    at 8192 against runaway output. It prevents truncation upstream (cause #1
    of lost records: an array cut in the middle); the tolerant parser remains
    the safety net downstream."""
    return max(1200, min(8192, 512 + int(max_per_text) * _TOKENS_PER_RECORD))


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


def _extract_labeled_audit_values(text: str, audit_fields: list[str],
                                  *, lexicon=None) -> dict:
    """Extract source-level ``Label [qualifier]: value`` facts.

    This pass is deterministic and cardinality-neutral.  Qualifiers allow
    forms such as ``Fornitore alternativo proposto: Vega`` while the closed
    bilingual aliases keep unrelated prose from becoming an audit fact.
    """
    lines = str(text or "").splitlines()
    out: dict[str, str] = {}
    if lexicon is None:
        lexicon = _reconciliation_lex.load()
    if lexicon is None:
        return out
    for field in audit_fields:
        aliases = lexicon.audit_forms(field)
        if aliases is None:
            continue
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


def _declare_truncation(result: dict, *, what: str, cap_field: str,
                        cap_value: int, intentional: bool = False,
                        **details) -> None:
    """Aggiunge un motivo senza sovrascrivere gli altri cap raggiunti.

    I campi top-level restano per retrocompatibilità; ``truncations`` è la
    rappresentazione completa quando più limiti scattano nello stesso turno.
    """
    item = {
        "truncated_what": what,
        "cap_field": cap_field,
        "cap_value": cap_value,
        "truncated_intentional": bool(intentional),
        **details,
    }
    result.setdefault("truncations", []).append(item)
    result.update({
        "truncated": True,
        "truncated_what": what,
        "cap_field": cap_field,
        "cap_value": cap_value,
        **details,
    })
    if intentional:
        result["truncated_intentional"] = True
    else:
        result["partial"] = True


def _handle_extract_entries(args, *, verbose: bool = False,
                            collection_complete: bool = True) -> dict:
    a = args or {}
    entries = a.get("entries")
    if entries is None and isinstance(a.get("texts"), list):
        entries = a["texts"]
    if not isinstance(entries, list):
        return {"ok": False,
                "error": _msg("ERR_ARG_NOT_LIST", arg="entries"),
                "error_class": "invalid_args", "entries": []}

    instruction = a.get("instruction") or a.get("what") or ""
    from llm_workloads import tier_for
    tier = tier_for("entries.extract")
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

    # This executor builds one structured artefact from several linguistic
    # resources.  Freeze one complete, native-ready family before doing any
    # inference or projection; a missing/partial locale must not create a
    # hybrid record or apply caller-provided natural-language state markers.
    reconciliation_lexicon = _reconciliation_lex.load()
    if reconciliation_lexicon is None:
        return {
            "ok": False,
            "entries": [],
            "error": _msg("ERR_EXT_SVC_UNAVAILABLE"),
            "error_code": "ERR_I18N_LEXICON_UNAVAILABLE",
            "error_class": "dependency_unavailable",
        }

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
                "error": _msg("ERR_LLM_UNAVAILABLE_ACTION"),
                "error_code": "ERR_LLM_UNAVAILABLE",
                "error_class": "field_inference_failed",
            }
        if not fields:
            return {
                "ok": False, "entries": [],
                "error": _msg("ERR_LLM_UNAVAILABLE_ACTION"),
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
        explicit_relevance_terms, reference_entries, reference_fields,
        lexicon=reconciliation_lexicon,
    )
    if relevance_terms:
        sources = [source for source in sources
                   if any(_matches_relevance(_pick_text(source), term)
                          for term in relevance_terms)]
    # The heading a page lists its rows under is their class: rows under a
    # heading of another class than the requested one are other items, not
    # read and declared in the sheet's Notes (Roberto 21/10/2026: «righe
    # classificate da intestazioni diverse non sono uguali»).
    foreign_sections = 0
    heading_labels: list = []
    if instruction and len(sources) > 0:
        headings = [_section_heading(source) for source in sources]
        labels = _heading_labels(headings, instruction, tier)
        heading_labels = [labels.get(heading) for heading in headings]
        # Excluded only beside a heading of the requested class.
        if "same" in labels.values():
            kept_sources = [source for source, label, heading in zip(sources, heading_labels, headings)
                            if not heading or label != "other"]
            foreign_sections = len(sources) - len(kept_sources)
            sources = kept_sources
    filtered_source_total = input_source_total - len(sources)
    # Provenance/confidence/domain and other producer-owned facts are attached
    # deterministically below.  Asking the model to repeat them in every row
    # consumed a material share of generation time and could only lower their
    # fidelity.  Keep the original public schema at the boundary.
    model_fields = [field for field in fields
                    if not _is_runtime_owned_field(
                        field, lexicon=reconciliation_lexicon,
                    )]
    if not model_fields:
        model_fields = list(fields)
    prompt = _build_prompt(
        model_fields, instruction, max_per_text,
        lexicon=reconciliation_lexicon,
    )

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
                record, source, fields, lexicon=reconciliation_lexicon,
            )
            projected_record = _attach_runtime_evidence(
                projected_record, source, _pick_text(source), 1,
                relevance_terms, state_markers)
            # A mapped field is copied from an already structured producer;
            # missing fields are inapplicable, not uncertainty about the
            # observed values.  Confidence therefore describes provenance
            # fidelity rather than the density of this cross-domain schema.
            for field in fields:
                if (_field_identity(field, lexicon=reconciliation_lexicon)
                        == "confidence"):
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
            _declare_truncation(
                result, what=_msg("MSG_OBJECT_SOURCES"),
                cap_field="n_sources", cap_value=max_sources,
                available_input_total=len(entries))
        if max_total and len(projected) >= max_total < len(sources):
            _declare_truncation(
                result, what=_msg("MSG_OBJECT_ENTRIES"),
                cap_field="max_total", cap_value=max_total,
                intentional=True)
        return result

    mt = _extract_max_tokens(max_per_text)
    out: list = []
    in_tok = int(schema_meta.get("in_tokens") or 0)
    out_tok = int(schema_meta.get("out_tokens") or 0)
    lat = int(schema_meta.get("latency_ms") or 0)
    failed = 0
    failed_sources: list[dict] = []
    text_truncated_sources = 0
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

    class _ObservedRecords(list):
        # Runtime-owned parser cardinality, before any truncation.
        def __init__(self, values, *, total, complete):
            super().__init__(values)
            self.total = total
            self.complete = complete

    def _complete_output(raw, meta, cap):
        try:
            json.loads(raw.strip())
        except (ValueError, AttributeError):
            return False
        return (int(meta.get("out_tokens") or 0) < cap - 16
                and meta.get("finish_reason") not in ("length", "max_tokens"))

    def _llm_extract(src_text):
        raw, meta = call_llm(src_text, prompt, tier=tier, max_tokens=mt)
        _record_llm_meta(meta, mt)
        observed = _parse_records(raw, model_fields)
        parsed = _ObservedRecords([{**{f: rec.get(f, "") for f in model_fields},
                   "_identity_facts": rec.get("_identity_facts", [])}
                  for rec in observed[:max_per_text]], total=len(observed),
                  complete=_complete_output(raw, meta, mt))
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
                        and set(record) == {*model_fields, "_identity_facts"}
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
        payload = [
            {internal_field: source_index, "text": text}
            for source_index, _entry, text in batch
        ]
        record_cap = min(64, max_per_text * len(batch))
        raw, meta = call_llm(
            payload,
            _build_batch_prompt(
                model_fields, instruction, max_per_text,
                lexicon=reconciliation_lexicon,
            ),
            tier=tier, max_tokens=_extract_max_tokens(record_cap),
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
        # Compact positional protocol ends in private fact annotations.
        # Malformed/missing facts cannot authorize identity; keep public data
        # from otherwise readable records, including object output drift.
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
                        and len(row) in (len(model_fields) + 1, len(model_fields) + 2)):
                    parsed_records.append((
                        row[0], {**dict(zip(model_fields, row[1:1 + len(model_fields)])),
                                 "_identity_facts": (row[-1] if len(row) == len(model_fields) + 2 else [])}))
                elif (isinstance(row, dict)
                      and any(field in row for field in model_fields)):
                    parsed_records.append((
                        row.get(internal_field),
                        {**{field: row.get(field, "") for field in model_fields},
                         "_identity_facts": row.get("_identity_facts", [])}))
        if not parsed_records:
            parsed_records = [
                (record.get(internal_field),
                 {**{field: record.get(field, "") for field in model_fields},
                  "_identity_facts": record.get("_identity_facts", [])})
                for record in _parse_records(raw, model_fields)
            ]
        for raw_index, normalized in parsed_records:
            try:
                source_index = int(raw_index)
            except (TypeError, ValueError):
                continue
            if source_index not in valid_indexes:
                continue
            bucket = grouped.setdefault(source_index, [])
            bucket.append(normalized)
        complete = (_complete_output(raw, meta, _extract_max_tokens(record_cap))
                    and len(parsed_records) < record_cap)
        return {index: _ObservedRecords(values[:max_per_text], total=len(values), complete=complete)
                for index, values in grouped.items()}

    # §2.2/§7.3/§7.9 — proiezione deterministica su entries GIA' strutturate:
    # se OGNI sorgente e' un dict che contiene GIA' i `fields` richiesti come
    # chiavi, «estrarre» = PROIETTARE quei campi (niente LLM). extract-da-testo
    # su input strutturato e' inutile e FALLISCE: es. read_events -> evento
    # {summary,start,...}, `_pick_text` vede solo il summary, l'LLM non trova lo
    # `start` -> 0 record -> create_files_spreadsheet saltato -> §2.8 falsa
    # mutazione («creato il foglio» senza file). Deterministico > LLM. Scatta
    # solo se l'input e' UNIFORMEMENTE strutturato coi campi richiesti.
    def _has_all_fields(e):
        if not isinstance(e, dict):
            return False
        return all(
            field in e
            or _is_runtime_owned_field(
                field, lexicon=reconciliation_lexicon,
            )
            or _source_field_is_observed(
                e, field, lexicon=reconciliation_lexicon,
            )
            for field in fields
        )
    if sources and all(_has_all_fields(e) for e in sources):
        proj = []
        for source in sources:
            projected = _attach_source_provenance(
                {field: (source.get(field)
                         if source.get(field) is not None else "")
                 for field in fields},
                source, fields, lexicon=reconciliation_lexicon,
            )
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
        if truncated_inputs:
            _declare_truncation(
                projected_result, what=_msg("MSG_OBJECT_SOURCES"),
                cap_field="n_sources", cap_value=max_sources,
                available_input_total=len(entries))
        if max_total and len(sources) > max_total:
            _declare_truncation(
                projected_result, what=_msg("MSG_OBJECT_ENTRIES"),
                cap_field="max_total", cap_value=max_total,
                intentional=True)
        return projected_result

    total_capped = False

    # A snapshot another one of the same page shows whole adds no record:
    # reading it again costs one more extraction of the same rows.
    redundant = _redundant_snapshots(sources)
    if redundant:
        sources = [source for index, source in enumerate(sources) if index not in redundant]

    observations = {id(source): index for index, source in enumerate(sources)}
    observation_scope = uuid4().hex

    # Listed records are copied from their rows: the model only indicates the
    # columns, once per list shape (list_columns). Copied rows leave the text
    # the model reads; a source with nothing else dated or priced is not read.
    from list_columns import copy_listed_rows
    listed = copy_listed_rows(
        sources, model_fields, instruction, tier, call_llm=call_llm,
        date_field=lambda name: _field_temporal_kind(
            name, lexicon=reconciliation_lexicon) is not None)

    def _copied_records(source_index):
        records = listed.records[source_index]
        return _ObservedRecords(records, total=len(records), complete=True)

    source_texts: dict = {}
    source_pages: dict = {}

    def _append_records(entry, text, records, *, copied: bool = False) -> None:
        nonlocal total_capped
        audit_values = (_extract_labeled_audit_values(
                            text, audit_fields,
                            lexicon=reconciliation_lexicon,
                        )
                        if audit_fields and records and text.strip() else {})
        from record_identity import (admit_identity_facts, complete_identifier_facts,
                                     valid_reference)
        source_ref = entry.get("_source_snapshot_ref") if isinstance(entry, dict) else None
        if not valid_reference(source_ref):
            source_ref = f"{observation_scope}:{observations.get(id(entry), -1)}"
        source_texts[source_ref] = text
        if _page_key(entry) is not None:
            source_pages[source_ref] = _page_key(entry)
        admitted = []
        for extracted in records:
            norm = {
                field: _normalize_extracted_date(
                    field, extracted.get(field, ""),
                    lexicon=reconciliation_lexicon,
                )
                for field in fields
            }
            if audit_values:
                norm.update(audit_values)
            admitted.append(admit_identity_facts(
                norm, extracted.get("_identity_facts"), source_text=text,
                source_ref=source_ref,
                scope=(entry.get("_source_identity_scope") if isinstance(entry, dict) else None),
            ))
            # Private: values copied from a listed row, and the labels the
            # site writes beside them (list_columns).
            for key in ("_record_extraction", "_record_field_labels"):
                if extracted.get(key):
                    admitted[-1][key] = (dict(extracted[key]) if isinstance(extracted[key], dict)
                                         else extracted[key])
        for norm in complete_identifier_facts(admitted, text):
            if isinstance(entry, dict):
                norm = _attach_source_provenance(
                    norm, entry, fields, lexicon=reconciliation_lexicon,
                )
                # Private: the record partition (contract, account...) the page
                # was read in; it keeps rows of different partitions apart.
                label, partition = entry.get("_source_context_label"), entry.get("_source_partition")
                if isinstance(label, str) and label.strip() and isinstance(partition, str) and partition:
                    norm["_record_partition"] = partition
                    norm["_record_partition_label"] = " ".join(label.split())
            norm = _attach_runtime_evidence(
                norm, entry if isinstance(entry, dict) else None, text,
                len(records), relevance_terms, state_markers)
            if isinstance(entry, dict) and isinstance(entry.get("_source_record_view"), dict):
                norm["_source_record_view"] = {
                    **entry["_source_record_view"],
                    "single": (len(records) == 1 and max_per_text > 1
                               and getattr(records, "total", None) == 1
                               and getattr(records, "complete", False)
                               and len(text) >= len(_pick_model_text(entry))),
                }
            # Provenienza privata riservata al runtime. Nelle estrazioni
            # confinate conserva il link osservato senza aprirlo.
            if drill_down is False and isinstance(entry, dict):
                source_url = entry.get("url") or entry.get("htmlLink")
                if isinstance(source_url, str) and source_url.startswith(
                        ("http://", "https://")):
                    norm["_source_url"] = source_url
                    if isinstance(entry.get("title"), str):
                        norm["_source_title"] = entry["title"]
            # Private: which read of a source produced the row (identity merge).
            norm["_observation"] = observations.get(id(entry), -1)
            # Private: the model returned every record of this whole source.
            norm["_source_complete"] = copied or bool(
                getattr(records, "complete", True)
                and getattr(records, "total", len(records)) < max_per_text
                and (not isinstance(entry, dict) or len(text) >= len(_pick_model_text(entry))))
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
            full_text = listed.residual.get(source_index, _pick_model_text(entry))
            text_value = full_text[:_MAX_TEXT_CHARS]
            if text_value.strip():
                text_truncated_sources += int(len(full_text) > _MAX_TEXT_CHARS)
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
                            failed_sources.append({"index": source_index})
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
                if total_capped:
                    break
                continue
            if source_index in listed.records:
                _append_records(entry, _pick_model_text(entry),
                                _copied_records(source_index), copied=True)
                if total_capped:
                    break
            if source_index in pending_map:
                pending_entry, text_value = pending_map[source_index]
                _append_records(
                    pending_entry,
                    _pick_model_text(pending_entry) if source_index in listed.records else text_value,
                    records_by_index.get(source_index, []))
            if total_capped:
                break

    else:
        for source_index, entry in enumerate(sources):
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
            full_text = _pick_model_text(entry)
            if source_index in listed.records:
                # Copied rows first; the model reads only what is left beside them.
                _append_records(entry, full_text, _copied_records(source_index), copied=True)
                if total_capped:
                    break
                nonlocal_drill = False
                full_text = listed.residual[source_index]
            text = full_text[:_MAX_TEXT_CHARS]
            if not text.strip() and not nonlocal_drill:
                # Un documento osservato ma non decodificabile non scompare
                # dalla pipeline: il ramo unreadable sopra conserva i parse
                # falliti; input davvero vuoti non generano record inventati.
                continue
            text_truncated_sources += int(len(full_text) > _MAX_TEXT_CHARS)
            try:
                records = _llm_extract(text) if text.strip() else []
            except Exception as ex:
                failed += 1
                failed_sources.append({"index": source_index})
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
                        # A combined fetched text is not the original bound
                        # snapshot; it cannot certify that source's cardinality.
                        records.complete = False
                        drilled_sources += 1
            # A listed source keeps its whole text: quotes of either part are there.
            _append_records(entry, _pick_model_text(entry) if source_index in listed.records else text,
                            records)
            if total_capped:
                break

    from record_identity import complete_identifier_facts_across
    out = complete_identifier_facts_across(out, source_texts)
    from sites_collection_sources import merge_extracted_views
    out, merged_views = merge_extracted_views(out)
    from record_identity import claims_identity, merge_observed_identifiers, unidentified_absorbed
    complete = collection_complete and all(record.get("_source_complete", True) for record in out)
    out, merged_identifiers, suspected_duplicates = merge_observed_identifiers(
        out, source_key="_record_source", complete=complete)
    list_outcomes: dict = {}
    list_resolved = _resolve_list_conflicts(out, list_outcomes, source_pages)
    # Where the requested records carry a unique identifier, a row still
    # without one after the merges is rejected; the writer declares it with
    # its values. Without identifiers, equal rows coexist (Roberto, 20/10/2026).
    absorbed_rows = rejected_rows = 0
    if any(claims_identity(record) for record in out):
        absorbed_rows = unidentified_absorbed(out)
        for record in out:
            if not claims_identity(record) and not record.get("_record_shared_code"):
                record["_record_rejected"] = True
                rejected_rows += 1
    for record in out:
        record.pop("_observation", None)
        record.pop("_source_complete", None)
        for witness in record.get("_record_provenance") or ():
            if isinstance(witness, dict):
                witness.pop("_source_complete", None)
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
    if merged_views:
        res["merged_record_views"] = merged_views
    if merged_identifiers:
        res["merged_identifier_records"] = merged_identifiers
    if suspected_duplicates:
        res["suspected_duplicate_records"] = suspected_duplicates
    if absorbed_rows:
        res["absorbed_unidentified_records"] = absorbed_rows
    if rejected_rows:
        res["rejected_unidentified_records"] = rejected_rows
    if foreign_sections:
        res["foreign_section_sources"] = foreign_sections
    if redundant:
        # Diagnostics: snapshots not read again, their rows shown in another.
        res["redundant_snapshot_sources"] = len(redundant)
    if listed.meta:
        # Diagnostics: listed rows copied, choices asked and their outcomes.
        res["list_columns"] = dict(listed.meta)
    if heading_labels:
        # Private diagnostics: the label of each input page, in input order.
        res["source_heading_labels"] = heading_labels
    if list_outcomes:
        # Diagnostics: closed outcome of each disagreeing field.
        res["list_checks"] = list_outcomes
        res["list_resolved_fields"] = list_resolved
    conflicting_rows = sum(bool(row.get("_record_conflicts")) for row in out)
    if conflicting_rows:
        res["conflicting_record_rows"] = conflicting_rows
    if relevance_terms:
        res["relevance_filter"] = True
        res["relevance_term_count"] = len(relevance_terms)
    if failed:
        res["failed_sources"] = failed
        res["failed"] = failed_sources
        res["partial"] = True
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
        _declare_truncation(
            res, what=_msg("MSG_OBJECT_ENTRIES"),
            cap_field="max_per_text", cap_value=max_per_text,
            output_truncated_sources=out_truncated)
    if text_truncated_sources:
        _declare_truncation(
            res, what=_msg("MSG_OBJECT_SOURCES"),
            cap_field="max_text_chars", cap_value=_MAX_TEXT_CHARS,
            text_truncated_sources=text_truncated_sources)
    if truncated_inputs:
        # §2.7 visibility
        _declare_truncation(
            res, what=_msg("MSG_OBJECT_SOURCES"),
            cap_field="n_sources", cap_value=max_sources,
            available_input_total=len(entries))
    if total_capped:
        # §2.7/§2.8 (ADR 0062): cap max_total RICHIESTO dall'utente raggiunto →
        # visibile ma truncated_intentional (il runtime NON propone allargamento).
        # Il totale reale non è noto (loop interrotto): `used` = record mostrati.
        _declare_truncation(
            res, what=_msg("MSG_OBJECT_ENTRIES"), cap_field="max_total",
            cap_value=max_total, intentional=True)
    return res


def handle_extract_entries(args, *, verbose: bool = False) -> dict:
    """Keep collection evidence outside records and per-view merge gates."""
    a = args or {}
    try:
        inherited = merge_source_metadata(a.get("source_metadata"))
    except ValueError:
        return {"ok": False, "entries": [], "error_class": "invalid_args",
                "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="source_metadata",
                              reason="invalid_source_metadata")}
    # Runtime-owned upstream evidence: a partial collection may hold the
    # unobserved referent of a mention.
    collection_complete = not any(
        any(item.get(key) is True for key in ("partial", "collection_partial", "truncated"))
        for item in inherited)
    result = _handle_extract_entries(a, verbose=verbose, collection_complete=collection_complete)
    local = []
    scope = uuid4().hex
    for index, truncation in enumerate(result.get("truncations") or []):
        # These declarations are made by this runtime implementation, never
        # read from extracted records or the model's response envelope.
        item = {"producer": "extract_entries", "scope": scope,
                "source_ref": str(index), "truncated": True,
                "partial": truncation.get("truncated_intentional") is not True,
                "reason_code": "extraction_limit"}
        for key in ("cap_field", "cap_value"):
            if key in truncation:
                item[key] = truncation[key]
        local.append(item)
    if result.get("partial") is True and not local:
        local.append({"producer": "extract_entries", "scope": scope,
                      "partial": True, "reason_code": "extraction_incomplete"})
    if any(result.get(key) for key in ("merged_record_views", "merged_identifier_records",
                                      "suspected_duplicate_records", "conflicting_record_rows",
                                      "absorbed_unidentified_records")):
        # Runtime relationship/identity evidence, never model-supplied counts.
        local.append({"producer": "extract_entries", "scope": scope,
                      "reason_code": "record_enrichment",
                      "merged": (int(result.get("merged_record_views") or 0)
                                 + int(result.get("merged_identifier_records") or 0)),
                      "conflicted": int(result.get("conflicting_record_rows") or 0),
                      "suspected": int(result.get("suspected_duplicate_records") or 0),
                      "absorbed": int(result.get("absorbed_unidentified_records") or 0)})
    if result.get("foreign_section_sources"):
        local.append({"producer": "extract_entries", "scope": scope,
                      "reason_code": "foreign_section",
                      "excluded": int(result["foreign_section_sources"])})
    try:
        result["source_metadata"] = merge_source_metadata(inherited, local)
    except ValueError:
        return {"ok": False, "entries": [], "source_metadata": inherited,
                "error_class": "invalid_source_metadata", "error_code": "ERR_ARG_INVALID",
                "error": _msg("ERR_ARG_INVALID", arg="source_metadata",
                              reason="invalid_source_metadata")}
    return result


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
                "source_metadata": {
                    "type": "array", "items": {"type": "object"},
                    "runtime_resolved": True,
                    "from_result_key": "source_metadata",
                },
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
