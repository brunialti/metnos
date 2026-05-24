"""Test G7 (24/5/2026): codegen round-trip end-to-end.

Pipeline completa: SKILL.md → parser → translator → codegen → manifest TOML
parsabile + executor Python parsabile come modulo.

Verifica deterministica che la pipeline non rompa l'output strutturale di
nessuno step.

Determinismo §7.9: solo lookup tabellare, nessun LLM.
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


# Minimal SKILL.md valido che ha 2-3 sub-command per coprire entries/results.
# Format atteso dal parser (skill_parser._parse_usage_subcommands):
#   ## Usage
#   ### <domain>
#   ```bash
#   $SHORTHAND <domain> <action> [--flags]
#   ```
_SKILL_MD = """---
name: round-trip-test
version: 1.0
description: Round trip skill test
required_credential_files:
  - path: /tmp/fake_creds.json
---

## Scripts

- `scripts/test_cli.py` — Test CLI.

## Usage

```bash
GAPI="python scripts/test_cli.py"
```

### calendar

#### calendar list

```bash
$GAPI calendar list --max=10
```

#### calendar create

```bash
$GAPI calendar create --summary "Title" --start 2026-05-24T10:00:00Z --end 2026-05-24T11:00:00Z
```

#### calendar delete

```bash
$GAPI calendar delete EVENT_ID
```
"""


@pytest.fixture
def skill_md_path(tmp_path):
    p = tmp_path / "SKILL.md"
    p.write_text(_SKILL_MD)
    return p


class TestRoundTripPipeline:
    """E2E: parse → translate → codegen → parsable TOML + valid Python."""

    def test_parse_translate_codegen_produces_valid_manifest_toml(
        self, skill_md_path, tmp_path,
    ):
        from skill_parser import parse_skill_md
        from skill_translator import translate_skill
        from skill_codegen import generate_executor_files

        parsed = parse_skill_md(skill_md_path)
        assert parsed.name == "round-trip-test"
        assert len(parsed.sub_commands) >= 1

        plans, rejected = translate_skill(parsed)
        assert len(plans) >= 1, f"no plans, rejected={rejected}"

        executors_dir = tmp_path / "executors"
        for plan in plans:
            out = generate_executor_files(
                plan, parsed, executors_dir,
                description_it="Test IT",
                description_en="Test EN",
                affinity=["test"],
            )
            manifest = Path(out["manifest_path"])
            code = Path(out["code_path"])
            lang_state = Path(out["lang_state_path"])

            assert manifest.is_file()
            assert code.is_file()
            assert lang_state.is_file()

            # TOML deve essere parsabile.
            doc = tomllib.loads(manifest.read_text())
            assert doc.get("name") == plan.name
            assert "args" in doc
            assert "provenance" in doc
            assert doc["provenance"]["imported_from"]

            # Python deve essere parsabile (no syntax error).
            import ast
            ast.parse(code.read_text())

            # lang_state json deve essere parsabile.
            import json
            json.loads(lang_state.read_text())

    def test_executor_code_has_invoke_function(self, skill_md_path, tmp_path):
        from skill_parser import parse_skill_md
        from skill_translator import translate_skill
        from skill_codegen import generate_executor_files

        parsed = parse_skill_md(skill_md_path)
        plans, _ = translate_skill(parsed)
        executors_dir = tmp_path / "executors"
        plan = plans[0]
        out = generate_executor_files(
            plan, parsed, executors_dir,
            description_it="x", description_en="y", affinity=["a"],
        )
        code = Path(out["code_path"]).read_text()
        # Convenzione synt §3: ogni executor espone `def invoke(...)`.
        assert "def invoke" in code

    def test_manifest_provenance_complete(self, skill_md_path, tmp_path):
        from skill_parser import parse_skill_md
        from skill_translator import translate_skill
        from skill_codegen import generate_executor_files

        parsed = parse_skill_md(skill_md_path)
        plans, _ = translate_skill(
            parsed,
            imported_from_url="agentskills.io/test/round-trip-test",
            imported_at="2026-05-24T00:00:00Z",
        )
        executors_dir = tmp_path / "executors"
        plan = plans[0]
        out = generate_executor_files(
            plan, parsed, executors_dir,
            description_it="x", description_en="y", affinity=["a"],
        )
        doc = tomllib.loads(Path(out["manifest_path"]).read_text())
        prov = doc["provenance"]
        # ADR 0123: provenance fields obbligatori.
        for field in (
            "imported_from", "source_version", "source_section",
            "source_subcommand", "imported_at", "source_sha256",
            "importer_version",
        ):
            assert field in prov, f"missing provenance.{field}"
        assert prov["imported_from"] == "agentskills.io/test/round-trip-test"
        assert prov["source_version"] == "1.0"
        assert prov["source_sha256"] == parsed.source_sha256

    def test_codegen_idempotent(self, skill_md_path, tmp_path):
        """Re-running codegen overwrites consistently (no stale state)."""
        from skill_parser import parse_skill_md
        from skill_translator import translate_skill
        from skill_codegen import generate_executor_files

        parsed = parse_skill_md(skill_md_path)
        plans, _ = translate_skill(parsed)
        plan = plans[0]
        executors_dir = tmp_path / "executors"
        out1 = generate_executor_files(
            plan, parsed, executors_dir,
            description_it="V1", description_en="V1en", affinity=["v1"],
        )
        manifest1 = Path(out1["manifest_path"]).read_text()
        # Re-run with different description.
        out2 = generate_executor_files(
            plan, parsed, executors_dir,
            description_it="V2", description_en="V2en", affinity=["v2"],
        )
        manifest2 = Path(out2["manifest_path"]).read_text()
        assert manifest1 != manifest2
        assert "V2" in manifest2
        assert "V1" not in manifest2

    def test_handcrafted_familes_distinct_semantics(self, tmp_path):
        """G6 fix (24/5/2026): `skill_admission._HANDCRAFTED_DIRS` (path
        strings) e `loader.HANDCRAFTED_FAMILIES` (frozenset di nomi) sono
        semanticamente distinti — verifica esposizione separata."""
        from skill_admission import _HANDCRAFTED_DIRS, _is_handcrafted
        import loader
        # _HANDCRAFTED_DIRS: tuple di path strings.
        assert isinstance(_HANDCRAFTED_DIRS, tuple)
        for d in _HANDCRAFTED_DIRS:
            assert isinstance(d, str)
            assert "/" in d or "\\" in d  # path-like
        # loader.HANDCRAFTED_FAMILIES: frozenset di nomi (no path sep).
        assert isinstance(loader.HANDCRAFTED_FAMILIES, frozenset)
        for n in loader.HANDCRAFTED_FAMILIES:
            assert "/" not in n
            assert "_" in n  # convention azione_oggetto
        # _is_handcrafted prende un name. Smoke negative: nome random
        # non esiste come dir handcrafted.
        assert _is_handcrafted("nonexistent_xyz_definitely_not_real") is False
        # Smoke positive: scan reale, ma SOLO se il path attualmente
        # configurato contiene davvero almeno un executor handcrafted
        # (HOME/conftest non lo isola al tmp_path quando _is_handcrafted
        # legge _C.PATH_EXECUTORS al call time). Se nessun handcrafted
        # rilevato, skippiamo l'assertion (test indipendente dal layout).
        # NB: si limita a verificare che la semantica name->bool sia ben
        # definita, non che un nome specifico esista (rename-resilient).
        any_handcrafted_dir = any(
            Path(d).is_dir() and any(Path(d).iterdir())
            for d in _HANDCRAFTED_DIRS
        )
        if any_handcrafted_dir:
            # Scan il primo handcrafted reale dal disco.
            for d in _HANDCRAFTED_DIRS:
                root = Path(d)
                if not root.is_dir():
                    continue
                for child in root.iterdir():
                    if child.is_dir() and (child / "manifest.toml").is_file():
                        assert _is_handcrafted(child.name) is True
                        return  # ok, primo trovato basta
