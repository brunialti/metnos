"""Contract tests for the canonical boundary policy and standalone projection."""
from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
import importlib.util
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys

import pytest

import contract_boundary_api_policy as api_policy
import contract_boundary_guard as guard
import contract_boundary_policy as policy
import contract_boundary_projection as projection
import contract_boundary_syntax_policy as syntax_policy
import executor_birth_admin_preflight as standalone
from executor_birth_crypto_framing import framed_sha256_v1


ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT = ROOT / "runtime" / "executor_birth_admin_preflight.py"
TOOL = ROOT / "internal" / "tools" / "render_contract_boundary_policy.py"
GOLDEN_DIGEST_V1 = (
    "sha256:b23324d1233d82884912664dd7191b2a454bcdbee35a2934d62821c47957fff1"
)

POLICY_NAMES = (
    "SCHEMA", "BIRTH_CLOSED_SCHEMA", "BIRTH_CLOSED_GUARD_VERSION",
    "SCAN_ROOTS", "AUTHORING_FILES", "BOUNDARY_APIS", "BOUNDARY_MODULES",
    "BOUNDARY_SOURCE_OWNERS", "READ_OPERATIONS", "WRITE_OPERATIONS",
    "PROCESS_CALLS", "DYNAMIC_CODE_LOADER_APIS",
    "DYNAMIC_CODE_LOADER_CANONICALS", "SENSITIVE_FIRST_CLASS_REFERENCES",
    "SENSITIVE_IMPORT_NAMESPACES", "SYS_MODULES_EXPOSING_METHODS",
    "SYS_MODULES_MUTATING_METHODS", "AUTHENTICATED_EXECUTION_SCOPE",
    "AUTHENTICATED_PREFLIGHT_EXECUTION_SCOPE", "LIVE_READER_FORBIDDEN",
    "PUBLISH_CAPABILITIES", "FLOW_CAPABILITIES",
)
LIMIT_NAMES = (
    "SOURCE_FILES", "SOURCE_BYTES", "TOTAL_SOURCE_BYTES", "AST_NODES",
    "TOTAL_AST_NODES", "AST_DEPTH", "SCOPES", "CALLS",
)
REGEX_NAMES = (
    "_AUTHORING_NAME_RE", "_AMBIGUOUS_AUTHORING_ARGUMENT_RE",
    "_STORE_NAME_RE", "_CONTRACT_SCOPE_RE", "_GENERIC_PATH_NAME_RE",
)


def _assert_type_order_exact(left: object, right: object) -> None:
    assert type(left) is type(right)
    if type(left) is dict:
        assert tuple(left) == tuple(right)  # type: ignore[arg-type]
        for key in left:  # type: ignore[union-attr]
            _assert_type_order_exact(left[key], right[key])  # type: ignore[index]
    elif type(left) is tuple:
        assert len(left) == len(right)  # type: ignore[arg-type]
        for first, second in zip(left, right, strict=True):  # type: ignore[arg-type]
            _assert_type_order_exact(first, second)
    elif type(left) is frozenset:
        assert left == right
    elif isinstance(left, re.Pattern):
        assert left.pattern == right.pattern  # type: ignore[union-attr]
        assert type(left.pattern) is type(right.pattern)  # type: ignore[union-attr]
        assert left.flags == right.flags  # type: ignore[union-attr]
    else:
        assert left == right


@pytest.mark.parametrize("name", POLICY_NAMES)
def test_owner_guard_and_standalone_policy_are_type_order_exact(name: str) -> None:
    owner = getattr(policy, name)
    _assert_type_order_exact(owner, getattr(guard, name))
    _assert_type_order_exact(owner, getattr(standalone, name))


@pytest.mark.parametrize("suffix", LIMIT_NAMES)
def test_all_eight_limits_are_exact(suffix: str) -> None:
    owner = getattr(policy, f"MAX_BOUNDARY_{suffix}")
    _assert_type_order_exact(owner, getattr(guard, f"MAX_BOUNDARY_{suffix}"))
    _assert_type_order_exact(
        owner, getattr(standalone, f"MAX_BOUNDARY_{suffix}_V1"),
    )


@pytest.mark.parametrize("name", REGEX_NAMES)
def test_regex_classification_preserves_pattern_type_value_and_flags(name: str) -> None:
    owner = getattr(policy, name)
    _assert_type_order_exact(owner, getattr(guard, name))
    _assert_type_order_exact(owner, getattr(standalone, name))


