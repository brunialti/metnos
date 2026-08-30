"""RM-0005: prove native readiness for the residual A--M consumers."""
from __future__ import annotations

import datetime

import pytest

import detection_lexicon as dl
import detection_lexicon_seed_residual_am as seed
import i18n


@pytest.fixture(autouse=True)
def isolated_detection_store(monkeypatch, tmp_path):
    old_conn = dl._conn
    monkeypatch.setattr(dl, "DB_PATH", tmp_path / "detection.sqlite")
    monkeypatch.setattr(dl, "_conn", None)
    monkeypatch.setattr(dl, "_seeded", False)
    monkeypatch.setattr(dl, "_cache", {})
    monkeypatch.setattr(dl, "_regex_cache", {})
    monkeypatch.setattr(dl, "_coverage_gaps_logged", set())
    monkeypatch.setattr(dl, "_declared_review_policies", {})
    monkeypatch.setattr(dl, "_declared_baseline_languages", {})
    monkeypatch.setattr(seed, "_registered_target", None)
    monkeypatch.setattr(i18n, "current_lang", lambda: "it")
    yield
    new_conn = dl._conn
    if new_conn is not None and new_conn is not old_conn:
        new_conn.close()


class _Folders:
    def list(self):
        return "OK", [
            b'(\\Junk) "/" "INBOX.Spam"',
            b'(\\Trash) "/" "INBOX.Trash"',
            b'(\\Sent) "/" "INBOX.Sent"',
        ]


def _translate(concept, language, payload):
    dl.mark_for_translation(concept, language)
    dl.set_translated(concept, language, payload)


def test_historical_italian_and_english_behaviour_is_preserved(monkeypatch):
    from admin.manifest_refactor import _is_verbose
    from agent_runtime import (
        _query_has_notify_continuation, detect_binding,
    )
    from backends.events.google_workspace import _resolve_calendar_id
    from backends.messages.gmail_google_workspace import (
        _resolve_dst_folder as _resolve_gmail_folder,
    )
    from backends.messages.email_metnos import (
        _resolve_dst_folder, _rolling_window_delta,
    )
    from engine.cluster import normalize_query

    seed.register_all()
    assert normalize_query("Dimmi gli appuntamenti di domani") == (
        "appuntamenti domani"
    )
    assert normalize_query("Show me the events") == "me events"
    assert detect_binding("apri il portale aziendale") == "web"
    assert detect_binding("mount NAS team") == "cifs"
    assert _query_has_notify_continuation("proponi tre orari e avvisami")
    assert _query_has_notify_continuation("offer three slots and email me")
    assert _resolve_calendar_id("utente") == "primary"
    assert _resolve_calendar_id("self") == "primary"
    assert _rolling_window_delta("last-settimana") == datetime.timedelta(days=7)
    assert _rolling_window_delta("last-week") == datetime.timedelta(days=7)
    assert _rolling_window_delta("3 giorni") == datetime.timedelta(days=3)
    assert _rolling_window_delta("3 days") == datetime.timedelta(days=3)
    assert _resolve_dst_folder(_Folders(), "posta indesiderata") == "INBOX.Spam"
    assert _resolve_dst_folder(_Folders(), "trash") == "INBOX.Trash"
    assert _resolve_gmail_folder("cestino") == (
        ["TRASH"], ["INBOX"], "Trash",
    )
    assert _resolve_gmail_folder("archive") == ([], ["INBOX"], "archive")
    assert _is_verbose({"description": {"it": "USO CORRETTO: x"}})[0]
    assert _is_verbose({
        "description": {"en": " ".join(["Example:"] * 4)},
    })[0]


def test_missing_and_pending_native_rows_are_invisible(monkeypatch):
    from admin.manifest_refactor import _is_verbose
    from agent_runtime import _query_has_notify_continuation, detect_binding
    from backends.events.google_workspace import (
        _CalendarIdentityLexiconUnavailable, _resolve_calendar_id,
    )
    from backends.messages.gmail_google_workspace import (
        _GmailFolderLexiconUnavailable,
        _resolve_dst_folder as _resolve_gmail_folder,
        modify as modify_gmail,
    )
    monkeypatch.setattr(
        "backends.messages.gmail_google_workspace._run_gmail",
        lambda *_args, **_kwargs: pytest.fail("Gmail runner must not execute"),
    )
    from backends.messages.email_metnos import (
        _resolve_dst_folder, _rolling_window_delta,
    )
    from engine.cluster import normalize_query

    seed.register_all()
    monkeypatch.setattr(i18n, "current_lang", lambda: "zz")
    assert normalize_query("dime los eventos") == "dime los eventos"
    assert detect_binding("apri il portale") == "generic"
    assert detect_binding("ssh host.example") == "ssh"
    assert not _query_has_notify_continuation("e mandami una email")
    with pytest.raises(_CalendarIdentityLexiconUnavailable):
        _resolve_calendar_id("utente")
    assert _resolve_calendar_id("primary") == "primary"
    assert _resolve_calendar_id("team@example.test") == "team@example.test"
    assert _rolling_window_delta("3 giorni") is None
    assert _resolve_dst_folder(_Folders(), "cestino") == "cestino"
    with pytest.raises(_GmailFolderLexiconUnavailable):
        _resolve_gmail_folder("cestino")
    blocked = modify_gmail({"message_id": "m1", "dst_folder": "cestino"})
    assert blocked["error_class"] == "dependency_unavailable"
    assert blocked["used"] == 0
    assert not _is_verbose({"description": {"it": "USO CORRETTO: x"}})[0]

    dl.mark_for_translation(seed.NOTIFY_CONTINUATION, "zz")
    dl.mark_for_translation(seed.CALENDAR_IDENTITY_ALIAS, "zz")
    dl.mark_for_translation(seed.GMAIL_FOLDER_ALIAS, "zz")
    assert not _query_has_notify_continuation("and send me an email")
    with pytest.raises(_CalendarIdentityLexiconUnavailable):
        _resolve_calendar_id("self")
    with pytest.raises(_GmailFolderLexiconUnavailable):
        _resolve_gmail_folder("trash")


