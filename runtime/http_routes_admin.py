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
import telos_proposals_store
import users
import config as _C  # §7.11
from http_auth import (
    ADMIN_COOKIE,
    ADMIN_COOKIE_TTL_S,
    issue_admin_cookie,
)
from http_render import _error, negotiate_collection, render_template, serve_with_etag
from logging_setup import get_logger

log = get_logger(__name__)


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
    return _C.PATH_USER_DATA / "turns"


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


# --- /admin/changes (ADR 0158, unified change-intent lifecycle) ----------------

# Default cap della UI. Sopra 50 il triage manuale non scala (the design guide §F.2).
_CHANGES_DEFAULT_LIMIT = 30
_CHANGES_TABS = [
    {"key": "proposed"},
    {"key": "accepted"},
    {"key": "applied"},
    {"key": "observed"},
    {"key": "finalized"},
    {"key": "staged"},
    {"key": "rejected"},
    {"key": "rolled_back"},
    {"key": "failed"},
]


async def admin_changes(request: web.Request) -> web.Response:
    """GET /admin/changes — vista unificata change_intent lifecycle.

    Query params:
      state: proposed (default) | accepted | applied | observed |
             finalized | staged | rejected | rolled_back | failed
      family: telos|introvertiva|synt|multi_tool|canonical|user (vuoto=tutti)
      kind: create_executor|extend_executor|... (vuoto=tutti)
      min_score: float (default 0)
      limit: int (default 30)
    """
    import change_intents as ci_mod
    import change_intents_i18n
    change_intents_i18n.bootstrap_keys()

    state = (request.query.get("state") or "proposed").strip().lower()
    if state not in {t["key"] for t in _CHANGES_TABS}:
        state = "proposed"
    family = (request.query.get("family") or "").strip() or None
    kind = (request.query.get("kind") or "").strip() or None
    try:
        min_score = float(request.query.get("min_score") or 0.0)
    except (ValueError, TypeError):
        min_score = 0.0
    try:
        limit = int(request.query.get("limit") or _CHANGES_DEFAULT_LIMIT)
    except (ValueError, TypeError):
        limit = _CHANGES_DEFAULT_LIMIT
    limit = max(1, min(500, limit))

    rows_objs = ci_mod.list_intents(
        state=state, origin_family=family, intent_kind=kind,
        min_score=min_score, limit=limit, order_by="score_desc",
    )
    rows = [asdict(r) for r in rows_objs]
    counts = ci_mod.count_by_state()

    # Facets — conta per family e kind sull'INSIEME corrente (state filtered)
    all_in_state = ci_mod.list_intents(state=state, limit=5000)
    family_counts: dict[str, int] = {}
    kind_counts: dict[str, int] = {}
    for r in all_in_state:
        family_counts[r.origin_family] = family_counts.get(r.origin_family, 0) + 1
        kind_counts[r.intent_kind] = kind_counts.get(r.intent_kind, 0) + 1

    # Query string passthrough (per i link delle tabs)
    qs_parts = []
    if family:
        qs_parts.append(f"family={urllib.parse.quote(family)}")
    if kind:
        qs_parts.append(f"kind={urllib.parse.quote(kind)}")
    if min_score:
        qs_parts.append(f"min_score={min_score}")
    if limit != _CHANGES_DEFAULT_LIMIT:
        qs_parts.append(f"limit={limit}")
    qs_extra = ("&" + "&".join(qs_parts)) if qs_parts else ""

    return negotiate_collection(
        request,
        json_payload={
            "rows": rows,
            "counts": counts,
            "family_counts": family_counts,
            "kind_counts": kind_counts,
            "filters": {
                "state": state,
                "family": family or "",
                "kind": kind or "",
                "min_score": min_score,
                "limit": limit,
            },
        },
        template="changes.html",
        template_ctx={
            "rows": rows,
            "counts": counts,
            "family_counts": family_counts,
            "kind_counts": kind_counts,
            "tabs": _CHANGES_TABS,
            "state": state,
            "family": family or "",
            "kind": kind or "",
            "min_score": min_score,
            "limit": limit,
            "qs_extra": qs_extra,
        },
    )


