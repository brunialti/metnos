# SPDX-License-Identifier: AGPL-3.0-only
"""proposals_unified.py — Hub multi-sorgente per /admin/proposals.

Adapter per ogni sorgente di proposte → `UnifiedProposal` (definito in
`telos_proposals_store`). Permette al frontend di mostrare un'unica
tabella con proposte da telos engine, introvertiva (dedupe/generalize/
specialize), synt_proposals, multi_tool_paths, ecc.

Decisioni: scrivono sempre nella sorgente NATIVA (telos_decisions.jsonl,
proposals_state.last_action, etc.), NON in un unified store. Single source
of truth per sorgente; il hub legge.

API pubblica:
    load_unified(*, sources=("telos", "introvertiva"),
                 tier=None, source_filter=None, only_pending=False,
                 max_rows=200) -> list[dict]
    apply_decision_unified(prop_id, source, action, by="admin") -> dict
    source_counts() -> dict[source, dict[state, count]]

Determinismo §7.9. Lazy import per minimizzare costi a inizio import.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import datetime
from typing import Optional


# Sorgenti supportate (oggi 2; aggiungere synt_proposals come MVP+1)
SUPPORTED_SOURCES = ("telos", "introvertiva")


def _intr_prop_id(sig_key: str) -> str:
    """ID stabile per proposta introvertiva: prefisso + sha256 corto."""
    h = hashlib.sha256(sig_key.encode("utf-8")).hexdigest()[:16]
    return f"intr:{h}"


def _intr_score(n_seen: int, last_uses: int) -> float:
    """Score deterministico [0,1] per introvertiva: piu' osservazioni e
    uses recenti = piu' alto. Funzione monotona, no LLM (§7.9).

    Calibratura grezza: n_seen=5 + uses=50 → ~0.55 (interesting).
    Fine-tuning rinviato a quando ci saranno feedback Roberto sull'utilita'.
    """
    return min(1.0, n_seen * 0.05 + last_uses * 0.005)


def _intr_target_from_sigkey(sig_key: str) -> str:
    """Estrae executor_target dal sig_key JSON-encoded list.

    Forme:
    - ["dedupe", "legacy_orphan", "fetch_urls", "write_files"] → "fetch_urls" (a)
    - ["dedupe", "<reason>", "<a>", "<b>"] → "<a>"
    - ["generalize", ["t1", "t2", "t3"]] → primo della seq
    - ["specialize", "<exec>", "<arg>", "<val>"] → "<exec>"
    - ["fastpath_promote", "<expected_name>", ["t1", "t2"]] → "<expected_name>"
    """
    try:
        parsed = json.loads(sig_key)
    except (TypeError, ValueError):
        return ""
    if not isinstance(parsed, list) or not parsed:
        return ""
    head = parsed[0]
    if head == "dedupe" and len(parsed) >= 3:
        return str(parsed[2])
    if head == "generalize" and len(parsed) >= 2:
        seq = parsed[1]
        if isinstance(seq, list) and seq:
            return str(seq[0])
        return ""
    if head == "specialize" and len(parsed) >= 2:
        return str(parsed[1])
    if head == "fastpath_promote" and len(parsed) >= 2:
        return str(parsed[1])
    return ""


def _intr_decision_from_state(state: str, last_action: Optional[str],
                              last_action_at: Optional[str]) -> Optional[dict]:
    """Converte state proposals_state → unified decision dict.

    Mapping:
    - "applied" → decision accept (azione gia' applicata al catalog)
    - "rejected" → decision reject (mai stato visto live)
    - "dormant" → decision stage (in pausa, riemergera' se rilevato)
    - "pending" → decision None
    """
    if state == "pending":
        return None
    mapping = {"applied": "accept", "rejected": "reject", "dormant": "stage",
               "blocked": "reject"}
    action = mapping.get(state)
    if action is None:
        return None
    ts = 0.0
    if last_action_at:
        try:
            ts = datetime.fromisoformat(last_action_at.rstrip("Z")).timestamp()
        except (ValueError, TypeError):
            ts = 0.0
    return {"action": action, "by": last_action or "system", "ts": ts}


def _load_telos(only_pending: bool, max_rows: int,
                 enrich: bool = True,
                 group_clusters: bool = False) -> list[dict]:
    """Carica proposte telos via telos_proposals_store.

    `source` granulare = "telos:<lens>" (es. "telos:scamper") per filtri UI.
    `source_family` = "telos" per raggruppamento aggregato.
    `enrich=False` salta il turn log lookup (utile per tier_counts dove
    serve solo ranking_score, non l'esempio applicabile).

    `group_clusters=True` (mandato 12/6/2026): il cluster e' l'unita' di
    decisione. Carica l'INTERA collezione senza enrich (size cluster VERE,
    non troncate dal cap), ricompone in head via `recompose_clusters`,
    poi arricchisce SOLO gli head (≤ ~25 righe, costo turn-log minimo).
    `ranking_score` degli head = `cluster_score` (EA max + bonus
    convergenza fra lenti distinte), non l'EA della singola istanza.
    """
    import telos_proposals_store as S
    if group_clusters:
        rows = S.load_all(
            min_alignment=0.0,
            max_rows=100000,
            include_decided=not only_pending,
            enrich_rows=False,
        )
        rows = S.recompose_clusters(rows)
        if enrich and rows:
            turns = S._load_turns()
            for r in rows:
                S.enrich(r, turns)
    else:
        rows = S.load_all(
            min_alignment=0.0,
            max_rows=max_rows,
            include_decided=not only_pending,
            enrich_rows=enrich,
        )
    for r in rows:
        lens = r.get("lens", "") or "?"
        r["source"] = f"telos:{lens}"
        r["source_family"] = "telos"
        r["source_id"] = r.get("prop_id", "")
        r["ranking_score"] = r.get("cluster_score",
                                   r.get("expected_alignment", 0.0))
        r["origin_module"] = lens
    return rows


def _describe_proposal(kind: str, sig_key: str) -> str:
    """Spiegazione user-readable di una proposta introvertiva.

    Determinismo §7.9: parsing JSON-tagged sig_key + template i18n.
    Niente LLM. Lingua corrente da `messages.get` (config.DEFAULT_LANG,
    env METNOS_LANG). Fallback su template `MSG_PROP_UNKNOWN` se la shape
    non matcha le 6 forme note (dedupe+legacy_orphan, dedupe generico,
    generalize lista N, generalize lista vuota, fastpath_promote,
    specialize).
    """
    from messages import get as _msg
    try:
        parsed = json.loads(sig_key)
    except (TypeError, ValueError):
        return _msg("MSG_PROP_UNKNOWN", raw=sig_key[:80])
    if not isinstance(parsed, list) or not parsed:
        return _msg("MSG_PROP_UNKNOWN", raw=sig_key[:80])
    head = parsed[0]
    if head == "dedupe" and len(parsed) >= 4:
        reason = parsed[1] or "duplicate"
        a, b = parsed[2], parsed[3]
        if reason == "legacy_orphan":
            return _msg("MSG_PROP_DEDUPE_LEGACY", a=a, b=b)
        return _msg("MSG_PROP_DEDUPE_GENERIC", a=a, b=b, reason=reason)
    if head == "generalize" and len(parsed) >= 2:
        seq = parsed[1]
        if not isinstance(seq, list) or not seq:
            return _msg("MSG_PROP_GENERALIZE_NOISE")
        return _msg("MSG_PROP_GENERALIZE_SEQ",
                    seq=" → ".join(str(s) for s in seq))
    if head == "fastpath_promote" and len(parsed) >= 3:
        chain = parsed[2]
        chain_disp = (" → ".join(str(t) for t in chain)
                      if isinstance(chain, list) and chain else "?")
        return _msg("MSG_PROP_FASTPATH_PROMOTE",
                    name=parsed[1], chain=chain_disp)
    if head == "specialize" and len(parsed) >= 4:
        exec_name, arg, val_json = parsed[1], parsed[2], parsed[3]
        # val_json e' una stringa JSON-encoded del valore originale (es.
        # '"<install_root>"' o '["dates.semantic"]'). Decodifica per leggibilita',
        # fallback al raw se invalida.
        try:
            val = json.loads(val_json)
            val_disp = (val if isinstance(val, str)
                        else json.dumps(val, ensure_ascii=False))
        except (TypeError, ValueError):
            val_disp = str(val_json)
        return _msg("MSG_PROP_SPECIALIZE", exec=exec_name, arg=arg, val=val_disp)
    return _msg("MSG_PROP_UNKNOWN", raw=sig_key[:80])


def _load_introvertiva(only_pending: bool, max_rows: int) -> list[dict]:
    """Carica proposte introvertiva da proposals_state.db, adapter inline."""
    try:
        import proposals_state
    except ImportError:
        return []
    db = proposals_state.DB_PATH
    if not db.exists():
        return []
    where = " WHERE state = 'pending'" if only_pending else ""
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            "SELECT * FROM proposals_state" + where +
            " ORDER BY n_seen DESC, last_seen DESC LIMIT ?",
            (max_rows,),
        )
        raw = cur.fetchall()
    finally:
        conn.close()

    # Adapter inline: introvertiva row → unified dict
    out: list[dict] = []
    for r in raw:
        d = dict(r)
        sig_key = d.get("sig_key", "")
        kind = d.get("kind", "")
        n_seen = int(d.get("n_seen", 0))
        last_uses = int(d.get("last_uses", 0))
        target = _intr_target_from_sigkey(sig_key)
        rec = {
            "prop_id": _intr_prop_id(sig_key),
            "source": f"introvertiva:{kind}",
            "source_family": "introvertiva",
            "source_id": sig_key,
            "origin_module": kind,  # dedupe/generalize/specialize
            "generated_at": _parse_iso(d.get("first_seen") or ""),
            "ranking_score": _intr_score(n_seen, last_uses),
            "confidence": 0.8,
            "executor_target": target,
            "proposed_action": _describe_proposal(kind, sig_key),
            "rationale": (
                f"introvertiva {kind}: osservato {n_seen}× con {last_uses} usi "
                f"cumulativi. Stato: {d.get('state', 'pending')}."
            ),
            "telos_id": None,
            "operator": None,
            "lens": kind,  # alias per compat UI
            "alignment_per_telos": [],
            "paternalism_flag": False,
            "n_observed": n_seen,
            # Validation flags: la maggior parte non si applica a introvertiva.
            "name_status": "n/a",
            "name_status_reason": None,
            "name_grammar_valid": True,
            "hallucinated_tool_mentions": [],
            "is_parametric_extension": (kind == "specialize"),
            # Convergence non rilevante per introvertiva (single source).
            "convergence_count": 1,
            "convergence_lenses": [],
            # Enrichment turn log: non implementato per introvertiva.
            "pipeline_tools_mentioned": [],
            "pipeline_observed": False,
            "example_query": None,
            "current_path": [],
            "new_path_estimated": [],
            "current_latency_ms": None,
            "latency_saved_ms_est": None,
            # Decision
            "decision": _intr_decision_from_state(
                d.get("state", "pending"),
                d.get("last_action"),
                d.get("last_action_at"),
            ),
            "signature": _intr_prop_id(sig_key),
            "signature_relaxed": _intr_prop_id(sig_key),  # 1:1 per introvertiva
            "dedup_cluster": [],
            # Anche `expected_alignment` per backward-compat con template telos
            # che la legge direttamente (riusa la stessa colonna).
            "expected_alignment": _intr_score(n_seen, last_uses),
            "ts": _parse_iso(d.get("first_seen") or ""),
        }
        out.append(rec)
    return out


def _parse_iso(s: str) -> float:
    """ISO date → unix ts, fallback 0."""
    if not s:
        return 0.0
    try:
        return datetime.fromisoformat(s.rstrip("Z")).timestamp()
    except (ValueError, TypeError):
        return 0.0


def load_unified(
    *,
    sources: tuple = SUPPORTED_SOURCES,
    tier: Optional[str] = None,
    source_filter: Optional[str] = None,
    only_pending: bool = False,
    group_clusters: bool = True,
    max_rows_per_source: int = 200,
    max_rows: int = 200,
    enrich: bool = True,
) -> list[dict]:
    """Carica proposte da TUTTE le sorgenti supportate, unificate.

    Filtri:
    - `tier`: 'top' / 'interesting' / 'weak' / None (no filter)
    - `source_filter`: 'telos' / 'introvertiva' / None (tutte)
    - `only_pending`: nasconde proposte gia' accept/reject (stage resta)
    - `group_clusters`: telos → 1 head per cluster relaxed (recompose_clusters),
      ranking_score = cluster_score (EA max + bonus convergenza lenti)

    Ordering: ranking_score desc cross-source. Tier bands sincronizzati con
    `telos_proposals_store.TIER_*`.
    """
    import telos_proposals_store as S
    all_rows: list[dict] = []

    # source_filter granulare: "telos", "introvertiva", "telos:scamper",
    # "introvertiva:dedupe", ecc. None = tutte le sorgenti.
    loaders = {
        "telos": _load_telos,
        "introvertiva": _load_introvertiva,
    }
    # Quando filtro e' "<family>:<module>", carichiamo la family e poi
    # filtriamo per source granulare (post-load).
    if source_filter and ":" in source_filter:
        family = source_filter.split(":", 1)[0]
        targets = [family] if family in loaders else []
    elif source_filter:
        targets = [source_filter] if source_filter in loaders else []
    else:
        targets = list(sources)
    for src in targets:
        loader = loaders.get(src)
        if loader is None:
            continue
        # _load_telos accetta `enrich`+`group_clusters`; _load_introvertiva
        # no (sempre raw, signature 1:1 = gia' cluster-level).
        if src == "telos":
            all_rows.extend(loader(only_pending, max_rows_per_source,
                                   enrich, group_clusters))
        else:
            all_rows.extend(loader(only_pending, max_rows_per_source))

    # Filtro granulare post-load: confronta r["source"] (es. "telos:scamper")
    # con source_filter. Se source_filter e' solo family ("telos"), match
    # ogni r con source_family == family.
    if source_filter:
        if ":" in source_filter:
            all_rows = [r for r in all_rows if r.get("source") == source_filter]
        else:
            all_rows = [r for r in all_rows
                        if r.get("source_family") == source_filter]

    # Tier filter (post-load, semplice da gestire qui)
    if tier == "top":
        all_rows = [r for r in all_rows if r["ranking_score"] >= S.TIER_TOP_MIN]
    elif tier == "interesting":
        all_rows = [r for r in all_rows
                    if S.TIER_INTERESTING_MIN <= r["ranking_score"] < S.TIER_TOP_MIN]
    elif tier == "weak":
        all_rows = [r for r in all_rows if r["ranking_score"] < S.TIER_INTERESTING_MIN]

    # Sort cross-source: azionabili prima, poi ranking_score desc.
    # (Il vecchio dedup post-sort era ROTTO: confrontava r["source"] con
    # "telos" ma il valore e' "telos:<lens>" → nessuna riga veniva mai
    # raggruppata. Ora il clustering avviene in _load_telos via
    # recompose_clusters, fix 12/6/2026.)
    all_rows.sort(key=lambda r: (not r.get("actionable", True),
                                 -r.get("ranking_score", 0.0)))

    return all_rows[:max_rows]


def apply_decision_unified(prop_id: str, source: str, action: str,
                            by: str = "admin") -> dict:
    """Delega al sorgente nativo. Validazione action ∈ {accept,reject,stage}.

    Telos: chiama telos_proposals_store.apply_decision (jsonl append-only).
    Introvertiva: chiama proposals_state.mark_action (sqlite UPDATE).

    Ritorna dict {source, action, ts, by, ...} per uniformita' UI.
    """
    if action not in ("accept", "reject", "stage"):
        raise ValueError(f"action must be accept|reject|stage, got {action!r}")
    # Normalizza source granulare → family: i bottoni della dashboard postano
    # `r.source` che e' "telos:<lens>" / "introvertiva:<kind>". Fix 12/6/2026:
    # senza normalizzazione OGNI decisione dal hub unificato falliva con 400
    # "unknown source" — una delle cause dell'accettazione ~0.
    source = source.split(":", 1)[0]
    if source == "telos":
        import telos_proposals_store as S
        # Lookup per popolare extra (anti-resurrezione C.5).
        extra: dict = {}
        try:
            rows = S.load_all(min_alignment=0.0, max_rows=10000, enrich_rows=True)
            for r in rows:
                if r.get("prop_id") == prop_id:
                    extra["executor_target"] = r.get("executor_target") or ""
                    extra["signature_relaxed"] = r.get("signature_relaxed") or ""
                    extra["lens"] = r.get("lens") or ""
                    break
        except Exception:
            pass
        rec = S.apply_decision(prop_id, action, by=by, **extra)
        rec["source"] = "telos"
        return rec
    if source == "introvertiva":
        import proposals_state
        # Estrazione sig_key da prop_id introvertiva: il prop_id e' un hash
        # opaco, devo lookup il sig_key reale.
        sig_key = _find_introvertiva_sig_key(prop_id)
        if not sig_key:
            raise ValueError(f"prop_id {prop_id} not found in introvertiva store")
        # proposals_state.mark_action accetta {approve, reject, block}.
        # Stage (in pausa, non terminale): nessuna azione nativa; la
        # proposta rimane "pending" e l'ager naturale puo' portarla
        # a dormant. Ritorniamo un record di facciata per uniformita' UI.
        if action == "stage":
            return {
                "source": "introvertiva",
                "prop_id": prop_id,
                "action": "stage",
                "ts": time.time(),
                "by": by,
                "note": "no-op: introvertiva mantiene state 'pending', "
                        "l'ager naturale puo' portarla a dormant",
            }
        action_map = {"accept": "approve", "reject": "reject"}
        native_action = action_map.get(action)
        if native_action is None:
            raise ValueError(f"action {action!r} not supported for introvertiva")
        row = proposals_state.mark_action(sig_key, native_action)
        if row is None:
            raise RuntimeError(f"mark_action returned None for {sig_key}")
        rec = {
            "source": "introvertiva",
            "prop_id": prop_id,
            "action": action,
            "ts": time.time(),
            "by": by,
            "native_state": row.state,
        }
        # Promozione fastpath (mandato 11/6): l'accept scrive il marker
        # synt_pending → telos_synth_consumer → pipeline synt completa.
        if action == "accept" and row.kind == "fastpath_promote":
            try:
                from engine.fastpath_promote import on_proposal_approved
                rec["operative_effect"] = on_proposal_approved(row.sig_key)
            except Exception as ex:
                rec["operative_effect"] = {"kind": "error", "error": str(ex)}
        return rec
    raise ValueError(f"unknown source: {source}")


def _find_introvertiva_sig_key(prop_id: str) -> Optional[str]:
    """Reverse lookup hash → sig_key. Linear scan sulla tabella introvertiva.

    OK per <10000 righe (sigkey index implicito al PRIMARY KEY).
    """
    try:
        import proposals_state
    except ImportError:
        return None
    db = proposals_state.DB_PATH
    if not db.exists():
        return None
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute("SELECT sig_key FROM proposals_state").fetchall()
    finally:
        conn.close()
    for (sig_key,) in rows:
        if _intr_prop_id(sig_key) == prop_id:
            return sig_key
    return None


def granular_source_counts() -> dict:
    """Conteggi per source granulare (telos:lens, introvertiva:kind).

    Lettura raw del file telos (no enrich, no decisions index): conta i
    `lens` direttamente. Per introvertiva: query SQL GROUP BY kind.
    Performance: <50ms anche su 10k+ proposte (vs ~10s con load_all+enrich).
    """
    from collections import Counter
    counts: Counter = Counter()
    try:
        import telos_proposals_store as S
        # Unione candidati dedup ts (stessa vista di load_all, fix 12/6/2026:
        # le proposte post-snapshot backfill devono contare anche qui).
        for rec in S._iter_merged_rows():
            lens = rec.get("lens", "?") or "?"
            counts[f"telos:{lens}"] += 1
    except Exception:
        pass
    try:
        import proposals_state
        db = proposals_state.DB_PATH
        if db.exists():
            conn = sqlite3.connect(str(db))
            try:
                rows = conn.execute(
                    "SELECT kind, COUNT(*) FROM proposals_state GROUP BY kind"
                ).fetchall()
            finally:
                conn.close()
            for kind, c in rows:
                counts[f"introvertiva:{kind}"] = int(c)
    except Exception:
        pass
    return dict(counts.most_common())


def source_counts() -> dict:
    """Conteggi per sorgente, per stato. UI summary card."""
    out: dict = {}
    try:
        import telos_proposals_store as S
        telos_stats = S.stats()
        out["telos"] = {
            "total": telos_stats.get("total", 0),
            "pending": telos_stats.get("pending", 0),
            "accepted": telos_stats.get("accepted", 0),
            "rejected": telos_stats.get("rejected", 0),
            "staged": telos_stats.get("staged", 0),
        }
    except Exception:
        out["telos"] = {"total": 0, "pending": 0, "accepted": 0, "rejected": 0, "staged": 0}

    try:
        import proposals_state
        db = proposals_state.DB_PATH
        if db.exists():
            conn = sqlite3.connect(str(db))
            try:
                rows = conn.execute(
                    "SELECT state, COUNT(*) FROM proposals_state GROUP BY state"
                ).fetchall()
            finally:
                conn.close()
            counts = {"pending": 0, "applied": 0, "dormant": 0, "rejected": 0}
            for state, c in rows:
                counts[state] = int(c)
            out["introvertiva"] = {
                "total": sum(counts.values()),
                "pending": counts.get("pending", 0),
                "accepted": counts.get("applied", 0),
                "rejected": counts.get("rejected", 0),
                "staged": counts.get("dormant", 0),
            }
        else:
            out["introvertiva"] = {"total": 0, "pending": 0, "accepted": 0, "rejected": 0, "staged": 0}
    except Exception:
        out["introvertiva"] = {"total": 0, "pending": 0, "accepted": 0, "rejected": 0, "staged": 0}

    return out
