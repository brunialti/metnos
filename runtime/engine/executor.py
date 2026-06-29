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

import hashlib
import json
import logging
import re
import sqlite3
import time
from typing import Any, Callable, Optional

from .types import Framework, StepSpec, StepRun, RunResult
from messages import get as _msg  # §11: render user-facing via DB i18n

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
                    bits.append(str(e[k])[:60])
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
    candidates = {
        "actor": actor,
        "actor_email": actor_email,
        "lang": str(ctx.get("lang") or "it"),
        "channel": str(ctx.get("channel") or ""),
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


def _resolve_runtime_time_key(key: str) -> str | None:
    """Universal §7.9: risolve placeholder TIME dinamici (now, today,
    now_plus_Nd, now_minus_Nh, ecc.) → ISO timestamp. Pattern in regex
    chiusa, deterministico, no LLM.
    """
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    if _RUNTIME_TIME_NOW.match(key):
        return now.isoformat()
    if _RUNTIME_TIME_TODAY.match(key):
        return now.date().isoformat()
    m = _RUNTIME_TIME_NOW_PLUS_DAYS.match(key)
    if m:
        return (now + _dt.timedelta(days=int(m.group(1)))).isoformat()
    m = _RUNTIME_TIME_NOW_MINUS_DAYS.match(key)
    if m:
        return (now - _dt.timedelta(days=int(m.group(1)))).isoformat()
    m = _RUNTIME_TIME_NOW_PLUS_HOURS.match(key)
    if m:
        return (now + _dt.timedelta(hours=int(m.group(1)))).isoformat()
    m = _RUNTIME_TIME_NOW_MINUS_HOURS.match(key)
    if m:
        return (now - _dt.timedelta(hours=int(m.group(1)))).isoformat()
    return None


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
    def _resolve_key(key: str) -> str | None:
        # 1. Static resolver (actor/lang/channel)
        if key in resolvers:
            return resolvers[key]
        # 2. Dynamic time resolver
        return _resolve_runtime_time_key(key)
    def _sub_str(v: str) -> str:
        m = _RUNTIME_RE.search(v)
        if not m:
            return v
        def _repl(mm):
            r = _resolve_key(mm.group(1))
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
        if cv:
            out.setdefault(f"_{ck}", cv)
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

# Helper universali che consumano una lista `entries` (auto-wire prev step).
_ENTRIES_CONSUMERS = frozenset({
    "describe_entries", "classify_entries", "extract_entries", "filter_entries",
    "sort_entries", "group_entries", "compute_entries", "compare_entries",
})

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


def _consumer_match_arg(consumer_schema, prev_entries):
    """Rileva l'arg-lista consumer naturale per una lista di entries via
    convenzione I/O Metnos (plurale↔singolare + `from_entries_key`).

    Replica `agent_runtime._consumer_match_arg` — replicato qui per evitare
    l'import circolare engine←agent_runtime. Es: find_urls produce
    entries=[{url, title, ...}], read_urls_html consuma `urls` → singolare
    `url` presente in entries[0] → estrai entries[*].url. Ritorna il nome
    dell'arg consumer o None. Esclude `entries`/`from_step`."""
    if not isinstance(consumer_schema, dict) or not isinstance(prev_entries, list):
        return None
    props = consumer_schema.get("properties") or {}
    if not isinstance(props, dict):
        return None
    required = consumer_schema.get("required") or []
    if not isinstance(required, list):
        required = []
    # Caso degenere lista vuota: primo array required (escluso entries/from_step).
    if not prev_entries:
        for arg_name in required:
            if arg_name in ("entries", "from_step"):
                continue
            spec = props.get(arg_name)
            if isinstance(spec, dict) and spec.get("type") and spec.get("type") != "array":
                continue
            return arg_name
        return None
    if not isinstance(prev_entries[0], dict):
        return None
    sample_keys = set(prev_entries[0].keys())
    candidates = []
    for arg_name, spec in props.items():
        if arg_name in ("entries", "from_step"):
            continue
        if isinstance(spec, dict) and spec.get("type") and spec.get("type") != "array":
            continue
        from_key = spec.get("from_entries_key") if isinstance(spec, dict) else None
        if isinstance(from_key, str) and from_key in sample_keys:
            candidates.append(((0 if arg_name in required else 1) - 1, arg_name))
            continue
        singular = (arg_name[:-1] if arg_name.endswith("s") and len(arg_name) > 1
                    else arg_name)
        if singular in sample_keys:
            candidates.append((0 if arg_name in required else 1, arg_name))
    if not candidates:
        return None
    candidates.sort(key=lambda t: (t[0], t[1]))
    return candidates[0][1]


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


def _resolve_from_step(args: dict, history: list[StepRun],
                       consumer_schema=None) -> dict:
    """Espande from_step: N → entries da step N (1-based), con proiezione
    consumer-arg (parità con agent_runtime.resolve_from_step Layer 4)."""
    if "from_step" not in args:
        return args
    n = args.get("from_step")
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
    # Proiezione consumer-arg: se lo schema del consumer dichiara un arg-lista
    # naturale (es. read_urls_html→`urls`) e non è già presente, proietta
    # entries[*].<key> in quell'arg. Senza questo, executor che consumano
    # `urls`/`paths`/`ids` (NON `entries`) ricevevano solo `entries` →
    # arg required mancante → terminator "Pipeline malformata o argomenti
    # insufficienti" (bug web pipeline find_urls→read_urls_html via engine v2).
    consumer_arg = _consumer_match_arg(consumer_schema, entries)
    if consumer_arg and consumer_arg not in out:
        match_key = None
        _spec = (consumer_schema.get("properties") or {}).get(consumer_arg) \
            if isinstance(consumer_schema, dict) else None
        if isinstance(_spec, dict):
            fek = _spec.get("from_entries_key")
            if isinstance(fek, str) and fek:
                match_key = fek
        if not match_key:
            match_key = (consumer_arg[:-1]
                         if consumer_arg.endswith("s") and len(consumer_arg) > 1
                         else consumer_arg)
        out[consumer_arg] = [e[match_key] for e in entries
                             if isinstance(e, dict) and e.get(match_key) is not None]
        return out
    out["entries"] = entries
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


from field_synonyms import resolve_dotted_with_synonyms as _resolve_dotted_with_synonyms_base


def _resolve_dotted_with_synonyms(obj, path: str):
    return _resolve_dotted_with_synonyms_base(obj, path, _resolve_dotted)


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


def _resolve_stepref(value: Any, history: list[StepRun]) -> Any:
    """Sostituisce ${stepN.field} (1-indexed) e ${steps.N.field} (0-indexed).
    Full-match preserva tipo nativo (list/dict/int), embedded stringifica.
    Fallback universal §7.9 su entries projection quando path diretto non
    risolve. Ricorsivo su nested dict/list.
    """
    if isinstance(value, dict):
        return {k: _resolve_stepref(v, history) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_stepref(x, history) for x in value]
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
            ans = llm_call(sys_msg, query, max_tokens=20, think=False)
            ans = (ans or "").strip().split("\n")[0].strip()
            if ans:
                return ans
        except Exception:
            pass
    return default or ""


# ── Step condition (skip-guard) ───────────────────────────────────────────

def _mutating_input_is_empty(step: StepSpec, history: list[StepRun]) -> bool:
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
        if 0 <= idx < len(history):
            res = history[idx].result if isinstance(history[idx].result, dict) else {}
            return not _step_list_payload(res)
        return False
    if isinstance(args.get("entries"), list):
        return len(args["entries"]) == 0
    if history:  # auto-wire: ultimo producer
        res = history[-1].result if isinstance(history[-1].result, dict) else {}
        return not _step_list_payload(res)
    return False


def _step_condition_passes(step: StepSpec, history: list[StepRun]) -> bool:
    """Skip-guard di uno step. Due regole:
    1. opt-in: step.if_prev_entries_nonempty=True + ultimo step entries vuote.
    2. AUTO (strutturale): mutante che consuma una lista d'input VUOTA."""
    if step.if_prev_entries_nonempty:
        if not history:
            return True
        last = history[-1]
        entries = last.result.get("entries") if isinstance(last.result, dict) else None
        if not entries:
            return False
    if _mutating_input_is_empty(step, history):
        return False
    return True


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
        last = history[-1]
        r = last.result if isinstance(last.result, dict) else {}
        if isinstance(r.get("summary"), str) and r["summary"].strip():
            template = f"${{step{last.step_idx}.summary}}"
        elif "entries" in r and isinstance(r["entries"], list) and r["entries"]:
            # Trigger auto-render via summary path (None → entries list)
            template = f"${{step{last.step_idx}.summary}}"
        elif "value" in r:
            template = f"${{step{last.step_idx}.value}}"
        else:
            template = f"${{step{last.step_idx}.@count}}"
    if not template:
        return ""
    def _sub_one(result, path):
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
        return _sub_one(history[n - 1].result, path)
    def _sub_steps(m):
        n = int(m.group(1))
        path = m.group(2)
        if not (0 <= n < len(history)):
            return ""
        return _sub_one(history[n].result, path)
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
})

