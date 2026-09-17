"""The four administrative F5 operations, behind one closed entry point.

An operator reaches these through a root-owned launcher with a fixed argument
set, so this module is the only place where the operations exist and the only
place their inputs are validated. It adds no authority: each subcommand calls
the owner that already holds it, and every one of them refuses on its own terms.

Evidence is the one operation with a payload, and it arrives on standard input
as a closed document rather than as arguments. Artifacts are bytes, and bytes
do not belong in an argument vector an operator can mistype or a process list
can leak.
"""
from __future__ import annotations

import base64
import binascii
import json
import sys


class AuthorityInputError(ValueError):
    __slots__ = ("code", "detail")

    def __init__(self, code: str, detail: str = "") -> None:
        self.code, self.detail = code, detail
        super().__init__(f"{code}: {detail}" if detail else code)


_MAX_DOCUMENT_BYTES = 64 * 1024 * 1024
_EVIDENCE_FIELDS = {
    "census": {"scope_id", "sources", "review", "findings"},
    "close_defect": {"finding_id", "opening", "repair", "verification", "review"},
    "profile": {"base", "manifest", "cases"},
    "start_cycle": set(),
    "finish_cycle": {"start", "results", "turns"},
}


def _artifact(value: object, field: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise AuthorityInputError("evidence_document_invalid", field)
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AuthorityInputError("evidence_document_invalid", field) from exc
    if not raw:
        raise AuthorityInputError("evidence_document_invalid", field)
    return raw


def _read_document() -> dict:
    """Read one closed evidence document from standard input."""
    raw = sys.stdin.buffer.read(_MAX_DOCUMENT_BYTES + 1)
    if len(raw) > _MAX_DOCUMENT_BYTES:
        raise AuthorityInputError("evidence_document_invalid", "size")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise AuthorityInputError("evidence_document_invalid", "json") from exc
    if type(document) is not dict:
        raise AuthorityInputError("evidence_document_invalid", "shape")
    kind = document.get("kind")
    if kind not in _EVIDENCE_FIELDS:
        raise AuthorityInputError("evidence_document_invalid", "kind")
    if set(document) - {"kind"} != _EVIDENCE_FIELDS[kind]:
        raise AuthorityInputError("evidence_document_invalid", "fields")
    return document


def _apply_evidence(owner, document: dict) -> dict:
    """Dispatch one document to the owner method that already validates it."""
    kind = document["kind"]
    if kind == "census":
        sources = document["sources"]
        findings = document["findings"]
        if (not isinstance(sources, list) or not sources
                or not isinstance(findings, dict)):
            raise AuthorityInputError("evidence_document_invalid", "census")
        frontier = owner.census(
            scope_id=document["scope_id"],
            sources=tuple(_artifact(item, "sources") for item in sources),
            review=_artifact(document["review"], "review"),
            findings={name: _artifact(item, "findings")
                      for name, item in findings.items()},
        )
    elif kind == "close_defect":
        frontier = owner.close_defect(
            document["finding_id"], opening=document["opening"],
            repair=_artifact(document["repair"], "repair"),
            verification=_artifact(document["verification"], "verification"),
            review=_artifact(document["review"], "review"),
        )
    elif kind == "profile":
        if not isinstance(document["base"], dict):
            raise AuthorityInputError("evidence_document_invalid", "base")
        frontier = owner.profile(
            base=document["base"],
            manifest=_artifact(document["manifest"], "manifest"),
            cases=_artifact(document["cases"], "cases"),
        )
    elif kind == "start_cycle":
        frontier = owner.start_cycle()
    else:
        turns = document["turns"]
        if not isinstance(turns, dict):
            raise AuthorityInputError("evidence_document_invalid", "turns")
        frontier = owner.finish_cycle(
            start=document["start"],
            results=_artifact(document["results"], "results"),
            turns={case: {turn: _artifact(item, "turns")
                          for turn, item in entries.items()}
                   for case, entries in turns.items()},
        )
    return {
        "head": frontier.head, "event_count": frontier.event_count,
        "census_scope": frontier.census_scope,
        "open_findings": list(frontier.open_findings),
        "profile": frontier.profile,
        "consecutive_successes": list(frontier.consecutive_successes),
        "pending_cycle": frontier.pending_cycle,
    }


def _provision_key() -> dict:
    from install.birth_certification_authority_provisioner import (
        provision_certification_authority_v1,
    )

    public = provision_certification_authority_v1()
    # Public material only: the private half never crosses this boundary.
    return {"key_id": public.key_id, "status": public.status}


def _evidence() -> dict:
    from install.birth_certification_evidence import administrative_evidence_v1

    document = _read_document()
    with administrative_evidence_v1() as owner:
        return _apply_evidence(owner, document)


def _migrate(stage: str) -> dict:
    from install.birth_lifecycle_migration import apply_cutover_v1, plan_cutover_v1

    return apply_cutover_v1() if stage == "apply" else plan_cutover_v1()


def _certify(stage: str) -> dict:
    from install.birth_certification_issuer import issue_certificate_v1

    return issue_certificate_v1(apply=stage == "issue")


_COMMANDS = {
    ("provision-key",): _provision_key,
    ("evidence",): _evidence,
    ("migrate", "plan"): lambda: _migrate("plan"),
    ("migrate", "apply"): lambda: _migrate("apply"),
    ("certify", "derive"): lambda: _certify("derive"),
    ("certify", "issue"): lambda: _certify("issue"),
}


def main(argv: list[str] | None = None) -> int:
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    command = _COMMANDS.get(arguments)
    if command is None:
        print("usage: f5_authority.py provision-key | evidence | "
              "migrate plan|apply | certify derive|issue", file=sys.stderr)
        return 64
    try:
        report = command()
    except Exception as exc:
        code = getattr(exc, "code", type(exc).__name__)
        detail = getattr(exc, "detail", "")
        print(json.dumps({"error": str(code), "detail": str(detail or "")}),
              file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True,
                     default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover - administrative entry point
    raise SystemExit(main())
