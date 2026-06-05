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
    """
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
    out: dict = {}
    for k, v in args.items():
        if isinstance(v, str):
            m = _FILLER_RE.search(v)
            if m:
                name = m.group(1)
                spec = fillers.get(name)
                if spec is None:
                    out[k] = v  # placeholder non risolto, lascia letterale
                    continue
                resolved = _resolve_one_filler(name, spec, llm_call, query)
                out[k] = _FILLER_RE.sub(resolved, v)
                continue
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
            sys_msg = (f"Rispondi con UN solo valore breve per il filler "
                        f"`{name}`. {prompt}")
            ans = llm_call(sys_msg, query, max_tokens=20, think=False)
            ans = (ans or "").strip().split("\n")[0].strip()
            if ans:
                return ans
        except Exception:
            pass
    return default or ""


# ── Step condition (skip-guard) ───────────────────────────────────────────

def _step_condition_passes(step: StepSpec, history: list[StepRun]) -> bool:
    """Se step.if_prev_entries_nonempty=True e ultimo step ha entries vuote
    → skip (return False)."""
    if not step.if_prev_entries_nonempty:
        return True
    if not history:
        return True
    last = history[-1]
    entries = last.result.get("entries") if isinstance(last.result, dict) else None
    return bool(entries)


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


_TEMPLATE_FALLBACK_BY_OUTPUT = {
    "scalar_metric": "${stepN.@count}",
    "free_text": "${stepN.summary}",
    "file_entry[]": "${stepN.@count} file",
    "message_entry[]": "${stepN.@count} messaggi",
    "event_entry[]": "${stepN.@count} eventi",
    "person_entry[]": "${stepN.@count} persone",
}


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
        # Universal §7.9 fallback: prova path diretto, poi entries[*].field
        v = _resolve_stepref_with_fallback(result, path)
        # Se path richiesto è "summary" e None, auto-render entries list (§7.9)
        if v is None and path == "summary":
            entries = result.get("entries", [])
            if isinstance(entries, list) and entries:
                lines = []
                for e in entries[:20]:  # cap a 20 elementi per readability
                    if isinstance(e, dict):
                        # Pattern universal: prendi i primi 2-3 field testuali
                        bits = []
                        for k in ("start", "summary", "subject", "title",
                                  "name", "path", "url", "date"):
                            if k in e and e[k]:
                                bits.append(str(e[k])[:60])
                            if len(bits) >= 3:
                                break
                        lines.append("- " + " | ".join(bits))
                    else:
                        lines.append(f"- {str(e)[:80]}")
                more = len(entries) - 20
                tail = ("\n" + _msg("MSG_RENDER_MORE_HIDDEN", more=more)) if more > 0 else ""
                return "\n".join(lines) + tail
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
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


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
    import json as _json
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
                for e in entries[:5]:
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
                        for k in ("name", "subject", "title", "description",
                                  "value", "summary", "email", "role", "path",
                                  "date", "status"):
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
                                    items.append(iv or _json.dumps(
                                        it, ensure_ascii=False)[:60])
                                else:
                                    items.append(str(it)[:60])
                            if items:
                                fields.append(f"{k}=[{', '.join(items)}]")
                    ebits.append(" ".join(fields) if fields else _json.dumps(
                        {k: v for k, v in e.items()
                         if not isinstance(v, (dict, list))},
                        ensure_ascii=False)[:200])
                parts.append(f"entries[{len(entries)}]: " + " | ".join(ebits))
            if not parts:
                slim = {k: v for k, v in res.items()
                        if k not in ("ok", "metadata", "attachments", "entries")}
                parts.append(_json.dumps(slim, ensure_ascii=False)[:400])
            obs_lines.append(f"{tool}: " + " ; ".join(parts))
    if not obs_lines:
        return ""
    sys_msg = (
        "Sei l'assemblatore della risposta finale. Data la richiesta utente e "
        "i risultati degli strumenti, scrivi UNA risposta diretta, concisa, in "
        "linguaggio naturale, nella lingua della richiesta. Niente preamboli, "
        "niente JSON, niente placeholder. I risultati contengono dati gia' "
        "recuperati e autorizzati per l'utente proprietario che li richiede: se "
        "l'informazione richiesta e' presente nei risultati, RIPORTALA "
        "fedelmente. NON rifiutare e NON dire di non avervi accesso."
    )
    user_msg = (
        f"Richiesta: {query}\n\nRisultati strumenti:\n" + "\n".join(obs_lines)
        + "\n\nRisposta:"
    )
    try:
        out = llm_fast(sys_msg, user_msg, max_tokens=160, think=False)
        return (out or "").strip()
    except Exception as ex:
        log.warning("Executor: synthesize_final fallback failed: %r", ex)
        return ""


class Executor:
    """Esegue Framework deterministicamente. SHARED fra tutti gli engine."""

    def __init__(self, *,
                 invoke_executor: Callable[[str, dict], dict],
                 llm_call_fast: Optional[Callable] = None,
                 vaglio_judge: Optional[Callable] = None,
                 max_steps: int = 12,
                 catalog: Optional[list] = None):
        self.invoke = invoke_executor
        self.llm_fast = llm_call_fast
        self.vaglio = vaglio_judge
        self.max_steps = max_steps
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
                        import re as _re_render
                        is_count_only = (
                            not rendered.strip()
                            or _re_render.fullmatch(r"[\(\s]*\d+\s*(?:elementi|entries|elements|voci)?\s*[\)\s]*",
                                                     rendered.strip())
                        )
                        if is_count_only:
                            lines = []
                            for e in entries[:20]:
                                if isinstance(e, dict):
                                    bits = []
                                    for k in ("start", "summary", "subject",
                                              "title", "name", "path", "url"):
                                        if k in e and e[k]:
                                            bits.append(str(e[k])[:60])
                                        if len(bits) >= 3:
                                            break
                                    lines.append("- " + " | ".join(bits))
                                else:
                                    lines.append(f"- {str(e)[:80]}")
                            more = len(entries) - 20
                            tail = ("\n" + _msg("MSG_RENDER_AND_MORE", more=more)) if more > 0 else ""
                            rendered = (rendered.strip() + "\n\n" if rendered.strip() else "") + "\n".join(lines) + tail
                # §2.8: render degenere (placeholder reso vuoto, es. get_now
                # "Sono le .") → sintetizza dalle observation via LLM fast.
                if _render_is_degenerate(framework.final_message, rendered):
                    synth = _synthesize_final_from_steps(
                        query, result.steps, self.llm_fast)
                    if synth:
                        rendered = synth
                result.final_text = rendered
                result.final_kind = "answer"
                break

            # Resolve in ordine: from_step → stepref → fillers → runtime
            args = _resolve_from_step(
                step.args, result.steps,
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
                    import re as _re_tk
                    if not _re_tk.search(r"\b\d+\b", query or ""):
                        args["top_k"] = 100

            # Universal §7.3 (safety net deterministico): il Proposer vede solo
            # gli enum di `mode` (non la loro descrizione) e tende a scegliere
            # 'research' per query informative ('cerca informazioni su X') →
            # crawl ricorsivo fino a 900s che blocca il turno interattivo.
            # Downgrade research/archive → default salvo intento ESPLICITO di
            # esplorare/archiviare un INTERO sito. Causa generalizzata: enum
            # pericoloso scelto senza la semantica dell'arg (§7.9 code>LLM).
            if step.tool == "find_urls" and args.get("mode") in ("research", "archive"):
                import re as _re_mode
                _deepcrawl = _re_mode.search(
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
            if step.tool == "describe_entries" and result.steps:
                prev = result.steps[-1].result if isinstance(result.steps[-1].result, dict) else {}
                if isinstance(prev.get("attachments"), list) and prev["attachments"]:
                    log.info("Executor: skip describe_entries (prev step has %d attachments)",
                              len(prev["attachments"]))
                    lat_ms = 0
                    step_idx = len(result.steps) + 1
                    result.steps.append(StepRun(
                        step_idx=step_idx, tool=step.tool, args=args,
                        result={"ok": True, "skipped": "attachments_present"},
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
            t0 = time.time()
            try:
                r = self.invoke(step.tool, args)
            except Exception as ex:
                log.warning("Executor: %s raised %r", step.tool, ex)
                r = {"ok": False, "error": str(ex), "error_class": "exception"}
            lat_ms = int((time.time() - t0) * 1000)

            # Recovery args remediate (1× per step)
            if not r.get("ok") and remediate_args_cb is not None:
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
                synth = _synthesize_final_from_steps(
                    query, result.steps, self.llm_fast)
                if synth:
                    result.final_text = synth
        return result
