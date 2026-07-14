"""P6 (ADR 0191 §7) — cooldown anti-lockout: formula, incremento selettivo,
reset, persistenza, concorrenza atomica."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sites_cooldown as cd


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("METNOS_SITES_COOLDOWN_DB",
                       str(tmp_path / "cooldown.sqlite"))
    # parametri di default deterministici
    for k in ("N", "BASE_S", "FACTOR", "CAP_S"):
        monkeypatch.delenv(f"METNOS_SITES_COOLDOWN_{k}", raising=False)


K = ("alice", "https://x.test:443", "fp123")


def test_formula_and_threshold(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    t = 1_000_000
    # 1° fallimento: sotto N=2 → nessun cooldown
    assert cd.record_failure(*K, "credentials_rejected", now=t) == t + 0
    assert cd.retry_after_s(*K, now=t) == 0
    # 2° → 900s
    assert cd.record_failure(*K, "credentials_rejected", now=t) == t + 900
    # 3° → 2700 (900*3)
    assert cd.record_failure(*K, "rate_limited", now=t) == t + 2700
    # 4° → 8100
    assert cd.record_failure(*K, "credentials_rejected", now=t) == t + 8100


def test_cap(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    t = 2_000_000
    until = 0
    for _ in range(8):
        until = cd.record_failure(*K, "credentials_rejected", now=t)
    assert until == t + 43200  # cap 12h


def test_only_eligible_reasons_increment(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    t = 3_000_000
    for reason in ("selector_missing", "empty_surface", "timeout",
                   "challenge_observed", "login_inconclusive"):
        assert cd.record_failure(*K, reason, now=t) == 0
    assert cd.retry_after_s(*K, now=t) == 0  # nessun incremento


def test_reset_on_success(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    t = 4_000_000
    cd.record_failure(*K, "credentials_rejected", now=t)
    cd.record_failure(*K, "credentials_rejected", now=t)
    assert cd.retry_after_s(*K, now=t) > 0
    cd.reset(*K)
    assert cd.retry_after_s(*K, now=t) == 0
    # dopo reset il conteggio riparte da zero (1° sotto soglia)
    assert cd.record_failure(*K, "credentials_rejected", now=t) == t + 0


def test_persistence_across_connections(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    t = 5_000_000
    cd.record_failure(*K, "credentials_rejected", now=t)
    cd.record_failure(*K, "credentials_rejected", now=t)
    # nuova "connessione" (il modulo riapre il DB a ogni chiamata) = restart-safe
    assert cd.retry_after_s(*K, now=t + 100) == 900 - 100


def test_ttl_idle_purge(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    t = 6_000_000
    cd.record_failure(*K, "credentials_rejected", now=t)
    cd.record_failure(*K, "credentials_rejected", now=t)
    # 25h dopo: record idle purgato → conteggio riparte
    later = t + 25 * 3600
    assert cd.active_until(*K, now=later) == 0
    assert cd.record_failure(*K, "credentials_rejected", now=later) == later + 0


def test_post_submit_outcome_five_way_mapping():
    from playwright_sidecar import credential_injection as ci
    # login_verified: stabile-positivo + navigazione confermata
    assert ci.post_submit_outcome(
        {"surface_checked": True, "stable_positive": True,
         "navigation_confirmed": True}, []) == "login_verified"
    # rate_limited: 429 osservato
    assert ci.post_submit_outcome({"http_status": 429}, []) == "rate_limited"
    # #5: il 429 (status) BATTE un falso stable_positive (contenuto): la
    # navigazione verso la pagina d'errore 429 non deve sembrare login riuscito.
    assert ci.post_submit_outcome(
        {"http_status": 429, "surface_checked": True, "stable_positive": True,
         "navigation_confirmed": True}, []) == "rate_limited"
    # challenge_observed: otp / captcha / push
    for marker in ("otp", "captcha", "push"):
        assert ci.post_submit_outcome({marker: True}, []) == "challenge_observed"
    # credentials_rejected: rifiuto esplicito
    assert ci.post_submit_outcome(
        {"password_rejected": True, "surface_checked": True},
        []) == "credentials_rejected"
    # login_inconclusive: nessun segnale conclusivo (remount/timeout)
    assert ci.post_submit_outcome({"surface_checked": True}, []) == "login_inconclusive"


def test_only_cooldown_outcomes_touch_the_store():
    from playwright_sidecar import credential_injection as ci
    assert ci.COOLDOWN_OUTCOMES == frozenset(
        {"credentials_rejected", "rate_limited"})
    # gli esiti neutri non sono nel set che alimenta il cooldown
    for neutral in ("login_verified", "challenge_observed", "login_inconclusive"):
        assert neutral not in ci.COOLDOWN_OUTCOMES


def test_concurrent_increments_are_atomic(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    import threading
    t = 7_000_000
    errors = []

    def worker():
        try:
            cd.record_failure(*K, "credentials_rejected", now=t)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(12)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert not errors
    # 12 fallimenti coerenti: fail_count esatto (nessuna race lost-update)
    conn = cd._connect()
    try:
        row = conn.execute(
            "SELECT fail_count FROM sites_cooldown WHERE owner=?", (K[0],)
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == 12