def test_authoring_facts_are_immutable_and_facade_materializes_legacy_types() -> None:
    assert type(api_policy.BOUNDARY_API_OWNERS_V1) is tuple
    assert type(api_policy.BOUNDARY_MODULE_OWNERS_V1) is tuple
    assert type(api_policy.BOUNDARY_SOURCE_OWNERS_V1) is tuple
    with pytest.raises(FrozenInstanceError):
        api_policy.BOUNDARY_API_OWNERS_V1[0].owner = "changed"  # type: ignore[misc]
    assert type(policy.BOUNDARY_APIS) is dict
    assert type(policy.BOUNDARY_MODULES) is dict
    assert type(policy.BOUNDARY_SOURCE_OWNERS) is dict


def test_contract_convergence_owner_is_exact_in_all_three_registries() -> None:
    owner = "executor_birth_contract_convergence"
    expected_apis = (
        ("<module>", (
            "authoring_read", "authoring_write", "birth", "store_write",
            "verified_store_read",
        )),
        ("_source_generation_has_historical_receipt", (
            "store_write", "verified_store_read",
        )),
        ("_candidate_for_transition", ("authoring_read", "authoring_write")),
        ("converge", (
            "authoring_read", "authoring_write", "birth", "store_write",
            "verified_store_read",
        )),
        ("main", (
            "authoring_read", "authoring_write", "birth", "store_write",
            "verified_store_read",
        )),
    )
    api = next(row for row in api_policy.BOUNDARY_API_OWNERS_V1 if row.owner == owner)
    module = next(
        row for row in api_policy.BOUNDARY_MODULE_OWNERS_V1 if row.owner == owner
    )
    source = next(
        row for row in api_policy.BOUNDARY_SOURCE_OWNERS_V1 if row.owner == owner
    )
    assert api.apis == expected_apis
    assert module.module_names == ("install.executor_birth_contract_convergence",)
    assert (source.path, source.owner) == (
        "install/executor_birth_contract_convergence.py", owner,
    )
    assert tuple(policy.BOUNDARY_APIS[owner].items()) == expected_apis
    assert tuple(standalone.BOUNDARY_APIS[owner].items()) == expected_apis
    assert guard.BOUNDARY_SOURCE_OWNERS[source.path] == owner
    assert standalone.BOUNDARY_SOURCE_OWNERS[source.path] == owner


def test_host_effect_api_has_one_owner_and_complete_module_closure() -> None:
    owner = "executor_birth_host_provisioning"
    api = next(row for row in api_policy.BOUNDARY_API_OWNERS_V1 if row.owner == owner)
    module = next(
        row for row in api_policy.BOUNDARY_MODULE_OWNERS_V1 if row.owner == owner
    )
    sources = tuple(
        row.path for row in api_policy.BOUNDARY_SOURCE_OWNERS_V1
        if row.owner == owner
    )
    assert module.module_names == (
        "install.executor_birth_host_capability",
        "install.executor_birth_host_journal_posix",
        "install.executor_birth_host_posix",
        "install.executor_birth_host_provisioning",
    )
    assert sources == tuple(name.replace(".", "/") + ".py" for name in module.module_names)
    assert tuple(policy.BOUNDARY_APIS[owner].items()) == api.apis
    expected_modules = frozenset(module.module_names)
    assert policy.BOUNDARY_MODULES[owner] == expected_modules
    assert standalone.BOUNDARY_MODULES[owner] == expected_modules


def test_projection_consumes_immutable_records_not_mutable_facade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = projection.render_generated_region_v1()
    monkeypatch.setitem(policy.BOUNDARY_APIS, "mutable_facade_probe", {})
    monkeypatch.setitem(
        policy.BOUNDARY_APIS["executor_birth"],
        "birth_executor", ("mutable_facade_probe",),
    )
    assert projection.render_generated_region_v1() == expected


def test_catalog_mutants_reject_alias_collision_and_capability_order() -> None:
    with pytest.raises(ValueError, match="capability_order"):
        api_policy.BoundaryApiOwnerV1("owner", (("api", ("z", "a")),))
    api_rows = tuple(
        api_policy.BoundaryApiOwnerV1(owner, (("api", ("capability",)),))
        for owner in ("first", "second")
    )
    module_rows = tuple(
        api_policy.BoundaryModuleOwnerV1(owner, ("shared.alias",))
        for owner in ("first", "second")
    )
    source_rows = (
        api_policy.BoundarySourceOwnerV1("runtime/first.py", "first"),
        api_policy.BoundarySourceOwnerV1("runtime/second.py", "second"),
    )
    with pytest.raises(ValueError, match="duplicate:module_aliases"):
        api_policy._validate_catalog_v1(api_rows, module_rows, source_rows)


