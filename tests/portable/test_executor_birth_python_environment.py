from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import ast
import json
from pathlib import Path
import runpy
import subprocess
import sys
import venv

import pytest

import executor_birth_python_environment as environment


LOCK_BYTES = b"aiohttp==3.13.5 --hash=sha256:0123456789abcdef\n"
PROFILE = "linux-x86_64-cpython-312"
LOCK_HASH = environment.python_dependency_lock_hash_v1(PROFILE, LOCK_BYTES)


def _hash(path: str, content: bytes) -> str:
    return environment.python_environment_file_hash_v1(
        path, len(content), (content,),
    )


def _file(
    path: str = "bin/python", content: bytes = b"elf",
    mode: int = 0o755,
) -> environment.PythonEnvironmentFileV1:
    return environment.PythonEnvironmentFileV1(
        path, len(content), mode, _hash(path, content),
    )


def _record() -> environment.PythonEnvironmentV1:
    files = (
        _file(),
        _file(
            "lib/python3.12/site-packages/aiohttp/__init__.py",
            b"__version__ = '3.13.5'\n", 0o644,
        ),
        _file(
            "lib/python3.12/site-packages/yarl/_quoting.so", b"elf", 0o755,
        ),
        _file("pyvenv.cfg", b"include-system-site-packages = false\n", 0o644),
    )
    return environment.build_python_environment_v1(
        profile=PROFILE,
        dependency_lock_hash=LOCK_HASH, files=files,
    )


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    ).encode("ascii")


def test_profile_is_closed_arch_specific_and_venv_based() -> None:
    assert environment.PYTHON_ENVIRONMENT_PROFILES_V1 == (
        "linux-aarch64-cpython-312", "linux-x86_64-cpython-312",
    )
    x86 = environment.python_environment_profile_v1(
        "linux-x86_64-cpython-312",
    )
    arm = environment.python_environment_profile_v1(
        "linux-aarch64-cpython-312",
    )
    assert x86.soabi == "cpython-312-x86_64-linux-gnu"
    assert arm.soabi == "cpython-312-aarch64-linux-gnu"
    assert x86.python_relative == "bin/python"
    assert x86.site_packages_relative == "lib/python3.12/site-packages"
    for invalid in (True, 1, "linux-x86_64-cpython-313", "windows-x86_64-cpython-312"):
        with pytest.raises(environment.PythonEnvironmentError):
            environment.python_environment_profile_v1(invalid)


def test_lock_hash_derives_the_fixed_external_environment_root() -> None:
    record = _record()
    expected = f"/var/lib/metnos/python-envs-v1/{LOCK_HASH.removeprefix('sha256:')}"
    assert record.environment_root == expected
    assert record.python_executable == expected + "/bin/python"
    assert record.site_packages == expected + "/lib/python3.12/site-packages"
    assert "/opt/metnos/.venv" not in environment.encode_python_environment_v1(record).decode()
    assert "python-runtime-v1" not in environment.encode_python_environment_v1(record).decode()
    assert environment.python_dependency_lock_hash_v1(PROFILE, LOCK_BYTES) == LOCK_HASH
    assert environment.python_dependency_lock_hash_v1(
        "linux-aarch64-cpython-312", LOCK_BYTES,
    ) != LOCK_HASH
    assert environment.python_dependency_lock_hash_v1(
        PROFILE, LOCK_BYTES + b"# drift\n",
    ) != LOCK_HASH


def test_record_round_trips_canonical_and_is_frozen() -> None:
    record = _record()
    encoded = environment.encode_python_environment_v1(record)
    assert encoded == _canonical(json.loads(encoded))
    assert environment.decode_python_environment_v1(encoded) == record
    with pytest.raises(FrozenInstanceError):
        record.environment_id = LOCK_HASH  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        record.files[0].mode = 0o644  # type: ignore[misc]


def test_environment_id_has_a_stable_golden_value() -> None:
    assert _record().environment_id == (
        "sha256:f08ee69204555343200118b27acdabe7"
        "e41ba3dae5fc9259b76ef088fd6636ae"
    )


@pytest.mark.parametrize("field", (
    "profile", "platform", "architecture", "implementation", "python_version",
    "cache_tag", "soabi", "environment_root", "python_executable",
    "site_packages", "dependency_lock_hash",
))
def test_string_metadata_tamper_is_rejected(field: str) -> None:
    value = json.loads(environment.encode_python_environment_v1(_record()))
    value[field] = value[field] + "-changed"
    with pytest.raises(environment.PythonEnvironmentError):
        environment.decode_python_environment_v1(_canonical(value))


def test_launcher_and_system_site_metadata_tamper_are_rejected() -> None:
    value = json.loads(environment.encode_python_environment_v1(_record()))
    value["launcher_flags"].append("-S")
    with pytest.raises(environment.PythonEnvironmentError):
        environment.decode_python_environment_v1(_canonical(value))
    value = json.loads(environment.encode_python_environment_v1(_record()))
    value["system_site_packages"] = True
    with pytest.raises(environment.PythonEnvironmentError):
        environment.decode_python_environment_v1(_canonical(value))
    value = json.loads(environment.encode_python_environment_v1(_record()))
    value["python_home"] = "python-runtime-v1"
    with pytest.raises(environment.PythonEnvironmentError):
        environment.decode_python_environment_v1(_canonical(value))


