"""Contracts for the canonical analyzer primitives and standalone projection."""
from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, asdict, fields
import importlib.util
import inspect
import os
from pathlib import Path
import subprocess
import sys

import pytest

import contract_boundary_analyzer_ast as analyzer_ast
import contract_boundary_analyzer_projection as projection
import contract_boundary_analyzer_types as analyzer_types
import contract_boundary_guard as guard
import contract_boundary_projection as policy_projection
import executor_birth_admin_preflight as standalone
import executor_birth_distribution_manifest as distribution_manifest


ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT = ROOT / "runtime" / "executor_birth_admin_preflight.py"
TOOL = ROOT / "internal" / "tools" / "render_contract_boundary_policy.py"
GOLDEN_DIGEST_V1 = (
    "sha256:28f38d6a85d1719a223c03afbb2c3c5a7dd7309eafffcbf1c33cb7af1241dd9a"
)


def test_guard_reexports_owner_types_while_standalone_types_are_distinct() -> None:
    assert guard.ScopeFacts is analyzer_types.ScopeFacts
    assert guard.Finding is analyzer_types.Finding
    assert standalone.ScopeFacts is not analyzer_types.ScopeFacts
    assert standalone.Finding is not analyzer_types.Finding
    assert fields(analyzer_types.ScopeFacts) == fields(guard.ScopeFacts)
    assert tuple(field.name for field in fields(standalone.ScopeFacts)) == tuple(
        field.name for field in fields(analyzer_types.ScopeFacts)
    )
    values = ("runtime/probe.py", "probe", 7, ("sign",), ("call",))
    owner = analyzer_types.ScopeFacts(*values)
    projected = standalone.ScopeFacts(*values)
    assert asdict(owner) == asdict(projected)
    assert owner.key == projected.key
    assert owner != projected
    with pytest.raises(FrozenInstanceError):
        owner.path = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        projected.path = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("public", "private"),
    analyzer_ast.ANALYZER_AST_HELPER_CATALOG_V1,
)
def test_guard_and_standalone_expose_owner_equivalent_helpers(
    public: str, private: str,
) -> None:
    owner = getattr(analyzer_ast, public)
    assert getattr(guard, public) is owner
    assert getattr(guard, private) is owner
    assert getattr(standalone, private) is getattr(standalone, public)
    assert getattr(standalone, public) is not owner
    assert inspect.signature(getattr(standalone, public)) == inspect.signature(owner)


def test_ast_helper_field_matrix_is_equivalent() -> None:
    expression = ast.parse("alias.child.leaf", mode="eval").body
    target = ast.parse("(first, [second, third]) = value").body[0].targets[0]
    strings = ast.parse("VALUE = 'a' + 'b'; OTHER = f'c'; dynamic = f'{name}'")
    aliases = {"alias": "runtime.sign"}
    pairs = (
        ("leaf_name_v1", (expression,)),
        ("dotted_name_v1", (expression,)),
        ("module_leaf_v1", ("runtime.sign",)),
        ("target_names_v1", (target,)),
        ("static_string_v1", (strings.body[0].value,)),
        ("static_strings_v1", (strings,)),
        ("resolved_alias_name_v1", (expression, aliases)),
        ("has_bound_root_v1", (expression, aliases)),
    )
    for name, arguments in pairs:
        expected = getattr(analyzer_ast, name)(*arguments)
        assert getattr(standalone, name)(*arguments) == expected
    assert tuple(standalone.string_values_v1(strings)) == tuple(
        analyzer_ast.string_values_v1(strings)
    )
    assert analyzer_ast.static_string_v1(strings.body[2].value) is None


def test_projection_digest_source_and_checked_region_are_deterministic() -> None:
    assert projection.OWNER_PATHS_V1 == (
        "runtime/contract_boundary_analyzer_types.py",
        "runtime/contract_boundary_analyzer_ast.py",
    )
    first = projection.render_generated_region_v1()
    assert first == projection.render_generated_region_v1()
    assert projection.projection_digest_v1() == GOLDEN_DIGEST_V1
    assert first.isascii() and b"\r" not in first and first.endswith(b"\n")
    assert projection.check_generated_region_v1(PREFLIGHT.read_bytes())
    assert standalone._BOUNDARY_ANALYZER_PROJECTION_SHA256_V1 == GOLDEN_DIGEST_V1
    tree = ast.parse(projection.canonical_source_v1())
    assert not any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in tree.body)


