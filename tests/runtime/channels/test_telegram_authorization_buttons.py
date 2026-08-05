"""Test deterministici: bottoni inline Telegram per i flussi di
AUTORIZZAZIONE (10/6/2026).

Coprono il sottoinsieme Telegram di get_inputs (ADR 0090) e i bottoni
delle approvazioni cap-pending:

  1. `orchestration.invoke_get_inputs_internal` con channel='telegram'
     risolve fmt='auto' → 'telegram_inline' per dialog yes_no/choice
     (prima: SEMPRE 'dialogue' → l'utente doveva digitare il numero).
     HTTP invariato (form/dialogue). Kind non rappresentabili → dialogue
     (degrado onesto §2.8).
  2. `channels.inline_ui`: cap alternative (INLINE_MAX_CHOICES), keyboard
     Approva/Rifiuta per admin_approval (`cap:<turn_id>:yes|no` ≤ 64 byte),
     `keyboard_for_proposal` con lookup multi-candidato dello stato.
  3. `ChannelDaemon._handle_cap_callback`: tap Approva consuma
     l'admin_approval pendente; tap Rifiuta pulisce; turn_id stale →
     refusal onesto senza esecuzione.
  4. `ChannelDaemon._handle_dialog_callback`: risolve stato salvato per
     ACTOR logico (`telegram:host`) da un tap del chat_id numerico
     (fix key-mismatch) e per choices dict {label,value} salva il VALUE
     (coerente col parser testuale).
  5. `TelegramProgress.finish(buttons=...)`: reply_markup nell'edit del
     progress message (percorso di consegna normale dei turni planner).
  6. `recurring_tasks._run_user_query_callback`: push schedulato con
     proposta interattiva → inline keyboard allegata + cap_pending
     salvato per il chat_id (la risposta testuale resta valida).

Mock totale di Telegram: nessuna chiamata Bot API, nessun daemon.
"""
from __future__ import annotations

import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")
_TEST_OWNER = "pytest-runtime-owner"

import dialog_pending as _dp  # noqa: E402
from channels import inline_ui  # noqa: E402


# ── Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def isolated_dialog_dir(tmp_path, monkeypatch):
    d = tmp_path / "get_inputs"
    monkeypatch.setattr(_dp, "DIALOG_DIR", d)
    return d


@pytest.fixture
def isolated_cap_dir(tmp_path, monkeypatch):
    from channels import daemon as daemon_mod
    d = tmp_path / "cap_pending"
    monkeypatch.setattr(daemon_mod, "CAP_PENDING_DIR", d)
    return d


@pytest.fixture
def fake_daemon(isolated_cap_dir):
    """ChannelDaemon con fake channel (pattern test_telegram_choice_with_preview)."""
    from channels.daemon import ChannelDaemon
    fake_channel = MagicMock()
    fake_channel.name = "telegram"
    fake_channel.send = MagicMock(return_value={"ok": True})
    d = ChannelDaemon.__new__(ChannelDaemon)
    d.channel = fake_channel
    d._stop = False
    d.dry_run = False
    d.run_turn = None
    return d


def _choice_step(n=3, var="decisione"):
    return {
        "var": var,
        "prompt": "Cosa vuoi fare?",
        "schema": {"kind": "choice",
                    "choices": [f"opzione {i}" for i in range(1, n + 1)]},
    }


def test_channel_turn_uses_the_bound_users_language(fake_daemon, monkeypatch):
    """La lingua di un utente Telegram non modifica quella dell'istanza."""
    import i18n
    import users
    from channels import InboundMessage

    monkeypatch.setattr(
        fake_daemon, "_callback_principal",
        lambda _msg: {"user_id": "user-1", "actor": "alice", "role": "guest"},
    )
    monkeypatch.setattr(
        users, "get_pref",
        lambda owner, key, default=None: "en" if key == "lang" else default,
    )
    monkeypatch.setattr(
        fake_daemon, "_handle_message_scoped",
        lambda _msg: {"lang": i18n.current_lang()},
    )
    before = i18n.current_lang()
    result = fake_daemon.handle_message(InboundMessage(
        channel="telegram", sender_id="123", text="help",
        message_id="m1", received_at=0.0,
    ))
    assert result == {"lang": "en"}
    assert i18n.current_lang() == before


