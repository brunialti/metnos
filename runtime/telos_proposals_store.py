# SPDX-License-Identifier: AGPL-3.0-only
"""telos_proposals_store.py — Store proposte telos engine + decisioni admin.

Read-only sui proposals JSONL (cui scrivono lenti + AlignmentEngine),
append-only sulle decisioni admin in `telos_decisions.jsonl`.

Definisce anche `UnifiedProposal` (vedi sotto): struttura unica per
proposte di QUALSIASI sorgente (telos, introvertiva, synt, multi_tool).
Metadati di provenance + ranking separati dal payload comune.

Determinismo §7.9: nessun LLM, nessuna logica fuzzy. Filtri sono predicati
puri. ID proposta = `ts` (timestamp float, granularita' microsecondi:
collisione virtualmente impossibile per le sole proposte introspettive
notturne; cluster batch backfill via scrittura sequenziale).

API pubblica:
    load_all(*, min_alignment=0.0, lens=None, telos_id=None,
             max_rows=500) -> list[dict]
    recompose_clusters(rows) -> list[dict]   # 1 head per cluster relaxed
    cluster_score(ea_max, n_lenses) -> float
    apply_decision(prop_id, action, by="admin") -> dict
    decisions_index() -> dict[str, dict]
    proposals_count() -> int

Sorgenti file (path canonical, dataclass-free per leggerezza):
- INPUT: UNIONE dei candidati (dedup per ts, priorita' al primo):
  `telos_proposals.rescored.recomposed.jsonl` (EA ricomposta v1.3) >
  `.rescored.jsonl` (pre-v1.3) > `telos_proposals.jsonl` (EA inline di
  generazione). L'unione garantisce che le proposte generate DOPO uno
  snapshot di backfill restino visibili (fix 12/6/2026).
- OUTPUT: `~/.local/share/metnos/telos_decisions.jsonl`
  Schema: {"prop_id": "<ts>", "action": "accept|reject|stage", "ts": float, "by": str}
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Optional

import config as _C  # §7.11


# ============================================================================
# UnifiedProposal — struttura comune cross-sorgente (telos, introvertiva, ...)
# ============================================================================
#
# Razionale (utente, 22/5/2026): "l'oggetto proposal deve essere unico.
# Contiene metadati (origine, valori numerici di ranking ecc) ma il rimanente
# deve essere uguale". Separiamo provenance (source/source_id/generator) +
# ranking (score, confidence) dal payload comune (target, action, rationale,
# pipeline, validation flags, enrichment turn log).
#
# `signature` cross-sorgente abilita dedup: due proposte da lenti diverse che
# coincidono su (target, pipeline_tools_normalized) hanno la stessa signature
# e vengono raggruppate in cluster (convergence_count > 1).

# Tier band per il filtro UI: definito qui per single-source con telos_proposals_store.stats().
TIER_TOP_MIN = 0.45
TIER_INTERESTING_MIN = 0.30

_DATA_DIR = _C.PATH_USER_DATA
_PROPOSALS_CANDIDATES = (
    _DATA_DIR / "telos_proposals.rescored.recomposed.jsonl",
    _DATA_DIR / "telos_proposals.rescored.jsonl",
    _DATA_DIR / "telos_proposals.jsonl",
)
DECISIONS_PATH = _DATA_DIR / "telos_decisions.jsonl"

_VALID_ACTIONS = frozenset({"accept", "reject", "stage"})




def _iter_merged_rows():
    """Itera i record JSON da TUTTI i file candidati, dedup per `ts`.

    Priorita' = ordine `_PROPOSALS_CANDIDATES` (recomposed > rescored >
    raw): il primo file che contiene un ts vince (EA ricomposta v1.3 ha
    precedenza sull'EA inline di generazione).

    Fix 12/6/2026 (loop proposta→accettazione): prima veniva letto SOLO il
    primo file esistente. Le proposte generate DOPO lo snapshot di backfill
    (536 righe del 23-25/5, EA inline) erano INVISIBILI a dashboard e
    decisioni — il loop non poteva chiudersi su di esse.
    """
    seen: set = set()
    for cand in _PROPOSALS_CANDIDATES:
        if not cand.is_file():
            continue
        with cand.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = rec.get("ts")
                if not isinstance(ts, (int, float)) or ts in seen:
                    continue
                seen.add(ts)
                yield rec


def proposals_count() -> int:
    """Conteggio totale proposte (unione candidati, dedup ts)."""
    return sum(1 for _ in _iter_merged_rows())


def _format_prop_id(ts: float) -> str:
    """ts float → stringa stabile per URL/decision key. 6 decimali = us."""
    return f"{ts:.6f}"


def load_all(
    *,
    min_alignment: float = 0.0,
    lens: Optional[str] = None,
    telos_id: Optional[str] = None,
    max_rows: int = 500,
    include_decided: bool = True,
    enrich_rows: bool = False,
) -> list[dict]:
    """Carica proposte con filtri, ordina per expected_alignment desc.

    Ogni record include:
    - `prop_id` (string da `ts`, usato come PK per decisioni)
    - `decision` (dict | None) se decisa, da `telos_decisions.jsonl`
    - tutti gli altri campi dal JSONL sorgente

    `include_decided=False` filtra le proposte gia' accept/reject (mostra
    solo pending + stage).
    """
    decisions = decisions_index()
    out: list[dict] = []
    for rec in _iter_merged_rows():
        ts = rec.get("ts")
        ea = rec.get("expected_alignment", 0.0)
        try:
            ea = float(ea)
        except (TypeError, ValueError):
            ea = 0.0
        if ea < min_alignment:
            continue
        if lens and rec.get("lens") != lens:
            continue
        if telos_id and rec.get("telos_id") != telos_id:
            continue
        prop_id = _format_prop_id(float(ts))
        dec = decisions.get(prop_id)
        if not include_decided and dec and dec.get("action") in ("accept", "reject"):
            continue
        rec["prop_id"] = prop_id
        rec["decision"] = dec
        rec["expected_alignment"] = ea
        out.append(rec)
    out.sort(key=lambda r: -r.get("expected_alignment", 0.0))
    out = out[:max_rows]
    if enrich_rows and out:
        turns = _load_turns()
        for rec in out:
            enrich(rec, turns)
        annotate_clusters(out)
    return out


def annotate_clusters(rows: list[dict]) -> None:
    """Signature + convergence cluster sul set dato (C.2, 22/5/2026).

    Due livelli di signature:
      - `signature` (strict): hash(target | sorted(others) | parametric)
        → proposte identiche su pipeline + intent.
      - `signature_relaxed`: hash(target | parametric) → proposte sullo
        STESSO target/intent base anche se la pipeline parsata differisce
        (es. 7 proposte "deadline-to-calendar" che citano tool diversi
        ma propongono la stessa capability su create_events).
    `convergence_count` usa signature_relaxed (segnale "n lenti
    concordano sull'intent"); `dedup_cluster` usa signature strict
    (collassa duplicate esatte). Entrambi disponibili al consumer.

    Richiede `pipeline_tools_mentioned` + `is_parametric_extension` gia'
    annotati (annotate_naming o enrich). Mutates in place.
    """
    from collections import defaultdict as _dd
    cluster_strict: dict[str, list[dict]] = _dd(list)
    cluster_relaxed: dict[str, list[dict]] = _dd(list)
    for rec in rows:
        target = rec.get("executor_target", "") or ""
        mentions = rec.get("pipeline_tools_mentioned", []) or []
        others = sorted(t for t in mentions if t != target)
        parametric = 1 if rec.get("is_parametric_extension") else 0
        strict_key = f"{target}|{','.join(others)}|{parametric}"
        relaxed_key = f"{target}|{parametric}"
        sig_strict = hashlib.sha256(strict_key.encode("utf-8")).hexdigest()[:16]
        sig_relaxed = hashlib.sha256(relaxed_key.encode("utf-8")).hexdigest()[:16]
        rec["signature"] = sig_strict
        rec["signature_relaxed"] = sig_relaxed
        cluster_strict[sig_strict].append(rec)
        cluster_relaxed[sig_relaxed].append(rec)
    for sig, members in cluster_strict.items():
        ids = [m["prop_id"] for m in members]
        for m in members:
            m["dedup_cluster"] = ids
    for sig, members in cluster_relaxed.items():
        lenses = sorted({m.get("lens", "?") for m in members})
        for m in members:
            m["convergence_count"] = len(members)
            m["convergence_lenses"] = lenses


# --- Cluster recomposition (mandato 12/6/2026) -------------------------------
#
# Il cluster (signature_relaxed = target|parametric) e' l'UNITA' di analisi e
# decisione: 1017 istanze LLM reali collassano in ~22 intent distinti. Il
# triage per-istanza non scala e seppellisce il segnale (convergenza fra
# lenti INDIPENDENTI) sotto la ripetizione intra-lente. §7.9 deterministico.

# name_status il cui accept produce un'azione operativa (proposal_actions):
# synt_pending / change_pending / pipeline_pending. `existing_redundant` e
# `new_invalid` sono noop per costruzione → non-azionabili.
ACTIONABLE_NAME_STATUS = frozenset(
    {"new_valid", "existing_parametric", "existing_pipeline"})

_CONVERGENCE_BONUS_PER_LENS = 0.05
_CONVERGENCE_BONUS_CAP = 0.20


def cluster_score(ea_max: float, n_lenses: int) -> float:
    """Score cluster-aware: EA del miglior membro + bonus convergenza.

    Ogni LENTE DISTINTA oltre la prima che converge sullo stesso intent e'
    evidenza indipendente: +0.05, cap +0.20 (5+ lenti). Le ripetizioni
    INTRA-lente non contano nulla (anti-rumore: 118 varianti scamper sullo
    stesso target non valgono piu' di una). Clamp a 1.0.
    """
    bonus = min(_CONVERGENCE_BONUS_CAP,
                _CONVERGENCE_BONUS_PER_LENS * max(0, int(n_lenses) - 1))
    return min(1.0, float(ea_max) + bonus)


def recompose_clusters(rows: list[dict]) -> list[dict]:
    """N istanze → 1 head per cluster relaxed. Deterministico, no LLM.

    Head = miglior membro del cluster: prima gli azionabili
    (name_status ∈ ACTIONABLE_NAME_STATUS), poi expected_alignment max,
    tie-break ts min (stabile). Sul head vengono aggiunti i campi cluster:

    - `is_cluster_head`: True
    - `cluster_size`: numero istanze del cluster (intero set passato)
    - `cluster_lenses`: lenti distinte convergenti (sorted)
    - `ea_max`: EA massima fra i membri
    - `cluster_score`: cluster_score(ea_max, n_lenses)
    - `actionable`: il name_status del head produce azione su accept

    Se le righe non sono ancora annotate (annotate_naming/annotate_clusters)
    le annota qui. Ordinamento output: actionable desc, cluster_score desc.
    Muta i dict head in place (le righe sono gia' copie per-request).
    """
    if not rows:
        return []
    if any("signature_relaxed" not in r for r in rows):
        for r in rows:
            if "pipeline_tools_mentioned" not in r:
                annotate_naming(r)
        annotate_clusters(rows)
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[r.get("signature_relaxed") or r.get("prop_id", "?")].append(r)
    heads: list[dict] = []
    for members in groups.values():
        def _head_key(m: dict):
            return (m.get("name_status") not in ACTIONABLE_NAME_STATUS,
                    -float(m.get("expected_alignment") or 0.0),
                    float(m.get("ts") or 0.0))
        head = sorted(members, key=_head_key)[0]
        lenses = sorted({m.get("lens", "?") or "?" for m in members})
        ea_max = max(float(m.get("expected_alignment") or 0.0)
                     for m in members)
        head["is_cluster_head"] = True
        head["cluster_size"] = len(members)
        head["cluster_lenses"] = lenses
        head["ea_max"] = ea_max
        head["cluster_score"] = cluster_score(ea_max, len(lenses))
        head["actionable"] = head.get("name_status") in ACTIONABLE_NAME_STATUS
        heads.append(head)
    heads.sort(key=lambda h: (not h["actionable"], -h["cluster_score"]))
    return heads


def decisions_index() -> dict[str, dict]:
    """Ultima decisione per prop_id (LWW: last-write-wins).

    Append-only file → l'ultimo record per chiave vince. Stage non e' uno
    stato terminale: una proposta stage puo' essere riaccettata/rifiutata.
    """
    if not DECISIONS_PATH.is_file():
        return {}
    idx: dict[str, dict] = {}
    with DECISIONS_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            pid = d.get("prop_id")
            if pid:
                idx[pid] = d  # LWW
    return idx


def apply_decision(
    prop_id: str,
    action: str,
    by: str = "admin",
    *,
    executor_target: Optional[str] = None,
    signature_relaxed: Optional[str] = None,
    lens: Optional[str] = None,
    run_on_accept: bool = True,
) -> dict:
    """Appende una decisione al file. Ritorna il record persistito.

    Campi opzionali (executor_target, signature_relaxed, lens) servono al
    writer (telos_introspect) per anti-resurrezione: una nuova proposta con
    stessa signature_relaxed di una rejected DEVE essere skippata (C.5).
    Persistere nel decision record evita lookup all'origine.

    `run_on_accept=False` salta l'effetto operativo (proposal_actions):
    usato dalle azioni cluster che applicano la decisione a N membri ma
    vogliono UN solo trigger operativo (sul head; il marker e' comunque
    idempotente per signature, questo e' solo per evitare N lookup full).

    Raises ValueError per action invalida. Non valida prop_id contro il
    set delle proposte (le decisioni sono append-only, l'orphan check
    e' compito dell'UI di visualizzazione).
    """
    if action not in _VALID_ACTIONS:
        raise ValueError(f"action must be in {sorted(_VALID_ACTIONS)}, got {action!r}")
    rec: dict = {
        "prop_id": prop_id,
        "action": action,
        "ts": time.time(),
        "by": by,
    }
    if executor_target:
        rec["executor_target"] = executor_target
    if signature_relaxed:
        rec["signature_relaxed"] = signature_relaxed
    if lens:
        rec["lens"] = lens
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    with DECISIONS_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    # C.8: per action=accept invoca on_accept che crea marker operativi
    # (synt_pending / change_pending / pipeline_pending). Idempotente per
    # signature (cluster-level: 28 varianti → 1 marker).
    if action == "accept" and run_on_accept:
        try:
            from proposal_actions import on_accept as _on_accept
            # Recupera la proposta enriched per leggere name_status,
            # signature_relaxed, ecc.
            for r in load_all(min_alignment=0.0, max_rows=10000, enrich_rows=True):
                if r.get("prop_id") == prop_id:
                    rec["operative_effect"] = _on_accept(r, rec)
                    break
        except Exception as ex:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "proposal_actions.on_accept failed: %r", ex,
            )
    return rec


def rejected_signatures_relaxed() -> set[str]:
    """Insieme di `signature_relaxed` di proposte con decision=reject (LWW).

    Usata dal writer (telos_introspect) per anti-resurrezione: skippare
    proposte nuove con stessa signature_relaxed di una rejected. LWW
    significa che se una proposta era reject ed e' stata poi accept/stage,
    NON e' piu' nella set (giusto: l'utente ha cambiato idea).

    Granularita': signature_relaxed = hash(executor_target | is_parametric).
    Una nuova proposta con stesso target+parametric viene bloccata anche
    se la pipeline parsata differisce — coerente col messaggio utente:
    "se cancello una proposta non deve riapparire la sera dopo".
    """
    idx = decisions_index()
    out = set()
    for d in idx.values():
        if d.get("action") != "reject":
            continue
        sig = d.get("signature_relaxed")
        if sig:
            out.add(sig)
    return out


def rejected_targets() -> set[str]:
    """Insieme di executor_target di proposte rejected. Fallback a
    `rejected_signatures_relaxed` per proposte vecchie senza target salvato.

    Usato per anti-resurrezione "stesso target": piu' aggressivo della
    signature_relaxed (collassa anche varianti is_parametric).
    """
    idx = decisions_index()
    out = set()
    for d in idx.values():
        if d.get("action") != "reject":
            continue
        tgt = d.get("executor_target")
        if tgt:
            out.add(tgt)
    return out


def compute_signature_relaxed(executor_target: str,
                              is_parametric_extension: bool = False) -> str:
    """Calcola signature_relaxed senza dover costruire un UnifiedProposal.

    Utile per il writer (`telos_introspect`) che vuole verificare anti-
    resurrezione prima di persistere.
    """
    key = f"{executor_target or ''}|{int(bool(is_parametric_extension))}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


# --- Enrichment (turn log + rationale parsing) --------------------------------
#
# Per ogni proposta arricchiamo con i campi che il design richiede:
# - `example_query` (str|None): una user_query reale dal turn log che ha
#   invocato `executor_target` nella catena tools.
# - `current_path` (list[str]): la sequenza `chosen_tool` del turno trovato.
# - `current_latency_ms` (int|None): wall-time del turno (LLM+exec+intent
#   sommati per step).
# - `new_path_estimated` (list[str]): stima del path se la proposta venisse
#   adottata. Default minimo: `[executor_target]` (singolo step pipelined).
# - `latency_saved_ms_est` (int|None): differenza stimata. Assume mediana
#   per-step ~5500ms (LLM 4-5s + exec 0-1s + intent 1.6s, dalla telemetria).
# - `n_observed` (int|None): conteggio osservazioni dal rationale (regex
#   "N volte" / "N occorrenze").
#
# Determinismo §7.9: parsing regex + lookup file. No LLM, no fuzzy match.

_TURN_LOG_DIR = _DATA_DIR / "turns"
_TURN_LOG_CACHE: dict[Path, list[dict]] = {}
_TURN_LOG_CACHE_MTIME: dict[Path, float] = {}
_TURN_LOG_MAX_FILES = 14  # ~2 settimane di storico, evita scan illimitato

# Mediana per-step osservata in produzione (telemetria 22/5/2026 ~9s LLM
# + 0-1s exec + 1.6s intent_extractor). Usato per stima saving.
_PER_STEP_LATENCY_MS_MEDIAN = 5500

_RATIONALE_NUMBER_RE = re.compile(
    r"(\d+)\s*(?:volte|occorrenze|times|co-?attiv)", re.IGNORECASE
)

# Pattern per detection "modulazione parametrica": la proposta NON aggiunge
# un nuovo executor ma estende args/modes/output di uno esistente.
_PARAMETRIC_RE = re.compile(
    r"\b(?:aggiungere?|aggiunta|estendere?|nuovo\s+parametro|nuova\s+modalit[aà]|"
    r"parametro\s+`|argomento|arg\s+|modo\s+\w+\b|"
    r"add\s+(?:parameter|arg|mode)|extend|new\s+(?:parameter|mode|flag))",
    re.IGNORECASE,
)


# --- Catalog cache (executor names live) -----------------------------------
# Necessario per validation: capire se executor_target esiste GIA' (caso
# create_events: target ricorrente nelle proposte ma gia' implementato).
_CATALOG_CACHE: dict = {"names": frozenset(), "mtime": 0.0}
_CATALOG_TTL_S = 60.0


def _live_catalog_names() -> frozenset[str]:
    """Set di executor names attualmente nel catalog. Cache TTL 60s.

    Import lazy per evitare overhead se enrich non viene chiamato.
    """
    now = time.time()
    if now - _CATALOG_CACHE["mtime"] < _CATALOG_TTL_S and _CATALOG_CACHE["names"]:
        return _CATALOG_CACHE["names"]
    try:
        # Import lazy (loader pesante per via di project_paths/sign verify).
        import loader as _loader
        cat = _loader.load_catalog()
        names = frozenset(e.name for e in cat)
    except Exception:
        names = frozenset()
    _CATALOG_CACHE["names"] = names
    _CATALOG_CACHE["mtime"] = now
    return names


def _validate_executor_naming(name: str) -> tuple[bool, Optional[str]]:
    """Wrapper su `naming_grammar.validate_name`. Ritorna (ok, reason).

    Import lazy (naming_grammar carica vocab.py).
    """
    if not name:
        return False, "empty name"
    try:
        import naming_grammar as _ng
    except ImportError:
        return True, None  # se non disponibile, no-op tollerante
    try:
        live = {e.split("_", 2)[0] + "_" + e.split("_", 2)[1]
                for e in _live_catalog_names() if "_" in e}
    except Exception:
        live = None
    r = _ng.validate_name(name, live_canonicals=live)
    return bool(r.ok), (None if r.ok else r.reason)


def _detect_parametric_extension(
    target: str,
    target_in_catalog: bool,
    action: str,
    pipeline_mentions: list,
) -> bool:
    """Riconosce proposte che sono SOLO modulazione di un executor esistente.

    Criteri (AND):
    - Il target esiste gia' nel catalog.
    - L'azione cita un'estensione di parametri/modes/args.
    - I tool menzionati sono al massimo {target} (no nuovi step pipeline).
    """
    if not target_in_catalog:
        return False
    if not _PARAMETRIC_RE.search(action):
        return False
    other_mentions = {t for t in pipeline_mentions if t != target}
    if other_mentions:
        return False
    return True


def _classify_name_status(
    target: str,
    catalog_names: frozenset,
    grammar_ok: bool,
    grammar_reason: Optional[str],
    is_parametric: bool,
    pipeline_mentions: list,
) -> tuple[str, Optional[str]]:
    """Classifica la relazione semantica fra nome proposto e catalog esistente.

    Ritorna (status, reason). Status:
    - `new_valid`: nome non in catalog, grammar §2.2 ok → proposta legittima.
    - `new_invalid`: nome non in catalog, grammar fail → allucinazione naming.
    - `existing_parametric`: nome in catalog + estensione args/modes chiara →
      modifica utile, ortogonale alla pipeline.
    - `existing_pipeline`: nome in catalog + combinato con altri tool → uso
      legittimo del target come step di una nuova pipeline (NOMINA non
      cambia, ma il composto e' nuovo).
    - `existing_redundant`: nome in catalog, no estensione param chiara, no
      altri tool nella pipeline → la proposta sembra riproporre il target
      cosi' com'e'. Sospetto: o l'LLM ha allucinato che non esiste, oppure
      vuole sovrascriverne la semantica (review umana).
    """
    if not target:
        return "unknown", "no target"
    if target not in catalog_names:
        if grammar_ok:
            return "new_valid", None
        return "new_invalid", grammar_reason
    # target esiste in catalog
    other_mentions = [t for t in pipeline_mentions if t != target]
    if is_parametric:
        return "existing_parametric", "estensione di parametri/modes su executor esistente"
    if other_mentions:
        return "existing_pipeline", f"combina con {len(other_mentions)} altri tool"
    return "existing_redundant", (
        "target gia' implementato, nessuna estensione parametrica e nessuna pipeline nuova: "
        "sembra ricreazione dello stesso executor"
    )




def _classify_hallucinated_mentions(
    mentions: list,
    catalog_names: frozenset,
) -> list[str]:
    """Tool nominati nella proposta ma non esistenti in catalog.

    Solo VERE allucinazioni di tool name: nomi che passano `validate_name`
    (verb/obj/qualifier nel vocab §2.2) ma NON sono nel catalog. Es.
    `create_events_format` parsa come create+events+_format → valido
    sintatticamente, ma l'executor non esiste → halluc.

    Esclude:
    - placeholder semantici (es. `documento_scadenza`, `deadline_date`,
      `file_metadata`): parsano sintatticamente ma verb/obj NON sono nel
      vocab, quindi `validate_name` fallisce → skip.
    - tool esistenti nel catalog.
    - target stesso (gestito da name_status).
    """
    try:
        import naming_grammar as _ng
    except ImportError:
        return []
    hall = []
    for m in mentions:
        if m in catalog_names:
            continue
        r = _ng.validate_name(m)
        if not r.ok:
            continue  # parsing fail O verb/obj fuori vocab → non e' un tool name
        hall.append(m)
    return hall

# Tool names sono `verbo_oggetto` o `verbo_oggetto_qualifier` (§2.2). Pattern
# safe: 2-4 token snake_case di lunghezza ragionevole.
_TOOL_NAME_RE = re.compile(r"\b([a-z][a-z0-9]+(?:_[a-z][a-z0-9]+){1,3})\b")
# Falsi positivi comuni da escludere (parole composte non-tool del prompt IT).
_TOOL_NAME_BLACKLIST = frozenset({
    "dei_documenti", "del_sistema", "lista_di", "lista_paths",
    "lista_della", "una_pipeline", "uno_step", "valore_aggiunto",
    "tutti_i", "non_e", "che_non", "dal_telos", "telos_id",
    "telos_phrase", "alignment_per", "expected_alignment",
    "paternalism_flag", "proposed_action", "executor_target",
    "is_a", "ti_aiuta", "to_calendar", "to_action",
})


def _extract_tool_mentions(*texts: str) -> list[str]:
    """Estrae i nomi-tool menzionati nei testi (dedup, ordine di apparizione).

    NON valida contro il catalog runtime (overhead): la blacklist filtra i
    falsi positivi comuni; eventuali nomi orphan finiscono nella lista ma
    non causano errori (lookup nel turn log restituisce zero match).
    """
    seen = set()
    out = []
    for txt in texts:
        if not txt:
            continue
        for m in _TOOL_NAME_RE.finditer(txt):
            name = m.group(1)
            if name in _TOOL_NAME_BLACKLIST:
                continue
            if name in seen:
                continue
            seen.add(name)
            out.append(name)
    return out


def _load_turns(max_files: int = _TURN_LOG_MAX_FILES) -> list[dict]:
    """Carica tutti i turni dai file *.jsonl piu' recenti. Cache mtime-based.

    Restituisce flat list (ordine = piu' recente prima, file per file).
    """
    if not _TURN_LOG_DIR.is_dir():
        return []
    files = sorted(_TURN_LOG_DIR.glob("*.jsonl"), reverse=True)[:max_files]
    out: list[dict] = []
    for fp in files:
        mtime = fp.stat().st_mtime
        if _TURN_LOG_CACHE.get(fp) and _TURN_LOG_CACHE_MTIME.get(fp) == mtime:
            out.extend(_TURN_LOG_CACHE[fp])
            continue
        records = []
        try:
            with fp.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue
        _TURN_LOG_CACHE[fp] = records
        _TURN_LOG_CACHE_MTIME[fp] = mtime
        out.extend(records)
    return out


def _step_latency_ms(step: dict) -> int:
    """Wall-time stimato di uno step: LLM + exec + intent + prefilter."""
    fields = ("llm_latency_ms", "exec_ms", "intent_ms",
              "prefilter_ms", "vaglio_ms", "rerank_ms")
    total = 0
    for f in fields:
        v = step.get(f)
        if isinstance(v, (int, float)):
            total += int(v)
    return total


_EXAMPLE_QUERY_MIN_SHARED_TOKENS = 1


def _semantic_overlap_query(user_query: str, proposed_action: str) -> int:
    """Token overlap minimo IT+EN fra user_query e proposed_action.

    Heuristic deterministico (§7.9, no LLM): split lowercase su non-alfanumeric,
    filtra stop-word minimali, conta intersezione. Per validare che un
    `example_query` matchi semanticamente la `proposed_action` (caso live
    turn 22/5/2026: proposta su create_events 'pipeline deadline-to-calendar'
    veniva mostrata con esempio 'converti heic in jpg', che NON c'entrava).
    """
    import re as _re
    stop = {"il","la","i","gli","le","un","una","di","da","del","della","dei",
            "delle","a","al","alla","ai","alle","in","con","su","per","tra",
            "fra","e","o","ma","che","mi","ci","ti","si","ho","ha","hai",
            "the","a","an","of","to","in","is","it","for","on","with","and",
            "or","but","this","that","una","sono","ci"}
    def _toks(s: str) -> set:
        return {t for t in _re.split(r"[^\w]+", (s or "").lower())
                if t and t not in stop and len(t) >= 3}
    return len(_toks(user_query) & _toks(proposed_action))


def _find_example_turn(
    target: str,
    related_tools: list[str],
    turns: list[dict],
    proposed_action: str = "",
):
    """Trova il turno piu' rilevante che illustra la proposta.

    Ritorna: (turn|None, pipeline_observed: bool).
    Priorita':
      (1) turno con TUTTI i related_tools (pipeline osservata).
      (2) turno con overlap related_tools + token overlap user_query vs
          proposed_action (semantic match).
      (3) turno con solo il target E almeno 1 token in comune fra
          user_query e proposed_action (evita esempi spuri).
    Se nessun candidato passa il filtro semantico → None.
    """
    if not turns:
        return None, False
    candidates: list[tuple[int, int, dict]] = []  # (overlap, sem, turn)
    for t in turns:
        steps = t.get("steps") or []
        chosen = {s.get("chosen_tool") for s in steps if s.get("chosen_tool")}
        if not chosen:
            continue
        overlap = sum(1 for tool in related_tools if tool in chosen)
        sem = _semantic_overlap_query(
            t.get("user_query", ""), proposed_action) if proposed_action else 1
        if related_tools and overlap == len(related_tools):
            # Pipeline osservata: tutti i tool della proposta presenti.
            # L'evidenza e' la pipeline stessa: niente filtro semantico extra.
            return t, True
        if target and target in chosen:
            # Target in chosen e' evidenza forte: il turn usa effettivamente
            # l'executor proposto. Niente filtro semantico (rischio FN su
            # action generiche tipo "schedula evento" vs query "domani alle 15 dentista").
            # L'anti-spurious resta perche' target match e' restrittivo per costruzione.
            candidates.append((overlap, sem, t))
    if not candidates:
        return None, False
    candidates.sort(key=lambda kv: (-kv[0], -kv[1]))
    return candidates[0][2], False


def _path_from_turn(turn: dict) -> list[str]:
    """Catena di `chosen_tool` di un turno, in ordine."""
    return [s.get("chosen_tool") for s in (turn.get("steps") or [])
            if s.get("chosen_tool")]


def _turn_total_latency_ms(turn: dict) -> int:
    """Somma wall-time per-step dell'intero turno."""
    return sum(_step_latency_ms(s) for s in (turn.get("steps") or []))


def _parse_n_observed(rationale: str) -> Optional[int]:
    if not rationale:
        return None
    m = _RATIONALE_NUMBER_RE.search(rationale)
    return int(m.group(1)) if m else None


def annotate_naming(prop: dict) -> dict:
    """Annotazioni deterministiche in-memory: tool menzionati, validazione
    naming, parametric, name_status, hallucinated. Mutates+returns `prop`.

    Sottoinsieme economico di enrich() — NIENTE matching sul turn log
    (~0.03ms/riga vs ~11ms/riga): usabile sull'INTERA collezione per
    badge/gate della dashboard.
    """
    target = prop.get("executor_target") or ""
    rationale = prop.get("rationale") or ""
    proposed = prop.get("proposed_action") or ""
    # I tool menzionati nella proposta = pipeline da sostituire con `target`.
    mentions = _extract_tool_mentions(proposed, rationale)
    prop["pipeline_tools_mentioned"] = mentions

    # --- Naming + dedup validation (C.2, 22/5/2026) ------------------------
    catalog_names = _live_catalog_names()
    target_in_catalog = bool(target) and target in catalog_names

    grammar_ok, grammar_reason = _validate_executor_naming(target) if target else (True, None)
    prop["name_grammar_valid"] = grammar_ok

    is_parametric = _detect_parametric_extension(
        target, target_in_catalog, proposed, mentions,
    )
    prop["is_parametric_extension"] = is_parametric

    status, reason = _classify_name_status(
        target=target,
        catalog_names=catalog_names,
        grammar_ok=grammar_ok,
        grammar_reason=grammar_reason,
        is_parametric=is_parametric,
        pipeline_mentions=mentions,
    )
    prop["name_status"] = status
    prop["name_status_reason"] = reason

    prop["hallucinated_tool_mentions"] = _classify_hallucinated_mentions(
        mentions, catalog_names,
    )
    return prop


def enrich(prop: dict, turns: Optional[list[dict]] = None) -> dict:
    """Arricchisce una proposta con campi UI. Mutates+returns `prop`.

    Lookup linear: piu' di 100 proposte da arricchire? Pre-carica `turns`
    una volta sola e passa.
    """
    if turns is None:
        turns = _load_turns()
    annotate_naming(prop)
    target = prop.get("executor_target") or ""
    rationale = prop.get("rationale") or ""
    proposed = prop.get("proposed_action") or ""

    prop["n_observed"] = _parse_n_observed(rationale)
    mentions = prop["pipeline_tools_mentioned"]
    # Target deve essere nel set per il matching ma puo' essere implicito.
    related_tools = [t for t in mentions if t != target]

    if target or related_tools:
        turn, pipeline_observed = _find_example_turn(
            target, related_tools, turns, proposed_action=proposed)
    else:
        turn, pipeline_observed = None, False
    prop["pipeline_observed"] = pipeline_observed
    if turn is not None:
        cur_path = _path_from_turn(turn)
        cur_lat = _turn_total_latency_ms(turn)
        # Stima new_path: sostituisco i related_tools (e il target se gia'
        # presente) con UN singolo step `target`. Mantengo gli step non
        # correlati (es. get_now iniziale, final_answer chiusura).
        to_replace = set(related_tools)
        if target:
            to_replace.add(target)
        new_path: list[str] = []
        replaced_block = False
        for step in cur_path:
            if step in to_replace:
                if not replaced_block:
                    new_path.append(target or step)
                    replaced_block = True
                # else: skip (collassiamo il blocco)
            else:
                new_path.append(step)
                replaced_block = False  # reset: blocco interrotto
        saved_steps = max(0, len(cur_path) - len(new_path))
        prop["example_query"] = turn.get("user_query")
        prop["current_path"] = cur_path
        prop["current_latency_ms"] = cur_lat
        prop["new_path_estimated"] = new_path
        prop["latency_saved_ms_est"] = saved_steps * _PER_STEP_LATENCY_MS_MEDIAN
    else:
        prop["example_query"] = None
        prop["current_path"] = []
        prop["current_latency_ms"] = None
        prop["new_path_estimated"] = [target] if target else []
        prop["latency_saved_ms_est"] = None
    return prop


def stats() -> dict:
    """Aggregati per dashboard summary card."""
    total = proposals_count()
    decisions = decisions_index()
    by_action = {"accept": 0, "reject": 0, "stage": 0}
    for d in decisions.values():
        a = d.get("action")
        if a in by_action:
            by_action[a] += 1
    pending = max(0, total - by_action["accept"] - by_action["reject"])
    return {
        "total": total,
        "accepted": by_action["accept"],
        "rejected": by_action["reject"],
        "staged": by_action["stage"],
        "pending": pending,
    }