def test_ready_third_language_drives_every_residual_consumer(monkeypatch):
    from admin.manifest_refactor import _is_verbose
    from agent_runtime import (
        _query_has_continuation, _query_has_notify_continuation,
        detect_binding,
    )
    from backends.events.google_workspace import _resolve_calendar_id
    from backends.messages.gmail_google_workspace import (
        _resolve_dst_folder as _resolve_gmail_folder,
    )
    from backends.messages.email_metnos import (
        _resolve_dst_folder, _resolve_window, _rolling_window_delta,
    )
    from engine.cluster import normalize_query

    seed.register_all()
    translations = {
        seed.MANIFEST_VERBOSITY: {
            "marker": ["USO DETALLADO"], "example": ["Ejemplo:"],
        },
        seed.AGENT_AFFINITY_STOPWORD: ["el"],
        seed.AGENT_BINDING_WEAK: {
            "cifs": ["recurso"], "ssh": ["acceso-seguro"],
            "web": ["sitio"],
        },
        seed.AGENT_SIMPLE_CONJUNCTION: ["y"],
        seed.NOTIFY_CONTINUATION: [r"\bavisame\b"],
        seed.CALENDAR_IDENTITY_ALIAS: {
            "primary": ["usuario"], "all": ["todos"],
        },
        seed.GMAIL_FOLDER_ALIAS: {
            "inbox": ["entrada"], "trash": ["papelera"],
            "spam": ["correo basura"], "sent": ["enviados"],
            "drafts": ["borradores"], "important": ["importante-es"],
            "starred": ["destacados"], "archive": ["archivo"],
        },
        seed.MAIL_TIME_WINDOW: {
            "today": ["hoy"], "yesterday": ["ayer"],
            "preset_week": ["semana-anterior"],
            "preset_month": ["mes-anterior"],
            "preset_year": ["ano-anterior"],
            "day_unit": ["dia", "dias"],
            "hour_unit": ["hora", "horas"],
            "week_unit": ["semana", "semanas"],
            "month_unit": ["mes", "meses"],
            "year_unit": ["ano", "anos"],
            "relative_marker": ["hace"],
        },
        seed.MAIL_FOLDER_SPECIAL: {
            "junk": ["correo basura"], "trash": ["papelera"],
            "sent": ["enviados"], "drafts": ["borradores"],
        },
        seed.PACKAGE_DIRECTION_ALIAS: {
            "install": ["instalar"], "uninstall": ["desinstalar"],
        },
        seed.CLUSTER_STOPWORD: ["dime", "los"],
    }
    for concept, payload in translations.items():
        _translate(concept, "es", payload)
    monkeypatch.setattr(i18n, "current_lang", lambda: "es")

    assert normalize_query("dime los eventos") == "eventos"
    assert detect_binding("abre el sitio corporativo") == "web"
    assert _query_has_notify_continuation("avisame")
    # The translated conjunction participates in the two-action gate.
    monkeypatch.setattr("agent_runtime._detlex.search", lambda *_a: False)
    assert _query_has_continuation("crea un file y invia una mail")
    assert _resolve_calendar_id("usuario") == "primary"
    assert _rolling_window_delta("3 dias") == datetime.timedelta(days=3)
    now = datetime.datetime(2026, 8, 30, 10, 0, 0)
    assert _resolve_window("hoy", now=now)[0] == "30-Aug-2026"
    assert _resolve_dst_folder(_Folders(), "papelera") == "INBOX.Trash"
    assert _resolve_gmail_folder("papelera") == (
        ["TRASH"], ["INBOX"], "Trash",
    )
    assert _is_verbose({"description": {"es": "USO DETALLADO"}})[0]


def test_mutating_or_identity_concepts_require_manual_review():
    seed.register_all()
    for concept in (
        seed.AGENT_BINDING_WEAK,
        seed.AGENT_SIMPLE_CONJUNCTION,
        seed.NOTIFY_CONTINUATION,
        seed.CALENDAR_IDENTITY_ALIAS,
        seed.GMAIL_FOLDER_ALIAS,
        seed.MAIL_FOLDER_SPECIAL,
        seed.PACKAGE_DIRECTION_ALIAS,
    ):
        for language in ("it", "en"):
            resource = dl.resource_for_language(
                concept, language, fallback=False, ready_only=True,
            )
            assert resource is not None
            assert resource["review_policy"] == "manual"

    assert seed.ready_forms(seed.AGENT_SIMPLE_CONJUNCTION) == ("e", "and")
    dl._open().execute(
        "UPDATE detection_lexicon SET review_policy='automatic' "
        "WHERE concept=? AND lang='it'",
        (seed.AGENT_SIMPLE_CONJUNCTION,),
    )
    dl._open().commit()
    dl._invalidate(seed.AGENT_SIMPLE_CONJUNCTION)
    assert seed.ready_forms(seed.AGENT_SIMPLE_CONJUNCTION) == ()