# ── 1. fmt resolution orchestrata (Gap principale) ───────────────────────

def test_orchestrated_choice_resolves_telegram_inline(isolated_dialog_dir):
    """Dialog di autorizzazione orchestrato (es. strato3 con frontier)
    su Telegram → fmt=telegram_inline, NON dialogue."""
    from orchestration import invoke_get_inputs_internal
    res = invoke_get_inputs_internal(
        sender_id="telegram:host",
        title="Autorizzazione",
        description=None,
        dialog=[_choice_step(5)],
        fmt="auto",
        on_complete={"type": "strato3_choice_dispatch"},
        actor="host",
        channel="telegram",
        owner_user_id=_TEST_OWNER,
    )
    assert res["ok"] is True
    assert res["fmt"] == "telegram_inline"
    caps = res["expandable_caps"]
    assert caps and caps[0]["fmt"] == "telegram_inline"
    assert caps[0]["sender_for_state"] == "telegram:host"


def test_orchestrated_yes_no_resolves_telegram_inline(isolated_dialog_dir):
    from orchestration import invoke_get_inputs_internal
    res = invoke_get_inputs_internal(
        sender_id="telegram:host", title="Conferma", description=None,
        dialog=[{"var": "confirm", "prompt": "Procedo?",
                  "schema": {"kind": "yes_no"}}],
        fmt="auto", on_complete=None, actor="host", channel="telegram",
        owner_user_id=_TEST_OWNER,
    )
    assert res["fmt"] == "telegram_inline"


def test_orchestrated_text_degrades_to_dialogue(isolated_dialog_dir):
    """Kind non rappresentabile a bottoni (text) → dialogue onesto."""
    from orchestration import invoke_get_inputs_internal
    res = invoke_get_inputs_internal(
        sender_id="telegram:host", title="Input", description=None,
        dialog=[{"var": "nome", "prompt": "Come lo chiamo?",
                  "schema": {"kind": "text"}}],
        fmt="auto", on_complete=None, actor="host", channel="telegram",
        owner_user_id=_TEST_OWNER,
    )
    assert res["fmt"] == "dialogue"


def test_orchestrated_too_many_choices_degrades(isolated_dialog_dir):
    """Oltre INLINE_MAX_CHOICES la keyboard non e' usabile → dialogue
    (lista numerata, risposta col numero)."""
    from orchestration import invoke_get_inputs_internal
    res = invoke_get_inputs_internal(
        sender_id="telegram:host", title="Scelta", description=None,
        dialog=[_choice_step(inline_ui.INLINE_MAX_CHOICES + 1)],
        fmt="auto", on_complete=None, actor="host", channel="telegram",
        owner_user_id=_TEST_OWNER,
    )
    assert res["fmt"] == "dialogue"


def test_orchestrated_http_form_unchanged(isolated_dialog_dir):
    """Canale HTTP: comportamento INVARIATO (form per >=2 step)."""
    from orchestration import invoke_get_inputs_internal
    res = invoke_get_inputs_internal(
        sender_id="http:host:_", title="Form", description=None,
        dialog=[_choice_step(), {"var": "x", "prompt": "?",
                                   "schema": {"kind": "text"}}],
        fmt="auto", on_complete=None, actor="host", channel="http",
        owner_user_id=_TEST_OWNER,
    )
    assert res["fmt"] == "form"


