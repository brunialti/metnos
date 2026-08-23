from __future__ import annotations

import ast
import html
import importlib.util
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "generate_executor_catalog.py"
RUNTIME = ROOT / "runtime"

from vocab import ACTIONS, OBJECTS, SAFE_VERBS  # noqa: E402


def _module():
    spec = importlib.util.spec_from_file_location("executor_catalog_docs", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_executor_catalog_covers_every_first_party_manifest_once():
    module = _module()
    entries = module.load_entries()
    manifests = sorted((ROOT / "executors").glob("*/manifest.toml"))
    assert len(entries) == len(manifests)
    assert len({entry.name for entry in entries}) == len(entries)
    assert {entry.name for entry in entries} == {
        path.parent.name for path in manifests}


def test_executor_catalog_groups_sites_under_canonical_domain():
    module = _module()
    sites = {entry.name for entry in module.load_entries()
             if entry.domain == "sites"}
    assert sites == {
        "act_sites", "delete_sites", "login_sites", "open_sites", "read_sites"}


def test_checked_in_catalog_is_fresh_and_bilingual():
    module = _module()
    entries = module.load_entries()
    for lang, output in module.OUTPUTS.items():
        content = module.render(entries, lang)
        assert output.read_text(encoding="utf-8") == content
        assert all(f">{entry.name}<" in content for entry in entries)


def test_executor_catalog_exposes_three_state_undo_contract():
    module = _module()
    entries = module.load_entries()
    by_name = {entry.name: entry for entry in entries}

    assert by_name["write_files"].undo_state == module.UNDOABLE
    assert by_name["write_files"].reverse_patterns == (
        "restore_blob_backup", "delete_created_paths")
    assert by_name["share_files"].undo_state == module.UNDOABLE
    assert by_name["share_files"].reverse_patterns == ("module.reverse",)
    assert by_name["open_sites"].undo_outcome_contract == "per_execution"
    assert by_name["set_signatures"].undo_outcome_contract == "per_execution"
    assert by_name["create_processes"].undo_outcome_contract == "per_execution"
    assert by_name["login_urls"].undo_outcome_contract == "per_execution"
    assert by_name["read_files"].undo_state == module.NOT_APPLICABLE
    assert by_name["consult_frontier"].undo_state == module.NOT_APPLICABLE
    assert by_name["consult_frontier"].execution_effect == "read_only"

    assert all(
        entry.reverse_patterns
        for entry in entries if entry.undo_state == module.UNDOABLE)
    assert all(
        entry.execution_effect == "read_only"
        or entry.verb is None or entry.verb in SAFE_VERBS
        for entry in entries if entry.undo_state == module.NOT_APPLICABLE)
    assert all(
        entry.execution_effect != "read_only"
        and entry.verb is not None and entry.verb not in SAFE_VERBS
        for entry in entries if entry.undo_state == module.NOT_UNDOABLE)
    counts = {
        state: sum(entry.undo_state == state for entry in entries)
        for state in (
            module.UNDOABLE, module.NOT_UNDOABLE, module.NOT_APPLICABLE)
    }
    assert counts == {
        module.UNDOABLE: 23,
        module.NOT_UNDOABLE: 13,
        module.NOT_APPLICABLE: 49,
    }

    for lang in ("it", "en"):
        content = module.render(entries, lang)
        assert 'data-undo-census="true"' in content
        assert 'data-undo-state="undoable"' in content
        assert 'data-undo-state="not_undoable"' in content
        assert 'data-undo-state="not_applicable"' in content


def _marked_tokens(content: str, attribute: str, value: str) -> tuple[str, ...]:
    match = re.search(
        rf'<(?P<tag>code|span) {attribute}="{value}">'
        rf'(?P<body>.*?)</(?P=tag)>',
        content,
        flags=re.DOTALL,
    )
    assert match, f"missing {attribute}={value}"
    body = html.unescape(match.group("body"))
    code_tokens = re.findall(r"<code>([^<]+)</code>", body)
    if code_tokens:
        return tuple(token.strip() for token in code_tokens)
    return tuple(token.strip() for token in body.split(","))


def _tuple_constant(path: Path, name: str) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == name
                   for target in node.targets):
                value = ast.literal_eval(node.value)
                assert isinstance(value, tuple)
                return value
    raise AssertionError(f"missing tuple constant {name} in {path}")


def test_architecture_vocabulary_and_primitives_match_runtime_sources():
    engine = ROOT / "runtime" / "engine" / "executor.py"
    structural = _tuple_constant(engine, "ENTRY_PIPELINE_STRUCTURAL")
    semantic = _tuple_constant(engine, "ENTRY_PIPELINE_SEMANTIC")

    for lang in ("it", "en"):
        path = ROOT / "docs" / lang / "architecture" / "index.html"
        content = path.read_text(encoding="utf-8")
        assert _marked_tokens(content, "data-vocab", "actions") == ACTIONS
        assert _marked_tokens(content, "data-vocab", "objects") == OBJECTS
        assert _marked_tokens(
            content, "data-entry-primitives", "structural") == structural
        assert _marked_tokens(
            content, "data-entry-primitives", "semantic") == semantic

    it = (ROOT / "docs" / "it" / "architecture" / "index.html").read_text()
    en = (ROOT / "docs" / "en" / "architecture" / "index.html").read_text()
    assert f"<strong>{len(ACTIONS)} azioni</strong>" in it
    assert f"<strong>{len(OBJECTS)} oggetti</strong>" in it
    assert f"<strong>{len(ACTIONS)} actions</strong>" in en
    assert f"<strong>{len(OBJECTS)} objects</strong>" in en