def _render_change_row_html(ci_row) -> str:
    """Riga aggiornata post-decisione (htmx swap)."""
    from messages import get as _msg
    state_chip = {
        "accepted":    f'<span class="chip ok">{_msg("UI_CHANGE_BADGE_ACCEPTED")}</span>',
        "rejected":    f'<span class="chip bad">{_msg("UI_CHANGE_BADGE_REJECTED")}</span>',
        "staged":      f'<span class="chip">{_msg("UI_CHANGE_TAB_STAGED")}</span>',
        "applied":     f'<span class="chip ok">{_msg("UI_CHANGE_BADGE_APPLIED")}</span>',
        "observed":    f'<span class="chip ok">{_msg("UI_CHANGE_BADGE_OBSERVED")}</span>',
        "finalized":   f'<span class="chip ok">{_msg("UI_CHANGE_BADGE_FINALIZED")}</span>',
        "rolled_back": f'<span class="chip bad">{_msg("UI_CHANGE_BADGE_ROLLED_BACK")}</span>',
        "failed":      f'<span class="chip bad">{_msg("UI_CHANGE_BADGE_FAILED")}</span>',
        "proposed":    '<span class="chip muted">proposed</span>',
    }.get(ci_row.state, f'<span class="chip muted">{ci_row.state}</span>')
    return (
        f'<tr id="ci-{ci_row.id}">'
        f'<td colspan="6" class="muted">'
        f'{ci_row.intent_target}: {state_chip}'
        f' <span class="muted">(by {ci_row.decision_by or "system"} at '
        f'{ci_row.updated_at})</span></td></tr>'
    )


async def admin_change_action(request: web.Request) -> web.Response:
    """POST /admin/changes/{id}/{action}.

    action ∈ accept | reject | stage | rollback | retry
    Body opzionale: {"reason": "..."}.
    """
    import change_intents as ci_mod
    import change_intents_i18n
    change_intents_i18n.bootstrap_keys()

    from messages import get as _msg

    id_ = request.match_info["id"]
    action = request.match_info["action"]

    if action not in ("accept", "reject", "stage", "rollback", "retry"):
        return _error(400, "INVALID_ACTION",
                      _msg("ERR_CHANGE_INVALID_ACTION", action=action))

    by = request.get("admin_user") or "admin"

    try:
        body = await request.json() if request.body_exists else {}
    except Exception:
        body = {}
    reason = body.get("reason") if isinstance(body, dict) else None

    try:
        if action in ("accept", "reject", "stage"):
            updated = ci_mod.apply_decision(id_, action=action, by=by, reason=reason)
        elif action == "rollback":
            updated = ci_mod.mark_rolled_back(
                id_, reason=reason or "user-initiated rollback",
            )
        elif action == "retry":
            # failed → accepted (re-enqueue al daemon)
            cur = ci_mod.get_intent(id_)
            if cur is None:
                return _error(404, "NOT_FOUND",
                              _msg("ERR_CHANGE_NOT_FOUND", id=id_))
            if cur.state != "failed":
                return _error(400, "INVALID_TRANSITION",
                              f"retry richiede state=failed, attuale={cur.state}")
            updated = ci_mod._transition(id_, to_state="accepted",
                                          extra_cols={"decision_by": by,
                                                      "decision_ts": ci_mod._iso_utc_now(),
                                                      "decision_action": "retry",
                                                      "decision_reason": reason})
        else:
            return _error(400, "INVALID_ACTION",
                          _msg("ERR_CHANGE_INVALID_ACTION", action=action))
    except ci_mod.TransitionError as exc:
        return _error(400, "TRANSITION_ERROR", str(exc))
    except Exception as exc:
        log.exception("change_action failed: %s", exc)
        return _error(500, "INTERNAL", str(exc))

    # htmx negotiate (HX-Request header) o JSON
    if request.headers.get("HX-Request") == "true":
        return web.Response(
            text=_render_change_row_html(updated),
            content_type="text/html",
        )
    return web.json_response({
        "ok": True, "id": id_, "action": action, "state": updated.state,
        "message": _msg("MSG_CHANGE_DECISION_OK", action=action),
    })


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


# --- /admin/praxis — motore cognitivo Engine v2 -------------------------------

