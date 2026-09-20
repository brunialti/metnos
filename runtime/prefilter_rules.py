"""prefilter_rules — porting selettivo delle rules §7.3 dal simulator.

Aggiunge score boost/penalty a `prefilter.affinity_score()` basati su:
  - PATH/EXT pattern detection (es. /Immagini/ → find_images_indices boost)
  - QUERY pattern detection (es. "ultimi N" → get_files boost)
  - PRODUCER compatibility (verb → tool family)
  - RARE-TOKEN unmatched penalty
  - INPUT COVERAGE (rule 10.5, typed): required+source inputs coperti
    da semantic-type signals nella query → boost; non coperti → demote
  - SCHEMA FIELD MATCH (rule 11.0, typed): output.schema field names
    presenti nei token query → boost

Le rules string-only non richiedono ExecutorRegistry tipato del simulator.
Le rules tipate (10.5, 11.0) leggono `runtime/executor_typing.py` che a sua
volta legge i 84 typing JSON in `tests/simulator/typing_cache/`. Se un typing
manca per un executor, le rule tipate sono no-op (graceful degrade §2.8).

Reference: bench 446q baseline prefilter 47% top-1 → atteso 65-75% post-rules.
Source rules: tests/simulator/graph_search_v2.py SIM_RULE_* + curator data.

Wiring: enable via env METNOS_PREFILTER_RULES=1 in `prefilter.affinity_score`.
"""
from __future__ import annotations

import re
from typing import Optional


# Closed schema-class identities; translations can add surface forms but cannot
# invent a new equivalence class or change lookup priority.
_KEY_CLASS_ORDER = (
    "hash", "date", "size", "name", "pattern", "channel", "limit",
    "author",
)


def _same_class(a: str, b: str) -> bool:
    """True when two localized field names share one canonical class."""
    a, b = (a or "").lower(), (b or "").lower()
    if a == b:
        return True
    from detection_lexicon_seed_routing import mapping

    classes = mapping("routing.rule.key_class")
    ca = next((name for name in _KEY_CLASS_ORDER
               if a in classes.get(name, ())), None)
    cb = next((name for name in _KEY_CLASS_ORDER
               if b in classes.get(name, ())), None)
    return ca is not None and ca == cb


# ─── Detection semantic-type signals nella query ───────────────────────────
# Heuristica deterministica §7.9: riconosce signal di tipo semantico da
# pattern nel raw query. Equivalente "degraded" di query.inputs[].semantic_type
# del simulator (che lì viene dal parser strutturato).
#
# Mapping: regex query → semantic_type detected.
_SEMANTIC_TYPE_DETECTORS = [
    # File system: path assoluti POSIX + Windows
    # Path che terminano con / OR contengono trailing slash → dir_path
    (re.compile(r"(?:^|\s)(?:/|~/)[\w./\-]*/(?:\s|$)", re.UNICODE), "dir_path"),
    # Path con extension (file noto) → file_path; path generico bare → both
    (re.compile(r"(?:^|\s)(?:/|~/)[\w./\-]+\.\w+\b", re.UNICODE), "file_path"),
    (re.compile(r"(?:^|\s)(?:/|~/)[\w./\-]+", re.UNICODE), "file_path"),
    (re.compile(r"\b[A-Za-z]:\\[\w\\.\-]+"), "file_path"),
    (re.compile(r"[\w/]*\*[\w/]*"), "glob_pattern"),
    (re.compile(r"\.(?:jpg|jpeg|png|heic|webp|gif)\b", re.I), "image_path"),
    (re.compile(r"\.(?:pdf)\b", re.I), "pdf_path"),
    (re.compile(r"\.(?:csv|tsv)\b", re.I), "file_path"),
    # Network
    (re.compile(r"\bhttps?://\S+", re.I), "url"),
    # People / identity
    (re.compile(r"\b[\w.\-]+@[\w.\-]+\.\w+\b"), "email_address"),
    # ISO timestamps
    (re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T\s]\d{2}:\d{2})?\b"), "iso_timestamp"),
    # Person name: SOLO due parole capitalizzate consecutive (Nome Cognome)
    # OR a localized explicit preposition plus a capitalized word. Conservativo: la
    # detection di singola capitalized word causa troppi falsi positivi
    # ("Downloads", "Roma", "Junk" matchavano).
    (re.compile(r"\b[A-ZÀ-ÖØ-Þ][a-zà-öø-þ]+\s+[A-ZÀ-ÖØ-Þ][a-zà-öø-þ]+\b"),
     "person_name"),
]


