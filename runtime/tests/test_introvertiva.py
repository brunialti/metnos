"""Smoke test introvertiva — corpus fittizio controllato.

Verifica che generalize identifichi un pattern atteso, che skip filtri
funzionino (skip default, skip X→X, skip smoke channel), che specialize
ignori il default match.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def tmp_corpus(monkeypatch):
    """Sostituisce TURNS_DIR, AUDIT_DIR, MNESTOMA_DB_PATH con tmpdir + popola
    turni fittizi + mnest fittizi per le transizioni testate."""
    tmp = Path(tempfile.mkdtemp(prefix="introvertiva_test_"))
    turns = tmp / "turns"
    audit = tmp / "introvertiva"
    mnest_db = tmp / "mnest.sqlite"
    turns.mkdir()
    audit.mkdir()
    monkeypatch.setenv("MNESTOMA_DB_PATH", str(mnest_db))
    # Il corpus fittizio ha date FISSE (aprile/maggio 2026): la finestra
    # rolling dei generatori (default 60gg) va disattivata qui, e testata
    # esplicitamente in test_window_excludes_old_turns.
    monkeypatch.setenv("METNOS_INTROVERTIVA_WINDOW_DAYS", "0")
    # Popola mnest fittizi per le transizioni che il test usa: serve perche'
    # candidates_generalize calcola avg_weight su mnest reali — senza,
    # weights=[] → catena scartata.
    from mnestoma import Mnestoma
    m = Mnestoma()
    for src, dst in [("find_files", "sort_entries"),
                       ("sort_entries", "describe_entries"),
                       ("find_dirs", "sort_entries")]:
        m.record_passing(src, "1.0", dst, "1.0", turn_id="test_seed")
    m.close()
    fixtures = [
        # 5 turni REALI (channel telegram) con catena identica find→sort→describe
        # → candidato generalize ATTESO
        *[{
            "ts_start": "2026-04-29T08:00:00Z",
            "user_query": f"trova file py grandi {i}",
            "channel": "telegram",
            "steps": [
                {"chosen_tool": "find_files", "raw_args": {"pattern": "*.py"}},
                {"chosen_tool": "sort_entries", "raw_args": {"by": "size", "desc": True}},
                {"chosen_tool": "describe_entries", "raw_args": {}},
            ],
        } for i in range(5)],
        # 1 turno smoke (channel test_uc) con stessa catena → DEVE essere skipped
        {
            "ts_start": "2026-04-29T09:00:00Z",
            "user_query": "smoke test",
            "channel": "test_uc",
            "steps": [
                {"chosen_tool": "find_files", "raw_args": {}},
                {"chosen_tool": "sort_entries", "raw_args": {}},
                {"chosen_tool": "describe_entries", "raw_args": {}},
            ],
        },
        # 3 turni REALI con catena RIDONDANTE sort→sort consecutive → diagnostic, no promote
        *[{
            "ts_start": "2026-04-30T10:00:00Z",
            "user_query": f"top dirs {i}",
            "channel": "telegram",
            "steps": [
                {"chosen_tool": "find_dirs", "raw_args": {}},
                {"chosen_tool": "sort_entries", "raw_args": {}},
                {"chosen_tool": "sort_entries", "raw_args": {}},
            ],
        } for i in range(3)],
        # 5 turni con sort_entries(desc=true): specialize candidate (default
        # del manifest e' desc=false → non scartato dal default-skip)
        *[{
            "ts_start": "2026-05-01T11:00:00Z",
            "user_query": f"top {i}",
            "channel": "telegram",
            "steps": [
                {"chosen_tool": "sort_entries", "raw_args": {"by": "size", "desc": True}},
            ],
        } for i in range(5)],
    ]
    fpath = turns / "2026-04-29.jsonl"
    with fpath.open("w") as f:
        for t in fixtures:
            f.write(json.dumps(t) + "\n")
    with mock.patch("introvertiva.TURNS_DIR", turns), \
         mock.patch("introvertiva.AUDIT_DIR", audit):
        yield tmp
    shutil.rmtree(tmp)


def test_generalize_finds_expected_pattern(tmp_corpus):
    from introvertiva import candidates_generalize
    cands = candidates_generalize(min_uses=3, min_chain_len=3,
                                    min_distinct_intents=2, min_avg_weight=0.0)
    promo = [c for c in cands if "_kind" not in c]
    assert promo, "generalize should find at least one promotion candidate"
    expected = ["find_files", "sort_entries", "describe_entries"]
    found = [c for c in promo if c["pattern"] == expected]
    assert found, f"expected pattern {expected} not in {[c['pattern'] for c in promo]}"
    assert found[0]["uses"] == 5, "should count exactly 5 real turns (smoke skipped)"


def test_generalize_skips_redundant_xtox(tmp_corpus):
    from introvertiva import candidates_generalize
    cands = candidates_generalize(min_uses=3, min_chain_len=3,
                                    min_distinct_intents=2, min_avg_weight=0.0)
    redundant = next((c for c in cands if c.get("_kind") == "diagnostic"), None)
    assert redundant is not None, "redundant patterns block missing"
    pats = redundant["redundant_patterns"]
    assert any(rp["pattern"] == ["find_dirs", "sort_entries", "sort_entries"]
                for rp in pats), \
        f"X→X pattern should be in diagnostic, got {pats}"


def test_generalize_skips_smoke_channel(tmp_corpus):
    from introvertiva import candidates_generalize
    cands = candidates_generalize(min_uses=3, min_chain_len=3,
                                    min_distinct_intents=1, min_avg_weight=0.0)
    promo = [c for c in cands if "_kind" not in c]
    expected = ["find_files", "sort_entries", "describe_entries"]
    found = [c for c in promo if c["pattern"] == expected]
    if found:
        # 5 telegram + 1 test_uc; se filter funziona, uses=5 non 6
        assert found[0]["uses"] == 5


def test_specialize_skips_default_value(tmp_corpus):
    from introvertiva import candidates_specialize
    # 4/5/2026 (ADR 0077): i candidati con valori booleani sono scartati
    # perche' lo slug `True`/`False` violerebbe il vocabolario chiuso
    # (qualifier non puo' essere `True`/`False`). Il test storico si
    # aspettava che sort_entries(desc=true) apparisse anche se valore !=
    # default; ora la regola del vocab vince. Verifichiamo la nuova
    # invariante: niente proposed_name che termini in `_True` o `_False`.
    cands = candidates_specialize(min_uses=3, min_arg_dominance=0.6)
    bad = [c for c in cands
           if c["proposed_name"].endswith(("_True", "_False"))]
    assert not bad, f"proposed_name with bool slug should be filtered: {bad}"


def test_specialize_skips_system_args(tmp_corpus, monkeypatch):
    """Gli arg di sistema `_`-prefixed (iniettati dal runtime a OGNI chiamata:
    _lang, _channel, _actor_email...) hanno dominance=1.0 per costruzione e
    NON sono scelte utente specializzabili. Bug 1/7: 14/20 candidati del run
    notturno erano rumore _lang/_channel/_actor_email."""
    turns_dir = tmp_corpus / "turns"
    rows = [{
        "ts_start": "2026-05-02T10:00:00Z",
        "user_query": f"leggi le mail di sistema {i}",
        "channel": "telegram",
        "steps": [
            {"chosen_tool": "read_messages",
             "raw_args": {"_lang": "it", "_actor_email": "x@example.com",
                            "account": "work"}},
        ],
    } for i in range(12)]
    with (turns_dir / "2026-05-02.jsonl").open("w") as f:
        for t in rows:
            f.write(json.dumps(t) + "\n")
    from introvertiva import candidates_specialize
    cands = candidates_specialize(min_uses=3, min_arg_dominance=0.6)
    system = [c for c in cands if c["arg_name"].startswith("_")]
    assert not system, f"system args devono essere skippati: {system}"
    # Il candidato legittimo sullo stesso tool sopravvive (il filtro non
    # spegne la specializzazione, toglie solo il rumore).
    legit = [c for c in cands if c["executor"] == "read_messages"
             and c["arg_name"] == "account"]
    assert legit, "il candidato non-sistema deve restare"


def test_specialize_skips_pii_values(tmp_corpus):
    """Review Fable 2/7 (§7.5): il proposed_name deriva dal VALORE dell'arg —
    un'email/path/token dominante finirebbe in audit/DB e /admin/changes.
    Il candidato PII-like si SCARTA (non si maschera)."""
    turns_dir = tmp_corpus / "turns"
    rows = [{
        "ts_start": "2026-05-03T10:00:00Z",
        "user_query": f"manda a ospite {i}",
        "channel": "telegram",
        "steps": [
            {"chosen_tool": "send_messages",
             "raw_args": {"to": "ospite@example.com"}},
            {"chosen_tool": "read_files",
             "raw_args": {"base_path": "/home/ospite/documenti"}},
        ],
    } for i in range(12)]
    with (turns_dir / "2026-05-03.jsonl").open("w") as f:
        for t in rows:
            f.write(json.dumps(t) + "\n")
    from introvertiva import candidates_specialize
    cands = candidates_specialize(min_uses=3, min_arg_dominance=0.6)
    pii = [c for c in cands
           if "@" in c["dominant_value"] or "example_com" in c["proposed_name"]
           or "home_ospite" in c["proposed_name"]]
    assert not pii, f"candidati PII-like devono essere scartati: {pii}"


def test_pii_like_predicate():
    from introvertiva import _pii_like
    assert _pii_like("ospite@example.com")
    assert _pii_like("/home/ospite/foto")
    assert _pii_like("~/documenti")
    assert _pii_like("C:\\Users\\ospite")
    assert _pii_like("ghp_a1B2c3D4e5F6g7H8i9J0k1L2m3N4")   # token-like
    assert not _pii_like("INBOX")
    assert not _pii_like("work")
    assert not _pii_like("posta indesiderata")
    assert not _pii_like(12)


def test_window_filters_float_ts_start(tmp_corpus):
    """Integration del fix 2ccda51: i turni REALI portano ts_start come float
    epoch (11563/11668 in prod) — il confine after_iso deve filtrare su epoch
    per entrambi i formati, escludendo i ts_start non validi."""
    from datetime import datetime, timezone
    turns_dir = tmp_corpus / "turns"
    cutoff_iso = "2026-06-01T00:00:00Z"
    old_ep = datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp()
    new_ep = datetime(2026, 6, 15, tzinfo=timezone.utc).timestamp()
    rows = [
        {"ts_start": old_ep, "user_query": "vecchio float", "channel": "telegram", "steps": []},
        {"ts_start": new_ep, "user_query": "nuovo float", "channel": "telegram", "steps": []},
        {"ts_start": "2026-06-20T10:00:00Z", "user_query": "nuovo iso", "channel": "telegram", "steps": []},
        {"ts_start": "non-una-data", "user_query": "rotto", "channel": "telegram", "steps": []},
    ]
    with (turns_dir / "2026-06-15.jsonl").open("w") as f:
        for t in rows:
            f.write(json.dumps(t) + "\n")
    from introvertiva import _load_turns
    got = {t["user_query"] for t in _load_turns(after_iso=cutoff_iso)}
    assert "nuovo float" in got and "nuovo iso" in got
    assert "vecchio float" not in got and "rotto" not in got


def test_prune_old_keeps_rejected(tmp_corpus, monkeypatch):
    """Review Fable 2/7: il reject umano mappa su state='dormant' — la
    potatura lo cancellava e il generatore notturno ri-emetteva la proposta
    come pending FRESCA (memoria della decisione persa)."""
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
    assert left == {"reject"}  # il reject sopravvive a R1 E R2


def test_window_excludes_old_turns(tmp_corpus, monkeypatch):
    """Finestra rolling: con METNOS_INTROVERTIVA_WINDOW_DAYS attivo i turni
    piu' vecchi del cutoff (il corpus fittizio e' di aprile/maggio 2026)
    escono dai contatori dei generatori."""
    monkeypatch.setenv("METNOS_INTROVERTIVA_WINDOW_DAYS", "1")
    from introvertiva import candidates_generalize, candidates_specialize
    assert candidates_generalize(min_uses=1, min_chain_len=3,
                                   min_distinct_intents=1,
                                   min_avg_weight=0.0) == []
    assert candidates_specialize(min_uses=1, min_arg_dominance=0.0) == []


def test_sync_proposals_state_roundtrip(tmp_corpus, monkeypatch):
    """sync_proposals_state proietta i candidati nel DB (touch_or_insert):
    sig_key canonici compatibili con lo storico/adapter, skip diagnostici."""
    import proposals_state as ps
    db = tmp_corpus / "proposals_state.db"
    monkeypatch.setattr(ps, "DB_PATH", db)
    from introvertiva import sync_proposals_state
    out = {
        "dedupe": [{"kind": "legacy_orphan", "src_executor": "fetch_urls",
                     "dst_executor": "write_files", "uses": 7}],
        "generalize": [
            {"pattern": ["find_files", "sort_entries", "describe_entries"],
             "uses": 5},
            {"_kind": "diagnostic", "note": "skip me"},
        ],
        "specialize": [{"executor": "read_messages", "arg_name": "account",
                         "dominant_value": "\"metnos_system\"",
                         "total_uses": 12}],
    }
    counts = sync_proposals_state(out)
    assert counts == {"dedupe": 1, "generalize": 1, "specialize": 1}
    row = ps.lookup(["specialize", "read_messages", "account",
                      "\"metnos_system\""])
    assert row is not None and row.state == "pending" and row.last_uses == 12
    # Shape identica allo storico del DB (contratto adapter change_intent).
    row = ps.lookup(["dedupe", "legacy_orphan", "fetch_urls", "write_files"])
    assert row is not None
    # Secondo run: touch, n_seen avanza (lifecycle vivo).
    sync_proposals_state(out)
    row = ps.lookup(["generalize",
                      ["find_files", "sort_entries", "describe_entries"]])
    assert row is not None and row.n_seen == 2


def test_prune_old_removes_dead_evidence_keeps_decisions(tmp_corpus, monkeypatch):
    """prune_old: R1 evidenza morta (last_seen oltre refresh_days) e R2 TTL
    (first_seen oltre days) SOLO su pending/dormant; applied e blocked
    (anti-resurrezione) mai toccate."""
    import sqlite3
    import proposals_state as ps
    db = tmp_corpus / "proposals_state.db"
    monkeypatch.setattr(ps, "DB_PATH", db)
    conn = ps._open()
    rows = [
        # (sig_key, state, first_seen, last_seen) — vecchie di ~65gg
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
    # una riga pending FRESCA (ri-toccata dal sync) → deve sopravvivere
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


def test_validator_rejects_uppercase_qualifier():
    from introvertiva import _is_valid_proposed_name
    assert _is_valid_proposed_name("move_messages_Posta_indesiderata") is False
    assert _is_valid_proposed_name("move_messages_posta_indesiderata") is True
    assert _is_valid_proposed_name("get_files_dates_semantic") is True
    assert _is_valid_proposed_name("move_messages_True") is False


def test_diff_audit_returns_error_with_no_history(tmp_corpus):
    from introvertiva import diff_audit
    r = diff_audit("generalize")
    assert "error" in r


def test_diff_audit_works_with_two_runs(tmp_corpus):
    from introvertiva import candidates_generalize, _audit_write, diff_audit
    # Run 1: snapshot iniziale
    cands1 = candidates_generalize(min_uses=3, min_chain_len=3, min_distinct_intents=2,
                                    min_avg_weight=0.0)
    _audit_write("candidates_generalize", cands1)
    # Run 2: stesso corpus (idempotente, persisted=tutti)
    import time; time.sleep(1.1)  # garantisce ts diverso
    cands2 = candidates_generalize(min_uses=3, min_chain_len=3, min_distinct_intents=2,
                                    min_avg_weight=0.0)
    _audit_write("candidates_generalize", cands2)
    diff = diff_audit("generalize")
    assert "error" not in diff
    assert diff["n_added"] == 0
    assert diff["n_removed"] == 0
    assert diff["n_persisted"] >= 1
