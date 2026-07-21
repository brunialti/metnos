"""http_routes_admin — endpoint /admin/* (collezioni read-only + azioni proposte).

Tutte le rotte richiedono ruolo `admin` (la policy e' applicata a livello
di middleware in `http_auth.auth_middleware`).
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import urllib.parse
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import web

from http_app_state import (
    ADMIN_KEY as APP_ADMIN_KEY, CATALOG_PROVIDER, STARTED_AT, app_get,
)

import executor_aging
import proposals_state
import telos_proposals_store
import users
import services_registry
import config as _C  # §7.11
from http_auth import (
    ADMIN_COOKIE,
    ADMIN_COOKIE_TTL_S,
    issue_admin_cookie,
)
from http_render import (
    _error,
    negotiate_collection,
    render_template,
    serve_with_etag,
    wants_html,
)
from logging_setup import get_logger

log = get_logger(__name__)


async def admin_services(request: web.Request) -> web.Response:
    """GET /admin/services — status and bounded lifecycle controls."""
    rows = await asyncio.to_thread(services_registry.snapshots)
    if not wants_html(request):
        return web.json_response({"services": rows})
    notice = request.query.get("notice", "")
    return web.Response(
        text=render_template("services.html", services=rows, notice=notice),
        content_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


async def admin_service_action(request: web.Request) -> web.Response:
    """POST /admin/services/{name}/{action}; only catalog entries are allowed."""
    name = request.match_info["name"]
    action = request.match_info["action"]
    ok = False
    try:
        ok, detail = await asyncio.to_thread(
            services_registry.control, name, action,
        )
        if not ok:
            log.warning(
                "service action failed service=%s action=%s: %s",
                name, action, detail,
            )
    except Exception:  # noqa: BLE001 — il controllo non deve rompere la UI
        log.exception("service action failed service=%s action=%s", name, action)
    notice = "accepted" if ok else "failed"
    raise web.HTTPFound(f"/admin/services?notice={notice}")


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
    started = app_get(request.app, STARTED_AT, time.time())
    catalog = app_get(request.app, CATALOG_PROVIDER, lambda: [])()
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
    # chiavi UI_CHANGE_* nel catalogo seed (§7.13; bootstrap ritirato 7/7 —
    # guard: test_seed_i18n_gate_keys._REQUIRED_UI_CHANGE_KEYS).

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
    # chiavi UI_CHANGE_* nel catalogo seed (§7.13; bootstrap ritirato 7/7 —
    # guard: test_seed_i18n_gate_keys._REQUIRED_UI_CHANGE_KEYS).

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
    """Map canonical catalog metadata to serializable admin rows."""
    out = []
    for ex in sorted(catalog, key=lambda e: e.name):
        out.append({
            "name": ex.name,
            "version": ex.version,
            "lifecycle": ex.lifecycle,
            "source": getattr(ex, "source", "handcrafted"),
            "transport": getattr(ex, "transport", "local-subprocess"),
            "executor_standard": getattr(ex, "executor_standard", ""),
            "standard_state": getattr(ex, "standard_state", "legacy"),
            "execution_policy": getattr(ex, "execution_policy", {
                "effect": "unknown",
                "parallelism_class": 0,
                "resource_class": "default",
                "concurrency_key": "none",
                "equivalence_gate": "unverified",
            }),
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


async def admin_caches_flush(request: web.Request) -> web.Response:
    """POST /admin/caches/{layer:l0|l1|all}/flush — svuota le cache di piano.

    Opzione admin (Roberto 6/7): «se cambio engine cancello cache». L0 =
    fastpaths; L1 = autopaths + anti_autopaths + observations (i cluster
    semantici restano). Il riapprendimento riparte dal traffico reale.
    Best-effort: azzera anche la LRU in-process del proposer."""
    layer = request.match_info.get("layer") or ""
    loop = asyncio.get_running_loop()
    report: dict = {"layer": layer}

    def _do() -> None:
        if layer in ("l0", "all"):
            from engine import fastpath as _fp
            report.update(_fp.flush())
        if layer in ("l1", "all"):
            from engine import autopath as _ap
            report.update(_ap.flush())

    await loop.run_in_executor(None, _do)
    try:
        from engine.proposer import get_proposer
        _prop = get_proposer()
        if hasattr(_prop, "_candidate_cache"):
            _prop._candidate_cache.clear()
            report["proposer_lru_cleared"] = True
    except Exception as ex:
        log.debug("flush proposer LRU noop: %r", ex)
    log.info("[admin] cache flush %s: %s", layer, report)
    if "text/html" in (request.headers.get("Accept") or ""):
        from urllib.parse import quote
        raise web.HTTPFound("/admin/praxis?flash=" + quote(
            f"flush {layer}: " + ", ".join(
                f"{k}={v}" for k, v in report.items() if k != "layer")))
    return web.json_response(report)


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
        template_ctx={**payload, "flash": request.query.get("flash", "")},
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
    catalog = app_get(request.app, CATALOG_PROVIDER, lambda: [])()
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


# --- /admin/jobs/{key} (scheduler callback introspection) --------------------

def _scheduler_callback_registry():
    """Build the same callback registry used by manual scheduler actions."""
    from scheduler_v2 import builtin_callbacks
    from scheduler_v2.callbacks import CallbackRegistry

    class _StubScheduler:
        def __init__(self):
            self.callbacks = CallbackRegistry()

    stub = _StubScheduler()
    builtin_callbacks.install_default_callbacks(stub)
    return stub.callbacks


async def admin_job_info(request: web.Request) -> web.Response:
    """GET /admin/jobs/{key} - inspect registration without firing it."""
    key = request.match_info["key"]
    try:
        info = _scheduler_callback_registry().get(key)
    except Exception as exc:
        log.exception("job_info setup failed")
        return _error(500, "INTERNAL", str(exc))
    if info is None:
        return _error(404, "UNKNOWN_CALLBACK", f"callback `{key}` non registrato")
    return web.json_response({
        "ok": True,
        "callback": key,
        "is_async": bool(info.is_async),
        "description": info.description,
    })


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
        info = _scheduler_callback_registry().get(key)
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
    import devices as devices_mod
    import placement as placement_mod
    owned = devices_mod.list_by_owner(u["id"])
    dev_rows = [{
        "id": d.id, "name": d.name,
        "os": f"{d.os_family or '?'}/{d.os_arch or '?'}",
        "last_heartbeat": d.last_heartbeat,
        "available": placement_mod.is_available(d),
    } for d in owned]
    payload = {
        **_user_to_dict(u),
        "notes": u.get("notes"),
        "channels": [
            {**c, "verified": bool(c.get("verified_at"))} for c in chans
        ],
        "devices": dev_rows,
        # W2 v1 (ADR 0187): preferenze esplicite (vocabolario chiuso).
        "prefs": users.list_prefs(u["id"]),
    }
    if "text/html" in request.headers.get("Accept", ""):
        stealth_options = users.sites_stealth_preference_specs()
        web_pref_keys = {
            "sites_browser_mode", "sites_stealth",
            *(item["preference_key"] for item in stealth_options),
        }
        html = render_template("user_detail.html", user=payload,
                               channels=chans, devices=dev_rows,
                               prefs=payload["prefs"],
                               pref_allowed={
                                   key: allowed
                                   for key, allowed in users.PREF_ALLOWED.items()
                                   if key not in web_pref_keys
                               },
                               stealth_options=stealth_options,
                               flash=request.query.get("flash", ""))
        return web.Response(text=html, content_type="text/html")
    return web.json_response(payload)


async def admin_user_prefs(request: web.Request) -> web.Response:
    """POST /admin/users/{id}/prefs — imposta/azzera le preferenze (W2 v1).

    I campi generali vuoti eliminano la pref. Il gruppo stealth invia un marker:
    checkbox assente = off, presente = on, cosi' ogni tecnica e' indipendente."""
    user_id = request.match_info["id"]
    data = await request.post()
    results = []
    stealth_specs = users.sites_stealth_preference_specs()
    stealth_keys = (
        "sites_stealth",
        *(item["preference_key"] for item in stealth_specs),
    )
    web_keys = ("sites_browser_mode", *stealth_keys)
    if data.get("_sites_web_browsing_group") == "1":
        browser_mode = str(data.get("sites_browser_mode") or "headless")
        r = users.set_pref(user_id, "sites_browser_mode", browser_mode)
        results.append(
            f"sites_browser_mode={browser_mode}" if r.get("ok")
            else f"sites_browser_mode: {r.get('error')}")
        for key in stealth_keys:
            val = "on" if data.get(key) == "on" else "off"
            r = users.set_pref(user_id, key, val)
            results.append(f"{key}={val}" if r.get("ok")
                           else f"{key}: {r.get('error')}")
    for key in users.PREF_ALLOWED:
        if key in web_keys:
            continue
        if key not in data:
            continue
        val = str(data.get(key) or "").strip()
        if not val:
            users.delete_pref(user_id, key)
            results.append(f"{key}=∅")
        else:
            r = users.set_pref(user_id, key, val)
            results.append(f"{key}={val}" if r.get("ok")
                           else f"{key}: {r.get('error')}")
    raise web.HTTPFound(f"/admin/users/{user_id}")


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
    admin_key = app_get(request.app, APP_ADMIN_KEY, "")
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


# --- /admin/devices (executor remoti, design doc §5.2) ----------------------

async def admin_devices(request: web.Request) -> web.Response:
    """GET /admin/devices — lista device appaiati + stato heartbeat."""
    import devices as devices_mod
    import placement as placement_mod
    loop = asyncio.get_running_loop()
    devs = await loop.run_in_executor(None, devices_mod.list_devices)
    all_users = await loop.run_in_executor(None, users.list_users)
    uname = {u["id"]: (u.get("display_name") or u["name"]) for u in all_users}
    def _client_version(d):
        # ADR 0184: la versione viaggia nel profile del heartbeat (client
        # ≥0.2.12); i client più vecchi non la riportano → "—" onesto.
        try:
            import json as _json
            return (_json.loads(d.profile_json or "{}") or {}).get(
                "client_version") or ""
        except Exception:
            return ""
    rows = [{
        "id": d.id,
        "name": d.name,
        "owner_user_id": d.owner_user_id,
        "owner_name": uname.get(d.owner_user_id) or d.owner_user_id,
        "os_family": d.os_family,
        "os_arch": d.os_arch,
        "fingerprint": d.public_key_fingerprint,
        "last_heartbeat": d.last_heartbeat,
        "client_version": _client_version(d),
        "available": placement_mod.is_available(d),
    } for d in devs]
    return negotiate_collection(
        request,
        json_payload={"rows": rows, "total": len(rows)},
        template="devices.html",
        template_ctx={"rows": rows, "users": all_users},
    )


def _agent_server_url(request: web.Request) -> str:
    """URL dell'agent_server (porta 8765) visto dal device: stesso host della
    console, porta METNOS_AGENT_PORT. MVP senza TLS (overlay Headscale).

    Se la richiesta e' arrivata attraverso un proxy fidato (tunnel pubblico,
    es. Cloudflare — `request.remote` e' il tunnel locale, non il browser),
    l'Host header e' il dominio PUBBLICO: instrada tipicamente solo la porta
    console (8770), mai la porta device (8765, LAN/overlay-only per design,
    §6 design doc). Riusarlo per il link di join produce un URL che non
    risponde mai (bug live 2/7: browser bloccato su chat.metnos.com:8765).
    In quel caso ripiega su un IP LAN reale del server — il device che si
    appaia e' per contratto sulla stessa LAN/overlay, mai su Internet
    pubblico (mai allargare il tunnel a esporre la 8765, §6/ADR 0007)."""
    import os as _os
    from http_auth import _is_trusted_proxy
    port = _os.environ.get("METNOS_AGENT_PORT", "8765")
    if _is_trusted_proxy(request.remote):
        lan_ip = _pick_lan_ip()
        if lan_ip:
            log.warning(
                "[devices] richiesta via proxy fidato (Host=%s): uso IP LAN "
                "%s per il link device (la porta %s non e' instradata dal "
                "tunnel pubblico)",
                request.headers.get("Host", "?"), lan_ip, port)
            return f"http://{lan_ip}:{port}"
        log.warning(
            "[devices] richiesta via proxy fidato ma nessun IP LAN "
            "rilevato: ripiego sull'Host header (%s) — il link potrebbe "
            "non rispondere se non instrada la porta %s",
            request.headers.get("Host", "?"), port)
    host = (request.headers.get("Host") or "127.0.0.1").split(":")[0]
    return f"http://{host}:{port}"


def _resolve_pairing_owner(raw: str | None) -> tuple[str | None, str | None]:
    """Predisposizione multi-utente (2026-07-04): ogni device appaiato DEVE
    essere associato a un utente reale del registro. Risolve l'owner scelto
    dall'admin a un vero `users.id`. Input vuoto → utente host (default
    esplicito, non piu' il sentinel 'host'). Ritorna (owner_user_id, error):
    error!=None se l'utente indicato non esiste."""
    import devices as _devices
    s = (raw or "").strip()
    if not s:
        return _devices.host_user_id(), None
    u = users.get_user(s)
    if not u:
        return None, f"utente {s!r} inesistente"
    return u["id"], None


