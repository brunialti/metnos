from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from vocab import OBJECTS


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "generate_domain_reference.py"


def _module():
    spec = importlib.util.spec_from_file_location("domain_reference_docs", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_domain_reference_copy_covers_the_canonical_vocabulary_exactly():
    module = _module()
    module.validate_copy()
    assert set(module.DOMAIN_COPY) == set(OBJECTS) | {"_system"}
    assert all(len(copy.examples_it) >= 2 and len(copy.examples_en) >= 2
               for copy in module.DOMAIN_COPY.values())


def test_domain_reference_operation_names_come_from_signed_contracts():
    module = _module()
    operations = module.load_operations()
    assert set(operations) == set(OBJECTS) | {"_system"}
    assert "find_files" in operations["files"]
    assert "compute_files_loc" in operations["files"]
    assert "create_tasks" in operations["tasks"]
    assert "list_skills" in operations["skills"]
    assert "undo_last_turn" in operations["_system"]


def test_checked_in_domain_reference_is_fresh_bilingual_and_linked():
    module = _module()
    operations = module.load_operations()
    for lang, output in module.OUTPUTS.items():
        content = module.render(lang, operations)
        assert output.read_text(encoding="utf-8") == content
        assert content.count('class="domain-card"') == len(OBJECTS) + 1
        assert content.count('class="example"') >= (len(OBJECTS) + 1) * 2
        assert '<span class="count">1 operazioni documentate</span>' not in content
        assert '<span class="count">1 documented operations</span>' not in content
        for domain in (*OBJECTS, "_system"):
            assert f'id="domain-{domain.lstrip("_")}"' in content
        assert "architecture/tutor.html" in content
        assert "architecture/executor_catalog.html" in content


def test_long_domain_reference_is_semantically_chunked_before_composition():
    from tutor.sources import _document_units

    module = _module()
    files_example = module.DOMAIN_COPY["files"].examples_it[-1]
    messages_example = module.DOMAIN_COPY["messages"].examples_it[0]
    path = ROOT / "docs" / "it" / "domains.html"
    units = _document_units(
        source_id="domain-reference-test",
        lang="it",
        path=path,
        audience="user",
        source_kind="manual",
        priority=80,
    )
    assert len(units) > len(OBJECTS)
    assert max(len(unit.text) for unit in units) <= 920
    assert any(files_example in unit.text for unit in units)
    assert any(messages_example in unit.text for unit in units)
    assert all(not (files_example in unit.text
                    and messages_example in unit.text) for unit in units)


def test_major_public_docs_link_to_the_domain_reference():
    for lang in ("it", "en"):
        paths = (
            ROOT / "docs" / lang / "index.html",
            ROOT / "docs" / lang / "Metnos_QuickTour.html",
            ROOT / "docs" / lang / "architecture" / "index.html",
            ROOT / "docs" / lang / "architecture" / "tutor.html",
        )
        for path in paths:
            content = path.read_text(encoding="utf-8")
            assert "domains.html" in content, path
