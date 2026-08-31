#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Evidence that the closed-build policy bit was OBSERVED, not claimed.

Point 3 of the group 7 wrapper recomputes the catalog, the effective topology
and a separate evidence from the current bytes, with `closed_build_enforcement()`
true. This module owns that evidence, and owns it in one specific way: the
evidence is built from a reading of the compiled source that carries the bit,
never from the caller's word about it.

The distinction is the whole point. A boolean passed as an argument records
what someone believed; a digest of the bytes that define the function records
what the build actually contains. Only the second survives being replayed on a
different build, which is exactly the attack a policy bit invites — flip it in
a report while the artefact keeps the old value.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path


ENFORCEMENT_EVIDENCE_DOMAIN_V1 = b"metnos.executor-birth.enforcement-evidence/v1\0"
GATE_MODULE_BASENAME_V1 = "executor_birth_legacy_gate.py"
MAX_GATE_MODULE_BYTES_V1 = 256 * 1024

# The literal is read from the source, not evaluated: importing the module and
# calling the function would report the bit of the process that is asking,
# which on a candidate build is not the bit of the artefact being certified.
_ENFORCEMENT_LITERAL_RE_V1 = re.compile(
    rb"\ndef closed_build_enforcement\(\) -> bool:\n"
    rb"(?:[ ]{4}.*\n|\n)*?[ ]{4}return (True|False)\n",
)


class EnforcementEvidenceError(RuntimeError):
    """One stable denial class; detail never reaches an operator stream."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail or code)


def _invalid(code: str, detail: str = "") -> EnforcementEvidenceError:
    return EnforcementEvidenceError(code, detail)


@dataclass(frozen=True, slots=True)
class EnforcementEvidenceV1:
    """What the artefact contains, and the bytes that say so."""

    enforced: bool
    module_digest: str
    module_bytes: int


def observe_enforcement_v1(gate_module_path: Path) -> EnforcementEvidenceV1:
    """Read the bit out of the artefact's own bytes.

    Refuses a module that declares the function more than once: two
    definitions mean the value depends on import order, and evidence that
    depends on import order is not evidence.
    """
    if not isinstance(gate_module_path, Path) or not gate_module_path.is_absolute():
        raise _invalid("enforcement_module_invalid", "path")
    if gate_module_path.name != GATE_MODULE_BASENAME_V1:
        raise _invalid("enforcement_module_invalid", gate_module_path.name)
    if gate_module_path.is_symlink() or not gate_module_path.is_file():
        raise _invalid("enforcement_module_invalid", "not a regular file")
    payload = gate_module_path.read_bytes()
    if not payload or len(payload) > MAX_GATE_MODULE_BYTES_V1:
        raise _invalid("enforcement_module_invalid", "size")
    # Match on NORMALISED line endings, digest the RAW bytes. The artefact's
    # identity is its bytes, CRLF included, but the bit it declares must not
    # depend on which platform checked the file out: a gate module with Windows
    # line endings would otherwise make the evidence unobtainable, which is a
    # denial of service on the certification rather than a safety property.
    matches = _ENFORCEMENT_LITERAL_RE_V1.findall(
        payload.replace(b"\r\n", b"\n").replace(b"\r", b"\n"),
    )
    if len(matches) != 1:
        raise _invalid("enforcement_literal_ambiguous", str(len(matches)))
    return EnforcementEvidenceV1(
        matches[0] == b"True",
        f"sha256:{hashlib.sha256(payload).hexdigest()}",
        len(payload),
    )


def evidence_digest_v1(evidence: EnforcementEvidenceV1) -> str:
    """Frame the evidence so the bit cannot be read apart from its bytes."""
    if type(evidence) is not EnforcementEvidenceV1:
        raise _invalid("enforcement_evidence_invalid", "type")
    digest = hashlib.sha256(ENFORCEMENT_EVIDENCE_DOMAIN_V1)
    digest.update(b"\x01" if evidence.enforced else b"\x00")
    encoded = evidence.module_digest.encode("ascii")
    digest.update(len(encoded).to_bytes(8, "big"))
    digest.update(encoded)
    digest.update(evidence.module_bytes.to_bytes(8, "big"))
    return f"sha256:{digest.hexdigest()}"


def require_enforced_v1(evidence: EnforcementEvidenceV1) -> str:
    """Return the digest only when the artefact really carries the closed bit."""
    if type(evidence) is not EnforcementEvidenceV1:
        raise _invalid("enforcement_evidence_invalid", "type")
    if not evidence.enforced:
        raise _invalid("enforcement_not_closed")
    return evidence_digest_v1(evidence)


__all__ = [
    "ENFORCEMENT_EVIDENCE_DOMAIN_V1",
    "EnforcementEvidenceError",
    "EnforcementEvidenceV1",
    "evidence_digest_v1",
    "observe_enforcement_v1",
    "require_enforced_v1",
]
