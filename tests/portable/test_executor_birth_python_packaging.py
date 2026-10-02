"""The closed release includes and preserves its packaged sidecar runtime."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
import pytest

import executor_birth_python_environment as environment

if sys.platform != "linux":
    pytest.skip("Linux release packaging and POSIX environment sealing", allow_module_level=True)

from install import executor_birth_python_environment_posix as installer


ROOT = Path(__file__).resolve().parents[2]


def _locked_versions() -> dict[str, str]:
    encoded = (ROOT / "requirements-linux-x86_64.lock").read_bytes()
    installer._lock_requirements_v1(encoded)
    return dict(line.split(" ", 1)[0].split("==") for line in encoded.decode().splitlines())


@pytest.mark.parametrize("relative", (
    "requirements.txt", "runtime/playwright_sidecar/requirements.txt",
))
def test_closed_release_lock_covers_declared_runtime_requirements(relative) -> None:
    locked = _locked_versions()
    for line in (ROOT / relative).read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        requirement = Requirement(line)
        if requirement.marker and not requirement.marker.evaluate({
            "python_version": "3.12", "sys_platform": "linux",
        }):
            continue
        name = canonicalize_name(requirement.name)
        assert name in locked, f"{relative}: missing {name} in closed release"
        assert locked[name] in requirement.specifier


def test_closed_release_lock_includes_playwright_runtime_dependency_closure() -> None:
    # Runtime Requires-Dist from the pinned Playwright 1.61.0 and pyee wheels;
    # greenlet has no runtime dependencies, only optional docs/test extras.
    locked = _locked_versions()
    for text in ("playwright==1.61.0", "pyee>=13,<14", "greenlet>=3.1.1,<4", "typing-extensions"):
        requirement = Requirement(text)
        assert requirement.name in locked, f"missing runtime dependency: {requirement.name}"
        assert locked[requirement.name] in requirement.specifier


@pytest.mark.skipif(sys.platform != "linux", reason="POSIX environment sealing")
@pytest.mark.parametrize("input_mode, expected_mode", (
    (0o644, 0o644), (0o755, 0o755), (0o700, 0o755),
    (0o777, 0o755), (0o6755, 0o755), (0o6644, 0o644),
))
def test_sealing_preserves_execution_without_special_or_write_permissions(
    tmp_path, input_mode, expected_mode,
) -> None:
    root = tmp_path / "environment"
    driver = root / "lib/python3.12/site-packages/example/driver"
    driver.parent.mkdir(parents=True)
    driver.write_bytes(b"#!/bin/sh\nexit 0\n")
    driver.chmod(input_mode)
    assert stat.S_IMODE(driver.stat().st_mode) == input_mode
    installer._seal_tree_v1(root, (os.geteuid(), os.getegid()))
    assert stat.S_IMODE(driver.stat().st_mode) == expected_mode
    assert (driver.stat().st_uid, driver.stat().st_gid) == (os.geteuid(), os.getegid())
    assert stat.S_IMODE(root.stat().st_mode) == 0o755
    if expected_mode == 0o755:
        subprocess.run([str(driver)], stdin=subprocess.DEVNULL, check=True, timeout=10)


def test_packaged_executable_mode_is_bound_to_the_environment_inventory() -> None:
    files = []
    for path, content, mode in (
        ("bin/python", b"python", 0o755),
        ("lib/python3.12/site-packages/example/driver", b"driver", 0o755),
        ("pyvenv.cfg", b"include-system-site-packages = false\n", 0o644),
    ):
        files.append(environment.PythonEnvironmentFileV1(
            path, len(content), mode,
            environment.python_environment_file_hash_v1(path, len(content), (content,)),
        ))
    record = environment.build_python_environment_v1(
        profile="linux-x86_64-cpython-312",
        dependency_lock_hash=environment.python_dependency_lock_hash_v1(
            "linux-x86_64-cpython-312", b"example==1\n",
        ), files=tuple(files),
    )
    encoded = environment.encode_python_environment_v1(record)
    assert environment.decode_python_environment_v1(encoded) == record
    drift = json.loads(encoded)
    drift["files"][1]["mode"] = 0o644
    with pytest.raises(environment.PythonEnvironmentError):
        environment.decode_python_environment_v1(json.dumps(
            drift, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
        ).encode("ascii"))


@pytest.mark.parametrize("relative", (
    "__pycache__/helper.cpython-314.pyc",
    "__pycache__/helper.cpython-312.opt-2.pyc",
    "helper.pyc", "helper.pyo",
))
def test_wheel_bytecode_with_source_is_removed_before_sealing(tmp_path, relative):
    package = tmp_path / "lib/python3.12/site-packages/example"
    cache = package / relative
    cache.parent.mkdir(parents=True)
    source = package / "helper.py"
    source.write_bytes(b"VALUE = 42\n")
    cache.write_bytes(b"foreign interpreter bytecode")
    installer._remove_packaged_bytecode_v1(tmp_path)
    assert not cache.exists()
    assert not (package / "__pycache__").exists()
    assert source.read_bytes() == b"VALUE = 42\n"
    installer._remove_packaged_bytecode_v1(tmp_path)  # repeat is harmless


@pytest.mark.parametrize("source_kind", ("missing", "symlink", "directory"))
def test_packaged_bytecode_requires_a_regular_source(tmp_path, source_kind):
    package = tmp_path / "lib/python3.12/site-packages/example"
    package.mkdir(parents=True)
    source = package / "helper.py"
    if source_kind == "symlink":
        source.symlink_to(tmp_path / "outside.py")
        (tmp_path / "outside.py").write_text("VALUE = 42\n")
    elif source_kind == "directory":
        source.mkdir()
    cache = package / "helper.pyc"
    cache.write_bytes(b"cache")
    with pytest.raises(installer.PythonEnvironmentPosixError):
        installer._remove_packaged_bytecode_v1(tmp_path)
    assert cache.read_bytes() == b"cache"


def test_bytecode_cleanup_never_follows_links_or_removes_other_files(tmp_path):
    package = tmp_path / "lib/python3.12/site-packages/example"
    package.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "helper.pyc").write_bytes(b"untouched")
    (package / "__pycache__").symlink_to(outside, target_is_directory=True)
    hook = package / "load.pth"
    hook.write_bytes(b"import example\n")
    installer._remove_packaged_bytecode_v1(tmp_path)
    assert (outside / "helper.pyc").read_bytes() == b"untouched"
    assert hook.read_bytes() == b"import example\n"
    with pytest.raises(environment.PythonEnvironmentError):
        environment.python_environment_file_hash_v1(
            hook.relative_to(tmp_path).as_posix(), hook.stat().st_size,
            (hook.read_bytes(),),
        )


def test_bytecode_link_is_rejected_even_when_source_exists(tmp_path):
    package = tmp_path / "lib/python3.12/site-packages/example"
    package.mkdir(parents=True)
    (package / "helper.py").write_text("VALUE = 42\n")
    outside = tmp_path / "outside.pyc"
    outside.write_bytes(b"untouched")
    (package / "helper.pyc").symlink_to(outside)
    with pytest.raises(installer.PythonEnvironmentPosixError):
        installer._remove_packaged_bytecode_v1(tmp_path)
    assert outside.read_bytes() == b"untouched"