async def admin_devices_token(request: web.Request) -> web.Response:
    """POST /admin/devices/token — token effimero (TTL 10 min) + one-liner
    install Linux/Windows (design doc §5.2/§5.3/§5.4)."""
    import devices as devices_mod
    if request.content_type == "application/json":
        body = await request.json()
        name = (body.get("name") or "").strip()
        owner_raw = body.get("owner_user_id")
    else:
        form = await request.post()
        name = (form.get("name") or "").strip()
        owner_raw = form.get("owner_user_id")
    if not name or any(c.isspace() for c in name):
        return _error(400, "invalid_name",
                      "nome device non valido (no spazi, non vuoto)")
    owner_id, oerr = _resolve_pairing_owner(owner_raw)
    if oerr:
        return _error(400, "invalid_owner", oerr)
    loop = asyncio.get_running_loop()
    try:
        token = await loop.run_in_executor(
            None, lambda: devices_mod.generate_token(
                name, owner_user_id=owner_id, ttl_seconds=600))
    except devices_mod.TokenError as e:
        return _error(400, "token_error", str(e))
    server_url = _agent_server_url(request)
    accept = request.headers.get("Accept", "")
    if "application/json" in accept and "text/html" not in accept:
        return web.json_response({
            "name": name, "token": token, "server_url": server_url,
            "ttl_seconds": 600,
        })
    html = render_template(
        "devices_token.html",
        name=name, token=token, server_url=server_url,
        expires_at=int(time.time()) + 600,
    )
    return web.Response(text=html, content_type="text/html")