def test_limit_and_regex_models_reject_bool_and_accept_closed_lower_edge() -> None:
    values = {name: 1 for name in syntax_policy.BoundaryLimitsV1.__dataclass_fields__}
    assert syntax_policy.BoundaryLimitsV1(**values).calls == 1
    for invalid in (0, True):
        candidate = dict(values, calls=invalid)
        with pytest.raises(ValueError, match="boundary_limit_invalid:calls"):
            syntax_policy.BoundaryLimitsV1(**candidate)
    with pytest.raises(ValueError, match="boundary_regex_flags_invalid"):
        syntax_policy.BoundaryRegexV1("probe", True)


def test_projection_digest_rendering_and_checked_source_are_deterministic() -> None:
    first = projection.render_generated_region_v1()
    second = projection.render_generated_region_v1()
    assert first == second
    assert projection.projection_digest_v1() == GOLDEN_DIGEST_V1
    assert first.isascii() and b"\r" not in first and first.endswith(b"\n")
    assert projection.check_generated_region_v1(PREFLIGHT.read_bytes())
    embedded = standalone._BOUNDARY_POLICY_CANONICAL_ASCII_V1
    assert embedded == projection.canonical_payload_v1()
    assert framed_sha256_v1(
        projection.PROJECTION_DIGEST_DOMAIN_V1, embedded,
    ) == standalone._BOUNDARY_POLICY_PROJECTION_SHA256_V1


def test_projection_is_deterministic_across_hash_seeds() -> None:
    code = (
        f"import sys;sys.path.insert(0,{str(ROOT / 'runtime')!r});"
        "import contract_boundary_projection as p;"
        "sys.stdout.buffer.write(p.render_generated_region_v1())"
    )
    rendered = []
    for seed in ("1", "777"):
        environment = dict(os.environ, PYTHONHASHSEED=seed)
        result = subprocess.run(
            [sys.executable, "-c", code], check=True, capture_output=True,
            env=environment,
        )
        rendered.append(result.stdout)
    assert rendered[0] == rendered[1] == projection.render_generated_region_v1()


def test_stale_tampered_and_invalid_markers_fail_closed() -> None:
    source = PREFLIGHT.read_bytes()
    tampered = source.replace(GOLDEN_DIGEST_V1.encode(), b"sha256:" + b"0" * 64, 1)
    assert not projection.check_generated_region_v1(tampered)
    repaired = projection.replace_generated_region_v1(tampered)
    assert projection.check_generated_region_v1(repaired)
    assert projection.replace_generated_region_v1(repaired) == repaired
    missing = source.replace(projection.END_MARKER_V1, b"missing-marker", 1)
    with pytest.raises(projection.ContractBoundaryProjectionError):
        projection.check_generated_region_v1(missing)
    duplicate = source + projection.BEGIN_MARKER_V1 + b"\n"
    with pytest.raises(projection.ContractBoundaryProjectionError):
        projection.check_generated_region_v1(duplicate)
    reversed_markers = (
        projection.END_MARKER_V1 + b"\nbody\n"
        + projection.BEGIN_MARKER_V1 + b"\n"
    )
    with pytest.raises(
        projection.ContractBoundaryProjectionError, match="marker_order",
    ):
        projection.check_generated_region_v1(reversed_markers)


def test_fixed_tool_check_and_standalone_isolated_loading() -> None:
    checked = subprocess.run(
        [sys.executable, str(TOOL), "--check"], cwd=ROOT,
        check=False, capture_output=True, text=True,
    )
    assert checked.returncode == 0, checked.stderr
    code = (
        "import runpy;"
        f"compile(open({str(PREFLIGHT)!r},'rb').read(),{str(PREFLIGHT)!r},'exec');"
        f"d=runpy.run_path({str(PREFLIGHT)!r});"
        "print(d['_BOUNDARY_POLICY_PROJECTION_SHA256_V1'])"
    )
    isolated = subprocess.run(
        [sys.executable, "-I", "-S", "-c", code], check=True,
        capture_output=True, text=True,
    )
    assert isolated.stdout.strip() == GOLDEN_DIGEST_V1