def test_orchestrated_http_single_step_form(isolated_dialog_dir):
    """B2 (59f3519, 5/7): su HTTP anche il mono-step choice-like (yes_no
    incluso) va a FORM cliccabile — «dialogue» degradava a testo."""
    from orchestration import invoke_get_inputs_internal
    res = invoke_get_inputs_internal(
        sender_id="http:host:_", title="Conferma", description=None,
        dialog=[{"var": "confirm", "prompt": "?",
                  "schema": {"kind": "yes_no"}}],
        fmt="auto", on_complete=None, actor="host", channel="http",
        owner_user_id=_TEST_OWNER,
    )
    assert res["fmt"] == "form"


# ── 2. inline_ui ──────────────────────────────────────────────────────────

def test_all_inline_compatible_respects_choice_cap():
    ok_dialog = [_choice_step(inline_ui.INLINE_MAX_CHOICES)]
    over_dialog = [_choice_step(inline_ui.INLINE_MAX_CHOICES + 1)]
    assert inline_ui.all_inline_compatible(ok_dialog) is True
    assert inline_ui.all_inline_compatible(over_dialog) is False


def test_build_approval_keyboard_shape_and_size():
    rows = inline_ui.build_approval_keyboard("abcdef0123456789")
    assert len(rows) == 1 and len(rows[0]) == 2
    assert rows[0][0]["data"] == "cap:abcdef0123456789:yes"
    assert rows[0][1]["data"] == "cap:abcdef0123456789:no"
    for row in rows:
        for btn in row:
            assert len(btn["data"].encode("utf-8")) <= 64
            assert btn["text"]  # label i18n non vuota


def test_keyboard_for_proposal_admin_approval():
    buttons, preview = inline_ui.keyboard_for_proposal(
        {"kind": "admin_approval", "executor": "admin"},
        sender_candidates=["telegram:42"],
        owner_user_id=_TEST_OWNER,
        turn_id="deadbeef00112233",
    )
    assert preview is None
    assert buttons and buttons[0][0]["data"] == "cap:deadbeef00112233:yes"


def test_keyboard_for_proposal_dialog_state_actor_keyed(isolated_dialog_dir):
    """Stato salvato per actor logico (telegram:host): i candidati
    multi-chiave lo trovano anche partendo dal chat_id numerico."""
    state = {
        "dialog_id": "dlg11", "title": "Autorizzazione",
        "dialog": [_choice_step(3)],
        "fmt": "telegram_inline", "values_collected": {}, "step_index": 0,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "actor": "host", "channel": "telegram",
        "owner_user_id": _TEST_OWNER,
        "completed": False, "cancelled": False,
    }
    _dp.save_pending("telegram:host", "dlg11", state)
    cands = inline_ui.sender_state_candidates(
        "telegram", "42", actor="host", sender_for_state=None)
    assert cands == ["telegram:42", "42", "telegram:host", "host"]
    buttons, preview = inline_ui.keyboard_for_proposal(
        {"kind": "get_inputs_response", "fmt": "telegram_inline",
         "dialog_id": "dlg11"},
        sender_candidates=cands, owner_user_id=_TEST_OWNER, turn_id=None,
    )
    assert preview is None
    # 3 alternative (una row ciascuna) + row Annulla
    assert buttons is not None and len(buttons) == 4
    assert buttons[0][0]["data"] == "dlg:dlg11:0:c0"
    assert buttons[3][0]["data"] == "dlg:dlg11:cancel"


def test_keyboard_for_proposal_dialogue_fmt_none():
    """fmt=dialogue → niente bottoni (degrado onesto: testo)."""
    buttons, preview = inline_ui.keyboard_for_proposal(
        {"kind": "get_inputs_response", "fmt": "dialogue",
         "dialog_id": "whatever"},
        sender_candidates=["telegram:42"], owner_user_id=_TEST_OWNER,
        turn_id="t1",
    )
    assert buttons is None and preview is None


# ── 3. _handle_cap_callback (approvazioni admin) ─────────────────────────

def _save_cap_pending(sender_id, kind, turn_id):
    from channels import daemon as daemon_mod
    daemon_mod._cap_pending_save(
        sender_id, "attiva il frontier sulla issue",
        {"kind": kind, "executor": "admin",
         "args_suggested": {"intent": "x", "command_proposed": "y",
                              "actor_consent_token": "tok"}},
        turn_id,
        owner_user_id="user-a",
    )


