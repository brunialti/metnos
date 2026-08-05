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

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


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

#### gmail search

```bash
$GAPI gmail search "is:unread" --max 10
```

#### gmail get

```bash
$GAPI gmail get MESSAGE_ID
```
"""


def test_affinity_tables_cover_the_canonical_vocabulary_exactly():
    from skill_codegen import _AFFINITY_BY_OBJ, _AFFINITY_BY_VERB
    from vocab import ACTIONS, OBJECTS

    assert set(_AFFINITY_BY_VERB) == set(ACTIONS)
    assert set(_AFFINITY_BY_OBJ) == set(OBJECTS)
    assert all(_AFFINITY_BY_VERB[action] for action in ACTIONS)
    assert all(_AFFINITY_BY_OBJ[obj] for obj in OBJECTS)


def test_google_workspace_generates_24_distinct_canonical_manifests(tmp_path):
    """La fonte pubblicata, il mapping e i manifest generati convergono 24/24."""
    from naming_grammar import validate_name
    from skill_codegen import generate_executor_files
    from skill_parser import parse_skill_md
    from skill_translator import translate_skill

    source = _RUNTIME.parent / "executors/skills/google-workspace/SKILL.md"
    parsed = parse_skill_md(source)
    plans, rejected = translate_skill(parsed)
    assert len(parsed.sub_commands) == 24
    assert len(plans) == 24
    assert rejected == []
    assert len({plan.name for plan in plans}) == 24
    expected = {
        "send_messages_thread_google_workspace",
        "list_messages_labels_google_workspace",
        "get_files_google_workspace",
        "read_files_google_workspace",
        "set_files_xlsx_google_workspace",
        "write_files_xlsx_google_workspace",
    }
    assert expected <= {plan.name for plan in plans}
    generated = tmp_path / "executors"
    for plan in plans:
        assert validate_name(plan.name).ok, plan.name
        paths = generate_executor_files(
            plan, parsed, generated,
            description_it=f"Executor importato {plan.name}.",
            description_en=f"Imported executor {plan.name}.",
            affinity=[plan.name],
        )
        manifest = tomllib.loads(Path(paths["manifest_path"]).read_text())
        assert manifest["name"] == plan.name
        assert manifest["provenance"]["source_subcommand"] == (
            f"{plan.skill_domain} {plan.skill_action}")


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
            assert doc.get("executor_standard") == "metnos.executor/1.0"
            assert doc["execution"] == {
                "effect": "unknown",
                "parallelism_class": 0,
                "resource_class": "default",
                "concurrency_key": "none",
                "equivalence_gate": "unverified",
            }
            assert "args" in doc
            assert "provenance" in doc
            assert doc["provenance"]["imported_from"]
            assert "error_code?: str" in doc["output"]["schema_inline"]
            tests = doc.get("tests") or []
            assert tests
            assert not any(
                str(key).startswith("_force_")
                for test in tests for key in (test.get("input") or {})
            )
            provider_tests = [
                test for test in tests
                if test["name"] in {
                    "happy_path_offline", "auth_missing_offline_needs_inputs",
                    "empty_result",
                }
            ]
            assert provider_tests
            assert all(
                str((test.get("env") or {}).get(
                    "METNOS_SUBPROCESS_FAKE", "")).startswith(
                        "skill_test_fakes.")
                for test in provider_tests
            )

            # Python deve essere parsabile (no syntax error).
            import ast
            ast.parse(code.read_text())
            code_text = code.read_text()
            assert "_validate_skill_args" in code_text
            assert "_error_code_for_class" in code_text

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

    def test_free_form_positional_is_not_rendered_as_flag(
        self, skill_md_path, tmp_path,
    ):
        from skill_parser import parse_skill_md
        from skill_translator import translate_skill
        from skill_codegen import generate_executor_files

        parsed = parse_skill_md(skill_md_path)
        plans, _ = translate_skill(parsed)
        plan = next(p for p in plans
                    if p.skill_domain == "gmail" and p.skill_action == "search")
        out = generate_executor_files(
            plan, parsed, tmp_path / "executors",
            description_it="x", description_en="y", affinity=["a"],
        )
        code_path = Path(out["code_path"])
        code = code_path.read_text()
        manifest = tomllib.loads(Path(out["manifest_path"]).read_text())
        assert "query" in manifest["args"]["required"]
        assert 'args.get("query")' in code
        assert 'argv.append(str(_pv))' in code
        assert '"--query"' not in code

        namespace = {"__file__": str(code_path)}
        exec(compile(code, str(code_path), "exec"), namespace)
        result = namespace["invoke"]({})
        assert result["ok"] is False
        assert result["error_class"] == "invalid_args"
        assert result["error_code"] == "ERR_ARG_INVALID"

    def test_resource_positional_has_one_of_and_vector_reader(
        self, skill_md_path, tmp_path,
    ):
        from skill_parser import parse_skill_md
        from skill_translator import translate_skill
        from skill_codegen import generate_executor_files

        parsed = parse_skill_md(skill_md_path)
        plans, _ = translate_skill(parsed)
        plan = next(p for p in plans
                    if p.skill_domain == "gmail" and p.skill_action == "get")
        out = generate_executor_files(
            plan, parsed, tmp_path / "executors",
            description_it="x", description_en="y", affinity=["a"],
        )
        manifest = tomllib.loads(Path(out["manifest_path"]).read_text())
        assert manifest["args"]["requires_one_of"] == [[
            "message_id", "message_ids", "entries",
        ]]

        code_path = Path(out["code_path"])
        code = code_path.read_text()
        assert "def _coalesce_rows" in code
        assert "def _argv_for_row" in code
        assert 'raw_items = raw if isinstance(raw, list) else [raw]' in code
        assert "per_argv.append(rid)" in code

        namespace = {"__file__": str(code_path)}
        exec(compile(code, str(code_path), "exec"), namespace)
        result = namespace["invoke"]({})
        assert result["ok"] is False
        assert result["error_class"] == "invalid_args"
        assert result["error_code"] == "ERR_ARG_INVALID"

        calls = []

        def fake_run(_script, argv, **_kwargs):
            import json
            calls.append(list(argv))
            return 0, json.dumps({"id": argv[-1], "subject": "test"}), ""

        namespace["_run_api"] = fake_run
        result = namespace["invoke"]({"message_ids": ["m1", "m2"]})
        assert result["ok"] is True
        assert [entry["id"] for entry in result["entries"]] == ["m1", "m2"]
        assert calls == [["gmail", "get", "m1"],
                         ["gmail", "get", "m2"]]

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

    def test_github_codegen_remains_builtin_with_handcrafted_origin(
        self, skill_md_path, tmp_path,
    ):
        from skill_parser import parse_skill_md
        from skill_translator import translate_skill
        from skill_codegen import generate_executor_files

        parsed = parse_skill_md(skill_md_path)
        parsed.name = "github"
        plans, rejected = translate_skill(
            parsed,
            imported_from_url="agentskills.io/local/github",
            imported_at="2026-05-24T00:00:00Z",
        )
        assert plans, rejected
        out = generate_executor_files(
            plans[0], parsed, tmp_path / "executors",
            description_it="x", description_en="y", affinity=["a"],
        )
        manifest = tomllib.loads(Path(out["manifest_path"]).read_text())
        code = Path(out["code_path"]).read_text()
        assert manifest["origin"] == "handcrafted"
        assert manifest["author"] == "Metnos builtin maintainers"
        assert "provenance" not in manifest
        assert "executor builtin GitHub handcrafted" in code
        assert "executor importato" not in code

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
