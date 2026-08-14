# SPDX-License-Identifier: AGPL-3.0-only
"""Bounded second-factor resolvers used inside the sites broker.

The planner never sees a factor value.  A resolver receives a narrow request,
uses an explicitly authorized channel, and returns only to credential injection.
Email is the first implementation; other channels can implement the same
``prepare`` / ``resolve`` contract without changing ``login_sites``.
"""
from __future__ import annotations

import asyncio
import imaplib
import re
import time
from dataclasses import dataclass, field
from email.utils import parseaddr, parsedate_to_datetime


EMAIL_FACTOR_SCOPE = "sites.read"
_EMAIL_IO_TIMEOUT_S = 3.0
_EMAIL_PREPARE_WALL_S = 15.0
_EMAIL_POLL_WALL_S = 18.0
_EMAIL_POLL_INTERVAL_S = 1.5
_MAX_FACTOR_FOLDERS = 3
_MAX_NEW_MESSAGES = 8
_MAX_MESSAGE_BYTES = 65_536

_EMAIL_PAGE_RE = re.compile(
    r"\b(?:e[- ]?mail|email address|sent to|posta elettronica|posta)\b",
    re.IGNORECASE,
)
_FACTOR_WORD_RE = re.compile(
    r"\b(?:verification|verify|security|one[- ]?time|otp|passcode|"
    r"sign[- ]?in|login|access|codice|verifica|sicurezza|accesso|"
    r"conferma)\b",
    re.IGNORECASE,
)
_CODE_PATTERNS = (
    re.compile(
        r"\b(?:verification|security|one[- ]?time|access|login)?\s*"
        r"(?:code|codice|passcode|otp)\s*(?:is|e|\u00e8|:|-)\s*"
        r"([A-Za-z0-9]{4,12})\b", re.IGNORECASE),
    re.compile(
        r"\b([A-Za-z0-9]{4,12})\b\s+(?:is|e|\u00e8)\s+"
        r"(?:your|il tuo)\s+(?:verification\s+)?"
        r"(?:code|codice|passcode|otp)\b", re.IGNORECASE),
    re.compile(
        r"\b([A-Za-z0-9]{4,12})\b\s*[-:]\s*"
        r"(?:verification|security|one[- ]?time|access)\s+"
        r"(?:code|codice)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:verification|security|one[- ]?time|access|login)?\s*"
        r"(?:code|codice|passcode|otp)\s+"
        r"([A-Za-z0-9]{4,12})\b", re.IGNORECASE),
)
_NON_CODES = frozenset({
    "access", "accesso", "codice", "code", "confirm", "conferma",
    "email", "login", "passcode", "security", "sicurezza", "verify",
    "verifica", "verification",
})


@dataclass(frozen=True)
class FactorResolution:
    status: str
    code: str | None = None
    diagnostics: dict | None = field(default=None, compare=False)


@dataclass(frozen=True)
class _CodeCandidate:
    value: str
    confidence: int
    source_rank: int


def is_email_factor_page(text: str) -> bool:
    return bool(_EMAIL_PAGE_RE.search(str(text or "")))


def _exact_mailbox(address: str) -> str | None:
    try:
        from mail_client import exact_account_for_address
        return exact_account_for_address(address)
    except Exception:
        return None


def _parse_folder(raw) -> tuple[str, str] | None:
    line = (raw.decode("utf-8", "replace")
            if isinstance(raw, (bytes, bytearray)) else str(raw or ""))
    match = re.match(
        r'\((?P<flags>[^)]*)\)\s+(?:"[^"]*"|\S+)\s+'
        r'(?P<name>"[^"]+"|\S+)\s*$', line)
    if not match:
        return None
    return (match.group("name").strip().strip('"'),
            match.group("flags").casefold())


def _factor_folders(conn) -> list[str]:
    folders = ["INBOX"]
    try:
        status, rows = conn.list()
    except Exception:
        return folders
    if status != "OK":
        return folders
    for raw in rows or ():
        parsed = _parse_folder(raw)
        if not parsed:
            continue
        name, flags = parsed
        normalized = name.casefold()
        if (name and ("\\junk" in flags or "junk" in normalized
                      or "spam" in normalized)
                and name not in folders):
            folders.append(name)
        if len(folders) >= _MAX_FACTOR_FOLDERS:
            break
    return folders


def _uid_list(conn, folder: str) -> list[int]:
    status, _ = conn.select(folder, readonly=True)
    if status != "OK":
        return []
    status, data = conn.uid("SEARCH", "ALL")
    if status != "OK" or not data or not data[0]:
        return []
    out = []
    for raw in data[0].split():
        try:
            out.append(int(raw))
        except (TypeError, ValueError):
            continue
    return out


def _close_imap(conn) -> None:
    try:
        conn.close()
    except Exception:
        pass
    try:
        conn.logout()
    except Exception:
        pass


