"""adaptive_rerank.py — re-ranking adattativo del pool tool fra step
del turno (4-5/5/2026).

Il prefilter `rank_adaptive` viene chiamato UNA volta a inizio turno con
la query utente come unico input. I candidati restano costanti per tutto
il turno: 5-8 executor + universal helpers, totale ≈ 13-17 tool al
PLANNER. Per pipeline multi-dominio (es. mail → file → ocr → mail) il
PLANNER potrebbe non vedere il tool che gli serve allo step N perché il
prefilter al turno 0 ha pesato verbi diversi.

Questo modulo aggiunge un **hook intra-turno** in due strategie
complementari, applicate post-step:

1. **Keyword re-rank** (storica): estrae keywords dall'observation,
   costruisce una "query estesa", ri-chiama `rank_adaptive`, *aggiunge*
   (non sostituisce) i nuovi tool emergenti al pool dei candidati.

2. **Consumer field match** (Layer 3, 5/5/2026): identifica gli executor
   che CONSUMANO i field prodotti dallo step precedente. Si fonda sulla
   convenzione di naming Metnos già stringente (the design guide §2.10
   "Coerenza I/O fra executor in pipeline"): se step1 produce
   `entries=[{url, title, ...}]`, ogni executor con un arg `urls`/`url`
   nel suo `args.properties` e' un consumer naturale del prossimo step.
   Niente dichiarazioni manifest, niente hardcoded mapping: lo schema
   degli args parla. Generale a domini non ancora pensati.

Coerente con due principi:

1. Indipendente dal ranker: vale per token-based oggi e per embedding
   domani — il modulo non assume implementazione del ranker sottostante.

2. Add-only, mai remove: i tool già scelti per il turno restano
   disponibili. Il re-rank può solo aggiungere candidati. Così le
   pipeline multi-step non «perdono» tool a metà strada.

Costo a regime (300 executor, misurato 4/5/2026): ~5 ms per re-rank
intra-turno; ~25 ms per turno tipico (5 step intermedi). Sotto lo 0.5%
della latenza totale del turno (~5-15 s).
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:
    from loader import Catalog, Executor


# ── Keyword extraction ────────────────────────────────────────────────

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]+")
_MAX_VALUE_LEN = 200          # taglio lunghezza per ogni valore string
_MAX_KEYWORDS_FROM_OBS = 12   # cap su keywords estratte per observation
_STOPWORDS = {
    "the", "and", "for", "from", "with", "this", "that", "these", "those",
    "have", "has", "had", "will", "would", "could", "should", "are", "was",
    "were", "been", "being", "ok", "true", "false", "null", "none",
    "json", "dict", "list", "str", "int", "float", "bool",
    "che", "del", "della", "dei", "delle", "con", "per", "non", "una",
    "uno", "gli", "questo", "questa", "quello", "quella", "essere",
    "avere", "stato", "stata", "molto", "poco", "tanto",
}

_SKIP_FIELDS = frozenset({
    "ts", "ts_start", "ts_end", "duration_ms", "elapsed_ms",
    "_inline_rejected", "_duplicate", "_cyclic", "_synth",
    "audit_path", "audit_dir", "audit_file",
    "raw_output", "raw_input", "stderr", "stdout",
    "digest", "version", "manifest_format", "manifest_path",
})


def _walk_strings(obj: Any, *, depth: int = 0) -> Iterable[str]:
    """Yield string values dall'observation, scendendo nelle strutture
    annidate fino a profondita' 4 (limite sano contro liste enormi)."""
    if depth > 4:
        return
    if isinstance(obj, str):
        if obj:
            yield obj[:_MAX_VALUE_LEN]
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if k in _SKIP_FIELDS:
                continue
            if isinstance(k, str) and len(k) >= 3 and k.isidentifier():
                yield k
            yield from _walk_strings(v, depth=depth + 1)
    elif isinstance(obj, (list, tuple)):
        for v in obj[:30]:
            yield from _walk_strings(v, depth=depth + 1)


def extract_keywords(observation: dict, *, max_keywords: int = _MAX_KEYWORDS_FROM_OBS) -> list[str]:
    """Estrae fino a `max_keywords` token significativi dall'observation."""
    if not isinstance(observation, dict):
        return []
    counts: dict[str, int] = {}
    for s in _walk_strings(observation):
        for tok in _TOKEN_RE.findall(s.lower()):
            if len(tok) < 3 or tok in _STOPWORDS or tok.isdigit():
                continue
            counts[tok] = counts.get(tok, 0) + 1
    if not counts:
        return []
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [tok for tok, _ in ranked[:max_keywords]]


