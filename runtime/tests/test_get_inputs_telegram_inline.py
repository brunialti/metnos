"""Test deterministici per fmt='telegram_inline' (ADR 0090, 7/5/2026 notte).

Coprono:
- _decide_fmt: telegram + dialog yes_no/choice → telegram_inline.
- _decide_fmt: telegram + dialog mixed (yes_no + text) → dialogue.
- _decide_fmt: telegram + 1 step yes_no → telegram_inline (no soglia min).
- _decide_fmt: http + 2 step yes_no/choice → form (HTTP win).
- _all_inline_compatible: yes_no/choice solo → True; con multi_choice → False.
- _build_dialog_keyboard parsa correttamente yes_no/choice + cancel.
- callback_data parsing: dlg:<id>:<idx>:yes / dlg:<id>:cancel / dlg:<id>:<idx>:c0.

Niente subprocess, niente Telegram API: tutto puro Python.
"""
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2] / "executors" / "get_inputs"))

import get_inputs as _gi


# ── _decide_fmt ──────────────────────────────────────────────────────────

def test_telegram_yes_no_only_inline():
    fmt = _gi._decide_fmt("auto", 1, "telegram",
                            [{"schema": {"kind": "yes_no"}}])
    assert fmt == "telegram_inline", f"got {fmt!r}"


def test_telegram_choice_only_inline():
    fmt = _gi._decide_fmt("auto", 2, "telegram",
                            [{"schema": {"kind": "yes_no"}},
                             {"schema": {"kind": "choice", "choices": ["a", "b"]}}])
    assert fmt == "telegram_inline"


def test_telegram_with_text_falls_back_to_dialogue():
    """Mixed yes_no + text: il text richiede sequenza dialogue."""
    fmt = _gi._decide_fmt("auto", 2, "telegram",
                            [{"schema": {"kind": "yes_no"}},
                             {"schema": {"kind": "text"}}])
    assert fmt == "dialogue", f"mixed got {fmt!r}"


def test_telegram_with_multi_choice_falls_back():
    """multi_choice non ancora supportato in MVP inline keyboard."""
    fmt = _gi._decide_fmt("auto", 1, "telegram",
                            [{"schema": {"kind": "multi_choice",
                                          "choices": ["a", "b"]}}])
    assert fmt == "dialogue"


def test_telegram_with_credentials_falls_back():
    fmt = _gi._decide_fmt("auto", 2, "telegram",
                            [{"schema": {"kind": "credentials"}},
                             {"schema": {"kind": "text"}}])
    assert fmt == "dialogue"


def test_http_wins_over_inline():
    """HTTP + ≥2 step → form, indipendentemente dai kind."""
    fmt = _gi._decide_fmt("auto", 2, "http",
                            [{"schema": {"kind": "yes_no"}},
                             {"schema": {"kind": "yes_no"}}])
    assert fmt == "form"


def test_explicit_telegram_inline_passthrough():
    """fmt='telegram_inline' esplicito: pass-through, no degrade."""
    fmt = _gi._decide_fmt("telegram_inline", 1, "telegram",
                            [{"schema": {"kind": "yes_no"}}])
    assert fmt == "telegram_inline"


# ── _all_inline_compatible ───────────────────────────────────────────────

def test_all_inline_compatible_yes_no_choice():
    dialog = [{"schema": {"kind": "yes_no"}},
              {"schema": {"kind": "choice", "choices": ["x"]}}]
    assert _gi._all_inline_compatible(dialog) is True


def test_all_inline_compatible_with_text_false():
    dialog = [{"schema": {"kind": "yes_no"}},
              {"schema": {"kind": "text"}}]
    assert _gi._all_inline_compatible(dialog) is False


def test_all_inline_compatible_empty_true():
    """Lista vuota: trivially True (nessuno step incompatibile)."""
    assert _gi._all_inline_compatible([]) is True


# ── _build_dialog_keyboard (via daemon import) ────────────────────────────

def test_build_keyboard_yes_no():
    """yes_no: 1 row con 2 button + 1 row Annulla. callback_data shape."""
    from channels.daemon import ChannelDaemon
    # Costruzione minima: serve solo channel.name per i18n (non lo usa qui).
    # Usiamo un'istanza fake con metodo statico-simile.
    step = {"var": "ok", "schema": {"kind": "yes_no"}}
    fake = type("F", (), {})()
    rows = ChannelDaemon._build_dialog_keyboard(fake, "abcd1234", 0, step)
    assert len(rows) == 2  # 1 row sì/no + 1 row cancel
    assert len(rows[0]) == 2
    assert rows[0][0]["data"] == "dlg:abcd1234:0:yes"
    assert rows[0][1]["data"] == "dlg:abcd1234:0:no"
    assert rows[1][0]["data"] == "dlg:abcd1234:cancel"


def test_build_keyboard_choice_3_options():
    """choice: N row verticali (uno per choice) + 1 row Annulla."""
    from channels.daemon import ChannelDaemon
    step = {"var": "color",
            "schema": {"kind": "choice", "choices": ["rosso", "verde", "blu"]}}
    fake = type("F", (), {})()
    rows = ChannelDaemon._build_dialog_keyboard(fake, "xxx", 1, step)
    assert len(rows) == 4  # 3 choice + cancel
    assert rows[0][0]["text"] == "rosso"
    assert rows[0][0]["data"] == "dlg:xxx:1:c0"
    assert rows[1][0]["data"] == "dlg:xxx:1:c1"
    assert rows[2][0]["data"] == "dlg:xxx:1:c2"
    assert rows[3][0]["data"] == "dlg:xxx:cancel"


def test_build_keyboard_choice_callback_under_64_bytes():
    """Telegram limita callback_data a 64 byte. L'indice cN regge anche
    per choice con label molto lunghi."""
    from channels.daemon import ChannelDaemon
    long_choices = [f"opzione_che_descrive_un_caso_X_di_dominio_{i}_lunga"
                     for i in range(20)]
    step = {"var": "x",
            "schema": {"kind": "choice", "choices": long_choices}}
    fake = type("F", (), {})()
    rows = ChannelDaemon._build_dialog_keyboard(fake, "deadbeef" * 2, 5, step)
    for row in rows:
        for btn in row:
            assert len(btn["data"].encode("utf-8")) <= 64, btn["data"]


# ── callback_data parsing (smoke) ─────────────────────────────────────────

def test_callback_data_format_yes():
    data = "dlg:abc123:0:yes"
    parts = data.split(":", 3)
    assert parts[0] == "dlg"
    assert parts[1] == "abc123"
    assert parts[2] == "0"
    assert parts[3] == "yes"


def test_callback_data_format_choice():
    data = "dlg:abc123:2:c5"
    parts = data.split(":", 3)
    assert parts[3] == "c5"
    # Decode: c5 → indice 5
    assert parts[3].startswith("c") and int(parts[3][1:]) == 5


def test_callback_data_format_cancel():
    data = "dlg:abc123:cancel"
    parts = data.split(":", 3)
    assert parts[2] == "cancel"
    assert len(parts) == 3