def _callback_extra():
    return {"kind": "callback", "_principal": {
        "user_id": "user-a", "actor": "alice", "role": "host",
    }}


def test_cap_pending_is_atomic_and_private(tmp_path, monkeypatch):
    from channels import daemon as daemon_mod
    monkeypatch.setattr(daemon_mod, "CAP_PENDING_DIR", tmp_path / "pending")

    _save_cap_pending("42", "admin_approval", "turn-private")

    path = daemon_mod._cap_pending_path("42")
    assert path.exists()
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert not list(path.parent.glob("*.tmp"))


def test_cap_callback_yes_consumes_admin_approval(fake_daemon):
    from channels.daemon import InboundMessage
    from channels import daemon as daemon_mod
    _save_cap_pending("42", "admin_approval", "turn0001aaaa")
    fake_daemon._consume_admin_approval = MagicMock(return_value="Eseguito.")
    import actor_resolver
    # niente pairing DB nel test: actor fisso
    orig = actor_resolver.resolve_actor
    actor_resolver.resolve_actor = lambda c, s: "host"
    try:
        msg = InboundMessage(channel="telegram", sender_id="42",
                              text="cap:turn0001aaaa:yes", message_id="m9",
                              received_at=0.0, extra=_callback_extra())
        res = fake_daemon._handle_cap_callback(msg, "cap:turn0001aaaa:yes")
    finally:
        actor_resolver.resolve_actor = orig
    assert res == {"ok": True, "callback": "cap_yes", "kind": "admin_approval"}
    fake_daemon._consume_admin_approval.assert_called_once()
    # cap_pending consumato: un secondo tap deve trovare stato vuoto
    assert daemon_mod._cap_pending_load(
        "42", owner_user_id="user-a") is None
    # la reply con l'esito e' stata inviata via canale
    assert fake_daemon.channel.send.called


def test_cap_callback_no_declines_without_executing(fake_daemon):
    from channels.daemon import InboundMessage
    from channels import daemon as daemon_mod
    _save_cap_pending("42", "admin_approval", "turn0002bbbb")
    fake_daemon._consume_admin_approval = MagicMock()
    msg = InboundMessage(channel="telegram", sender_id="42",
                          text="cap:turn0002bbbb:no", message_id="m9",
                          received_at=0.0, extra=_callback_extra())
    res = fake_daemon._handle_cap_callback(msg, "cap:turn0002bbbb:no")
    assert res == {"ok": True, "callback": "cap_no", "kind": "admin_approval"}
    fake_daemon._consume_admin_approval.assert_not_called()
    assert daemon_mod._cap_pending_load(
        "42", owner_user_id="user-a") is None


def test_cap_callback_stale_turn_id_honest_refusal(fake_daemon):
    """Tap su un messaggio VECCHIO (turn_id diverso dal pendente) →
    refusal onesto: niente esecuzione, pending corrente intatto (§2.8)."""
    from channels.daemon import InboundMessage
    from channels import daemon as daemon_mod
    _save_cap_pending("42", "admin_approval", "turnNEW00cccc")
    fake_daemon._consume_admin_approval = MagicMock()
    msg = InboundMessage(channel="telegram", sender_id="42",
                          text="cap:turnOLD00dddd:yes", message_id="m9",
                          received_at=0.0, extra=_callback_extra())
    res = fake_daemon._handle_cap_callback(msg, "cap:turnOLD00dddd:yes")
    assert res["ok"] is False and res["reason"] == "cap_pending_expired"
    fake_daemon._consume_admin_approval.assert_not_called()
    assert daemon_mod._cap_pending_load(
        "42", owner_user_id="user-a") is not None  # intatto
    assert fake_daemon.channel.send.called  # refusal inviato, non silenzio


