"""mail_account_resolver — «tutta la posta» → account="all" (bug live 10/6/2026).

«controlla tutta la mia posta ultime 24h» leggeva UN solo account: il
proposer copia il PATTERN del manifest (account="metnos_system") e il
quantificatore «tutta» non arriva mai all'arg. Il resolver deterministico
(gemello di self_recipient_resolver) canonicalizza account="all" quando la
query chiede TUTTA la posta senza nominare un account configurato.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_RUNTIME = str((Path(__file__).resolve().parents[3] / "runtime"))

import mail_account_resolver as mar
from mail_account_resolver import resolve_mail_account


@pytest.fixture(autouse=True)
def _known_accounts(monkeypatch):
    """list_known_accounts deterministico per i test (no IMAP/creds)."""
    import mail_client
    monkeypatch.setattr(
        mail_client, "list_known_accounts",
        lambda: ["metnos_system", "knowcastle", "mykleos"])


def test_account_for_address_accepts_unique_provider_alias(monkeypatch):
    import mail_client
    creds = {
        "metnos_system": {"user": "system@example.test"},
        "knowcastle": {"user": "roberto.brunialti@knowcastle.com"},
        "mykleos": {"user": "other@tiscali.test"},
    }
    monkeypatch.setattr(mail_client, "_account_creds",
                        lambda account: creds[account])
    assert mail_client.account_for_address(
        "roberto_brunialti@knowcastle.com") == "knowcastle"


def test_account_for_address_rejects_ambiguous_domain(monkeypatch):
    import mail_client
    monkeypatch.setattr(mail_client, "list_known_accounts",
                        lambda: ["one", "two"])
    monkeypatch.setattr(mail_client, "_account_creds", lambda account: {
        "user": f"{account}@example.test"})
    assert mail_client.account_for_address("third@example.test") is None


# ── override: «tutta/all la posta» senza account nominato ─────────────────

def test_tutta_la_mia_posta_it():
    out = resolve_mail_account(
        "read_messages", {"time_window": "last-24h"},
        "controlla tutta la mia posta ultime 24h")
    assert out["account"] == "all"
    assert out["time_window"] == "last-24h"  # altri arg preservati


def test_pattern_default_del_manifest_viene_sovrascritto():
    # Il proposer copia account="metnos_system" dal PATTERN §2.5: il
    # quantificatore della query vince (stessa filosofia self_recipient).
    out = resolve_mail_account(
        "read_messages", {"account": "metnos_system"},
        "leggi tutte le mie mail di oggi")
    assert out["account"] == "all"


def test_all_my_email_en():
    out = resolve_mail_account(
        "read_messages", {}, "check all my email from the last 24 hours")
    assert out["account"] == "all"


def test_tutti_gli_account_it():
    out = resolve_mail_account(
        "read_messages", {}, "leggi la posta di tutti gli account")
    assert out["account"] == "all"


# ── account NOMINATO: ri-risoluzione dalla query attuale (12/6/2026) ───────
# Faglia arg-leakage: un piano servito da L1 champion / L0 0b porta l'account
# della SUA query d'origine; l'account nominato nella query ATTUALE vince.

def test_account_nominato_sovrascrive_il_leak_del_champion():
    # Champion nato da «mail di metnos» (account='metnos_system') servito a
    # una query che nomina knowcastle: lo slot si ri-riempie.
    out = resolve_mail_account(
        "read_messages", {"account": "metnos_system"},
        "controlla la mail di knowcastle")
    assert out["account"] == "knowcastle"


def test_account_nominato_vince_anche_sul_quantificatore():
    # «tutte le mail di knowcastle»: il nome scopa il quantificatore (l'utente
    # ha scelto il perimetro: quell'account).
    out = resolve_mail_account(
        "read_messages", {"account": "metnos_system"},
        "leggi tutte le mail di knowcastle")
    assert out["account"] == "knowcastle"


def test_due_account_nominati_ambiguo_noop():
    args = {"account": "metnos_system"}
    out = resolve_mail_account(
        "read_messages", args, "confronta knowcastle e mykleos")
    assert out["account"] == "metnos_system"  # decide il planner


def test_nome_non_configurato_non_scatta():
    # «metnos» NON e' un nome account completo (metnos_system lo e'):
    # nessun override; il valore del piano resta.
    out = resolve_mail_account(
        "read_messages", {"account": "metnos_system"},
        "controlla le mail di metnos")
    assert out["account"] == "metnos_system"


# ── noop: account nominato / già multi / query senza quantificatore ───────

def test_account_nominato_non_viene_toccato():
    out = resolve_mail_account(
        "read_messages", {"account": "knowcastle"},
        "controlla tutta la posta di knowcastle")
    assert out["account"] == "knowcastle"


def test_query_senza_tutta_noop():
    args = {"time_window": "today"}
    out = resolve_mail_account(
        "read_messages", args, "controlla la posta di oggi")
    assert "account" not in out


def test_account_lista_esplicita_noop():
    out = resolve_mail_account(
        "read_messages", {"account": ["work", "personal"]},
        "leggi tutte le mail")
    assert out["account"] == ["work", "personal"]


def test_account_gia_all_idempotente():
    out = resolve_mail_account(
        "read_messages", {"account": "all"}, "leggi tutte le mail")
    assert out["account"] == "all"


def test_solo_read_messages():
    # Mai allargare azioni mutating: move/send non vengono toccati.
    for tool in ("move_messages", "send_messages", "find_files"):
        out = resolve_mail_account(tool, {}, "sposta tutte le mail in Junk")
        assert "account" not in out


def test_via_channel_telegram_noop():
    out = resolve_mail_account(
        "read_messages", {"via_channel": "telegram"},
        "leggi tutti i messaggi telegram")
    assert "account" not in out


def test_tutti_i_file_non_matcha():
    # «tutti» senza parola-mail entro 3 parole: nessun override.
    out = resolve_mail_account(
        "read_messages", {}, "leggi tutti i file della cartella")
    assert "account" not in out


def test_input_degeneri():
    assert resolve_mail_account("read_messages", {}, "") == {}
    args = {"account": "x"}
    assert resolve_mail_account("read_messages", args, None) is args


def test_creds_lookup_fallisce_resta_robusto(monkeypatch):
    # list_known_accounts esplode → il resolver non deve propagare.
    import mail_client
    def _boom():
        raise RuntimeError("no creds")
    monkeypatch.setattr(mail_client, "list_known_accounts", _boom)
    out = resolve_mail_account(
        "read_messages", {}, "controlla tutta la mia posta")
    assert out["account"] == "all"
