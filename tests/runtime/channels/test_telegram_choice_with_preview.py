"""Test deterministici per Telegram media-group invio per
`choice_with_preview` (PR5).

Mock del TelegramChannel: niente HTTP, niente API. Verifichiamo:
  - _send_choice_preview_album invoca channel.send_dialog_preview_album
    con N attachments (1 per option) e crop applicato.
  - >10 opzioni → fallback (ritorna error too_many_options) cosi' il
    caller costruisce solo la keyboard plain.
  - integrazione _build_dialog_keyboard per choice_with_preview emette
    1 row per option (label) + cancel.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


@pytest.fixture
def safe_image(tmp_path, monkeypatch):
    """Immagine reale + monkeypatch dei root consentiti."""
    from PIL import Image
    p = tmp_path / "src.jpg"
    Image.new("RGB", (300, 300), "red").save(p, "JPEG")
    import dialog_preview as _dpv
    monkeypatch.setattr(_dpv, "_ALLOWED_ROOTS_RAW", (str(tmp_path),))
    return p


@pytest.fixture
def fake_daemon():
    """Costruisce un ChannelDaemon-like con un fake channel mockato."""
    from channels.daemon import ChannelDaemon
    fake_channel = MagicMock()
    fake_channel.name = "telegram"
    fake_channel.send_dialog_preview_album = MagicMock(
        return_value={"ok": True, "message_ids": [1, 2]},
    )
    fake_channel.send = MagicMock(return_value={"ok": True})
    # Bypassiamo __init__ (richiede modules pesanti); creiamo l'istanza
    # via __new__ + assign manuale degli attributi minimi che il test
    # tocca (channel, _stop, run_turn, dry_run).
    d = ChannelDaemon.__new__(ChannelDaemon)
    d.channel = fake_channel
    d._stop = False
    d.dry_run = False
    d.run_turn = None
    return d


# ── _send_choice_preview_album ───────────────────────────────────────────

def test_telegram_builds_media_group_for_choice_with_preview(
    fake_daemon, safe_image,
):
    """3 opzioni → channel.send_dialog_preview_album invocato con 3 thumb."""
    step = {
        "var": "y", "prompt": "?",
        "schema": {
            "kind": "choice_with_preview",
            "options": [
                {"value": "a", "label": "A",
                 "preview_image_path": f"{safe_image}#bbox=10,10,40,40"},
                {"value": "b", "label": "B",
                 "preview_image_path": str(safe_image)},
                {"value": "c", "label": "C",
                 "preview_image_path": f"{safe_image}#bbox=50,50,80,80"},
            ],
        },
    }
    res = fake_daemon._send_choice_preview_album(
        chat_id="999", step=step, reply_to=None,
    )
    assert res["ok"] is True
    fake_daemon.channel.send_dialog_preview_album.assert_called_once()
    kwargs = fake_daemon.channel.send_dialog_preview_album.call_args.kwargs
    atts = kwargs["attachments"]
    assert len(atts) == 3
    assert all("tmp_path" in a for a in atts)
    assert [a["caption"] for a in atts] == ["A", "B", "C"]


def test_telegram_fallback_text_when_more_than_10_options(
    fake_daemon, safe_image,
):
    """11 opzioni: _send_choice_preview_album rifiuta (>10), il fallback
    sara' la keyboard plain (gestito da _build_dialog_keyboard)."""
    options = [
        {"value": f"v{i}", "label": f"L{i}",
         "preview_image_path": str(safe_image)}
        for i in range(11)
    ]
    step = {"var": "y", "prompt": "?",
            "schema": {"kind": "choice_with_preview",
                        "options": options}}
    res = fake_daemon._send_choice_preview_album(
        chat_id="999", step=step,
    )
    assert res["ok"] is False
    assert res["error"] == "too_many_options"
    assert res["n_options"] == 11
    fake_daemon.channel.send_dialog_preview_album.assert_not_called()


def test_telegram_keyboard_choice_with_preview_label_only(fake_daemon):
    """_build_dialog_keyboard per choice_with_preview emette N row label
    + cancel. callback_data = `dlg:<id>:<idx>:c<i>`."""
    step = {
        "var": "y", "prompt": "?",
        "schema": {
            "kind": "choice_with_preview",
            "options": [
                {"value": "ospite_alfa", "label": "Ospite Alfa",
                 "preview_image_path": "/x"},
                {"value": "ospite_beta", "label": "Ospite Beta",
                 "preview_image_path": "/y"},
            ],
        },
    }
    rows = fake_daemon._build_dialog_keyboard("abcd1234", 0, step)
    assert len(rows) == 3  # 2 option + cancel
    assert rows[0][0]["text"] == "Ospite Alfa"
    assert rows[0][0]["data"] == "dlg:abcd1234:0:c0"
    assert rows[1][0]["text"] == "Ospite Beta"
    assert rows[1][0]["data"] == "dlg:abcd1234:0:c1"
    assert rows[2][0]["data"] == "dlg:abcd1234:cancel"


def test_telegram_callback_decode_choice_with_preview(safe_image, monkeypatch):
    """callback_data `c<idx>` per choice_with_preview risolve sull'option
    corrispondente e ritorna `value` (non label)."""
    from channels.daemon import ChannelDaemon, InboundMessage
    import dialog_pending as _dp

    # Setup state su disco isolato.
    tmp_dialog_dir = safe_image.parent / "get_inputs"
    monkeypatch.setattr(_dp, "DIALOG_DIR", tmp_dialog_dir)
    state = {
        "dialog_id": "dlg00",
        "title": "x",
        "dialog": [{
            "var": "chosen_slug", "prompt": "?",
            "schema": {
                "kind": "choice_with_preview",
                "options": [
                    {"value": "ospite_alfa", "label": "A",
                     "preview_image_path": str(safe_image)},
                    {"value": "ospite_beta", "label": "B",
                     "preview_image_path": str(safe_image)},
                ],
            },
        }],
        "fmt": "telegram_inline",
        "values_collected": {},
        "step_index": 0,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "actor": "host",
        "channel": "telegram",
        "owner_user_id": "pytest-runtime-owner",
        "completed": False, "cancelled": False,
    }
    _dp.save_pending("telegram:42", "dlg00", state)

    fake_channel = MagicMock()
    fake_channel.name = "telegram"
    fake_channel.send = MagicMock(return_value={"ok": True})
    d = ChannelDaemon.__new__(ChannelDaemon)
    d.channel = fake_channel
    d._stop = False
    d.dry_run = False
    d.run_turn = None
    # Bypass _send_text e _on_get_inputs_completed (unimplemented qui).
    d._send_text = MagicMock(return_value={"ok": True})
    d._on_get_inputs_completed = MagicMock(return_value="")

    msg = InboundMessage(channel="telegram", sender_id="42",
                          text="dlg:dlg00:0:c1", message_id="m1",
                          received_at=0.0,
                          extra={"_principal": {
                              "user_id": "pytest-runtime-owner",
                              "actor": "host", "role": "host"}})
    res = d._handle_dialog_callback(msg, "dlg:dlg00:0:c1")
    assert res["ok"] is True
    assert res["callback"] == "dlg_completed"
    # Verifica che il value salvato sia "ospite_beta" (non "B").
    final = _dp.load_pending("telegram:42", "dlg00",
                             owner_user_id="pytest-runtime-owner")
    assert final["values_collected"]["chosen_slug"] == "ospite_beta"