def test_cap_callback_routed_from_handle_callback(fake_daemon):
    """`_handle_callback` instrada il prefisso `cap:` al handler dedicato."""
    from channels.daemon import InboundMessage
    fake_daemon._handle_cap_callback = MagicMock(return_value={"ok": True})
    msg = InboundMessage(channel="telegram", sender_id="42",
                          text="cap:t1:yes", message_id="m1",
                          received_at=0.0, extra={"kind": "callback"})
    fake_daemon._handle_callback(msg)
    fake_daemon._handle_cap_callback.assert_called_once_with(msg, "cap:t1:yes")


def test_cap_callback_approval_required_kind(fake_daemon):
    from channels.daemon import InboundMessage
    _save_cap_pending("42", "approval_required", "turn0003eeee")
    fake_daemon._consume_approval_required = MagicMock(return_value="Fatto.")
    import actor_resolver
    orig = actor_resolver.resolve_actor
    actor_resolver.resolve_actor = lambda c, s: "host"
    try:
        msg = InboundMessage(channel="telegram", sender_id="42",
                              text="cap:turn0003eeee:yes", message_id="m9",
                              received_at=0.0, extra=_callback_extra())
        res = fake_daemon._handle_cap_callback(msg, "cap:turn0003eeee:yes")
    finally:
        actor_resolver.resolve_actor = orig
    assert res["ok"] is True and res["kind"] == "approval_required"
    fake_daemon._consume_approval_required.assert_called_once()


def test_promoter_aggregated_callback_links_live_change_review(
        fake_daemon, monkeypatch):
    """Il digest aggregato deve puntare alla UI unificata realmente cablata."""
    from channels.daemon import InboundMessage
    monkeypatch.setenv("METNOS_HTTP_BASE_URL", "http://metnos.test:8770")
    fake_daemon._send_text = MagicMock(return_value={"ok": True})
    msg = InboundMessage(
        channel="telegram", sender_id="42",
        text="promoter:_aggregated:open_form", message_id="m-review",
        received_at=0.0, extra={"kind": "callback"},
    )

    result = fake_daemon._handle_promoter_callback(
        msg, "promoter:_aggregated:open_form")

    assert result == {"ok": True, "callback": "promoter_open_form"}
    sent_text = fake_daemon._send_text.call_args.args[1]
    assert "http://metnos.test:8770/admin/changes?state=proposed" in sent_text
    assert "/admin/promotions" not in sent_text


def test_promoter_rollback_callback_invokes_domain_operation(
        fake_daemon, monkeypatch):
    """Verifica il wiring callback reale; la semantica rollback ha test propri."""
    from channels.daemon import InboundMessage
    from jobs import promoter_rollback
    rollback = MagicMock(return_value={"ok": True, "name": "find_packages"})
    monkeypatch.setattr(promoter_rollback, "rollback_promotion", rollback)
    fake_daemon._send_text = MagicMock(return_value={"ok": True})
    msg = InboundMessage(
        channel="telegram", sender_id="42",
        text="promoter:p-123:rollback", message_id="m-rollback",
        received_at=0.0, extra={"kind": "callback"},
    )

    result = fake_daemon._handle_promoter_callback(
        msg, "promoter:p-123:rollback")

    assert result["ok"] is True
    assert result["callback"] == "promoter_rollback"
    rollback.assert_called_once_with("p-123")


# ── 4. _handle_dialog_callback: stato actor-keyed + choice dict ─────────