@pytest.mark.skipif(os.name != "posix", reason="tests the POSIX projection writer")
def test_fixed_tool_check_rejects_drift(tmp_path: Path) -> None:
    replica = tmp_path / "repo"
    (replica / "runtime").mkdir(parents=True)
    (replica / "internal" / "tools").mkdir(parents=True)
    runtime_names = (
        "contract_boundary_analyzer_ast.py",
        "contract_boundary_analyzer_projection.py",
        "contract_boundary_analyzer_types.py",
        "contract_boundary_api_policy.py", "contract_boundary_module_policy.py",
        "contract_boundary_syntax_policy.py",
        "contract_boundary_policy_types.py",
        "contract_boundary_role_policy.py",
        "contract_boundary_birth_authority_policy.py",
        "contract_boundary_birth_exception_policy.py",
        "contract_boundary_birth_policy.py",
        "contract_boundary_policy.py", "contract_boundary_projection.py",
        "executor_birth_account_identity.py", "executor_birth_canonical.py",
        "executor_birth_crypto_framing.py", "executor_birth_host_layout.py",
        "executor_birth_host_path_policy.py",
        "executor_birth_host_provisioning_evidence.py",
        "executor_birth_legacy_state_journal.py",
        "executor_birth_legacy_state_policy.py",
        "executor_birth_legacy_state_preflight_projection.py",
        "executor_birth_legacy_state_request.py",
        "executor_birth_legacy_state_wire.py",
        "executor_birth_admin_preflight.py",
    )
    for name in runtime_names:
        shutil.copy2(ROOT / "runtime" / name, replica / "runtime" / name)
    copied_tool = replica / "internal" / "tools" / TOOL.name
    shutil.copy2(TOOL, copied_tool)
    clean = subprocess.run([sys.executable, str(copied_tool), "--check"])
    assert clean.returncode == 0
    target = replica / "runtime" / "executor_birth_admin_preflight.py"
    before_noop = target.stat()
    assert subprocess.run(
        [sys.executable, str(copied_tool), "--write"],
    ).returncode == 0
    after_noop = target.stat()
    assert (after_noop.st_ino, after_noop.st_mtime_ns) == (
        before_noop.st_ino, before_noop.st_mtime_ns,
    )
    target.write_bytes(target.read_bytes().replace(
        GOLDEN_DIGEST_V1.encode(), b"sha256:" + b"0" * 64, 1,
    ))
    stale = subprocess.run([sys.executable, str(copied_tool), "--check"])
    assert stale.returncode == 1
    written = subprocess.run([sys.executable, str(copied_tool), "--write"])
    assert written.returncode == 0
    repaired = target.read_bytes()
    assert subprocess.run(
        [sys.executable, str(copied_tool), "--check"],
    ).returncode == 0
    before_second_noop = target.stat()
    assert subprocess.run(
        [sys.executable, str(copied_tool), "--write"],
    ).returncode == 0
    assert target.read_bytes() == repaired
    after_second_noop = target.stat()
    assert (after_second_noop.st_ino, after_second_noop.st_mtime_ns) == (
        before_second_noop.st_ino, before_second_noop.st_mtime_ns,
    )


def test_check_mode_never_imports_posix_write_dependencies() -> None:
    probe = (
        "import builtins,runpy,sys; original=builtins.__import__;"
        "\ndef guarded(name,*args,**kwargs):\n"
        " if name=='fcntl': raise AssertionError('POSIX dependency in check mode')\n"
        " return original(name,*args,**kwargs)\n"
        "builtins.__import__=guarded;"
        "sys.argv=[sys.argv[1],'--check'];runpy.run_path(sys.argv[0],run_name='__main__')"
    )
    checked = subprocess.run(
        [sys.executable, "-B", "-c", probe, str(TOOL)],
        check=False, capture_output=True, text=True,
    )
    assert checked.returncode == 0, checked.stderr