def _local_server_ips() -> set[str]:
    """IP locali del server (§5.2): psutil se disponibile, getaddrinfo come
    fallback. Cache di modulo: la lista cambia solo a riconfigurazione rete."""
    global _LOCAL_IPS_CACHE
    if _LOCAL_IPS_CACHE is not None:
        return _LOCAL_IPS_CACHE
    import socket
    ips: set[str] = {"127.0.0.1", "::1", "localhost"}
    try:
        import psutil
        for addrs in psutil.net_if_addrs().values():
            for a in addrs:
                if a.family in (socket.AF_INET, socket.AF_INET6) and a.address:
                    ips.add(a.address.split("%")[0])
    except Exception:
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None):
                ips.add(str(info[4][0]).split("%")[0])
        except Exception:
            pass
    _LOCAL_IPS_CACHE = ips
    return ips


_LOCAL_IPS_CACHE: set[str] | None = None


def _default_route_iface() -> str | None:
    """Interfaccia Linux della default route, quella a METRIC piu' basso
    fra le righe con Destination=00000000 in `/proc/net/route` (kernel
    routing table, nessun parsing di `ip route` via subprocess). `None` su
    non-Linux o senza default route: il chiamante ha un fallback."""
    try:
        with open("/proc/net/route") as f:
            next(f)  # header
            best_iface, best_metric = None, None
            for line in f:
                fields = line.split()
                if len(fields) < 7 or fields[1] != "00000000":
                    continue
                metric = int(fields[6])
                if best_metric is None or metric < best_metric:
                    best_iface, best_metric = fields[0], metric
            return best_iface
    except (OSError, ValueError, IndexError):
        return None