def _detect_semantic_types(query_raw: str) -> set[str]:
    """Ritorna set di semantic_type detectati nel query.

    Heuristica conservativa: solo signal forti (path/url/email/glob/iso) +
    keyword time/person. Falsi positivi su person_name (capitalizzato) sono
    accettati perche' la rule e' un BOOST (no demote per absence).
    """
    if not query_raw:
        return set()
    found: set[str] = set()
    for rx, stype in _SEMANTIC_TYPE_DETECTORS:
        if rx.search(query_raw):
            found.add(stype)
    from detection_lexicon_seed_routing import forms, matches
    if matches("routing.rule.time_window", query_raw):
        found.add("time_window")
    person_prefixes = forms("routing.rule.person_prefix")
    if person_prefixes and re.search(
            r"\b(?:" + "|".join(re.escape(form) for form in person_prefixes)
            + r")\s+[A-ZÀ-ÖØ-Þ][a-zà-öø-þ]{2,}\b", query_raw):
        found.add("person_name")
    return found


# Compatibility semplificata produced→required (subset di types_semantic.py).
# Solo casi rilevanti per rules: downcasting file_path/dir_path families.
_TYPE_PARENTS = {
    "image_path": ("file_path",),
    "pdf_path": ("file_path",),
    "audio_path": ("file_path",),
    "video_path": ("file_path",),
    "text_path": ("file_path",),
    "dir_path": (),
    "file_path": (),
    "url": (),
    "email_address": (),
    "person_name": (),
    "time_window": (),
    "iso_timestamp": (),
    "glob_pattern": (),
    "account_name": (),
    "name": (),
    "free_text": (),
    "count": ("scalar_metric",),
    "size_bytes": ("scalar_metric",),
    "scalar_metric": (),
    "bool": (),
}


def _type_compatible(provided: str, required: str) -> bool:
    """True se `provided` semantic_type soddisfa `required` (con downcast)."""
    if not provided or not required:
        return False
    if provided == required:
        return True
    parents = _TYPE_PARENTS.get(provided, ())
    for p in parents:
        if _type_compatible(p, required):
            return True
    return False


# ─── Rule 1: PATH/EXT pattern boosts ────────────────────────────────────────
# Detect signal nel query (path absolutes, estensioni, dir comuni) e mappa
# a tool boost. Source: simulator constraint_promote.

_PATH_PATTERNS = [
    # (regex query, target_tool_substring, boost_score, description)
    (re.compile(r"\.csv\b", re.I),
     "read_files_csv", 4, ".csv ext → csv reader"),
    (re.compile(r"\.pdf\b", re.I),
     "read_files_pdf", 4, ".pdf ext → pdf reader"),
    (re.compile(r"\.json\b", re.I),
     "read_files_json", 3, ".json ext → json reader"),
    (re.compile(r"/etc/|/var/log/|/proc/", re.I),
     "read_files", 3, "system path → read_files"),
    (re.compile(r"https?://[\w.-]+/?", re.I),
     "read_urls_html", 2, "URL → read_urls"),
    (re.compile(r"\bgithub\.com\b|\bgithub\b", re.I),
     "read_urls_html", 5, "github → read_urls (strong)"),
]