def _decode_observation_tools(observations: list[dict]) -> None:
    """Aggiunge `tools` (catena dal framework_json) per il display."""
    import json as _json
    for o in observations:
        try:
            fw = _json.loads(o.get("framework_json") or "{}")
            o["tools"] = [s.get("tool") for s in fw.get("steps") or []]
        except Exception:
            o["tools"] = []


async def admin_praxis(request: web.Request) -> web.Response:
    """GET /admin/praxis — dashboard del motore cognitivo (Engine v2).

    Espone gli strati CON STATO del motore a 4 strati
    (docs/it/architecture/praxis_engine.html):
      L0 fastpath — scorciatoie AUTO-prodotte dai turni riusciti
                    (fastpaths.sqlite; valvola: delete per riga)
      L1 autopath — autopath apprese dal feedback ✓ (autopath.sqlite)
    L2 validator e L3 proposer/recovery sono stateless (niente storage).
    """
    # L0 — fastpath (scorciatoie auto-prodotte)
    try:
        from engine import fastpath as _fastpath
        fastpaths = _fastpath.list_all(limit=100)
    except Exception as ex:
        log.warning("admin_praxis: fastpath read failed: %r", ex)
        fastpaths = []
    # L1 — autopath (autopath apprese, osservazioni, anti-autopath)
    try:
        from engine import autopath as _autopath
        stats = _autopath.stats()
        autopaths_active = _autopath.list_autopaths(status="active", limit=50)
        autopaths_demoted = _autopath.list_autopaths(status="demoted", limit=20)
        observations = _autopath.recent_observations(limit=30)
        _decode_observation_tools(observations)
        anti_autopaths = _autopath.active_anti_autopaths(limit=20)
    except Exception as ex:
        log.warning("admin_praxis: autopath read failed: %r", ex)
        stats = {"autopaths_by_status": {}, "observations_total": 0,
                  "anti_autopaths_active": 0, "error": str(ex)}
        autopaths_active = autopaths_demoted = []
        observations = anti_autopaths = []
    # Pronoia config display (tier di escalation del recovery)
    import os as _os
    pronoia_tier = _os.environ.get("METNOS_PRONOIA_TIER", "wise")

    payload = {
        "stats": stats,
        "fastpaths": fastpaths,
        "autopaths_active": autopaths_active,
        "autopaths_demoted": autopaths_demoted,
        "observations": observations,
        "anti_autopaths": anti_autopaths,
        "pronoia_tier": pronoia_tier,
    }
    return negotiate_collection(
        request,
        json_payload=payload,
        template="praxis.html",
        template_ctx=payload,
    )


async def admin_praxis_fastpath_delete(request: web.Request) -> web.Response:
    """POST /admin/praxis/fastpaths/{id}/delete — valvola admin sui fastpath
    L0 AUTO-prodotti: una scorciatoia sbagliata si rimuove a mano (si ricrea
    solo se il piano pieno ri-succede sulla stessa query)."""
    role = request.get("role", "anonymous")
    if role != "admin":
        return _error(403, "forbidden", "admin role required")
    fid_str = request.match_info.get("id", "")
    try:
        fid = int(fid_str)
    except ValueError:
        return _error(400, "bad_id", f"id must be int, got {fid_str!r}")
    try:
        from engine import fastpath as _fastpath
        if not _fastpath.delete(fid):
            return _error(404, "not_found", f"fastpath {fid} inesistente")
        log.info("admin: fastpath %d eliminato (valvola manuale)", fid)
        return web.json_response({"ok": True, "id": fid, "deleted": True})
    except Exception as ex:
        return _error(500, "internal", str(ex))


async def admin_praxis_config(request: web.Request) -> web.Response:
    """POST /admin/praxis/config — aggiorna config Pronoia (tier, ecc.).

    Body JSON: {pronoia_tier: 'wise'|'frontier'}.
    Persistenza: env update + runtime_settings.toml.
    """
    role = request.get("role", "anonymous")
    if role != "admin":
        return _error(403, "forbidden", "admin role required")
    try:
        body = await request.json()
    except Exception:
        return _error(400, "bad_json", "invalid JSON")
    tier = (body.get("pronoia_tier") or "").strip().lower()
    if tier not in ("wise", "frontier"):
        return _error(400, "bad_value",
                       f"pronoia_tier must be wise|frontier, got {tier!r}")
    # Update env in current process
    import os
    os.environ["METNOS_PRONOIA_TIER"] = tier
    # Persist su runtime.toml deferred (MVP: env runtime only — survive
    # finche' processo vive; per persistente cross-restart, set in unit env).
    return web.json_response({
        "ok": True, "pronoia_tier": tier, "persisted": False,
        "note": "env runtime only; per persistere cross-restart "
                 "edit /etc/systemd/system/metnos-http.service Environment.",
    })


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