def _load_render_tool_v1():
    spec = importlib.util.spec_from_file_location("boundary_render_tool_test", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX directory locks")
def test_writer_cas_preserves_changed_target_and_cleans_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = _load_render_tool_v1()
    target = tmp_path / "preflight.py"
    target.write_bytes(b"changed")
    monkeypatch.setattr(tool, "TARGET_V1", target)
    with pytest.raises(
        projection.ContractBoundaryProjectionError, match="target_changed",
    ):
        tool._atomic_write_v1(b"expected", b"replacement")
    assert target.read_bytes() == b"changed"
    assert not tuple(tmp_path.glob(".preflight.py.*"))


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX fchmod")
def test_writer_closes_descriptor_when_setup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = _load_render_tool_v1()
    target = tmp_path / "preflight.py"
    target.write_bytes(b"current")
    monkeypatch.setattr(tool, "TARGET_V1", target)
    real_close = tool.os.close
    closed: list[int] = []

    def tracked_close(descriptor: int) -> None:
        closed.append(descriptor)
        real_close(descriptor)

    monkeypatch.setattr(tool.os, "close", tracked_close)
    monkeypatch.setattr(
        tool.os, "fchmod",
        lambda descriptor, mode: (_ for _ in ()).throw(OSError("probe")),
    )
    with pytest.raises(OSError, match="probe"):
        tool._write_temporary_v1(b"replacement", 0o600)
    assert closed and not tuple(tmp_path.glob(".preflight.py.*"))


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX directory fsync")
def test_writer_fsyncs_content_and_parent_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = _load_render_tool_v1()
    target = tmp_path / "preflight.py"
    target.write_bytes(b"current")
    monkeypatch.setattr(tool, "TARGET_V1", target)
    real_fsync = tool.os.fsync
    directory_flags: list[bool] = []

    def tracked_fsync(descriptor: int) -> None:
        directory_flags.append(stat.S_ISDIR(os.fstat(descriptor).st_mode))
        real_fsync(descriptor)

    monkeypatch.setattr(tool.os, "fsync", tracked_fsync)
    tool._atomic_write_v1(b"current", b"replacement")
    assert target.read_bytes() == b"replacement"
    assert False in directory_flags and True in directory_flags


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX flock")
def test_writer_parent_lock_excludes_a_second_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = _load_render_tool_v1()
    target = tmp_path / "preflight.py"
    target.write_bytes(b"current")
    monkeypatch.setattr(tool, "TARGET_V1", target)
    probe = (
        "import errno,fcntl,os,sys;"
        "fd=os.open(sys.argv[1],os.O_RDONLY|getattr(os,'O_DIRECTORY',0));"
        "\ntry:\n fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)"
        "\nexcept OSError as exc:\n"
        " sys.exit(0 if exc.errno in (errno.EACCES,errno.EAGAIN) else 2)"
        "\nelse:\n sys.exit(1)"
    )
    with tool._locked_parent_v1():
        result = subprocess.run(
            [sys.executable, "-c", probe, str(tmp_path)], check=False,
        )
    assert result.returncode == 0


def test_preflight_has_no_local_policy_or_renderer_import() -> None:
    tree = ast.parse(PREFLIGHT.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert not imported & {
        "contract_boundary_analyzer_ast",
        "contract_boundary_analyzer_projection",
        "contract_boundary_analyzer_types",
        "contract_boundary_api_policy", "contract_boundary_birth_policy",
        "contract_boundary_policy",
        "contract_boundary_projection", "contract_boundary_syntax_policy",
        "executor_birth_canonical", "executor_birth_crypto_framing",
    }


def test_projection_has_no_io_or_dynamic_execution_surface() -> None:
    path = ROOT / "runtime" / "contract_boundary_projection.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert not imported & {"os", "pathlib", "runpy", "subprocess", "tempfile"}
    assert "contract_boundary_policy" not in imported
    assert {
        "contract_boundary_api_policy", "contract_boundary_birth_policy",
        "contract_boundary_syntax_policy",
    } <= imported
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not calls & {"eval", "exec", "open"}


def test_policy_modules_and_functions_obey_size_limits() -> None:
    names = (
        "contract_boundary_api_policy.py", "contract_boundary_module_policy.py",
        "contract_boundary_syntax_policy.py",
        "contract_boundary_policy_types.py",
        "contract_boundary_role_policy.py",
        "contract_boundary_birth_authority_policy.py",
        "contract_boundary_birth_exception_policy.py",
        "contract_boundary_birth_policy.py",
        "contract_boundary_policy.py", "contract_boundary_projection.py",
    )
    for name in names:
        source = (ROOT / "runtime" / name).read_text(encoding="utf-8")
        assert len(source.splitlines()) <= 400
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.end_lineno - node.lineno + 1 <= 40