# Arg `pattern`/glob: content-bearing SOLO se NON universale (25/6, turn
# 73476663). `pattern="*.py"` deriva dalla parola «python» della query → 0a-only
# (servirlo via cosine a «quanti file ci sono» dava 448 invece di 980). Ma
# `pattern="*"`/`"*.*"` = «tutti i file», nessuna informazione di query →
# resta NON query-specific (riusabile per cosine). Gestito a parte da
# is_query_specific perche' il valore-universale e' un'eccezione al literal.
_GLOB_ARG_KEYS = frozenset({"pattern", "patterns", "glob"})
_UNIVERSAL_GLOBS = frozenset({"*", "*.*", "**", ""})


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
    try:
        from mail_account_resolver import resolve_mail_account
        args = resolve_mail_account(tool, args, query)
    except Exception as _mre:
        log.debug("mail_account_resolver noop: %r", _mre)
    try:
        from from_contains_resolver import resolve_from_contains
        args = resolve_from_contains(tool, args, query)
    except Exception as _fce:
        log.debug("from_contains_resolver noop: %r", _fce)
    try:
        from junk_mail_resolver import resolve_junk_mail
        args = resolve_junk_mail(tool, args, query)
    except Exception as _jme:
        log.debug("junk_mail_resolver noop: %r", _jme)
    try:
        from time_window_resolver import resolve_time_window
        args = resolve_time_window(tool, args, query, args_schema=args_schema)
    except Exception as _twe:
        log.debug("time_window_resolver noop: %r", _twe)
    return args


