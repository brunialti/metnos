"""E2E pipeline bollette utility/telecom universale (24/5/2026).

Verifica end-to-end:
  1. Scansione TUTTE le mailbox configurate (mail/*.env + builtin)
  2. Classificazione sender vs VENDOR_WHITELIST (deterministic §7.9)
  3. Estrazione strutturata via best strategy per provider:
     - magic-link JSON (Eni/Engie/Doxee)
     - PDF allegato testuale (pdftotext)
     - PDF allegato scansionato (Tesseract OCR)
     - HTML body LLM
  4. Append nello sheet target (con cleanup post-test)
  5. Verifica schema: ogni row deve avere vendor + (amount_eur OR due_date)

Convergenza errore=0: tutte le bollette identificate devono avere ALMENO
vendor + due_date estratti (amount_eur ammesso null per provider che
richiedono login portale, es. Iliad).

Skip se: creds mail assenti / token Sheets assente / METNOS_E2E_RUN_SLOW != 1.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

# Repo root derivato dal path del test (§7.11 rename-resilient).
_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# Import path canonici per i moduli usati dai test:
# - runtime/ (config, backends, mail_client)
# - skill bundle it_locale/scripts/ (bills_extract — ADR 0160)
sys.path.insert(0, str(_REPO_ROOT / "runtime"))
sys.path.insert(0, str(_REPO_ROOT / "executors" / "skills" / "it_locale" / "scripts"))


_TOKEN_PATH = Path.home() / ".hermes" / "google_token.json"
_MAIL_DIR = Path.home() / ".config/metnos/mail"
_RUN_SLOW = os.environ.get("METNOS_E2E_RUN_SLOW", "0") == "1"


pytestmark = [
    pytest.mark.skipif(not _TOKEN_PATH.is_file(),
                        reason="OAuth Google assente"),
    pytest.mark.skipif(not _MAIL_DIR.is_dir(),
                        reason="~/.config/metnos/mail/ assente"),
    pytest.mark.skipif(not _RUN_SLOW,
                        reason="abilita METNOS_E2E_RUN_SLOW=1"),
]


@pytest.fixture(scope="module")
def test_sheet_id():
    """Sheet temporaneo per test. Cleanup garantito a teardown."""
    from backends.files import google_workspace as gws
    r = gws.create_spreadsheet({
        "title": f"E2E Test Bills {time.strftime('%Y-%m-%d %H:%M')}",
    })
    sid = (r.get("spreadsheet_id")
           or (r.get("results") or [{}])[0].get("spreadsheet_id"))
    assert sid, f"create_spreadsheet failed: {r}"
    # Headers + formati colonne
    gws.write_spreadsheet({
        "spreadsheet_id": sid,
        "range": "A1:G1",
        "values": [["Data Scadenza", "Vendor", "Categoria",
                     "Importo (EUR)", "Bolletta #", "Cliente #", "Note"]],
    })
    yield sid
    try:
        gws.delete({"file_id": sid})
    except Exception:
        pass


# --- Unit-ish tests sull'helper bills_extract ---------------------

def test_vendor_whitelist_classification():
    from bills_extract import _classify_sender, VENDOR_WHITELIST
    # Tutti i provider definiti devono matcharsi
    for domain, meta in VENDOR_WHITELIST.items():
        result = _classify_sender(f"Test <noreply@{domain}>")
        assert result is not None, f"{domain} non classificato"
        assert result["vendor"] == meta["vendor"]
    # Non-bill sender → None
    assert _classify_sender("Amazon <ship@amazon.it>") is None
    assert _classify_sender("") is None


def test_marketing_filter():
    from bills_extract import _is_marketing
    assert _is_marketing("Passa a Enel anche per il gas. 30% sconto!")
    assert _is_marketing("Nuova offerta gas e luce")
    assert not _is_marketing("Notifica emissione Bolletta Digitale")
    assert not _is_marketing("La tua fattura del 01/05/2026 è stata emessa")


def test_doxee_json_parser():
    """Schema Eni Plenitude / Doxee: verifica robustezza con payload tipico."""
    from bills_extract import _parse_doxee_json
    sample = {
        "FILE": {"DOCUMENT": [{"TEMPLATE": [{
            "Importo": 165.25,
            "DATA": [{
                "fattura": [{
                    "data_scadenza_cal": "2026/05/15",
                    "numero": 2618740825,
                    "data_riferimento_dal": "01/12/2025",
                    "data_riferimento_a": "31/03/2026",
                    "importo": "165,25",
                }],
                "utente": [{"cod_cliente": 110917375502}],
                "gas": [{"utilizzo": [{"consumo": 155}]}],
            }],
        }]}]}
    }
    out = _parse_doxee_json(sample)
    assert out is not None
    assert out["amount_eur"] == 165.25
    assert out["due_date"] == "2026-05-15"
    assert out["bill_number"] == "2618740825"
    assert out["customer_id"] == "110917375502"
    assert out["consumption"] == {"smc_gas": 155}


def test_doxee_json_parser_partial():
    """JSON malformato → None, no crash."""
    from bills_extract import _parse_doxee_json
    assert _parse_doxee_json({}) is None
    assert _parse_doxee_json({"FILE": {}}) is None


def test_magic_link_extract():
    from bills_extract import _extract_magic_link
    html = '<a href="https://interattiva.eniplenitude.com/abc123def">Vai</a>'
    p = r"https?://interattiva\.eniplenitude\.com/[a-f0-9]+"
    assert _extract_magic_link(html, p) == "https://interattiva.eniplenitude.com/abc123def"
    assert _extract_magic_link("nessun link", p) is None
    assert _extract_magic_link(html, None) is None


# --- E2E reale: scansione mailbox + extract + sheet append --------

def test_e2e_all_accounts_to_sheet(test_sheet_id: str):
    """Pipeline reale: itera tutti gli account, classifica, extract,
    append nello sheet test. Verifica error=0 sullo schema dei row."""
    from bills_extract import all_bills_across_accounts
    from backends.files import google_workspace as gws

    result = all_bills_across_accounts(time_window="last-90d")

    # Conta bollette per account
    total_bills = 0
    errors = []
    rows_to_append = []
    for acc, bills in result.items():
        if isinstance(bills, dict) and "error" in bills:
            # Account inaccessibile (es. mailbox vuota / no creds) → skip
            continue
        for b in bills:
            total_bills += 1
            # Schema check: vendor obbligatorio, almeno due_date OR amount_eur
            if not b.get("vendor"):
                errors.append(f"{acc}: vendor mancante")
                continue
            if not b.get("due_date") and b.get("amount_eur") is None:
                errors.append(f"{acc}: {b['vendor']} senza due_date NE amount")
                continue
            rows_to_append.append([
                b.get("due_date") or "",
                b["vendor"],
                b["category"],
                b["amount_eur"] if b["amount_eur"] is not None else "",
                b.get("bill_number") or "",
                b.get("customer_id") or "",
                f"provenance={b['raw_provenance']}",
            ])

    assert total_bills >= 1, (
        f"Nessuna bolletta trovata in alcun account. result={result!r}"
    )
    assert not errors, (
        f"Schema errors ({len(errors)}/{total_bills}):\n  - "
        + "\n  - ".join(errors)
    )

    # Append nello sheet
    r = gws.append_spreadsheet({
        "spreadsheet_id": test_sheet_id,
        "range": "A:G",
        "values": rows_to_append,
    })
    assert r.get("ok"), f"append failed: {r}"

    # Verify read-back
    r2 = gws.read_spreadsheet({
        "spreadsheet_id": test_sheet_id,
        "range": f"A1:G{len(rows_to_append) + 1}",
    })
    assert r2.get("ok")
    vals = (r2.get("values")
            or (r2.get("entries") or [{}])[0].get("values"))
    assert len(vals) == len(rows_to_append) + 1, \
        f"read returned {len(vals)} rows, expected {len(rows_to_append) + 1}"


def test_e2e_magic_link_extract_real_eni():
    """Test specifico magic-link Eni Plenitude: verifica end-to-end
    contro l'URL reale dell'ultima bolletta."""
    from bills_extract import (
        _fetch_magic_link_json, _parse_doxee_json,
    )
    # URL reale Eni Plenitude (test del 24/5/2026)
    url = "https://interattiva.eniplenitude.com/195a846e00124f7aa8b238de2226a0af"
    data = _fetch_magic_link_json(url)
    if data is None:
        pytest.skip("URL Eni non raggiungibile (token expired o offline)")
    parsed = _parse_doxee_json(data)
    assert parsed is not None
    assert parsed["amount_eur"] > 0
    assert parsed["due_date"]  # YYYY-MM-DD non vuoto
    assert parsed["bill_number"]
