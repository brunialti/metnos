"""engine/executor.py — esecutore deterministico SHARED del Layer 3.

NON pluggable. Esegue il Framework prodotto da qualsiasi Proposer in
modo deterministico §7.9:
  - risolve `from_step: N` → entries da step precedente
  - risolve `${stepN.field}` (dot-path nested + projection `*`)
  - risolve `${FILLER:name}` via LLM fast tier (con cache)
  - risolve `${RUNTIME:key}` (actor, lang, channel)
  - invoca executor reale (callback) per ogni step
  - applica vaglio.judge post-step (se fornito)
  - tracking PraxisRun-like: ok_count, latency, aborted_reason

Adattato da runtime/_legacy/praxis_executor.py — invariate le logiche
di resolve, alleggerito orchestration.

§7.3 universalità: nessuna logica domain-specific. Aggiungere nuovo
executor target NON richiede modifiche qui.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .types import Framework, StepSpec, StepRun, RunResult
from messages import get as _msg  # §11: render user-facing via DB i18n
from undo import UNDO_OUTCOMES
from field_synonyms import (
    LABEL_FIELDS as _LABEL_FIELDS, field_group as _field_group,
    is_identifier_field as _is_identifier_field,
)
import detection_lexicon_seed_runtime_safety as _runtime_safety_lexicon

log = logging.getLogger(__name__)


# ── Regex placeholder ─────────────────────────────────────────────────────

_FILLER_RE = re.compile(r"\$\{FILLER:([a-zA-Z_][a-zA-Z0-9_]*)\}")
# Step reference: supporta sia ${stepN.field} (1-indexed) che ${steps.N.field}
# (0-indexed array access). Universal §7.9 — robusto a entrambi gli stili
# che Mētis LLM emette.
_STEPREF_RE = re.compile(r"\$\{step(\d+)\.(@?[a-zA-Z_][a-zA-Z0-9_.*]*)\}")
_STEPSREF_RE = re.compile(r"\$\{steps\.(\d+)\.(@?[a-zA-Z_][a-zA-Z0-9_.*]*)\}")
_RUNTIME_RE = re.compile(r"\$\{RUNTIME:([a-zA-Z_][a-zA-Z0-9_]*)\}")

# Campi testuali in cascata per il render bullet-list delle entries (§7.9).
_BULLET_FIELDS_DATED = ("start", "summary", "subject", "title",
                        "name", "path", "url", "date")
_BULLET_FIELDS = ("start", "summary", "subject", "title", "name", "path", "url")
# Fallback G-mode per entries REMOTE (senza path locale → niente gallery):
# campi salienti di album/foto google_photos e simili (turn 4fa8d6bd).
_BULLET_FIELDS_GALLERY_FALLBACK = ("title", "filename", "name", "items_count",
                                   "created_at", "album", "url")

# ── L-mode: tabella deterministica (output_policy mode L, ADR matrice §3) ─────
# Ordine-colonna preferito (le più identificanti prima); le altre in coda,
# alfabetiche. Campi verbosi/binari esclusi (rovinano la tabella).
_TABLE_COL_PREFERENCE = ("name", "title", "subject", "path", "size", "mtime",
                         "date", "start", "type", "kind", "status", "pid",
                         "cpu", "mem", "role", "email", "url", "count")
# The fields an entry's content travels in: the content view (`@content`,
# `_read_content`) shows them, the metadata table never does, whatever their
# length. output_policy chooses between the two views; a table chosen for a
# list must not disclose what was not asked for (2/11/2026: short file text
# appeared in «mostrami la lista dei file» once length decided alone).
_CONTENT_FIELDS = ("content", "body_text", "text")
# Layout only: verbose or binary columns that spoil a table.
_TABLE_COL_SKIP = frozenset({"body", "snippet", "thumbnail", "data", "raw",
                             "html", "mime"})
# These names are English; another language can mean something short by the
# same word ("data" is the date in Italian: live Fineco 1f69a247, 2/11/2026,
# lost its date column). A named column is skipped only when it is bulky.
_TABLE_BULKY_CHARS = 200


def _bulky_cell(value: object) -> bool:
    return (isinstance(value, (dict, list, bytes, bytearray))
            or (isinstance(value, str) and len(value) > _TABLE_BULKY_CHARS))
_TABLE_MAX_ROWS = 200
_TABLE_MAX_COLS = 6
_TABLE_MAX_CHARS = 16_000
# The web chat pages long tables itself, so it receives them whole; a chat
# without pages (Telegram) keeps the declared budget.
_TABLE_PAGED_ROWS = 2_000
_TABLE_PAGED_CHARS = 300_000
_paged_tables = contextvars.ContextVar("paged_tables", default=False)
# Private-use markers consumed only by the safe HTML table renderer.  Markdown
# has no native per-cell attributes, so this is the transport between a
# manifest's declarative ``nowrap`` field and the browser presentation.
_TABLE_NOWRAP_OPEN = "\ue000"
_TABLE_NOWRAP_CLOSE = "\ue001"

def _align_url_reader_jit(tool: str, args: dict) -> str:
    """Choose the non-lossy reader at the last responsible moment.

    ``read_urls_pdf`` rejects every non-PDF response, while
    ``read_urls_html`` already dispatches ``application/pdf`` to the shared
    PDF extractor.  Therefore a PDF-only reader is safe only when every
    explicit URL is unambiguously PDF-shaped.  Mixed and extensionless lists
    use the content-type-aware reader so a stale/cached plan cannot turn a
    whole HTML batch into twelve deterministic failures.

    This changes only the invoked sibling, never the framework template or
    its cached arguments.
    """
    if tool != "read_urls_pdf" or not isinstance(args, dict):
        return tool
    urls = args.get("urls")
    if not isinstance(urls, list) or not urls:
        return tool
    from urllib.parse import urlsplit

    paths = []
    for value in urls:
        if not isinstance(value, str) or not value.strip():
            return "read_urls_html"
        try:
            paths.append(urlsplit(value).path.lower())
        except ValueError:
            return "read_urls_html"
    return "read_urls_pdf" if all(path.endswith(".pdf") for path in paths) \
        else "read_urls_html"


def _table_budget(max_rows: int, max_chars: int) -> tuple[int, int]:
    return ((_TABLE_PAGED_ROWS, _TABLE_PAGED_CHARS) if _paged_tables.get()
            else (max_rows, max_chars))


# ── How a person reads a record ───────────────────────────────────────────
# A value that is wholly an ISO date or instant. Text that merely contains a
# date is never matched.
_ISO_TEMPORAL_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[T ](\d{2}:\d{2}(?::\d{2}(?:[.,]\d{1,9})?)?)"
    r"(?:Z|[+-]\d{2}(?::?\d{2})?)?)?")
_LINK_RE = re.compile(r"https?://\S+")


def _blank(value: object) -> bool:
    """Nothing to read: absent, empty text, empty collection (0 is a value)."""
    return (value is None or value == [] or value == {}
            or (isinstance(value, str) and not value.strip()))


def _display_temporal(raw: object, kind: object = None) -> object:
    """A date or an instant the way a person reads it: on the instance's
    clock, without the zone offset.

    ``kind`` is the type a manifest column declares ("date" or "datetime"):
    it also admits an epoch number and a mail-style date. Without it only a
    value that is wholly an ISO date or instant is recognised, so the same
    rule serves a table nothing declared. A day stays a day; an instant that
    names no zone is not moved. Anything else is returned as it came.
    """
    import datetime as _dt
    typed = kind in ("date", "datetime")
    try:
        if isinstance(raw, str):
            text = raw.strip()
            match = _ISO_TEMPORAL_RE.fullmatch(text)
            if match:
                parsed = _dt.datetime.fromisoformat(
                    text.replace("Z", "+00:00").replace(",", "."))
                day_only = match.group(1) is None
            elif typed:
                from email.utils import parsedate_to_datetime
                parsed, day_only = parsedate_to_datetime(text), False
            else:
                return raw
        elif (typed and isinstance(raw, (int, float))
              and not isinstance(raw, bool)):
            parsed = _dt.datetime.fromtimestamp(float(raw), _dt.timezone.utc)
            day_only = False
        else:
            return raw
        if not isinstance(parsed, _dt.datetime):
            return raw
        if parsed.tzinfo is not None:
            from time_window_parser import temporal_now
            parsed = parsed.astimezone(temporal_now().tzinfo)
        return parsed.strftime("%d/%m/%y" if day_only or kind == "date"
                               else "%d/%m/%y %H:%M")
    except (OverflowError, OSError, TypeError, ValueError):
        return raw


def _link_column(rows: list, key) -> bool:
    """Every value the column holds is a web address."""
    values = [row.get(key) for row in rows if not _blank(row.get(key))]
    return bool(values) and all(
        isinstance(value, str) and _LINK_RE.fullmatch(value.strip())
        for value in values)


def _generic_columns(rows: list, max_cols: int, wanted: list | None) -> list:
    """The columns of a table that no manifest declared, in the order a
    person reads a record: what it is called, its period with the end beside
    the start, the preferred fields, the others, the links; a code that
    stands for the record only when nothing names it. A column with no value
    in any row is not a column.

    ``wanted`` asks for exactly those columns instead (the file of a long
    answer repeats the table of the chat over more rows): [] when the records
    do not have them.
    """
    if wanted is not None:
        names: dict = {}
        for row in rows:
            for key in row.keys():
                names.setdefault(str(key), key)
        return ([names[name] for name in wanted]
                if wanted and all(name in names for name in wanted) else [])
    sample = rows[:50]
    seen: list = []
    for e in sample:
        for k in e.keys():
            ks = str(k)
            if (k not in seen and not ks.startswith("_")
                    and ks not in _CONTENT_FIELDS
                    and not (ks in _TABLE_COL_SKIP
                             and any(_bulky_cell(r.get(k)) for r in sample))):
                seen.append(k)
    seen = [k for k in seen if any(not _blank(r.get(k)) for r in rows)]
    label = next((k for k in _LABEL_FIELDS if k in seen), None)
    start = next((k for k in _field_group("start") if k in seen), None)
    end = next((k for k in _field_group("end") if k in seen), None)
    period = (start, end) if start is not None and end is not None else ()

    def _rank(k):
        ks = str(k)
        if k == label:
            return (0, 0, ks)
        if k in period:
            return (1, period.index(k), ks)
        if _is_identifier_field(ks):
            return (5, 0, ks)
        if k in _TABLE_COL_PREFERENCE:
            return (2, _TABLE_COL_PREFERENCE.index(k), ks)
        return (4 if _link_column(sample, k) else 3, 0, ks)

    cols = sorted(seen, key=_rank)
    if label is not None:
        cols = [k for k in cols if not _is_identifier_field(str(k))]
    return cols[:max_cols]


def _table_text(lines: list[str], shown: int, total: int,
                rendered: dict | None = None) -> str:
    """Keep the table's extent and counts with its source result. A file
    can then replace this whole presentation without interpreting its words."""
    if total > shown:
        lines.append(_msg("MSG_RENDER_MORE_HIDDEN", more=total - shown))
    text = "\n".join(lines)
    if rendered is not None:
        rendered.update(shown=shown, total=total, lines=len(lines),
                        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest())
    return text


def _entries_table(entries: list, *, max_rows: int = _TABLE_MAX_ROWS,
                   max_cols: int = _TABLE_MAX_COLS,
                   max_chars: int = _TABLE_MAX_CHARS,
                   presentation: dict | None = None,
                   columns: list | None = None,
                   rendered: dict | None = None) -> str:
    """Rende una lista di entries come TABELLA markdown deterministica (§7.9,
    zero LLM). Colonne = quelle dichiarate dal manifest, altrimenti l'ordine
    di lettura di `_generic_columns` (cap `max_cols`); righe fino al primo fra
    `max_rows` e `max_chars` (§2.7: nota i18n sul resto). Entries non-dict o
    senza campi tabulabili → fallback bullet-list. `columns` chiede
    esattamente quelle colonne: stringa vuota se i record non le hanno."""
    contract = (presentation or {}).get("list") if isinstance(presentation, dict) else None
    if isinstance(contract, dict) and contract.get("columns"):
        return _contract_entries_table(entries, contract, columns, rendered=rendered)

    max_rows, max_chars = _table_budget(max_rows, max_chars)
    rows = [e for e in entries if isinstance(e, dict)]
    if not rows:
        return "" if columns is not None else _entries_bullet_lines(
            entries, fields=_BULLET_FIELDS_DATED,
            more_key="MSG_RENDER_MORE_HIDDEN")

    cols = _generic_columns(rows, max_cols, columns)
    if not cols:
        return "" if columns is not None else _entries_bullet_lines(
            entries, fields=_BULLET_FIELDS_DATED,
            more_key="MSG_RENDER_MORE_HIDDEN")
    # A cut path or link is not actionable and can name the wrong thing.
    whole = {c for c in cols
             if c in ("path", "url") or _link_column(rows[:50], c)}

    def _cell(column, v):
        if v is None:
            return ""
        # A raw epoch is not something a person reads: filesystem producers
        # expose a numeric `mtime`. The payload is not changed.
        numeric_time = (column in {"mtime", "mtime_epoch", "date_modified_epoch"}
                        and isinstance(v, (int, float))
                        and not isinstance(v, bool))
        v = _display_temporal(v, "datetime" if numeric_time else None)
        text = str(v).replace("|", "\\|").replace("\n", " ")
        # Per gli altri campi mantieni un cap generoso ma rendi VISIBILE il
        # troncamento con ellissi.
        if column in whole:
            return text
        max_chars = 120
        return text if len(text) <= max_chars else text[:max_chars - 1] + "…"
    lines = ["| " + " | ".join(str(c) for c in cols) + " |",
             "| " + " | ".join("---" for _ in cols) + " |"]
    rendered_chars = sum(len(line) + 1 for line in lines)
    shown = 0
    for e in rows[:max_rows]:
        row = "| " + " | ".join(_cell(c, e.get(c)) for c in cols) + " |"
        # Keep at least one data row even when a single actionable path is
        # unusually long. Thereafter the response byte budget is the primary
        # bound: hundreds of long paths must not turn one chat answer into a
        # multi-megabyte transport or browser-rendering problem.
        if shown and rendered_chars + len(row) + 1 > max(1, max_chars):
            break
        lines.append(row)
        rendered_chars += len(row) + 1
        shown += 1
    return _table_text(lines, shown, len(rows), rendered)


def _read_content(result: dict, *, max_chars: int = _TABLE_MAX_CHARS) -> str:
    """Render text-reader output without replacing content with metadata."""
    scalar = isinstance(result.get("content"), str)
    entries = [result] if scalar else result.get("entries")
    if not isinstance(entries, list):
        return ""
    blocks: list[str] = []
    used = 0
    partial = False
    display_truncated = False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        content = next((entry.get(key) for key in _CONTENT_FIELDS
                        if isinstance(entry.get(key), str)), None)
        source = entry.get("path") or entry.get("url") or entry.get("name")
        unreadable = (entry.get("readable") is False
                      or bool(entry.get("parse_diagnostic") and not content))
        if unreadable:
            block = _msg("MSG_DOCUMENT_AUDIT_UNREADABLE",
                         files=source or _msg("MSG_OBJECT_FILES"))
        elif content is None:
            continue
        else:
            block = f"### {source}\n{content}" if source and not scalar else content
            partial |= (entry.get("truncated") is True
                        or any(entry.get(key) is False for key in
                               ("source_complete", "input_complete", "parse_complete")))
        remaining = max_chars - used - (2 if blocks else 0)
        blocks.append(block[:max(0, remaining + 1)])
        used += len(block) + (2 if len(blocks) > 1 else 0)
        if used > max_chars:
            display_truncated = True
            break
    rendered = "\n\n".join(blocks)
    # Top-level truncation is already announced by TurnLog. Entry-only
    # limits and the renderer's own cap otherwise have no user-facing notice.
    notice = (_msg("MSG_TRUNCATED_UNKNOWN")
              if (partial or display_truncated) and not result.get("truncated") else "")
    suffix = "\n\n" + notice if notice else ""
    if len(rendered) + len(suffix) > max_chars or display_truncated:
        budget = max(0, max_chars - len(suffix) - 1)
        return (rendered[:budget] + "…" + suffix)[:max_chars]
    return rendered + suffix


def _contract_entries_table(entries: list, contract: dict,
                            wanted: list | None = None, *,
                            rendered: dict | None = None) -> str:
    """Render a manifest-declared list projection with bounded dimensions.

    A declared column with no value in any row is not shown; ``wanted`` asks
    for exactly those declared columns instead ("" when one is not declared).
    """
    columns = contract.get("columns") or []
    max_rows, max_chars = _table_budget(int(contract.get("max_rows", _TABLE_MAX_ROWS)),
                                        int(contract.get("max_chars", _TABLE_MAX_CHARS)))

    def value(entry: object, spec: dict):
        source = spec.get("source", spec.get("key"))
        sources = source if isinstance(source, list) else [source]
        for key in sources:
            if key == "$entry":
                return entry
            if not isinstance(entry, dict):
                continue
            candidate = entry.get(key) if isinstance(key, str) else None
            if candidate not in (None, "", [], {}):
                return candidate
        return spec.get("fallback", "")

    if wanted is not None:
        declared = {str(spec.get("key")): spec for spec in columns}
        if not wanted or not all(name in declared for name in wanted):
            return ""
        columns = [declared[name] for name in wanted]
    else:
        filled = [spec for spec in columns
                  if any(not _blank(value(entry, spec)) for entry in entries)]
        columns = filled or columns

    def cell(entry: object, spec: dict) -> str:
        raw = _display_temporal(value(entry, spec), spec.get("type"))
        if isinstance(raw, (list, tuple, dict)):
            raw = json.dumps(raw, ensure_ascii=False, sort_keys=True)
        text = str(raw).replace("|", "\\|").replace("\n", " ").strip()
        limit = int(spec.get("cell_max", 180))
        text = text if len(text) <= limit else text[:limit - 1] + "…"
        # A manifest, not a domain branch, decides which compact identifiers
        # are indivisible in a table cell (for example a date or account).
        return (f"{_TABLE_NOWRAP_OPEN}{text}{_TABLE_NOWRAP_CLOSE}"
                if spec.get("nowrap") else text)

    headers = [str(spec.get("key")) for spec in columns]
    lines = ["| " + " | ".join(headers) + " |",
             "| " + " | ".join("---" for _ in headers) + " |"]
    used = sum(len(line) + 1 for line in lines)
    shown = 0
    for entry in entries[:max_rows]:
        line = "| " + " | ".join(cell(entry, spec) for spec in columns) + " |"
        if shown and used + len(line) + 1 > max_chars:
            break
        lines.append(line)
        used += len(line) + 1
        shown += 1
    return _table_text(lines, shown, len(entries), rendered)


def _entries_bullet_lines(entries: list, *, fields: tuple,
                          more_key: str, max_items: int = 20) -> str:
    """Render condiviso entries→bullet-list (#0 fonte unica). Primi 2-3 campi
    testuali per entry (cap 60 char/campo, 80 per scalare), tail i18n `more_key`
    quando si supera `max_items`. fields/more_key passati dai call-site per
    preservarne l'esatto comportamento."""
    lines = []
    for e in entries[:max_items]:
        if isinstance(e, dict):
            bits = []
            for k in fields:
                if k in e and e[k]:
                    bits.append(str(_display_temporal(e[k]))[:60])
                if len(bits) >= 3:
                    break
            lines.append("- " + " | ".join(bits))
        else:
            lines.append(f"- {str(e)[:80]}")
    more = len(entries) - max_items
    tail = ("\n" + _msg(more_key, more=more)) if more > 0 else ""
    return "\n".join(lines) + tail


# ── Runtime placeholder resolver ──────────────────────────────────────────

def _build_runtime_resolvers(ctx: dict) -> dict[str, str]:
    """Whitelist chiusa §7.9 dei valori runtime.

    Actor "host"/"guest" generico → display_name reale via users.db.
    """
    actor = str(ctx.get("actor") or "")
    if actor.lower() in ("host", "guest"):
        try:
            import config as _C
            udb = _C.PATH_USER_DATA / "users.db"
            if udb.exists():
                conn = sqlite3.connect(str(udb))
                row = conn.execute(
                    "SELECT name, display_name FROM users WHERE role=? LIMIT 1",
                    (actor.lower(),)).fetchone()
                conn.close()
                if row:
                    actor = row[1] or row[0] or actor
        except Exception:
            pass
    # Resolve actor_email da users.db (universal §7.9, no hardcoded)
    actor_email = ""
    try:
        import config as _C
        udb = _C.PATH_USER_DATA / "users.db"
        if udb.exists():
            conn = sqlite3.connect(str(udb))
            row = conn.execute(
                "SELECT email FROM users WHERE name=? OR display_name=? LIMIT 1",
                (actor, actor)).fetchone()
            conn.close()
            if row and row[0]:
                actor_email = row[0]
    except Exception:
        pass
    # §2.8 no-silent-failure: una chiave RUNTIME risolta a stringa vuota NON
    # va inserita nel resolver-map. Altrimenti `${RUNTIME:actor_email}` si
    # risolverebbe a "" → `_detect_unresolved_placeholders` non lo intercetta
    # (non è letterale) → un send con to="" partirebbe silenzioso. Omettendo
    # la chiave, il placeholder resta letterale e viene bloccato come irrisolto.
    # «la mia cartella di lavoro», «la mia area di lavoro»: the person's
    # working folder on this server. The planner writes it by analogy with
    # `${RUNTIME:actor}`; left unresolved the answer was «0» (11-13/11/2026).
    try:
        import person_files
        workspace = str(person_files.for_actor(ctx.get("actor")))
    except Exception:
        workspace = ""
    candidates = {
        "actor": actor,
        "actor_email": actor_email,
        "workspace": workspace,
        "actor_home": workspace,
        "lang": str(ctx.get("lang") or "it"),
        "channel": str(ctx.get("channel") or ""),
        "turn_id": str(ctx.get("turn_id") or ""),
    }
    return {k: v for k, v in candidates.items() if v != ""}


# Pattern matchers per placeholder TIME dinamici §7.9.
# Sintassi accettata (universal):
#   now_plus_30d / now_plus_30_days / now_plus_30days
#   now_minus_5h / now_minus_5_hours
_RUNTIME_TIME_NOW = re.compile(r"^now$")
_RUNTIME_TIME_TODAY = re.compile(r"^today$")
_RUNTIME_TIME_NOW_PLUS_DAYS = re.compile(r"^now_plus_(\d+)_?(?:d|days?)$")
_RUNTIME_TIME_NOW_MINUS_DAYS = re.compile(r"^now_minus_(\d+)_?(?:d|days?)$")
_RUNTIME_TIME_NOW_PLUS_HOURS = re.compile(r"^now_plus_(\d+)_?(?:h|hours?)$")
_RUNTIME_TIME_NOW_MINUS_HOURS = re.compile(r"^now_minus_(\d+)_?(?:h|hours?)$")


def _resolve_runtime_time_key(key: str, *, as_date: bool = False, now=None) -> str | None:
    """Universal §7.9: risolve placeholder TIME dinamici (now, today,
    now_plus_Nd, now_minus_Nh, ecc.) → ISO timestamp. Pattern in regex
    chiusa, deterministico, no LLM.

    The clock is the instance's zone: «today» is the person's day, not the
    UTC one (at half past midnight they differ).  ``as_date`` gives the day
    alone, for a plan that writes a time after the placeholder
    («${RUNTIME:now_plus_1d}T17:00:00+01:00» is tomorrow at 17).
    """
    import datetime as _dt
    from time_window_parser import temporal_now

    now = temporal_now(now)
    if _RUNTIME_TIME_NOW.match(key):
        moment = now
    elif _RUNTIME_TIME_TODAY.match(key):
        return now.date().isoformat()
    else:
        moment = None
        for pattern, unit, sign in ((_RUNTIME_TIME_NOW_PLUS_DAYS, "days", 1),
                                    (_RUNTIME_TIME_NOW_MINUS_DAYS, "days", -1),
                                    (_RUNTIME_TIME_NOW_PLUS_HOURS, "hours", 1),
                                    (_RUNTIME_TIME_NOW_MINUS_HOURS, "hours", -1)):
            m = pattern.match(key)
            if m:
                moment = now + sign * _dt.timedelta(**{unit: int(m.group(1))})
                break
        if moment is None:
            return None
    return moment.date().isoformat() if as_date else moment.isoformat()


# A time written right after a placeholder: the placeholder is the day.
_TIME_AFTER_PLACEHOLDER = re.compile(r"(?:T|\s+)\d{1,2}:\d{2}")


# Runtime values a plan may name with `${RUNTIME:key}` but that are not passed
# to every tool as hidden context.
_RESOLVED_ONLY = frozenset({"workspace", "actor_home"})


def _resolve_runtime_placeholders(args: dict, runtime_ctx: dict) -> dict:
    """Sostituisce ${RUNTIME:key} + inietta `_actor`/`_lang`/`_channel`
    come arg hidden (prefix `_`, mai emesso da LLM).

    Supporta:
      - static keys: actor, lang, channel (via _build_runtime_resolvers)
      - dynamic time keys: now, today, now_plus_Nd, now_minus_Nh, ecc.
    Ricorsivo su nested dict/list per gestire args.time_range, ecc.
    """
    if not runtime_ctx:
        return args
    resolvers = _build_runtime_resolvers(runtime_ctx)
    def _resolve_key(key: str, *, as_date: bool = False) -> str | None:
        # 1. Static resolver (actor/lang/channel)
        if key in resolvers:
            return resolvers[key]
        # 2. User prefs (W2 v1, ADR 0187): ${RUNTIME:pref_<chiave>} →
        #    users.get_pref dell'utente dell'actor. Best-effort: pref
        #    assente/attore ignoto → placeholder INTATTO (il chiamante vede
        #    il buco, §2.8 — mai stringa vuota silenziosa).
        if key.startswith("pref_"):
            try:
                import devices as _dev
                import users as _users
                _uid = _dev.owner_id_for_actor(
                    str(runtime_ctx.get("actor") or "host"))
                return _users.get_pref(_uid, key[5:], None)
            except Exception:
                return None
        # 3. Dynamic time resolver
        return _resolve_runtime_time_key(key, as_date=as_date)
    def _sub_str(v: str) -> str:
        m = _RUNTIME_RE.search(v)
        if not m:
            return v
        def _repl(mm):
            dated = _TIME_AFTER_PLACEHOLDER.match(v, mm.end()) is not None
            r = _resolve_key(mm.group(1), as_date=dated)
            return r if r is not None else mm.group(0)
        return _RUNTIME_RE.sub(_repl, v)
    def _sub_recursive(v):
        if isinstance(v, str):
            return _sub_str(v)
        if isinstance(v, dict):
            return {k: _sub_recursive(vv) for k, vv in v.items()}
        if isinstance(v, list):
            return [_sub_recursive(x) for x in v]
        return v
    out: dict = {}
    for k, v in args.items():
        out[k] = _sub_recursive(v)
    for ck, cv in resolvers.items():
        # The working folder only answers a placeholder: it is not context
        # every tool must receive (13/11/2026: it travelled as `_workspace`
        # in the arguments of every call, to a device too).
        if cv and ck not in _RESOLVED_ONLY:
            # Autorita' runtime: un valore omonimo presente nel piano non deve
            # mai prevalere sull'identita'/contesto autenticati.
            out[f"_{ck}"] = cv
    return out


# ── Dotted resolver con projection ────────────────────────────────────────

def _resolve_dotted(obj, path: str):
    """Traverse dict/list per dot-path. Supporta:
      - dict key: 'health.thermal'
      - list index: 'entries.0.name'
      - projection: 'entries.0.examples.*.image_path' → list
    """
    cur = obj
    parts = path.split(".")
    for i, part in enumerate(parts):
        if isinstance(cur, list) and part.isdigit():
            idx = int(part)
            if 0 <= idx < len(cur):
                cur = cur[idx]
            else:
                return None
        elif isinstance(cur, list) and part == "*":
            rest = ".".join(parts[i + 1:])
            if not rest:
                return list(cur)
            return [v for v in (_resolve_dotted(el, rest) for el in cur)
                    if v is not None]
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


_UNRESOLVED_PATTERNS = (
    re.compile(r"\$\{step\d+\.[^}]+\}"),
    re.compile(r"\$\{steps\.\d+\.[^}]+\}"),
    re.compile(r"\$\{RUNTIME:[^}]+\}"),
    re.compile(r"\$\{FILLER:[^}]+\}"),
)


def _detect_unresolved_placeholders(value, _seen=None) -> list[str]:
    """Universal §7.9: scan ricorsivo args per placeholder rimasti letterali.

    Ritorna lista di placeholder non risolti. Vuota = tutto OK.
    """
    found = []
    if isinstance(value, str):
        for pat in _UNRESOLVED_PATTERNS:
            for m in pat.findall(value):
                found.append(m)
    elif isinstance(value, dict):
        for v in value.values():
            found.extend(_detect_unresolved_placeholders(v))
    elif isinstance(value, list):
        for x in value:
            found.extend(_detect_unresolved_placeholders(x))
    return found


def _find_list_of_dicts(result: dict) -> list:
    """Trova prima list[dict] nel result (per @count fallback)."""
    if not isinstance(result, dict):
        return []
    for v in result.values():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return v
    return []


_BRIEF_ID_FIELDS = ("issue_number", "number", "id", "ref")
_BRIEF_TITLE_FIELDS = ("title", "summary", "subject", "name", "question_text")
_BRIEF_MAX_ITEMS = 3
_BRIEF_TITLE_CHARS = 40


def _brief_list(items: list, *, max_items: int = _BRIEF_MAX_ITEMS) -> str:
    """Riassunto BREVE di una lista di record: «#<id> <titolo>» per i primi
    `max_items`, poi «… (+N)». Generale cross-dominio (issue/mail/evento/…):
    id e titolo presi dai primi campi noti disponibili. Vuoto se la lista non e'
    di dict utilizzabili. Determinismo §7.9, no LLM."""
    if not isinstance(items, list) or not items:
        return ""
    parts: list[str] = []
    for it in items[:max_items]:
        if not isinstance(it, dict):
            continue
        ident = next((str(it[f]) for f in _BRIEF_ID_FIELDS
                      if it.get(f) not in (None, "")), "")
        title = next((str(it[f]) for f in _BRIEF_TITLE_FIELDS
                      if it.get(f) not in (None, "")), "")
        title = title.strip().replace("\n", " ")
        if len(title) > _BRIEF_TITLE_CHARS:
            title = title[:_BRIEF_TITLE_CHARS - 1].rstrip() + "…"
        label = (f"#{ident} {title}".strip() if ident
                 else title or f"#{ident}".strip())
        if label:
            parts.append(label)
    if not parts:
        return ""
    extra = len(items) - len(parts)
    out = "; ".join(parts)
    if extra > 0:
        out += f"… (+{extra})"
    return out


# ── from_step resolver ────────────────────────────────────────────────────

# Elenco canonico dei consumer planner-visible della pipeline di entries.
# Le due categorie alimentano anche la documentazione anti-drift. `filter_lists`
# richiede due sorgenti e quindi non usa l'auto-wire mono-lista qui sotto.
ENTRY_PIPELINE_STRUCTURAL = (
    "filter_entries", "filter_lists", "sort_entries", "group_entries",
    "compute_entries",
)
ENTRY_PIPELINE_SEMANTIC = (
    "classify_entries", "compare_entries", "extract_entries",
    "describe_entries",
)
ENTRY_PIPELINE_CONSUMERS = frozenset(
    ENTRY_PIPELINE_STRUCTURAL + ENTRY_PIPELINE_SEMANTIC)

# Helper universali mono-lista che consumano `entries` (auto-wire prev step).
_ENTRIES_CONSUMERS = ENTRY_PIPELINE_CONSUMERS - {"filter_lists"}

# Executor TRASFORMATIVI che consumano `entries` via un *_template/_field ma NON
# sono _ENTRIES_CONSUMERS: stesso bug (proposer emette content_template/path_template
# senza from_step → 0 entries → output vuoto → ok=False). Auto-wire entries quando
# c'è un template ma manca ogni sorgente-lista (bug q28 5/6).
_TEMPLATE_CONSUMERS = frozenset({"write_files", "move_files"})
_TEMPLATE_ARGS = ("content_template", "path_template", "dst_template", "content_field")


def _step_list_payload(step_result: dict):
    """Estrae la lista-payload prodotta da uno step, indipendentemente dalla
    chiave (§2.10): entries > results > lines > matches. Universale — l'auto-wire
    non deve dipendere dal nome-chiave del produttore (bug q45: filter_texts_lines
    ritorna `lines`, non `entries`)."""
    if not isinstance(step_result, dict):
        return None
    for _k in ("entries", "results", "lines", "matches"):
        _v = step_result.get(_k)
        if isinstance(_v, list) and _v:
            return _v
    return None


from from_step_projection import (  # noqa: E402
    CONTEXT_ERRORS_KEY as _FROM_STEP_CONTEXT_ERRORS_KEY,
    consumer_match_arg as _consumer_match_arg,
    from_step_alternatives as _from_step_alternatives,
    has_explicit_from_step_alternative as _has_explicit_from_step_alternative,
    without_restated_rows as _without_restated_rows,
    contradiction_refusal as _contradiction_refusal,
    project_from_entries as _project_from_entries,
)
from tabular_records import (  # noqa: E402
    has_explicit_column_sources as _has_explicit_column_sources,
    preserve_explicit_record_source as _preserve_explicit_record_source,
)


def _resolve_implicit_reverse_target(
    tool: str,
    args: dict,
    history: list[StepRun],
    consumer_schema=None,
) -> dict:
    """Bind a target only from a preceding executor's sealed undo contract.

    A planner may explicitly schedule an inverse action but omit its dataflow
    reference.  The runtime may repair that omission only when a committed
    prior result advertises the exact inverse executor and identifiers through
    the standard ``_undo`` envelope.  Explicit targets always win.
    """

    if not history or "from_step" in args:
        return args

    # A literal unresolved placeholder is not an explicit target. It is a
    # failed planner projection, and may be replaced only by a matching sealed
    # `_undo` receipt below. Keep every other argument unchanged. If no receipt
    # matches, return the original args so the standard unresolved-placeholder
    # guard can report/drop it according to the manifest contract.
    unresolved_alternatives = {
        name for name in _from_step_alternatives(consumer_schema)
        if name in args and _detect_unresolved_placeholders(args[name])
    }
    concrete_args = {
        key: value for key, value in args.items()
        if key not in unresolved_alternatives
    }
    if _has_explicit_from_step_alternative(concrete_args, consumer_schema):
        return args
    from reverse_patterns_patch import build_undo_calls

    properties = (
        consumer_schema.get("properties", {})
        if isinstance(consumer_schema, dict) else {}
    )
    if not properties:
        return args
    for previous in reversed(history):
        if not previous.ok:
            continue
        result = previous.result if isinstance(previous.result, dict) else {}
        undo = result.get("_undo")
        pattern = undo.get("reverse_pattern") if isinstance(undo, dict) else None
        if not isinstance(pattern, str) or not pattern:
            continue
        calls, error = build_undo_calls(pattern, result)
        matching = [call for call in calls if call.get("executor") == tool]
        if error or not matching:
            continue
        if len(matching) != 1 or not isinstance(matching[0].get("args"), dict):
            return args
        injected = matching[0]["args"]
        injected = {key: value for key, value in injected.items() if key in properties}
        return {**injected, **concrete_args} if injected else args
    return args


def _seed_entries(seed_steps) -> list:
    """Payload-lista del seed-state (ADR 0177 M1): le entries del primo
    seed-step CONSUMABILE (kind!="done") che ne ha (es. `@uploaded` → le foto
    allegate). [] se assente. Usata dal seed-wiring per decidere se il primo
    step reale può consumare un INPUT seminato. Gli step kind="done" (già
    eseguiti, continuazione dialogo) NON sono input da consumare: esclusi."""
    for s in (seed_steps or []):
        if getattr(s, "kind", "live") == "done":
            continue
        r = getattr(s, "result", None)
        if isinstance(r, dict):
            pl = _step_list_payload(r)
            if pl:
                return pl
    return []


def _seed_done_tools(seed_steps) -> set:
    """Insieme dei NOMI-TOOL seminati come kind="done" (già eseguiti in un turno
    precedente: continuazione di un dialogo). Il proposer, anche istruito via
    «FATTO FINORA», è un LLM e potrebbe ri-emetterli; la guardia dedup in
    Executor.run li salta. Vuoto se nessun done.

    Match per NOME-TOOL (non per shape-args): il proposer del turno di ripresa
    rigenera lo stesso PRODUTTORE con chiavi-arg diverse (es. `time_window`→
    `time_windows`+`size`, granularità a sua scelta) — è semanticamente la
    STESSA ri-esecuzione che il seed «done» rende superflua (il risultato è già
    nel seed, referenziato via from_step). Lo `step_idx` del seed fa sì che gli
    executor a valle (from_step=N) puntino comunque al risultato corretto."""
    out = set()
    for s in (seed_steps or []):
        if getattr(s, "kind", "live") == "done":
            t = getattr(s, "tool", "")
            if t:
                out.add(t)
    return out


_STEP_REF_RE = re.compile(r"(?:\$\{|\{\{)\s*step(\d+)")

# Consumer che serializzano VALORI gia' materializzati: il payload JSON puo'
# attraversare server→device in sicurezza. Diverso da get/read/move/compress,
# che dereferenziano path nel filesystem del producer e devono co-localizzare.
_DEVICE_VALUE_SINKS = frozenset({"write_files", "create_files_spreadsheet"})

# Trasformazioni che selezionano/riordinano/arricchiscono record senza
# dereferenziare o cambiare i loro path. Se girano sul server sopra un producer
# remoto, l'autorita' filesystem resta quella del producer.
_PATH_PRESERVING_TRANSFORMS = frozenset({
    "filter_entries", "sort_entries", "classify_entries", "compare_entries",
})


def _referenced_producer(step, history: list):
    """Producer esplicito `from_step` (o ultimo carrier per auto-wire)."""
    sa = getattr(step, "args", None)
    if not isinstance(sa, dict):
        return None
    fs = sa.get("from_step")
    if isinstance(fs, str) and fs.isdigit():
        fs = int(fs)
    if isinstance(fs, int):
        if 1 <= fs <= len(history):
            producer = history[fs - 1]
            if getattr(producer, "kind", None) != "skipped":
                return producer
        return None
    # Gli entries-consumer senza from_step vengono auto-cablati dall'ultimo
    # carrier a monte: replica la stessa scelta per la provenienza.
    if getattr(step, "tool", "") in _PATH_PRESERVING_TRANSFORMS:
        for prod in reversed(history):
            if getattr(prod, "kind", None) == "skipped":
                continue
            res = prod.result if isinstance(prod.result, dict) else {}
            if isinstance(res.get("entries"), list):
                return prod
    return None


def _referenced_steps(args: object, history: list) -> list:
    """The earlier steps these arguments read from, `from_step` first, then
    the `${stepN…}` / `{{stepN…}}` references in the order they are written."""
    if not isinstance(args, dict):
        return []
    numbers: list = []
    fs = args.get("from_step")
    if isinstance(fs, str) and fs.isdigit():
        fs = int(fs)
    if isinstance(fs, int) and not isinstance(fs, bool):
        numbers.append(fs)
    try:
        blob = json.dumps(args, default=str)
    except Exception:
        blob = str(args)
    numbers += [int(m.group(1)) for m in _STEP_REF_RE.finditer(blob)]
    steps: list = []
    for n in dict.fromkeys(numbers):
        if 1 <= n <= len(history):
            prod = history[n - 1]
            if getattr(prod, "kind", None) != "skipped" and prod not in steps:
                steps.append(prod)
    return steps


def _record_shapes(result: object) -> set | None:
    """The sets of readable field names the records of a result have; None
    when the result lists no records."""
    if not isinstance(result, dict):
        return None
    rows = next((result[k] for k in ("entries", "results")
                 if isinstance(result.get(k), list) and result[k]), None)
    if not rows or not all(isinstance(row, dict) for row in rows):
        return None
    return {frozenset(str(k) for k in row if not str(k).startswith("_"))
            for row in rows}


def _inherited_presentation(args: object, history: list, result: object):
    """The table of the step these records came from, when the step that
    passed them on declares none of its own.

    A step that selects or reorders records (a filter, a sort) returns them
    with the fields they had: they are shown as their producer shows them,
    whatever the two tools are called. A step that adds a field or keeps
    fewer returns other records, and is shown by the general rule.
    """
    if not isinstance(result, dict) or result.get("_presentation_contract"):
        return None
    shapes = _record_shapes(result)
    if not shapes:
        return None
    for producer in _referenced_steps(args, history):
        source = producer.result if isinstance(producer.result, dict) else {}
        contract = source.get("_presentation_contract")
        if contract and shapes <= (_record_shapes(source) or set()):
            return contract
    return None


def _identity_choice_obs(tool: str, args: dict, context_errors: list,
                         runtime_ctx: dict) -> dict | None:
    """needs_inputs asking which identity each ambiguous entry denotes.

    Only for a single vector projection whose unresolved entries all offer
    distinct identities (see from_step_projection._identity_slots).  The step
    has not run, so the callback carries the destination and conversation
    chosen by the runtime, never an observed host.
    """
    if len(context_errors) != 1 or not isinstance(context_errors[0], dict):
        return None
    arg = context_errors[0].get("arg")
    slots = context_errors[0].get("slots")
    if not isinstance(arg, str) or not arg or not isinstance(slots, list):
        return None
    dialog: list[dict] = []
    composed: list = []
    for position, slot in enumerate(slots, start=1):
        if not isinstance(slot, list):
            composed.append(slot)
            continue
        var = f"{arg}_{position}"
        dialog.append({
            "var": var,
            "prompt": _msg("MSG_ROUTE_DISAMBIG_PROMPT"),
            "schema": {"kind": "choice", "choices": [{
                "label": " ".join(part for part in (
                    option["name"], option["version"],
                    f"({option['value']})") if part),
                "value": option["value"],
            } for option in slot]},
        })
        composed.append({"var": var})
    if not dialog:
        return None
    callback = {
        "type": "resume_executor_with_values",
        "executor": tool,
        "args_base": {key: value for key, value in args.items() if key != arg},
        "list_args": {arg: composed},
        "conversation_id": str(runtime_ctx.get("conversation_id") or ""),
    }
    device = str(runtime_ctx.get("target_device") or "")
    if device and device != "server":
        callback["target_device"] = device
    return {"decision": "needs_inputs", "needs_inputs": {
        "title": _msg("MSG_ROUTE_DISAMBIG_TITLE"),
        "dialog": dialog, "fmt": "auto", "on_complete": callback,
        "timeout_s": 3600,
    }}


def _data_host_for_step(step, history: list, execution_host: str) -> str:
    """Autorita' dati dello step, distinta dall'host che esegue il codice."""
    if (execution_host == "server"
            and getattr(step, "tool", "") in _PATH_PRESERVING_TRANSFORMS):
        producer = _referenced_producer(step, history)
        if producer is not None:
            return getattr(producer, "data_host", None) \
                or getattr(producer, "host", "server")
    return execution_host


def _references_server_producer(step, history: list) -> bool:
    """True se lo step referenzia un PRODUCER girato sul SERVER (host='server'),
    via `from_step:N` O placeholder `${stepN…}`/`{{stepN…}}` (§4.1). Base della
    co-location data-locality: un consumer device_ok di dati prodotti sul server
    deve restare sul server (i path/entries non esistono sul device)."""
    sa = getattr(step, "args", None)
    if not isinstance(sa, dict):
        return False
    if getattr(step, "tool", "") in _DEVICE_VALUE_SINKS:
        return False
    return any((getattr(prod, "data_host", None)
                or getattr(prod, "host", "server")) == "server"
               for prod in _referenced_steps(sa, history))


def _resolve_from_step(args: dict, history: list[StepRun],
                       consumer_schema=None) -> dict:
    """Espande from_step: N → entries da step N (1-based), con proiezione
    consumer-arg (parità con agent_runtime.resolve_from_step Layer 4)."""
    if "from_step" not in args:
        return args
    args = _without_restated_rows(args)
    # Un target esplicito dichiarato dal manifest prevale sul piping: espandere
    # anche from_step potrebbe allargare involontariamente un'azione mutante.
    if _has_explicit_from_step_alternative(args, consumer_schema):
        return {k: v for k, v in args.items() if k != "from_step"}
    n = args.get("from_step")
    # Coercizione stringa numerica (parità col legacy): lo schema-guided emette
    # un int, ma un "1" non deve degradare a no-op silenzioso.
    if isinstance(n, str) and n.isdigit():
        n = int(n)
    if not isinstance(n, int) or n < 1 or n > len(history):
        return args
    src = history[n - 1].result
    # Selezione per PRESENZA+TIPO, non per verità: una lista vuota [] e' un
    # risultato valido a 0 elementi (§2.1) e NON deve cadere su 'results' o su
    # una lista stale non correlata. Prima `... or ...` testava la verità →
    # entries=[] (N=0 legittimo) veniva scartato.
    if isinstance(src.get("entries"), list):
        entries = src["entries"]
    elif isinstance(src.get("results"), list):
        entries = src["results"]
    else:
        entries = _find_list_of_dicts(src) or []
    out = {k: v for k, v in args.items() if k != "from_step"}
    # Manifest-driven projection shared with the legacy engine: vector payload
    # plus homogeneous scalar context (for example UID + mailbox account).
    out, _ = _project_from_entries(
        out, entries, consumer_schema, source_result=src)
    return out


# ── Stepref resolver con full-match type preservation ─────────────────────

def _entries_to_2d_matrix(entries: list) -> list[list]:
    """Universal §7.9: converte list[dict] in matrix 2D [headers + rows].
    Per spreadsheet write_X_spreadsheet (values arg).
    """
    if not entries or not isinstance(entries, list):
        return []
    # Use first entry keys as headers (excluding nested/list values)
    first = entries[0]
    if not isinstance(first, dict):
        return [[str(e)] for e in entries]
    headers = [k for k, v in first.items()
               if not isinstance(v, (dict, list))]
    matrix = [headers]
    for e in entries:
        if not isinstance(e, dict):
            continue
        row = []
        for h in headers:
            v = e.get(h, "")
            row.append(str(v) if v is not None else "")
        matrix.append(row)
    return matrix


def _normalize_values_carrier(args: dict) -> dict:
    """Materialize legacy values only when no record-source map owns them."""
    out = _preserve_explicit_record_source(args)
    values = out.get("values")
    if (not _has_explicit_column_sources(out)
            and isinstance(values, list) and values
            and isinstance(values[0], dict)):
        out = dict(out)
        out["values"] = _entries_to_2d_matrix(values)
    return out


from field_synonyms import resolve_dotted_with_synonyms as _resolve_dotted_with_synonyms_base


def _resolve_dotted_with_synonyms(obj, path: str):
    return _resolve_dotted_with_synonyms_base(obj, path, _resolve_dotted)


def _handed_rows(result: dict) -> Optional[int]:
    """How many rows a step hands to the next one; None when it returns none."""
    return next((len(result[key]) for key in ("entries", "results", "items")
                 if isinstance(result.get(key), list)), None)


def _cut_text(result: dict) -> str:
    """What a question must say when the rows it is about to act upon were
    cut by a ceiling: how many more there are, and in which folders."""
    if not isinstance(result, dict) or result.get("truncated") is not True:
        return ""
    from messages import get as message

    rows = _handed_rows(result)
    total = result.get("available_total")
    more = (total - rows if isinstance(total, int) and not isinstance(total, bool)
            and isinstance(rows, int) and total > rows else None)
    text = (message("MSG_CONSENT_GATE_CUT", n=more) if more is not None
            else message("MSG_CONSENT_GATE_CUT_ANY"))
    parts = [part for part in result.get("partitions") or ()
             if isinstance(part, dict) and part.get("partition")] \
        if isinstance(result.get("partitions"), list) else []
    cut = [str(part["partition"]) for part in parts if part.get("truncated") is True]
    if cut:
        from partitioned_runs import labels
        names = labels([str(part["partition"]) for part in parts])
        text += " " + message("MSG_CONSENT_GATE_CUT_FOLDERS",
                              labels=", ".join(names[name] for name in cut))
    return text


def _resolve_stepref_with_fallback(result: dict, path: str):
    """Universal §7.9: prova path diretto + synonym, poi fallback su
    entries projection.

    Standard executor output: {entries: [{field1, field2, ...}], ...}.
    Planner spesso emette `${stepN.field}` pensando sia top-level → fallback
    a `entries.*.field` (extract list di field values via projection).
    Coerente con executor manifest pattern.

    Special path "entries" by itself (when consumer is spreadsheet write):
    auto-convert to 2D matrix [headers + rows].

    Magic `@count` (cascata available_total → ok_count → used → len lista):
    consistente col render del messaggio finale, così un ARG può citare
    `${stepN.@count}` (es. prompt del consent-gate «N elementi pronti»).
    """
    if path == "@count":
        for k in ("available_total", "ok_count", "used"):
            v = result.get(k)
            if v is not None:
                return v
        for k in ("entries", "results", "items"):
            v = result.get(k)
            if isinstance(v, list):
                return len(v)
        lst = _find_list_of_dicts(result)
        return len(lst) if lst else 0
    if path == "@rows":
        # The rows this step hands to the next one: what a step that reads it
        # will act upon. `@count` says how many there are in all, which is
        # more when a ceiling cut the list (review of 16/11/2026: the question
        # before a move said 9 and 6 were moved).
        rows = _handed_rows(result)
        return rows if rows is not None else _resolve_stepref_with_fallback(result, "@count")
    if path == "@cut":
        # One sentence when a ceiling cut the rows of this step; empty otherwise.
        return _cut_text(result)
    if path == "@by_partition":
        # How many rows belong to each partition, for a prompt that must say
        # where something is about to happen. Empty when the rows carry none.
        from partitioned_runs import counts_text
        for k in ("entries", "results", "items"):
            v = result.get(k)
            if isinstance(v, list) and v:
                return counts_text(v, excluded_files=result.get("excluded_files"))
        return counts_text(_find_list_of_dicts(result), excluded_files=result.get("excluded_files"))
    if path == "@brief":
        # Riassunto BREVE per-item della lista prodotta (id + titolo), per i
        # prompt che devono dire COSA si sta per fare (es. consent-gate: «#53
        # Come cambio la lingua…»), non solo quanti. Generale cross-dominio:
        # id = primo fra (issue_number/number/id/ref), titolo = primo fra
        # (title/summary/subject/name/question_text). Cap a 3 item + «…».
        for k in ("entries", "results", "items"):
            v = result.get(k)
            if isinstance(v, list) and v:
                return _brief_list(v)
        lst = _find_list_of_dicts(result)
        return _brief_list(lst) if lst else ""
    direct = _resolve_dotted_with_synonyms(result, path)
    if direct is not None:
        return direct
    # entries↔results synonym (§2.6): gli executor TRASFORMATIVI (create/write/
    # move/set/delete) ritornano `results`, non `entries`. Un placeholder
    # ${stepN.entries.M.X} verso un produttore trasformativo va risolto su
    # `results` (e viceversa). Bug q38 5/6: create_issues_github→results, ma
    # set_issues_github pipava ${step1.entries.0.number} → non risolto.
    for _a, _b in (("entries.", "results."), ("results.", "entries.")):
        if path.startswith(_a):
            alt = _resolve_dotted_with_synonyms(result, _b + path[len(_a):])
            if alt is not None:
                return alt
    # Fallback metadata: molti executor espongono aggregati scalari in
    # `metadata` (total_count, total_size_gb, ...). Il proposer scrive spesso
    # `${stepN.total_size_gb}` invece di `${stepN.metadata.total_size_gb}`.
    # Cerca lo scalare in metadata PRIMA del fallback entries (che per uno
    # scalare ritornerebbe la lista path → "(N entries)" fuorviante).
    meta = result.get("metadata")
    if isinstance(meta, dict):
        mv = _resolve_dotted_with_synonyms(meta, path)
        if mv is not None:
            return mv
    # Sinonimi di CONTEGGIO: il proposer nomina il conteggio in molti modi
    # (`total_count`, `count`, `n_files`, `total`, `num_files`), ma l'executor
    # lo espone come `count`/`available_total`/`ok_count`. Qualunque placeholder
    # che CHIEDA un conteggio cade sulla stessa cascata di `@count` (§7.3: una
    # regola, non un mapping per-nome). Caso live «quanti file python»:
    # ${step1.total_count} non risolveva → final_message degenere → synth LLM
    # che tergiversa invece del numero esatto.
    _lp = path.lower()
    if "." not in path and ("count" in _lp or _lp in ("total", "totale", "n", "num")):
        for k in ("count", "available_total", "ok_count", "used"):
            v = result.get(k)
            if isinstance(v, int):
                return v
    # Fallback: prova entries.*.path (es. step1.urls → step1.entries.*.url)
    if "." not in path:
        # path è singolo field; prova singular form
        # plural → singular: "urls" → "url", "files" → "file", "paths" → "path"
        singular = path
        if path.endswith("s") and len(path) > 2:
            singular = path[:-1]
        # Try entries.*.singular
        val = _resolve_dotted(result, f"entries.*.{singular}")
        if val is not None and val != []:
            return val
        # Try entries.*.path (es. files → entries[*].path)
        val = _resolve_dotted(result, "entries.*.path")
        if val is not None and val != []:
            return val
        # Try entries.*.url
        val = _resolve_dotted(result, "entries.*.url")
        if val is not None and val != []:
            return val
    return None


def _render_embedded(val) -> str:
    """Rende un valore (lista/dict/scalare) come TESTO leggibile per la
    sostituzione EMBEDDED in un template-stringa (§output-format, no Python-repr
    user-facing). list[dict] → una riga per item (label + data + link);
    list scalari → CSV; dict → idem 1 item; scalare → str. Universale."""
    def _one(d):
        if not isinstance(d, dict):
            return str(d)
        label = next((d[k] for k in ("summary", "title", "name", "subject",
                                      "description", "path") if d.get(k)), None)
        when = next((d[k] for k in ("start", "date", "taken_at", "when",
                                    "datetime", "due") if d.get(k)), None)
        link = next((d[k] for k in ("htmlLink", "url", "link", "permalink")
                     if d.get(k)), None)
        parts = []
        if label:
            parts.append(str(label))
        if when:
            parts.append(f"({when})")
        if link:
            parts.append(str(link))
        if parts:
            return "• " + " ".join(parts)
        vis = {k: v for k, v in d.items()
               if not str(k).startswith("_") and k not in ("ok", "id", "uid")}
        return "• " + ", ".join(f"{k}: {v}" for k, v in vis.items())
    if isinstance(val, list):
        if not val:
            return ""
        if all(not isinstance(x, dict) for x in val):
            return ", ".join(str(x) for x in val)
        return "\n".join(_one(x) for x in val)
    if isinstance(val, dict):
        return _render_embedded([val])
    return str(val)


def _resolve_stepref(value: Any, history: list[StepRun], schema=None) -> Any:
    """Resolve native references and embedded text recursively.

    A reference item returning strings expands into an array of strings only
    when the consumer declares that item type. Literal containers, matrices
    and callers without a schema retain their existing meaning.
    """
    if isinstance(value, dict):
        properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
        return {k: _resolve_stepref(v, history, properties.get(k))
                for k, v in value.items()}
    if isinstance(value, list):
        item_schema = schema.get("items") if isinstance(schema, dict) else None
        strings = (isinstance(schema, dict) and schema.get("type") == "array"
                   and isinstance(item_schema, dict) and item_schema.get("type") == "string")
        out = []
        for item in value:
            resolved = _resolve_stepref(item, history, item_schema)
            if (strings and isinstance(item, str)
                    and (_STEPREF_RE.fullmatch(item.strip()) or _STEPSREF_RE.fullmatch(item.strip()))
                    and isinstance(resolved, list) and all(isinstance(x, str) for x in resolved)):
                out.extend(resolved)
            else:
                out.append(resolved)
        return out
    if not isinstance(value, str):
        return value
    # Try ${stepN.field} (1-indexed)
    fm = _STEPREF_RE.fullmatch(value.strip())
    if fm:
        n = int(fm.group(1))
        path = fm.group(2)
        if 1 <= n <= len(history):
            val = _resolve_stepref_with_fallback(history[n - 1].result, path)
            return val if val is not None else value
        return value
    # Try ${steps.N.field} (0-indexed array)
    fm2 = _STEPSREF_RE.fullmatch(value.strip())
    if fm2:
        n = int(fm2.group(1))
        path = fm2.group(2)
        if 0 <= n < len(history):
            val = _resolve_stepref_with_fallback(history[n].result, path)
            return val if val is not None else value
        return value
    # Embedded substitutions (mix in template strings). §output-format:
    # NIENTE Python-repr di list/dict in testo user-facing (es. body
    # send_messages con ${stepN.results}); _render_embedded → testo leggibile.
    def _sub_step(m):
        n = int(m.group(1))
        path = m.group(2)
        if 1 <= n <= len(history):
            val = _resolve_stepref_with_fallback(history[n - 1].result, path)
            return "" if val is None else _render_embedded(val)
        return ""
    def _sub_steps(m):
        n = int(m.group(1))
        path = m.group(2)
        if 0 <= n < len(history):
            val = _resolve_stepref_with_fallback(history[n].result, path)
            return "" if val is None else _render_embedded(val)
        return ""
    value = _STEPREF_RE.sub(_sub_step, value)
    value = _STEPSREF_RE.sub(_sub_steps, value)
    return value


# ── Filler resolver ───────────────────────────────────────────────────────

def _resolve_fillers(args: dict, fillers: dict,
                      llm_call: Optional[Callable], query: str) -> dict:
    """Sostituisce ${FILLER:name} con valore risolto via LLM fast +
    default. Niente cache complessa (può essere aggiunta in autopath)."""
    def _sub_one(m) -> str:
        """Risolvi OGNI ${FILLER:name} per sé (group del match), non il primo
        su tutti: un body con due filler distinti deve avere due valori, non il
        primo duplicato. Spec assente → lascia il placeholder letterale."""
        name = m.group(1)
        spec = fillers.get(name)
        if spec is None:
            return m.group(0)
        return _resolve_one_filler(name, spec, llm_call, query)

    out: dict = {}
    for k, v in args.items():
        if isinstance(v, str) and _FILLER_RE.search(v):
            out[k] = _FILLER_RE.sub(_sub_one, v)
        else:
            out[k] = v
    return out


def _resolve_one_filler(name: str, spec, llm_call: Optional[Callable],
                         query: str) -> str:
    """Singolo filler: LLM fast → default → ''."""
    # spec può essere FillerSpec o dict legacy
    if hasattr(spec, "prompt"):
        prompt = spec.prompt
        default = spec.default
    else:
        prompt = (spec or {}).get("prompt", "")
        default = (spec or {}).get("default", "")
    if llm_call and prompt:
        try:
            from messages import get as _msg  # §11 i18n
            sys_msg = _msg("MSG_FILLER_LLM_INSTRUCTION", name=name, prompt=prompt)
            ans = llm_call(sys_msg, query, max_tokens=20)
            ans = (ans or "").strip().split("\n")[0].strip()
            if ans:
                return ans
        except Exception:
            pass
    return default or ""


# ── Step condition (skip-guard) ───────────────────────────────────────────

def _mutating_input_is_empty(step: StepSpec, history: list[StepRun], *,
                             reference_steps: list[StepRun] | None = None) -> bool:
    """True se `step` e' un MUTANTE che CONSUMA una lista d'input (from_step /
    entries / template / entries-consumer) e tale lista e' VUOTA.

    Guard strutturale §7.3/§2.8 (no ad-hoc): un'azione/artefatto su 0 input
    (es. create_files_spreadsheet da 0 fatture, send a 0 destinatari) non va
    eseguita — niente file vuoto, niente falso successo, e il piano risulta
    INEFFICACE (non cachato da L0). I create STANDALONE (crea cartella X, senza
    input-lista) NON ricadono qui → nessun falso-positivo. Turn 36a40c35/
    3fd7add6/e591854e/71117eef."""
    from pipeline_effects import MUTATING_TOOL_PREFIXES
    tool = step.tool or ""
    if not any(tool.startswith(p) for p in MUTATING_TOOL_PREFIXES):
        return False
    args = step.args or {}
    fs = args.get("from_step")
    consumes = (fs is not None or "entries" in args
                or tool in _ENTRIES_CONSUMERS or tool in _TEMPLATE_CONSUMERS
                or any(k in args for k in _TEMPLATE_ARGS))
    if not consumes:
        return False  # mutante standalone (nessun input-lista) → mai skippare
    if fs is not None:
        try:
            idx = int(fs) - 1
        except (TypeError, ValueError):
            return False
        slots = history if reference_steps is None else reference_steps
        if 0 <= idx < len(slots):
            res = slots[idx].result
            return not _step_list_payload(res)
        return False
    if isinstance(args.get("entries"), list):
        return len(args["entries"]) == 0
    if history:  # auto-wire: ultimo producer
        res = history[-1].result if isinstance(history[-1].result, dict) else {}
        return not _step_list_payload(res)
    return False


def _step_condition_passes(step: StepSpec, history: list[StepRun], *,
                           reference_steps: list[StepRun] | None = None) -> bool:
    """Skip-guard di uno step. Due regole:
    1. opt-in: step.if_prev_entries_nonempty=True + producer referenziato
       vuoto (oppure ultimo step per il solo auto-wire).
    2. AUTO (strutturale): mutante che consuma una lista d'input VUOTA."""
    if step.if_prev_entries_nonempty:
        slots = history if reference_steps is None else reference_steps
        if not slots:
            return True
        producer = history[-1] if history else None
        source = (step.args or {}).get("from_step")
        if source is not None:
            try:
                source_index = int(source) - 1
            except (TypeError, ValueError):
                source_index = -1
            if 0 <= source_index < len(slots):
                producer = slots[source_index]
        if producer is not None and not _step_list_payload(producer.result):
            return False
    return not _mutating_input_is_empty(
        step, history, reference_steps=reference_steps)


# ── Final message renderer ────────────────────────────────────────────────

def _format_value(v) -> str:
    if v is None:
        return ""
    if isinstance(v, dict):
        items = [f"{k}={_format_value(vv)}" for k, vv in v.items()
                 if vv is not None and k != "available"]
        return " · ".join(items)
    if isinstance(v, list):
        # Universal §7.3: lista vuota non emette "(0 entries)" — produce
        # stringa vuota lasciando al renderer top-level usare i fallback
        # (attachments, summary auto-render).
        if not v:
            return ""
        return f"({len(v)} entries)"
    return str(v)



def _render_final_message(template: str, history: list[StepRun]) -> str:
    """Risolve ${stepN.path}. Magic:
      - @count: cascata available_total → ok_count → used → len(prima list dict)
      - .a.b.c: dot-path con projection `*`
    Se template vuoto e history non vuota, deriva fallback dal ultimo step.
    """
    if not template and history:
        # v2: auto-derive template per ultimo step.
        # Universal §7.9: se entries non vuote → render summary (auto entries
        # list, no placeholder count). Se result ha "summary" pre-rendered →
        # usa quello. Else count fallback.
        last_index = next((i for i in range(len(history), 0, -1)
                           if getattr(history[i - 1], "kind", None) != "skipped"), 0)
        if not last_index:
            return ""
        last = history[last_index - 1]
        r = last.result if isinstance(last.result, dict) else {}
        if isinstance(r.get("summary"), str) and r["summary"].strip():
            template = f"${{step{last_index}.summary}}"
        elif "entries" in r and isinstance(r["entries"], list) and r["entries"]:
            # Trigger auto-render via summary path (None → entries list)
            template = f"${{step{last_index}.summary}}"
        elif "value" in r:
            template = f"${{step{last_index}.value}}"
        else:
            template = f"${{step{last_index}.@count}}"
    if not template:
        return ""
    def _sub_one(result, path, *, step):
        if path == "@count":
            for k in ("available_total", "ok_count", "used"):
                v = result.get(k)
                if v is not None:
                    return str(v)
            for k in ("entries", "results", "items"):
                v = result.get(k)
                if isinstance(v, list):
                    return str(len(v))
            lst = _find_list_of_dicts(result)
            return str(len(lst)) if lst else "0"
        if path == "@shown":
            # Elementi MOSTRATI (≠ @count che privilegia available_total =
            # totale pre-cap): cascata used → len(lista) → ok_count. Usato
            # dagli header dei modi ranked (output_policy G: «i K mostrati»).
            v = result.get("used")
            if v is not None:
                return str(v)
            for k in ("entries", "results", "items"):
                v = result.get(k)
                if isinstance(v, list):
                    return str(len(v))
            v = result.get("ok_count")
            if v is not None:
                return str(v)
            lst = _find_list_of_dicts(result)
            return str(len(lst)) if lst else "0"
        if path == "@gallery_fallback":
            # G-mode presume foto LOCALI (gli attachments derivano dai path):
            # entries REMOTE (album/foto google_photos, nessun path) non
            # mostravano NULLA oltre al conteggio (turn 4fa8d6bd: «2» senza i
            # titoli). Nessuna entry con path renderizzabile → bullet dei campi
            # salienti; altrimenti "" (la gallery vera copre).
            entries = result.get("entries")
            if not (isinstance(entries, list) and entries):
                return ""
            if any(isinstance(e, dict)
                   and (e.get("path") or e.get("local_path"))
                   for e in entries):
                return ""
            return "\n\n" + _entries_bullet_lines(
                entries, fields=_BULLET_FIELDS_GALLERY_FALLBACK,
                more_key="MSG_RENDER_MORE_HIDDEN")
        if path == "@note":
            # Voce ONESTA dell'executor (`message`, i18n) come coda opzionale:
            # i final deterministici (G header, L table, S count) sostituiscono
            # la prosa LLM e senza questo canale una dichiarazione di perimetro/
            # limite §2.8 non arrivava all'utente (turn e2b0e529: «0 album»
            # senza dire che l'API Google vede SOLO l'app-created — Roberto:
            # «vede quelli condivisi?»). Assente → stringa vuota, zero rumore.
            v = result.get("message")
            return ("\n\n" + v.strip()
                    if isinstance(v, str) and v.strip() else "")
        if path == "@links":
            # Provenienza navigabile di un producer web/sites a monte. Solo
            # URL http(s), deduplicati; il renderer HTML li apre in una nuova
            # scheda. Nessun titolo fisso: evita una nuova stringa non-i18n.
            entries = result.get("entries")
            if not isinstance(entries, list):
                return ""
            links: list[str] = []
            seen: set[str] = set()
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                url = entry.get("url")
                if (not isinstance(url, str)
                        or not re.match(r"^https?://", url)
                        or url in seen):
                    continue
                seen.add(url)
                title = str(entry.get("title") or url)
                title = title.replace("[", "\\[").replace("]", "\\]")
                links.append(f"- [{title}]({url})")
            return "\n".join(links)
        if path == "@table":
            # L-mode (output_policy): entries → tabella markdown deterministica
            # + la nota @note in coda. Un producer che espone una
            # ``final_message_hint`` ha però già una presentazione canonica:
            # per una mutazione/creazione la tabella dei campi tecnici
            # (ok/path/kind/rows/...) non è il risultato utile in chat.
            _note = _sub_one(result, "@note", step=step)
            _hint = result.get("final_message_hint")
            if isinstance(_hint, str) and _hint.strip():
                rendered_hint = _hint.strip()
                if _note.strip() and _note.strip() not in rendered_hint:
                    rendered_hint += _note
                return rendered_hint
            entries = result.get("entries")
            if not (isinstance(entries, list) and entries):
                entries = result.get("results")
            if isinstance(entries, list) and entries:
                rendered = {}
                table = _entries_table(
                    entries,
                    presentation=result.get("_presentation_contract"),
                    rendered=rendered,
                )
                result["_rendered_table"] = rendered
                return table + _note
            zero = _deterministic_zero_result([step])
            if zero:
                return zero + _note
            return _sub_one(result, "@count", step=step) + _note
        if path == "@content":
            return _read_content(result)
        # Universal §7.9 fallback: prova path diretto, poi entries[*].field
        v = _resolve_stepref_with_fallback(result, path)
        # Se path richiesto è "summary" e None, auto-render entries list (§7.9)
        if v is None and path == "summary":
            entries = result.get("entries", [])
            if isinstance(entries, list) and entries:
                return _entries_bullet_lines(
                    entries, fields=_BULLET_FIELDS_DATED,
                    more_key="MSG_RENDER_MORE_HIDDEN")
        return _format_value(v)
    def _sub_step(m):
        n = int(m.group(1))
        path = m.group(2)
        if not (1 <= n <= len(history)):
            return ""
        return _sub_one(history[n - 1].result, path, step=history[n - 1])
    def _sub_steps(m):
        n = int(m.group(1))
        path = m.group(2)
        if not (0 <= n < len(history)):
            return ""
        return _sub_one(history[n].result, path, step=history[n])
    out = _STEPREF_RE.sub(_sub_step, template)
    out = _STEPSREF_RE.sub(_sub_steps, out)
    return out


# ── Framework hash (per excluded_hashes in recovery) ──────────────────────

# Registry hash→forma leggibile del piano (B15): l'hash sha nel prompt del
# Proposer e' un token OPACO che il modello ignora → challenger/retry usciva
# identico al piano escluso. Popolato a ogni compute_framework_hash (tutti i
# path che escludono un hash lo hanno calcolato qui in-process: guard/
# validator in dispatch, recovery, metis grammar-multi); gli hash di processi
# passati (anti_skills DB) restano non risolti e il render del Proposer
# degrada a conteggio onesto. Bounded LRU; deterministico §7.9 (derivato dal
# framework, nessun LLM).
from collections import OrderedDict as _OrderedDict

_HASH_SHAPES: "_OrderedDict[str, str]" = _OrderedDict()
_HASH_SHAPES_MAX = 512


def framework_shape_for_hash(h: str) -> Optional[str]:
    """Forma leggibile «tool(arg_keys) → tool2(...)» del framework con hash
    `h`, se hashato in questo processo; None altrimenti."""
    return _HASH_SHAPES.get(h)


def _framework_shape(fw: Framework) -> str:
    """Rende la STESSA informazione coperta dall'hash (tool sequence + args
    keys) in forma leggibile dal modello medio: shape diversa ⇔ piano
    materialmente diverso per l'esclusione."""
    parts = []
    for s in fw.steps:
        keys = ",".join(sorted((s.args or {}).keys()))
        parts.append(f"{s.tool}({keys})" if keys else s.tool)
    return " → ".join(parts)


def compute_framework_hash(fw: Framework) -> str:
    """Hash della SHAPE: tool sequence + args keys + template. NON include
    valori filler (dst_folder=Junk vs Spam sono stessa shape)."""
    minimal = {
        "steps": [
            {"tool": s.tool, "args_keys": sorted((s.args or {}).keys())}
            for s in fw.steps
        ],
        "final_message": fw.final_message,
    }
    blob = json.dumps(minimal, sort_keys=True, ensure_ascii=False)
    h = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
    _HASH_SHAPES[h] = _framework_shape(fw)
    _HASH_SHAPES.move_to_end(h)
    while len(_HASH_SHAPES) > _HASH_SHAPES_MAX:
        _HASH_SHAPES.popitem(last=False)
    return h


def compute_execution_fingerprint(fw: Framework) -> str:
    """Hash the executable plan, including argument values.

    ``compute_framework_hash`` deliberately identifies a reusable *shape* for
    caches and proposer exclusions.  Recovery needs a different identity: a
    reformulated search with the same tool and argument names is a new attempt,
    while changing only the final prose is not.  Keep this digest separate so
    the established cache contract remains unchanged.
    """
    executable = {
        "steps": [
            {
                "tool": step.tool,
                "args": step.args or {},
                "if_prev_entries_nonempty": bool(
                    step.if_prev_entries_nonempty
                ),
            }
            for step in fw.steps
        ],
        "fillers": {
            name: {
                "prompt": spec.prompt,
                "default": spec.default,
                "tier": spec.tier,
            }
            for name, spec in sorted((fw.fillers or {}).items())
        },
        "runtime_step_cap": int(fw.runtime_step_cap or 0),
    }
    blob = json.dumps(
        executable,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ── Query-specificity (condiviso L0 fastpath + L1 autopath) ───────────────
# Arg che LEGANO il piano alla singola query: se uno di questi ha un valore
# LITERAL (non un placeholder ${...}), il framework e' legato a QUELLA query
# e NON generalizza al cluster/intent. Lista CHIUSA (§2.2), due famiglie:
#   - content-bearing: il TESTO di ricerca dell'utente (find_* su
#     immagini/persone/url/messaggi testuali);
#   - finestra temporale RELATIVA (time_window/time_windows, §2.1
#     "today|last-Nd"): misura BGE-M3 12/6/2026 — il pivot temporale
#     («mail di oggi» vs «di ieri», cosine 0.9722) supera la soglia 0b
#     (0.92) PIU' delle parafrasi legittime (0.946-0.964): nessuna soglia
#     li separa, quindi un piano che pinna la finestra e' servibile SOLO
#     via hash 0a (replay esatto: la finestra relativa si ri-risolve
#     correttamente). I literal temporali ASSOLUTI (ISO) sono invece
#     non-cacheabili del tutto (fastpath._has_absolute_temporal_literal).
CONTENT_ARG_KEYS = frozenset({
    "query_text", "name", "names", "content", "query", "search", "search_text",
    "text_query", "body_contains", "subject_contains", "from_contains",
    "time_window", "time_windows",
    # File-target LOCATOR literali (16/6, turn 9805fb61): un piano che legge/
    # opera su FILE SPECIFICI bakeizzati (es. read_files_csv(paths=[
    # "/home/anthropic/fatture.csv"]), spesso path ALLUCINATO dal proposer) e'
    # legato a UNA query → non promuovibile a L1 ne' servibile via cosine 0b.
    # Un piano generale userebbe from_step/${...}. NB: `base_path` (radice di
    # RICERCA, riusabile in un cluster «file in /tmp») resta NON query-specific.
    "paths", "path",
    # Destinatari + contenuto messaggio OUTBOUND (1/7/2026): un piano send/
    # create con destinatario/oggetto/corpo/titolo LITERAL e' legato a UNA
    # query — servito via cosine 0b a una query vicina («manda a Mario» vs
    # «manda a Luigi», stessa classe del pivot 0.9722) manderebbe al
    # destinatario SBAGLIATO il testo di un'altra query. `_mutating_args_
    # grounded` non li vede (token solo numerici/slug). NB: send_messages li
    # annida in messages=[{to,subject,body}] → scansione RICORSIVA sotto.
    "to", "to_user", "cc", "bcc", "subject", "body", "body_html", "title",
    # Destinatari ACL share_* + contenuto evento/calendario (2/7/2026, review
    # Fable): stessa classe outbound — share_files(email=...) o create_events
    # (summary/attendees/location/description literal) serviti via cosine 0b
    # a una query vicina concederebbero l'ACL o creerebbero l'evento col
    # target/contenuto di un'ALTRA query. `_mutating_args_grounded` non li
    # copre (vede solo cifre e slug a/b, non email né testo libero).
    "email", "domain", "summary", "attendees", "location", "description",
    # Contesto editoriale passato ai renderer/summarizer. Quando deriva dalla
    # query (describe_entries.context) può contenere scope, esclusioni e numeri
    # che cambiano il significato del rapporto: sicuro su L0 exact-match, non
    # ereditabile da query soltanto simili.
    "context",
})

# Arg `pattern`/glob: content-bearing SOLO se NON universale (25/6, turn
# 73476663). `pattern="*.py"` deriva dalla parola «python» della query → 0a-only
# (servirlo via cosine a «quanti file ci sono» dava 448 invece di 980). Ma
# `pattern="*"`/`"*.*"` = «tutti i file», nessuna informazione di query →
# resta NON query-specific (riusabile per cosine). Gestito a parte da
# is_query_specific perche' il valore-universale e' un'eccezione al literal.
_GLOB_ARG_KEYS = frozenset({"pattern", "patterns", "glob"})
_UNIVERSAL_GLOBS = frozenset({"*", "*.*", "**", ""})


@dataclass(frozen=True)
class ArgTransform:
    """Voce del registro delle trasformazioni-arg deterministiche pre-esecuzione
    (§7.9), gemello di `dispatch.Guard`. Entry = DATI: la `fn` e' localizzata via
    (module, func) — lazy import nel driver, niente import-cycle. `scope`:
    "query-det" (riapplicabile a esecuzione E record L0, idempotente) |
    "exec-only" (dipende da runtime ctx/creds). `needs_schema`: la fn vuole
    `args_schema=` kwarg. `reads/writes` dichiarano cosa legge/scrive
    (incrociabili con arg_provenance, come i Guard). ADR 0177 T3 (estensione)."""
    name: str
    module: str
    func: str
    scope: str
    needs_schema: bool = False
    reads: frozenset = field(default_factory=frozenset)
    writes: frozenset = field(default_factory=frozenset)
    rationale: str = ""
    adr: str = ""


ARG_TRANSFORM_PIPELINE: tuple = (
    # ── query-deterministici: il segnale vive nella QUERY, non negli arg
    #    ereditati (champion L1 / piano cachato). Riapplicati a esecuzione
    #    (Executor.run) E al RECORD L0 (dispatch._maybe_record_fastpath), §2.8.
    ArgTransform("mail_account", "mail_account_resolver", "resolve_mail_account",
                 "query-det", reads=frozenset({"query"}),
                 writes=frozenset({"args.account"}),
                 rationale="«tutta la posta»->all, account nominato->quello (bug live 10-11/6/2026)",
                 adr="0163"),
    ArgTransform("from_contains", "from_contains_resolver", "resolve_from_contains",
                 "query-det", reads=frozenset({"query"}),
                 writes=frozenset({"args.where_field", "args.where_value", "args.where_in"}),
                 rationale="predicato «che contiene X» -> where_* (STOP-list conservativa)"),
    ArgTransform("read_format", "read_format_resolver", "resolve_read_format",
                 "query-det", needs_schema=True,
                 reads=frozenset({"query", "args.entries", "args.paths",
                                  "args_schema"}),
                 writes=frozenset({"args.parse", "args.deduplicate_content"}),
                 rationale="collezione locale PDF/DOCX/XLSX/CSV -> parse auto; dedup logica se richiesta"),
    ArgTransform("junk_mail", "junk_mail_resolver", "resolve_junk_mail",
                 "query-det", reads=frozenset({"query"}),
                 writes=frozenset({"args.where_field", "args.where_in"}),
                 rationale="«posta indesiderata»->filtro category_hints bulk (23/6/2026)"),
    ArgTransform("time_window", "time_window_resolver", "resolve_time_window",
                 "query-det", needs_schema=True,
                 reads=frozenset({"query", "args_schema"}),
                 writes=frozenset({"args.time_window", "args.mtime_after",
                                   "args.mtime_before"}),
                 rationale="«ultime 24 ore»->last-24h o bound mtime; slot query-specific, non ereditato"),
    ArgTransform("photo_fields", "photo_fields_resolver", "resolve_photo_fields",
                 "query-det", reads=frozenset({"query", "dl:photo.metadata_fields"}),
                 writes=frozenset({"args.fields"}),
                 rationale="sinonimi NL metadata foto -> enum canonico via detection_lexicon (7/7/2026)"),
    ArgTransform("unique_rows", "unique_rows_resolver", "resolve_unique_rows",
                 "query-det", needs_schema=True,
                 reads=frozenset({"query", "args_schema",
                                  "dl:tabular.unique_rows_request"}),
                 writes=frozenset({"args.unique_rows"}),
                 rationale="vincolo esplicito di unicita' righe -> flag schema, indipendente dal planner"),
    ArgTransform("install_direction", "install_direction_resolver",
                 "resolve_install_direction",
                 "query-det", needs_schema=True,
                 reads=frozenset({"query", "args_schema",
                                  "dl:packages.uninstall_request"}),
                 writes=frozenset({"args.uninstall"}),
                 rationale="direzione mettere/togliere dalla richiesta: misurata 12/12 sbagliata dal modello (ADR 0209, 17/8/2026)"),
    ArgTransform("filter_field", "filter_field_resolver", "resolve_filter_field",
                 "query-det", reads=frozenset({"args.entries",
                                                "args.name_regex"}),
                 writes=frozenset({"args.name_regex", "args.where_field",
                                   "args.where_regex"}),
                 rationale="name_regex su record senza name -> unico campo scalare compatibile, altrimenti fail-closed"),
    # ── execution-only: dipendono da runtime ctx/creds/actor -> NON riapplicabili
    #    al record L0 (resterebbero legati allo stato del turno).
    ArgTransform("backend_arg", "backend_resolver", "resolve_backend_arg",
                 "exec-only", needs_schema=True,
                 reads=frozenset({"query", "args_schema", "creds"}),
                 writes=frozenset({"args.client"}),
                 rationale="provider multi-backend risolto dal runtime (default creds + esplicito query), LLM-invisibile",
                 adr="0155"),
    ArgTransform("self_recipient", "self_recipient_resolver", "resolve_self_recipient",
                 "exec-only", reads=frozenset({"query", "actor"}),
                 writes=frozenset({"args.to"}),
                 rationale="send senza destinatario esterno -> actor («inviami»/«mia email» = identità)",
                 adr="0163"),
    ArgTransform("calendar", "calendar_resolver", "resolve_calendar",
                 "exec-only", reads=frozenset({"query", "creds"}),
                 writes=frozenset({"args.calendar_id"}),
                 rationale="quale calendario fra gli owned (default primary) = config runtime, non LLM",
                 adr="0136"),
)


def _load_arg_transform_module(module_name: str):
    """Load one compiled transform module without an open import capability."""

    if module_name == "backend_resolver":
        import backend_resolver
        return backend_resolver
    if module_name == "calendar_resolver":
        import calendar_resolver
        return calendar_resolver
    if module_name == "filter_field_resolver":
        import filter_field_resolver
        return filter_field_resolver
    if module_name == "from_contains_resolver":
        import from_contains_resolver
        return from_contains_resolver
    if module_name == "install_direction_resolver":
        import install_direction_resolver
        return install_direction_resolver
    if module_name == "junk_mail_resolver":
        import junk_mail_resolver
        return junk_mail_resolver
    if module_name == "mail_account_resolver":
        import mail_account_resolver
        return mail_account_resolver
    if module_name == "photo_fields_resolver":
        import photo_fields_resolver
        return photo_fields_resolver
    if module_name == "read_format_resolver":
        import read_format_resolver
        return read_format_resolver
    if module_name == "self_recipient_resolver":
        import self_recipient_resolver
        return self_recipient_resolver
    if module_name == "time_window_resolver":
        import time_window_resolver
        return time_window_resolver
    if module_name == "unique_rows_resolver":
        import unique_rows_resolver
        return unique_rows_resolver
    raise ValueError("unsupported argument-transform module")


def apply_arg_transforms(tool: str, args: dict, query: str, *, scope: str,
                         args_schema: Optional[dict] = None) -> dict:
    """Driver UNICO del registro ArgTransform (gemello del loop GUARD_PIPELINE):
    applica in ordine le entry del `scope` dato. Best-effort per entry (noop
    loggato, come il cablaggio precedente). Lazy import via (module, func)."""
    for t in ARG_TRANSFORM_PIPELINE:
        if t.scope != scope:
            continue
        try:
            fn = getattr(_load_arg_transform_module(t.module), t.func)
            args = (fn(tool, args, query, args_schema=args_schema)
                    if t.needs_schema else fn(tool, args, query))
        except Exception as _e:
            log.debug("%s noop: %r", t.name, _e)
    return args


def resolve_query_canonical_args(tool: str, args: dict, query: str,
                                 args_schema: Optional[dict] = None) -> dict:
    """Catena dei resolver QUERY-DETERMINISTICI (§7.9): ri-risoluzione degli
    slot query-specific dalla query ATTUALE.

    Un piano servito da un layer la cui query d'origine ≠ query attuale
    (L1 champion, L0 0b) e' un template di STRUTTURA: gli arg che dipendono
    dalla query (account mail, time_window) NON si ereditano verbatim, si
    ri-riempiono (bug live 11/6/2026: «controlla tutte le mie mailbox ultime
    24 ore» serviva il champion di «mail di metnos» → account singolo, zero
    finestra). Su L0 0a (query identica) la ri-risoluzione e' no-op.

    Qui SOLO i resolver puri (query, config istanza) → riapplicabili sia a
    ESECUZIONE (Executor.run, ogni layer) sia a RECORD (dispatch.
    _maybe_record_fastpath: lo store L0 riflette cio' che esegue, §2.8).
    NON qui: backend/self_recipient/calendar resolver — dipendono da runtime
    ctx (actor) o da stato creds, restano execution-only in Executor.run.
    Ogni resolver e' best-effort: il fallimento non blocca (noop loggato).
    """
    return apply_arg_transforms(tool, args, query, scope="query-det",
                                args_schema=args_schema)


def bakes_query_number(steps_args, query: str) -> bool:
    """A number of the request is written in an argument that is not content.

    ``steps_args`` are the argument objects of the plan's steps. Such a plan
    answers that sentence only: «cancella il task 40» must never be served the
    plan of «cancella il task 42». Content and glob keys are left out: their
    literals already bind the plan to its sentence (`is_query_specific`) and
    the replay resolves them again."""
    numbers = set(re.findall(r"\d+", query or ""))
    if not numbers:
        return False
    exempt = CONTENT_ARG_KEYS | _GLOB_ARG_KEYS
    for args in steps_args or ():
        for key, value in (args.items() if isinstance(args, dict) else ()):
            if key in ("from_step", "from_steps") or key in exempt:
                continue
            if numbers & set(re.findall(r"\d+", str(value))):
                return True
    return False


def is_query_specific(framework_json: str) -> bool:
    """True se il framework incorpora un arg content-bearing LITERAL (non
    placeholder ${...}) → legato alla singola query.

    Conseguenze per i due layer con stato:
      - L1 autopath: non promuovibile a skill di cluster (avvelenerebbe le
        query sorelle col piano congelato di UNA query).
      - L0 fastpath: servibile SOLO via hash 0a con verifica della query
        originale integra, MAI via cosine 0b (query simile ma semanticamente
        diversa riuserebbe i literal sbagliati: «foto di X» vs «foto di Y»).
    Deterministico (§7.9): nessun LLM, solo ispezione args."""
    try:
        d = json.loads(framework_json)
    except Exception:
        return False

    def _has_fixed_value(value) -> bool:
        # Gli ingressi vettoriali possono usare qualsiasi nome di argomento.
        # Solo riferimenti interamente dinamici sono riusabili su altre query;
        # numeri, valori vuoti e testo misto restano dati del turno originale.
        if isinstance(value, str):
            return re.fullmatch(r"\$\{[^{}]+\}", value) is None
        if isinstance(value, dict):
            return not value or any(_has_fixed_value(v) for v in value.values())
        if isinstance(value, list):
            return not value or any(_has_fixed_value(v) for v in value)
        return True

    def _content_in(obj) -> bool:
        """Scansione RICORSIVA (1/7/2026): i campi content-bearing possono
        vivere ANNIDATI (send_messages: messages=[{to,subject,body}]) — il
        check top-level-only li mancava (qspec=0 → 0b servibile)."""
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in CONTENT_ARG_KEYS:
                    for item in (v if isinstance(v, list) else [v]):
                        if (isinstance(item, str) and item.strip()
                                and "${" not in item):
                            return True
                if _content_in(v):
                    return True
            return False
        if isinstance(obj, list):
            return _has_fixed_value(obj)
        return False

    for step in (d.get("steps") or []):
        if not isinstance(step, dict):
            continue
        args = step.get("args") or {}
        if not isinstance(args, dict):
            continue
        # Step iniettato dalla clausola «ordina/raggruppa per X»
        # (ordering_clause.apply_to_framework): la chiave di presentazione
        # deriva dalla query → il piano vale solo per quella query esatta
        # (0a-only); una query SIMILE senza clausola non deve ereditare
        # l'ordinamento via cosine 0b.
        if args.get("_ordering_clause"):
            return True
        if _content_in(args):
            return True
        # Glob CONCRETO (non universale): query-specific. `*.py`/`*.md` derivano
        # dal tipo-file nominato nella query → 0a-only. `*`/`*.*` no.
        for k in _GLOB_ARG_KEYS:
            v = args.get(k)
            for item in (v if isinstance(v, list) else [v]):
                if (isinstance(item, str) and "${" not in item
                        and item.strip().lower() not in _UNIVERSAL_GLOBS):
                    return True
    return False


# ── Main execution ────────────────────────────────────────────────────────

_PLACEHOLDER_RE = re.compile(r"\$\{[^}]+\}|\{\{[^}]+\}\}")


def _render_is_degenerate(template: str, rendered: str) -> bool:
    """True se il render del final_message è degenere: il template conteneva
    placeholder `${stepN.x}`/`{{stepN.x}}` ma il risultato ha buchi (sostituiti
    a vuoto) → frase monca tipo "Sono le ." o "Ho scritto  byte".

    Universal §2.8 (no silent failure): una risposta con un placeholder reso
    vuoto NON è una risposta onesta. Caso scoperto 31/5: get_now → template
    "Sono le ${step1.X}" con X errato → "Sono le ." (ora mancante).

    NON degenere: template senza placeholder (testo statico legittimo), o
    render pieno. Il caso entries/count è gestito a parte (auto-list).
    """
    if not template or not _PLACEHOLDER_RE.search(template):
        return False  # nessun placeholder → niente da risolvere, non degenere
    r = (rendered or "").strip()
    if not r:
        return True  # tutto vuoto
    # Segnale più forte: un placeholder è rimasto LETTERALE nel rendered
    # (es. "${1.@count}" formato non-standard non risolto da _STEPREF_RE,
    # o ${stepN.x} fuori range). Una risposta con `${...}`/`{{...}}` visibile
    # all'utente è sempre degenere.
    if _PLACEHOLDER_RE.search(r):
        return True
    # Artefatto "(N entries)": _format_value su una lista quando un campo
    # scalare del template non si e' risolto (es. ${step1.uuid} → fallback a
    # entries → "(1 entries)"). Mai contenuto reale → degenere, sintetizza
    # dall'observation (§2.8 no silent failure).
    if re.search(r"\(\d+ entries\)", r):
        return True
    # Rimuovi punteggiatura/whitespace residui: se resta quasi nulla rispetto
    # alle parti statiche del template, il placeholder è stato perso.
    # Heuristica: il template senza i placeholder dà le parti statiche; se il
    # rendered == solo-parti-statiche (i placeholder hanno reso ""), è monco.
    static_only = _PLACEHOLDER_RE.sub("", template).strip()
    # normalizza spazi multipli
    norm = lambda s: re.sub(r"\s+", " ", s).strip(" .,:;-—–\t\n")
    return norm(r) == norm(static_only) and bool(norm(static_only) != norm(template))


# Presentazione personale — primo punto d'effetto delle preferenze utente.
# Le chiavi `reply_length`, `tone` e `units` esistono da tempo in `user_prefs`
# (`users.py:633`) e non erano lette da nessun percorso di risposta: erano uno
# store senza consumatori. Qui viene cablata la SOLA lunghezza, che ha un
# effetto interamente deterministico e misurabile: quante voci entrano in un
# elenco e quanto può essere lunga la sintesi finale.
#
# `tone` e `units` restano dichiarati senza effetto, e non per dimenticanza:
# il tono richiede una variabile nel prompt del compositore, che è testo
# rivolto al modello e non si tocca da qui; le unità richiedono un insieme
# CHIUSO di campi convertibili, che oggi è vuoto. Insieme vuoto = nessun
# effetto, dichiarato invece che finto (§2.8).
_REPLY_BULLETS = {"breve": 6, "normale": 20, "dettagliata": 40}
_REPLY_TOKENS = {"breve": (220, 380), "normale": (360, 700),
                 "dettagliata": (700, 1200)}


def _presentation_bullets(presentation: dict | None) -> int:
    """Numero massimo di voci in un elenco, secondo la preferenza utente."""
    key = (presentation or {}).get("reply_length") or "normale"
    return _REPLY_BULLETS.get(key, _REPLY_BULLETS["normale"])


def _presentation_tokens(presentation: dict | None, *, wide: bool) -> int:
    """Tetto d'uscita della sintesi finale. `wide` conserva la distinzione
    esistente fra risposta breve e risposta su più osservazioni."""
    key = (presentation or {}).get("reply_length") or "normale"
    narrow_cap, wide_cap = _REPLY_TOKENS.get(key, _REPLY_TOKENS["normale"])
    return wide_cap if wide else narrow_cap


def _finalize_answer_text(framework, steps: list, query: str,
                          llm_fast, presentation: dict | None = None, *,
                          empty_mutation_skipped: bool = False,
                          reference_steps: list[StepRun] | None = None) -> str:
    """FINALIZER unico (ADR 0177 T5, CP2·M2): l'UNICA fonte del testo di un
    turno `answer`. Strategia dichiarata, in ordine:

      1. presentazione canonica della ricevuta d'effetto terminale, se
         dichiarata; altrimenti RENDER del template del proposer
         (`${stepN.*}`) sulle observation;
      2. arricchimento COUNT-ONLY→bullets: render vuoto/solo-conteggio ma
         l'ultimo step ha `entries` → lista puntata onesta (§2.7,
         `MSG_RENDER_AND_MORE` per il resto oltre il cap);
      3. render VUOTO o DEGENERE (placeholder perso/letterale, §2.8) →
         (a) zero-result DETERMINISTICO i18n (`MSG_NO_RESULTS`: mai una call
         LLM per dire «niente trovato»), altrimenti (b) sintesi LLM fast
         dalle observation (`_synthesize_final_from_steps`).

    Era duplicato in DUE punti (S5, ADR 0177: step `final_answer` e fallback
    post-loop) già DIVERGENTI: il fallback non arricchiva i count-only né
    passava dai bullets §2.7. i18n:
    ogni stringa runtime passa da `MSG_*`; il template del proposer è testo
    generato nella lingua utente (non una stringa runtime).

    NB: `output_policy` (modo di presentazione) opera PRE-esecuzione sul
    framework (dispatch.normalize_terminal), non qui; `describe_entries` è
    uno STEP le cui observation alimentano il render — non fonti parallele."""
    # 0. RIDUZIONE terminale (compute_entries → scalare): presentazione i18n
    #    AUTORITATIVA server-side (§7.9/§7.13). Precede il render perché il
    #    template del proposer sarebbe il valore NUDO («695132») o un framing
    #    stantìo rispetto al piano riscritto («N directory», turn 5cdf80d0);
    #    l'executor gira in sandbox senza i18n → la presentazione vive qui.
    scalar = _deterministic_scalar_result(steps)
    if scalar:
        return scalar
    # A pre-execution template cannot turn an observed no-op into a performed
    # action, or overstate a partial effect. The terminal executor owns its
    # localized receipt; read-only formatting keeps the existing render path.
    receipt = _last_self_presentation(steps, effect_receipt_only=True)
    if receipt:
        return receipt
    if empty_mutation_skipped:
        # A skipped branch does not erase effects completed by other branches.
        from pipeline_effects import pipeline_effect_counts

        mutations = 0
        for step in reversed(steps):
            counts = pipeline_effect_counts([step])
            if counts and counts["mutations"]:
                hint = _last_self_presentation([step])
                if hint:
                    return hint
                mutations += counts["mutations"]
        if mutations:
            return _msg("MSG_DEGENERATE_FINAL_MUTATIONS", n=mutations)
        return _deterministic_zero_result(steps) or _msg("MSG_NO_RESULTS")
    # A terminal content projection carries external text, not another
    # template.  Do not expand literal ${...}/{{...}} found in a file or
    # reinterpret numeric/empty content as a count or failed rendering.
    content_ref = _STEPREF_RE.fullmatch(framework.final_message or "")
    slots = steps if reference_steps is None else reference_steps
    if content_ref and content_ref.group(2) == "@content":
        position = int(content_ref.group(1)) - 1
        if 0 <= position < len(slots) and slots[position].ok:
            result = slots[position].result
            if isinstance(result, dict):
                content = _read_content(result)
                if content or isinstance(result.get("content"), str):
                    return content
    rendered = _render_final_message(framework.final_message, slots)
    # 2. count-only → bullets (universal §7.9)
    if steps:
        last_res = steps[-1].result
        entries = (last_res.get("entries") if isinstance(last_res, dict)
                   else None)
        if isinstance(entries, list) and entries:
            is_count_only = (
                not rendered.strip()
                or re.fullmatch(
                    r"[\(\s]*\d+\s*(?:elementi|entries|elements|voci)?\s*[\)\s]*",
                    rendered.strip())
            )
            if is_count_only:
                bullets = _entries_bullet_lines(
                    entries, fields=_BULLET_FIELDS,
                    more_key="MSG_RENDER_AND_MORE",
                    max_items=_presentation_bullets(presentation))
                rendered = ((rendered.strip() + "\n\n")
                            if rendered.strip() else "") + bullets
    # 3. vuoto/degenere → zero-result, self-presentazione, poi synth LLM.
    #    (la riduzione scalare terminale è già gestita al passo 0.)
    if (not rendered.strip()
            or _render_is_degenerate(framework.final_message, rendered)):
        zero = _deterministic_zero_result(steps)
        if zero:
            return zero
        hint = _last_self_presentation(steps)
        if hint:
            return hint
        synth = _synthesize_final_from_steps(
            query, steps, llm_fast, presentation=presentation)
        if synth:
            return synth
    return rendered


def _last_self_presentation(steps: list, *,
                            effect_receipt_only: bool = False) -> str:
    """`final_message_hint` dell'ultimo step produttivo (non final_answer), se
    presente e non-degenere. È la presentazione canonica che il produttore fa
    del proprio output (§7.9 deterministico). "" se nessuno si auto-presenta."""
    for s in reversed(steps or []):
        if (getattr(s, "tool", "") or "") == "final_answer":
            continue
        res = getattr(s, "result", None)
        if effect_receipt_only:
            metadata = res.get("_undo") if isinstance(res, dict) else None
            outcome = metadata.get("outcome") if isinstance(metadata, dict) else None
            if not isinstance(outcome, str) or outcome not in UNDO_OUTCOMES:
                return ""
        hint = res.get("final_message_hint") if isinstance(res, dict) else None
        if isinstance(hint, str) and hint.strip() \
                and not hint.lstrip().startswith("<missing:"):
            return hint.strip()
        return ""
    return ""


# Chiavi il cui valore è un peso in BYTE → presentazione umana (KB/MB/…).
_SIZE_KEYS_FMT = frozenset({"size", "total_bytes", "size_bytes", "bytes",
                            "filesize", "file_size"})


def _human_bytes(n) -> str:
    """Formato umano deterministico (unità-simbolo, language-neutral)."""
    try:
        v = float(n)
    except (TypeError, ValueError):
        return str(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(v) < 1024.0 or unit == "TB":
            return f"{int(v)} {unit}" if unit == "B" else f"{v:.1f} {unit}"
        v /= 1024.0
    return f"{v:.1f} TB"


def _deterministic_scalar_result(steps) -> str:
    """Presentazione i18n (§7.13, processo server) del risultato di una
    RIDUZIONE terminale (compute_entries op numerico → scalare). Byte-aware
    quando la key è una dimensione. "" se l'ultimo step produttivo non è una
    riduzione scalare valida (lascia decidere ai fallback a valle)."""
    for s in reversed(steps or []):
        tool = getattr(s, "tool", "") or ""
        if tool == "final_answer":
            continue
        res = getattr(s, "result", None)
        if tool != "compute_entries" or not isinstance(res, dict):
            return ""
        op = str(res.get("op") or "").lower()
        val = res.get("value")
        if op not in ("sum", "avg", "min", "max") or val is None:
            return ""
        key = str(res.get("key") or "")
        count = res.get("count_used")
        if not isinstance(count, int):
            count = res.get("count_input") or 0
        if op == "sum" and key.lower() in _SIZE_KEYS_FMT:
            # Contesto onesto (§2.8): «peso cartella» con molte sottocartelle
            # (spesso vuote) sorprende se si mostra solo il conteggio file. Il
            # produttore find_files espone `visited` = sottocartelle esplorate.
            dirs = _folder_subdir_count(steps)
            if dirs:
                return _msg("MSG_COMPUTE_SIZE_TOTAL_DIRS",
                            human=_human_bytes(val), bytes=int(val),
                            count=count, dirs=dirs)
            return _msg("MSG_COMPUTE_SIZE_TOTAL", human=_human_bytes(val),
                        bytes=int(val), count=count)
        return _msg("MSG_COMPUTE_RESULT", op=op, field=key, value=val,
                    count=count)
    return ""


def _folder_subdir_count(steps) -> int:
    """Sottocartelle esplorate dal produttore find_files/find_dirs (metadata
    `visited`), per il contesto della risposta-dimensione. 0 se non disponibile."""
    for s in reversed(steps or []):
        if (getattr(s, "tool", "") or "") in ("find_files", "find_dirs"):
            res = getattr(s, "result", None)
            if isinstance(res, dict):
                md = res.get("metadata")
                v = md.get("visited") if isinstance(md, dict) else None
                if isinstance(v, int) and v > 0:
                    return v
            return 0
    return 0


def _synthesize_final_from_steps(
    query: str,
    steps: list,
    llm_fast,
    *,
    presentation: dict | None = None,
) -> str:
    """Sintesi LLM della risposta finale dalle observation degli step.

    Fallback quando il template è degenere: invece di mostrare "Sono le .",
    diamo al LLM fast la query + le observation e gli chiediamo la risposta.
    È ciò che fa describe_entries / il PLANNER legacy: ragionare sui VALORI.
    Determinismo §7.9 eccetto la singola call irriducibilmente generativa;
    se llm_fast manca o fallisce, ritorna "" (caller decide il fallback).
    """
    if llm_fast is None or not steps:
        return ""
    obs_lines = []
    for s in steps[-4:]:  # ultime 4 observation bastano
        res = getattr(s, "result", None)
        tool = getattr(s, "tool", "?")
        if isinstance(res, dict):
            parts = []
            # 1) scalare top-level utile (content/value/summary)
            for k in ("content", "value", "summary"):
                v = res.get(k)
                if v:
                    parts.append(str(v)[:400])
                    break
            # 2) contenuto delle entries: spesso il dato utile sta QUI, non nel
            # summary generico. Es. «scarica X e dimmi Y» → body_text di get_urls
            # contiene la risposta. Privilegia campi testuali per-entry.
            entries = res.get("entries")
            if isinstance(entries, list) and entries:
                ebits = []
                # Campione ampio (non 5): per i read-LISTA (mail/eventi/file) il
                # finalizer DEVE vedere abbastanza entries da coprire lo SPAN
                # reale, altrimenti misrappresenta (bug 8/6: 41 mail last-7d →
                # "solo 7-8 giu" perche' vedeva solo le 5 piu' recenti). Per-entry
                # resta compatto (campi capati) → budget contenuto. Il prefisso
                # entries[N] porta comunque il TOTALE.
                for e in entries[:30]:
                    if not isinstance(e, dict):
                        ebits.append(str(e)[:200])
                        continue
                    fields = []
                    # 2a) campo testuale lungo (body/content/text esaustivo → stop)
                    # HTML→testo: body_text grezzo (SPA non parsata) inquina il
                    # contesto LLM e rischia l'eco nel final_message. No-op se
                    # gia' testo (§7.9). Import lazy per evitare cicli.
                    picked_long = False
                    from output_format import _strip_html_to_text as _sht
                    for k in ("body_text", "content", "text"):
                        if e.get(k):
                            fields.append(f"{k}={_sht(str(e[k]))[:300]}")
                            picked_long = True
                            break
                    if not picked_long:
                        # 2b) scalari salienti (identita'/valori)
                        for k in ("name", "subject", "date", "mtime", "modified",
                                  "size", "size_bytes", "format", "mime", "title",
                                  "description", "value", "summary", "email",
                                  "role", "path", "status"):
                            if e.get(k):
                                fields.append(f"{k}={str(e[k])[:120]}")
                            if len(fields) >= 4:
                                break
                        # 2c) campi-LISTA salienti (mail_accounts, channels, ...):
                        # proietta un campo identificativo per elemento. Senza
                        # questo la sintesi e' cieca sul dato nidificato (bug
                        # 31/5: "quali account mail" → mail_accounts mai vista).
                        for k, v in e.items():
                            if not (isinstance(v, list) and v):
                                continue
                            items = []
                            for it in v[:12]:
                                if isinstance(it, dict):
                                    iv = next((str(it[x]) for x in (
                                        "address", "email", "name", "title",
                                        "value", "summary", "channel", "path")
                                        if it.get(x)), None)
                                    items.append(iv or json.dumps(
                                        it, ensure_ascii=False)[:60])
                                else:
                                    items.append(str(it)[:60])
                            if items:
                                fields.append(f"{k}=[{', '.join(items)}]")
                    ebits.append(" ".join(fields) if fields else json.dumps(
                        {k: v for k, v in e.items()
                         if not isinstance(v, (dict, list))},
                        ensure_ascii=False)[:200])
                parts.append(f"entries[{len(entries)}]: " + " | ".join(ebits))
            if not parts:
                slim = {k: v for k, v in res.items()
                        if k not in ("ok", "metadata", "attachments", "entries")}
                parts.append(json.dumps(slim, ensure_ascii=False)[:400])
            obs_lines.append(f"{tool}: " + " ; ".join(parts))
    if not obs_lines:
        return ""
    # System prompt + labels in the INSTANCE language (§11 no hardcoded;
    # ADR 0092 prompt files). The whole prompt — not just a directive — must
    # be in current_lang, else a neutral LLM follows the prompt's language.
    import i18n as _i18n
    import prompt_loader as _pl
    _lang = _i18n.current_lang()
    sys_msg = _pl.get("final_assembler", _lang)
    _req, _res, _ans = {
        "it": ("Richiesta", "Risultati strumenti", "Risposta"),
        "en": ("Request", "Tool results", "Answer"),
    }.get(_lang, ("Request", "Tool results", "Answer"))
    user_msg = (
        f"{_req}: {query}\n\n{_res}:\n" + "\n".join(obs_lines)
        + f"\n\n{_ans}:"
    )
    try:
        # Scalar answers benefit from the 360-token fast cap.  A multidomain
        # result (three or more producer observations) commonly contains paths,
        # sizes, health and a comparison; the old fixed cap cut the answer
        # after the first health field even though every executor succeeded.
        # Raise the cap only for that bounded case.
        final_tokens = _presentation_tokens(presentation,
                                            wide=len(obs_lines) >= 3)
        out = llm_fast(sys_msg, user_msg, max_tokens=final_tokens)
        return (out or "").strip()
    except Exception as ex:
        log.warning("Executor: synthesize_final fallback failed: %r", ex)
        return ""


def _turn_is_zero_entries(steps) -> bool:
    """Recognize an empty terminal collection without reviving earlier rows."""
    from pipeline_effects import terminal_collection_output
    output = terminal_collection_output(steps)
    return output is not None and output[2] == 0


def _deterministic_zero_result(steps) -> str:
    """Use observed selection counts for an honest localized empty result."""
    from pipeline_effects import terminal_collection_output
    from vocab import PROCESSOR_VERBS
    output = terminal_collection_output(steps)
    if output is None or output[2] != 0:
        return ""
    tool, result, _count = output
    metadata = result.get("metadata")
    if isinstance(metadata, dict) and tool.split("_", 1)[0] in PROCESSOR_VERBS:
        count_in, count_out = metadata.get("count_in"), metadata.get("count_out")
        if (isinstance(count_in, int) and not isinstance(count_in, bool)
                and count_in > 0 and isinstance(count_out, int) and count_out == 0
                and not isinstance(count_out, bool)):
            return _msg("MSG_PROCESSOR_EMPTY", tool=tool)
    return _msg("MSG_NO_RESULTS")


@dataclass
class _ParallelCall:
    args: dict
    future: object
    submitted_at: float
    completed_at: float | None = None

    def finish(self, _future=None) -> None:
        self.completed_at = time.perf_counter()

    def latency_ms(self) -> int:
        end = self.completed_at or time.perf_counter()
        return max(0, int((end - self.submitted_at) * 1000))


class Executor:
    """Esegue Framework deterministicamente. SHARED fra tutti gli engine."""

    def __init__(self, *,
                 invoke_executor: Callable[[str, dict], dict],
                 submit_executor: Optional[Callable[[str, dict], object]] = None,
                 can_parallelize: Optional[Callable[[str], bool]] = None,
                 llm_call_fast: Optional[Callable] = None,
                 vaglio_judge: Optional[Callable] = None,
                 vaglio_guard: Optional[Callable] = None,
                 max_steps: int = 12,
                 seed_steps: Optional[list] = None,
                 catalog: Optional[list] = None):
        self.invoke = invoke_executor
        self.submit = submit_executor
        self.can_parallelize = can_parallelize
        self.llm_fast = llm_call_fast
        self.vaglio = vaglio_judge
        # Guardia deterministica PRE-invoke (forbidden-path/shell). Distinta dal
        # giudice post-step `vaglio`: previene l'azione, non la blocca a valle.
        self.vaglio_guard = vaglio_guard
        self.max_steps = max_steps
        # Seed-state (ADR 0177 M1): step pre-esistenti iniettati come history a
        # 0-offset PRIMA del primo step reale, così `from_step=1` li raggiunge.
        # Oggi: foto allegate (`@uploaded`, ADR 0092 — assorbe il path legacy);
        # domani: ripresa-dialog (resume). NON sono in `framework.steps` → non
        # ri-eseguiti, non contano verso `max_steps`. Read-only nel resolver.
        self.seed_steps = list(seed_steps or [])
        # NOMI-TOOL seminati kind="done" (continuazione dialogo): la guardia
        # dedup salta una loro ri-emissione del proposer (ADR 0177 M1).
        self._seed_done_tools = _seed_done_tools(self.seed_steps)
        # Map name→args_schema per la proiezione consumer-arg in from_step
        # (es. read_urls_html.urls ← entries[*].url). Senza catalog la
        # proiezione è no-op (degrade graceful, comportamento pre-fix).
        self._schema_map = {}
        self._catalog_map = {}
        for e in (catalog or []):
            nm = getattr(e, "name", None)
            if nm:
                self._schema_map[nm] = getattr(e, "args_schema", None)
                self._catalog_map[nm] = e

    def _prepare_static_read_args(
            self, step: StepSpec, *, query: str, runtime_ctx: dict) -> dict:
        """Prepare an admitted root read with the ordinary resolver sequence.

        Cross-step admission rejects pipeline/filler/step references, so this
        helper has no history input by construction.  Keeping query/runtime
        transforms here lets a peer be submitted before the main loop reaches
        it without bypassing canonical account, window or scope resolution.
        """
        args = dict(step.args or {})
        args = _resolve_runtime_placeholders(args, runtime_ctx)
        args = apply_arg_transforms(
            step.tool, args, query, scope="exec-only",
            args_schema=self._schema_map.get(step.tool))
        args = resolve_query_canonical_args(
            step.tool, args, query,
            args_schema=self._schema_map.get(step.tool))
        try:
            from args_resolver import resolve_scope_args
            args = resolve_scope_args(
                step.tool, args, self._schema_map.get(step.tool),
                actor=args.get("_actor") or "host", query=query)
        except Exception as exc:
            log.debug("args_resolver parallel-prep noop: %r", exc)
        args = _normalize_values_carrier(args)
        if step.tool in {"find_images_indices", "find_persons_indices"}:
            top_k = args.get("top_k")
            if isinstance(top_k, int) and top_k < 100 \
                    and not re.search(r"\b\d+\b", query or ""):
                args["top_k"] = 100
        if step.tool == "find_urls" \
                and args.get("mode") in {"research", "archive"}:
            if not _runtime_safety_lexicon.has_deep_crawl_intent(query):
                args["mode"] = "default"
        return args

    def _parallel_preflight(
            self, step: StepSpec, args: dict, *, query: str,
            runtime_ctx: dict) -> bool:
        """Apply every pre-invoke gate before a read enters a wave."""
        if not runtime_ctx.get("_gate_approved"):
            try:
                from args_resolver import scope_form_request
                if scope_form_request(
                        step.tool, args, self._schema_map.get(step.tool), query):
                    return False
            except Exception as exc:
                log.debug("scope_form_request parallel-preflight noop: %r", exc)
        if self.vaglio_guard is not None:
            try:
                from vaglio import check_executor_guard
                allowed, _reason = check_executor_guard(
                    self.vaglio_guard, step.tool, args,
                    executor=self._catalog_map.get(step.tool))
            except Exception as exc:
                log.warning("vaglio_guard parallel-preflight raised %r", exc)
                allowed = False
            if not allowed:
                return False
        return True

    def _start_parallel_wave(
            self, framework: Framework, start: int, first_args: dict,
            pending: dict[int, _ParallelCall], *, query: str,
            runtime_ctx: dict, history: list[StepRun], budget: int,
            progress=None) -> None:
        """Submit one conservative contiguous wave, or leave it serial.

        ``budget`` is what the step cap still allows: a step already started
        cannot be taken back when the cap is checked at collection time.
        """
        if (self.submit is None or self.can_parallelize is None
                or self.seed_steps or pending):
            return
        from .parallel_steps import contiguous_wave
        try:
            indexes = contiguous_wave(
                framework.steps, start, self._catalog_map,
                lambda ex: bool(self.can_parallelize(
                    str(getattr(ex, "name", "") or ""))),
            )
        except Exception as exc:
            log.warning("parallel wave admission failed closed: %r", exc)
            return
        indexes = indexes[:budget]
        if len(indexes) < 2:
            return

        prepared: dict[int, dict] = {start: first_args}
        for index in indexes[1:]:
            prepared[index] = self._prepare_static_read_args(
                framework.steps[index], query=query, runtime_ctx=runtime_ctx)
        if any(not self._parallel_preflight(
                framework.steps[index], prepared[index], query=query,
                runtime_ctx=runtime_ctx) for index in indexes):
            log.info("parallel wave %s degraded to serial by preflight", indexes)
            return

        base_path = [item.tool for item in history]
        for offset, index in enumerate(indexes):
            step = framework.steps[index]
            args = prepared[index]
            log.info("Executor: submit_parallel %s args=%s", step.tool,
                     {k: v for k, v in args.items() if k != "entries"})
            if progress is not None and hasattr(progress, "tool_call"):
                try:
                    progress.tool_call(
                        tool=step.tool,
                        step_num=len(history) + offset + 1,
                        path_so_far=base_path + [
                            framework.steps[pos].tool
                            for pos in indexes[:offset + 1]],
                        args={k: v for k, v in args.items()
                              if not k.startswith("_") and k != "entries"},
                        predicted_remaining=[])
                except Exception as exc:
                    log.debug("parallel progress.tool_call noop: %r", exc)
            submitted_at = time.perf_counter()
            future = self.submit(step.tool, args)
            call = _ParallelCall(
                args=args, future=future, submitted_at=submitted_at)
            add_callback = getattr(future, "add_done_callback", None)
            if callable(add_callback):
                add_callback(call.finish)
            pending[index] = call
        log.info("Executor: parallel wave started tools=%s",
                 [framework.steps[index].tool for index in indexes])

    def run(self, framework: Framework, *,
            query: str = "",
            runtime_ctx: Optional[dict] = None,
            remediate_args_cb: Optional[Callable] = None,
            progress=None) -> RunResult:
        if (_dispatch := sys.modules.get("engine.dispatch")) is not None:
            _dispatch._require_execution()
        result = RunResult()
        _paged_tables.set((runtime_ctx or {}).get("channel") == "http")
        # Seed-state (ADR 0177 M1): le history pre-esistenti (es. foto allegate
        # @uploaded) partono a 0-offset così `from_step=1` del primo step reale
        # le consuma. Copia per-run: il recovery ri-esegue con lo stesso seed.
        if self.seed_steps:
            result.steps = list(self.seed_steps)
        reference_steps = list(result.steps)

        def record(observation: StepRun) -> None:
            result.steps.append(observation)
            reference_steps.append(observation)

        result.framework_hash = compute_framework_hash(framework)
        t_start = time.time()
        _parallel_pending: dict[int, _ParallelCall] = {}
        empty_mutation_skipped = False

        # Il cap storico resta il confine dei piani LLM.  Solo framework
        # canonici costruiti nel runtime possono chiedere un budget maggiore;
        # anche per loro esiste un hard ceiling operativo centralizzato.  Il
        # marker non è deserializzabile da Framework.from_dict, quindi non è
        # un modo per il proposer di auto-estendere il proprio budget.
        effective_max_steps = self.max_steps
        try:
            runtime_step_cap = int(
                getattr(framework, "runtime_step_cap", 0) or 0)
            hard_max_steps = max(12, min(
                128, int(os.environ.get(
                    "METNOS_ENGINE_RUNTIME_MAX_STEPS", "32"))))
            if runtime_step_cap > 0:
                effective_max_steps = max(
                    effective_max_steps,
                    min(runtime_step_cap, hard_max_steps),
                )
        except (TypeError, ValueError):
            # Configurazione invalida: fail-safe sul cap ordinario.
            effective_max_steps = self.max_steps

        for i, step in enumerate(framework.steps):
            if i >= effective_max_steps:
                result.aborted_reason = f"cap_steps {effective_max_steps}"
                break
            if not step.tool:
                result.aborted_reason = f"step_{i+1}_no_tool"
                break

            # Branching: skip se condizione step non passa
            if not _step_condition_passes(
                    step, result.steps, reference_steps=reference_steps):
                empty_mutation_skipped |= _mutating_input_is_empty(
                    step, result.steps, reference_steps=reference_steps)
                # Keep the plan position without recording an invocation or effect.
                reference_steps.append(StepRun(
                    step_idx=i + 1, tool=step.tool, args={}, result={"entries": []},
                    ok=False, latency_ms=0, kind="skipped",
                ))
                result.skipped_step_count += 1
                continue

            # Dedup «semina» (ADR 0177 M1, §7.9 backstop deterministico): se
            # questo step ri-emette uno step GIÀ ESEGUITO seminato come
            # kind="done" (continuazione di un dialogo: il produttore è già
            # girato nel turno precedente), NON ri-eseguirlo — il suo risultato
            # è già in `result.steps` (il seed) e gli step a valle lo
            # referenziano via from_step. Il proposer, anche se istruito a
            # «pianificare solo il resto», è un LLM: questa è la rete di
            # sicurezza che rende la continuazione sicura a prescindere
            # (evita doppia latenza e — critico — ri-esecuzione di side-effect).
            # Match per NOME-TOOL: il proposer del turno di ripresa rigenera lo
            # stesso produttore con chiavi-arg diverse, ma è la stessa
            # ri-esecuzione che il seed «done» rende superflua.
            if (self._seed_done_tools
                    and step.tool != "final_answer"
                    and step.tool in self._seed_done_tools):
                continue

            # Terminator → FINALIZER unico (ADR 0177 T5): render + bullets +
            # zero/synth in una sola fonte, condivisa col fallback post-loop.
            if step.tool == "final_answer":
                result.final_text = _finalize_answer_text(
                    framework, result.steps, query, self.llm_fast,
                    presentation=(runtime_ctx or {}).get("presentation"),
                    empty_mutation_skipped=empty_mutation_skipped,
                    reference_steps=reference_steps)
                result.final_kind = "answer"
                break

            # Seed-state wiring (ADR 0177 M1): il PRIMO step reale che può
            # CONSUMARE il seed (es. @uploaded foto → find_images_indices via
            # consumer-match `reference_images`, oppure un entries-consumer) →
            # from_step=1 deterministico, così il seed viene proiettato nell'arg
            # consumer giusto. «Primo step reale» = nessun real-step ancora in
            # `result.steps` (= solo il seed); robusto a skip di branching.
            # Fire quando il proposer NON ha già dato una sorgente USABILE:
            #   (a) nessun `from_step`, E
            #   (b) l'arg-consumer naturale è assente, vuoto, o tiene un
            #       placeholder NON risolvibile (`${step0...}`/`${stepN...}` verso
            #       il seed: il proposer, ignaro del seed, indovina l'indice — e
            #       0-index/`step0` non risolve, 1-index `step1` sì → in ENTRAMBI
            #       i casi from_step=1 è la forma canonica). Le foto allegate
            #       VINCONO su un eventuale `query_text` del proposer (parità col
            #       path legacy ADR 0092: allegato presente = ricerca per-immagine).
            # Local: il framework NON è mutato (idempotenza sugli hit cache,
            # §S3/ADR 0174); opera su una COPIA degli args.
            # Gli argomenti del Framework sono il template cacheabile, non uno
            # scratchpad di esecuzione. Ogni resolver/auto-wire lavora sempre
            # su una copia: in precedenza il ramo senza from_step restituiva
            # lo stesso dict e l'auto-wire vi inseriva entries materializzate,
            # contaminando il piano dopo il run (e il successivo record L0).
            _step_args = dict(step.args or {})
            if (self.seed_steps and "from_step" not in _step_args
                    and len(result.steps) == len(self.seed_steps)):
                _se = _seed_entries(self.seed_steps)
                _carg = _consumer_match_arg(
                    self._schema_map.get(step.tool), _se) if _se else None
                _is_entries_consumer = step.tool in _ENTRIES_CONSUMERS
                if _se and (_is_entries_consumer or _carg):
                    # Valore già presente per l'arg-consumer (o `entries`)?
                    _tgt = _carg or "entries"
                    _cur = _step_args.get(_tgt)
                    _usable = bool(_cur) and not _detect_unresolved_placeholders(_cur)
                    if not _usable:
                        # Droppa il placeholder rotto e instrada dal seed.
                        _step_args = {k: v for k, v in _step_args.items()
                                      if k != _tgt}
                        _step_args["from_step"] = 1
            _step_args = _resolve_implicit_reverse_target(
                step.tool,
                _step_args,
                result.steps,
                consumer_schema=self._schema_map.get(step.tool),
            )
            # A step that names all the rows of a step and another choice
            # among those same rows is not run: which was meant cannot be
            # told, and guessing would act on the wrong records.
            _refusal = _contradiction_refusal(_step_args)
            if _refusal is not None:
                log.warning("Executor: %s names the rows of step %s twice, "
                            "differently: not run", step.tool,
                            _step_args.get("from_step"))
                record(StepRun(
                    step_idx=len(result.steps) + 1, tool=step.tool,
                    args=_step_args, ok=False, latency_ms=0, result=_refusal))
                continue
            # Resolve in ordine: from_step → stepref → fillers → runtime
            args = _resolve_from_step(
                _step_args, reference_steps,
                consumer_schema=self._schema_map.get(step.tool))
            if step.tool == "filter_entries":
                source_pos = _step_args.get("from_step")
                if isinstance(source_pos, str) and source_pos.isdigit():
                    source_pos = int(source_pos)
                if (isinstance(source_pos, int)
                        and 1 <= source_pos <= len(reference_steps)
                        and reference_steps[source_pos - 1].tool == "extract_entries"
                        and reference_steps[source_pos - 1].kind != "skipped"):
                    from filter_field_resolver import (
                        resolve_period_filter_field, site_collection_periods,
                    )
                    # Plan references count the seeded steps of a resumed
                    # tail (the paused executor is position 1): align the
                    # specs to the same numbering before following them.
                    plan_steps = [StepSpec(tool=seed.tool,
                                           args=dict(getattr(seed, "args", None) or {}))
                                  for seed in self.seed_steps] + list(framework.steps)
                    periods = site_collection_periods(plan_steps, source_pos, query)
                    if periods:
                        args = resolve_period_filter_field(
                            args, reference_steps[source_pos - 1].result, periods)
            _context_errors = args.pop(
                _FROM_STEP_CONTEXT_ERRORS_KEY, None)
            if isinstance(_context_errors, list) and _context_errors:
                _choice = _identity_choice_obs(
                    step.tool, args, _context_errors, runtime_ctx or {})
                if _choice is not None:
                    # The consumer has not run: ask which identity is meant.
                    log.info("Executor: %s asks to choose an identity",
                             step.tool)
                    record(StepRun(
                        step_idx=i + 1, tool=step.tool,
                        args=args, result=_choice, ok=False, latency_ms=0,
                    ))
                    result.final_kind = "ask"
                    result.final_text = ""
                    break
                fields = ", ".join(sorted({
                    str(item.get("arg"))
                    for item in _context_errors
                    if isinstance(item, dict) and item.get("arg")
                })) or "?"
                unresolved_identities = all(
                    isinstance(item, dict)
                    and item.get("reason") == "incomplete_vector_projection"
                    for item in _context_errors)
                failure_code = ("ERR_FROM_STEP_IDENTITIES_UNRESOLVED"
                                if unresolved_identities
                                else "ERR_FROM_STEP_CONTEXT_AMBIGUOUS")
                failure = {
                    "ok": False,
                    "error_class": ("source_identity_unresolved"
                                    if unresolved_identities
                                    else "ambiguous_source_context"),
                    "error_code": failure_code,
                    "error": _msg(failure_code, fields=fields),
                    "context_errors": _context_errors,
                }
                log.warning(
                    "Executor: %s ambiguous required source context: %s",
                    step.tool, fields)
                step_idx = len(result.steps) + 1
                record(StepRun(
                    step_idx=step_idx, tool=step.tool, args=args,
                    result=failure, ok=False, latency_ms=0,
                ))
                # What this step needed is unknown: the steps after it must
                # not act as if it had run (R-055 E1).
                result.aborted_reason = f"step_{step_idx}_error"
                result.final_kind = "error"
                break
            # Universal §7.3: gli helper che consumano `entries` (describe/
            # classify/filter/sort/group/compute/compare_entries) sono spesso
            # emessi dal Proposer SENZA from_step → ricevono 0 entries →
            # risultato degenere → terminator "Pipeline malformata" (bug live
            # find_urls→describe_entries). Auto-wire deterministico: se manca
            # `entries`, eredita la lista dall'ultimo step che ne ha prodotta
            # una (equivale alla precursor-injection del path legacy).
            # NB: `entries` può arrivare come PLACEHOLDER non risolto (anti-pattern
            # §4.1 `entries:"{{stepN.entries}}"` invece di from_step) → è truthy ma
            # verrebbe droppato più sotto (1060) lasciando l'helper a 0 entries
            # (bug q13 4/6: describe_entries terminale ok=False → terminator). Va
            # trattato come ASSENTE: l'auto-wire lo ripesca dallo scratchpad.
            # La risoluzione dei ${stepN...} avviene poco sotto: la sola
            # presenza di una multilist non vuota dichiara già una sorgente
            # intenzionale. Se un placeholder fosse invalido deve emergere
            # come tale, non essere mascherato con l'ultimo producer.
            _has_explicit_multilist = bool(args.get("entries_lists"))
            # ``from_step`` dichiara una sorgente precisa anche quando quella
            # sorgente ha prodotto zero record. In quel caso ``entries=[]`` e'
            # il risultato osservato, non un argomento mancante da riparare
            # pescando una lista precedente e non correlata.
            _has_explicit_from_step = "from_step" in _step_args
            if (step.tool in _ENTRIES_CONSUMERS and result.steps
                    and not _has_explicit_multilist
                    and not _has_explicit_from_step
                    and (not args.get("entries")
                         or _detect_unresolved_placeholders(args.get("entries")))):
                for _prev in reversed(result.steps):
                    _pr = _prev.result if isinstance(_prev.result, dict) else {}
                    _pe = _step_list_payload(_pr)
                    if _pe:
                        args["entries"] = _pe
                        break
                    # Scalar readers expose their value at top level instead
                    # of under ``entries``.  A proposer-emitted empty list is
                    # not an intentional source when such a value exists: make
                    # it a one-record payload so semantic helpers cannot turn
                    # a successful read into a false zero-result response.
                    _content = _pr.get("content")
                    if isinstance(_content, str) and _content:
                        _entry = {"content": _content}
                        _metadata = _pr.get("metadata")
                        if isinstance(_metadata, dict):
                            for _key in ("path", "url", "title", "name"):
                                if _metadata.get(_key) is not None:
                                    _entry[_key] = _metadata[_key]
                        args["entries"] = [_entry]
                        break
            # write/move trasformativi con un *_template/_field ma SENZA alcuna
            # sorgente-lista (entries/files/paths) → eredita entries dall'ultimo
            # step produttore (bug q28: write_files(content_template=...) senza
            # from_step → 0 entries → write vuoto → ok=False).
            if (step.tool in _TEMPLATE_CONSUMERS and result.steps
                    and not args.get("entries") and not args.get("files")
                    and not args.get("paths") and not args.get("content")
                    and (any(args.get(_t) for _t in _TEMPLATE_ARGS)
                         or args.get("path"))):
                # template-arg presente, OPPURE solo un `path` di output scalare
                # senza alcun content/sorgente-lista → è un write AGGREGATO della
                # lista prodotta a monte (bug q45: write_files(path=X) con i dati
                # da filter/read non pipati → 'write non completata').
                for _prev in reversed(result.steps):
                    _pr = _prev.result if isinstance(_prev.result, dict) else {}
                    _pe = _step_list_payload(_pr)
                    if _pe:
                        args["entries"] = _pe
                        break
            question_template = args.get("prompt") if step.tool == "get_approval" else None
            args = _resolve_stepref(args, reference_steps, self._schema_map.get(step.tool))
            args = _resolve_fillers(args, framework.fillers, self.llm_fast, query)
            args = _resolve_runtime_placeholders(args, runtime_ctx or {})
            if question_template:
                from .dispatch import _partition_question_text
                preview, refused = "", {}
                # A folder-creation step may precede the approved move. Only
                # inspect actions before the next question and rows already
                # resolved from observations; never execute a step to preview it.
                if runtime_ctx and not runtime_ctx.get("target_device"):
                    import server_filesystem
                    for following in framework.steps[i + 1:]:
                        if following.tool == "get_approval":
                            break
                        target = self._catalog_map.get(following.tool)
                        if target is None:
                            continue
                        preview_args = _resolve_from_step(dict(following.args or {}), reference_steps,
                            consumer_schema=self._schema_map.get(following.tool))
                        preview_args = _resolve_stepref(preview_args, reference_steps,
                            self._schema_map.get(following.tool))
                        preview, refused = server_filesystem.fixed_refusal_preview(
                            runtime_ctx.get("owner_user_id") or runtime_ctx.get("actor") or "host",
                            server_filesystem.manifest_for(target), preview_args)
                        if preview:
                            break
                options = {"fixed_note": preview, "refused_files": refused} if preview else {}
                args["prompt"] = _partition_question_text(
                    question_template, args.get("prompt"), reference_steps, **options)
            # Resolver EXEC-ONLY (backend/self_recipient/calendar): dipendono da
            # runtime ctx/creds → override deterministico LLM-invisibile. Registro
            # ArgTransform scope="exec-only" (ADR 0155/0163/0136; razionali
            # per-entry in ARG_TRANSFORM_PIPELINE). NON riapplicati al record L0.
            args = apply_arg_transforms(
                step.tool, args, query, scope="exec-only",
                args_schema=self._schema_map.get(step.tool))
            # Slot query-specific UNIFORMI (§7.9): account mail («tutta la
            # posta»→"all", account nominato→quello; bug live 10-11/6/2026)
            # + time_window («ultime 24 ore»→"last-24h"). Il segnale vive
            # nella QUERY, non negli arg ereditati dal piano (champion L1 /
            # piano cachato): vedi resolve_query_canonical_args.
            args = resolve_query_canonical_args(
                step.tool, args, query,
                args_schema=self._schema_map.get(step.tool))
            # Scope-arg UNIFORME (§7.9, gemello dei resolver sopra): l'«oggetto»
            # di una CRUD (repo/calendar/account…) mancante o a placeholder →
            # inline-dalla-query → ricordato (actor+dominio) → config. Cattura
            # del valore dopo l'invoke OK. Vedi args_resolver.
            try:
                from args_resolver import resolve_scope_args
                args = resolve_scope_args(
                    step.tool, args, self._schema_map.get(step.tool),
                    actor=args.get("_actor") or "host", query=query)
            except Exception as _are:
                log.debug("args_resolver noop: %r", _are)
            # Explicit record sources must reach projection before any
            # positional matrix is materialized; legacy values stay as before.
            args = _normalize_values_carrier(args)

            # Universal §7.3: image-search executor → top_k default 100 per
            # consentire pattern «20 preview + 100 gallery» standard UI.
            # Override solo se LLM ha emesso valore basso E user query non
            # contiene un count numerico esplicito.
            if step.tool in {"find_images_indices", "find_persons_indices"}:
                tk = args.get("top_k")
                if isinstance(tk, int) and tk < 100:
                    if not re.search(r"\b\d+\b", query or ""):
                        args["top_k"] = 100

            # Universal §7.3 (safety net deterministico): il Proposer vede solo
            # gli enum di `mode` (non la loro descrizione) e tende a scegliere
            # 'research' per query informative ('cerca informazioni su X') →
            # crawl ricorsivo fino a 900s che blocca il turno interattivo.
            # Downgrade research/archive → default salvo intento ESPLICITO di
            # esplorare/archiviare un INTERO sito. Causa generalizzata: enum
            # pericoloso scelto senza la semantica dell'arg (§7.9 code>LLM).
            if step.tool == "find_urls" and args.get("mode") in ("research", "archive"):
                if not _runtime_safety_lexicon.has_deep_crawl_intent(query):
                    log.info("Executor: find_urls mode=%s → default "
                             "(query informativa, no deep-crawl intent)",
                             args.get("mode"))
                    args["mode"] = "default"

            # Universal §7.3: describe_entries dopo step con attachments
            # immagini è ridondante (thumbnail parlano da soli). Skip per
            # risparmiare 20-30s LLM call e ridurre rischio timeout client.
            # Universal §7.3 (12/6/2026, bug live T1/T2 «chi è enrollato»): un
            # producer che emette `final_message_hint` ha GIÀ la presentazione
            # canonica del proprio output (es. get_persons enumera il registro
            # name+n_examples). Passare quell'enumerazione al describe default
            # (by_importance = segnale-vs-rumore pensato per mail) è
            # distruttivo: le entità anagrafiche venivano «scartate come
            # rumore». Skip deterministico §7.9: il hint diventa `summary`,
            # così il template finale `${stepN.summary}` si risolve col testo
            # del producer — vale anche per i piani cachati (fastpath/autopath).
            # NON si applica se il describe porta direttive ESPLICITE
            # (style/context/group_by): lì la sintesi LLM è richiesta.
            if step.tool == "describe_entries" and result.steps:
                prev = result.steps[-1].result if isinstance(result.steps[-1].result, dict) else {}
                skip_reason = ""
                skip_result = {"ok": True}
                _explicit = bool(args.get("context")) or bool(args.get("group_by"))
                if (isinstance(prev.get("attachments"), list)
                        and prev["attachments"] and not _explicit):
                    skip_reason = "attachments_present"
                    log.info("Executor: skip describe_entries (prev step has %d attachments)",
                              len(prev["attachments"]))
                else:
                    _hint = prev.get("final_message_hint")
                    # «esplicito» = direttiva di VERA intenzione utente che
                    # giustifica una sintesi LLM sopra un producer che già si
                    # auto-presenta (final_message_hint). `style` NON conta: è un
                    # preset che l'LLM sceglie da sé (e spesso sbaglia — 'compact'
                    # o l'invalido 'bullet_list'), non una richiesta dell'utente;
                    # lasciarlo bloccare lo skip fa riassumere un'enumerazione
                    # fedele (lista task con id+query) perdendone i dettagli, in
                    # modo dipendente dal phrasing. Solo `context`/`group_by`
                    # (l'utente ha chiesto un focus o un raggruppamento) valgono.
                    # §7.9 deterministico, robusto al rumore-enum dell'LLM.
                    if isinstance(_hint, str) and _hint.strip() and not _explicit:
                        skip_reason = "final_message_hint_present"
                        skip_result["summary"] = _hint.strip()
                        log.info("Executor: skip describe_entries (prev step "
                                 "self-presents via final_message_hint)")
                if skip_reason:
                    skip_result["skipped"] = skip_reason
                    lat_ms = 0
                    step_idx = len(result.steps) + 1
                    record(StepRun(
                        step_idx=step_idx, tool=step.tool, args=args,
                        result=skip_result,
                        ok=True, latency_ms=lat_ms,
                    ))
                    continue

            # Universal §7.9 (drop-optional-unresolved): un placeholder rimasto
            # letterale su un arg OPZIONALE = filler/ref che il planner ha emesso
            # ma il runtime non sa valorizzare (es. find_images
            # base_path=${FILLER:base_path}, o ${stepN.x} da step a entries
            # vuote). Per gli arg NON-required lo si LASCIA CADERE invece di
            # fallire: l'executor applica il suo default (find_images →
            # discovery automatica su tutti gli indici). Deterministico §7.9,
            # model-independent (Qwen emette filler spuri su arg opzionali);
            # stessa logica del self_recipient_resolver per il send.
            schema = self._schema_map.get(step.tool) or {}
            required = set(schema.get("required") or [])
            dropped = []
            for _k in list(args.keys()):
                if _k in required:
                    continue
                if _detect_unresolved_placeholders(args[_k]):
                    del args[_k]
                    dropped.append(_k)
            if dropped:
                log.info("Executor: %s drop arg opzionali con placeholder non "
                         "risolti: %s (default executor)", step.tool, dropped)

            # Universal §7.9: rileva placeholder NON risolti rimasti su arg
            # REQUIRED (i soli che non possiamo lasciar cadere).
            # Pattern: `${stepN.X}`, `${steps.N.X}`, `${RUNTIME:X}`, `${FILLER:X}`
            # rimasti letterali → marca errore prima di invocare executor
            # (evita pass-through di placeholder a API esterne come Google).
            unresolved = _detect_unresolved_placeholders(args)
            if unresolved:
                r = {
                    "ok": False,
                    "error": f"unresolved placeholders: {unresolved}",
                    "error_class": "unresolved_placeholder",
                    "unresolved": unresolved,
                }
                log.warning("Executor: %s unresolved placeholders: %s",
                            step.tool, unresolved)
                # Salta invoke, lascia auto-remediation o errore propagare
                lat_ms = 0
                step_idx = len(result.steps) + 1
                record(StepRun(
                    step_idx=step_idx, tool=step.tool, args=args,
                    result=r, ok=False, latency_ms=lat_ms,
                ))
                continue

            # JIT sibling alignment for explicit URL batches.  A cached
            # proposer alternative can select read_urls_pdf for object=urls;
            # delay the correction until args/from_step are fully resolved,
            # then execute the content-type-aware reader without mutating the
            # framework cache.  read_urls_html also handles genuine PDF
            # responses, so mixed/extensionless batches remain lossless.
            _exec_tool = _align_url_reader_jit(step.tool, args)
            if _exec_tool != step.tool:
                log.info("Executor: JIT URL reader %s -> %s", step.tool,
                         _exec_tool)

            # Notifica in-piano nei run schedulati a vuoto (§2.8, 13/6/2026):
            # un send/notify finale («ti ho fatto X») NON deve partire se la
            # pipeline a monte non ha prodotto nulla (bug live: maintenance
            # github → send_messages su 0 issue aperte = falso successo). La
            # soppressione del push SCHEDULER non copre il send IN-PIANO.
            # Generale §7.3, deterministico §7.9, no-op sui turni interattivi.
            try:
                from treated_issues_guard import suppress_scheduled_notify
                if suppress_scheduled_notify(_exec_tool, result.steps):
                    log.info("[scheduled-notify-guard] skip %s: pipeline a "
                             "vuoto, notifica soppressa (run schedulato, §2.8)",
                             _exec_tool)
                    record(StepRun(
                        step_idx=i + 1, tool=_exec_tool, args=args,
                        result={"ok": True, "ok_count": 0,
                                "skipped": "scheduled_noop_notify",
                                "note": "notifica soppressa: run schedulato a "
                                        "vuoto (0 nuovi work-item)"},
                        ok=True, latency_ms=0))
                    continue
            except Exception as _sng:
                log.debug("scheduled-notify-guard noop: %r", _sng)

            # gate-resume re-run (20/6/2026): se questo turno e' la RIPRESA
            # dopo approvazione (runtime_ctx._gate_approved), il gate
            # get_approval e' gia' stato consentito → auto-passa (nessun nuovo
            # dialog) cosi' la pipeline prosegue verso send/write. §7.9.
            if (_exec_tool == "get_approval"
                    and (runtime_ctx or {}).get("_gate_approved")):
                args["_pre_approved"] = True

            if _exec_tool == step.tool:
                self._start_parallel_wave(
                    framework, i, args, _parallel_pending, query=query,
                    runtime_ctx=runtime_ctx or {}, history=result.steps,
                    budget=effective_max_steps - i, progress=progress)
            _parallel_call = _parallel_pending.pop(i, None)
            if _parallel_call is not None and _exec_tool != step.tool:
                # Defensive future-proofing if a preceding wave was built
                # before the dynamic URL list became available.  Admitted
                # wave members are read-only; discard/cancel the stale sibling
                # and execute the aligned reader synchronously.
                _parallel_call.future.cancel()
                _parallel_call = None

            # Invoke
            # Osservabilità (#4): logga tool + args risolti (escluso il payload
            # `entries`, voluminoso) appena prima dell'invoke. Senza, un turn
            # engine v2 che si blocca su un executor è una scatola nera.
            if _parallel_call is None:
                log.info("Executor: invoke %s args=%s", _exec_tool,
                         {k: v for k, v in args.items()
                          if k not in ("entries",)})
            # Breadcrumb live in chat: emette `tool_call` sul progress (sia
            # TurnEventProgress sia _SSEProgress lo accettano). Senza, engine v2
            # mostrava solo `start` poi `final` (⏳ muto per tutto il turno).
            if (_parallel_call is None and progress is not None
                    and hasattr(progress, "tool_call")):
                try:
                    progress.tool_call(
                        tool=_exec_tool, step_num=len(result.steps) + 1,
                        path_so_far=[s.tool for s in result.steps] + [_exec_tool],
                        args={k: v for k, v in args.items()
                              if not k.startswith("_") and k != "entries"},
                        predicted_remaining=[])
                except Exception as _pe:
                    log.debug("progress.tool_call noop: %r", _pe)
            # F2 scope-arg: se serve un form (read required-mancante / write
            # conferma-target), emetti needs_inputs invece di invocare. Il bridge
            # engine lo propaga → dialog get_inputs → resume_executor_with_values.
            _form_obs = None
            # gate-resume (20/6): sulla RIPRESA dopo approvazione il consenso
            # umano e' GIA' stato dato dal gate → niente form di conferma
            # scope-args (sarebbe un secondo consenso ridondante che blocca il
            # publish). I default config (repo/store) restano risolti a monte
            # (backend_resolver/args_resolver). §7.9 deterministico.
            if (_parallel_call is None
                    and not (runtime_ctx or {}).get("_gate_approved")):
                try:
                    from args_resolver import scope_form_request
                    _form_obs = scope_form_request(
                        _exec_tool, args, self._schema_map.get(_exec_tool), query)
                except Exception as _fe:
                    log.debug("scope_form_request noop: %r", _fe)
            # Vaglio GUARD pre-invoke (sicurezza, gap confermato 23/6): blocca le
            # mutazioni su forbidden-path (~/.ssh, /etc/shadow, .aws/credentials,
            # /boot...) PRIMA di eseguirle. Il legacy chiama judge() prima
            # dell'invoke; l'engine (path di prod) NON lo faceva → regressione
            # silenziosa sul nucleo non-negoziabile. Solo la GUARDIA
            # deterministica (forbidden-path/shell), NON il giudice teleologico.
            # Pre-invoke = prevenzione vera, non blocco-a-valle.
            if (_parallel_call is None and self.vaglio_guard is not None
                    and _form_obs is None):
                try:
                    from vaglio import check_executor_guard
                    _ok_g, _why_g = check_executor_guard(
                        self.vaglio_guard, _exec_tool, args,
                        executor=self._catalog_map.get(_exec_tool))
                except Exception as _ge:
                    _ok_g, _why_g = False, "guard_check_failed"
                    log.warning("vaglio_guard raised %r — fail-closed", _ge)
                if not _ok_g:
                    log.warning("[vaglio guard] BLOCCO pre-invoke %s: %s",
                                _exec_tool, _why_g)
                    record(StepRun(
                        step_idx=i + 1, tool=_exec_tool, args=args,
                        result={"ok": False, "error_class": "vaglio_guard",
                                "error": _why_g or "forbidden"},
                        ok=False, latency_ms=0))
                    result.aborted_reason = f"step_{i+1}_vaglio_guard"
                    result.final_kind = "error"
                    break
            # Data-locality (co-location consumer↔producer, §7.9): uno step
            # device_ok che CONSUMA l'output di un producer girato sul SERVER
            # deve restare sul server — entries/path sono dati LOCALI, inesistenti
            # sul device (bug 7/7: get_files su `${step1.entries.*.path}` di
            # find_images_indices@.33 con device sticky → C:\mnt\... → 0 trovati).
            # Producer via from_step:N O placeholder ${stepN}/{{stepN}} (§4.1).
            # Marker letto e rimosso da invoke_executor prima del placement.
            if (_parallel_call is None
                    and _references_server_producer(step, reference_steps)):
                args = {**args, "_colocate_server": True}
            from execution_effects import resolve_execution_effect
            _execution_effect = resolve_execution_effect(
                self._catalog_map.get(_exec_tool), args)
            t0 = time.time()
            if _parallel_call is not None:
                if args != _parallel_call.args:
                    # The eligibility contract says preparation is history-free.
                    # A mismatch means that assumption drifted; the call was
                    # read-only, but its result must not be committed silently.
                    r = {
                        "ok": False,
                        "error_class": "parallel_prepare_mismatch",
                        "error": "parallel preparation diverged from serial path",
                    }
                    log.error("parallel preparation mismatch tool=%s", _exec_tool)
                else:
                    try:
                        r = _parallel_call.future.result()
                    except Exception as ex:
                        if type(ex).__name__ == "TimeoutExpired":
                            _to = getattr(ex, "timeout", None)
                            r = {"ok": False, "error_class": "timeout",
                                 "error": _msg(
                                     "ERR_EXECUTOR_TIMEOUT", tool=_exec_tool,
                                     seconds=int(_to or 0))}
                        else:
                            r = {"ok": False, "error": str(ex),
                                 "error_class": "exception"}
                    log.info("Executor: commit_parallel %s latency_ms=%d",
                             _exec_tool, _parallel_call.latency_ms())
                lat_ms = _parallel_call.latency_ms()
            elif _form_obs is not None:
                r = _form_obs
            else:
                try:
                    # In-process executors may emit bounded progress without
                    # leaking callbacks into validated/logged JSON args.  The
                    # context is reset after every invocation, including
                    # failures, and remote executors simply never observe it.
                    from executor_progress import bind as _bind_executor_progress
                    with _bind_executor_progress(progress):
                        from lre_submission import submit_rows
                        r = (None if (runtime_ctx or {}).get("_rows_continuation") else submit_rows(
                            self._catalog_map.get(_exec_tool), args, runtime_ctx or {}, framework,
                            [(item.tool, item.result) for item in result.steps], query))
                        if r is None:
                            r = self.invoke(_exec_tool, args)
                except Exception as ex:
                    if type(ex).__name__ == "TimeoutExpired":
                        # §11/§2.8: messaggio CHIARO invece del grezzo
                        # "Command '[...python...]' timed out after Ns".
                        _to = getattr(ex, "timeout", None)
                        log.warning("Executor: %s timeout (%ss)", _exec_tool, _to)
                        r = {"ok": False, "error_class": "timeout",
                             "error": _msg("ERR_EXECUTOR_TIMEOUT",
                                           tool=_exec_tool, seconds=int(_to or 0))}
                    else:
                        log.warning("Executor: %s raised %r", _exec_tool, ex)
                        r = {"ok": False, "error": str(ex),
                             "error_class": "exception"}
            if _parallel_call is None:
                lat_ms = int((time.time() - t0) * 1000)

            # Recovery args remediate (1× per step) — mai per needs_inputs
            if _form_obs is None and not r.get("ok") and remediate_args_cb is not None:
                try:
                    fixed = remediate_args_cb(tool=_exec_tool, args=args,
                                                result=r, query=query)
                    if fixed and fixed != args:
                        log.info("Executor: retry %s with remediated args",
                                  _exec_tool)
                        t0 = time.time()
                        r = self.invoke(_exec_tool, fixed)
                        lat_ms = int((time.time() - t0) * 1000)
                        args = fixed
                except Exception as ex:
                    log.warning("remediate_args_cb raised %r", ex)

            from list_outcomes import normalize as _normalize_list_outcome
            r = _normalize_list_outcome(r)
            # Cattura scope-arg: ultimo valore usato → default per il giro dopo
            # (§7.9, no LLM). Vedi args_resolver.remember_scope_args.
            if isinstance(r, dict) and r.get("ok"):
                try:
                    from args_resolver import remember_scope_args
                    remember_scope_args(_exec_tool, args,
                                        actor=args.get("_actor") or "host")
                except Exception as _rse:
                    log.debug("remember_scope_args noop: %r", _rse)

            _shown_as = _inherited_presentation(_step_args, reference_steps, r)
            if _shown_as:
                r = {**r, "_presentation_contract": _shown_as}

            _host = (r.get("_ran_on_device") or "server") if isinstance(r, dict) else "server"
            _data_host = _data_host_for_step(step, reference_steps, _host)
            from frozen_plan_consent import prepare_resume, redact_args, redact_result
            _frozen_plan_resume = prepare_resume(
                self._catalog_map.get(_exec_tool), args, r)
            _logged_args = redact_args(self._catalog_map.get(_exec_tool), args)
            r = redact_result(self._catalog_map.get(_exec_tool), r)
            sr = StepRun(step_idx=i + 1, tool=_exec_tool, args=_logged_args,
                          result=r, ok=bool(r.get("ok")), latency_ms=lat_ms,
                          host=_host, data_host=_data_host,
                          execution_effect=_execution_effect,
                          frozen_plan_resume=_frozen_plan_resume)
            record(sr)
            if sr.ok:
                result.ok_count += 1

            # §7.3: needs_inputs decision → terminate immediately. Il caller
            # (agent_runtime._run_engine) gestisce il dialog_pending +
            # form rendering. NON proseguire con steps successivi.
            if r.get("decision") == "needs_inputs":
                result.final_kind = "ask"
                result.final_text = ""
                break

            if _frozen_plan_resume is not None:
                result.final_kind = "ask"
                result.final_text = ""
                break

            # Un consenso ancora aperto non e' un errore e non autorizza gli
            # step successivi. Il canale persiste la proposta strutturata e
            # riprende soltanto dopo una decisione tipizzata dell'utente.
            if (r.get("decision") == "approval_required"
                    and r.get("approval_required") is True):
                result.final_kind = "ask"
                result.final_text = (
                    r.get("final_message_hint") or r.get("summary") or ""
                )
                break

            # §7.9 gate-resume: qualunque executor puo' produrre un dialogo di
            # approvazione (sites lo costruisce solo DOPO avere risolto il
            # target DOM). La pipeline deve fermarsi sul contratto strutturato,
            # non sul nome letterale `get_approval`; altrimenti gli step a valle
            # girano prima del consenso. `needs_inputs` resta gestito sopra.
            if (r.get("decision") == "input_required"
                    and r.get("dialog_id")):
                result.final_kind = "ask"
                result.final_text = r.get("final_message_hint") or ""
                result.gate_dialog_id = r.get("dialog_id") or ""
                break

            # A successful asynchronous admission is a terminal receipt for
            # this interactive turn. The executor has accepted ownership of
            # the remaining work, so later planner steps would duplicate or
            # contradict that mandate. The protocol is result-based and
            # executor-name independent; a non-localized or failed receipt is
            # not allowed to short-circuit the ordinary honesty path.
            _receipt = r.get("final_message_hint")
            if (r.get("decision") == "accepted" and sr.ok
                    and isinstance(_receipt, str) and _receipt.strip()
                    and not _receipt.lstrip().startswith("<missing:")):
                result.final_kind = "answer"
                result.final_text = _receipt.strip()
                break

            # Vaglio post-step (opt-in)
            if self.vaglio is not None:
                try:
                    if not self.vaglio(_exec_tool, args, r):
                        result.aborted_reason = f"step_{i+1}_vaglio_block"
                        result.final_kind = "error"
                        break
                except Exception as ex:
                    log.warning("vaglio raised %r — fail-open", ex)

            # Stop on hard error (no recovery attempted)
            if not sr.ok or (r.get("partial") is True and r.get("fail_count", 0) > 0):
                result.aborted_reason = f"step_{i+1}_error"
                result.final_kind = "error"
                if r.get("_list_partial_notice"):
                    if ((runtime_ctx or {}).get("_gate_approved") and r.get("fail_count") == len(r.get("failed", []))
                            and all(row.get("error_code") == "ERR_REFUSE_MOVE" for row in r["failed"])
                            and not any(r.get(key) for key in ("error_class", "error_code", "error"))):
                        result.final_kind = "answer"
                    result.final_text = r["_list_partial_notice"]
                    if any(step.tool != "final_answer" for step in framework.steps[i + 1:]):
                        result.final_text += "\n\n" + _msg("MSG_LIST_STOPS_PLAN")
                break

        # A hard stop preserves the historical commit boundary.  Already
        # running wave peers are read-only by admission; cancel queued work and
        # discard every uncommitted result.
        for _call in _parallel_pending.values():
            try:
                _call.future.cancel()
            except Exception:
                pass
        _parallel_pending.clear()

        result.elapsed_ms = int((time.time() - t_start) * 1000)
        if not result.final_kind:
            result.final_kind = "error" if result.aborted_reason else "answer"
        if result.final_kind == "answer" and not result.final_text:
            # Fallback post-loop (framework SENZA step final_answer): stessa
            # UNICA fonte del terminator (ADR 0177 T5) — prima era un blocco
            # gemello divergente (niente arricchimento count-only→bullets).
            result.final_text = _finalize_answer_text(
                framework, result.steps, query, self.llm_fast,
                presentation=(runtime_ctx or {}).get("presentation"),
                empty_mutation_skipped=empty_mutation_skipped,
                reference_steps=reference_steps)
        if result.final_kind == "answer":
            notices = list(dict.fromkeys(
                row.result["_list_partial_notice"] for row in result.steps
                if isinstance(row.result, dict) and row.result.get("_list_partial_notice")))
            for notice in reversed(notices):
                if not result.final_text.startswith(notice):
                    result.final_text = notice + "\n\n" + result.final_text
        return result
