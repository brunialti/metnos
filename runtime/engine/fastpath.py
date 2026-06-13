"""engine/fastpath.py — Layer 0: cache query→piano AUTO-PRODOTTA.

Bypass completo dell'engine quando la query matcha un fastpath. I fastpath
si producono IN AUTOMATICO a ogni turno completato con successo la cui
query esatta non è ancora in cache 0a: piano pieno (L3 engine/recovery),
hit L1 autopath (il piano di cluster vale anche per la query esatta) e hit
0b cosine (promozione a 0a); MAI da hit 0a, già registrato (classe estesa
12/6/2026 — bug live: le query con skill L1 di famiglia non registravano
mai). Le catene sono executor GIÀ vagliati e testati, nessuna approvazione
esplicita (decisione 11/6/2026; il bottone «approva fast-path» citato in
passato non è mai esistito). Valvole: delete da admin (/admin/praxis) +
aging deterministico (prune) + feedback ✗ utente (turn_feedback →
delete_by_query, LWW: si ri-crea al prossimo turno-successo).

Match in 2 sotto-layer:
  0a — hash lookup deterministic (<5ms, no LLM, no embed)
  0b — semantic cosine via BGE-M3 (<150ms, embed query nuova); serve SOLO
       framework non query-specific: un piano con literal content-bearing
       («name=Tizio») replicherebbe gli arg di UN'ALTRA query simile
       («foto di Caio», sim>soglia) → pertinenza, non sicurezza.

Confine vs autopath (L1): L0 = ripetizione della STESSA query, ammette piani
query-specific (via 0a); L1 = generalizzazione a cluster/intent col consenso
del feedback ✓. Il fast-path vince sempre (primo in cascata).

§7.9 deterministic. LLM mai chiamato in lookup. Embed BGE-M3 in 0b.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .types import Framework
from . import cluster as _cluster
from .executor import is_query_specific

log = logging.getLogger(__name__)

# Step-tool il cui replay fuori dal turno d'origine è semanticamente
# scorretto: undo_last_turn si riferisce al TURNO PRECEDENTE (replay =
# annullare un turno arbitrario), get_inputs apre un dialog interattivo
# (flusso non riproducibile). Set CHIUSO (§2.2): estendere solo per la
# stessa classe di motivi (semantica dipendente dal contesto del turno).
NON_CACHEABLE_TOOLS = frozenset({"undo_last_turn", "get_inputs"})

# Literal temporale ASSOLUTO negli args del piano (ISO date/datetime, es.
# since_iso="2026-06-11", start="2026-06-15T10:00"): il replay in un giorno
# diverso eseguirebbe la finestra/data CONGELATA al momento del record →
# risposta stantia SILENZIOSA (§2.8) — e l'hit 0a rinfresca last_used, quindi
# l'aging non lo poterebbe mai. Piano NON cacheabile (record_success → 0).
# I valori RELATIVI (time_window="today"/"last-24h") restano cacheabili: il
# replay li ri-risolve correttamente; il loro rischio è cross-query (0b/L1)
# ed è coperto da CONTENT_ARG_KEYS (vedi executor.is_query_specific).
_ABS_TEMPORAL_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def _has_absolute_temporal_literal(framework: Framework) -> bool:
    """True se un arg LITERAL (non placeholder ${...}) di uno step contiene
    una data ISO assoluta. Scansione ricorsiva di str/list/dict (§7.9,
    nessun LLM). Il final_message non è scandito: i template usano
    ${stepN.x} e si risolvono a runtime."""
    def _scan(v) -> bool:
        if isinstance(v, str):
            return "${" not in v and bool(_ABS_TEMPORAL_RE.search(v))
        if isinstance(v, list):
            return any(_scan(x) for x in v)
        if isinstance(v, dict):
            return any(_scan(x) for x in v.values())
        return False
    return any(_scan(s.args) for s in framework.steps if s.args)


from timefmt import now_iso_z as _now_iso


@dataclass
class FastpathHit:
    fp_id: int
    canonical_text: str
    framework: Framework
    match_kind: str  # 'hash' | 'cosine'
    similarity: float = 1.0  # 1.0 per hash, cosine per semantic


def _db_path() -> Path:
    import config as _C
    return _C.PATH_USER_DATA / "fastpaths.sqlite"


def _conn() -> sqlite3.Connection:
    """Apre connessione + DDL idempotent ad ogni call."""
    p = _db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(p))
    c.executescript("""
    CREATE TABLE IF NOT EXISTS fastpaths (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        canonical_text TEXT NOT NULL,
        canonical_hash TEXT NOT NULL UNIQUE,
        embedding BLOB,
        framework_json TEXT NOT NULL,
        origin TEXT NOT NULL DEFAULT 'auto',
        intent_verb TEXT NOT NULL DEFAULT '',
        intent_object TEXT NOT NULL DEFAULT '',
        query_specific INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT '',
        n_uses INTEGER NOT NULL DEFAULT 0,
        last_used TEXT
    );
    CREATE INDEX IF NOT EXISTS fp_hash ON fastpaths(canonical_hash);
    CREATE INDEX IF NOT EXISTS fp_uses ON fastpaths(n_uses DESC);
    CREATE TABLE IF NOT EXISTS promotions (
        executor_name TEXT NOT NULL,
        fp_id INTEGER NOT NULL,
        canonical_hash TEXT NOT NULL DEFAULT '',
        tier INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (executor_name, fp_id)
    );
    """)
    c.commit()
    _migrate_schema(c)
    return c


def _migrate_schema(c: sqlite3.Connection) -> None:
    """Migrazione idempotente per DB con lo schema v1 (era-approvazione:
    colonne approved_by/approved_at, mai popolate in produzione — il bottone
    di approvazione non è mai esistito). Due passi, entrambi no-op a regime:
      1. ADD delle colonne v2 mancanti, + backfill created_at da approved_at
         (l'età reale del fastpath sopravvive alla migrazione).
      2. DROP delle vestigia approved_at/approved_by (§7.1 no-legacy).
         CAUSA-RADICE 0-righe in prod (11/6/2026): approved_at era
         TEXT NOT NULL e record_success non la valorizza → IntegrityError
         su OGNI insert; il passo 1 da solo non bastava.
    Non distruttiva: preserva righe e colonne canoniche (ALTER, mai rebuild).
    Richiede SQLite ≥ 3.35 per DROP COLUMN (prod: 3.45)."""
    try:
        cols = {r[1] for r in c.execute("PRAGMA table_info(fastpaths)")}
        changed = False
        for col, decl in (
            ("origin", "TEXT NOT NULL DEFAULT 'auto'"),
            ("intent_verb", "TEXT NOT NULL DEFAULT ''"),
            ("intent_object", "TEXT NOT NULL DEFAULT ''"),
            ("query_specific", "INTEGER NOT NULL DEFAULT 0"),
            ("created_at", "TEXT NOT NULL DEFAULT ''"),
        ):
            if col not in cols:
                c.execute(f"ALTER TABLE fastpaths ADD COLUMN {col} {decl}")
                changed = True
        if "approved_at" in cols:
            c.execute("UPDATE fastpaths SET created_at = approved_at "
                      "WHERE created_at = '' AND approved_at IS NOT NULL")
            c.execute("ALTER TABLE fastpaths DROP COLUMN approved_at")
            changed = True
        if "approved_by" in cols:
            c.execute("ALTER TABLE fastpaths DROP COLUMN approved_by")
            changed = True
        if changed:
            c.commit()
    except sqlite3.Error as ex:
        log.warning("fastpath: migrate schema fallita: %r", ex)


def lookup(query: str) -> Optional[FastpathHit]:
    """Tenta match query → fastpath. Layer 0a (hash) prima, poi 0b (cosine).

    Ritorna FastpathHit se match, None se miss.
    """
    if not query or not query.strip():
        return None
    # Layer 0a: hash deterministic
    h = _cluster.normalize_hash(query)
    try:
        c = _conn()
        row = c.execute(
            "SELECT id, canonical_text, framework_json FROM fastpaths "
            "WHERE canonical_hash = ?", (h,)).fetchone()
        c.close()
        if row:
            try:
                fw = Framework.from_dict(json.loads(row[2]))
                _touch(row[0])
                return FastpathHit(fp_id=row[0], canonical_text=row[1],
                                    framework=fw, match_kind="hash",
                                    similarity=1.0)
            except Exception as ex:
                log.warning("fastpath: parse framework_json failed: %r", ex)
    except Exception as ex:
        log.warning("fastpath: 0a lookup failed: %r", ex)
        return None
    # Layer 0b: semantic cosine. SOLO framework non query-specific (guard di
    # PERTINENZA §2.4/§7.9: i piani con literal content-bearing valgono per
    # quella query esatta → servibili solo via hash 0a).
    eb = _cluster.embed(query)
    if not eb:
        return None  # BGE-M3 unavailable, miss
    try:
        c = _conn()
        rows = c.execute(
            "SELECT id, canonical_text, framework_json, embedding "
            "FROM fastpaths WHERE embedding IS NOT NULL "
            "AND query_specific = 0").fetchall()
        c.close()
    except Exception as ex:
        log.warning("fastpath: 0b query failed: %r", ex)
        return None
    threshold = _cluster.COSINE_HIGH + 0.02  # leggermente più stretto del cluster
    best = None
    best_sim = 0.0
    for fp_id, ctext, fjson, stored_eb in rows:
        if not stored_eb:
            continue
        sim = _cluster.cosine(eb, stored_eb)
        if sim > best_sim:
            best_sim = sim
            best = (fp_id, ctext, fjson)
    if best and best_sim >= threshold:
        try:
            fw = Framework.from_dict(json.loads(best[2]))
            _touch(best[0])
            return FastpathHit(fp_id=best[0], canonical_text=best[1],
                                framework=fw, match_kind="cosine",
                                similarity=best_sim)
        except Exception:
            return None
    return None


def record_success(query: str, framework: Framework, *,
                   intent=None, origin: str = "auto") -> int:
    """Auto-produce un fastpath da un turno completato con SUCCESSO dal piano
    pieno (chiamato da dispatch.run_turn sui percorsi engine/recovery).

    Nessuna approvazione esplicita: gli step sono executor già vagliati e
    testati; le valvole sono delete admin + aging (prune). Idempotente
    sull'hash canonico: la ripetizione RINFRESCA framework e metadati
    (self-healing: il piano cached segue l'ultimo successo).

    Non cacheabile (ritorna 0): framework senza step-executor reali (prosa
    statica → replay darebbe risposta in scatola), con step il cui replay è
    context-dependent (NON_CACHEABLE_TOOLS: undo_last_turn, get_inputs), o
    con literal temporale ASSOLUTO negli args (data ISO congelata → replay
    stantio silenzioso, vedi _has_absolute_temporal_literal).

    `intent` (engine.types.Intent, opzionale): verb/object salvati per la
    morte-su-executor-equivalente (vedi prune). Returns fp_id, 0 su skip/errore.
    """
    if not query or not query.strip() or not framework:
        return 0
    exec_steps = [s.tool for s in framework.steps
                  if s.tool and s.tool != "final_answer"]
    if not exec_steps:
        return 0
    if NON_CACHEABLE_TOOLS.intersection(exec_steps):
        return 0
    if _has_absolute_temporal_literal(framework):
        log.info("fastpath: skip record (literal temporale assoluto nel "
                 "piano: il replay sarebbe stantio)")
        return 0
    canonical = _cluster.normalize_query(query)
    h = _cluster.normalize_hash(query)
    eb = _cluster.embed(query)  # può essere None se BGE-M3 mancante → solo 0a
    fjson = json.dumps(framework.to_dict(), ensure_ascii=False)
    qspec = 1 if is_query_specific(fjson) else 0
    iverb = (getattr(intent, "verb", "") or "").lower().strip()
    iobj = (getattr(intent, "object", "") or "").lower().strip()
    try:
        c = _conn()
        # Refresh: oltre al piano, COALESCE ripara un embedding NULL (BGE-M3
        # giù al primo record) senza mai cancellarne uno valido (eb=None ora).
        c.execute(
            "INSERT INTO fastpaths(canonical_text, canonical_hash, embedding, "
            "framework_json, origin, intent_verb, intent_object, "
            "query_specific, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(canonical_hash) DO UPDATE SET "
            "framework_json = excluded.framework_json, "
            "embedding = COALESCE(excluded.embedding, embedding), "
            "query_specific = excluded.query_specific, "
            "intent_verb = excluded.intent_verb, "
            "intent_object = excluded.intent_object",
            (canonical, h, eb, fjson, origin, iverb, iobj, qspec, _now_iso()))
        c.commit()
        # fp_id dal SELECT, non da lastrowid: sull'upsert-UPDATE (refresh)
        # lastrowid NON è la riga aggiornata → telemetria falsa (§2.8).
        row = c.execute("SELECT id FROM fastpaths WHERE canonical_hash = ?",
                        (h,)).fetchone()
        c.close()
        return int(row[0]) if row else 0
    except Exception as ex:
        log.warning("fastpath.record_success failed: %r", ex)
        return 0


def _touch(fp_id: int) -> None:
    """Aggiorna n_uses + last_used."""
    try:
        c = _conn()
        c.execute(
            "UPDATE fastpaths SET n_uses = n_uses + 1, last_used = ? "
            "WHERE id = ?",
            (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), fp_id))
        c.commit()
        c.close()
    except Exception:
        pass


def list_all(limit: int = 100) -> list[dict]:
    """Lista fastpaths per admin UI (telemetria: usi + ultimo uso)."""
    try:
        c = _conn()
        rows = c.execute(
            "SELECT id, canonical_text, origin, intent_verb, intent_object, "
            "query_specific, created_at, n_uses, last_used FROM fastpaths "
            "ORDER BY n_uses DESC, created_at DESC LIMIT ?",
            (limit,)).fetchall()
        c.close()
        return [
            {"id": r[0], "canonical_text": r[1], "origin": r[2],
             "intent_verb": r[3], "intent_object": r[4],
             "query_specific": bool(r[5]), "created_at": r[6],
             "n_uses": r[7], "last_used": r[8]}
            for r in rows
        ]
    except Exception:
        return []


def delete(fp_id: int) -> bool:
    """Cancella fastpath. Usato da admin UI (valvola: un fastpath sbagliato
    si rimuove a mano; non si ricrea finché il piano pieno non ri-succede)."""
    try:
        c = _conn()
        cur = c.execute("DELETE FROM fastpaths WHERE id = ?", (fp_id,))
        c.commit()
        c.close()
        return cur.rowcount > 0
    except Exception:
        return False


def delete_by_query(query: str) -> int:
    """Valvola feedback ✗ (12/6/2026, chiamata da turn_feedback): cancella
    la riga il cui canonical_hash corrisponde alla query del turno bocciato.

    Necessaria perché un fastpath SBAGLIATO che continua a essere colpito
    rinfresca last_used (l'aging non lo vede) e impedisce alla query di
    ri-raggiungere il piano pieno (L0 vince in cascata) → senza valvola
    sarebbe immortale fino al delete admin. LWW simmetrico con autopath:
    si ri-crea da solo al prossimo turno-successo. Deterministico §7.9.
    Ritorna le righe rimosse (0 = nessun fastpath per quella query)."""
    if not query or not query.strip():
        return 0
    try:
        c = _conn()
        cur = c.execute("DELETE FROM fastpaths WHERE canonical_hash = ?",
                        (_cluster.normalize_hash(query),))
        c.commit()
        c.close()
        if cur.rowcount:
            log.info("fastpath: delete_by_query (feedback ✗) — %d riga/e",
                     cur.rowcount)
        return max(0, cur.rowcount)
    except Exception as ex:
        log.warning("fastpath.delete_by_query failed: %r", ex)
        return 0


# ── Provenienza promozioni (fastpath → executor, mandato 11/6) ─────────────

def record_promotion(executor_name: str, members: list[tuple[int, str]],
                     *, tier: int = 1) -> int:
    """Registra la provenienza di una promozione: l'executor `executor_name`
    (proposto tier 1 o auto-sintetizzato tier 2) nasce dai fastpath `members`
    = [(fp_id, canonical_hash), ...].

    Doppia chiave per robustezza: fp_id (esatto oggi) + canonical_hash
    (sopravvive alla ri-creazione del fastpath dopo un prune: stessa query
    → stesso hash → provenienza ancora valida). Idempotente per
    (executor_name, fp_id); il refresh notturno aggiunge i membri nuovi
    del cluster. La morte si attiva SOLO quando `executor_name` compare nel
    catalog (vedi prune): registrare alla proposta è innocuo.

    Returns: righe scritte/aggiornate (0 su input vuoto o errore).
    """
    if not executor_name or not members:
        return 0
    n = 0
    try:
        c = _conn()
        for fp_id, chash in members:
            if not fp_id:
                continue
            c.execute(
                "INSERT INTO promotions(executor_name, fp_id, canonical_hash,"
                " tier, created_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(executor_name, fp_id) DO UPDATE SET "
                "canonical_hash = excluded.canonical_hash, "
                "tier = excluded.tier",
                (executor_name, int(fp_id), chash or "", int(tier),
                 _now_iso()))
            n += 1
        c.commit()
        c.close()
    except Exception as ex:
        log.warning("fastpath.record_promotion failed: %r", ex)
        return 0
    return n


def list_promotions() -> list[dict]:
    """Provenienza registrata (telemetria/test)."""
    try:
        c = _conn()
        rows = c.execute(
            "SELECT executor_name, fp_id, canonical_hash, tier, created_at "
            "FROM promotions ORDER BY executor_name, fp_id").fetchall()
        c.close()
        return [{"executor_name": r[0], "fp_id": r[1],
                 "canonical_hash": r[2], "tier": r[3], "created_at": r[4]}
                for r in rows]
    except Exception:
        return []


# ── Aging + morte (state_reaper notturno) ──────────────────────────────────

def framework_tools(framework_json: str) -> list[str]:
    """Tool-step reali del framework serializzato (escluso final_answer)."""
    try:
        d = json.loads(framework_json)
    except Exception:
        return []
    out = []
    for s in (d.get("steps") or []):
        if isinstance(s, dict):
            t = s.get("tool") or ""
            if t and t != "final_answer":
                out.append(t)
    return out


def _in_family(name: str, stem: str) -> bool:
    """True se `name` appartiene alla famiglia §2.2 di `stem`
    (verb_object esatto o con qualifier/descriptor: stem oppure stem_*)."""
    return name == stem or name.startswith(stem + "_")


def _family_heir(stem: str, catalog_names: set) -> str:
    """Erede deterministico per la morte C2 name-based: il membro della
    famiglia `stem` presente nel catalog — lo stem esatto se c'è, altrimenti
    il primo qualified in ordine alfabetico (§7.9)."""
    if stem in catalog_names:
        return stem
    fam = sorted(n for n in catalog_names if n.startswith(stem + "_"))
    return fam[0] if fam else ""


def _catalog_objects(catalog, catalog_names: set):
    """Oggetti executor (con .name/.affinity) per il check-prefilter.

    Se il caller non li passa (contratto storico di prune: solo
    catalog_names), best-effort dal loader; in entrambi i casi INTERSECATI
    con catalog_names — il set dichiarato invocabile resta l'unico contratto
    di morte (§2.8: un catalog objects più largo non deve uccidere di più).
    None su errore → check-prefilter saltato stanotte (mai falsi kill).
    """
    if catalog is not None:
        return [e for e in catalog if getattr(e, "name", "") in catalog_names]
    try:
        from loader import load_catalog
        return [e for e in load_catalog() if e.name in catalog_names]
    except Exception as ex:
        log.warning("fastpath: catalog objects per check-prefilter "
                    "non disponibili: %r", ex)
        return None


def _prefilter_supersedes(canonical_text: str, iverb: str, iobj: str,
                          tools: list, catalog_objs) -> str:
    """CHECK-PREFILTER deterministico alla morte C2 (mandato #5-followup).

    Esegue il prefilter di routing (prefilter.rank_with_intent — §7.9
    deterministico, NESSUN LLM) sulla query canonica del fastpath: se ORA
    mette in cima un SINGOLO executor che implementa l'intent verb_object
    (verbo esatto o sibling _VERB_ALSO_CANONICAL, object nei name-parts)
    mentre il piano del fastpath è multi-step e NON usa quella famiglia →
    il fastpath è SUPERATO. Chiude i falsi-negativi del match name-based:
    un equivalente con NOME DIVERSO (es. create_files_spreadsheet per
    intent write_files) non matcha lo stem ma vince il routing.
    SOLO decisione di routing: niente confronto di output (unsafe).

    Ritorna il nome dell'executor che supera, '' se il fastpath resta.
    """
    if not (iverb and iobj) or len(tools) < 2 or not catalog_objs:
        return ""
    try:
        import prefilter as _pf
        ranked = _pf.rank_with_intent(
            canonical_text, catalog_objs,
            {"verb": iverb, "object": iobj}, k=3)
    except Exception as ex:
        log.warning("fastpath: check-prefilter fallito: %r", ex)
        return ""
    if not ranked:
        return ""
    top = getattr(ranked[0], "name", "") or ""
    parts = top.split("_")
    siblings = getattr(_pf, "_VERB_ALSO_CANONICAL", {}).get(iverb, ())
    if not parts or (parts[0] != iverb and parts[0] not in siblings):
        return ""  # top-1 da injection (precursor/admin/get_now): non
        #            implementa l'intent → nessuna morte
    if iobj not in parts:
        return ""
    if any(_in_family(top, t) or _in_family(t, top) for t in tools):
        return ""  # il piano usa già quella famiglia → nessun oscuramento
    return top


def _inherit_uses(executor_name: str, n_uses: int) -> int:
    """EREDITÀ-PUNTI (mandato #5-followup): quando un fastpath muore perché
    SUPERATO (provenienza / name-based / check-prefilter), i suoi usi —
    domanda PROVATA dall'utente — passano all'executor che lo supera.

    Deposito: `executor_stats` (runtime/executor_aging.py, total_calls +
    last_used_at) — ESATTAMENTE la telemetria che l'aging legge
    (apply_executor_ager: demote/archive su inattività) e su cui il
    lifecycle/promozione ragiona. Senza il trasferimento l'erede partirebbe
    da zero e rischierebbe il demote immotivato proprio mentre serve la
    domanda che il fastpath ha dimostrato.

    FLAG implementativo: executor_aging espone solo touch() (incremento
    singolo, nessuna bulk-API) e questo mandato non può modificarlo →
    loop di touch (n piccolo, job notturno). Se diventa caldo: aggiungere
    una bulk-API a executor_aging.

    Ritorna gli usi trasferiti (0 su input vuoto o errore, best-effort).
    """
    if not executor_name or n_uses <= 0:
        return 0
    try:
        import executor_aging as _ea
        for _ in range(int(n_uses)):
            _ea.touch(executor_name)
        return int(n_uses)
    except Exception as ex:
        log.warning("fastpath: eredità usi → %s fallita: %r",
                    executor_name, ex)
        return 0


def prune(*, catalog_names: Optional[set] = None,
          catalog: Optional[list] = None,
          stale_days: int | None = None, grace_days: int | None = None,
          max_rows: int | None = None, now_ts: float | None = None) -> dict:
    """Aging + morte deterministici (§7.9) dello store fastpath. Chiamato
    dal `task_state_reaper` notturno (stesso aggancio di autopath.prune).

    AGING, tre regole in quest'ordine:
      1. mai-riusato: last_used IS NULL e created_at oltre la grazia
         (default 14gg, env METNOS_FASTPATH_GRACE_DAYS) — la query non si è
         mai ripetuta, la cache non ha valore.
      2. stale: last_used oltre la soglia (default 30gg, env
         METNOS_FASTPATH_STALE_DAYS) — la ricorrenza è cessata.
      3. cap LRU: oltre max_rows (default 500, env METNOS_FASTPATH_MAX)
         pota le least-recently-active — bound sia sul disco sia sulla
         latenza del lookup 0b (scan O(N) degli embedding).

    MORTE (solo con `catalog_names`, che DEVE essere il set COMPLETO dei
    tool invocabili: executor caricati + builtin in-process; None o set
    incompleto → il chiamante passi None: meglio nessuna morte che falsi
    kill §2.8):
      C1. tool del piano non più nel catalog (ritirato/rinominato/archiviato)
          → il replay fallirebbe wrong_tool.
      C2-provenienza (mandato 11/6): il fastpath compare nella tabella
          `promotions` (per fp_id O canonical_hash) e l'executor promosso è
          ORA nel catalog ma NON nel piano → morte ESATTA per provenienza
          (chiude il falso-negativo del match name-based: il legame
          fastpath→executor è registrato, non inferito dal nome).
      C2. executor EQUIVALENTE (name-based): esiste
          `{intent_verb}_{intent_object}[_*]` nel catalog ma NESSUN tool del
          piano è di quella famiglia → un executor implementa ora
          DIRETTAMENTE l'intent del fastpath (§2.2: synt nomina
          verb_object[_qualifier] dall'intent); il fastpath, vincendo in
          cascata, lo oscurerebbe per sempre → muore, il prossimo turno
          ripianifica via L3 col nuovo executor.
      C2-prefilter (mandato #5-followup): per i fastpath MULTI-step con
          intent che il name-based NON uccide, il prefilter di routing
          (deterministico, no LLM) sulla query canonica dice se ORA un
          singolo executor implementa l'intent (anche con NOME DIVERSO,
          es. sibling verb) → superato → muore. Vedi
          _prefilter_supersedes. `catalog` (oggetti executor, opzionale):
          se assente, best-effort dal loader ∩ catalog_names
          (_catalog_objects); non disponibile → check saltato, mai falsi
          kill.

    EREDITÀ-PUNTI: ogni morte per superamento (promoted / superseded /
    superseded_prefilter) trasferisce gli n_uses del fastpath all'executor
    erede via _inherit_uses (deposito: executor_stats di executor_aging) —
    la domanda provata non va persa per aging/promozione dell'erede.

    Economia: un fastpath potato per errore SI RICREA DA SOLO alla prossima
    ripetizione riuscita (auto-produzione) → la potatura costa zero e i
    default possono essere aggressivi. Simmetria soglie: executor aging
    30/14 (executor_aging.py). Idempotente. Ritorna report conteggi.
    """
    import os
    if stale_days is None:
        stale_days = int(os.environ.get("METNOS_FASTPATH_STALE_DAYS", "30"))
    if grace_days is None:
        grace_days = int(os.environ.get("METNOS_FASTPATH_GRACE_DAYS", "14"))
    if max_rows is None:
        max_rows = int(os.environ.get("METNOS_FASTPATH_MAX", "500"))
    now = now_ts if now_ts is not None else time.time()

    def _cutoff(days: int) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ",
                             time.gmtime(now - days * 86400))

    report = {"never_reused_removed": 0, "stale_removed": 0,
              "cap_removed": 0, "dead_missing_tool": 0,
              "dead_promoted": 0, "dead_superseded": 0,
              "dead_superseded_prefilter": 0, "inherited_uses": 0,
              "kept": 0}
    try:
        c = _conn()
        report["never_reused_removed"] = c.execute(
            "DELETE FROM fastpaths WHERE last_used IS NULL "
            "AND created_at < ?", (_cutoff(grace_days),)).rowcount
        report["stale_removed"] = c.execute(
            "DELETE FROM fastpaths WHERE last_used IS NOT NULL "
            "AND last_used < ?", (_cutoff(stale_days),)).rowcount
        report["cap_removed"] = c.execute(
            "DELETE FROM fastpaths WHERE id NOT IN ("
            "SELECT id FROM fastpaths "
            "ORDER BY COALESCE(last_used, created_at) DESC LIMIT ?)",
            (int(max_rows),)).rowcount
        # Morte C1/C2 (solo con un catalog completo)
        if catalog_names:
            # Provenienza promozioni: fp_id/hash → executor promosso.
            promo_by_id: dict[int, str] = {}
            promo_by_hash: dict[str, str] = {}
            for ename, pfp_id, phash in c.execute(
                    "SELECT executor_name, fp_id, canonical_hash "
                    "FROM promotions").fetchall():
                promo_by_id[pfp_id] = ename
                if phash:
                    promo_by_hash[phash] = ename
            # Catalog objects per il check-prefilter: build LAZY (una volta,
            # solo se almeno un fastpath arriva a quel check — il fallback
            # dal loader non è gratis).
            _objs_cache: dict = {}

            def _objs():
                if "v" not in _objs_cache:
                    _objs_cache["v"] = _catalog_objects(catalog,
                                                        catalog_names)
                return _objs_cache["v"]

            # dead = (fp_id, why, heir, n_uses): heir/n_uses alimentano
            # l'EREDITÀ-PUNTI (heir='' = nessun erede, es. missing_tool).
            dead: list[tuple[int, str, str, int]] = []
            rows = c.execute(
                "SELECT id, canonical_text, canonical_hash, framework_json, "
                "intent_verb, intent_object, n_uses FROM fastpaths").fetchall()
            for fp_id, ctext, chash, fjson, iverb, iobj, n_uses in rows:
                tools = framework_tools(fjson)
                missing = [t for t in tools if t not in catalog_names]
                if missing:
                    dead.append((fp_id, "missing_tool", "", 0))
                    log.info("fastpath: morte fp_id=%d (tool mancante %s)",
                             fp_id, missing[0])
                    continue
                # C2-provenienza: esatta, vince sul match name-based.
                promoted = promo_by_id.get(fp_id) or promo_by_hash.get(chash)
                if (promoted and promoted in catalog_names
                        and promoted not in tools):
                    dead.append((fp_id, "promoted", promoted, n_uses))
                    log.info("fastpath: morte fp_id=%d (promosso a "
                             "executor %s, provenienza)", fp_id, promoted)
                    continue
                if iverb and iobj:
                    stem = f"{iverb}_{iobj}"
                    if not any(_in_family(t, stem) for t in tools):
                        if any(_in_family(n, stem) for n in catalog_names):
                            heir = _family_heir(stem, catalog_names)
                            dead.append((fp_id, "superseded", heir, n_uses))
                            log.info("fastpath: morte fp_id=%d (executor "
                                     "equivalente famiglia %s_*)",
                                     fp_id, stem)
                            continue
                        # C2-prefilter: equivalente con NOME DIVERSO che il
                        # name-based non coglie — decide il routing.
                        top = _prefilter_supersedes(ctext, iverb, iobj,
                                                    tools, _objs())
                        if top:
                            dead.append((fp_id, "superseded_prefilter",
                                         top, n_uses))
                            log.info("fastpath: morte fp_id=%d (check-"
                                     "prefilter: %s in cima al routing "
                                     "per '%s')", fp_id, top, ctext)
            for fp_id, why, heir, n_uses in dead:
                c.execute("DELETE FROM fastpaths WHERE id = ?", (fp_id,))
                key = {"missing_tool": "dead_missing_tool",
                       "promoted": "dead_promoted",
                       "superseded_prefilter":
                           "dead_superseded_prefilter"}.get(
                           why, "dead_superseded")
                report[key] += 1
                if heir:
                    inherited = _inherit_uses(heir, n_uses)
                    if inherited:
                        report["inherited_uses"] += inherited
                        log.info("fastpath: eredità %d usi fp_id=%d → %s",
                                 inherited, fp_id, heir)
        c.commit()
        report["kept"] = c.execute(
            "SELECT COUNT(*) FROM fastpaths").fetchone()[0]
        try:
            c.execute("VACUUM")
        except sqlite3.Error:
            pass
        c.close()
    except Exception as ex:
        log.warning("fastpath.prune failed: %r", ex)
        report["error"] = repr(ex)
    return report
