"""bills_extract — estrazione strutturata bollette utility/telecom italiane.

Sub-feature della skill bundle `it_locale` (ADR 0160): pattern bundle-per-locale,
aggregata con altre future feature italiane (codice fiscale, IBAN IT, P.IVA, ecc.).

Pattern §7.3 generale: una sola pipeline che gestisce N provider, no
hardcoding del singolo caso. Constants table-driven via `vendors.json`
nel parent della skill.

Provenance ordering (priorità):
  1. magic-link JSON embedded (interattiva.<vendor>.com/<uuid>) — best
     accuracy, no auth, deterministic. Pattern Doxee/Engie comune.
  2. PDF allegato testuale (pdftotext nativo) — qualita' media.
  3. PDF allegato scansionato (Tesseract OCR via read_files_ocr) —
     lento ma copre legacy.
  4. HTML body LLM extract (Gemma 4 26B locale) — fallback.

Schema output stabile:
  {
    "vendor": str,                     # nome canonical (Eni Plenitude, Fastweb, ...)
    "category": str,                   # gas|electricity|water|waste|telecom|other
    "amount_eur": float | None,        # importo
    "due_date": str (YYYY-MM-DD),      # scadenza
    "bill_number": str | None,
    "customer_id": str | None,
    "period": str | None,
    "raw_provenance": str,             # "magic_link" | "pdf_native" | "ocr" | "html_llm"
    "consumption": dict | None,        # opzionale: smc gas, kWh luce, gb dati
    "source_message_id": str,
  }

Determinismo §7.9: tutto deterministic, LLM solo per fallback HTML.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from email.message import Message
from pathlib import Path


# --- Vendor config: caricato lazy da vendors.json nella skill root --------

_SKILL_ROOT = Path(__file__).resolve().parent.parent  # .../it_locale/
_DEFAULT_VENDORS_PATH = _SKILL_ROOT / "vendors.json"


def _load_vendor_config() -> tuple[dict, re.Pattern]:
    """Carica vendors.json. Override env METNOS_BILLS_VENDORS_PATH per test."""
    path_override = os.environ.get("METNOS_BILLS_VENDORS_PATH")
    path = Path(path_override) if path_override else _DEFAULT_VENDORS_PATH
    if not path.is_file():
        return {}, re.compile(r"(?!x)x")  # never match
    data = json.loads(path.read_text(encoding="utf-8"))
    vendors = data.get("vendors") or {}
    not_bill = re.compile(data.get("not_bill_subject_pattern", ""),
                          re.IGNORECASE)
    return vendors, not_bill


VENDOR_WHITELIST, _NOT_BILL_HINTS = _load_vendor_config()


def _classify_sender(from_header: str) -> dict | None:
    """Match FROM header contro VENDOR_WHITELIST. Ritorna metadata o None."""
    if not from_header:
        return None
    lower = from_header.lower()
    for domain, meta in VENDOR_WHITELIST.items():
        if domain in lower:
            return meta
    return None


def _is_marketing(subject: str) -> bool:
    return bool(_NOT_BILL_HINTS.search(subject or ""))


def _strip_html(html: str) -> str:
    text = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.S)
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_magic_link(html: str, pattern: str) -> str | None:
    if not (html and pattern):
        return None
    m = re.search(pattern, html)
    return m.group(0) if m else None


def _fetch_magic_link_json(url: str, *, timeout_s: int = 15) -> dict | None:
    """GET URL + cerca `<script id="doc-data">{...}</script>` con
    JSON strutturato (pattern Eni Plenitude/Engie/Doxee). Ritorna dict
    JSON parsed o None."""
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        html = urllib.request.urlopen(req, timeout=timeout_s).read().decode("utf-8", errors="replace")
    except Exception:
        return None
    m = re.search(
        r'<script\s+type="text/plain"\s+id="doc-data">\s*(\{.*?\})\s*</script>',
        html, re.S,
    )
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def _parse_doxee_json(data: dict) -> dict | None:
    """Estrae campi billing dal blob JSON Doxee/Engie (Eni Plenitude pattern).
    Riusabile per qualunque provider che usi questo schema."""
    try:
        doc = data["FILE"]["DOCUMENT"][0]
        template = doc["TEMPLATE"][0]
        fattura = template["DATA"][0].get("fattura", [{}])[0]
        utente = template["DATA"][0].get("utente", [{}])[0]
        return {
            "amount_eur": float(template.get("Importo")
                                 or fattura.get("importo", "0").replace(",", ".") or 0),
            "due_date": fattura.get("data_scadenza_cal", "").replace("/", "-"),
            "bill_number": str(fattura.get("numero", "")) or None,
            "customer_id": str(utente.get("cod_cliente", "")) or None,
            "period": (f"{fattura.get('data_riferimento_dal')} → "
                        f"{fattura.get('data_riferimento_a')}"
                        if fattura.get("data_riferimento_dal") else None),
            "consumption": (
                {"smc_gas": template["DATA"][0]["gas"][0]["utilizzo"][0].get("consumo")}
                if template["DATA"][0].get("gas") else None
            ),
        }
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _extract_html_body(msg: Message) -> str | None:
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            try:
                return part.get_payload(decode=True).decode("utf-8", errors="replace")
            except Exception:
                continue
    return None


def _extract_text_body(msg: Message) -> str | None:
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            try:
                return part.get_payload(decode=True).decode("utf-8", errors="replace")
            except Exception:
                continue
    return None


def _save_pdf_attachments(msg: Message, out_dir: Path) -> list[str]:
    """Salva tutti i PDF allegati in out_dir. Ritorna lista path."""
    saved = []
    out_dir.mkdir(parents=True, exist_ok=True)
    for part in msg.walk():
        if part.get_content_disposition() != "attachment":
            continue
        fn = part.get_filename() or ""
        if not fn.lower().endswith(".pdf"):
            continue
        try:
            data = part.get_payload(decode=True)
            p = out_dir / fn
            p.write_bytes(data)
            saved.append(str(p))
        except Exception:
            continue
    return saved


def _pdf_text(pdf_path: str, *, min_native_chars: int = 100) -> tuple[str, str]:
    """pdftotext + fallback OCR Tesseract via read_files_ocr executor.
    Ritorna (text, method) dove method ∈ {'pdf_native', 'ocr', 'failed'}."""
    try:
        out = subprocess.run(
            ["pdftotext", "-layout", pdf_path, "-"],
            capture_output=True, text=True, timeout=15,
        ).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        out = ""
    if len(out.strip()) >= min_native_chars:
        return out, "pdf_native"
    # OCR fallback — rename-resilient via C.PATH_EXECUTORS (§7.11)
    try:
        try:
            import config as _C  # type: ignore
            ocr_exec = _C.PATH_EXECUTORS / "read_files_ocr" / "read_files_ocr.py"
        except Exception:
            # Skill runs standalone (es. test diretto) → resolve via _SKILL_ROOT
            ocr_exec = _SKILL_ROOT.parent.parent / "read_files_ocr" / "read_files_ocr.py"
        if not ocr_exec.is_file():
            return "", "failed"
        r = subprocess.run(
            ["python3", str(ocr_exec)],
            input=json.dumps({"paths": [pdf_path], "lang": "ita+eng"}),
            capture_output=True, text=True, timeout=180,
        )
        data = json.loads(r.stdout or "{}")
        entries = data.get("entries") or []
        if entries:
            return entries[0].get("content", ""), "ocr"
    except Exception:
        pass
    return "", "failed"


_BILL_KEYWORDS = re.compile(
    r"(totale|scadenz|import|euro|periodo|fattura|consumo|kwh|smc|"
    r"ta\.ri|riferimento|emissione)",
    re.IGNORECASE,
)


def _focus_text(text: str, *, max_chars: int = 4000) -> str:
    lines = [l for l in text.splitlines() if _BILL_KEYWORDS.search(l)]
    return "\n".join(lines)[:max_chars] if lines else text[:max_chars]


def _llm_extract_bill(focused_text: str, vendor_hint: str) -> dict:
    """LLM Gemma 4 26B extract schema bolletta. Determinismo
    temperature=0; output sempre parsato come dict."""
    from llm_helpers import call_llm
    prompt = (
        f"Estrai dalla bolletta italiana di {vendor_hint} questi campi:\n"
        "- amount_eur (importo totale in EUR, float; virgola/punto decimale italiana)\n"
        "- due_date (YYYY-MM-DD scadenza pagamento)\n"
        "- bill_number (numero bolletta/fattura)\n"
        "- customer_id (numero cliente)\n"
        "- period (mese/anno servizio fatturato)\n\n"
        "RISPONDI SOLO CON UN OGGETTO JSON. NO markdown, NO prosa.\n"
        "Se un campo manca: null."
    )
    try:
        out, _ = call_llm(focused_text, prompt, tier="middle",
                          max_tokens=200, temperature=0.0)
    except Exception:
        return {}
    cleaned = out.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1].split("```")[0]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    try:
        return json.loads(cleaned.strip())
    except json.JSONDecodeError:
        return {}


def extract_bill_from_email(msg: Message, *,
                              tmp_attach_dir: Path | None = None) -> dict | None:
    """Pipeline universale §7.3:
      1. Classifica sender vs VENDOR_WHITELIST → meta o None.
      2. Magic-link JSON se pattern dichiarato e link presente.
      3. PDF allegato testuale (pdftotext).
      4. PDF allegato scansionato (OCR).
      5. HTML body LLM.

    Ritorna dict schema stabile o None se la mail NON e' una bolletta.
    Idempotente, no side-effects salvo PDF attach save in tmp_attach_dir.
    """
    from_h = str(msg.get("From", ""))
    subject_h = str(msg.get("Subject", ""))
    meta = _classify_sender(from_h)
    if meta is None:
        return None
    if _is_marketing(subject_h):
        return None

    result = {
        "vendor": meta["vendor"],
        "category": meta["category"],
        "amount_eur": None,
        "due_date": None,
        "bill_number": None,
        "customer_id": None,
        "period": None,
        "consumption": None,
        "raw_provenance": "unknown",
        "source_message_id": str(msg.get("Message-ID", "")),
    }

    html = _extract_html_body(msg)

    # Strategy 1: magic-link JSON
    pattern = meta.get("magic_link_pattern")
    if pattern and html:
        url = _extract_magic_link(html, pattern)
        if url:
            data = _fetch_magic_link_json(url)
            if data:
                parsed = _parse_doxee_json(data)
                if parsed:
                    result.update(parsed)
                    result["raw_provenance"] = "magic_link"
                    return result

    # Strategy 2+3: PDF allegato (pdftotext or OCR)
    if tmp_attach_dir is None:
        tmp_attach_dir = Path(tempfile.mkdtemp(prefix="bills_extract_"))
    pdfs = _save_pdf_attachments(msg, tmp_attach_dir)
    for pdf in pdfs:
        text, method = _pdf_text(pdf)
        if method == "failed":
            continue
        focused = _focus_text(text)
        parsed = _llm_extract_bill(focused, meta["vendor"])
        if parsed.get("amount_eur") or parsed.get("due_date"):
            result.update({k: v for k, v in parsed.items() if v is not None})
            result["raw_provenance"] = method
            return result

    # Strategy 4: HTML body LLM
    if html:
        body_text = _strip_html(html)
        focused = body_text[:4000]
        parsed = _llm_extract_bill(focused, meta["vendor"])
        if parsed.get("amount_eur") or parsed.get("due_date"):
            result.update({k: v for k, v in parsed.items() if v is not None})
            result["raw_provenance"] = "html_llm"
            return result

    # Strategy 5: text/plain body LLM (last resort)
    text_body = _extract_text_body(msg)
    if text_body:
        focused = text_body[:4000]
        parsed = _llm_extract_bill(focused, meta["vendor"])
        if parsed.get("amount_eur") or parsed.get("due_date"):
            result.update({k: v for k, v in parsed.items() if v is not None})
            result["raw_provenance"] = "text_llm"
            return result

    # Niente extract riuscito ma identificato come bolletta
    result["raw_provenance"] = "identified_only"
    return result


def bills_in_account(account: str, *, time_window: str = "last-90d",
                     max_results: int = 200) -> list[dict]:
    """Itera mail dell'account, estrae bolletta per ognuna che matcha
    VENDOR_WHITELIST. Ritorna lista schema stabile.

    Usa `mail_client.open_imap` per single source of truth credenziali
    (riusa fallback dyn ~/.config/metnos/mail/<account>.env).
    """
    from mail_client import open_imap
    from datetime import datetime, timedelta
    import email as email_lib

    days_match = re.match(r"last-(\d+)d", time_window or "")
    days = int(days_match.group(1)) if days_match else 90
    since_date = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")

    conn = open_imap(account)
    try:
        conn.select("INBOX", readonly=True)
        whitelist_domains = list(VENDOR_WHITELIST.keys())
        # FROM filter: OR of all whitelist domains (IMAP query)
        froms = []
        for d in whitelist_domains:
            froms.append(f'(FROM "{d}")')
        # IMAP OR is binary; chain
        query = f'(SINCE "{since_date}")'
        # Per semplicita': scan ALL since, classify in Python (deterministic)
        typ, data = conn.search(None, query)
        ids = data[0].split()[:max_results]
        bills = []
        for mid in ids:
            try:
                typ, mdata = conn.fetch(mid, "(RFC822)")
                if not mdata or not mdata[0]:
                    continue
                msg = email_lib.message_from_bytes(mdata[0][1])
                extracted = extract_bill_from_email(msg)
                if extracted is not None:
                    bills.append(extracted)
            except Exception:
                continue
        return bills
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def all_bills_across_accounts(*, time_window: str = "last-90d") -> dict:
    """Scan tutti gli account configurati. Ritorna dict
    {account: [bills...]}. Skip silenziosamente account inaccessibili."""
    import config as _C

    accounts: list[str] = []
    # Built-in 3 (mail_client._account_creds first-class)
    for name in ("metnos_system", "metnos_secondary", "account_personal"):
        accounts.append(name)
    # Dynamic via mail/*.env
    mail_dir = _C.PATH_USER_CONFIG / "mail"
    if mail_dir.is_dir():
        for envp in sorted(mail_dir.glob("*.env")):
            accounts.append(envp.stem)

    out = {}
    for acc in accounts:
        try:
            bills = bills_in_account(acc, time_window=time_window)
            out[acc] = bills
        except Exception as e:
            out[acc] = {"error": str(e)[:200]}
    return out