# --- /admin/jobs/{key}/fire (manual trigger scheduler callbacks) -------------

async def admin_job_fire(request: web.Request) -> web.Response:
    """POST /admin/jobs/{key}/fire — invoca manualmente un callback
    scheduler v2 (es. change_intent_materialize, change_applier,
    change_observer). Body opzionale JSON come `payload`.

    Use case: trigger manuale admin + simulatore E2E. Idempotente per
    job che sono progettati cosi'. Niente lock cross-fire: il caller
    e' responsabile di evitare race.
    """
    key = request.match_info["key"]
    try:
        body = await request.json() if request.body_exists else {}
    except Exception:
        body = {}
    payload = body if isinstance(body, dict) else {}
    try:
        from scheduler_v2 import builtin_callbacks
        # Lazy-build callback registry sub-instance (no scheduler running).
        from scheduler_v2.callbacks import CallbackRegistry
        # Construct fake scheduler-like object exposing only .callbacks.
        class _StubScheduler:
            callbacks = CallbackRegistry()
        stub = _StubScheduler()
        builtin_callbacks.install_default_callbacks(stub)
        info = stub.callbacks.get(key)
        if info is None:
            return _error(404, "UNKNOWN_CALLBACK",
                            f"callback `{key}` non registrato")
        cb = info.fn  # CallbackInfo wraps fn
    except Exception as exc:
        log.exception("job_fire setup failed")
        return _error(500, "INTERNAL", str(exc))
    try:
        if info.is_async:
            result = await cb(payload)
        else:
            result = cb(payload)
    except Exception as exc:
        log.exception("job_fire callback failed: %s", key)
        return _error(500, "CALLBACK_ERROR", str(exc))
    return web.json_response({"ok": True, "callback": key, "result": result})


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
        secure=True,  # servito via HTTPS (Cloudflare); allinea al cookie user
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

_SYNT_PROPOSALS_DIR = _C.PATH_USER_DATA / "synt_proposals"


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


