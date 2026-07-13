from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "generate_executor_catalog.py"


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