def _prepare_email_cursor_sync(address: str) -> dict | None:
    account = _exact_mailbox(address)
    if not account:
        return None
    try:
        from mail_client import open_imap
        conn = open_imap(
            account, timeout_s=_EMAIL_IO_TIMEOUT_S, attempts=1)
    except Exception:
        return None
    try:
        folders = _factor_folders(conn)
        cursor = {}
        for folder in folders:
            uids = _uid_list(conn, folder)
            cursor[folder] = max(uids, default=0)
        return {
            "account": account,
            "folders": cursor,
            "prepared_at": time.time(),
        }
    finally:
        _close_imap(conn)


async def prepare_email_factor(address: str, *, allowed: bool) -> dict | None:
    """Capture mailbox UIDs before an authentication submit.

    New UID correlation is stronger than sender-controlled Date headers and
    prevents a previous login code from being reused accidentally.
    """
    if not allowed or not isinstance(address, str) or "@" not in address:
        return None
    if not _exact_mailbox(address):
        return None
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_prepare_email_cursor_sync, address),
            timeout=_EMAIL_PREPARE_WALL_S)
    except Exception:
        return None


def _message_payload(raw) -> tuple[bytes, bytes]:
    metadata = b""
    payload = b""
    for item in raw or ():
        if not isinstance(item, tuple) or len(item) < 2:
            continue
        head, body = item[0], item[1]
        if isinstance(head, (bytes, bytearray)):
            metadata += bytes(head)
        if isinstance(body, (bytes, bytearray)):
            payload += bytes(body)
    return metadata, payload


def _received_at(metadata: bytes, declared_date: str) -> float:
    try:
        parsed = imaplib.Internaldate2tuple(metadata)
        if parsed:
            return time.mktime(parsed)
    except Exception:
        pass
    try:
        return parsedate_to_datetime(str(declared_date or "")).timestamp()
    except Exception:
        return 0.0


def _issuer_relevance(issuer_domain: str, sender: str,
                      subject: str, body: str) -> int:
    issuer = str(issuer_domain or "").strip().rstrip(".").casefold()
    if not issuer:
        return 0
    sender_address = parseaddr(str(sender or ""))[1].casefold()
    sender_domain = sender_address.rsplit("@", 1)[-1] if "@" in sender_address else ""
    score = 0
    if sender_domain == issuer or sender_domain.endswith("." + issuer):
        score += 5
    combined = f"{sender} {subject} {body}".casefold()
    if issuer in combined:
        score += 3
    brand = issuer.split(".", 1)[0]
    if len(brand) >= 4 and re.search(
            rf"(?<![a-z0-9]){re.escape(brand)}(?![a-z0-9])",
            f"{sender} {subject}".casefold()):
        score += 2
    return score


def _extract_code_candidates(text: str, *, source: str) -> list[_CodeCandidate]:
    """Extract contextual codes without treating every word after `code` as one.

    Rules with an explicit connector (``code: X`` / ``X is your code``) are
    stronger than a bare adjacent token.  A bare alphabetic word is never
    sufficient evidence: phrases such as ``code with ...`` otherwise turn
    ordinary prose into a second, false factor candidate.
    """
    source_rank = 2 if source == "subject" else 1
    best: dict[str, _CodeCandidate] = {}
    for pattern_index, pattern in enumerate(_CODE_PATTERNS):
        confidence = 4 if pattern_index in {0, 1, 2} else 2
        for match in pattern.finditer(text):
            code = str(match.group(1) or "").strip()
            if (not code or code.casefold() in _NON_CODES
                    or (confidence < 4 and code.isalpha())):
                continue
            candidate = _CodeCandidate(code, confidence, source_rank)
            previous = best.get(code)
            if previous is None or (
                    candidate.confidence, candidate.source_rank) > (
                        previous.confidence, previous.source_rank):
                best[code] = candidate
    return list(best.values())


def _extract_codes(text: str) -> list[str]:
    """Compatibility helper used by tests and non-ranking callers."""
    return [item.value for item in _extract_code_candidates(
        text, source="body")]


def _code_matches_form(code: str, *, expected_length: int | None,
                       numeric_only: bool) -> bool:
    if expected_length and len(code) != expected_length:
        return False
    if numeric_only and not code.isdigit():
        return False
    return True