def test_dialog_callback_resolves_actor_keyed_state(
        fake_daemon, isolated_dialog_dir, monkeypatch):
    """Dialog orchestrato salvato per `telegram:host`; il tap arriva dal
    chat_id numerico 42. Il lookup multi-candidato lo trova e il value
    della choice DICT salvato e' `value` (non il label, non il dict)."""
    from channels.daemon import InboundMessage
    import actor_resolver
    monkeypatch.setattr(actor_resolver, "resolve_actor", lambda c, s: "host")
    state = {
        "dialog_id": "dlg77", "title": "Bozza risposta issue",
        "dialog": [{
            "var": "decisione", "prompt": "Approva, edita o rifiuta?",
            "schema": {"kind": "choice", "choices": [
                {"label": "approva la bozza", "value": "approve"},
                {"label": "edita la bozza", "value": "edit"},
                {"label": "rifiuta la bozza", "value": "reject"},
            ]},
        }],
        "fmt": "telegram_inline", "values_collected": {}, "step_index": 0,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "actor": "host", "channel": "telegram",
        "owner_user_id": _TEST_OWNER,
        "sender_id": "telegram:host",
        "completed": False, "cancelled": False,
    }
    _dp.save_pending("telegram:host", "dlg77", state)
    fake_daemon._send_text = MagicMock(return_value={"ok": True})
    fake_daemon._on_get_inputs_completed = MagicMock(return_value="")
    msg = InboundMessage(channel="telegram", sender_id="42",
                          text="dlg:dlg77:0:c1", message_id="m1",
                          received_at=0.0, extra={"kind": "callback",
                          "_principal": {"user_id": _TEST_OWNER,
                                         "actor": "host", "role": "host"}})
    res = fake_daemon._handle_dialog_callback(msg, "dlg:dlg77:0:c1")
    assert res["ok"] is True and res["callback"] == "dlg_completed"
    final = _dp.load_pending("telegram:host", "dlg77",
                             owner_user_id=_TEST_OWNER)
    assert final["completed"] is True
    assert final["values_collected"]["decisione"] == "edit"


def test_dialog_callback_hides_state_from_different_canonical_owner(
        fake_daemon, isolated_dialog_dir):
    from channels.daemon import InboundMessage
    state = {
        "dialog_id": "owned-dialog", "title": "Privato",
        "dialog": [{"var": "ok", "prompt": "Procedo?",
                    "schema": {"kind": "yes_no"}}],
        "fmt": "telegram_inline", "values_collected": {}, "step_index": 0,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "actor": "owner", "owner_user_id": "owner-user",
        "sender_id": "telegram:owner", "completed": False,
        "cancelled": False,
    }
    _dp.save_pending("telegram:owner", "owned-dialog", state)
    fake_daemon._send_text = MagicMock(return_value={"ok": True})
    msg = InboundMessage(
        channel="telegram", sender_id="42",
        text="dlg:owned-dialog:0:yes", message_id="m-owner",
        received_at=0.0,
        extra={"kind": "callback", "_principal": {
            "user_id": "different-user", "actor": "different",
            "role": "guest",
        }},
    )

    result = fake_daemon._handle_dialog_callback(
        msg, "dlg:owned-dialog:0:yes")

    # The lookup itself is owner-scoped: a foreign principal must not learn
    # that another user's dialog exists.
    assert result["reason"] == "dialog_expired"
    assert _dp.load_pending(
        "telegram:owner", "owned-dialog",
        owner_user_id="owner-user")["values_collected"] == {}


def test_typed_reply_resolves_actor_keyed_state(
        fake_daemon, isolated_dialog_dir):
    """Percorso TESTUALE (`_consume_get_inputs_response`): la risposta
    digitata risolve un dialogo salvato per actor logico anche quando il
    cap_pending porta solo il chat_id (push da query schedulata)."""
    state = {
        "dialog_id": "dlg88", "title": "Conferma",
        "dialog": [{"var": "confirm", "prompt": "Procedo?",
                     "schema": {"kind": "yes_no"}}],
        "fmt": "telegram_inline", "values_collected": {}, "step_index": 0,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "actor": "host", "channel": "telegram",
        "owner_user_id": _TEST_OWNER,
        "sender_id": "telegram:host",
        "completed": False, "cancelled": False,
    }
    _dp.save_pending("telegram:host", "dlg88", state)
    # Proposta SENZA sender_for_state (caso executor-created/scheduled):
    # il lookup deve cadere sul candidato actor (`telegram:host`).
    reply, retry, summary = fake_daemon._consume_get_inputs_response(
        {"kind": "get_inputs_response", "dialog_id": "dlg88",
         "fmt": "telegram_inline"},
        "sì", actor="host", sender_id="42",
        owner_user_id=_TEST_OWNER,
    )
    assert retry is True and summary is not None
    final = _dp.load_pending("telegram:host", "dlg88",
                             owner_user_id=_TEST_OWNER)
    assert final["completed"] is True
    assert final["values_collected"]["confirm"] is True


