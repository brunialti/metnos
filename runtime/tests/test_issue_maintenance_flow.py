"""test_issue_maintenance_flow.py — flusso maintenance issue GitHub (#45).

Copre i mattoni LOCALI del flusso «Metnos maintains Metnos»
(internal/reports/github_maintenance_flow.html): store `issue_qa` +
executor `write_issues` / `read_issues` / `find_issues`.

Verifica STATICA della macchina a stati new→prepared→approved→posted:
- upsert parziale + idempotenza per status (doppio `posted` non duplica);
- promozione bozza→accettata su status='approved' senza accepted_reply;
- posted_at fissato la prima volta (idempotente);
- alias `issue_number` e default top-level in write_issues (§2.10:
  read_issues → write_issues pipabile via from_step);
- dedup semantico find_similar con embedding sintetici (niente BGE-M3).

Determinismo §7.9: niente LLM, niente rete, db sqlite su tmp_path.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import numpy as np
import pytest

_RUNTIME = Path(__file__).resolve().parent.parent
_ROOT = _RUNTIME.parent
sys.path.insert(0, str(_RUNTIME))

import github_issue_qa_store as store  # noqa: E402

REPO = "owner/name"


def _load_executor(name: str):
    """Importa l'executor `<name>.py` da executors/<name>/ come modulo."""
    path = _ROOT / "executors" / name / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_test_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _unit(i: int) -> np.ndarray:
    """Embedding sintetico L2-normalized: one-hot sull'asse i."""
    v = np.zeros(store.EMBEDDING_DIM, dtype=np.float32)
    v[i] = 1.0
    return v


@pytest.fixture()
def tmp_store(tmp_path, monkeypatch):
    """Punta lo store a un db temporaneo (hermetic)."""
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "issue_qa.sqlite")
    return store


@pytest.fixture()
def fake_embedder(monkeypatch):
    """jobs.github_dedup.embed_query deterministico (no BGE-M3)."""
    mod = types.ModuleType("jobs.github_dedup")
    mod.embed_query = lambda text: _unit(len(text) % store.EMBEDDING_DIM)
    monkeypatch.setitem(sys.modules, "jobs.github_dedup", mod)
    return mod


# ── Store: macchina a stati ───────────────────────────────────────────────

def test_status_machine_new_to_posted(tmp_store):
    s = tmp_store
    # new (default implicito alla creazione)
    rid = s.upsert_treatment(REPO, 1, title="t1")
    assert rid > 0
    rec = s.list_records(repo=REPO, numbers=[1])[0]
    assert rec["status"] == "new" and rec["posted_at"] is None

    # prepared con bozza
    s.upsert_treatment(REPO, 1, status="prepared", draft_reply="bozza A",
                       classification="question")
    rec = s.list_records(repo=REPO, numbers=[1])[0]
    assert rec["status"] == "prepared"
    assert rec["draft_reply"] == "bozza A"
    assert rec["accepted_reply"] is None  # nessuna promozione anticipata

    # approved SENZA accepted_reply → promozione bozza→accettata
    s.upsert_treatment(REPO, 1, status="approved")
    rec = s.list_records(repo=REPO, numbers=[1])[0]
    assert rec["status"] == "approved"
    assert rec["accepted_reply"] == "bozza A"

    # posted → posted_at fissato
    s.upsert_treatment(REPO, 1, status="posted")
    rec = s.list_records(repo=REPO, numbers=[1])[0]
    assert rec["status"] == "posted"
    first_ts = rec["posted_at"]
    assert isinstance(first_ts, int) and first_ts > 0

    # idempotenza: ri-marcare posted NON duplica ne' cambia il timestamp
    s.upsert_treatment(REPO, 1, status="posted")
    rows = s.list_records(repo=REPO, numbers=[1])
    assert len(rows) == 1
    assert rows[0]["posted_at"] == first_ts


def test_approved_with_explicit_reply_wins(tmp_store):
    s = tmp_store
    s.upsert_treatment(REPO, 2, status="prepared", draft_reply="bozza B")
    # L'admin EDITA la bozza: accepted_reply esplicita vince sulla promozione.
    s.upsert_treatment(REPO, 2, status="approved",
                       accepted_reply="testo corretto dall'admin")
    rec = s.list_records(repo=REPO, numbers=[2])[0]
    assert rec["accepted_reply"] == "testo corretto dall'admin"
    assert rec["draft_reply"] == "bozza B"  # preservata (upsert parziale)


