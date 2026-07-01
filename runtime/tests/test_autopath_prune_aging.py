"""Aging della tabella autopaths in prune() (1/7/2026).

Prima del fix prune() potava solo anti_autopaths scadute e observations oltre
cap: le righe autopaths NON avevano alcuna valvola — demoted zombie e active
mai piu' usate restavano per sempre. Regole nuove:
  - demoted con COALESCE(ts_last_used, ts_created) oltre
    METNOS_AUTOPATH_DEMOTED_TTL_DAYS (30gg = TTL anti_autopath) → DELETE;
  - active con ts_last_used oltre METNOS_AUTOPATH_STALE_DAYS (90gg = 3x L0
    stale) → DELETE.
Un feedback ✓ successivo ri-promuove da zero (MIN_OBS_PROMOTE).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import engine.autopath as AP  # noqa: E402


def _iso_z(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _seed(tmp, monkeypatch, rows):
    monkeypatch.setattr(AP, "_db_path", lambda: Path(tmp) / "autopath.sqlite")
    AP._DB_INIT_DONE = False
    c = AP._conn()
    for rid, status, ts_created, ts_last_used in rows:
        c.execute(
            "INSERT INTO autopaths (id, intent_sig, intent_hash, cluster_id, "
            " framework_json, framework_hash, status, ts_created, ts_last_used) "
            "VALUES (?, 'read|messages', 'h', 'c', '{}', 'f', ?, ?, ?)",
            (rid, status, ts_created, ts_last_used))
    c.commit()
    c.close()


def test_prune_ages_demoted_and_stale_active(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, [
        ("demoted_old", "demoted", _iso_z(60), _iso_z(45)),   # oltre 30gg → via
        ("demoted_fresh", "demoted", _iso_z(10), _iso_z(5)),  # recente → resta
        ("active_stale", "active", _iso_z(200), _iso_z(120)),  # oltre 90gg → via
        ("active_live", "active", _iso_z(200), _iso_z(10)),   # usata → resta
        ("active_null_ts", "active", _iso_z(10), None),       # COALESCE→created
    ])
    report = AP.prune(keep_observations=5000)
    assert report["autopaths_demoted_removed"] == 1
    assert report["autopaths_stale_removed"] == 1
    c = AP._conn()
    left = {r[0] for r in c.execute("SELECT id FROM autopaths")}
    c.close()
    assert left == {"demoted_fresh", "active_live", "active_null_ts"}


def test_prune_aging_disabled_by_env(tmp_path, monkeypatch):
    monkeypatch.setenv("METNOS_AUTOPATH_STALE_DAYS", "0")
    monkeypatch.setenv("METNOS_AUTOPATH_DEMOTED_TTL_DAYS", "0")
    _seed(tmp_path, monkeypatch, [
        ("demoted_old", "demoted", _iso_z(60), _iso_z(45)),
        ("active_stale", "active", _iso_z(200), _iso_z(120)),
    ])
    report = AP.prune(keep_observations=5000)
    assert report["autopaths_demoted_removed"] == 0
    assert report["autopaths_stale_removed"] == 0


def _seed_obs(rows):
    """observations fittizie: (turn_id, verdict) — ihash/fhash fissi."""
    c = AP._conn()
    for turn_id, verdict in rows:
        c.execute(
            "INSERT INTO observations (turn_id, intent_hash, intent_sig, "
            " framework_json, framework_hash, verdict, ts) "
            "VALUES (?, 'h', 'read|messages', '{}', 'f', ?, ?)",
            (turn_id, verdict, _iso_z(0)))
    c.commit()
    c.close()


def test_prune_obs_window_keeps_verdict_rows(tmp_path, monkeypatch):
    """Review Fable 2/7: la finestra observations pota SOLO verdict=NULL —
    le righe votate (memoria di promote/demote) non vengono mai espulse,
    anche se piu' vecchie dell'intera finestra."""
    monkeypatch.setattr(AP, "_db_path", lambda: Path(tmp_path) / "autopath.sqlite")
    _seed_obs([("t_ok", "ok")] + [(f"t{i}", None) for i in range(10)])
    AP.prune(keep_observations=3)
    c = AP._conn()
    left = [r[0] for r in c.execute(
        "SELECT turn_id FROM observations ORDER BY rowid")]
    c.close()
    # la 'ok' (riga PIU' VECCHIA) sopravvive + le 3 NULL piu' recenti
    assert "t_ok" in left
    assert len(left) == 4


def test_lookup_touches_ts_last_used(tmp_path, monkeypatch):
    """Review Fable 2/7: il serve dalla cache rinfresca ts_last_used —
    prima era scritto SOLO su ✓-repromote e l'aging (stale <90gg) potava
    champion serviti attivamente ma mai ri-votati."""
    from engine.types import Intent
    intent = Intent(verb="read", object="messages")
    _, ihash = AP._compute_intent_sig(intent)
    old = _iso_z(80)
    monkeypatch.setattr(AP, "_db_path", lambda: Path(tmp_path) / "autopath.sqlite")
    c = AP._conn()
    c.execute(
        "INSERT INTO autopaths (id, intent_sig, intent_hash, cluster_id, "
        " framework_json, framework_hash, status, champion, ts_created, "
        " ts_last_used) "
        "VALUES ('ap1', 'read|messages|', ?, 'c', '{}', 'f', 'active', 1, ?, ?)",
        (ihash, old, old))
    c.commit()
    c.close()
    monkeypatch.setattr(AP._cluster, "embed", lambda q: None)
    hit = AP.lookup("leggi le mail", intent)
    assert hit is not None and hit.autopath_id == "ap1"
    c = AP._conn()
    ts = c.execute("SELECT ts_last_used FROM autopaths WHERE id='ap1'"
                   ).fetchone()[0]
    c.close()
    assert ts > old  # rinfrescato (ISO-Z lessicografico)