async def admin_timers(request: web.Request) -> web.Response:
    """GET /admin/timers — gestione timer di sistema (scheduler v2).

    Tabella semplice (§14 admin-only): nome+descrizione, trigger, stato,
    prossima esecuzione, ultima esec.+esito, run/fail, azioni (abilita/
    disabilita/esegui-ora). Legge da SchedulerStorage.list_all().
    """
    import html as _html
    from scheduler_v2.storage import SchedulerStorage, DEFAULT_DB_PATH
    entries = SchedulerStorage(DEFAULT_DB_PATH).list_all()
    flash = request.query.get("flash", "")

    def _next(ts):
        try:
            ts = float(ts)
        except (TypeError, ValueError):
            return "—"
        d = ts - time.time()
        base = time.strftime("%d/%m %H:%M", time.localtime(ts))
        if d < 0:
            return f"{base} · scaduto"
        if d < 3600:
            return f"{base} · tra {int(d // 60)}m"
        if d < 86400:
            return f"{base} · tra {d / 3600:.1f}h"
        return f"{base} · tra {int(d // 86400)}g"

    from scheduler_v2.builtin_callbacks import _BUILTIN_JOBS as _BJ
    # Classifica per appartenenza a _BUILTIN_JOBS (autoritativo): la colonna
    # `origin` nel DB puo' essere errata (es. multi_tool_maintenance, builtin,
    # marcato 'user' da una vecchia migrate).
    # (github_watcher RITIRATO → executor write/read/find_issues + comandi:
    # nessun builtin concettualmente-utente al momento. I `user_*` e i
    # non-builtin sono gia' utente per esclusione.)
    _user_facing_builtin: set = set()
    _sys_names = {j.get("name") for j in _BJ} - _user_facing_builtin
    sys_rows, user_rows = [], []
    for e in entries:
        en = bool(e.enabled)
        badge = ("<span style='color:#16a34a'>● attivo</span>" if en
                 else "<span style='color:#9ca3af'>○ disattivo</span>")
        stt = e.last_status or "—"
        col = "#16a34a" if stt == "success" else ("#dc2626" if stt not in ("—", None) else "#9ca3af")
        toggle = "disable" if en else "enable"
        toggle_lbl = "Disabilita" if en else "Abilita"
        nm = _html.escape(e.name)
        (sys_rows if e.name in _sys_names else user_rows).append(
            "<tr>"
            f"<td><b>{nm}</b><br><small style='color:#6b7280'>{_html.escape((e.description or '')[:140])}</small></td>"
            f"<td><code>{_html.escape(e.trigger)}</code></td>"
            f"<td>{badge}</td>"
            f"<td><small>{_next(e.next_fire_at)}</small></td>"
            f"<td><small>{_html.escape(str(e.last_run_at or '—'))}</small></td>"
            f"<td style='color:{col}'><small>{_html.escape(str(stt))}</small></td>"
            f"<td><small>{e.total_runs}/{e.total_failures}</small></td>"
            "<td style='white-space:nowrap'>"
            f"<form method='post' action='/admin/timers/{nm}/{toggle}' style='display:inline'><button>{toggle_lbl}</button></form> "
            f"<form method='post' action='/admin/timers/{nm}/fire' style='display:inline'><button>Esegui ora</button></form>"
            "</td></tr>"
        )

    flash_html = (
        f"<div style='background:#eef2ff;border:1px solid #c7d2fe;padding:8px 12px;"
        f"border-radius:6px;margin:10px 0'>{_html.escape(flash)}</div>"
    ) if flash else ""
    n_on = sum(1 for e in entries if e.enabled)
    body = (
        "<!doctype html><html lang='it'><head><meta charset='utf-8'>"
        "<title>Timer di sistema · Metnos</title><style>"
        "body{font-family:system-ui,-apple-system,sans-serif;max-width:1150px;margin:1.5rem auto;padding:0 1rem;color:#1f2937}"
        "table{border-collapse:collapse;width:100%;font-size:13.5px}"
        "th,td{border-bottom:1px solid #e5e7eb;padding:7px 9px;text-align:left;vertical-align:top}"
        "th{background:#f9fafb;font-size:11px;text-transform:uppercase;color:#6b7280;letter-spacing:.04em}"
        "button{cursor:pointer;padding:3px 9px;font-size:12px;border:1px solid #d1d5db;border-radius:5px;background:#fff}"
        "button:hover{background:#f3f4f6}code{background:#f3f4f6;padding:1px 5px;border-radius:4px;font-size:12px}"
        "a{color:#2563eb;text-decoration:none}h1{font-size:1.4rem;margin-bottom:.2rem}</style></head><body>"
        "<p><a href='/admin'>← admin</a></p>"
        "<h1>Scheduler · timer &amp; task</h1>"
        f"<p style='color:#6b7280'>{len(user_rows)} task utente · {len(sys_rows)} timer di sistema · {n_on} attivi · scheduler v2</p>"
        f"{flash_html}"
        "<h2 style='font-size:1.05rem;margin:1.2rem 0 .3rem'>Task utente</h2>"
        + ("<table><tr><th>Task</th><th>Trigger</th><th>Stato</th><th>Prossimo</th>"
           "<th>Ultima esec.</th><th>Esito</th><th>Run/Fail</th><th>Azioni</th></tr>"
           f"{''.join(user_rows)}</table>" if user_rows
           else "<p style='color:#9ca3af'>nessun task utente</p>")
        + "<h2 style='font-size:1.05rem;margin:1.4rem 0 .3rem'>Timer di sistema</h2>"
        "<table><tr><th>Job</th><th>Trigger</th><th>Stato</th><th>Prossimo</th>"
        "<th>Ultima esec.</th><th>Esito</th><th>Run/Fail</th><th>Azioni</th></tr>"
        f"{''.join(sys_rows)}</table></body></html>"
    )
    return web.Response(body=body.encode("utf-8"), content_type="text/html")