def is_query_specific(framework_json: str) -> bool:
    """True se il framework incorpora un arg content-bearing LITERAL (non
    placeholder ${...}) → legato alla singola query.

    Conseguenze per i due layer con stato:
      - L1 autopath: non promuovibile a skill di cluster (avvelenerebbe le
        query sorelle col piano congelato di UNA query).
      - L0 fastpath: servibile SOLO via hash 0a (query identica → args giusti
        per costruzione), MAI via cosine 0b (query simile ma semanticamente
        diversa riuserebbe i literal sbagliati: «foto di X» vs «foto di Y»).
    Deterministico (§7.9): nessun LLM, solo ispezione args."""
    try:
        d = json.loads(framework_json)
    except Exception:
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
        for k in CONTENT_ARG_KEYS:
            v = args.get(k)
            for item in (v if isinstance(v, list) else [v]):
                if isinstance(item, str) and item.strip() and "${" not in item:
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


def _synthesize_final_from_steps(query: str, steps: list, llm_fast) -> str:
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
                        for k in ("name", "subject", "date", "title",
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
        out = llm_fast(sys_msg, user_msg, max_tokens=360, think=False)
        return (out or "").strip()
    except Exception as ex:
        log.warning("Executor: synthesize_final fallback failed: %r", ex)
        return ""


def _turn_is_zero_entries(steps) -> bool:
    """True se il turno è genuinamente a 0 risultati: lo step più recente con
    semantica di lista (`item_count` di describe_entries, o una chiave-payload
    `entries`/`results`/`lines`/`matches`) è VUOTO. False se non esiste alcuno
    step-lista (scalare puro, es. get_now → degenere per ALTRO motivo, la synth
    LLM resta corretta) o se l'ultima lista è non-vuota. Deterministico,
    model-independent — scandisce a ritroso e si ferma al primo segnale.
    """
    for s in reversed(steps or []):
        if getattr(s, "tool", "") == "final_answer":
            continue
        r = getattr(s, "result", None)
        if not isinstance(r, dict):
            continue
        ic = r.get("item_count")
        if isinstance(ic, int):
            return ic == 0
        # Conteggio esplicito (es. count_only: entries=[] MA available_total/count
        # > 0). §2.8: «quanti file» con entries materializzate vuote NON è zero
        # risultati — il numero È il risultato. Va consultato prima della lista,
        # altrimenti un conteggio legittimo viene reso «Nessun risultato».
        for ck in ("available_total", "count", "ok_count"):
            cv = r.get(ck)
            if isinstance(cv, int):
                return cv == 0
        for k in ("entries", "results", "lines", "matches"):
            v = r.get(k)
            if isinstance(v, list):
                return not v
    return False


def _deterministic_zero_result(steps) -> str:
    """§7.9 (deterministico>LLM) + §2.8 (onesto): messaggio finale per i turni
    a 0 entries, da provare PRIMA della synth LLM — evita una call `fast`
    spesa solo per dire «niente trovato». "" se il turno NON è a 0 entries
    (lascia la synth ai degeneri-ma-non-vuoti). Byte-riproducibile (i18n)."""
    return _msg("MSG_NO_RESULTS") if _turn_is_zero_entries(steps) else ""


class Executor:
    """Esegue Framework deterministicamente. SHARED fra tutti gli engine."""

    def __init__(self, *,
                 invoke_executor: Callable[[str, dict], dict],
                 llm_call_fast: Optional[Callable] = None,
                 vaglio_judge: Optional[Callable] = None,
                 vaglio_guard: Optional[Callable] = None,
                 max_steps: int = 12,
                 seed_steps: Optional[list] = None,
                 catalog: Optional[list] = None):
        self.invoke = invoke_executor
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
        for e in (catalog or []):
            nm = getattr(e, "name", None)
            if nm:
                self._schema_map[nm] = getattr(e, "args_schema", None)

    def run(self, framework: Framework, *,
            query: str = "",
            runtime_ctx: Optional[dict] = None,
            remediate_args_cb: Optional[Callable] = None,
            progress=None) -> RunResult:
        result = RunResult()
        # Seed-state (ADR 0177 M1): le history pre-esistenti (es. foto allegate
        # @uploaded) partono a 0-offset così `from_step=1` del primo step reale
        # le consuma. Copia per-run: il recovery ri-esegue con lo stesso seed.
        if self.seed_steps:
            result.steps = list(self.seed_steps)
        result.framework_hash = compute_framework_hash(framework)
        t_start = time.time()

        for i, step in enumerate(framework.steps):
            if i >= self.max_steps:
                result.aborted_reason = f"cap_steps {self.max_steps}"
                break
            if not step.tool:
                result.aborted_reason = f"step_{i+1}_no_tool"
                break

            # Branching: skip se condizione step non passa
            if not _step_condition_passes(step, result.steps):
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

            # Terminator
            if step.tool == "final_answer":
                rendered = _render_final_message(
                    framework.final_message, result.steps)
                # Universal §7.9: se rendered è "vuoto"/conteggio-only ma
                # l'ultimo step ha entries, auto-append entries list.
                if result.steps:
                    last_res = result.steps[-1].result
                    entries = (last_res.get("entries") if isinstance(last_res, dict)
                               else None)
                    if isinstance(entries, list) and entries:
                        # Detect "rendered è solo count" pattern
                        is_count_only = (
                            not rendered.strip()
                            or re.fullmatch(r"[\(\s]*\d+\s*(?:elementi|entries|elements|voci)?\s*[\)\s]*",
                                            rendered.strip())
                        )
                        if is_count_only:
                            bullets = _entries_bullet_lines(
                                entries, fields=_BULLET_FIELDS,
                                more_key="MSG_RENDER_AND_MORE")
                            rendered = (rendered.strip() + "\n\n" if rendered.strip() else "") + bullets
                # §2.8: render degenere (placeholder reso vuoto, es. get_now
                # "Sono le .") → sintetizza dalle observation via LLM fast.
                if _render_is_degenerate(framework.final_message, rendered):
                    # §7.9/§2.8: turno a 0 entries → messaggio onesto
                    # deterministico (no call LLM per dire «niente trovato»);
                    # la synth resta per i degeneri NON-vuoti (es. get_now).
                    zero = _deterministic_zero_result(result.steps)
                    if zero:
                        rendered = zero
                    else:
                        synth = _synthesize_final_from_steps(
                            query, result.steps, self.llm_fast)
                        if synth:
                            rendered = synth
                result.final_text = rendered
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
            _step_args = step.args
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
            # Resolve in ordine: from_step → stepref → fillers → runtime
            args = _resolve_from_step(
                _step_args, result.steps,
                consumer_schema=self._schema_map.get(step.tool))
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
            if (step.tool in _ENTRIES_CONSUMERS and result.steps
                    and (not args.get("entries")
                         or _detect_unresolved_placeholders(args.get("entries")))):
                for _prev in reversed(result.steps):
                    _pr = _prev.result if isinstance(_prev.result, dict) else {}
                    _pe = _step_list_payload(_pr)
                    if _pe:
                        args["entries"] = _pe
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
            args = {k: _resolve_stepref(v, result.steps) for k, v in args.items()}
            args = _resolve_fillers(args, framework.fillers, self.llm_fast, query)
            args = _resolve_runtime_placeholders(args, runtime_ctx or {})
            # Backend UNIFORME (lessons_learned.md §B): per gli object
            # multi-provider il RUNTIME risolve il provider (default per-creds +
            # esplicito da query), LLM-invisibile. Override deterministico — il
            # backend non è scelta del planner (ADR 0155: runtime proprietario).
            try:
                from backend_resolver import resolve_backend_arg
                args = resolve_backend_arg(step.tool, args, query)
            except Exception as _bre:
                log.debug("backend_resolver noop: %r", _bre)
            # Self-recipient UNIFORME (§7.9, gemello di backend_resolver): un send
            # via email senza destinatario esplicito esterno → destinatario =
            # actor ("inviami/alla mia email" = identità, non intento LLM; ADR
            # 0163/0155). Model-independent. Vedi self_recipient_resolver.py.
            try:
                from self_recipient_resolver import resolve_self_recipient
                args = resolve_self_recipient(step.tool, args, query)
            except Exception as _sre:
                log.debug("self_recipient_resolver noop: %r", _sre)
            # Calendario UNIFORME (gemello backend_resolver): QUALE calendario
            # (target NL fra gli owned, default primary) è configurazione risolta
            # dal runtime, non scelta dell'LLM. Vedi calendar_resolver.py.
            try:
                from calendar_resolver import resolve_calendar
                args = resolve_calendar(step.tool, args, query)
            except Exception as _cre:
                log.debug("calendar_resolver noop: %r", _cre)
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
            # Universal §7.9: convert list[dict] entries to 2D matrix
            # quando arg name è "values" (write_files_spreadsheet pattern).
            if isinstance(args.get("values"), list) and args["values"]:
                v0 = args["values"][0]
                if isinstance(v0, dict):
                    # È list[dict] → convert a 2D matrix
                    args["values"] = _entries_to_2d_matrix(args["values"])

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
                _deepcrawl = re.search(
                    r"(esplor|mappa|archivi|scandagli|ricorsiv|intero sito|"
                    r"tutto il sito|crawl|approfondit|exhaustive|entire site|"
                    r"whole site|recursiv|\bexplore)", (query or "").lower())
                if not _deepcrawl:
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
                if isinstance(prev.get("attachments"), list) and prev["attachments"]:
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
                    _explicit = bool(args.get("context")) or bool(args.get("group_by"))
                    if isinstance(_hint, str) and _hint.strip() and not _explicit:
                        skip_reason = "final_message_hint_present"
                        skip_result["summary"] = _hint.strip()
                        log.info("Executor: skip describe_entries (prev step "
                                 "self-presents via final_message_hint)")
                if skip_reason:
                    skip_result["skipped"] = skip_reason
                    lat_ms = 0
                    step_idx = len(result.steps) + 1
                    result.steps.append(StepRun(
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
                result.steps.append(StepRun(
                    step_idx=step_idx, tool=step.tool, args=args,
                    result=r, ok=False, latency_ms=lat_ms,
                ))
                continue

            # Notifica in-piano nei run schedulati a vuoto (§2.8, 13/6/2026):
            # un send/notify finale («ti ho fatto X») NON deve partire se la
            # pipeline a monte non ha prodotto nulla (bug live: maintenance
            # github → send_messages su 0 issue aperte = falso successo). La
            # soppressione del push SCHEDULER non copre il send IN-PIANO.
            # Generale §7.3, deterministico §7.9, no-op sui turni interattivi.
            try:
                from treated_issues_guard import suppress_scheduled_notify
                if suppress_scheduled_notify(step.tool, result.steps):
                    log.info("[scheduled-notify-guard] skip %s: pipeline a "
                             "vuoto, notifica soppressa (run schedulato, §2.8)",
                             step.tool)
                    result.steps.append(StepRun(
                        step_idx=i + 1, tool=step.tool, args=args,
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
            if (step.tool == "get_approval"
                    and (runtime_ctx or {}).get("_gate_approved")):
                args["_pre_approved"] = True

            # Invoke
            # Osservabilità (#4): logga tool + args risolti (escluso il payload
            # `entries`, voluminoso) appena prima dell'invoke. Senza, un turn
            # engine v2 che si blocca su un executor è una scatola nera.
            log.info("Executor: invoke %s args=%s", step.tool,
                     {k: v for k, v in args.items()
                      if k not in ("entries",)})
            # Breadcrumb live in chat: emette `tool_call` sul progress (sia
            # TurnEventProgress sia _SSEProgress lo accettano). Senza, engine v2
            # mostrava solo `start` poi `final` (⏳ muto per tutto il turno).
            if progress is not None and hasattr(progress, "tool_call"):
                try:
                    progress.tool_call(
                        tool=step.tool, step_num=len(result.steps) + 1,
                        path_so_far=[s.tool for s in result.steps] + [step.tool],
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
            if not (runtime_ctx or {}).get("_gate_approved"):
                try:
                    from args_resolver import scope_form_request
                    _form_obs = scope_form_request(
                        step.tool, args, self._schema_map.get(step.tool), query)
                except Exception as _fe:
                    log.debug("scope_form_request noop: %r", _fe)
            # Vaglio GUARD pre-invoke (sicurezza, gap confermato 23/6): blocca le
            # mutazioni su forbidden-path (~/.ssh, /etc/shadow, .aws/credentials,
            # /boot...) PRIMA di eseguirle. Il legacy chiama judge() prima
            # dell'invoke; l'engine (path di prod) NON lo faceva → regressione
            # silenziosa sul nucleo non-negoziabile. Solo la GUARDIA
            # deterministica (forbidden-path/shell), NON il giudice teleologico.
            # Pre-invoke = prevenzione vera, non blocco-a-valle.
            if self.vaglio_guard is not None and _form_obs is None:
                try:
                    _ok_g, _why_g = self.vaglio_guard(step.tool, args)
                except Exception as _ge:
                    _ok_g, _why_g = True, None  # best-effort: fail-open
                    log.warning("vaglio_guard raised %r — fail-open", _ge)
                if not _ok_g:
                    log.warning("[vaglio guard] BLOCCO pre-invoke %s: %s",
                                step.tool, _why_g)
                    result.steps.append(StepRun(
                        step_idx=i + 1, tool=step.tool, args=args,
                        result={"ok": False, "error_class": "vaglio_guard",
                                "error": _why_g or "forbidden"},
                        ok=False, latency_ms=0))
                    result.aborted_reason = f"step_{i+1}_vaglio_guard"
                    result.final_kind = "error"
                    break
            t0 = time.time()
            if _form_obs is not None:
                r = _form_obs
            else:
                try:
                    r = self.invoke(step.tool, args)
                except Exception as ex:
                    if type(ex).__name__ == "TimeoutExpired":
                        # §11/§2.8: messaggio CHIARO invece del grezzo
                        # "Command '[...python...]' timed out after Ns".
                        _to = getattr(ex, "timeout", None)
                        log.warning("Executor: %s timeout (%ss)", step.tool, _to)
                        r = {"ok": False, "error_class": "timeout",
                             "error": _msg("ERR_EXECUTOR_TIMEOUT",
                                           tool=step.tool, seconds=int(_to or 0))}
                    else:
                        log.warning("Executor: %s raised %r", step.tool, ex)
                        r = {"ok": False, "error": str(ex),
                             "error_class": "exception"}
            lat_ms = int((time.time() - t0) * 1000)

            # Recovery args remediate (1× per step) — mai per needs_inputs
            if _form_obs is None and not r.get("ok") and remediate_args_cb is not None:
                try:
                    fixed = remediate_args_cb(tool=step.tool, args=args,
                                                result=r, query=query)
                    if fixed and fixed != args:
                        log.info("Executor: retry %s with remediated args",
                                  step.tool)
                        t0 = time.time()
                        r = self.invoke(step.tool, fixed)
                        lat_ms = int((time.time() - t0) * 1000)
                        args = fixed
                except Exception as ex:
                    log.warning("remediate_args_cb raised %r", ex)

            # Cattura scope-arg: ultimo valore usato → default per il giro dopo
            # (§7.9, no LLM). Vedi args_resolver.remember_scope_args.
            if isinstance(r, dict) and r.get("ok"):
                try:
                    from args_resolver import remember_scope_args
                    remember_scope_args(step.tool, args,
                                        actor=args.get("_actor") or "host")
                except Exception as _rse:
                    log.debug("remember_scope_args noop: %r", _rse)

            sr = StepRun(step_idx=i + 1, tool=step.tool, args=args,
                          result=r, ok=bool(r.get("ok")), latency_ms=lat_ms)
            result.steps.append(sr)
            if sr.ok:
                result.ok_count += 1

            # §7.3: needs_inputs decision → terminate immediately. Il caller
            # (agent_runtime._try_engine_v2) gestisce il dialog_pending +
            # form rendering. NON proseguire con steps successivi.
            if r.get("decision") == "needs_inputs":
                result.final_kind = "ask"
                result.final_text = ""
                break

            # §7.9 gate-resume (20/6/2026): get_approval (decision=input_required)
            # mette in PAUSA la pipeline; gli step a valle (send/write) NON
            # girano finche' l'utente non approva (senza la pausa il post
            # partirebbe SENZA consenso). Il bridge persiste il contesto di
            # ripresa nel dialog (on_complete resume_engine_gate); on-approve la
            # pipeline si riesegue col gate auto-passato (pre-gate read-only per
            # convenzione → re-query idempotente). Scope-limitato a get_approval
            # (get_inputs usa lo stesso decision ma e' gestito a monte).
            if (step.tool == "get_approval"
                    and r.get("decision") == "input_required"):
                result.final_kind = "ask"
                result.final_text = r.get("final_message_hint") or ""
                result.gate_dialog_id = r.get("dialog_id") or ""
                break

            # Vaglio post-step (opt-in)
            if self.vaglio is not None:
                try:
                    if not self.vaglio(step.tool, args, r):
                        result.aborted_reason = f"step_{i+1}_vaglio_block"
                        result.final_kind = "error"
                        break
                except Exception as ex:
                    log.warning("vaglio raised %r — fail-open", ex)

            # Stop on hard error (no recovery attempted)
            if not sr.ok:
                result.aborted_reason = f"step_{i+1}_error"
                result.final_kind = "error"
                break

        result.elapsed_ms = int((time.time() - t_start) * 1000)
        if not result.final_kind:
            result.final_kind = "error" if result.aborted_reason else "answer"
        if result.final_kind == "answer" and not result.final_text:
            result.final_text = _render_final_message(
                framework.final_message, result.steps)
            # §2.8: se anche il re-render è vuoto/degenere → sintesi LLM.
            if (not result.final_text.strip()
                    or _render_is_degenerate(framework.final_message,
                                              result.final_text)):
                zero = _deterministic_zero_result(result.steps)
                if zero:
                    result.final_text = zero
                else:
                    synth = _synthesize_final_from_steps(
                        query, result.steps, self.llm_fast)
                    if synth:
                        result.final_text = synth
        return result
