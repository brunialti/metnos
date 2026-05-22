"""http_routes_admin — endpoint /admin/* (collezioni read-only + azioni proposte).

Tutte le rotte richiedono ruolo `admin` (la policy e' applicata a livello
di middleware in `http_auth.auth_middleware`).
"""
from __future__ import annotations

import json
import sqlite3
import time
import urllib.parse
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import web

import executor_aging
import proposals_state
import proposals_unified
import telos_proposals_store
import users
from http_auth import (
    ADMIN_COOKIE,
    ADMIN_COOKIE_TTL_S,
    issue_admin_cookie,
)
from http_render import negotiate_collection, render_template, serve_with_etag
from logging_setup import get_logger

log = get_logger(__name__)


def _error(status: int, code: str, message: str) -> web.Response:
    return web.json_response({"error": code, "message": message}, status=status)


# --- /admin (root) -----------------------------------------------------------

def _summary_proposals() -> dict:
    """Conteggi per stato dalla tabella proposals_state."""
    db = proposals_state.DB_PATH
    if not db.exists():
        return {"pending": 0, "dormant": 0, "applied": 0, "rejected": 0, "blocked": 0}
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute(
            "SELECT state, COUNT(*) c FROM proposals_state GROUP BY state"
        ).fetchall()
    finally:
        conn.close()
    out = {"pending": 0, "dormant": 0, "applied": 0, "rejected": 0, "blocked": 0}
    for state, c in rows:
        out[state] = int(c)
    return out


def _summary_executors(catalog) -> dict:
    total = len(catalog)
    deprecated = sum(1 for e in catalog if e.lifecycle == "deprecated")
    counts = executor_aging.counts_by_source_lifecycle()
    handcrafted = sum(v.get("active", 0) for k, v in counts.items()
                      if k.startswith("handcrafted"))
    synth = sum(v.get("active", 0) for k, v in counts.items()
                if k.startswith("synth"))
    return {
        "total": total, "handcrafted": handcrafted,
        "synth": synth, "deprecated": deprecated,
    }


def _turn_log_dir() -> Path:
    return Path.home() / ".local" / "share" / "metnos" / "turns"