# ─── Demote PATH/EXT patterns: tool name match → score penalty ─────────────
# Equivalente di simulator RULE 20.a: web content URL (github/wiki/blog) →
# demote get_urls (che ritorna raw bytes, sbagliato per HTML content).
# Universal §7.3: deriva da semantica del URL (host pattern), no hardcoded query.
_PATH_DEMOTE_PATTERNS = [
    # GitHub URLs → demote get_urls (-3): contesto sempre web content.
    (re.compile(r"\bgithub(?:\.com)?\b", re.I), "get_urls", -3,
     "github → demote get_urls (binary fetch wrong)"),
    # Web page extension on URL → demote get_urls.
    (re.compile(r"https?://\S+\.(?:html?|md|wiki|asp|aspx|jsp|php)\b", re.I),
     "get_urls", -3, "html URL → demote get_urls"),
    # Wiki/blog/repo path patterns su URL.
    (re.compile(r"https?://\S+/(?:blob|raw|tree|wiki|issues|pulls?|commits?"
                r"|releases|discussions|comments?|reviews?)/", re.I),
     "get_urls", -3, "github-style URL path → demote get_urls"),
]


# ─── Rule 2: QUERY pattern boosts ───────────────────────────────────────────
# Pattern lessicali → tool family preference.

# Concept identifiers and target tool substrings are closed routing protocol,
# not user-language data.  Order and weights preserve the historical scorer.
_QUERY_PATTERN_BOOSTS = [
    ("routing.rule.boost.recent_count", "get_files", 4),
    ("routing.rule.boost.recent_messages", "list_messages", 6),
    ("routing.rule.boost.read_messages", "read_messages", 6),
    ("routing.rule.boost.send_messages", "send_messages", 8),
    ("routing.rule.boost.move_spam", "move_messages", 8),
    ("routing.rule.boost.find_files", "find_files", 4),
    ("routing.rule.boost.delete_files", "delete_files", 4),
    ("routing.rule.boost.write_files", "write_files", 5),
    ("routing.rule.boost.tasks", "list_tasks", 4),
    ("routing.rule.boost.count", "compute_entries", 3),
    ("routing.rule.boost.places", "find_places", 8),
    ("routing.rule.boost.github_metrics", "read_urls_html", 8),
    ("routing.rule.boost.location", "get_location", 15),
    ("routing.rule.boost.now", "get_now", 15),
    ("routing.rule.boost.processes", "get_processes", 8),
    ("routing.rule.boost.appointments", "read_events", 6),
    ("routing.rule.boost.events", "read_events", 6),
    ("routing.rule.boost.paired", "read_persons", 5),
    ("routing.rule.boost.enrolled", "get_persons", 5),
    ("routing.rule.boost.sort", "sort_entries", 3),
]


# ─── Rule 3: PRODUCER compatibility ─────────────────────────────────────────
# Verb → compatible producer prefix list. Cap small boost +1 for compatible.

_PRODUCER_COMPAT = {
    "list":    ("list_", "find_", "read_"),
    "find":    ("find_", "read_", "get_"),
    "read":    ("read_", "find_", "get_"),
    "get":     ("get_", "find_", "read_"),
    "compute": ("find_", "get_", "read_", "list_"),
    "describe": ("describe_",),
    "filter":  ("filter_",),
    "sort":    ("sort_",),
    "classify": ("classify_",),
    "send":    ("send_",),
    "write":   ("write_", "create_"),
    "create":  ("create_", "write_"),
    "delete":  ("delete_", "move_"),
    "move":    ("move_", "delete_"),
    "change":  ("change_",),
}


# ─── Rule 4: RARE-TOKEN unmatched penalty ──────────────────────────────────
# Token rari nella query NON matchati con executor → penalty.
# Cache deterministica del corpus rare tokens.

_RARE_TOKENS_CACHE: Optional[set[str]] = None
_RARE_TOKENS_CATALOG_ID: Optional[str] = None


