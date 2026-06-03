"""self_recipient_resolver: send email senza destinatario esplicito → actor (§7.9).

Riproduce il bug live 3/6 (turn f5caaf4f): Qwen emette send_messages(via=email,
messages=[{body}], attachments=[xlsx]) SENZA to/to_user → mail non parte. Il
resolver inietta `to=_actor_email` quando la query è self-targeted, senza
misroute sui destinatari esterni.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from self_recipient_resolver import resolve_self_recipient  # noqa: E402

EMAIL = "roberto.brunialti@knowcastle.com"


def _args(**over):
    a = {"via_channel": "email",
         "messages": [{"body": "Ecco lo spreadsheet."}],
         "attachments": ["/x.xlsx"],
         "_actor": "Roberto", "_actor_email": EMAIL}
    a.update(over)
    return a


def test_self_send_email_no_recipient_fills_actor():
    out = resolve_self_recipient("send_messages", _args(),
                                 "invia lo spreadsheet alla mia email")
    assert out["messages"][0]["to"] == EMAIL


def test_mandami_self_fills():
    out = resolve_self_recipient("send_messages", _args(),
                                 "mandami via email il riepilogo")
    assert out["messages"][0]["to"] == EMAIL


def test_explicit_external_recipient_not_overridden():
    # "a Mario" = destinatario esterno → NON auto-instradare all'actor (no misroute).
    out = resolve_self_recipient("send_messages", _args(),
                                 "invia il file via email a Mario")
    assert "to" not in out["messages"][0]


def test_explicit_email_in_query_not_overridden():
    out = resolve_self_recipient("send_messages", _args(),
                                 "invia via email a luca@example.com")
    assert "to" not in out["messages"][0]


def test_recipient_already_present_untouched():
    a = _args(messages=[{"body": "x", "to": "altro@x.com"}])
    out = resolve_self_recipient("send_messages", a, "invia alla mia email")
    assert out["messages"][0]["to"] == "altro@x.com"


def test_non_email_channel_untouched():
    # chat/auto → path "mandami=chat" (ea1ba7e), il resolver non tocca.
    a = _args(via_channel="auto")
    out = resolve_self_recipient("send_messages", a, "mandami il riepilogo")
    assert "to" not in out["messages"][0]


def test_no_actor_email_no_invention():
    a = _args(_actor_email="")
    out = resolve_self_recipient("send_messages", a, "invia alla mia email")
    assert "to" not in out["messages"][0]


def test_non_send_tool_untouched():
    a = {"via_channel": "email", "_actor_email": EMAIL}
    assert resolve_self_recipient("create_files_spreadsheet", a, "x") is a