def _pick_lan_ip() -> str | None:
    """IP LAN reale del server (esclude loopback), filtrato sulle stesse
    reti "LAN" di `http_auth.LAN_NETS` (fonte unica §7.2, no doppia
    definizione). Con piu' interfacce LAN candidate, preferisce quella
    della default route del kernel invece di un ordine alfabetico
    arbitrario — scoperto live 3/7: `eth0` (cablata, `.33`, default
    route reale) vs `wlp195s0` (WiFi secondaria DHCP, `.126`, metric 700);
    l'ordine alfabetico avrebbe scelto la WiFi secondaria. `None` se il
    server non ha un'interfaccia LAN rilevabile."""
    import ipaddress
    import socket
    from http_auth import LAN_NETS

    def _in_lan(ip_s: str) -> bool:
        try:
            ip = ipaddress.ip_address(ip_s)
        except ValueError:
            return False
        return not ip.is_loopback and any(ip in net for net in LAN_NETS)

    candidates = sorted(ip_s for ip_s in _local_server_ips() if _in_lan(ip_s))
    if len(candidates) <= 1:
        return candidates[0] if candidates else None
    iface = _default_route_iface()
    if iface:
        try:
            import psutil
            for a in psutil.net_if_addrs().get(iface, []):
                if a.family == socket.AF_INET and a.address in candidates:
                    return a.address
        except Exception:
            pass
    return candidates[0]


