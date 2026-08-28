"""The one door through which an executor may load another's code."""
from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from admitted_module_v1 import (
    ADMITTED_EXECUTORS_ENV_V1, AdmittedModuleError,
    code_digest_of_bytes_v1, encode_admitted_executor_records_v1,
    load_admitted_module_v1, runtime_admitted_executor_v1,
)

_ENTRY = b"VALUE = 41\n\n\ndef reverse(plan, results):\n    return {'ok': True}\n"
_HELPER = b"HELPED = True\n"


def _published(tmp_path: Path, *, entry: bytes = _ENTRY, signed: bool = True):
    """Lay out one published executor the way the store leaves it."""
    directory = tmp_path / "demo"
    directory.mkdir()
    (directory / "demo.py").write_bytes(entry)
    (directory / "helper.py").write_bytes(_HELPER)
    manifest = directory / "manifest.toml"
    manifest.write_text(
        'name = "demo"\n\n[code]\nfiles = ["demo.py", "helper.py"]\n',
        encoding="utf-8",
    )
    digest = code_digest_of_bytes_v1([entry, _HELPER]) if signed else ""
    return SimpleNamespace(
        name="demo", manifest_path=manifest, code_path=directory / "demo.py",
        digest=digest, code_files=("demo.py", "helper.py"),
    )


def test_the_door_agrees_with_the_signer_on_what_the_digest_is(tmp_path: Path):
    """One digest, two ways of reaching it: they must not drift apart.

    The signer reopens each declared file; the door digests the bytes it is
    about to run.  Same value, no gap between the check and the run.
    """
    import sign

    executor = _published(tmp_path)
    directory = Path(executor.manifest_path).parent
    assert code_digest_of_bytes_v1([_ENTRY, _HELPER]) == (
        "sha256:" + hashlib.sha256(_ENTRY + _HELPER).hexdigest()
    )
    assert code_digest_of_bytes_v1([_ENTRY, _HELPER]) == sign.compute_code_digest(
        directory, ["demo.py", "helper.py"],
    )


def test_signed_code_loads_and_the_module_works(tmp_path: Path):
    module = load_admitted_module_v1(_published(tmp_path))
    assert module.VALUE == 41
    assert module.reverse({}, {}) == {"ok": True}


def test_standard_dataclass_decorators_work_during_isolated_execution(
        tmp_path: Path):
    entry = (
        b"from dataclasses import dataclass\n"
        b"@dataclass\n"
        b"class Value:\n"
        b"    number: int\n"
    )
    module = load_admitted_module_v1(_published(tmp_path, entry=entry))
    assert module.Value(3).number == 3


def test_code_changed_after_the_signature_is_refused(tmp_path: Path):
    """The point of the door: the bytes about to run must be the signed ones."""
    executor = _published(tmp_path)
    Path(executor.code_path).write_bytes(_ENTRY + b"VALUE = 999\n")
    with pytest.raises(AdmittedModuleError, match="admitted_module_digest_mismatch"):
        load_admitted_module_v1(executor)


def test_a_sibling_changed_after_the_signature_is_refused_too(tmp_path: Path):
    """The signature covers every declared file, not only the entry."""
    executor = _published(tmp_path)
    (Path(executor.manifest_path).parent / "helper.py").write_bytes(b"HELPED = 0\n")
    with pytest.raises(AdmittedModuleError, match="admitted_module_digest_mismatch"):
        load_admitted_module_v1(executor)


def test_without_a_signed_digest_only_the_installed_distribution_is_admitted(
        tmp_path: Path, monkeypatch):
    """No digest means the distribution's own code, and nothing else."""
    import config as runtime_config

    executor = _published(tmp_path, signed=False)
    monkeypatch.setattr(runtime_config, "PATH_EXECUTORS", tmp_path / "elsewhere")
    with pytest.raises(AdmittedModuleError,
                       match="admitted_module_outside_distribution"):
        load_admitted_module_v1(executor)

    monkeypatch.setattr(runtime_config, "PATH_EXECUTORS", tmp_path)
    assert load_admitted_module_v1(executor).VALUE == 41


def test_a_caller_cannot_point_the_door_at_a_file_of_its_choosing(tmp_path: Path):
    """The entry is the first declared file, never one the caller names."""
    executor = _published(tmp_path)
    intruder = Path(executor.manifest_path).parent / "intruder.py"
    intruder.write_bytes(b"VALUE = 0\n")
    executor.code_path = intruder
    with pytest.raises(AdmittedModuleError, match="admitted_module_entry_mismatch"):
        load_admitted_module_v1(executor)


def test_a_record_without_a_publication_is_refused(tmp_path: Path):
    with pytest.raises(AdmittedModuleError, match="admitted_module_unpublished"):
        load_admitted_module_v1(
            SimpleNamespace(manifest_path="", code_path=None, digest=""),
        )


@pytest.mark.parametrize("files", [
    ("../demo.py",),
    ("demo.py", "DEMO.py"),
    ("demo.py", "demo.py"),
    ("/demo.py",),
])
def test_non_portable_or_colliding_record_paths_are_refused(
        tmp_path: Path, files: tuple[str, ...]):
    executor = _published(tmp_path)
    executor.code_files = files
    with pytest.raises(AdmittedModuleError,
                       match="admitted_module_files_undeclared"):
        load_admitted_module_v1(executor)


def test_the_parent_projection_round_trips_without_reopening_the_manifest(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    executor = _published(tmp_path)
    encoded = encode_admitted_executor_records_v1([executor])
    Path(executor.manifest_path).write_text("not toml", encoding="utf-8")
    monkeypatch.setenv(ADMITTED_EXECUTORS_ENV_V1, encoded)

    projected = runtime_admitted_executor_v1("demo")

    assert projected.code_files == ("demo.py", "helper.py")
    assert load_admitted_module_v1(projected).VALUE == 41


def test_a_link_in_place_of_the_code_is_refused(tmp_path: Path):
    """The final component is opened without following a link."""
    executor = _published(tmp_path)
    entry = Path(executor.code_path)
    real = entry.parent / "real.py"
    real.write_bytes(_ENTRY)
    entry.unlink()
    entry.symlink_to(real)
    with pytest.raises(AdmittedModuleError, match="admitted_module_unreadable"):
        load_admitted_module_v1(executor)


def test_a_link_in_an_intermediate_component_is_refused(tmp_path: Path):
    executor = _published(tmp_path)
    directory = Path(executor.manifest_path).parent
    real = directory / "real"
    real.mkdir()
    (real / "entry.py").write_bytes(_ENTRY)
    link = directory / "linked"
    link.symlink_to(real, target_is_directory=True)
    executor.code_files = ("linked/entry.py",)
    executor.code_path = link / "entry.py"
    executor.digest = code_digest_of_bytes_v1([_ENTRY])
    with pytest.raises(AdmittedModuleError, match="admitted_module_unreadable"):
        load_admitted_module_v1(executor)


def test_the_gate_never_imports_this_door():
    """Proved, not asserted: no Birth-gate module reaches this one.

    Inside the gate, dynamic evaluation is forbidden outright, because code
    evaluated there could rebuild the authority that writes to disk.  This door
    evaluates code by design, so it must stay outside — and stay unreachable
    from inside.
    """
    import ast

    import config as runtime_config

    root = Path(runtime_config.PATH_RUNTIME)
    offenders: list[str] = []
    for path in sorted(root.glob("executor_birth*.py")):
        tree = ast.parse(path.read_bytes(), filename=path.name)
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            if any(name.split(".")[0] == "admitted_module_v1" for name in names):
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == [], offenders