def _build_rare_tokens(catalog) -> set[str]:
    """Token che appaiono in ≤3 executor (rare = indicativi quando matchano)."""
    from collections import Counter
    from prefilter import tokenize, stopwords
    ignored = stopwords()
    cnt = Counter()
    for e in catalog:
        toks = set()
        toks.update(tokenize(e.name))
        toks.update(tokenize(e.description))
        for tag in (e.affinity or []):
            toks.update(tokenize(tag))
        for tk in toks:
            if tk in ignored or len(tk) <= 2:
                continue
            cnt[tk] += 1
    return {tk for tk, c in cnt.items() if c <= 3}


def init_rare_tokens(catalog) -> None:
    """Build the token index once per common RM-0008 catalog identity."""
    global _RARE_TOKENS_CACHE, _RARE_TOKENS_CATALOG_ID
    from executor_catalog_identity import catalog_identity
    catalog = list(catalog)
    identity = catalog_identity(catalog)
    if _RARE_TOKENS_CACHE is None or _RARE_TOKENS_CATALOG_ID != identity:
        _RARE_TOKENS_CACHE = _build_rare_tokens(catalog)
        _RARE_TOKENS_CATALOG_ID = identity


def compute_input_coverage_score(query_tokens: set, query_raw: str,
                                   query_canonical_verb: Optional[str],
                                   executor) -> int:
    """Rule 10.5 (typed) — input coverage ratio.

    Per ogni executor con typing disponibile:
      - estrai required+role=source inputs (`base_path`, `urls`, ecc.)
      - calcola overlap fra i loro semantic_type e i semantic-type signals
        detectati nel raw query
      - boost +6 * ratio se ratio > 0
      - demote -3 se ratio < 0.5 (l'executor richiede input non disponibili)

    Se l'executor non ha typing o non ha required+source inputs, la rule e'
    no-op (ritorna 0). Senza signal detectati nel query → 0 (non penalizza
    perche' molti pattern naturali non hanno path/URL espliciti).

    Source: tests/simulator/graph_search_v2.py:1209-1233 (SIM_RULE_10_5).
    Degradazione vs simulator: la versione simulator usa query.inputs[].
    semantic_type dal parser strutturato; qui dipendiamo da heuristica
    regex su query_raw (universal §7.3, no LLM, no parser dipendenza).
    """
    try:
        from executor_typing import get_typing, required_source_inputs
    except Exception:
        return 0
    typing = get_typing(executor.name)
    if not typing:
        return 0
    req_source = required_source_inputs(typing)
    if not req_source:
        return 0
    q_types = _detect_semantic_types(query_raw or "")
    if not q_types:
        return 0  # no signal: niente da scoring, evita falsa penalty
    covered = 0
    for inp in req_source:
        req_type = inp.get("semantic_type", "")
        if not req_type:
            continue
        # Strip list suffix per matching (es. file_path[] → file_path)
        req_atom = req_type[:-2] if req_type.endswith("[]") else req_type
        if any(_type_compatible(qt, req_atom) for qt in q_types):
            covered += 1
    ratio = covered / len(req_source)
    if ratio == 0:
        # Nessun input coperto MA executor ha required+source: tool sbagliato.
        # Demote -1 (lieve: schema/affinity restano dominanti, query ambigui).
        return -1
    return int(round(6 * ratio))  # boost positivo proporzionale