def is_request_from_server(request: web.Request) -> bool:
    """True se il browser sta girando SUL server (§5.2): in quel caso non
    c'e' nessun client da installare. X-Forwarded-For NON fa fede (input
    utente, nessun reverse proxy trusted in questa stesura)."""
    remote = request.remote or ""
    return remote in _local_server_ips()


async def admin_devices_current_client(request: web.Request) -> web.Response:
    """GET /admin/devices/current-client — il browser e' sul server? (§5.2)"""
    return web.json_response({
        "is_server_client": is_request_from_server(request),
        "remote": request.remote,
    })


async def admin_devices_join(request: web.Request) -> web.Response:
    """POST /admin/devices/join {name, platform} — crea la join session e
    ritorna il link /agent/client/join/<join_id> per il PC target (§5.4)."""
    import devices as devices_mod
    if request.content_type == "application/json":
        body = await request.json()
    else:
        body = dict(await request.post())
    name = (body.get("name") or "").strip()
    platform = (body.get("platform") or "auto").strip()
    if not name or any(c.isspace() for c in name):
        return _error(400, "invalid_name",
                      "nome device non valido (no spazi, non vuoto)")
    owner_id, oerr = _resolve_pairing_owner(body.get("owner_user_id"))
    if oerr:
        return _error(400, "invalid_owner", oerr)
    # §5.2: browser sul server = niente da installare QUI. La creazione del
    # link resta legittima solo come gesto esplicito «per un ALTRO PC»
    # (for_other_pc=true, che la UI manda dal percorso opt-in).
    if is_request_from_server(request) and not bool(body.get("for_other_pc")):
        return _error(409, "client_is_server",
                      "questa UI gira sul server Metnos: non c'e' nessun "
                      "client da installare qui; per generare un link da "
                      "aprire su un ALTRO PC ripeti con for_other_pc=true")
    server_url = _agent_server_url(request)
    loop = asyncio.get_running_loop()
    try:
        sess = await loop.run_in_executor(
            None, lambda: devices_mod.create_join_session(
                name, platform=platform, server_url=server_url,
                owner_user_id=owner_id))
    except devices_mod.TokenError as e:
        return _error(400, "token_error", str(e))
    return web.json_response({
        "join_id": sess["join_id"],
        "join_url": f"{server_url}/agent/client/join/{sess['join_id']}",
        "state": sess["state"],
        "device_name": sess["device_name"],
        "platform": sess["platform"],
        "expires_at": sess["expires_at"],
    })