def test_owner_catalogs_are_closed_over_exports() -> None:
    public = tuple(
        name for name, _legacy in analyzer_ast.ANALYZER_AST_HELPER_CATALOG_V1
    )
    assert tuple(analyzer_ast.__all__) == (
        "ANALYZER_AST_HELPER_CATALOG_V1", *public,
    )
    assert tuple(analyzer_types.__all__) == (
        "ANALYZER_TYPE_NAMES_V1", *analyzer_types.ANALYZER_TYPE_NAMES_V1,
    )


def test_owner_catalog_validators_reject_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as scoped:
        scoped.setattr(
            analyzer_ast,
            "ANALYZER_AST_HELPER_CATALOG_V1",
            analyzer_ast.ANALYZER_AST_HELPER_CATALOG_V1[:-1],
        )
        with pytest.raises(ValueError, match="catalog_exports"):
            analyzer_ast._validate_analyzer_ast_catalog_v1()
    with monkeypatch.context() as scoped:
        scoped.setattr(
            analyzer_types,
            "ANALYZER_TYPE_NAMES_V1",
            (*analyzer_types.ANALYZER_TYPE_NAMES_V1, "Finding"),
        )
        with pytest.raises(ValueError, match="catalog_invalid"):
            analyzer_types._validate_analyzer_type_catalog_v1()


def test_guard_imports_and_aliases_match_owner_catalog_exactly() -> None:
    tree = ast.parse((ROOT / "runtime/contract_boundary_guard.py").read_bytes())
    ast_import = next(
        node for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "contract_boundary_analyzer_ast"
    )
    type_import = next(
        node for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "contract_boundary_analyzer_types"
    )
    catalog = analyzer_ast.ANALYZER_AST_HELPER_CATALOG_V1
    assert tuple(item.name for item in ast_import.names) == tuple(
        public for public, _legacy in catalog
    )
    assert tuple(item.name for item in type_import.names) == (
        analyzer_types.ANALYZER_TYPE_NAMES_V1
    )
    expected = tuple((legacy, public) for public, legacy in catalog)
    expected_names = {name for pair in expected for name in pair}
    observed = tuple(
        (node.targets[0].id, node.value.id)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Name)
        and ({node.targets[0].id, node.value.id} & expected_names)
    )
    assert observed == expected
    assert not hasattr(projection, "_AST_FUNCTIONS_V1")
    assert not hasattr(projection, "_LEGACY_ALIASES_V1")


def test_analyzer_owners_are_required_release_files_in_exact_registry() -> None:
    assert tuple(standalone._REQUIRED_MANIFEST_PATHS.items()) == tuple(
        distribution_manifest._REQUIRED_PATH_ROLES.items()
    )
    for name in (
        "contract_boundary_analyzer_ast.py",
        "contract_boundary_analyzer_projection.py",
        "contract_boundary_analyzer_types.py",
    ):
        relative = f"runtime/{name}"
        assert standalone._REQUIRED_MANIFEST_PATHS[relative] == "runtime_code"


def test_projection_is_deterministic_across_hash_seeds() -> None:
    code = (
        f"import sys;sys.path.insert(0,{str(ROOT / 'runtime')!r});"
        "import contract_boundary_analyzer_projection as p;"
        "sys.stdout.buffer.write(p.render_generated_region_v1())"
    )
    rendered = []
    for seed in ("1", "777"):
        result = subprocess.run(
            [sys.executable, "-c", code], check=True, capture_output=True,
            env=dict(os.environ, PYTHONHASHSEED=seed),
        )
        rendered.append(result.stdout)
    assert rendered[0] == rendered[1] == projection.render_generated_region_v1()


