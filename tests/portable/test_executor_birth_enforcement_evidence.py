"""G7-C: the closed-build bit is evidence only when read from the artefact."""
from __future__ import annotations

from pathlib import Path

import pytest

import executor_birth_enforcement_evidence as evidence


GATE = Path(__file__).resolve().parents[2] / "runtime" / evidence.GATE_MODULE_BASENAME_V1


def _module(tmp_path: Path, body: str) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / evidence.GATE_MODULE_BASENAME_V1
    path.write_text(body, encoding="utf-8")
    return path


_CLOSED = '''
def closed_build_enforcement() -> bool:
    """Doc."""
    return True
'''

_OPEN = '''
def closed_build_enforcement() -> bool:
    """Doc."""
    return False
'''


def test_the_product_gate_reads_as_open_today() -> None:
    """The bit is still false, and the evidence says so rather than guessing."""
    observed = evidence.observe_enforcement_v1(GATE)
    assert observed.enforced is False
    assert observed.module_bytes == GATE.stat().st_size
    with pytest.raises(evidence.EnforcementEvidenceError) as denied:
        evidence.require_enforced_v1(observed)
    assert denied.value.code == "enforcement_not_closed"


def test_a_closed_artefact_yields_a_digest(tmp_path: Path) -> None:
    """Only a build that really carries the bit passes the requirement."""
    observed = evidence.observe_enforcement_v1(_module(tmp_path, _CLOSED))
    assert observed.enforced is True
    assert evidence.require_enforced_v1(observed) == (
        evidence.evidence_digest_v1(observed)
    )


def test_the_bit_cannot_be_read_apart_from_its_bytes(tmp_path: Path) -> None:
    """Same bit, different artefact: the evidence must differ.

    A digest that depended only on the boolean could be replayed from one build
    onto another, which is exactly what a policy bit invites.
    """
    first = evidence.observe_enforcement_v1(_module(tmp_path / "a", _CLOSED))
    second = evidence.observe_enforcement_v1(
        _module(tmp_path / "b", _CLOSED + "\n# a later build\n"),
    )
    assert first.enforced == second.enforced is True
    assert evidence.evidence_digest_v1(first) != evidence.evidence_digest_v1(second)


def test_two_definitions_are_not_evidence(tmp_path: Path) -> None:
    """A value that depends on import order is not evidence of anything."""
    with pytest.raises(evidence.EnforcementEvidenceError) as ambiguous:
        evidence.observe_enforcement_v1(_module(tmp_path, _OPEN + _CLOSED))
    assert ambiguous.value.code == "enforcement_literal_ambiguous"

    with pytest.raises(evidence.EnforcementEvidenceError) as absent:
        evidence.observe_enforcement_v1(_module(tmp_path, "# nothing here\n"))
    assert absent.value.code == "enforcement_literal_ambiguous"


@pytest.mark.parametrize("case", ["relative", "wrong_name", "symlink", "empty"])
def test_module_denials(tmp_path: Path, case: str) -> None:
    """Every denial is one row of one table, not one apparatus each."""
    if case == "relative":
        candidate = Path(evidence.GATE_MODULE_BASENAME_V1)
    elif case == "wrong_name":
        candidate = _module(tmp_path, _CLOSED).rename(tmp_path / "other.py")
    elif case == "symlink":
        real = _module(tmp_path / "real", _CLOSED)
        candidate = tmp_path / evidence.GATE_MODULE_BASENAME_V1
        candidate.symlink_to(real)
    else:
        candidate = _module(tmp_path, "")

    with pytest.raises(evidence.EnforcementEvidenceError) as denied:
        evidence.observe_enforcement_v1(candidate)
    assert denied.value.code == "enforcement_module_invalid"


def test_the_module_never_imports_the_gate_it_measures() -> None:
    """Importing it would report the bit of the process, not of the artefact.

    On a candidate build those two differ, and the difference is the only
    thing worth certifying.
    """
    source = Path(evidence.__file__).read_text("utf-8")
    assert "import executor_birth_legacy_gate" not in source
    assert "closed_build_enforcement()" not in source.replace(
        "`closed_build_enforcement()`", "",
    )