# ── 5. TelegramProgress.finish con buttons ───────────────────────────────

def test_progress_finish_attaches_reply_markup():
    from progress import TelegramProgress
    fake_channel = MagicMock()
    fake_channel._call = MagicMock(return_value={"ok": True})
    p = TelegramProgress(fake_channel, "42")
    p.message_id = 1234  # progress message gia' aperto
    rows = [[{"text": "Sì", "data": "dlg:abc:0:yes"},
             {"text": "No", "data": "dlg:abc:0:no"}]]
    p.finish("Procedo con l'operazione?", buttons=rows)
    edit_calls = [c for c in fake_channel._call.call_args_list
                  if c.args[0] == "editMessageText"]
    assert edit_calls, "editMessageText non chiamato"
    params = edit_calls[0].args[1]
    assert "reply_markup" in params
    markup = json.loads(params["reply_markup"])
    assert markup["inline_keyboard"][0][0]["callback_data"] == "dlg:abc:0:yes"


def test_progress_finish_no_buttons_no_markup():
    """Senza buttons: nessun reply_markup (comportamento invariato)."""
    from progress import TelegramProgress
    fake_channel = MagicMock()
    fake_channel._call = MagicMock(return_value={"ok": True})
    p = TelegramProgress(fake_channel, "42")
    p.message_id = 1234
    p.finish("Fatto.")
    edit_calls = [c for c in fake_channel._call.call_args_list
                  if c.args[0] == "editMessageText"]
    assert edit_calls
    assert "reply_markup" not in edit_calls[0].args[1]


# ── 6. recurring_tasks: push schedulato con proposta interattiva ─────────

class _FakeSendChannel:
    """Sostituto di TelegramChannel: cattura le send, zero rete."""
    name = "telegram"
    sent: list = []  # popolato per classe; reset nel test

    def __init__(self, *a, **kw):
        pass

    def send(self, recipient, message):
        type(self).sent.append((recipient, message))
        return {"ok": True}


