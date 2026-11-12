# SPDX-License-Identifier: MIT
"""The one decision of the person on a generated tool (ADR 0240).

A candidate that passed every automatic check waits for one answer in the
administration page. This module shows what is being decided and carries the
answer to the administrative signer; it holds no key and admits nothing.

The signer is a root-owned, socket-activated command installed once by the
administrator of the server. Without it the page says so; nothing else can
sign in its place.
"""
from __future__ import annotations

import json
import os
import re
import socket
import stat
from pathlib import Path

from logging_setup import get_logger

log = get_logger(__name__)

# A fixed system address, like the other administrative endpoints; an executor
# never sees it (``sandbox._FILESYSTEM_HOST_PRIVATE_PATHS``).
DECISION_SOCKET = Path("/run/metnos-synth-decision/socket")
# The review runs the cases in the isolated runner and asks the reviewer model.
DECISION_TIMEOUT_S = 1500
_MAX_REPLY = 4 * 1024 * 1024


def decision_socket() -> Path:
    return Path(os.environ.get("METNOS_SYNTH_DECISION_SOCKET") or DECISION_SOCKET)


def signer_available() -> bool:
    """True when the administrative signer is installed on this server."""
    try:
        return stat.S_ISSOCK(os.stat(decision_socket()).st_mode)
    except OSError:
        return False


def _manifest(proposals_dir: Path, proposal_id: str) -> tuple[dict, dict]:
    # The proposal store owns its entries: read through it, never by path.
    from synth_proposal_store import candidate_manifest

    return candidate_manifest(Path(proposals_dir), proposal_id)


def declared_cases(manifest: dict) -> list:
    """The cases the candidate was built on: born from the real requests that
    found no tool, shown to the person before the answer."""
    return [{"name": case.get("name"), "input": case.get("input"), "expect": case.get("expect")}
            for case in manifest.get("tests") or [] if isinstance(case, dict)]


def candidate_card(proposals_dir: Path, proposal_id: str, *, lang: str) -> dict:
    """What the person is asked to allow, in plain facts read from the candidate."""
    manifest, _metadata = _manifest(proposals_dir, proposal_id)
    description = manifest.get("description")
    if isinstance(description, dict):
        description = description.get(lang) or next(iter(description.values()), "")
    capabilities = [
        {"name": str(item.get("name") or ""),
         "hint": [str(hint) for hint in item.get("hint") or []]}
        for item in manifest.get("capabilities") or [] if isinstance(item, dict)]
    return {
        "proposal_id": proposal_id,
        "name": str(manifest.get("name") or ""),
        "description": " ".join(str(description or "").split()),
        "capabilities": capabilities,
        "revertible": manifest.get("revertible") is True,
        "cases": declared_cases(manifest),
    }


def discard_candidate(proposals_dir: Path, proposal_id: str) -> bool:
    """Remove a retained candidate that will not be decided again."""
    from synth_proposal_store import discard_candidate as _discard

    removed = _discard(Path(proposals_dir), proposal_id)
    if not removed:
        log.warning("synth_activation: candidate %s not removed", proposal_id[:12])
    return removed


def activate(proposals_dir: Path, proposal_id: str, *, by: str, at: str) -> dict:
    """Carry the person's «yes» to the signer and return what happened.

    The reply is the signer's own report: ``admitted`` with the publication,
    or the refusal with its reason. A missing or silent signer is an answer
    too, never an activation.
    """
    from synth_proposal_store import review_document

    manifest, _metadata = _manifest(proposals_dir, proposal_id)
    document = review_document(Path(proposals_dir), proposal_id, declared_cases(manifest))
    # The signer accepts a closed alphabet: an identity is recorded, never parsed.
    who = re.sub(r"[^A-Za-z0-9_.@:+-]", "_", str(by))[:120] or "admin"
    document["consent"] = {"decision": "approved", "by": who, "at": at}
    payload = json.dumps(document, ensure_ascii=False, allow_nan=False).encode("utf-8")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(DECISION_TIMEOUT_S)
            connection.connect(str(decision_socket()))
            connection.sendall(payload)
            connection.shutdown(socket.SHUT_WR)
            chunks, size = [], 0
            while True:
                chunk = connection.recv(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > _MAX_REPLY:
                    return {"status": "signer_failed", "error": "reply_too_large"}
                chunks.append(chunk)
    except (FileNotFoundError, ConnectionRefusedError):
        return {"status": "signer_missing"}
    except OSError as exc:
        log.warning("synth_activation: signer unreachable: %r", exc)
        return {"status": "signer_failed", "error": type(exc).__name__}
    try:
        reply = json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeError, ValueError):
        return {"status": "signer_failed", "error": "reply_unreadable"}
    if not isinstance(reply, dict):
        return {"status": "signer_failed", "error": "reply_unreadable"}
    if "status" not in reply:
        # A closed refusal code printed by the signer before any review.
        return {"status": "signer_refused", "error": str(reply.get("error") or "unknown"),
                "detail": str(reply.get("detail") or "")}
    return reply


# The refusals of the final checks a person can be told in words. Every other
# one is told with its closed code.
_REFUSAL_WORDS = {
    "semantic_review_uncertain": "MSG_ACTIVATION_REFUSED_UNCERTAIN",
    "semantic_misaligned": "MSG_ACTIVATION_REFUSED_MISALIGNED",
}


def _failed_check(reply: dict) -> dict:
    report = reply.get("report") if isinstance(reply.get("report"), dict) else {}
    return next((check for check in report.get("checks") or []
                 if isinstance(check, dict)
                 and str(check.get("status")).lower().endswith("failed")), {})


def refusal_words(reply: dict) -> str:
    """Why the tool was not activated, in the words a person reads.

    «birth check failed: semantic_review: semantic_review_uncertain
    (uncertain)» was what the page showed on 12/11/2026: the refusal was
    right, and nobody could tell what it said.
    """
    from messages import get as _msg

    status = str(reply.get("status") or "unknown")
    if status == "tests_failed":
        failed = [str(case.get("name")) for case in reply.get("tests") or []
                  if isinstance(case, dict) and case.get("passed") is not True]
        return _msg("MSG_ACTIVATION_REFUSED_TESTS", cases=", ".join(failed[:4]))
    code = str(_failed_check(reply).get("error_code") or reply.get("error") or status)
    key = _REFUSAL_WORDS.get(code)
    return _msg(key) if key else _msg("MSG_ACTIVATION_REFUSED_OTHER", code=code)


def refusal_reason(reply: dict) -> str:
    """One line saying why the signer's reply is not an admission."""
    status = str(reply.get("status") or "unknown")
    if status == "tests_failed":
        failed = [str(case.get("name")) for case in reply.get("tests") or []
                  if isinstance(case, dict) and case.get("passed") is not True]
        return "review tests failed: " + ", ".join(failed[:4])
    if status == "birth_refused":
        report = reply.get("report") if isinstance(reply.get("report"), dict) else {}
        for check in report.get("checks") or []:
            if isinstance(check, dict) and str(check.get("status")).lower().endswith("failed"):
                return (f"birth check failed: {check.get('check_id')}: "
                        f"{check.get('error_code')} ({check.get('redacted_detail')})")
        return f"birth refused: {reply.get('error') or 'unknown'}"
    detail = f" ({reply['detail']})" if reply.get("detail") else ""
    return f"{status}: {reply.get('error') or 'unknown'}{detail}"