def test_partial_upsert_preserves_fields(tmp_store):
    s = tmp_store
    s.upsert_treatment(REPO, 3, title="titolo", classification="bug",
                       status="prepared", draft_reply="d")
    s.upsert_treatment(REPO, 3, status="approved")  # solo status
    rec = s.list_records(repo=REPO, numbers=[3])[0]
    assert rec["title"] == "titolo"
    assert rec["classification"] == "bug"


def test_list_records_filters(tmp_store):
    s = tmp_store
    s.upsert_treatment(REPO, 10, status="prepared")
    s.upsert_treatment(REPO, 11, status="approved")
    s.upsert_treatment("other/repo", 10, status="approved")
    approved = s.list_records(repo=REPO, status="approved")
    assert [r["issue_number"] for r in approved] == [11]
    multi = s.list_records(repo=REPO, status=["prepared", "approved"])
    assert len(multi) == 2
    assert all(r["repo"] == REPO for r in multi)


def test_find_similar_ranking(tmp_store):
    s = tmp_store
    s.upsert_treatment(REPO, 20, status="posted", accepted_reply="risposta X",
                       question_text="q20", embedding=_unit(5))
    s.upsert_treatment(REPO, 21, status="posted", accepted_reply="risposta Y",
                       question_text="q21", embedding=_unit(7))
    out = s.find_similar(REPO, _unit(7), top_n=2)
    assert out[0]["issue_number"] == 21
    assert out[0]["similarity"] == pytest.approx(1.0)
    assert out[0]["accepted_reply"] == "risposta Y"
    assert out[1]["similarity"] == pytest.approx(0.0)


# ── Executor: write_issues ────────────────────────────────────────────────

def test_write_issues_top_level_defaults_and_alias(tmp_store, fake_embedder):
    """Pipe read_issues→write_issues: entries con `issue_number` (output di
    read_issues) + status top-level. E' la forma del Comando B (fase 8)."""
    w = _load_executor("write_issues")
    entries = [
        {"repo": REPO, "issue_number": 30, "title": "a"},
        {"issue_number": 31, "title": "b"},  # repo dal default top-level
    ]
    out = w.invoke({"entries": entries, "repo": REPO, "status": "posted"})
    assert out["ok"] is True and out["ok_count"] == 2
    recs = tmp_store.list_records(repo=REPO, status="posted")
    assert sorted(r["issue_number"] for r in recs) == [30, 31]
    assert all(r["posted_at"] for r in recs)


def test_write_issues_entry_status_wins_over_top_level(tmp_store, fake_embedder):
    w = _load_executor("write_issues")
    out = w.invoke({
        "entries": [{"repo": REPO, "number": 40, "status": "prepared"}],
        "status": "posted",
    })
    assert out["ok"] is True
    assert tmp_store.list_records(repo=REPO, numbers=[40])[0]["status"] == "prepared"


def test_write_issues_persists_question_text_and_embedding(tmp_store, fake_embedder):
    w = _load_executor("write_issues")
    q = "install fails on step 3"
    out = w.invoke({"entries": [
        {"repo": REPO, "number": 41, "question_text": q, "status": "posted",
         "accepted_reply": "use --check first"},
    ]})
    assert out["ok"] is True
    # find_similar trova il record con lo stesso embedding sintetico
    hits = tmp_store.find_similar(REPO, _unit(len(q) % store.EMBEDDING_DIM))
    assert hits and hits[0]["issue_number"] == 41
    assert hits[0]["similarity"] == pytest.approx(1.0)


def test_write_issues_invalid_status_rejected(tmp_store, fake_embedder):
    w = _load_executor("write_issues")
    out = w.invoke({"entries": [{"repo": REPO, "number": 50, "status": "bogus"}]})
    assert out["ok"] is False and out["fail_count"] == 1
    assert tmp_store.list_records(repo=REPO, numbers=[50]) == []


# ── write_issues: dedup / notify-once §2.8 (12/6/2026) ────────────────────

def test_write_issues_skip_known_same_status(tmp_store, fake_embedder):
    """Ri-registrazione allo stesso status = no-op: ok_count=0,
    skipped_known=1, bozza ORIGINALE preservata (niente churn LLM).
    E' il caso del run schedulato ogni 30m sulla stessa issue aperta."""
    w = _load_executor("write_issues")
    first = w.invoke({"entries": [
        {"repo": REPO, "number": 80, "status": "prepared",
         "draft_reply": "bozza originale"}]})
    assert first["ok_count"] == 1 and first["created_count"] == 1
    assert first["results"][0]["created"] is True
    # Re-run identico (bozza diversa: il classify LLM non e' deterministico)
    rerun = w.invoke({"entries": [
        {"repo": REPO, "number": 80, "status": "prepared",
         "draft_reply": "bozza DIVERSA del re-run"}]})
    assert rerun["ok"] is True
    assert rerun["ok_count"] == 0 and rerun["created_count"] == 0
    assert rerun["skipped_known"] == 1
    assert rerun["skipped"][0]["reason"] == "already_treated"
    rec = tmp_store.list_records(repo=REPO, numbers=[80])[0]
    assert rec["draft_reply"] == "bozza originale"


