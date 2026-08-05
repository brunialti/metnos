"""Test junk_mail_resolver + filter_entries su campo-LISTA (23/6/2026).

«sposta/filtra le email di spam» → filtro su category_hints (bulk/newsletter),
NON type='spam' (campo inesistente). Bug live turno 1a3127ee: filter(type=spam)
su 260 mail → 0 → move saltato. Opzione (b) Roberto: spam = rumore bulk.
"""
from __future__ import annotations

import sys
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")
sys.path.insert(0, str(_RUNTIME.parent / "executors" / "filter_entries"))


def _seed():
    from store_bootstrap import register_builtin_stores
    register_builtin_stores()
    import detection_lexicon_seed as seed
    seed.register_all()


# ── filter_entries su campo-LISTA (intersezione) ─────────────────────────
def test_filter_list_field_intersection():
    import filter_entries as fe
    mails = [
        {"subject": "Newsletter", "category_hints": ["list", "noreply"]},
        {"subject": "Bolletta", "category_hints": []},
        {"subject": "Promo", "category_hints": ["bulk"]},
        {"subject": "Personale", "category_hints": ["noreply"]},
    ]
    res = fe.invoke({"entries": mails, "where_field": "category_hints",
                     "where_in": ["list", "bulk", "esp"]})
    kept = res.get("entries") or []
    subs = {e["subject"] for e in kept}
    assert subs == {"Newsletter", "Promo"}, subs  # noreply-only NON e' nei marker


def test_filter_scalar_field_unchanged():
    import filter_entries as fe
    items = [{"folder": "INBOX"}, {"folder": "INBOX.Spam"}]
    res = fe.invoke({"entries": items, "where_field": "folder",
                     "where_in": ["INBOX"]})
    assert [e["folder"] for e in (res.get("entries") or [])] == ["INBOX"]


def test_filter_list_where_not_in():
    import filter_entries as fe
    mails = [{"id": 1, "category_hints": ["list"]},
             {"id": 2, "category_hints": []}]
    res = fe.invoke({"entries": mails, "where_field": "category_hints",
                     "where_not_in": ["list", "bulk"]})
    assert [e["id"] for e in (res.get("entries") or [])] == [2]


# ── junk_mail_resolver ───────────────────────────────────────────────────
def test_resolver_rewrites_spam_filter():
    _seed()
    import junk_mail_resolver as jmr
    q = "sposta le email di spam nella cartella Spam"
    # FORMA PROD REALE: il proposer aggiunge kind='mail' E type='spam'. Le mail
    # NON hanno il campo `kind` -> kind='mail' scarterebbe TUTTO. Vanno rimossi
    # entrambi (bug live 0f1fe504).
    out = jmr.resolve_junk_mail("filter_entries", {"type": "spam", "kind": "mail"}, q)
    assert out["where_field"] == "category_hints"
    # raffinato: list (newsletter/List-Unsubscribe) + esp (marketing), NON
    # noreply/auto/bulk da soli (catturerebbero ordini/bollette/host).
    assert "list" in out["where_in"] and "esp" in out["where_in"]
    assert "noreply" not in out["where_in"] and "auto" not in out["where_in"]
    assert "type" not in out
    assert "kind" not in out


def test_resolver_noop_without_junk_intent():
    _seed()
    import junk_mail_resolver as jmr
    q = "filtra le mail importanti di oggi"
    args = {"where_field": "relevance", "where_in": ["high"]}
    assert jmr.resolve_junk_mail("filter_entries", dict(args), q) == args


def test_resolver_noop_other_tool():
    _seed()
    import junk_mail_resolver as jmr
    q = "sposta le email di spam"
    assert jmr.resolve_junk_mail("read_messages", {"x": 1}, q) == {"x": 1}


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
