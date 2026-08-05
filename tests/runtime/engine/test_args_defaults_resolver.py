"""Test del sottosistema scope-arg: args_defaults (store + dominio) +
args_resolver (precedenza esplicito→inline→ricordato→config + cattura).

Determinismo §7.9: tutto tabellare, niente LLM. Pattern Roberto: una CRUD a cui
manca l'oggetto deve poterlo ricevere inline e ricordarlo come default.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock


import args_defaults as D  # noqa: E402
import args_resolver as R  # noqa: E402

_SCHEMA = {"required": ["repo"], "properties": {"repo": {"type": "string"}}}


def _fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "DB_PATH", tmp_path / "args_defaults.sqlite")
    D._conn_cache.clear()


# ---- domain_for: provider raggruppa, formato no -------------------------

def test_domain_provider_groups_skill():
    assert D.domain_for("find_issues_github") == "github"
    assert D.domain_for("find_pulls_github") == "github"  # stesso dominio


def test_domain_format_qualifier_falls_to_object():
    # `pdf` è un qualifier di FORMATO, non provider → dominio = object `files`
    assert D.domain_for("read_files_pdf") == "files"
    assert D.domain_for("find_files") == "files"


def test_domain_unparsable_is_none():
    assert D.domain_for("notavalidname!!") is None


# ---- store -------------------------------------------------------------

def test_store_set_get_update(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    assert D.get_default("a", "github", "repo") is None
    D.set_default("a", "github", "repo", "x/y")
    assert D.get_default("a", "github", "repo") == "x/y"
    D.set_default("a", "github", "repo", "p/q")  # update
    assert D.get_default("a", "github", "repo") == "p/q"


def test_store_isolation_per_actor_and_domain(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    D.set_default("a", "github", "repo", "a/r")
    D.set_default("b", "github", "repo", "b/r")
    assert D.get_default("a", "github", "repo") == "a/r"
    assert D.get_default("b", "github", "repo") == "b/r"
    assert D.get_default("a", "files", "repo") is None  # dominio diverso


def test_is_scope_arg():
    assert D.is_scope_arg("repo")
    assert not D.is_scope_arg("title")
    assert not D.is_scope_arg("query_text")


# ---- resolver: precedenza ----------------------------------------------

def test_resolve_inline_from_query(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    r = R.resolve_scope_args("find_issues_github", {}, _SCHEMA,
                             actor="t", query="nuove issue di brunialti/metnos")
    assert r["repo"] == "brunialti/metnos"


def test_resolve_remembered(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    D.set_default("t", "github", "repo", "foo/bar")
    r = R.resolve_scope_args("find_issues_github", {}, _SCHEMA,
                             actor="t", query="elenca le issue")
    assert r["repo"] == "foo/bar"


def test_resolve_placeholder_uses_config(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    # repo a PLACEHOLDER + nessun inline/ricordato → cade sul config (cred dominio)
    with mock.patch("credentials.load", return_value={"repo": "config/repo"}):
        r = R.resolve_scope_args("find_issues_github", {"repo": "owner/name"},
                                 _SCHEMA, actor="t", query="elenca le issue")
    assert r["repo"] == "config/repo"


def test_resolve_explicit_valid_untouched(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    D.set_default("t", "github", "repo", "remembered/x")
    r = R.resolve_scope_args("find_issues_github", {"repo": "real/repo"},
                             _SCHEMA, actor="t", query="x")
    assert r["repo"] == "real/repo"  # esplicito vince su ricordato


def test_resolve_inline_beats_remembered(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    D.set_default("t", "github", "repo", "old/remembered")
    r = R.resolve_scope_args("find_issues_github", {}, _SCHEMA,
                             actor="t", query="issue di new/inline")
    assert r["repo"] == "new/inline"  # inline ha precedenza sul ricordato


def test_resolve_no_scope_arg_noop(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    schema = {"required": ["title"], "properties": {"title": {"type": "string"}}}
    args = {"title": "hello"}
    r = R.resolve_scope_args("write_issues_github", args, schema,
                             actor="t", query="x")
    assert r == args  # nessun cambiamento


# ---- cattura -----------------------------------------------------------

def test_remember_captures_used_value(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    R.remember_scope_args("find_issues_github", {"repo": "cap/tured"}, actor="t")
    assert D.get_default("t", "github", "repo") == "cap/tured"


def test_remember_skips_placeholder(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    R.remember_scope_args("find_issues_github", {"repo": "owner/name"}, actor="t")
    assert D.get_default("t", "github", "repo") is None  # placeholder non ricordato


# ---- F2: scope_form_request (form ibrido read/write) -------------------

def test_form_read_missing_asks():
    # READ (find) con repo required ancora mancante → form
    fr = R.scope_form_request("find_issues_github", {}, _SCHEMA, query="elenca")
    assert fr is not None and fr["decision"] == "needs_inputs"
    vars_ = [s["var"] for s in fr["needs_inputs"]["dialog"]]
    assert vars_ == ["repo"]
    assert fr["needs_inputs"]["on_complete"]["type"] == "resume_executor_with_values"


def test_form_read_resolved_no_ask():
    # READ con repo già risolto → niente form (silenzioso)
    fr = R.scope_form_request("find_issues_github", {"repo": "a/b"}, _SCHEMA,
                              query="x")
    assert fr is None


def test_form_write_always_confirms_prefilled():
    # WRITE (delete) con repo risolto → form di CONFERMA pre-compilato (§2.8)
    sch = {"required": ["repo", "number"],
           "properties": {"repo": {"type": "string"}}}
    fr = R.scope_form_request("delete_issues_github", {"repo": "a/b", "number": 5},
                              sch, query="chiudi la issue 5")
    assert fr is not None
    d = fr["needs_inputs"]["dialog"][0]
    assert d["var"] == "repo" and d["default"] == "a/b"  # pre-compilato


def test_form_non_scope_no_ask():
    sch = {"required": ["title"], "properties": {"title": {"type": "string"}}}
    fr = R.scope_form_request("write_issues_github", {"title": "x"}, sch, query="x")
    assert fr is None
