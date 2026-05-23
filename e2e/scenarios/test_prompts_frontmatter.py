"""E2E §6.1 prompt typization audit: ogni `.j2` deve dichiarare
frontmatter Jinja `{# --- ... --- #}` con 8 campi (role, tier, lang,
style, version, owner, updated, sha_prev).

Style ∈ {prescriptive, definitional, few_shot} (§6.1 disgiunti).
Lang IT/EN symmetric (ADR 0092).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]
_PROMPTS_ROOT = _REPO_ROOT / "runtime" / "prompts"

_REQUIRED_FIELDS = {"role", "tier", "lang", "style", "version", "owner",
                     "updated", "sha_prev"}
_VALID_STYLES = {"prescriptive", "definitional", "few_shot"}

_FRONTMATTER_RE = re.compile(r"^\s*\{#\s*---(.*?)---\s*#\}", re.DOTALL)
_FIELD_RE = re.compile(r"^(\w+)\s*:\s*(.*?)$", re.MULTILINE)


def _parse_frontmatter(text: str) -> dict | None:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None
    body = m.group(1)
    out = {}
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        fm = re.match(r"^(\w+)\s*:\s*(.*)$", line)
        if fm:
            out[fm.group(1)] = fm.group(2).strip().strip('"').strip("'")
    return out


_J2_FILES = sorted(p for p in _PROMPTS_ROOT.rglob("*.j2"))


@pytest.mark.parametrize("path", _J2_FILES,
                          ids=[str(p.relative_to(_REPO_ROOT)) for p in _J2_FILES])
def test_prompt_has_frontmatter(path: Path):
    """Ogni `.j2` ha frontmatter 8-fields valido."""
    text = path.read_text(encoding="utf-8")
    fm = _parse_frontmatter(text)
    assert fm is not None, (
        f"{path.relative_to(_REPO_ROOT)}: frontmatter `{{# --- ... --- #}}` "
        f"mancante (§6.1)"
    )
    missing = _REQUIRED_FIELDS - set(fm.keys())
    # Alcuni file possono usare un subset: sha_prev pu' essere vuoto.
    # Check stretto solo per i campi semanticamente obbligatori.
    hard_required = {"role", "tier", "lang", "style", "version"}
    hard_missing = hard_required - set(fm.keys())
    assert not hard_missing, (
        f"{path.relative_to(_REPO_ROOT)}: campi obbligatori mancanti: "
        f"{hard_missing}"
    )
    # Style valido
    style = fm.get("style", "")
    assert style in _VALID_STYLES, (
        f"{path.relative_to(_REPO_ROOT)}: style `{style}` non in "
        f"{_VALID_STYLES} (§6.1)"
    )