def test_scheduled_push_attaches_keyboard_and_saves_cap_pending(
        isolated_dialog_dir, isolated_cap_dir, monkeypatch):
    """Query schedulata (flusso manutenzione) che apre un dialog di
    autorizzazione: il push Telegram porta la inline keyboard E il
    cap_pending viene salvato per il chat_id (risposta testuale valida)."""
    import recurring_tasks
    from channels import daemon as daemon_mod
    import channels.telegram as tg_mod

    # Stato dialog su disco (come lo lascia l'orchestratore al run_turn).
    state = {
        "dialog_id": "dlg99", "title": "Bozza pronta",
        "dialog": [{
            "var": "decisione", "prompt": "Approva, edita o rifiuta?",
            "schema": {"kind": "choice",
                        "choices": ["approva", "edita", "rifiuta"]},
        }],
        "fmt": "telegram_inline", "values_collected": {}, "step_index": 0,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "actor": "host", "channel": "telegram",
        "owner_user_id": _TEST_OWNER,
        "sender_id": "telegram:host",
        "completed": False, "cancelled": False,
    }
    _dp.save_pending("telegram:host", "dlg99", state)

    fake_log = types.SimpleNamespace(
        final_message="Bozza pronta. Approva, edita o rifiuta?",
        final_kind="ask",
        turn_id="turnsched0001",
        steps=[],
        expandable_caps=[{
            "kind": "get_inputs_response", "dialog_id": "dlg99",
            "step_total": 1, "fmt": "telegram_inline",
            "sender_for_state": "telegram:host",
        }],
    )
    fake_ar = types.SimpleNamespace(run_turn=lambda *a, **kw: fake_log)
    monkeypatch.setitem(sys.modules, "agent_runtime", fake_ar)
    monkeypatch.setattr(
        "users.get_user",
        lambda actor: ({"id": "user-a", "name": "host", "role": "host"}
                       if actor in {"host", "user-a"} else None),
    )
    monkeypatch.setattr("users.get_pref", lambda *_a, **_k: None)
    monkeypatch.setattr(recurring_tasks, "_live_telegram_recipient",
                        lambda _owner: "42")
    _FakeSendChannel.sent = []
    monkeypatch.setattr(tg_mod, "TelegramChannel", _FakeSendChannel)

    record = {"name": "maintenance_drafts", "label": "bozze issue",
              "query": "prepara le bozze di risposta alle issue nuove",
              "actor": "host", "channel": "telegram", "chat_id": "42",
              "owner_user_id": _TEST_OWNER, "scheduler_name": "maintenance_drafts"}
    out = recurring_tasks._run_user_query_callback_scoped(record)
    assert "pushed telegram chat=42" in out

    assert len(_FakeSendChannel.sent) == 1
    recipient, message = _FakeSendChannel.sent[0]
    assert recipient == "42"
    assert message.buttons is not None
    # 3 alternative + Annulla; callback self-contained dlg:
    assert message.buttons[0][0]["data"] == "dlg:dlg99:0:c0"
    assert message.buttons[-1][0]["data"] == "dlg:dlg99:cancel"

    # cap_pending salvato per il chat_id: la risposta TESTUALE al push
    # viene instradata al dialogo dal daemon (stesso percorso interattivo).
    pending = daemon_mod._cap_pending_load(
        "42", owner_user_id=_TEST_OWNER)
    assert pending is not None
    assert pending["proposal"]["dialog_id"] == "dlg99"
    assert pending["turn_id"] == "turnsched0001"


def test_scheduled_push_dialogue_fmt_no_buttons(
        isolated_dialog_dir, isolated_cap_dir, monkeypatch):
    """Proposta NON inline (fmt=dialogue, es. step text): il push resta
    testuale — degrado onesto, nessun bottone fasullo."""
    import recurring_tasks
    import channels.telegram as tg_mod

    fake_log = types.SimpleNamespace(
        final_message="Mi serve un testo: rispondi al prossimo messaggio.",
        final_kind="ask",
        turn_id="turnsched0002",
        steps=[],
        expandable_caps=[{
            "kind": "get_inputs_response", "dialog_id": "dlgtext",
            "step_total": 1, "fmt": "dialogue",
            "sender_for_state": "telegram:host",
        }],
    )
    fake_ar = types.SimpleNamespace(run_turn=lambda *a, **kw: fake_log)
    monkeypatch.setitem(sys.modules, "agent_runtime", fake_ar)
    monkeypatch.setattr(
        "users.get_user",
        lambda actor: ({"id": "user-a", "name": "host", "role": "host"}
                       if actor in {"host", "user-a"} else None),
    )
    monkeypatch.setattr("users.get_pref", lambda *_a, **_k: None)
    monkeypatch.setattr(recurring_tasks, "_live_telegram_recipient",
                        lambda _owner: "42")
    _FakeSendChannel.sent = []
    monkeypatch.setattr(tg_mod, "TelegramChannel", _FakeSendChannel)

    record = {"name": "t2", "label": None,
              "query": "raccogli un input", "actor": "host",
              "channel": "telegram", "chat_id": "42",
              "owner_user_id": _TEST_OWNER, "scheduler_name": "t2"}
    out = recurring_tasks._run_user_query_callback_scoped(record)
    assert "pushed telegram chat=42" in out
    _, message = _FakeSendChannel.sent[0]
    assert message.buttons is None
