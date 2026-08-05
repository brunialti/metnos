"""Test introvertiva — post-ritiro specialize+generalize (2/7/2026).

Unica op attiva: DEDUPE (mnest orfani/legacy). Il sync proietta SOLO
dedupe; le shape storiche (generalize/specialize) restano leggibili
dall'adapter ma nessun generatore le ri-emette. prune_old: R1/R2 con
reject umano mai potato.
"""
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest



@pytest.fixture
def tmp_corpus(monkeypatch):
    """AUDIT_DIR + MNESTOMA_DB_PATH su tmpdir + mnest fittizi."""
    tmp = Path(tempfile.mkdtemp(prefix="introvertiva_test_"))
    audit = tmp / "introvertiva"
    mnest_db = tmp / "mnest.sqlite"
    audit.mkdir()
    monkeypatch.setenv("MNESTOMA_DB_PATH", str(mnest_db))
    from mnestoma import Mnestoma
    m = Mnestoma()
    # transizione con executor ORFANO (fetch_urls rimosso dal catalog)
    m.record_passing("fetch_urls", "1.0", "write_files", "1.0",
                     turn_id="test_seed")
    # transizione sana (entrambi nel catalog reale)
    m.record_passing("find_files", "1.0", "sort_entries", "1.0",
                     turn_id="test_seed2")
    m.close()
    with mock.patch("introvertiva.AUDIT_DIR", audit):
        yield tmp
    shutil.rmtree(tmp)


def test_dedupe_finds_legacy_orphan(tmp_corpus):
    from introvertiva import candidates_dedupe
    cands = candidates_dedupe()
    orphans = [c for c in cands if c["kind"] == "legacy_orphan"]
    assert any(c["src_executor"] == "fetch_urls" for c in orphans)
    assert not any(c.get("src_executor") == "find_files" for c in orphans)


def test_run_all_is_dedupe_only(tmp_corpus):
    from introvertiva import run_all
    out = run_all(audit=False)
    assert "dedupe" in out
    assert "generalize" not in out and "specialize" not in out


def test_sync_projects_only_dedupe(tmp_corpus, monkeypatch):
    """Le shape storiche passate da un chiamante NON vengono proiettate:
    i generatori sono ritirati (2/7), il sync emette solo dedupe."""
    import proposals_state as ps
    db = tmp_corpus / "proposals_state.db"
    monkeypatch.setattr(ps, "DB_PATH", db)
    from introvertiva import sync_proposals_state
    out = {
        "dedupe": [{"kind": "legacy_orphan", "src_executor": "fetch_urls",
                     "dst_executor": "write_files", "uses": 7}],
        "generalize": [{"pattern": ["find_files", "sort_entries"], "uses": 5}],
        "specialize": [{"executor": "read_messages", "arg_name": "account",
                         "dominant_value": "\"x\"", "total_uses": 12}],
    }
    counts = sync_proposals_state(out)
    assert counts == {"dedupe": 1}
    assert ps.lookup(["dedupe", "legacy_orphan", "fetch_urls",
                       "write_files"]) is not None
    assert ps.lookup(["generalize", ["find_files", "sort_entries"]]) is None
    sync_proposals_state(out)
    row = ps.lookup(["dedupe", "legacy_orphan", "fetch_urls", "write_files"])
    assert row is not None and row.n_seen == 2


def test_prune_old_keeps_rejected(tmp_corpus, monkeypatch):
    """Review Fable 2/7: il reject umano mappa su state='dormant' — la
    potatura lo cancellava e la proposta risorgeva come pending fresca."""
    import sqlite3
    import proposals_state as ps
    db = tmp_corpus / "proposals_state.db"
    monkeypatch.setattr(ps, "DB_PATH", db)
    conn = ps._open()
    old = "2026-04-27T00:00:00Z"
    conn.execute(
        "INSERT INTO proposals_state "
        "(sig_key, kind, state, first_seen, last_seen, last_uses, n_seen, "
        " last_action) "
        "VALUES ('[\"specialize\", \"r\", \"x\", \"1\"]', 'specialize', "
        " 'dormant', ?, ?, 1, 1, 'reject')", (old, old))
    conn.execute(
        "INSERT INTO proposals_state "
        "(sig_key, kind, state, first_seen, last_seen, last_uses, n_seen) "
        "VALUES ('[\"specialize\", \"s\", \"x\", \"1\"]', 'specialize', "
        " 'dormant', ?, ?, 1, 1)", (old, old))
    conn.commit(); conn.close()
    report = ps.prune_old(days=1, refresh_days=1)
    assert report["removed_stale"] + report["removed_ttl"] >= 1
    conn = sqlite3.connect(str(db))
    left = {r[0] for r in conn.execute(
        "SELECT last_action FROM proposals_state")}
    conn.close()
    assert left == {"reject"}


def test_prune_old_removes_dead_evidence_keeps_decisions(tmp_corpus, monkeypatch):
    """prune_old: R1/R2 SOLO su pending/dormant; applied e blocked mai."""
    import sqlite3
    import proposals_state as ps
    db = tmp_corpus / "proposals_state.db"
    monkeypatch.setattr(ps, "DB_PATH", db)
    conn = ps._open()
    rows = [
        ("[\"specialize\", \"a\", \"x\", \"1\"]", "pending",
         "2026-04-27T00:00:00Z", "2026-04-27T00:00:00Z"),
        ("[\"specialize\", \"b\", \"x\", \"1\"]", "dormant",
         "2026-04-27T00:00:00Z", "2026-04-27T00:00:00Z"),
        ("[\"specialize\", \"c\", \"x\", \"1\"]", "applied",
         "2026-04-27T00:00:00Z", "2026-04-27T00:00:00Z"),
        ("[\"specialize\", \"d\", \"x\", \"1\"]", "blocked",
         "2026-04-27T00:00:00Z", "2026-04-27T00:00:00Z"),
    ]
    for sig, state, fs, ls in rows:
        conn.execute(
            "INSERT INTO proposals_state "
            "(sig_key, kind, state, first_seen, last_seen, last_uses, n_seen) "
            "VALUES (?, 'specialize', ?, ?, ?, 1, 1)", (sig, state, fs, ls))
    conn.execute(
        "INSERT INTO proposals_state (sig_key, kind, last_uses) "
        "VALUES ('[\"specialize\", \"e\", \"x\", \"1\"]', 'specialize', 1)")
    conn.commit(); conn.close()
    report = ps.prune_old(days=180, refresh_days=30)
    assert report["removed_stale"] == 2
    conn = sqlite3.connect(str(db))
    left = {r[0]: r[1] for r in conn.execute(
        "SELECT sig_key, state FROM proposals_state")}
    conn.close()
    assert set(left.values()) == {"applied", "blocked", "pending"}
    assert len(left) == 3


def test_diff_audit_returns_error_with_no_history(tmp_corpus):
    from introvertiva import diff_audit
    r = diff_audit("dedupe")
    assert "error" in r


def test_diff_audit_works_with_two_runs(tmp_corpus):
    import time
    from introvertiva import candidates_dedupe, _audit_write, diff_audit
    r1 = candidates_dedupe()
    _audit_write("candidates_dedupe", r1)
    time.sleep(1.1)  # ts filename a secondi: due file distinti
    _audit_write("candidates_dedupe", r1)
    d = diff_audit("dedupe")
    assert d.get("added") == [] and d.get("removed") == []