def _load_recent_turns(limit: int = 50) -> list[dict]:
    """Ultimi `limit` turni, newest first, dai jsonl di TURN_LOG_DIR."""
    out = []
    d = _turn_log_dir()
    if not d.exists():
        return out
    files = sorted(d.glob("*.jsonl"), reverse=True)
    for fp in files:
        for line in reversed(fp.read_text().splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                t = json.loads(line)
            except json.JSONDecodeError:
                continue
            out.append(t)
            if len(out) >= limit:
                return out
    return out


def _summary_turns() -> dict:
    """Snapshot ultime 24h."""
    cutoff = time.time() - 86400.0
    rows = _load_recent_turns(limit=200)
    rows = [r for r in rows if (r.get("ts_start") or 0) >= cutoff]
    if not rows:
        return {"total": 0, "errors": 0, "median_ms": 0.0}
    durations = [
        max(0, int(((r.get("ts_end") or 0) - (r.get("ts_start") or 0)) * 1000))
        for r in rows
    ]
    durations.sort()
    median = durations[len(durations) // 2] if durations else 0
    errors = sum(1 for r in rows if r.get("final_kind") == "error")
    return {"total": len(rows), "errors": errors, "median_ms": float(median)}


def _summary_runs() -> dict:
    try:
        from scheduler_v2 import client as sched_client
        history = sched_client.history(limit=100)
        tasks = sched_client.list_jobs()
    except Exception as e:
        log.debug("scheduler summary unavailable: %s", e)
        return {"total_today": 0, "failures_today": 0, "tasks": 0}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    total_today = sum(1 for r in history if (r.get("started_at") or "").startswith(today))
    # v2 statuses: success, error, timeout, crashed
    failures = sum(1 for r in history
                    if (r.get("started_at") or "").startswith(today)
                    and r.get("status") not in ("success", None, ""))
    return {"total_today": total_today, "failures_today": failures, "tasks": len(tasks)}


def _summary_users() -> dict:
    """Snapshot utenti per dashboard."""
    try:
        users.init_db()
        all_u = users.list_users()
    except Exception as e:
        log.debug("users summary unavailable: %s", e)
        return {"hosts": 0, "guests": 0, "recent": []}
    hosts = sum(1 for u in all_u if u["role"] == "host")
    guests = sum(1 for u in all_u if u["role"] == "guest")
    # Ultimi 5 con almeno un canale verified (= "pairati")
    recent = []
    for u in sorted(all_u, key=lambda x: x.get("created_at", ""), reverse=True):
        try:
            chans = users.list_channels(u["id"])
        except Exception:
            chans = []
        verified = [c for c in chans if c.get("verified_at")]
        if verified:
            recent.append({
                "name": u["name"],
                "role": u["role"],
                "channels": [c["channel"] for c in verified],
            })
        if len(recent) >= 5:
            break
    return {"hosts": hosts, "guests": guests, "recent": recent}


def _summary_safety() -> dict:
    try:
        from safety.storage import SafetyStore
        s = SafetyStore()
        rows = list(s.all_signatures())
        s.close()
    except Exception as e:
        log.debug("safety summary unavailable: %s", e)
        return {"whitelist": 0, "blacklist": 0, "graylist": 0, "forbidden": 0}
    out = {"whitelist": 0, "blacklist": 0, "graylist": 0, "forbidden": 0}
    for r in rows:
        out[r.kind] = out.get(r.kind, 0) + 1
    return out


async def admin_home(request: web.Request) -> web.Response:
    """GET /admin — dashboard root."""
    started = request.app.get("started_at", time.time())
    catalog = request.app.get("catalog_provider", lambda: [])()
    ctx = {
        "version": "1.1",
        "uptime_s": time.time() - started,
        "turn_summary": _summary_turns(),
        "proposals_summary": _summary_proposals(),
        "telos_proposals_summary": telos_proposals_store.stats(),
        "executors_summary": _summary_executors(catalog),
        "runs_summary": _summary_runs(),
        "safety_summary": _summary_safety(),
        "users_summary": _summary_users(),
    }
    body = render_template("dashboard.html", **ctx).encode("utf-8")
    return web.Response(body=body, content_type="text/html")


# --- /admin/proposals --------------------------------------------------------

def _describe_proposal(kind: str, sig_key: str) -> str:
    """Spiegazione user-readable di una proposta introvertiva.

    Determinismo §7.9: parsing JSON-tagged sig_key + template i18n.
    Niente LLM. Lingua corrente da `messages.get` (config.DEFAULT_LANG,
    env METNOS_LANG). Fallback su template `MSG_PROP_UNKNOWN` se la shape
    non matcha le 5 forme note (dedupe+legacy_orphan, dedupe generico,
    generalize lista N, generalize lista vuota, specialize).
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


def _list_proposals(kind_filter: str | None) -> list[dict]:
    db = proposals_state.DB_PATH
    if not db.exists():
        return []
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        if kind_filter:
            rows = conn.execute(
                "SELECT * FROM proposals_state WHERE kind = ? "
                "ORDER BY last_seen DESC LIMIT 200",
                (kind_filter,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM proposals_state "
                "ORDER BY last_seen DESC LIMIT 200"
            ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["description"] = _describe_proposal(d.get("kind", ""), d.get("sig_key", ""))
        out.append(d)
    return out


async def admin_proposals(request: web.Request) -> web.Response:
    """GET /admin/proposals?kind={dedupe|generalize|specialize}"""
    kind = request.query.get("kind", "").strip()
    rows = _list_proposals(kind or None)
    return negotiate_collection(
        request,
        json_payload={"rows": rows, "kind": kind, "total": len(rows)},
        template="proposals.html",
        template_ctx={"rows": rows, "kind": kind},
    )


async def admin_proposal_action(request: web.Request) -> web.Response:
    """POST /admin/proposals/{sig_key}/{action}"""
    sig_key = urllib.parse.unquote(request.match_info["sig_key"])
    action = request.match_info["action"]
    if action not in ("approve", "reject", "defer"):
        return _error(400, "invalid_action", f"action must be approve|reject|defer, got {action}")
    # `defer` semantica: marca dormant senza riscrivere come reject (per ora alias).
    persisted = "approve" if action == "approve" else ("reject" if action == "reject" else "reject")
    try:
        row = proposals_state.mark_action(sig_key, persisted)
    except Exception as e:
        log.exception("proposal action failed")
        return _error(500, "internal_error", str(e))
    if row is None:
        return _error(404, "not_found", f"proposal {sig_key} not found")

    if "text/html" in request.headers.get("Accept", ""):
        # Risposta htmx: una riga aggiornata da swappare al posto di quella corrente.
        html = (
            f'<tr><td colspan="7" class="muted">'
            f"sig <code>{sig_key}</code>: {action} done · state={row.state}</td></tr>"
        )
        return web.Response(text=html, content_type="text/html")
    return web.json_response({"ok": True, "sig_key": sig_key, "action": action,
                              "state": row.state})


# --- /admin/proposals (unified hub C.6, 22/5/2026) ---------------------------

_UNIFIED_DASH_MAX_ROWS = 80


async def admin_proposals_unified(request: web.Request) -> web.Response:
    """GET /admin/proposals — hub multi-sorgente (telos + introvertiva + ...).

    Query params:
      source: 'telos' / 'introvertiva' / '' (tutti)
      tier: 'top' / 'interesting' (default) / 'weak'
      only_pending: bool
      group_clusters: bool=true (collassa duplicati cluster telos)
    """
    source_filter = request.query.get("source", "").strip() or None
    tier = request.query.get("tier", "interesting").strip().lower()
    if tier not in ("top", "interesting", "weak"):
        tier = "interesting"
    only_pending = request.query.get("only_pending", "0") in ("1", "true", "on")
    group_clusters = request.query.get("group_clusters", "1") in ("1", "true", "on")

    rows = proposals_unified.load_unified(
        source_filter=source_filter,
        tier=tier,
        only_pending=only_pending,
        group_clusters=group_clusters,
        max_rows=_UNIFIED_DASH_MAX_ROWS,
    )
    src_counts = proposals_unified.source_counts()
    granular = proposals_unified.granular_source_counts()

    # Tier counts cross-source (per tab badge). enrich=False per evitare
    # 481x turn log lookup (~10s); serve solo ranking_score.
    all_unfiltered = proposals_unified.load_unified(
        source_filter=source_filter, tier=None, only_pending=False,
        group_clusters=group_clusters, max_rows=10000, enrich=False,
    )
    tier_counts = {"top": 0, "interesting": 0, "weak": 0}
    for r in all_unfiltered:
        sc = r.get("ranking_score", 0.0)
        if sc >= telos_proposals_store.TIER_TOP_MIN:
            tier_counts["top"] += 1
        elif sc >= telos_proposals_store.TIER_INTERESTING_MIN:
            tier_counts["interesting"] += 1
        else:
            tier_counts["weak"] += 1

    return negotiate_collection(
        request,
        json_payload={
            "rows": rows,
            "source_counts": src_counts,
            "granular_sources": granular,
            "tier_counts": tier_counts,
            "filters": {
                "source": source_filter or "",
                "tier": tier,
                "only_pending": only_pending,
                "group_clusters": group_clusters,
            },
        },
        template="proposals_unified.html",
        template_ctx={
            "rows": rows,
            "source_counts": src_counts,
            "granular_sources": granular,
            "tier_counts": tier_counts,
            "source": source_filter or "",
            "tier": tier,
            "only_pending": only_pending,
            "group_clusters": group_clusters,
        },
    )


def _render_unified_row_html(prop_id: str, source: str, action: str,
                              rec: dict) -> str:
    """Riga aggiornata post-decisione unified. htmx swap."""
    badge = {
        "accept": '<span class="chip ok">accepted</span>',
        "reject": '<span class="chip bad">rejected</span>',
        "stage":  '<span class="chip">staged</span>',
    }.get(action, '<span class="chip muted">pending</span>')
    return (
        f'<tr><td colspan="7" class="muted">'
        f'[{source}] {prop_id[:24]}: {badge} '
        f'(by {rec.get("by", "?")} at {time.strftime("%H:%M:%S", time.localtime(rec.get("ts", 0)))})'
        f'</td></tr>'
    )


async def admin_proposal_unified_action(request: web.Request) -> web.Response:
    """POST /admin/proposals/unified/{source}/{prop_id}/{action}"""
    source = request.match_info["source"]
    prop_id = urllib.parse.unquote(request.match_info["prop_id"])
    action = request.match_info["action"]
    if action not in ("accept", "reject", "stage"):
        return _error(400, "invalid_action",
                      f"action must be accept|reject|stage, got {action}")
    try:
        rec = proposals_unified.apply_decision_unified(
            prop_id, source, action, by="admin",
        )
    except ValueError as e:
        return _error(400, "invalid_request", str(e))
    except Exception as e:
        log.exception("unified proposal action failed")
        return _error(500, "internal_error", str(e))

    is_htmx = request.headers.get("HX-Request", "").lower() == "true"
    if is_htmx:
        html = _render_unified_row_html(prop_id, source, action, rec)
        return web.Response(text=html, content_type="text/html")
    return web.json_response({"ok": True, "source": source,
                              "prop_id": prop_id, "action": action,
                              "recorded_at": rec.get("ts")})


# --- /admin/proposals/telos --------------------------------------------------

# Soglia di default per la dashboard triage. Sotto 0.30 = quartile basso, non
# vale l'attenzione umana. Override via query param `min_alignment`.
_TELOS_DASH_DEFAULT_MIN = 0.30
_TELOS_DASH_MAX_ROWS = 60


def _telos_lens_facets(rows: list[dict]) -> dict:
    """Conteggi per lens e telos_id, usati nei filtri della UI."""
    lens_c: dict[str, int] = {}
    telos_c: dict[str, int] = {}
    for r in rows:
        lens_c[r.get("lens", "?")] = lens_c.get(r.get("lens", "?"), 0) + 1
        tid = r.get("telos_id") or "?"
        telos_c[tid] = telos_c.get(tid, 0) + 1
    return {"by_lens": lens_c, "by_telos": telos_c}


async def admin_telos_proposals(request: web.Request) -> web.Response:
    """GET /admin/proposals/telos

    Query params:
      tier: 'top' (≥0.45), 'interesting' (0.30-0.45, default), 'weak' (<0.30)
      min_alignment: float override del tier (advanced)
      lens, telos_id: filtri sorgente
      only_pending: bool, nasconde accept/reject (stage resta)
      group_clusters: bool=true, collassa proposte con stesso signature_relaxed
    """
    tier = request.query.get("tier", "interesting").strip().lower()
    # Tier bands (sincronizzati con telos_proposals_store.TIER_*)
    tier_bands = {
        "top":         (telos_proposals_store.TIER_TOP_MIN, 1.0),
        "interesting": (telos_proposals_store.TIER_INTERESTING_MIN,
                        telos_proposals_store.TIER_TOP_MIN),
        "weak":        (0.0, telos_proposals_store.TIER_INTERESTING_MIN),
    }
    if tier not in tier_bands:
        tier = "interesting"
    band_min, band_max = tier_bands[tier]
    # Override esplicito via min_alignment (advanced)
    try:
        min_align_override = request.query.get("min_alignment", "").strip()
        if min_align_override:
            band_min = float(min_align_override)
            band_max = 1.0
            tier = "custom"
    except ValueError:
        pass

    lens = request.query.get("lens", "").strip() or None
    telos_id = request.query.get("telos_id", "").strip() or None
    only_pending = request.query.get("only_pending", "0") in ("1", "true", "on")
    group_clusters = request.query.get("group_clusters", "1") in ("1", "true", "on")

    # Carica TUTTO sopra band_min e applica band_max + filtri post-load
    rows_all = telos_proposals_store.load_all(
        min_alignment=band_min,
        lens=lens,
        telos_id=telos_id,
        max_rows=_TELOS_DASH_MAX_ROWS * 10,
        include_decided=not only_pending,
        enrich_rows=True,
    )
    rows = [r for r in rows_all if r.get("expected_alignment", 0) < band_max]
    # Group by signature_relaxed: 1 riga leader per cluster, varianti collassate.
    if group_clusters:
        seen_sigs = set()
        grouped = []
        for r in rows:
            sig = r.get("signature_relaxed")
            if not sig or sig in seen_sigs:
                continue
            seen_sigs.add(sig)
            grouped.append(r)
        rows = grouped
    rows = rows[:_TELOS_DASH_MAX_ROWS]

    stats = telos_proposals_store.stats()
    # Tier counts su INTERA collezione (per UI tab badge).
    all_rows = telos_proposals_store.load_all(
        min_alignment=0.0, max_rows=10000, enrich_rows=False,
    )
    facets = _telos_lens_facets(all_rows)
    tier_counts = {"top": 0, "interesting": 0, "weak": 0}
    for r in all_rows:
        ea = r.get("expected_alignment", 0.0)
        if ea >= telos_proposals_store.TIER_TOP_MIN:
            tier_counts["top"] += 1
        elif ea >= telos_proposals_store.TIER_INTERESTING_MIN:
            tier_counts["interesting"] += 1
        else:
            tier_counts["weak"] += 1

    return negotiate_collection(
        request,
        json_payload={
            "rows": rows, "stats": stats, "facets": facets,
            "tier_counts": tier_counts,
            "filters": {
                "tier": tier, "min_alignment": band_min,
                "lens": lens or "", "telos_id": telos_id or "",
                "only_pending": only_pending, "group_clusters": group_clusters,
            },
        },
        template="proposals_telos.html",
        template_ctx={
            "rows": rows, "stats": stats, "facets": facets,
            "tier_counts": tier_counts, "tier": tier,
            "min_alignment": band_min, "lens": lens or "",
            "telos_id": telos_id or "", "only_pending": only_pending,
            "group_clusters": group_clusters,
        },
    )


def _render_telos_row_html(row: dict) -> str:
    """Riga aggiornata post-azione (per swap htmx). Mostra solo summary
    visto che il dettaglio era nella vista precedente."""
    decision = row.get("decision") or {}
    action = decision.get("action", "")
    badge = {
        "accept": '<span class="chip ok">accepted</span>',
        "reject": '<span class="chip bad">rejected</span>',
        "stage":  '<span class="chip">staged</span>',
    }.get(action, '<span class="chip muted">pending</span>')
    return (
        f'<tr><td colspan="6" class="muted">'
        f'prop {row.get("prop_id", "?")[:14]}: {badge} '
        f'(by {decision.get("by", "?")} at {time.strftime("%H:%M:%S", time.localtime(decision.get("ts", 0)))})'
        f'</td></tr>'
    )


async def admin_telos_proposal_cluster_action(request: web.Request) -> web.Response:
    """POST /admin/proposals/telos/{prop_id}/cluster/{action}

    Applica `action` a TUTTI i membri del `dedup_cluster` (cluster relaxed:
    proposte con stesso target+parametric da lenti diverse). C.8: 1 accept
    invece di N decisioni separate per le varianti dello stesso intent.
    Es. 28 proposte create_events deadline-to-calendar → 1 cluster accept.
    """
    prop_id = urllib.parse.unquote(request.match_info["prop_id"])
    action = request.match_info["action"]
    if action not in ("accept", "reject", "stage"):
        return _error(400, "invalid_action",
                      f"action must be accept|reject|stage, got {action}")

    # Lookup completo per recuperare dedup_cluster
    cluster_ids: list[str] = []
    extra_base: dict = {}
    try:
        rows = telos_proposals_store.load_all(
            min_alignment=0.0, max_rows=10000, enrich_rows=True,
        )
        for r in rows:
            if r.get("prop_id") == prop_id:
                cluster_ids = list(r.get("dedup_cluster") or [prop_id])
                extra_base["executor_target"] = r.get("executor_target") or ""
                extra_base["signature_relaxed"] = r.get("signature_relaxed") or ""
                extra_base["lens"] = r.get("lens") or ""
                break
    except Exception as ex:
        log.warning("cluster action lookup failed: %r", ex)
        cluster_ids = [prop_id]

    if not cluster_ids:
        cluster_ids = [prop_id]

    applied: list[dict] = []
    for cid in cluster_ids:
        try:
            rec = telos_proposals_store.apply_decision(
                cid, action, by="admin", **extra_base,
            )
            applied.append(rec)
        except Exception as ex:
            log.warning("cluster apply failed for %s: %r", cid, ex)

    is_htmx = request.headers.get("HX-Request", "").lower() == "true"
    if is_htmx:
        n = len(applied)
        badge = {
            "accept": '<span class="chip ok">accepted</span>',
            "reject": '<span class="chip bad">rejected</span>',
            "stage":  '<span class="chip">staged</span>',
        }.get(action, '<span class="chip muted">?</span>')
        html = (
            f'<tr><td colspan="6" class="muted">'
            f'cluster (n={n}): {badge} applicato a {n} membri'
            f'</td></tr>'
        )
        return web.Response(text=html, content_type="text/html")
    return web.json_response({"ok": True, "action": action,
                              "cluster_size": len(cluster_ids),
                              "applied": len(applied),
                              "prop_ids": cluster_ids})


async def admin_telos_proposal_action(request: web.Request) -> web.Response:
    """POST /admin/proposals/telos/{prop_id}/{action}"""
    prop_id = urllib.parse.unquote(request.match_info["prop_id"])
    action = request.match_info["action"]
    if action not in ("accept", "reject", "stage"):
        return _error(400, "invalid_action",
                      f"action must be accept|reject|stage, got {action}")
    # Lookup della proposta per popolare executor_target + signature_relaxed
    # nel decision record (C.5 anti-resurrezione). Lazy: solo per prop_id
    # ricercato, no full scan se reject (LWW gestisce LWW indipendentemente).
    extra: dict = {}
    try:
        rows = telos_proposals_store.load_all(
            min_alignment=0.0, max_rows=10000, enrich_rows=True,
        )
        for r in rows:
            if r.get("prop_id") == prop_id:
                extra["executor_target"] = r.get("executor_target") or ""
                extra["signature_relaxed"] = r.get("signature_relaxed") or ""
                extra["lens"] = r.get("lens") or ""
                break
    except Exception as ex:
        log.warning("telos proposal action: lookup failed: %r", ex)
    try:
        rec = telos_proposals_store.apply_decision(
            prop_id, action, by="admin", **extra,
        )
    except ValueError as e:
        return _error(400, "invalid_action", str(e))
    except Exception as e:
        log.exception("telos proposal action failed")
        return _error(500, "internal_error", str(e))

    # htmx 1.x manda `HX-Request: true`. `Accept` da htmx e' `*/*` di default
    # quindi il check "text/html in Accept" fallisce. Usiamo HX-Request come
    # discriminante autorevole: presente → swap HTML, assente → JSON.
    is_htmx = request.headers.get("HX-Request", "").lower() == "true"
    if is_htmx:
        html = _render_telos_row_html({"prop_id": prop_id, "decision": rec})
        return web.Response(text=html, content_type="text/html")
    return web.json_response({"ok": True, "prop_id": prop_id, "action": action,
                              "recorded_at": rec.get("ts")})


# --- /admin/executors --------------------------------------------------------

def _executors_rows(catalog) -> list[dict]:
    """Mappa il catalog in righe serializzabili. `source` derivato dal path."""
    from loader import SYNTHESIZED_EXECUTORS_DIR
    out = []
    synth_root = str(SYNTHESIZED_EXECUTORS_DIR)
    for ex in sorted(catalog, key=lambda e: e.name):
        src = "synth" if synth_root in str(ex.manifest_path) else "handcrafted"
        out.append({
            "name": ex.name,
            "version": ex.version,
            "lifecycle": ex.lifecycle,
            "source": src,
            "capabilities": [c.get("name", "") for c in (ex.capabilities or [])],
            "revertible": bool(ex.revertible),
            "deprecated_at": ex.deprecated_at,
            "superseded_by": ex.superseded_by,
        })
    return out


async def admin_executors(request: web.Request) -> web.Response:
    """GET /admin/executors"""
    catalog = request.app.get("catalog_provider", lambda: [])()
    rows = _executors_rows(catalog)
    return negotiate_collection(
        request,
        json_payload={"rows": rows, "total": len(rows)},
        template="executors.html",
        template_ctx={"rows": rows},
    )


async def admin_executors_stats(request: web.Request) -> web.Response:
    """GET /admin/executors/stats — counts + daily events.

    Negotiation Accept: html → grafici uPlot/CSS bar; json → payload raw.
    """
    counts = executor_aging.counts_by_source_lifecycle()
    daily = executor_aging.daily_event_counts(days=30)
    accept = request.headers.get("Accept", "")
    want_html = "text/html" in accept and "application/json" not in accept
    if want_html:
        # Pre-elabora dati per il template (semplifica il JS).
        # Bar chart: lista (source, lifecycle, count, max_for_source).
        bar_rows = []
        for source, by_lc in counts.items():
            tot = sum(by_lc.values()) or 1
            for lc in ("active", "deprecated", "archived"):
                bar_rows.append({
                    "source": source,
                    "lifecycle": lc,
                    "count": by_lc.get(lc, 0),
                    "pct": 100.0 * by_lc.get(lc, 0) / tot,
                })
        html = render_template(
            "executors_stats.html",
            counts=counts,
            bar_rows=bar_rows,
            daily=daily,
            daily_json=json.dumps(daily, default=str),
        )
        return web.Response(text=html, content_type="text/html")
    payload = {
        "counts_by_source_lifecycle": counts,
        "daily_event_counts": daily,
    }
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    return serve_with_etag(request, body, content_type="application/json")


# --- /admin/runs -------------------------------------------------------------

async def admin_runs(request: web.Request) -> web.Response:
    """GET /admin/runs?limit=N"""
    try:
        limit = int(request.query.get("limit", "100"))
    except ValueError:
        limit = 100
    try:
        from scheduler_v2 import client as sched_client
        rows = sched_client.history(limit=limit)
    except Exception as e:
        log.exception("scheduler history failed")
        return _error(500, "internal_error", str(e))
    # Normalizza per il template (compat v1 field names: task/ended_at/status):
    # v2 usa entry_name, finished_at, status in {success,error,timeout,crashed,running}.
    for r in rows:
        r["task"] = r.get("entry_name") or r.get("task") or "-"
        r["ended_at"] = r.get("finished_at") or r.get("ended_at")
        # Mappa "success" → "ok" per il chip verde del template legacy.
        if r.get("status") == "success":
            r["status"] = "ok"
        try:
            dur_ms = r.get("duration_ms")
            if dur_ms is not None:
                r["duration_s"] = round(dur_ms / 1000.0, 2)
            elif r.get("started_at") and r.get("ended_at"):
                a = datetime.fromisoformat(r["started_at"].replace("Z", "+00:00"))
                b = datetime.fromisoformat(r["ended_at"].replace("Z", "+00:00"))
                r["duration_s"] = round((b - a).total_seconds(), 2)
            else:
                r["duration_s"] = None
        except Exception:
            r["duration_s"] = None
    return negotiate_collection(
        request,
        json_payload={"rows": rows, "total": len(rows)},
        template="runs.html",
        template_ctx={"rows": rows},
    )


# --- /admin/builds -----------------------------------------------------------

async def admin_builds(request: web.Request) -> web.Response:
    """GET /admin/builds — lista delle build asincrone (ADR 0093).

    Una riga per (base_path, idx) noto: stato (running/done/aborted/error/
    interrupted), n_done/n_total, eta_s, age dell'ultimo update, unit_active.
    """
    try:
        import build_orchestrator as _bo
        rows_raw = _bo.list_active_builds()
    except Exception as e:
        log.exception("list_active_builds failed")
        return _error(500, "internal_error", str(e))

    rows = []
    for r in rows_raw:
        n_done = int(r.get("n_done") or 0)
        n_total = int(r.get("n_total") or 0)
        pct = (n_done / n_total * 100.0) if n_total > 0 else 0.0
        rows.append({
            "digest": r.get("digest", ""),
            "base_path": r.get("base_path", ""),
            "idx": r.get("idx", ""),
            "state": r.get("state", "?"),
            "n_done": n_done,
            "n_total": n_total,
            "pct": round(pct, 1),
            "eta_s": r.get("eta_s"),
            "errors": r.get("errors") or 0,
            "started_at": r.get("started_at"),
            "last_update": r.get("last_update"),
            "last_update_age_s": r.get("last_update_age_s"),
            "unit_active": bool(r.get("unit_active", False)),
            "unit_name": r.get("unit_name", ""),
            "duration_s": r.get("duration_s"),
            "n_entries": r.get("n_entries"),
            "model": r.get("model", ""),
        })
    return negotiate_collection(
        request,
        json_payload={"rows": rows, "total": len(rows)},
        template="builds.html",
        template_ctx={"rows": rows},
    )


# --- /admin/safety -----------------------------------------------------------

async def admin_safety(request: web.Request) -> web.Response:
    """GET /admin/safety?kind={whitelist|blacklist|graylist|forbidden}"""
    kind = request.query.get("kind", "").strip()
    try:
        from safety.storage import SafetyStore
        s = SafetyStore()
        if kind:
            rows = [asdict(r) for r in s.find_by_kind(kind)]
        else:
            rows = [asdict(r) for r in s.all_signatures()]
        s.close()
    except Exception as e:
        log.exception("safety listing failed")
        return _error(500, "internal_error", str(e))
    return negotiate_collection(
        request,
        json_payload={"rows": rows, "kind": kind, "total": len(rows)},
        template="safety.html",
        template_ctx={"rows": rows, "kind": kind},
    )


# --- /admin/turns ------------------------------------------------------------

async def admin_turns(request: web.Request) -> web.Response:
    """GET /admin/turns?limit=N"""
    try:
        limit = int(request.query.get("limit", "50"))
    except ValueError:
        limit = 50
    raw = _load_recent_turns(limit=limit)
    rows = []
    for t in raw:
        steps = t.get("steps") or []
        ts0 = t.get("ts_start") or 0
        ts1 = t.get("ts_end") or 0
        rows.append({
            "turn_id": t.get("turn_id") or "",
            "ts_start_iso": datetime.fromtimestamp(ts0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if ts0 else "",
            "channel": t.get("channel") or "",
            "actor": t.get("actor") or "",
            "n_steps": len(steps),
            "final_kind": t.get("final_kind") or "",
            "elapsed_s": round(ts1 - ts0, 1) if (ts0 and ts1) else 0,
            "user_query": t.get("user_query") or "",
        })
    return negotiate_collection(
        request,
        json_payload={"rows": rows, "total": len(rows)},
        template="turns.html",
        template_ctx={"rows": rows},
    )


# --- /admin/users (multi-user management, ADR 0083) -------------------------

def _user_to_dict(u: dict) -> dict:
    """Lightweight projection per HTML/JSON listing (escludi notes lunghe)."""
    return {
        "id": u["id"],
        "name": u["name"],
        "display_name": u.get("display_name"),
        "role": u["role"],
        "owner_user_id": u.get("owner_user_id"),
        "autonomy_level": u["autonomy_level"],
        "email": u.get("email"),
        "created_at": u.get("created_at"),
    }


def _channels_for(user_id: str) -> list[dict]:
    try:
        return users.list_channels(user_id)
    except Exception as e:
        log.debug("channels list failed for %s: %s", user_id, e)
        return []


async def admin_users(request: web.Request) -> web.Response:
    """GET /admin/users — tabella utenti.
    POST /admin/users — crea user da form.
    """
    if request.method == "POST":
        return await _admin_users_create(request)
    try:
        users.init_db()
        all_u = users.list_users()
    except Exception as e:
        log.exception("users list failed")
        return _error(500, "internal_error", str(e))
    rows = []
    for u in all_u:
        d = _user_to_dict(u)
        d["channels"] = [
            {"channel": c["channel"],
             "recipient_id": c.get("recipient_id"),
             "verified": bool(c.get("verified_at"))}
            for c in _channels_for(u["id"])
        ]
        rows.append(d)
    return negotiate_collection(
        request,
        json_payload={"rows": rows, "total": len(rows)},
        template="users.html",
        template_ctx={"rows": rows},
    )


async def _admin_users_create(request: web.Request) -> web.Response:
    body = await request.post()
    name = (body.get("name") or "").strip()
    role = (body.get("role") or "guest").strip()
    autonomy = (body.get("autonomy_level") or "restricted").strip()
    display_name = (body.get("display_name") or "").strip() or None
    email = (body.get("email") or "").strip() or None
    owner_user_id = (body.get("owner_user_id") or "").strip() or None
    try:
        users.init_db()
        # Default owner: se role='guest' e nessun owner specificato, usa
        # l'host bootstrappato (single-host policy).
        if role == "guest" and not owner_user_id:
            hosts = users.list_users(role="host")
            if hosts:
                owner_user_id = hosts[0]["id"]
        u = users.create_user(
            name,
            display_name=display_name,
            role=role,
            owner_user_id=owner_user_id,
            autonomy_level=autonomy,
            email=email,
        )
    except ValueError as e:
        return _error(400, "invalid_input", str(e))
    except Exception as e:
        log.exception("user create failed")
        return _error(500, "internal_error", str(e))
    if "text/html" in request.headers.get("Accept", ""):
        raise web.HTTPFound(f"/admin/users/{u['id']}")
    return web.json_response(_user_to_dict(u), status=201)


async def admin_user_detail(request: web.Request) -> web.Response:
    """GET /admin/users/{id} — dettaglio."""
    user_id = request.match_info["id"]
    try:
        users.init_db()
        u = users.get_user(user_id)
    except Exception as e:
        log.exception("user detail failed")
        return _error(500, "internal_error", str(e))
    if not u:
        return _error(404, "not_found", f"user {user_id!r} not found")
    chans = _channels_for(u["id"])
    payload = {
        **_user_to_dict(u),
        "notes": u.get("notes"),
        "channels": [
            {**c, "verified": bool(c.get("verified_at"))} for c in chans
        ],
    }
    if "text/html" in request.headers.get("Accept", ""):
        html = render_template("user_detail.html", user=payload, channels=chans)
        return web.Response(text=html, content_type="text/html")
    return web.json_response(payload)


async def admin_user_delete(request: web.Request) -> web.Response:
    """POST /admin/users/{id}/delete."""
    user_id = request.match_info["id"]
    try:
        ok = users.delete_user(user_id)
    except Exception as e:
        log.exception("user delete failed")
        return _error(500, "internal_error", str(e))
    if not ok:
        return _error(404, "not_found", f"user {user_id!r} not found")
    if "text/html" in request.headers.get("Accept", ""):
        raise web.HTTPFound("/admin/users")
    return web.json_response({"ok": True, "deleted": user_id})


async def admin_user_update(request: web.Request) -> web.Response:
    """POST /admin/users/{id}/update — aggiorna campi mutabili.

    Form fields opzionali: display_name, email, autonomy_level, notes.
    Stringa vuota su un campo = clear (NULL nel DB). Campo assente nel
    body = invariato. Ritorna 303 redirect alla detail page (HTML) o
    JSON dello user aggiornato.
    """
    user_id = request.match_info["id"]
    body = await request.post()

    def _field_or_unset(name: str):
        # web.MultiDict: la chiave esiste solo se presente nel form.
        if name not in body:
            return ...
        v = body.get(name)
        if v is None:
            return None
        s = str(v).strip()
        return s or None  # stringa vuota = clear (NULL)

    kwargs = {
        k: _field_or_unset(k)
        for k in ("display_name", "email", "autonomy_level", "notes")
    }
    # Filtra i campi non presenti (sentinel ...)
    kwargs = {k: v for k, v in kwargs.items() if v is not ...}

    try:
        users.init_db()
        ok = users.update_user(user_id, **kwargs)
    except ValueError as e:
        return _error(400, "invalid_input", str(e))
    except Exception as e:
        log.exception("user update failed")
        return _error(500, "internal_error", str(e))
    if not ok:
        return _error(404, "not_found", f"user {user_id!r} not found")

    if "text/html" in request.headers.get("Accept", ""):
        raise web.HTTPFound(f"/admin/users/{user_id}")
    u = users.get_user(user_id)
    return web.json_response(_user_to_dict(u))


async def admin_user_pair_channel(request: web.Request) -> web.Response:
    """POST /admin/users/{id}/channels/{channel}/pair."""
    user_id = request.match_info["id"]
    channel = request.match_info["channel"]
    if channel not in users.CHANNELS:
        return _error(400, "invalid_channel",
                      f"channel must be one of {users.CHANNELS}")
    try:
        u = users.get_user(user_id)
        if not u:
            return _error(404, "not_found", f"user {user_id!r} not found")
        token = users.issue_pairing_token(user_id, channel, ttl_s=3600)
    except Exception as e:
        log.exception("pair token issue failed")
        return _error(500, "internal_error", str(e))
    pair_url = ""
    if channel == "http":
        # Costruisci pair URL completo. Origin viene da X-Forwarded-Proto
        # (Cloudflare) o request.scheme + Host header.
        xfp = request.headers.get("X-Forwarded-Proto") or request.scheme
        host = request.host
        pair_url = f"{xfp}://{host}/pair/{token}"
        instructions = (
            f"Manda a {u['name']} questo URL (apre da browser/cellulare "
            f"UNA VOLTA): {pair_url}"
        )
    elif channel == "telegram":
        instructions = (
            f"Manda a {u['name']} questo comando da inviare al bot Telegram "
            f"di Metnos: /start {token}"
        )
    else:
        instructions = f"Token monouso emesso per channel '{channel}': {token}"
    if "text/html" in request.headers.get("Accept", ""):
        html = render_template("user_pair.html", user=u, channel=channel,
                                token=token, instructions=instructions,
                                pair_url=pair_url)
        return web.Response(text=html, content_type="text/html")
    return web.json_response({"ok": True, "user_id": user_id,
                              "channel": channel, "token": token,
                              "pair_url": pair_url,
                              "instructions": instructions})


async def admin_user_remove_channel(request: web.Request) -> web.Response:
    """POST /admin/users/{id}/channels/{channel}/remove."""
    user_id = request.match_info["id"]
    channel = request.match_info["channel"]
    try:
        ok = users.remove_channel(user_id, channel)
    except Exception as e:
        log.exception("remove channel failed")
        return _error(500, "internal_error", str(e))
    if not ok:
        return _error(404, "not_found",
                      f"channel {channel!r} not found for user {user_id!r}")
    if "text/html" in request.headers.get("Accept", ""):
        raise web.HTTPFound(f"/admin/users/{user_id}")
    return web.json_response({"ok": True, "user_id": user_id,
                              "channel": channel})


async def admin_user_set_autonomy(request: web.Request) -> web.Response:
    """POST /admin/users/{id}/autonomy."""
    user_id = request.match_info["id"]
    body = await request.post()
    level = (body.get("autonomy_level") or "").strip()
    if level not in users.AUTONOMY_LEVELS:
        return _error(400, "invalid_input",
                      f"autonomy_level must be one of {users.AUTONOMY_LEVELS}")
    try:
        ok = users.set_autonomy(user_id, level)
    except ValueError as e:
        return _error(400, "invalid_input", str(e))
    except Exception as e:
        log.exception("set autonomy failed")
        return _error(500, "internal_error", str(e))
    if not ok:
        return _error(404, "not_found", f"user {user_id!r} not found")
    if "text/html" in request.headers.get("Accept", ""):
        raise web.HTTPFound(f"/admin/users/{user_id}")
    return web.json_response({"ok": True, "user_id": user_id,
                              "autonomy_level": level})


_LOGIN_HTML = """<!doctype html>
<html lang="it"><head><meta charset="utf-8"><title>Metnos admin · login</title>
<style>
body{font:14px system-ui;display:flex;align-items:center;justify-content:center;
     min-height:80vh;margin:0;background:#fafafa}
form{background:#fff;padding:1.5rem;border:1px solid #ddd;border-radius:.5rem;
     min-width:340px}
h1{font-size:1.1em;margin:0 0 1rem 0}
input[type=password]{width:100%;padding:.5rem;font:inherit;font-family:monospace;
     box-sizing:border-box;border:1px solid #ccc;border-radius:.3rem}
button{margin-top:.7rem;padding:.5rem 1rem;font:inherit;cursor:pointer}
.err{color:#c00;margin-top:.5rem;font-size:.9em}
.muted{color:#888;font-size:.85em;margin-top:.7rem}
</style></head><body>
<form method="post" action="/admin/login">
<h1>Metnos admin</h1>
<input type="password" name="key" placeholder="admin key (hex)" autofocus required>
<button type="submit">entra</button>
__ERR__
<div class="muted">la chiave e' in <code>~/.config/metnos/admin.key</code> sul host.</div>
</form></body></html>"""


async def admin_login(request: web.Request) -> web.Response:
    """GET /admin/login — form HTML; POST /admin/login — verifica + cookie."""
    admin_key = request.app.get("admin_key", "")
    if request.method == "GET":
        already = request.cookies.get(ADMIN_COOKIE, "")
        if already:
            from http_auth import verify_admin_cookie
            if admin_key and verify_admin_cookie(already, admin_key):
                raise web.HTTPFound("/admin")
        return web.Response(
            text=_LOGIN_HTML.replace("__ERR__", ""),
            content_type="text/html",
        )
    # POST
    body = await request.post()
    submitted = (body.get("key") or "").strip()
    import hmac as _hmac
    if not (admin_key and submitted and
            _hmac.compare_digest(submitted, admin_key)):
        return web.Response(
            text=_LOGIN_HTML.replace(
                "__ERR__", '<div class="err">chiave non valida</div>'
            ),
            content_type="text/html",
            status=401,
        )
    cookie_val = issue_admin_cookie(admin_key)
    resp = web.HTTPFound("/admin")
    resp.set_cookie(
        ADMIN_COOKIE, cookie_val,
        max_age=ADMIN_COOKIE_TTL_S,
        httponly=True,
        samesite="Strict",
        path="/",
    )
    raise resp


async def admin_logout(request: web.Request) -> web.Response:
    """POST /admin/logout — clear cookie + redirect a /admin/login."""
    resp = web.HTTPFound("/admin/login")
    resp.del_cookie(ADMIN_COOKIE, path="/")
    raise resp


# --- /admin/synth-proposals/<id>/evaluate (ADR 0122) -------------------------

_SYNT_PROPOSALS_DIR = Path.home() / ".local" / "share" / "metnos" / "synt_proposals"


async def admin_synth_proposal_evaluate(request: web.Request) -> web.Response:
    """POST/GET /admin/synth-proposals/{id}/evaluate — auto-evaluator.

    Risolve `id` come stem o `proposal_id` dentro `~/.local/share/metnos/
    synt_proposals/`. Ritorna JSON dell'`EvaluationResult` (o testo se
    Accept: text/html). ADR 0122.
    """
    proposal_id = urllib.parse.unquote(request.match_info["id"])
    target_path: Path | None = None
    if _SYNT_PROPOSALS_DIR.exists():
        for cand in _SYNT_PROPOSALS_DIR.glob("*.json"):
            if "_archived" in cand.parts:
                continue
            try:
                d = json.loads(cand.read_text(encoding="utf-8"))
            except Exception:
                continue
            if d.get("id") == proposal_id or cand.stem == proposal_id:
                target_path = cand
                break
    if target_path is None:
        return _error(404, "not_found", f"synth proposal {proposal_id} not found")
    try:
        from proposal_evaluator import evaluate_proposal
        result = evaluate_proposal(target_path, audit=True)
    except Exception as e:
        log.exception("proposal evaluator failed")
        return _error(500, "internal_error", str(e))
    payload = result.to_dict()
    if "text/html" in request.headers.get("Accept", ""):
        body = (
            f'<div class="card"><h3>Evaluator — {payload["name"]}</h3>'
            f'<p><strong>verdict</strong>: <code>{payload["verdict"]}</code> · '
            f'<strong>score</strong>: {payload["score"]}</p>'
            f'<p>{payload["rationale"]}</p>'
            f'<details><summary>signals</summary>'
            f'<pre>{json.dumps(payload, ensure_ascii=False, indent=2)}</pre>'
            f'</details></div>'
        )
        return web.Response(text=body, content_type="text/html")
    return web.json_response(payload)


# --- /admin/promotions (ADR ?) -----------------------------------------------


_ALLOWED_PROMOTION_STATES: tuple[str, ...] = (
    "promoted_grace", "promoted_finalized", "review_needed",
    "rolled_back", "archived",
)


async def admin_promotions(request: web.Request) -> web.Response:
    """GET /admin/promotions?state=...&days=N — lista promozioni.

    Filtra per `state` (uno degli stati promoter) e finestra temporale
    `days` (default 30). Default `state=""` mostra tutti gli stati noti.
    """
    state = request.query.get("state", "").strip()
    days_raw = request.query.get("days", "30").strip()
    try:
        days = max(1, min(365, int(days_raw)))
    except ValueError:
        days = 30
    flash = request.query.get("flash", "").strip()
    try:
        from jobs.promoter_state import list_by_state
        from html_sanitizer import to_safe_html_full
    except ImportError as ex:
        log.exception("promoter_state import failed")
        return _error(500, "internal_error", str(ex))
    if state and state in _ALLOWED_PROMOTION_STATES:
        states = [state]
    else:
        states = list(_ALLOWED_PROMOTION_STATES)
    rows = list_by_state(states, limit=500)
    # Filtra per days su `promoted_at` se presente, altrimenti `created_at`.
    cutoff_ts = time.time() - days * 86400.0
    filtered: list[dict] = []
    for r in rows:
        anchor = r.get("promoted_at") or r.get("created_at") or ""
        # Parsing ISO -> epoch best-effort. Se vuoto, includi (defensive).
        if anchor:
            try:
                dt = datetime.strptime(anchor, "%Y-%m-%dT%H:%M:%SZ")
                dt = dt.replace(tzinfo=timezone.utc)
                if dt.timestamp() < cutoff_ts:
                    continue
            except ValueError:
                pass
        # Render markdown -> HTML safe per la cella esempio.
        ex_md = r.get("practical_example") or ""
        try:
            r["example_html"] = to_safe_html_full(ex_md) if ex_md else ""
        except Exception:
            import html as _html
            r["example_html"] = _html.escape(ex_md)
        filtered.append(r)
    return negotiate_collection(
        request,
        json_payload={"rows": filtered, "state": state, "days": days,
                        "total": len(filtered)},
        template="promotions.html",
        template_ctx={"rows": filtered, "state": state, "days": days,
                       "flash": flash},
    )


async def admin_promotion_rollback(request: web.Request) -> web.Response:
    """POST /admin/promotions/{id}/rollback — rollback di una promozione.

    Redirect alla lista con flash message. Errori → JSON 400/500.
    """
    proposal_id = urllib.parse.unquote(request.match_info["id"])
    try:
        from jobs.promoter_rollback import rollback_promotion
    except ImportError as ex:
        log.exception("promoter_rollback import failed")
        return _error(500, "internal_error", str(ex))
    try:
        result = rollback_promotion(proposal_id)
    except Exception as ex:  # noqa: BLE001
        log.exception("rollback failed for %s", proposal_id)
        return _error(500, "internal_error", str(ex))
    if not result.get("ok"):
        err = result.get("error") or "unknown"
        if "text/html" in request.headers.get("Accept", ""):
            msg = urllib.parse.quote(
                f"Rollback fallito per {proposal_id}: {err}"
            )
            raise web.HTTPFound(f"/admin/promotions?flash={msg}")
        return _error(400, "rollback_failed", err)
    if "text/html" in request.headers.get("Accept", ""):
        msg = urllib.parse.quote(
            f"Promozione {proposal_id} ({result.get('name', '?')}) annullata."
        )
        raise web.HTTPFound(f"/admin/promotions?flash={msg}")
    return web.json_response({"ok": True, **result})


async def admin_promotions_review(request: web.Request) -> web.Response:
    """GET /admin/promotions/review — form aggregator unico (E3, 11/5/2026).

    Costruisce un dialog `get_inputs`-shape (ADR 0090) con 3 step-group
    (Promossi, Da decidere, Bocciati recenti) e render HTML con
    `promotions_review.html`. Se nessuna decisione in attesa, mostra una
    pagina vuota con link di ritorno.
    """
    try:
        from admin.promotions_review import build_review_dialog
    except ImportError as ex:
        log.exception("promotions_review import failed")
        return _error(500, "internal_error", str(ex))
    try:
        dlg = build_review_dialog()
    except Exception as ex:  # noqa: BLE001
        log.exception("build_review_dialog failed")
        return _error(500, "internal_error", str(ex))
    flash = request.query.get("flash", "").strip()
    return negotiate_collection(
        request,
        json_payload=dlg,
        template="promotions_review.html",
        template_ctx={
            "dialog_id": dlg.get("dialog_id"),
            "title": dlg.get("title"),
            "description": dlg.get("description"),
            "dialog": dlg.get("dialog") or [],
            "groups": dlg.get("groups") or {},
            "flash": flash,
        },
    )


async def admin_promotions_review_submit(request: web.Request) -> web.Response:
    """POST /admin/promotions/review — applica decisioni form review.

    Il body e' `application/x-www-form-urlencoded` con la struttura:
        promoted_grace__<id1> = "Conferma promozione" | "Rollback" | "Skip"
        review_needed__<id2> = "Promuovi ora" | "Archivia" | "Skip"
        archived__<id3> = "Conferma archiviazione" | "Resurrect a pending" | "Skip"
        dialog_id = <hex16>  (opzionale, solo per audit)

    Atomic apply via `apply_review_decisions`. Redirect a /admin/promotions
    con flash message di summary.
    """
    try:
        from admin.promotions_review import apply_review_decisions
    except ImportError as ex:
        log.exception("promotions_review import failed")
        return _error(500, "internal_error", str(ex))
    try:
        form = await request.post()
    except Exception as ex:  # noqa: BLE001
        log.exception("form parse failed")
        return _error(400, "bad_form", str(ex))
    values: dict = {}
    for k, v in form.items():
        if k == "dialog_id":
            continue
        values[str(k)] = str(v)
    try:
        result = apply_review_decisions(values)
    except Exception as ex:  # noqa: BLE001
        log.exception("apply_review_decisions failed")
        return _error(500, "internal_error", str(ex))
    if "text/html" in request.headers.get("Accept", ""):
        msg = urllib.parse.quote(
            f"Review applicata: {result.get('applied', 0)} decisioni, "
            f"{result.get('skipped', 0)} skip, "
            f"{result.get('failed', 0)} errori."
        )
        raise web.HTTPFound(f"/admin/promotions?flash={msg}")
    return web.json_response({"ok": True, **result})


ROUTES = (
    ("GET",  "/admin/login",                      admin_login),
    ("POST", "/admin/login",                      admin_login),
    ("POST", "/admin/logout",                     admin_logout),
    ("GET",  "/admin",                            admin_home),
    ("GET",  "/admin/proposals",                  admin_proposals_unified),
    ("GET",  "/admin/proposals/introvertiva",     admin_proposals),
    ("POST", r"/admin/proposals/introvertiva/{sig_key}/{action:approve|reject|defer}", admin_proposal_action),
    ("GET",  "/admin/proposals/telos",            admin_telos_proposals),
    ("POST", r"/admin/proposals/telos/{prop_id}/{action:accept|reject|stage}", admin_telos_proposal_action),
    ("POST", r"/admin/proposals/telos/{prop_id}/cluster/{action:accept|reject|stage}",
              admin_telos_proposal_cluster_action),
    ("POST", r"/admin/proposals/unified/{source:telos|introvertiva}/{prop_id}/{action:accept|reject|stage}",
              admin_proposal_unified_action),
    ("GET",  r"/admin/synth-proposals/{id}/evaluate", admin_synth_proposal_evaluate),
    ("POST", r"/admin/synth-proposals/{id}/evaluate", admin_synth_proposal_evaluate),
    ("GET",  "/admin/promotions",                 admin_promotions),
    ("GET",  "/admin/promotions/review",          admin_promotions_review),
    ("POST", "/admin/promotions/review",          admin_promotions_review_submit),
    ("POST", r"/admin/promotions/{id}/rollback",  admin_promotion_rollback),
    ("GET",  "/admin/executors",                  admin_executors),
    ("GET",  "/admin/executors/stats",            admin_executors_stats),
    ("GET",  "/admin/runs",                       admin_runs),
    ("GET",  "/admin/builds",                     admin_builds),
    ("GET",  "/admin/safety",                     admin_safety),
    ("GET",  "/admin/turns",                      admin_turns),
    ("GET",  "/admin/users",                      admin_users),
    ("POST", "/admin/users",                      admin_users),
    ("GET",  "/admin/users/{id}",                 admin_user_detail),
    ("POST", "/admin/users/{id}/delete",          admin_user_delete),
    ("POST", "/admin/users/{id}/update",           admin_user_update),
    ("POST", "/admin/users/{id}/autonomy",        admin_user_set_autonomy),
    ("POST", r"/admin/users/{id}/channels/{channel}/pair",   admin_user_pair_channel),
    ("POST", r"/admin/users/{id}/channels/{channel}/remove", admin_user_remove_channel),
)