def _poll_email_factor_sync(address: str, issuer_domain: str,
                            requested_at: float,
                            cursor: dict | None, *,
                            expected_length: int | None = None,
                            numeric_only: bool = False) -> FactorResolution:
    account = _exact_mailbox(address)
    if not account:
        return FactorResolution("unavailable")
    if cursor and cursor.get("account") != account:
        return FactorResolution("unavailable")
    try:
        from mail_client import open_imap, parse_envelope
        conn = open_imap(
            account, timeout_s=_EMAIL_IO_TIMEOUT_S, attempts=1)
    except Exception:
        return FactorResolution("io_error")
    candidate_messages = []
    relevant_messages = 0
    candidate_count = 0
    try:
        baselines = dict((cursor or {}).get("folders") or {})
        folders = list(baselines) or _factor_folders(conn)
        for folder_rank, folder in enumerate(folders[:_MAX_FACTOR_FOLDERS]):
            uids = _uid_list(conn, folder)
            baseline = int(baselines.get(folder, 0))
            if cursor:
                uids = [uid for uid in uids if uid > baseline]
            uids = uids[-_MAX_NEW_MESSAGES:]
            for uid in reversed(uids):
                try:
                    status, raw = conn.uid(
                        "FETCH", str(uid),
                        f"(INTERNALDATE BODY.PEEK[]<0.{_MAX_MESSAGE_BYTES}>)")
                except Exception:
                    continue
                if status != "OK":
                    continue
                metadata, payload = _message_payload(raw)
                if not payload:
                    continue
                try:
                    envelope = parse_envelope(payload)
                except Exception:
                    continue
                received = _received_at(
                    metadata, str(envelope.get("date") or ""))
                # UID is authoritative when a pre-submit cursor exists.  The
                # timestamp fallback tolerates normal sender/server clock skew.
                if (not cursor and requested_at
                        and received < requested_at - 120):
                    continue
                subject = str(envelope.get("subject") or "")
                body = str(envelope.get("body_preview") or "")
                sender = str(envelope.get("from") or "")
                text = f"{subject} {body} {sender}"
                if not _FACTOR_WORD_RE.search(text):
                    continue
                issuer_score = _issuer_relevance(
                    issuer_domain, sender, subject, body)
                if issuer_score <= 0:
                    continue
                relevant_messages += 1
                extracted = (
                    _extract_code_candidates(subject, source="subject")
                    + _extract_code_candidates(body, source="body"))
                by_code: dict[str, _CodeCandidate] = {}
                for candidate in extracted:
                    if not _code_matches_form(
                            candidate.value,
                            expected_length=expected_length,
                            numeric_only=numeric_only):
                        continue
                    previous = by_code.get(candidate.value)
                    if previous is None or (
                            candidate.confidence, candidate.source_rank) > (
                                previous.confidence, previous.source_rank):
                        by_code[candidate.value] = candidate
                candidate_count += len(by_code)
                if not by_code:
                    continue
                ranked = sorted(
                    by_code.values(),
                    key=lambda item: (
                        item.confidence, item.source_rank, item.value),
                    reverse=True)
                top_strength = (ranked[0].confidence, ranked[0].source_rank)
                top_codes = {
                    item.value for item in ranked
                    if (item.confidence, item.source_rank) == top_strength
                }
                candidate_messages.append({
                    "rank": (issuer_score, received, -folder_rank, uid),
                    "codes": top_codes,
                    "strength": top_strength,
                })
    finally:
        _close_imap(conn)
    diagnostics = {
        "relevant_messages": relevant_messages,
        "candidate_messages": len(candidate_messages),
        "candidate_count": candidate_count,
        "expected_length": int(expected_length or 0),
        "numeric_only": bool(numeric_only),
    }
    if not candidate_messages:
        return FactorResolution("missing", diagnostics=diagnostics)
    candidate_messages.sort(key=lambda item: item["rank"], reverse=True)
    top = candidate_messages[0]
    # Two messages with the same issuer confidence and server timestamp are
    # causally indistinguishable across folders.  Accept only if they agree.
    tied_codes = set(top["codes"])
    for item in candidate_messages[1:]:
        if item["rank"][:2] != top["rank"][:2]:
            break
        tied_codes.update(item["codes"])
    diagnostics["top_tie_count"] = len(tied_codes)
    if len(tied_codes) != 1:
        return FactorResolution("ambiguous", diagnostics=diagnostics)
    return FactorResolution("found", next(iter(tied_codes)), diagnostics)


async def resolve_email_factor(*, page_text: str, address: str,
                               issuer_domain: str, requested_at: float,
                               cursor: dict | None, allowed: bool,
                               wait_s: float,
                               expected_length: int | None = None,
                               numeric_only: bool = False) -> FactorResolution:
    if (not allowed or not is_email_factor_page(page_text)
            or not _exact_mailbox(address)):
        return FactorResolution("unavailable")
    deadline = time.monotonic() + max(0.0, min(30.0, float(wait_s)))
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return FactorResolution("timeout")
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    _poll_email_factor_sync, address, issuer_domain,
                    requested_at, cursor,
                    expected_length=expected_length,
                    numeric_only=numeric_only),
                timeout=min(_EMAIL_POLL_WALL_S, remaining))
        except asyncio.TimeoutError:
            return FactorResolution("io_timeout")
        except Exception:
            return FactorResolution("io_error")
        if result.status in {"found", "ambiguous", "unavailable"}:
            return result
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return FactorResolution("timeout")
        await asyncio.sleep(min(_EMAIL_POLL_INTERVAL_S, remaining))