def build_extended_query(
    *,
    original_query: str,
    latest_observation: dict | None,
    history_tools: list[str] | None = None,
) -> str:
    """Combina query originale + keywords dall'observation come contesto."""
    base = (original_query or "").strip()
    keywords = extract_keywords(latest_observation or {})
    if not keywords:
        return base
    return base + " " + " ".join(keywords)


# ── Consumer field match (Layer 3, 5/5/2026) ──────────────────────────
#
# Generale: dopo uno step ok, gli executor che CONSUMANO i field prodotti
# sono i next-hop naturali per il PLANNER. Si fonda sulla convenzione di
# naming Metnos già stringente (the design guide §2.10): se step1 produce
# `entries=[{url, title, ...}]`, ogni executor con un arg `urls`/`url` nel
# suo `args.properties` e' un consumer naturale del prossimo step.
#
# Niente dichiarazioni manifest opt-in, niente hardcoded mapping: lo schema
# degli args parla. Vale per executor non ancora scritti, su domini non
# ancora pensati.
#
# Pattern di normalizzazione plurale↔singolare: la convenzione di Metnos
# alterna naturalmente `path/paths`, `url/urls`, `message_id/message_ids`,
# `entry/entries`. Stripping della `s` finale da ogni token unifica i due
# lati. Robusta su ~95% dei casi (eccezioni note: campi ripetuti tipo
# `address` o `class` la cui `s` non e' plurale; effetto: false-match
# sporadico — costo basso, recall alto).

# Argomenti che NON identificano una "data field connection" (sono di
# controllo/struttura, non di dato in pipeline). Esclusi dal match per
# evitare false positive: praticamente ogni executor ha `from_step`,
# `entries`, ecc.
_GENERIC_PIPING_ARGS = frozenset({
    "from_step", "entries", "results",
    # Args di pagination/limit, non di dato:
    "max_bytes", "max_pages", "max_results", "max_rows",
    "max_total", "max_files", "max_depth", "max_keywords",
    "limit", "offset", "encoding", "timeout_s",
    # Dati di output/policy generici:
    "dst", "dst_path", "dst_template", "dst_folder", "src", "src_path",
    "mode", "format", "force",
    # `kind` (12/5/2026): discriminator universale nei record entries
    # (es. `free_slot.kind`, `email_message.kind`) e arg enum in molti
    # executor (get_proposals.kind dedupe|generalize|specialize|all,
    # get_inputs.dialog[].schema.kind text|choice|...). NON identifica
    # connessione dati. Bug live turn 35431172: free_slot.kind matcho'
    # get_proposals.kind → step 5 invocava get_proposals invece di
    # get_inputs. §7.3 generale, non per-tool.
    "kind",
})


def _norm_key(s: str) -> str:
    """Normalizza un campo nome strippando la 's' finale (plurale → singolare).

    Robusto su ~95% dei nomi della convenzione Metnos (path/paths,
    url/urls, message_id/message_ids). Edge case noti (`address`,
    `process` con singolare-in-s) producono match leggermente over-eager,
    accettabile per recall.
    """
    if not s:
        return s
    return s[:-1] if len(s) > 2 and s.endswith("s") else s


def _produced_keys(observation: dict | None) -> set[str]:
    """Estrae l'insieme normalizzato dei field prodotti da un'observation.

    Convenzione Metnos (the design guide §2.6):
      - executor arricchitivi → `entries=[{...}]` (schema omogeneo)
      - executor trasformativi → `results=[{...}]`
      - executor scalari (es. get_now, get_location) → top-level field
    """
    if not isinstance(observation, dict):
        return set()
    keys: set[str] = set()
    for collection_name in ("entries", "results"):
        coll = observation.get(collection_name)
        if isinstance(coll, list) and coll:
            sample = coll[0]
            if isinstance(sample, dict):
                # Sample del primo elemento: lo schema e' omogeneo per
                # convenzione (uno per executor, anche se sotto soglia
                # potrebbero variare valori). Cap a 10 per evitare
                # blow-up su entries con molti campi.
                for k in list(sample.keys())[:10]:
                    if isinstance(k, str) and k not in _GENERIC_PIPING_ARGS:
                        keys.add(k)
    # Top-level scalari: campi come 'now', 'lat', 'lon', 'count', 'url', 'path'.
    # Saltati i campi noti di metadata/audit.
    _META_TOP = {
        "ok", "error", "errors", "ts", "ts_start", "ts_end",
        "duration_ms", "elapsed_ms", "audit_path", "audit_dir",
        "manifest_format", "version", "raw_output", "stderr", "stdout",
        "truncated", "truncated_what", "truncated_intentional",
        "used", "available_total", "cap_field", "cap_value", "ok_count",
    } | _GENERIC_PIPING_ARGS
    for k, v in observation.items():
        if not isinstance(k, str) or k in _META_TOP:
            continue
        if k in ("entries", "results"):
            continue
        # Solo se il valore e' "data-like" (non None/bool/dict di metadata).
        if isinstance(v, (str, int, float)):
            keys.add(k)
    return {_norm_key(k) for k in keys}


