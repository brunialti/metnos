"""Test G7 (24/5/2026): skill_parser robust contro input malformati comuni.

Copre input edge case che possono arrivare da agentskills.io / fetch URL:
- BOM UTF-8 in head (BOM byte sequence \\xef\\xbb\\xbf)
- CRLF line endings (Windows uploaders)
- TAB nell'YAML frontmatter (banned)
- Missing frontmatter dividers
- Empty body
- Mixed indentation YAML

Determinismo §7.9: pure parser, niente LLM.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

from skill_parser import (  # noqa: E402
    parse_skill_md,
    SkillParseError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_skill(tmp_path: Path, content: str, *, encoding="utf-8",
                  binary: bytes = None) -> Path:
    """Scrive un SKILL.md con content (str) o binary (bytes)."""
    p = tmp_path / "SKILL.md"
    if binary is not None:
        p.write_bytes(binary)
    else:
        p.write_text(content, encoding=encoding)
    return p


_MINIMAL_VALID = """---
name: test-skill
version: 1.0
description: A test skill
---

## Calendar.list

List events from primary calendar.

```bash
calendar list
```
"""


# ---------------------------------------------------------------------------
# UTF-8 BOM
# ---------------------------------------------------------------------------


class TestUTF8BOM:
    """SKILL.md con BOM iniziale (Windows Notepad o uploader confusi)."""

    def test_bom_utf8_at_start_rejected_with_clear_error(self, tmp_path):
        """BOM + valid content: deve fallire con messaggio chiaro (manca `---`
        all'inizio perche' il BOM e' interpretato come carattere)."""
        bom = b"\xef\xbb\xbf"
        content_bytes = bom + _MINIMAL_VALID.encode("utf-8")
        p = _write_skill(tmp_path, content="", binary=content_bytes)
        with pytest.raises(SkillParseError, match="frontmatter"):
            parse_skill_md(p)


# ---------------------------------------------------------------------------
# CRLF line endings
# ---------------------------------------------------------------------------


class TestCRLFLineEndings:
    """Windows-style CRLF non deve rompere il parser."""

    def test_crlf_line_endings_accepted(self, tmp_path):
        """Il regex frontmatter usa `\\n`: CRLF (`\\r\\n`) deve essere
        accettato come `\\n` (universal newlines durante read_text)."""
        crlf_content = _MINIMAL_VALID.replace("\n", "\r\n")
        p = _write_skill(tmp_path, crlf_content)
        parsed = parse_skill_md(p)
        # Se il parser tollera CRLF, name e version sono popolati.
        assert parsed.name == "test-skill"
        assert parsed.version == "1.0"


# ---------------------------------------------------------------------------
# TAB in YAML frontmatter
# ---------------------------------------------------------------------------


class TestYAMLTabs:
    """YAML formalmente vieta tab nell'indent. Il parser deve respingerli
    con messaggio chiaro."""

    def test_tab_in_frontmatter_rejected(self, tmp_path):
        bad = (
            "---\n"
            "name: test-skill\n"
            "metadata:\n"
            "\tkey: value\n"  # TAB indentation: YAML invalid
            "---\n\n"
        )
        p = _write_skill(tmp_path, bad)
        with pytest.raises(SkillParseError, match="tab"):
            parse_skill_md(p)


# ---------------------------------------------------------------------------
# Missing frontmatter
# ---------------------------------------------------------------------------


class TestMissingFrontmatter:
    """SKILL.md senza `---` di apertura non e' parsabile."""

    def test_missing_frontmatter_dividers(self, tmp_path):
        no_fm = "## Just a body\n\nNo frontmatter at all.\n"
        p = _write_skill(tmp_path, no_fm)
        with pytest.raises(SkillParseError, match="frontmatter"):
            parse_skill_md(p)

    def test_only_open_divider(self, tmp_path):
        """`---` aperto ma mai chiuso → non e' un frontmatter valido."""
        only_open = "---\nname: x\n## Section\n"
        p = _write_skill(tmp_path, only_open)
        with pytest.raises(SkillParseError):
            parse_skill_md(p)


# ---------------------------------------------------------------------------
# Empty / minimal cases
# ---------------------------------------------------------------------------


class TestEmptyCases:
    """File vuoti o quasi-vuoti."""

    def test_empty_file_raises(self, tmp_path):
        p = _write_skill(tmp_path, "")
        with pytest.raises(SkillParseError):
            parse_skill_md(p)

    def test_empty_body_after_frontmatter_ok(self, tmp_path):
        """Frontmatter valido + body vuoto: ok, ma 0 sub-commands."""
        content = "---\nname: x\nversion: 1.0\n---\n"
        p = _write_skill(tmp_path, content)
        parsed = parse_skill_md(p)
        assert parsed.name == "x"
        assert parsed.sub_commands == []


# ---------------------------------------------------------------------------
# File non esistente
# ---------------------------------------------------------------------------


class TestFileNotFound:
    def test_missing_path_raises_file_not_found(self, tmp_path):
        p = tmp_path / "nonexistent" / "SKILL.md"
        with pytest.raises(FileNotFoundError):
            parse_skill_md(p)


# ---------------------------------------------------------------------------
# Mixed encoding edge case
# ---------------------------------------------------------------------------


class TestMixedEncoding:
    def test_utf8_with_unicode_in_body(self, tmp_path):
        """Body con caratteri unicode (UTF-8) accettato senza problemi."""
        content = (
            "---\n"
            "name: skill-italiana\n"
            "version: 1.0\n"
            "description: Gestione appuntamenti à è ì ò ù\n"
            "---\n\n"
            "## Sezione\n\n"
            "Test con caratteri accentati: città però perché.\n"
        )
        p = _write_skill(tmp_path, content)
        parsed = parse_skill_md(p)
        assert parsed.name == "skill-italiana"
        assert "à è ì ò ù" in parsed.description