def compute_schema_field_score(query_tokens: set, executor) -> int:
    """Rule 11.0 (typed) — schema field match.

    Bonus quando i field name esposti dall'executor (`output.schema.keys`)
    sono menzionati nei query_tokens (direct o via _KEY_CLASSES synonym).

    Score:
      +3 per ciascun match diretto (cap a +6 = 2 match)
      +2 per ciascun match via synonym class (cap aggregato +6)

    Esempio: query "ordinati per data" → schema con `mtime`, `date`,
    `modified_at` riceve +3 (stesso _KEY_CLASSES bucket di "data").

    Source: tests/simulator/graph_search_v2.py:1312-1352 (SIM_RULE_SCHEMA).
    Degradazione: simulator usa query.constraints[] strutturato; qui
    matchiamo direttamente sui token query (subset di constraint.key).
    """
    if not query_tokens:
        return 0
    try:
        from executor_typing import get_typing, output_schema_keys
    except Exception:
        return 0
    typing = get_typing(executor.name)
    if not typing:
        return 0
    schema_keys = output_schema_keys(typing)
    if not schema_keys:
        return 0
    # Filtra schema "envelope" non semantici (gia' presenti in quasi tutti)
    SCHEMA_ENVELOPE = {"entries", "ok", "ok_count", "fail_count", "failed",
                       "error", "available_total", "cap_field", "cap_value",
                       "truncated", "truncated_what", "used"}
    semantic_keys = schema_keys - SCHEMA_ENVELOPE
    if not semantic_keys:
        return 0
    direct_hits = 0
    synonym_hits = 0
    for tok in query_tokens:
        tl = tok.lower()
        if tl in semantic_keys:
            direct_hits += 1
            continue
        # Synonym fallback (stessa classe _KEY_CLASSES)
        for sk in semantic_keys:
            if _same_class(tl, sk):
                synonym_hits += 1
                break
    score = min(direct_hits * 3, 6) + min(synonym_hits * 2, 6)
    return min(score, 8)  # cap aggregato


def compute_rule_boost(query: str, query_tokens: set,
                        query_canonical_verb: Optional[str],
                        executor) -> int:
    """Ritorna boost score complessivo dalle 6 rule categorie.

    Args:
      query: query raw (per regex match).
      query_tokens: set di token tokenize(query) (riusato da affinity_score).
      query_canonical_verb: verbo canonico detectato (None se ambiguo).
      executor: oggetto Executor (name, description, affinity).

    Returns:
      int delta score. Positivo = boost, negativo = penalty.
    """
    name = executor.name
    score = 0

    # Rule 1: PATH/EXT (boost positivi e demote negativi)
    from detection_lexicon_seed_routing import matches
    if (matches("routing.rule.image_folder", query)
            and "find_images_indices" in name):
        score += 5
    for rx, target_sub, boost, _desc in _PATH_PATTERNS:
        if rx.search(query) and target_sub in name:
            score += boost
    for rx, target_sub, penalty, _desc in _PATH_DEMOTE_PATTERNS:
        if rx.search(query) and target_sub in name:
            score += penalty  # penalty già negativo

    # Rule 2: QUERY PATTERN
    for concept, target_sub, boost in _QUERY_PATTERN_BOOSTS:
        if matches(concept, query) and target_sub in name:
            score += boost

    # Rule 3: PRODUCER compat
    if query_canonical_verb:
        prefixes = _PRODUCER_COMPAT.get(query_canonical_verb)
        if prefixes and any(name.startswith(p) for p in prefixes):
            score += 1

    # Rule 4: RARE-TOKEN penalty
    # Se la query ha rare tokens NON catturati da NESSUN match (hard/soft) →
    # penalty -1. Implementato in `apply_rare_penalty` esterno perché richiede
    # contesto su quali token sono già matched dal main affinity_score.

    # Rule 5 (typed): INPUT COVERAGE
    score += compute_input_coverage_score(query_tokens, query,
                                           query_canonical_verb, executor)

    # Rule 6 (typed): SCHEMA FIELD MATCH
    score += compute_schema_field_score(query_tokens, executor)

    return score


def compute_rare_penalty(query_tokens: set, executor) -> int:
    """Penalty -1 per ogni rare-token nella query NON matchato da executor.
    Cap a -3 (non sovrastare boost positivi).

    Pre-condition: init_rare_tokens(catalog) chiamato.
    """
    if _RARE_TOKENS_CACHE is None:
        return 0
    from prefilter import tokenize, stopwords
    exec_tokens = set()
    exec_tokens.update(tokenize(executor.name))
    exec_tokens.update(tokenize(executor.description))
    for tag in (executor.affinity or []):
        exec_tokens.update(tokenize(tag))
    rare_in_query = (query_tokens & _RARE_TOKENS_CACHE) - stopwords()
    unmatched = rare_in_query - exec_tokens
    return -min(len(unmatched), 3)
