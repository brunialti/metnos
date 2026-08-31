"""Prove RM-0005 mirate per il seed separato dei resolver operativi."""
from __future__ import annotations

from types import SimpleNamespace

import detection_lexicon_seed_resolvers as seed


def test_resolver_seed_census_and_i18n_outputs(monkeypatch):
    concepts = {}
    messages = {}

    def capture(concept, kind, **kwargs):
        concepts[concept] = {"kind": kind, **kwargs}
        return True

    monkeypatch.setattr(seed._dl, "register", capture)
    import i18n
    monkeypatch.setattr(
        i18n, "register_key_if_missing",
        lambda key, it, en=None, **_kw: messages.setdefault(key, (it, en)),
    )

    seed.register_all()

    assert set(concepts) == {
        "fast_path.intent_exact",
        "fast_path.undo_prefix",
        "fast_path.identity_suffix",
        "resolver.backend_provider",
        "resolver.calendar",
        "resolver.mail_bulk",
        "resolver.email_channel_request",
        "resolver.from_contains",
        "resolver.read_document_format",
        "resolver.read_deduplicate",
        "resolver.target_device",
    }
    assert concepts["resolver.target_device"]["review_policy"] == "manual"
    assert set(concepts["resolver.target_device"]["it"]) == {
        "locative_anchor", "nominal_anchor", "local_marker",
        "server_adjunct", "server_nominal",
    }
    assert {
        "MSG_FAST_TIME", "MSG_FAST_DATE", "MSG_FAST_IDENTITY",
        "MSG_FAST_LOCATION", "MSG_FAST_WEEKDAY_0", "MSG_FAST_MONTH_12",
        "ERR_FAST_EXECUTOR",
    } <= set(messages)


def test_modular_seed_enqueues_instance_language(monkeypatch):
    queued = []
    monkeypatch.setattr(seed, "_registered", False)
    monkeypatch.setattr(seed, "register_all", lambda: None)
    monkeypatch.setattr(seed._dl, "current_lang", lambda: "zz")
    monkeypatch.setattr(seed._dl, "enqueue_language", queued.append)

    seed.ensure_registered()

    assert queued == ["zz"]
    assert seed._registered is True


def test_fast_path_uses_third_language_input_and_output(monkeypatch):
    import fast_path

    monkeypatch.setattr(fast_path._resolver_seed, "ensure_registered", lambda: None)
    monkeypatch.setattr(fast_path._detlex, "mapping", lambda _concept: {})
    monkeypatch.setattr(
        fast_path._detlex, "mapping_for_language",
        lambda concept, lang, **_kw: (
            {"time": ["zz tempo"]}
            if concept == "fast_path.intent_exact" and lang == "zz" else {}
        ),
    )

    def translated(key, lang, **kwargs):
        assert lang == "zz"
        if key == "MSG_FAST_TIME":
            return f"ZZ {kwargs['hhmm']}"
        return f"{key}:{kwargs}"

    monkeypatch.setattr(fast_path._i18n, "get_for_language", translated)
    hit = fast_path.try_fast_path("zz tempo", lang="zz")
    assert hit is not None and hit["executor"] == "get_now"
    assert hit["render"]({
        "ok": True,
        "metadata": {"iso8601": "2026-08-30T12:34:00+02:00"},
    }) == "ZZ 12:34"


def test_fast_path_special_routes_stay_discriminant():
    import fast_path

    undo = fast_path.try_fast_path("annulla l'evento appena creato", lang="it")
    location = fast_path.try_fast_path("where am i", lang="en")
    identity = fast_path.try_fast_path("chi sei", lang="it")

    assert undo is not None and undo["executor"] == "undo_last_turn"
    assert location is not None and location["executor"] == "get_location"
    assert identity is not None and identity["executor"] is None
    assert "Metnos" in identity["direct_answer"]


def test_mail_bulk_keeps_structure_with_third_language_forms(monkeypatch):
    import mail_account_resolver as resolver

    monkeypatch.setattr(resolver._resolver_seed, "ensure_registered", lambda: None)
    monkeypatch.setattr(
        resolver._detlex, "mapping",
        lambda _concept: {
            "universal": ["zztutte"],
            "plural_possessive": ["zzmie"],
            "mail_noun": ["zzposta"],
        },
    )
    assert resolver._has_bulk_mail_request("zztutte le mie zzposta")
    assert not resolver._has_bulk_mail_request(
        "zztutte uno due tre quattro zzposta"
    )


def test_partial_target_materialization_is_fail_closed(monkeypatch):
    import target_device as target

    monkeypatch.setattr(target._resolver_seed, "ensure_registered", lambda: None)
    partial = {
        "locative_anchor": ["zu"],
        "nominal_anchor": ["der"],
        "local_marker": ["zuhost"],
        "server_adjunct": ["zuserver"],
        # server_nominal intentionally absent
    }
    monkeypatch.setattr(target._detlex, "current_lang", lambda: "zz")
    monkeypatch.setattr(
        target._detlex, "resource_for_language",
        lambda *_args, **_kwargs: {
            "kind": "mapping", "review_policy": "manual", "payload": partial,
        },
    )
    monkeypatch.setattr(
        target._detlex, "mapping",
        lambda _concept: {**partial, "server_nominal": ["der server"]},
    )

    result = target.resolve_target("do zuhost", [], is_available=lambda *_: True)
    assert result.status == "ambiguous"
    assert target.references_device("do zuhost", []) is True


def test_complete_reviewed_third_language_target_routes(monkeypatch):
    import target_device as target

    mapping = {
        "locative_anchor": ["zu"],
        "nominal_anchor": ["der"],
        "local_marker": ["zuhost"],
        "server_adjunct": ["zuserver"],
        "server_nominal": ["der server"],
    }
    monkeypatch.setattr(target._resolver_seed, "ensure_registered", lambda: None)
    monkeypatch.setattr(target._detlex, "current_lang", lambda: "zz")
    monkeypatch.setattr(
        target._detlex, "resource_for_language",
        lambda *_args, **_kwargs: {
            "kind": "mapping", "review_policy": "manual", "payload": mapping,
        },
    )
    monkeypatch.setattr(target._detlex, "mapping", lambda _concept: mapping)
    monkeypatch.setattr(target, "_match_polarity_state", lambda *_args: "asserted")
    device = SimpleNamespace(id="dev-1", name="Laptop", os_family="linux")

    result = target.resolve_target(
        "do zuhost", [device], is_available=lambda *_args: True,
    )
    assert result.status == "ok"
    assert result.target == "dev-1"
    assert result.explicit is True