def consumer_match(
    *,
    catalog: "Catalog",
    produced_keys: set[str],
    exclude_names: set[str] | None = None,
) -> list["Executor"]:
    """Ritorna gli executor i cui args.properties contengono almeno una
    key che (normalizzata) matcha un produced_key.

    Generale: niente hardcoding. Usa la convenzione di naming già stringente.
    Cap di sicurezza interno (max 8 consumer): un'observation con molti
    field puo' matchare mezzo catalog, ma il valore informativo decade
    (PLANNER si distrae). Ranking per # match overlap, tie-break alfabetico.
    """
    if not produced_keys:
        return []
    exclude = exclude_names or set()
    matches: list[tuple[int, str, "Executor"]] = []
    for executor in catalog:
        if executor.name in exclude:
            continue
        if executor.name == "final_answer":
            continue
        props = (executor.args_schema or {}).get("properties") or {}
        if not isinstance(props, dict):
            continue
        arg_keys = {
            _norm_key(k) for k in props.keys()
            if isinstance(k, str) and k not in _GENERIC_PIPING_ARGS
        }
        overlap = produced_keys & arg_keys
        if overlap:
            matches.append((len(overlap), executor.name, executor))
    # Ranking: piu' overlap = piu' rilevante. Tie-break alfabetico stabile.
    matches.sort(key=lambda t: (-t[0], t[1]))
    return [e for _, _, e in matches[:8]]


def re_rank_for_step(
    *,
    original_query: str,
    catalog: "Catalog",
    current_candidates: list["Executor"],
    latest_observation: dict | None,
    k_min: int = 5,
    k_max: int = 8,
    history_tools: list[str] | None = None,
) -> tuple[list["Executor"], dict]:
    """Re-rank intra-turno: aggiunge nuovi candidati emersi dall'observation.

    Add-only: i `current_candidates` restano sempre nel pool restituito.
    Cap di sicurezza: pool ≤ 2 × k_max (oltre il rumore comincia a contare).

    Due strategie complementari:
      1. Keyword re-rank: estrae token dall'observation, ri-chiama
         `rank_adaptive` su query estesa.
      2. Consumer field match (Layer 3): identifica gli executor che
         consumano i field prodotti (path → read_files, url → read_urls_html).
         Generale, fondato sulla convenzione naming Metnos.
    """
    from prefilter import rank_adaptive
    existing_names = {e.name for e in current_candidates}

    # ── Strategia 1: keyword re-rank (storica) ─────────────────────
    extended_q = build_extended_query(
        original_query=original_query,
        latest_observation=latest_observation,
        history_tools=history_tools or [],
    )
    keyword_additions: list = []
    rank_info_kw = None
    if extended_q != (original_query or "").strip():
        new_candidates, rank_info_kw = rank_adaptive(
            extended_q, catalog, k_min=k_min, k_max=k_max,
        )
        keyword_additions = [
            e for e in new_candidates if e.name not in existing_names
        ]

    # ── Strategia 2: consumer field match (Layer 3, 5/5/2026) ──────
    # Generale e independente dalle keyword. Si basa sulla convenzione
    # I/O di Metnos: produced fields → consumer args. Force-include oltre
    # cap top-K (al pari del Layer 1 nel prefilter).
    produced_keys = _produced_keys(latest_observation)
    consumer_seen = existing_names | {e.name for e in keyword_additions}
    consumer_additions = consumer_match(
        catalog=catalog,
        produced_keys=produced_keys,
        exclude_names=consumer_seen,
    )

    additions = keyword_additions + consumer_additions
    if not additions:
        return current_candidates, {
            "applied": False,
            "reason": "no_new_candidates",
            "rank_info": rank_info_kw,
            "produced_keys": sorted(produced_keys) if produced_keys else [],
        }

    cap = max(k_max * 2, len(current_candidates))
    out = list(current_candidates) + additions
    if len(out) > cap:
        out = out[:cap]

    return out, {
        "applied": True,
        "added": [e.name for e in additions if e.name in {x.name for x in out}],
        "added_by_keyword": [e.name for e in keyword_additions
                              if e.name in {x.name for x in out}],
        "added_by_consumer_match": [e.name for e in consumer_additions
                                     if e.name in {x.name for x in out}],
        "extended_query": extended_q[:200] if extended_q else "",
        "produced_keys": sorted(produced_keys) if produced_keys else [],
        "rank_info": rank_info_kw,
    }