def test_write_issues_status_advance_not_skipped(tmp_store, fake_embedder):
    """prepared → approved AVANZA la macchina a stati → scritto."""
    w = _load_executor("write_issues")
    w.invoke({"entries": [{"repo": REPO, "number": 81, "status": "prepared",
                           "draft_reply": "bozza"}]})
    out = w.invoke({"entries": [{"repo": REPO, "number": 81,
                                 "status": "approved"}]})
    assert out["ok_count"] == 1 and out["skipped_known"] == 0
    assert out["results"][0]["created"] is False  # update, non create
    rec = tmp_store.list_records(repo=REPO, numbers=[81])[0]
    assert rec["status"] == "approved"
    assert rec["accepted_reply"] == "bozza"  # promozione bozza→accettata


def test_write_issues_overwrite_forces_rewrite(tmp_store, fake_embedder):
    """overwrite=true forza la ri-scrittura allo stesso status."""
    w = _load_executor("write_issues")
    w.invoke({"entries": [{"repo": REPO, "number": 82, "status": "prepared",
                           "draft_reply": "v1"}]})
    out = w.invoke({"entries": [{"repo": REPO, "number": 82,
                                 "status": "prepared", "draft_reply": "v2"}],
                    "overwrite": True})
    assert out["ok_count"] == 1 and out["skipped_known"] == 0
    rec = tmp_store.list_records(repo=REPO, numbers=[82])[0]
    assert rec["draft_reply"] == "v2"


def test_write_issues_double_posted_skipped(tmp_store, fake_embedder):
    """Doppio 'posted' = skip → posted_at del primo post intoccato."""
    w = _load_executor("write_issues")
    w.invoke({"entries": [{"repo": REPO, "number": 83, "status": "posted"}]})
    first_ts = tmp_store.list_records(repo=REPO, numbers=[83])[0]["posted_at"]
    out = w.invoke({"entries": [{"repo": REPO, "number": 83,
                                 "status": "posted"}]})
    assert out["skipped_known"] == 1 and out["ok_count"] == 0
    assert tmp_store.list_records(repo=REPO, numbers=[83])[0]["posted_at"] == first_ts


# ── Executor: read_issues ─────────────────────────────────────────────────

def test_read_issues_by_status(tmp_store):
    tmp_store.upsert_treatment(REPO, 60, status="approved", draft_reply="d",
                               accepted_reply="ok!")
    tmp_store.upsert_treatment(REPO, 61, status="prepared")
    r = _load_executor("read_issues")
    out = r.invoke({"repo": REPO, "status": "approved"})
    assert out["ok"] is True and out["ok_count"] == 1
    e = out["entries"][0]
    assert e["issue_number"] == 60 and e["accepted_reply"] == "ok!"


# ── Executor: find_issues ─────────────────────────────────────────────────

def test_find_issues_dedup_with_threshold(tmp_store, fake_embedder):
    q = "windows install error"
    tmp_store.upsert_treatment(
        REPO, 70, status="posted", accepted_reply="known fix",
        embedding=_unit(len(q) % store.EMBEDDING_DIM))
    tmp_store.upsert_treatment(REPO, 71, status="posted",
                               accepted_reply="other", embedding=_unit(3))
    f = _load_executor("find_issues")
    out = f.invoke({"repo": REPO, "query_text": q, "min_similarity": 0.85})
    assert out["ok"] is True and out["embedder_available"] is True
    assert out["ok_count"] == 1
    assert out["entries"][0]["ref"] == f"{REPO}#70"
    assert out["entries"][0]["accepted_reply"] == "known fix"


def test_find_issues_honest_degrade_without_embedder(tmp_store, monkeypatch):
    """§2.8: embedder giu' → embedder_available=false, NON un falso '0 simili'."""
    mod = types.ModuleType("jobs.github_dedup")
    mod.embed_query = lambda text: None
    monkeypatch.setitem(sys.modules, "jobs.github_dedup", mod)
    f = _load_executor("find_issues")
    out = f.invoke({"repo": REPO, "query_text": "anything"})
    assert out["ok"] is True
    assert out["embedder_available"] is False
    assert out["entries"] == []