async def admin_devices_join_status(request: web.Request) -> web.Response:
    """GET /admin/devices/join/{join_id}/status — polling avanzamento UI."""
    import devices as devices_mod
    join_id = request.match_info["join_id"]
    loop = asyncio.get_running_loop()
    sess = await loop.run_in_executor(
        None, lambda: devices_mod.get_join_session(join_id))
    if sess is None:
        return _error(404, "unknown_join", "join session inesistente")
    return web.json_response({
        "join_id": join_id,
        "state": sess["state"],
        "device_name": sess["device_name"],
        "device_id": sess.get("device_id"),
        "expires_at": sess["expires_at"],
    }, headers={"Cache-Control": "no-store"})


async def admin_device_revoke(request: web.Request) -> web.Response:
    """POST /admin/devices/{id}/revoke — revoca device (token futuri rifiutati)."""
    import devices as devices_mod
    device_id = request.match_info["id"]
    loop = asyncio.get_running_loop()
    ok = await loop.run_in_executor(
        None, lambda: devices_mod.revoke_device(device_id))
    if not ok:
        return _error(404, "not_found", "device inesistente o gia' revocato")
    raise web.HTTPFound("/admin/devices")


# --- /admin/devices/{id}/test-invoke (manual trigger, gemello di jobs/fire) --

async def admin_device_test_invoke(request: web.Request) -> web.Response:
    """POST /admin/devices/{id}/test-invoke — invoca manualmente un executor
    su un device appaiato (W3.3, design doc §16.4: validazione E2E su
    hardware reale senza il bypass diretto-al-DB dello script bash di test).

    Body: {"executor": "<nome>", "args": {...}, "deadline_ms"?: int}.

    Va attraverso la pipeline REALE (firma Ed25519, poll, sandbox client,
    result firmato) — bypassa SOLO la decisione di placement (qui il device
    e' scelto a mano dall'admin, non da choose_placement, che i due
    executor oggi promossi per Windows non attraverserebbero comunque:
    nessuno ha ancora [placement] scope="device" nel manifest).

    Blocking: attende il result fino a deadline_ms (+ margine di rete) prima
    di rispondere, cosi' un solo comando PowerShell/curl basta per il test
    manuale — niente polling separato lato chiamante. Sincrono per contratto
    (§12 modalita' di fallimento): se il device non risponde in tempo,
    l'HTTP risponde comunque (202 pending), mai un timeout silenzioso.
    """
    device_id = request.match_info["id"]
    try:
        body = await request.json()
    except Exception:
        return _error(400, "invalid_json", "request body must be JSON")
    if not isinstance(body, dict):
        return _error(400, "invalid_json", "request body must be a JSON object")
    executor_name = body.get("executor")
    if not isinstance(executor_name, str) or not executor_name:
        return _error(400, "missing_field", "'executor' e' obbligatorio")
    args = body.get("args") if isinstance(body.get("args"), dict) else {}
    deadline_ms = body.get("deadline_ms")
    if not isinstance(deadline_ms, int) or deadline_ms <= 0:
        deadline_ms = 30_000

    import devices as devices_mod
    import invocations as invocations_mod
    import loader as loader_mod
    loop = asyncio.get_running_loop()

    dev = await loop.run_in_executor(None, devices_mod.get_device, device_id)
    if dev is None or dev.revoked_at is not None:
        return _error(404, "unknown_device", "device inesistente o revocato")

    # L'executor DEVE esistere firmato e verificato nel catalogo (stessa
    # garanzia del bundle §8): mai eseguire codice non verificato solo
    # perche' l'admin lo chiede a mano.
    catalog = await loop.run_in_executor(None, loader_mod.load_catalog)
    if catalog.get(executor_name) is None:
        return _error(404, "unknown_executor",
                      f"executor '{executor_name}' non nel catalogo (o non verificato)")

    try:
        inv_id = await loop.run_in_executor(
            None, lambda: invocations_mod.enqueue_invocation(
                device_id, executor_name, args, deadline_ms=deadline_ms))
    except invocations_mod.InvocationError as e:
        return _error(400, "enqueue_failed", str(e))

    # Attesa sincrona del result (§7.2: riusa wait_result, non re-inventa il
    # polling). Margine di rete oltre la deadline dell'invocazione stessa.
    result = await loop.run_in_executor(
        None, lambda: invocations_mod.wait_result(
            inv_id, timeout_s=(deadline_ms / 1000.0) + 5.0))
    if result is None:
        info = await loop.run_in_executor(None, invocations_mod.get_invocation, inv_id)
        return web.json_response(
            {"invocation_id": inv_id,
             "state": (info or {}).get("state", "queued"),
             "note": "deadline superato in attesa del result; riprova o "
                     "controlla il device (heartbeat, log client)"},
            status=202)
    return web.json_response({"invocation_id": inv_id, "state": "done",
                              "result": result})