def test_missing_duplicate_reversed_and_stale_markers_fail_closed() -> None:
    source = PREFLIGHT.read_bytes()
    stale = source.replace(b"return node.id", b"return node.attr", 1)
    assert not projection.check_generated_region_v1(stale)
    repaired = projection.replace_generated_region_v1(stale)
    assert projection.check_generated_region_v1(repaired)
    missing = source.replace(projection.END_MARKER_V1, b"missing-marker", 1)
    with pytest.raises(projection.ContractBoundaryAnalyzerProjectionError):
        projection.check_generated_region_v1(missing)
    duplicate = source + projection.BEGIN_MARKER_V1 + b"\n"
    with pytest.raises(projection.ContractBoundaryAnalyzerProjectionError):
        projection.check_generated_region_v1(duplicate)
    reversed_markers = (
        projection.END_MARKER_V1 + b"\nbody\n"
        + projection.BEGIN_MARKER_V1 + b"\n"
    )
    with pytest.raises(
        projection.ContractBoundaryAnalyzerProjectionError,
        match="marker_order",
    ):
        projection.check_generated_region_v1(reversed_markers)


@pytest.mark.parametrize(
    ("owner_name", "before", "after"),
    (
        (
            "contract_boundary_analyzer_ast.py",
            b"return node.id\n",
            b"return forbidden_probe(node)\n",
        ),
        (
            "contract_boundary_analyzer_types.py",
            b"@dataclass(frozen=True)\n",
            b"@untrusted(frozen=True)\n",
        ),
    ),
)
def test_projection_rejects_free_name_and_decorator_drift(
    owner_name: str, before: bytes, after: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources = projection._read_owner_sources_v1()
    mutated = tuple(
        (relative, source.replace(before, after, 1))
        if relative.endswith(owner_name) else (relative, source)
        for relative, source in sources
    )
    monkeypatch.setattr(projection, "_read_owner_sources_v1", lambda: mutated)
    with pytest.raises(
        projection.ContractBoundaryAnalyzerProjectionError,
        match="definition_free_names",
    ):
        projection.canonical_source_v1()


@pytest.mark.parametrize(
    "collision",
    (
        b"_leaf_name = object()\n",
        b"def leaf_name_v1():\n    pass\n",
        b"class ScopeFacts:\n    pass\n",
    ),
)
def test_projection_rejects_reserved_names_outside_generated_region(
    collision: bytes,
) -> None:
    source = PREFLIGHT.read_bytes() + collision
    with pytest.raises(
        projection.ContractBoundaryAnalyzerProjectionError,
        match="external_name_collision",
    ):
        projection.check_generated_region_v1(source)
    with pytest.raises(
        projection.ContractBoundaryAnalyzerProjectionError,
        match="external_name_collision",
    ):
        projection.replace_generated_region_v1(source)


def test_standalone_projection_loads_with_isolated_standard_library() -> None:
    code = (
        "import ast,runpy;"
        f"n=runpy.run_path({str(PREFLIGHT)!r});"
        "v=ast.parse('alias.child',mode='eval').body;"
        "assert n['dotted_name_v1'](v)=='alias.child'"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", code],
        check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def _load_render_tool_v1():
    spec = importlib.util.spec_from_file_location("analyzer_render_tool_test", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(os.name != "posix", reason="tests the POSIX projection writer")
def test_renderer_updates_both_regions_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = _load_render_tool_v1()
    target = tmp_path / "preflight.py"
    source = PREFLIGHT.read_bytes()
    stale = source.replace(
        policy_projection.projection_digest_v1().encode("ascii"),
        b"sha256:" + b"0" * 64,
        1,
    ).replace(b"return node.id", b"return node.attr", 1)
    target.write_bytes(stale)
    monkeypatch.setattr(tool, "TARGET_V1", target)
    assert tool.main(["--write"]) == 0
    updated = target.read_bytes()
    assert policy_projection.check_generated_region_v1(updated)
    assert projection.check_generated_region_v1(updated)

    malformed = updated.replace(projection.END_MARKER_V1, b"missing-marker", 1)
    target.write_bytes(malformed)
    assert tool.main(["--write"]) == 2
    assert target.read_bytes() == malformed


def test_analyzer_modules_and_functions_obey_size_limits() -> None:
    names = (
        "contract_boundary_analyzer_ast.py",
        "contract_boundary_analyzer_projection.py",
        "contract_boundary_analyzer_types.py",
    )
    for name in names:
        source = (ROOT / "runtime" / name).read_text(encoding="utf-8")
        assert len(source.splitlines()) <= 400
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.end_lineno - node.lineno + 1 <= 40
