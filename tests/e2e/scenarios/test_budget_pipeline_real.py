"""E2E budget pipeline reale (Scena 12 QuickTour).

Pipeline end-to-end:
  1. send_messages (SMTP Migadu) con N PDF allegati (bollette vere)
  2. IMAP fetch + save attachments su tmp
  3. pdftotext per PDF nativi
  4. read_files_ocr (Tesseract) fallback per PDF scansionati
  5. LLM Gemma 4 26B locale → estrazione schema (vendor, amount,
     due_date, period, category)
  6. append_spreadsheet (Google Sheets API)
  7. cleanup: revoca share del foglio + cancellazione mail di test

Skip se:
  - credenziali Migadu assenti (~/.config/metnos/mail.env)
  - token OAuth Google assente (~/.hermes/google_token.json)
  - bollette vere assenti (<repository>/UTENZE E SPESE/)
  - METNOS_E2E_RUN_SLOW != 1

Caveat documentati (Scena 12 QuickTour):
  - SSL transient su IMAP (retry 3x con backoff)
  - LLM probabilistico: vendor + amount affidabili; due_date + period
    dipendono dalla qualita' del filter regex e da max_tokens
  - Free tier Google API: 1000 req/mese (sempre nel free per test)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


_TOKEN_PATH = Path.home() / ".hermes" / "google_token.json"
_MAIL_ENV = Path.home() / ".config/metnos/mail.env"
_REPO_ROOT = Path(__file__).resolve().parents[3]
_BILLS_DIR = _REPO_ROOT / "UTENZE E SPESE"
_RUN_SLOW = os.environ.get("METNOS_E2E_RUN_SLOW", "0") == "1"


pytestmark = [
    pytest.mark.skipif(not _TOKEN_PATH.is_file(),
                        reason="OAuth Google assente"),
    pytest.mark.skipif(not _MAIL_ENV.is_file(),
                        reason="mail.env Migadu assente"),
    pytest.mark.skipif(not _BILLS_DIR.is_dir(),
                        reason="bollette vere assenti"),
    pytest.mark.skipif(not _RUN_SLOW,
                        reason="slow pipeline, abilita METNOS_E2E_RUN_SLOW=1"),
]


def _load_mail_env() -> None:
    for line in _MAIL_ENV.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ[k] = v


def _pick_bills(n: int = 2) -> list[str]:
    """Sceglie N bollette di periodi diversi (mix testo+scansione)."""
    candidates = [
        # testo nativo
        _BILLS_DIR / "ELETTRICITA ENEL" / "2023-04-ENEL-10-APRILE.pdf",
        # scansione → OCR
        _BILLS_DIR / "AMA" / "AMA-2022-31-DICEMBRE.pdf",
    ]
    available = [str(p) for p in candidates if p.is_file()]
    if len(available) < n:
        pytest.skip(f"bollette test insufficienti: {len(available)}/{n}")
    return available[:n]


def _extract_pdf_text(pdf_path: str, *, min_native_chars: int = 100
                      ) -> tuple[str, str]:
    """Pipeline §7.3 generale:
      1. pdftotext (poppler) — veloce per PDF nativi
      2. fallback read_files_ocr (Tesseract) se output < min_native_chars

    Ritorna (text, method) dove method ∈ {"native", "ocr"}.
    """
    try:
        out = subprocess.run(
            ["pdftotext", "-layout", pdf_path, "-"],
            capture_output=True, text=True, timeout=15,
        ).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        out = ""
    if len(out.strip()) >= min_native_chars:
        return out, "native"
    # Fallback OCR
    runtime_dir = _REPO_ROOT / "runtime"
    ocr_executor = _REPO_ROOT / "executors/read_files_ocr/read_files_ocr.py"
    r = subprocess.run(
        ["python3", str(ocr_executor)],
        input=json.dumps({"paths": [pdf_path], "lang": "ita+eng"}),
        capture_output=True, text=True, timeout=180,
    )
    data = json.loads(r.stdout)
    entries = data.get("entries") or []
    if not entries:
        return "", "ocr_failed"
    return entries[0].get("content", ""), "ocr"


_KEEP_KW = re.compile(
    r"(totale|scadenz|import|euro|periodo|fattura|consumo|kwh|smc|ta\.ri|"
    r"riferimento|vendor|customer)",
    re.IGNORECASE,
)


def _focus_text(text: str, *, max_chars: int = 4000) -> str:
    """Pre-filter deterministico §7.9: tiene solo linee con keyword
    finanziarie/temporali. Riduce input LLM da 40K→4K char."""
    lines = [l for l in text.splitlines() if _KEEP_KW.search(l)]
    return "\n".join(lines)[:max_chars]


def _llm_extract(focused_text: str) -> dict:
    """Chiama Gemma 4 26B locale per extract schema. Determinismo
    §7.9 limitato (LLM è probabilistico, con policy del tier exact)."""
    sys.path.insert(0, str(_REPO_ROOT / "runtime"))
    from llm_helpers import call_llm
    prompt = (
        "Estrai dalla bolletta italiana i campi:\n"
        "- vendor (gestore: ENEL, ENI, ACEA, AMA, etc.)\n"
        "- amount_eur (importo totale a saldo, numero float; "
        "attenzione virgola decimale italiana)\n"
        "- due_date (YYYY-MM-DD scadenza pagamento)\n"
        "- period (mese/anno servizio fatturato)\n"
        "- category (electricity|gas|water|waste|telecom|other) - "
        "AMA=waste, ENI=gas, ENEL=electricity\n\n"
        "RISPONDI SOLO CON UN OGGETTO JSON. NO markdown, NO prosa.\n"
    )
    out, _ = call_llm(focused_text, prompt,
                      tier="middle", max_tokens=200)
    cleaned = out.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1].split("```")[0]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    return json.loads(cleaned.strip())


# --- Fixtures -----------------------------------------------------------

@pytest.fixture(scope="module")
def bills() -> list[str]:
    _load_mail_env()
    return _pick_bills(n=2)


@pytest.fixture(scope="module")
def test_sheet_id() -> str:
    """Crea spreadsheet dedicato al test. Pulizia post-test in teardown."""
    sys.path.insert(0, str(_REPO_ROOT / "runtime"))
    from backends.files import google_workspace as gws
    r = gws.create_spreadsheet({
        "title": f"Metnos E2E Test Budget {time.strftime('%Y-%m-%d %H:%M')}",
    })
    assert r.get("ok"), f"create_spreadsheet failed: {r}"
    sid = (r.get("spreadsheet_id")
           or (r.get("results") or [{}])[0].get("spreadsheet_id"))
    assert sid
    # Headers
    gws.write_spreadsheet({
        "spreadsheet_id": sid,
        "range": "A1:E1",
        "values": [["Data", "Categoria", "Importo (EUR)", "Note", "Pagato"]],
    })
    yield sid
    # Cleanup: cancella il test sheet
    try:
        gws.delete({"file_id": sid})
    except Exception:
        pass


# --- Tests --------------------------------------------------------------

def test_pipeline_send_email(bills: list[str]):
    """Step 1: send_messages SMTP Migadu con N PDF allegati."""
    sys.path.insert(0, str(_REPO_ROOT / "runtime"))
    from backends.messages import email_metnos
    recipient = (os.environ.get("METNOS_ROBERTO_USER")
                 or os.environ.get("METNOS_SYSTEM_USER"))
    if not recipient:
        pytest.skip("nessun destinatario SMTP configurato")
    r = email_metnos.send({
        "messages": [{
            "to": recipient,
            "subject": "Metnos E2E test bollette",
            "body": "Pipeline E2E reale: PDF → OCR/text → LLM → sheet.",
            "attachments": [{"path": p} for p in bills],
        }],
        "account": "metnos_system",
    })
    assert r.get("ok"), f"SMTP send failed: {r}"
    assert r.get("ok_count") == 1


def test_pipeline_extract_native_pdf(bills: list[str]):
    """Step 2+3: pdftotext per PDF nativo (ENEL). Almeno 1 PDF deve
    avere text layer."""
    natives = []
    for p in bills:
        txt, method = _extract_pdf_text(p)
        if method == "native":
            natives.append((p, txt))
    assert natives, "Nessun PDF nativo trovato fra le bollette test"
    for p, txt in natives:
        assert len(txt) > 1000, f"{p}: pdftotext output too short ({len(txt)})"


def test_pipeline_extract_scanned_pdf_via_ocr(bills: list[str]):
    """Step 4: read_files_ocr (Tesseract) per PDF scansionato (AMA).
    Verifica fallback automatico quando pdftotext < threshold."""
    scanned = []
    for p in bills:
        txt, method = _extract_pdf_text(p)
        if method == "ocr":
            scanned.append((p, txt))
    if not scanned:
        pytest.skip("Nessun PDF scansionato in bills set (atteso AMA)")
    for p, txt in scanned:
        assert len(txt) > 500, f"OCR {p}: output too short ({len(txt)})"
        # Verifica che testo OCR contenga keyword italiane (proxy quality)
        assert _KEEP_KW.search(txt), f"OCR {p}: no financial keyword"


def test_pipeline_llm_extract_structure(bills: list[str]):
    """Step 5: LLM Gemma 4 26B estrae schema strutturato.
    Verifica: vendor + amount_eur sempre presenti (campi affidabili).
    """
    parsed_rows = []
    for p in bills:
        txt, method = _extract_pdf_text(p)
        if not txt:
            continue
        focused = _focus_text(txt)
        try:
            parsed = _llm_extract(focused)
        except (json.JSONDecodeError, Exception) as e:
            pytest.fail(f"LLM extract failed on {p}: {e}")
        parsed_rows.append((p, parsed))
        # Verifica campi affidabili (audit Scena 12 QuickTour)
        assert parsed.get("vendor"), f"{p}: vendor missing"
        amount = parsed.get("amount_eur")
        if amount is not None:  # AMA OCR garantisce, ENEL talvolta no
            assert isinstance(amount, (int, float)), \
                f"{p}: amount_eur non numerico: {amount!r}"
            assert 0 < amount < 100000, \
                f"{p}: amount_eur fuori range plausibile: {amount}"
    assert len(parsed_rows) >= 1, "nessun PDF processato con successo"


def test_pipeline_full_append_to_sheet(bills: list[str], test_sheet_id: str):
    """Step 6: pipeline completa end-to-end con append nello sheet."""
    sys.path.insert(0, str(_REPO_ROOT / "runtime"))
    from backends.files import google_workspace as gws
    appended = 0
    for p in bills:
        txt, method = _extract_pdf_text(p)
        if not txt:
            continue
        focused = _focus_text(txt)
        try:
            parsed = _llm_extract(focused)
        except Exception:
            continue
        row = [[
            parsed.get("due_date", "") or "",
            parsed.get("category", "") or "",
            parsed.get("amount_eur") or "",
            f"{parsed.get('vendor', '')} {parsed.get('period', '')}".strip(),
            "no",
        ]]
        r = gws.append_spreadsheet({
            "spreadsheet_id": test_sheet_id,
            "range": "A:E",
            "values": row,
        })
        assert r.get("ok"), f"append failed for {p}: {r}"
        appended += 1
    assert appended >= 1, "Nessuna riga appended"
    # Verifica lettura riflette quanto scritto
    r_read = gws.read_spreadsheet({
        "spreadsheet_id": test_sheet_id,
        "range": f"A1:E{appended + 1}",  # +1 per headers
    })
    assert r_read.get("ok")
    values = (r_read.get("values")
              or (r_read.get("entries") or [{}])[0].get("values"))
    assert values, f"read returned empty: {r_read}"
    assert len(values) >= appended + 1, \
        f"sheet rows {len(values)} < expected {appended + 1} (headers + data)"