async def admin_timer_action(request: web.Request) -> web.Response:
    """POST /admin/timers/{name}/{action} — enable|disable|fire di un timer."""
    from urllib.parse import quote
    import json as _json
    name = request.match_info["name"]
    action = request.match_info["action"]
    from scheduler_v2.storage import SchedulerStorage, DEFAULT_DB_PATH
    st = SchedulerStorage(DEFAULT_DB_PATH)
    entry = st.get_by_name(name)
    if entry is None:
        raise web.HTTPFound("/admin/timers?flash=" + quote(f"timer '{name}' non trovato"))
    if action == "disable":
        st.disable(entry.id)
        msg = f"'{name}' disabilitato"
    elif action == "enable":
        st.enable(entry.id)
        msg = f"'{name}' abilitato (next_fire ricalcolato)"
    elif action == "fire":
        try:
            from scheduler_v2 import builtin_callbacks
            from scheduler_v2.callbacks import CallbackRegistry

            class _Stub:
                callbacks = CallbackRegistry()
            stub = _Stub()
            builtin_callbacks.install_default_callbacks(stub)
            info = stub.callbacks.get(entry.callback_key)
            if info is None:
                msg = f"'{name}': callback '{entry.callback_key}' non registrato"
            else:
                # Passa il payload reale dell'entry: per i task utente
                # (run_user_query) e' il `record` con query/actor/channel;
                # per i builtin e' {} (ignorato).
                pl = getattr(entry, "payload", None)
                if isinstance(pl, str):
                    try:
                        pl = _json.loads(pl)
                    except Exception:
                        pl = {}
                _pl = pl if isinstance(pl, dict) else {}
                # await i callback async (es. nightly_maintenance): chiamarli senza
                # await ritorna una coroutine mai eseguita (§2.8 falso "eseguito").
                # Specchia il ramo corretto di admin_job_fire.
                res = await info.fn(_pl) if info.is_async else info.fn(_pl)
                msg = f"'{name}' eseguito → {str(res)[:160]}"
        except Exception as ex:
            msg = f"'{name}' errore: {type(ex).__name__}: {ex}"
    else:
        msg = f"azione '{action}' ignota"
    raise web.HTTPFound("/admin/timers?flash=" + quote(msg))


ROUTES = (
    # /admin/skills/{id}/history rimossa 13/6/2026: store Praxis dismesso (Engine v2).
    ("GET",  "/admin/timers",                     admin_timers),
    ("POST", r"/admin/timers/{name}/{action:enable|disable|fire}", admin_timer_action),
    ("GET",  "/admin/login",                      admin_login),
    ("POST", "/admin/login",                      admin_login),
    ("POST", "/admin/logout",                     admin_logout),
    ("GET",  "/admin",                            admin_home),
    ("GET",  "/admin/changes",                    admin_changes),
    ("POST", r"/admin/changes/{id}/{action:accept|reject|stage|rollback|retry}",
              admin_change_action),
    # /admin/proposals* rimosse 13/6/2026: superate dalla vista unificata
    # /admin/changes (ADR 0158, full migration accepted 22/5). Telos/introvertiva
    # ora sono `family` facet di change_intents.
    ("GET",  r"/admin/synth-proposals/{id}/evaluate", admin_synth_proposal_evaluate),
    ("POST", r"/admin/synth-proposals/{id}/evaluate", admin_synth_proposal_evaluate),
    # /admin/promotions* rimosse 13/6/2026: superate da /admin/changes
    # (lifecycle states applied|observed|finalized|rolled_back). ADR 0158.
    ("GET",  "/admin/praxis",                     admin_praxis),
    ("POST", "/admin/praxis/config",              admin_praxis_config),
    ("POST", r"/admin/praxis/fastpaths/{id}/delete", admin_praxis_fastpath_delete),
    # /admin/aporiae* rimosse 13/6/2026: store Aporia dismesso con Engine v2
    # (Bonifica 28/5). Feature ritirata, nessun rimpiazzo.
    ("GET",  "/admin/executors",                  admin_executors),
    ("GET",  "/admin/executors/stats",            admin_executors_stats),
    ("POST", r"/admin/jobs/{key}/fire",           admin_job_fire),
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