ROUTES = (
    ("GET",  "/admin/services",                  admin_services),
    ("POST", r"/admin/services/{name}/{action:start|stop|restart}", admin_service_action),
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
    ("POST", r"/admin/caches/{layer:l0|l1|all}/flush", admin_caches_flush),
    # /admin/aporiae* rimosse 13/6/2026: store Aporia dismesso con Engine v2
    # (Bonifica 28/5). Feature ritirata, nessun rimpiazzo.
    ("GET",  "/admin/executors",                  admin_executors),
    ("GET",  "/admin/executors/stats",            admin_executors_stats),
    ("GET",  r"/admin/jobs/{key}",                admin_job_info),
    ("POST", r"/admin/jobs/{key}/fire",           admin_job_fire),
    ("GET",  "/admin/runs",                       admin_runs),
    ("GET",  "/admin/builds",                     admin_builds),
    ("GET",  "/admin/safety",                     admin_safety),
    ("GET",  "/admin/turns",                      admin_turns),
    ("GET",  "/admin/users",                      admin_users),
    ("POST", "/admin/users",                      admin_users),
    ("GET",  "/admin/users/{id}",                 admin_user_detail),
    ("POST", "/admin/users/{id}/prefs",          admin_user_prefs),
    ("POST", "/admin/users/{id}/delete",          admin_user_delete),
    ("POST", "/admin/users/{id}/update",           admin_user_update),
    ("POST", "/admin/users/{id}/autonomy",        admin_user_set_autonomy),
    ("POST", r"/admin/users/{id}/channels/{channel}/pair",   admin_user_pair_channel),
    ("POST", r"/admin/users/{id}/channels/{channel}/remove", admin_user_remove_channel),
    ("GET",  "/admin/devices",                    admin_devices),
    ("GET",  "/admin/devices/current-client",     admin_devices_current_client),
    ("POST", "/admin/devices/token",              admin_devices_token),
    ("POST", "/admin/devices/join",               admin_devices_join),
    ("GET",  r"/admin/devices/join/{join_id}/status", admin_devices_join_status),
    ("POST", r"/admin/devices/{id}/revoke",       admin_device_revoke),
    ("POST", r"/admin/devices/{id}/test-invoke",  admin_device_test_invoke),
)
