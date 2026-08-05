"""E2E §6.1 ADR 0092: simmetria IT/EN — ogni `.j2` in `prompts/it/`
deve avere il corrispondente in `prompts/en/` e viceversa.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[3]
_IT_ROOT = _REPO_ROOT / "runtime" / "prompts" / "it"
_EN_ROOT = _REPO_ROOT / "runtime" / "prompts" / "en"


def _rel_set(root: Path) -> set:
    return {str(p.relative_to(root)) for p in root.rglob("*.j2")}


def test_it_en_prompts_symmetric():
    """Ogni `.j2` in `it/` ha sibling in `en/` con stesso path relativo."""
    it_set = _rel_set(_IT_ROOT)
    en_set = _rel_set(_EN_ROOT)
    only_it = it_set - en_set
    only_en = en_set - it_set
    issues = []
    if only_it:
        issues.append(f"presenti solo in IT: {sorted(only_it)}")
    if only_en:
        issues.append(f"presenti solo in EN: {sorted(only_en)}")
    if issues:
        pytest.fail("ADR 0092 simmetria broken:\n  " + "\n  ".join(issues))


def test_it_en_yaml_symmetric():
    """Stessa cosa per `.yaml` (sezioni planner)."""
    def _yaml_set(root: Path) -> set:
        return {str(p.relative_to(root)) for p in root.rglob("*.yaml")}
    it_set = _yaml_set(_IT_ROOT)
    en_set = _yaml_set(_EN_ROOT)
    only_it = it_set - en_set
    only_en = en_set - it_set
    issues = []
    if only_it:
        issues.append(f"yaml solo in IT: {sorted(only_it)}")
    if only_en:
        issues.append(f"yaml solo in EN: {sorted(only_en)}")
    if issues:
        pytest.fail("ADR 0092 yaml simmetria broken:\n  " + "\n  ".join(issues))
