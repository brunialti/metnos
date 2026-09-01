from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "internal/tools/rm0008_repin_source_roots.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("rm0008_repin_source_roots", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_private_pin_writer_repairs_independently_divergent_bindings(
    tmp_path: Path,
) -> None:
    values = {
        "runtime/executor_birth_admin_preflight.py": (
            '_BIRTH_CLOSED_SOURCE_REVIEW_SHA256 = "sha256:' + "1" * 64 + '"\n'
        ),
        "runtime/contract_boundary_guard.py": (
            'BIRTH_CLOSED_SOURCE_REVIEW_SHA256 = "sha256:' + "2" * 64 + '"\n'
        ),
        "scripts/publish-public.sh": (
            'PRIVATE_SOURCE_REVIEW_SHA256="sha256:' + "3" * 64 + '"\n'
            "PRIVATE_SOURCE_REVIEW_COUNT=7\n"
        ),
        "internal/reports/rm0007-m4-boundary-inventory.json": (
            '{\n  "schema": 1,\n  "source_census": "sha256:' + "4" * 64 + '"\n}\n'
        ),
    }
    for relative, content in values.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    root = "sha256:" + "a" * 64
    _load_tool()._write_private_pin(tmp_path, root, 19)

    for relative in values:
        assert root in (tmp_path / relative).read_text(encoding="utf-8")
    assert "PRIVATE_SOURCE_REVIEW_COUNT=19" in (
        tmp_path / "scripts/publish-public.sh"
    ).read_text(encoding="utf-8")
