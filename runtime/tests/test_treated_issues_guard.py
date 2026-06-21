"""test_treated_issues_guard.py — guard anti-costo run schedulati (12/6/2026).

Bug live (Roberto): nel flusso di manutenzione github (`run_user_query`
schedulato) un'issue GIÀ trattata (presente in `issue_qa`) e ancora aperta
ri-entrava ogni ora in classify_entries + step frontier (Anthropic, a
pagamento) PRIMA che il dedup a valle la scartasse. Il guard
`runtime/treated_issues_guard.py` filtra A MONTE, al
confine d'invocazione dei builtin LLM-augmented, SOLO nei turni schedulati.

Proprietà verificate (deterministiche §7.9, niente LLM/rete reale):
1. fuori dallo scope schedulato il guard è un no-op (query interattive
   intoccate);
2. nello scope schedulato le issue con status trattato (prepared/approved/
   posted) vengono droppate; status='new' e entry non-issue passano;
3. identity robusta: campi store locali (repo+issue_number) E shape
   `find_issues_github` (kind=github_issue + number + html_url);
4. fail-open §2.8: store rotto → entries intoccate;
5. END-TO-END simulato del costo: issue NUOVA → classify chiama il LLM
   1 volta; stessa issue GIÀ in issue_qa → 0 chiamate LLM (frontier
   compreso: il contatore copre OGNI tier) + annotazione onesta
   skipped_known sul risultato; idem describe_entries (step bozza).
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))

import store as _store  # noqa: E402
from store_bootstrap import _ISSUE_QA  # noqa: E402
import treated_issues_guard as guard  # noqa: E402

REPO = "owner/name"


@pytest.fixture()
def tmp_store(tmp_path):
    """Store GENERICO su db temp (unificazione C2 21/6: github_issue_qa_store
    ritirato). Mini-adapter `upsert_treatment` per non riscrivere i call-site:
    scrive via Store.write (upsert su (repo, issue_number))."""
    st = _store.register(_ISSUE_QA, name="github_issue_qa",
                         path=tmp_path / "issue_qa.sqlite")

    class _Adapter:
        def upsert_treatment(self, repo, issue_number, **fields):
            rec = {"repo": repo, "issue_number": int(issue_number)}
            rec.update({k: v for k, v in fields.items() if v is not None})
            st.write(rec, key=["repo", "issue_number"])

    yield _Adapter()
    _store.unregister("github_issue_qa")


@pytest.fixture()
def fake_embedder(monkeypatch):
    mod = types.ModuleType("jobs.github_dedup")
    mod.embed_query = lambda text: None
    monkeypatch.setitem(sys.modules, "jobs.github_dedup", mod)
    return mod


def _gh_entry(n: int, repo: str = REPO) -> dict:
    """Shape REALE di find_issues_github (vedi turn log 7/6/2026)."""
    return {"kind": "github_issue", "id": 1000 + n, "number": n,
            "title": f"issue {n}", "state": "open",
            "html_url": f"https://github.com/{repo}/issues/{n}",
            "body_preview": "..."}


def _store_entry(n: int, repo: str = REPO) -> dict:
    """Shape di un record letto dallo store locale via find_entries."""
    return {"repo": repo, "issue_number": n, "title": f"issue {n}",
            "status": "prepared"}


# ── 1+2. Scope + drop semantics ───────────────────────────────────────────

class TestFilterSemantics:
    def test_noop_outside_scheduled_scope(self, tmp_store):
        tmp_store.upsert_treatment(REPO, 1, status="prepared")
        args = {"entries": [_gh_entry(1)], "dimension": "relevance"}
        out, info = guard.filter_treated_issue_entries("classify_entries", args)
        assert info is None and out is args
        assert len(out["entries"]) == 1

    def test_noop_for_non_costly_tool(self, tmp_store):
        tmp_store.upsert_treatment(REPO, 1, status="prepared")
        args = {"entries": [_gh_entry(1)]}
        with guard.scheduled_turn_scope():
            out, info = guard.filter_treated_issue_entries("filter_entries", args)
        assert info is None and len(out["entries"]) == 1

    def test_drops_treated_keeps_new_and_generic(self, tmp_store):
        tmp_store.upsert_treatment(REPO, 1, status="prepared")
        mail = {"from": "a@b.c", "subject": "hi"}  # entry non-issue
        args = {"entries": [_gh_entry(1), _gh_entry(2), mail]}
        with guard.scheduled_turn_scope():
            out, info = guard.filter_treated_issue_entries("classify_entries", args)
        assert info is not None
        assert info["skipped_known"] == 1
        assert info["skipped_known_refs"] == [f"{REPO}#1"]
        kept_nums = [e.get("number") for e in out["entries"]]
        assert kept_nums == [2, None]  # nuova + mail passano, nota droppata

    def test_status_new_not_dropped(self, tmp_store):
        """Contratto flusso: frontier giù → resta 'new' → va ritrattata."""
        tmp_store.upsert_treatment(REPO, 3, status="new")
        args = {"entries": [_gh_entry(3)]}
        with guard.scheduled_turn_scope():
            out, info = guard.filter_treated_issue_entries("classify_entries", args)
        assert info is None and len(out["entries"]) == 1

    def test_local_store_shape_dropped(self, tmp_store):
        """Entries dello store locale (repo+issue_number) — stesso guard."""
        tmp_store.upsert_treatment(REPO, 4, status="posted")
        args = {"entries": [_store_entry(4)]}
        with guard.scheduled_turn_scope():
            out, info = guard.filter_treated_issue_entries("describe_entries", args)
        assert info is not None and info["skipped_known"] == 1
        assert out["entries"] == []

    def test_identity_from_html_url_only(self, tmp_store):
        tmp_store.upsert_treatment(REPO, 5, status="approved")
        e = {"kind": "github_issue", "title": "t",
             "html_url": f"https://github.com/{REPO}/issues/5"}
        with guard.scheduled_turn_scope():
            out, info = guard.filter_treated_issue_entries(
                "extract_entries", {"entries": [e]})
        assert info is not None and info["skipped_known_refs"] == [f"{REPO}#5"]
        assert out["entries"] == []

    def test_other_repo_not_dropped(self, tmp_store):
        tmp_store.upsert_treatment("other/repo", 6, status="posted")
        args = {"entries": [_gh_entry(6)]}  # stesso numero, repo diverso
        with guard.scheduled_turn_scope():
            out, info = guard.filter_treated_issue_entries("classify_entries", args)
        assert info is None and len(out["entries"]) == 1

    def test_fail_open_on_store_error(self, tmp_store, monkeypatch):
        tmp_store.upsert_treatment(REPO, 7, status="prepared")
        def _boom(*a, **kw):
            raise RuntimeError("db corrotto")
        # Store generico erra in lettura → la guard deve fail-open (§2.8).
        monkeypatch.setattr(_store.get_store("github_issue_qa"), "find", _boom)
        args = {"entries": [_gh_entry(7)]}
        with guard.scheduled_turn_scope():
            out, info = guard.filter_treated_issue_entries("classify_entries", args)
        assert info is None and len(out["entries"]) == 1  # §2.8 fail-open

    def test_scope_resets_after_exit(self, tmp_store):
        with guard.scheduled_turn_scope():
            assert guard.is_scheduled_turn() is True
        assert guard.is_scheduled_turn() is False


# ── annotate ──────────────────────────────────────────────────────────────

def test_annotate_skipped_known_merges():
    info = {"skipped_known": 2, "skipped_known_refs": ["o/n#1", "o/n#2"],
            "note": "guard note"}
    res = {"ok": True, "entries": [], "note": "prior"}
    out = guard.annotate_skipped_known(res, info)
    assert out["skipped_known"] == 2
    assert out["skipped_known_refs"] == ["o/n#1", "o/n#2"]
    assert out["note"] == "prior | guard note"
    # info None → no-op
    res2 = {"ok": True}
    assert guard.annotate_skipped_known(res2, None) == {"ok": True}


# ── 5. END-TO-END simulato: costo LLM/frontier ───────────────────────────

class _LLMCounter:
    """Stub di llm_helpers.call_llm: conta OGNI chiamata per tier.
    Nessuna rete, nessun costo. Risposta valida per classify (array JSON)."""
    def __init__(self):
        self.calls = []

    def __call__(self, items, prompt, *, tier="auto", **kw):
        self.calls.append(tier)
        n = len(items) if isinstance(items, list) else 1
        labels = json.dumps(["high"] * n)
        return labels, {"in_tokens": 1, "out_tokens": 1, "latency_ms": 1,
                        "model": "stub"}

    @property
    def total(self):
        return len(self.calls)

    @property
    def frontier(self):
        return sum(1 for t in self.calls if t == "frontier")


@pytest.fixture()
def llm_counter(monkeypatch):
    import classify_entries as ce
    import describe_entries as de
    counter = _LLMCounter()
    monkeypatch.setattr(ce, "call_llm", counter)
    monkeypatch.setattr(de, "call_llm", counter)
    return counter


class TestEndToEndCost:
    """Simula i due run schedulati consecutivi del flusso manutenzione.
    Passa dal choke-point REALE (`agent_runtime._invoke_builtin_handler`,
    lo stesso usato da engine v2 / piani serviti). 0 push, 0 costo reale."""

    def test_new_issue_classified_once_then_known_zero_llm(
            self, tmp_store, fake_embedder, llm_counter):
        from agent_runtime import _invoke_builtin_handler
        entry = _gh_entry(101)

        # ── RUN 1: issue NUOVA → il ciclo costoso gira (1 volta) ──────
        with guard.scheduled_turn_scope():
            obs1 = _invoke_builtin_handler(
                "classify_entries",
                {"entries": [entry], "dimension": "relevance",
                 "classes": ["high", "medium", "low"]})
        assert obs1["ok"] is True
        assert len(obs1["entries"]) == 1
        assert obs1["entries"][0]["relevance"] == "high"
        assert llm_counter.total == 1  # classify pagato UNA volta
        # persisti il trattamento (come fa write_entries nel run reale: il
        # flusso universale scrive lo stato nello store github_issue_qa).
        tmp_store.upsert_treatment(REPO, 101, status="prepared",
                                   draft_reply="bozza")

        # ── RUN 2: stessa issue ancora aperta → 0 chiamate LLM ────────
        with guard.scheduled_turn_scope():
            obs2 = _invoke_builtin_handler(
                "classify_entries",
                {"entries": [entry], "dimension": "relevance",
                 "classes": ["high", "medium", "low"]})
        assert obs2["ok"] is True
        assert obs2["entries"] == []          # niente da ri-classificare
        assert obs2["skipped_known"] == 1     # onestà §2.7/§2.8
        assert obs2["skipped_known_refs"] == [f"{REPO}#101"]
        assert llm_counter.total == 1         # NESSUNA chiamata in più
        assert llm_counter.frontier == 0      # frontier mai toccato

        # ── RUN 2, step bozza (describe = dove gira il frontier) ──────
        with guard.scheduled_turn_scope():
            obs3 = _invoke_builtin_handler(
                "describe_entries",
                {"entries": [entry], "style": "by_relevance",
                 "context": "bozza di risposta"})
        assert obs3["ok"] is True
        assert obs3.get("item_count") == 0    # short-circuit su lista vuota
        assert obs3["skipped_known"] == 1
        assert llm_counter.total == 1         # describe NON ha pagato nulla

    def test_interactive_turn_unaffected(self, tmp_store, fake_embedder,
                                          llm_counter):
        """Query interattiva (nessuno scope schedulato): issue nota viene
        comunque classificata — il guard non altera la semantica utente."""
        from agent_runtime import _invoke_builtin_handler
        tmp_store.upsert_treatment(REPO, 102, status="prepared")
        obs = _invoke_builtin_handler(
            "classify_entries",
            {"entries": [_gh_entry(102)], "dimension": "relevance",
             "classes": ["high", "medium", "low"]})
        assert obs["ok"] is True
        assert len(obs["entries"]) == 1
        assert "skipped_known" not in obs
        assert llm_counter.total == 1

    def test_scheduled_mixed_new_and_known(self, tmp_store, fake_embedder,
                                            llm_counter):
        """1 nota + 1 nuova: solo la nuova paga il LLM, entrambe onestamente
        contate (skipped_known=1, 1 entry classificata)."""
        from agent_runtime import _invoke_builtin_handler
        tmp_store.upsert_treatment(REPO, 103, status="posted")
        with guard.scheduled_turn_scope():
            obs = _invoke_builtin_handler(
                "classify_entries",
                {"entries": [_gh_entry(103), _gh_entry(104)],
                 "dimension": "relevance",
                 "classes": ["high", "medium", "low"]})
        assert obs["ok"] is True
        assert [e["number"] for e in obs["entries"]] == [104]
        assert obs["skipped_known"] == 1
        assert llm_counter.total == 1