def test_file_and_identifier_tamper_are_rejected() -> None:
    value = json.loads(environment.encode_python_environment_v1(_record()))
    value["files"][0]["content_hash"] = "sha256:" + "f" * 64
    with pytest.raises(environment.PythonEnvironmentError):
        environment.decode_python_environment_v1(_canonical(value))
    value = json.loads(environment.encode_python_environment_v1(_record()))
    value["environment_id"] = "sha256:" + "f" * 64
    with pytest.raises(environment.PythonEnvironmentError):
        environment.decode_python_environment_v1(_canonical(value))


@pytest.mark.parametrize("suffix", (
    "__pycache__/x.py", "x.pyc", "x.PYO", "load.pth",
    "SiteCustomiZe.py", "USERCUSTOMIZE.PY", ".venv/payload.py",
))
def test_automatic_code_and_development_venv_files_are_rejected(suffix: str) -> None:
    path = "lib/python3.12/site-packages/" + suffix
    with pytest.raises(environment.PythonEnvironmentError):
        _file(path, b"x", 0o644)


@pytest.mark.parametrize("path", (
    "python-runtime-v1/os.py", "/bin/python", "bin/python3",
    "lib/python3.12/os.py", "../bin/python", "lib//package.py",
    "lib\\package.py", "lib/python3.12/site-packages/a/\x00x.py",
    "lib/python3.12/site-packages/e\u0301.py",
))
def test_paths_are_canonical_and_limited_to_the_production_venv(path: str) -> None:
    with pytest.raises(environment.PythonEnvironmentError):
        _file(path, b"x", 0o644)


def test_link_kind_extra_keys_and_noncanonical_json_are_rejected() -> None:
    encoded = environment.encode_python_environment_v1(_record())
    value = json.loads(encoded)
    value["files"][0]["kind"] = "symlink"
    with pytest.raises(environment.PythonEnvironmentError):
        environment.decode_python_environment_v1(_canonical(value))
    value = json.loads(encoded)
    value["files"][0]["link_target"] = "/usr/bin/python3.12"
    with pytest.raises(environment.PythonEnvironmentError):
        environment.decode_python_environment_v1(_canonical(value))
    with pytest.raises(environment.PythonEnvironmentError):
        environment.decode_python_environment_v1(encoded + b"\n")
    duplicate = encoded[:-1] + b',"profile":"linux-x86_64-cpython-312"}'
    with pytest.raises(environment.PythonEnvironmentError):
        environment.decode_python_environment_v1(duplicate)


def test_exact_types_order_duplicates_modes_and_coverage_are_enforced() -> None:
    record = _record()
    with pytest.raises(environment.PythonEnvironmentError):
        environment.build_python_environment_v1(
            profile=record.profile.name, dependency_lock_hash=LOCK_HASH,
            files=tuple(reversed(record.files)),
        )
    with pytest.raises(environment.PythonEnvironmentError):
        replace(record, files=record.files + (record.files[-1],))
    with pytest.raises(environment.PythonEnvironmentError):
        replace(record, files=record.files[1:])
    with pytest.raises(environment.PythonEnvironmentError):
        environment.PythonEnvironmentFileV1("bin/python", True, 0o755, LOCK_HASH)
    with pytest.raises(environment.PythonEnvironmentError):
        environment.PythonEnvironmentFileV1("bin/python", 1, 0o644, LOCK_HASH)
    with pytest.raises(environment.PythonEnvironmentError):
        environment.PythonEnvironmentFileV1("pyvenv.cfg", 1, 0o755, LOCK_HASH)


def test_framed_file_hash_is_path_size_and_content_bound() -> None:
    path = "lib/python3.12/site-packages/pkg/data.bin"
    content = b"abcdef"
    expected = environment.python_environment_file_hash_v1(
        path, len(content), (b"ab", b"", b"cdef"),
    )
    assert expected == _hash(path, content)
    assert expected != _hash(path + ".new", content)
    assert expected != _hash(path, content + b"x")
    for size, chunks in ((5, (content,)), (7, (content,)), (6, (bytearray(content),))):
        with pytest.raises(environment.PythonEnvironmentError):
            environment.python_environment_file_hash_v1(path, size, chunks)


def test_isolated_launcher_without_dash_s_loads_the_venv_site(tmp_path) -> None:
    # The runner may itself use a base interpreter; own the tested environment.
    root = tmp_path / "launch-env"
    venv.EnvBuilder(with_pip=False).create(root)
    executable = root / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    script = (
        "import json,site,sys;"
        "print(json.dumps([sys.flags.isolated,sys.flags.dont_write_bytecode,"
        "sys.flags.no_site,'site' in sys.modules,sys.prefix,sys.base_prefix,"
        "any('site-packages' in item for item in sys.path)]))"
    )
    completed = subprocess.run(
        [str(executable), "-I", "-B", "-c", script], check=True,
        capture_output=True, text=True, timeout=30,
    )
    isolated, no_bytecode, no_site, loaded, prefix, base, has_site = json.loads(
        completed.stdout,
    )
    assert (isolated, no_bytecode, no_site, loaded) == (1, 1, 0, True)
    assert Path(prefix).resolve() == root.resolve()
    assert base == sys.base_prefix and prefix != base
    assert has_site is True
    assert environment.PYTHON_ENVIRONMENT_LAUNCH_FLAGS_V1 == ("-I", "-B")


def test_import_is_pure_and_module_size_limits_hold() -> None:
    source_path = Path(environment.__file__)
    namespace = runpy.run_path(source_path.as_posix())
    assert namespace["PYTHON_ENVIRONMENT_ROOT_V1"] == "/var/lib/metnos/python-envs-v1"
    lines = source_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 400
    tree = ast.parse("\n".join(lines))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert node.end_lineno is not None
            assert node.end_lineno - node.lineno + 1 <= 40, node.name
