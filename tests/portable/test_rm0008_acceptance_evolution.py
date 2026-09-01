"""Negative cases for the closed RM-0008 acceptance evolution."""
from __future__ import annotations

import pytest

from tests.portable.rm0008_2a_acceptance import certification_v1 as certification


_CERTIFICATION = (
    "tests/portable/rm0008_2a_acceptance/certification_v1.py"
)
_NEGATIVE_CASES = (
    "tests/portable/rm0008_2a_acceptance/test_manifest_acceptance.py"
)


def _baseline() -> dict[str, tuple[str, str]]:
    return {
        _CERTIFICATION: ("100644", "a" * 40),
        _NEGATIVE_CASES: ("100644", "b" * 40),
        "tests/portable/rm0008_2a_acceptance/required_cells_v1.py": (
            "100644", "c" * 40,
        ),
    }


def test_only_the_two_reviewed_acceptance_files_may_evolve() -> None:
    source = _baseline()
    current = dict(source)
    current[_CERTIFICATION] = ("100644", "d" * 40)
    current[_NEGATIVE_CASES] = ("100644", "e" * 40)

    certification._validate_reviewed_acceptance_tree_evolution(source, current)


@pytest.mark.parametrize("variant", ("missing", "added", "third", "only-one"))
def test_every_other_acceptance_tree_change_is_rejected(variant: str) -> None:
    source = _baseline()
    current = dict(source)
    current[_CERTIFICATION] = ("100644", "d" * 40)
    current[_NEGATIVE_CASES] = ("100644", "e" * 40)
    if variant == "missing":
        current.pop("tests/portable/rm0008_2a_acceptance/required_cells_v1.py")
    elif variant == "added":
        current["tests/portable/rm0008_2a_acceptance/extra.py"] = (
            "100644", "f" * 40,
        )
    elif variant == "third":
        current["tests/portable/rm0008_2a_acceptance/required_cells_v1.py"] = (
            "100644", "f" * 40,
        )
    else:
        current[_NEGATIVE_CASES] = source[_NEGATIVE_CASES]

    with pytest.raises(
        certification.CertificationError,
        match="frozen acceptance baseline has an unreviewed evolution",
    ):
        certification._validate_reviewed_acceptance_tree_evolution(
            source, current,
        )
