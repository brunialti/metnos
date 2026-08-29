"""The one door through which an executor may load another's code."""
from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from admitted_module_v1 import (
    ADMITTED_EXECUTORS_ENV_V1, AdmittedModuleError,
    admitted_code_dependency_projection_v1,
    code_digest_of_bytes_v1, encode_admitted_executor_records_v1,
    load_admitted_module_v1, runtime_admitted_executor_v1,
)

_ENTRY = b"VALUE = 41\n\n\ndef reverse(plan, results):\n    return {'ok': True}\n"
_HELPER = b"HELPED = True\n"
_CURRENT = {}
_TEST_PRIVATE = Ed25519PrivateKey.generate()


def _snapshot(executor):
    return SimpleNamespace(
        name=executor.name,
        manifest_path=Path(executor.manifest_path),
        code_path=Path(executor.code_path),
        digest=executor.digest,
        code_files=tuple(executor.code_files),
    )


def _sign_projection(executor, monkeypatch: pytest.MonkeyPatch) -> None:
    import admitted_module_v1 as admitted

    manifest = Path(executor.manifest_path)
    files = ", ".join(f'"{name}"' for name in executor.code_files)
    manifest_bytes = (
        f'name = "{executor.name}"\n\n[code]\nfiles = [{files}]\n'
        f'digest = "{executor.digest}"\n'
    ).encode("utf-8")
    manifest.write_bytes(manifest_bytes)
    manifest.with_name("manifest.toml.sig").write_bytes(
        _TEST_PRIVATE.sign(manifest_bytes),
    )
    monkeypatch.setattr(
        admitted, "_trusted_public_keys_v1",
        lambda: (_TEST_PRIVATE.public_key(),),
    )


@pytest.fixture(autouse=True)
def _verified_catalog(monkeypatch: pytest.MonkeyPatch):
    import admitted_module_v1 as admitted

    _CURRENT.clear()
    monkeypatch.setattr(
        admitted, "_invalidate_catalog_cache_at_start_v1", lambda: None,
    )
    monkeypatch.setattr(
        admitted,
        "_load_catalog_at_start_v1",
        lambda: SimpleNamespace(get=lambda name: _CURRENT.get(name)),
    )
    monkeypatch.setattr(
        admitted, "_trusted_public_keys_v1",
        lambda: (_TEST_PRIVATE.public_key(),),
    )
    yield
    _CURRENT.clear()


def _published(tmp_path: Path, *, entry: bytes = _ENTRY, signed: bool = True):
    """Lay out one published executor the way the store leaves it."""
    directory = tmp_path / "demo"
    directory.mkdir(parents=True)
    (directory / "demo.py").write_bytes(entry)
    (directory / "helper.py").write_bytes(_HELPER)
    manifest = directory / "manifest.toml"
    digest = code_digest_of_bytes_v1([entry, _HELPER]) if signed else ""
    manifest_bytes = (
        b'name = "demo"\n\n[code]\nfiles = ["demo.py", "helper.py"]\n'
        + f'digest = "{digest}"\n'.encode("utf-8")
    )
    manifest.write_bytes(manifest_bytes)
    manifest.with_name("manifest.toml.sig").write_bytes(
        _TEST_PRIVATE.sign(manifest_bytes),
    )
    executor = SimpleNamespace(
        name="demo", manifest_path=manifest, code_path=directory / "demo.py",
        digest=digest, code_files=("demo.py", "helper.py"),
    )
    _CURRENT[executor.name] = _snapshot(executor)
    return executor


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


def test_unreviewed_module_reverse_is_refused_before_top_level_execution(
        tmp_path: Path):
    marker = tmp_path / "executed"
    entry = (
        b"from pathlib import Path\n"
        + f"Path({str(marker)!r}).touch()\n".encode("utf-8")
        + b"def reverse(plan, results): return {'ok': True}\n"
    )
    executor = _published(tmp_path, entry=entry)
    manifest = Path(executor.manifest_path)
    manifest_bytes = (
        b'name = "demo"\nrevertible = true\n'
        b'reverse_pattern = "module.reverse"\n\n'
        b'[code]\nfiles = ["demo.py", "helper.py"]\n'
        + f'digest = "{executor.digest}"\n'.encode("utf-8")
    )
    manifest.write_bytes(manifest_bytes)
    manifest.with_name("manifest.toml.sig").write_bytes(
        _TEST_PRIVATE.sign(manifest_bytes),
    )

    with pytest.raises(
        AdmittedModuleError, match="admitted_module_reverse_unreviewed",
    ):
        load_admitted_module_v1(executor)
    assert not marker.exists()


def test_reviewed_module_reverse_requires_the_exact_name_and_digest(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import admitted_module_v1 as admitted

    executor = _published(tmp_path)
    manifest = Path(executor.manifest_path)
    manifest_bytes = (
        b'name = "demo"\nrevertible = true\n'
        b'reverse_pattern = "module.reverse"\n\n'
        b'[code]\nfiles = ["demo.py", "helper.py"]\n'
        + f'digest = "{executor.digest}"\n'.encode("utf-8")
    )
    manifest.write_bytes(manifest_bytes)
    manifest.with_name("manifest.toml.sig").write_bytes(
        _TEST_PRIVATE.sign(manifest_bytes),
    )
    monkeypatch.setitem(
        admitted._REVIEWED_MODULE_REVERSE_DIGESTS_V1,
        executor.name,
        executor.digest,
    )

    assert load_admitted_module_v1(executor).reverse({}, {}) == {"ok": True}


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
    _CURRENT[executor.name] = _snapshot(executor)
    with pytest.raises(AdmittedModuleError, match="admitted_module_record_mismatch"):
        load_admitted_module_v1(executor)


def test_a_caller_calculated_digest_cannot_forge_catalog_authority(tmp_path: Path):
    _published(tmp_path)
    forged_root = tmp_path / "forged" / "demo"
    forged_root.mkdir(parents=True)
    payload = b"VALUE = 999\n"
    entry = forged_root / "demo.py"
    entry.write_bytes(payload)
    manifest = forged_root / "manifest.toml"
    manifest.write_text('name = "demo"\n', encoding="utf-8")
    forged = SimpleNamespace(
        name="demo",
        manifest_path=manifest,
        code_path=entry,
        digest=code_digest_of_bytes_v1([payload]),
        code_files=("demo.py",),
    )

    with pytest.raises(AdmittedModuleError, match="admitted_module_record_mismatch"):
        load_admitted_module_v1(forged)


def test_rebinding_loader_functions_cannot_admit_caller_digested_code(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import admitted_module_v1 as admitted

    payload = b"VALUE = 999\n"
    forged_root = tmp_path / "forged" / "demo"
    forged_root.mkdir(parents=True)
    entry = forged_root / "demo.py"
    entry.write_bytes(payload)
    manifest = forged_root / "manifest.toml"
    manifest.write_text(
        'name = "demo"\n\n[code]\nfiles = ["demo.py"]\n'
        f'digest = "{code_digest_of_bytes_v1([payload])}"\n',
        encoding="utf-8",
    )
    forged = SimpleNamespace(
        name="demo", manifest_path=manifest, code_path=entry,
        digest=code_digest_of_bytes_v1([payload]), code_files=("demo.py",),
    )
    monkeypatch.setattr(
        admitted, "_load_catalog_at_start_v1",
        lambda: SimpleNamespace(get=lambda _name: forged),
    )

    with pytest.raises(
        AdmittedModuleError, match="admitted_module_(unreadable|projection_untrusted)",
    ):
        load_admitted_module_v1(forged)


def test_a_record_without_a_publication_is_refused(tmp_path: Path):
    with pytest.raises(AdmittedModuleError, match="admitted_module_record_invalid"):
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
    _CURRENT[executor.name] = _snapshot(executor)
    with pytest.raises(AdmittedModuleError,
                       match="admitted_module_files_undeclared"):
        load_admitted_module_v1(executor)


def test_the_parent_projection_round_trips_through_its_signed_manifest(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    executor = _published(tmp_path)
    _sign_projection(executor, monkeypatch)
    encoded = encode_admitted_executor_records_v1([executor])
    monkeypatch.setenv(ADMITTED_EXECUTORS_ENV_V1, encoded)

    projected = runtime_admitted_executor_v1("demo")

    assert projected.code_files == ("demo.py", "helper.py")
    assert load_admitted_module_v1(projected).VALUE == 41


def test_a_projected_record_with_a_changed_manifest_is_refused(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    executor = _published(tmp_path)
    _sign_projection(executor, monkeypatch)
    monkeypatch.setenv(
        ADMITTED_EXECUTORS_ENV_V1,
        encode_admitted_executor_records_v1([executor]),
    )
    projected = runtime_admitted_executor_v1("demo")
    Path(executor.manifest_path).write_text("not toml", encoding="utf-8")

    with pytest.raises(
        AdmittedModuleError, match="admitted_module_projection_untrusted",
    ):
        load_admitted_module_v1(projected)


def test_mutating_the_projection_environment_cannot_authorize_new_code(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    trusted = _published(tmp_path / "trusted")
    _sign_projection(trusted, monkeypatch)
    evil_root = tmp_path / "evil"
    evil_root.mkdir()
    marker = tmp_path / "executed"
    payload = (
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).touch()\n"
    ).encode("utf-8")
    code = evil_root / "victim.py"
    code.write_bytes(payload)
    manifest = evil_root / "manifest.toml"
    manifest.write_text('name = "victim"\n', encoding="utf-8")
    forged = SimpleNamespace(
        name="victim", manifest_path=manifest, code_path=code,
        digest=code_digest_of_bytes_v1([payload]), code_files=(code.name,),
    )
    monkeypatch.setenv(
        ADMITTED_EXECUTORS_ENV_V1,
        encode_admitted_executor_records_v1([forged]),
    )

    projected = runtime_admitted_executor_v1("victim")
    with pytest.raises(
        AdmittedModuleError, match="admitted_module_unreadable",
    ):
        load_admitted_module_v1(projected)
    assert not marker.exists()


def test_child_cannot_authorize_code_by_replacing_projection_after_launch(
        tmp_path: Path):
    import os
    import subprocess
    import sys

    config = tmp_path / "config" / "keys"
    config.mkdir(parents=True)
    trusted = Ed25519PrivateKey.generate()
    from cryptography.hazmat.primitives import serialization
    config.joinpath("parent_pub.bin").write_bytes(
        trusted.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        ),
    )
    evil = tmp_path / "evil"
    evil.mkdir()
    marker = tmp_path / "child-executed"
    payload = (
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).touch()\n"
    ).encode("utf-8")
    code = evil / "victim.py"
    code.write_bytes(payload)
    manifest = evil / "manifest.toml"
    manifest.write_text('name = "victim"\n', encoding="utf-8")
    forged = SimpleNamespace(
        name="victim", manifest_path=manifest, code_path=code,
        digest=code_digest_of_bytes_v1([payload]), code_files=(code.name,),
    )
    environment = os.environ.copy()
    environment["METNOS_USER_CONFIG"] = str(config.parent)
    environment["FORGED_PROJECTION"] = encode_admitted_executor_records_v1(
        [forged],
    )
    runtime = Path(__file__).resolve().parents[3] / "runtime"
    process = subprocess.run(
        [
            sys.executable, "-c",
            "import os; from admitted_module_v1 import ("
            "ADMITTED_EXECUTORS_ENV_V1, load_admitted_module_v1, "
            "runtime_admitted_executor_v1); "
            "os.environ[ADMITTED_EXECUTORS_ENV_V1] = "
            "os.environ['FORGED_PROJECTION']; "
            "record = runtime_admitted_executor_v1('victim'); "
            "load_admitted_module_v1(record)",
        ],
        capture_output=True,
        env={**environment, "PYTHONPATH": str(runtime)},
        text=True,
        timeout=10,
        check=False,
    )

    assert process.returncode != 0
    assert "admitted_module_" in process.stderr
    assert not marker.exists()


@pytest.mark.parametrize("catalog_value", [None, "wrong-name"])
def test_dependency_projection_fails_closed_on_a_missing_or_mismatched_record(
        tmp_path: Path, catalog_value: str | None):
    consumer = _published(tmp_path)
    consumer.code_dependencies = ("dependency",)
    target_root = tmp_path / "target"
    target_root.mkdir()
    target = _published(target_root) if catalog_value else None
    if target is not None:
        target.name = catalog_value
    catalog = SimpleNamespace(get=lambda _name: target)

    with pytest.raises(
        AdmittedModuleError, match="admitted_module_dependency_unavailable",
    ):
        admitted_code_dependency_projection_v1(consumer, catalog)


def test_dependency_projection_mounts_only_the_records_signer_key(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import sign

    target = _published(tmp_path)
    target.signed_by = "author"
    consumer = SimpleNamespace(code_dependencies=(target.name,))
    keys = tmp_path / "keys"
    keys.mkdir()
    author = keys / "author_pub.bin"
    unrelated = keys / "unrelated_pub.bin"
    author.write_bytes(b"a" * 32)
    unrelated.write_bytes(b"b" * 32)
    monkeypatch.setattr(sign, "KEYS_DIR", keys)

    encoded, roots = admitted_code_dependency_projection_v1(
        consumer, SimpleNamespace(get=lambda name: target),
    )

    assert encoded == encode_admitted_executor_records_v1([target])
    assert roots == [Path(target.manifest_path).parent, author]
    assert unrelated not in roots


def test_dependency_projection_refuses_a_public_key_symlink(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import sign

    target = _published(tmp_path)
    target.signed_by = "author"
    consumer = SimpleNamespace(code_dependencies=(target.name,))
    keys = tmp_path / "keys"
    keys.mkdir()
    private = keys / "author_priv.bin"
    private.write_bytes(b"p" * 32)
    (keys / "author_pub.bin").symlink_to(private)
    monkeypatch.setattr(sign, "KEYS_DIR", keys)

    with pytest.raises(
        AdmittedModuleError, match="admitted_module_dependency_unavailable",
    ):
        admitted_code_dependency_projection_v1(
            consumer, SimpleNamespace(get=lambda _name: target),
        )


def test_dependency_projection_refuses_a_public_key_hardlink(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import os
    import sign

    target = _published(tmp_path)
    target.signed_by = "author"
    consumer = SimpleNamespace(code_dependencies=(target.name,))
    keys = tmp_path / "keys"
    keys.mkdir()
    other = keys / "other_pub.bin"
    other.write_bytes(b"p" * 32)
    os.link(other, keys / "author_pub.bin")
    monkeypatch.setattr(sign, "KEYS_DIR", keys)

    with pytest.raises(
        AdmittedModuleError, match="admitted_module_dependency_unavailable",
    ):
        admitted_code_dependency_projection_v1(
            consumer, SimpleNamespace(get=lambda _name: target),
        )


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


def test_a_link_in_an_intermediate_component_is_refused(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
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
    _CURRENT[executor.name] = _snapshot(executor)
    _sign_projection(executor, monkeypatch)
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


def test_trusted_key_root_fails_closed_without_an_absolute_home(monkeypatch):
    import admitted_module_v1 as admitted

    monkeypatch.setenv("METNOS_USER_CONFIG", "relative/config")
    assert admitted._trusted_keys_dir_at_start_v1() is None

    monkeypatch.delenv("METNOS_USER_CONFIG")
    monkeypatch.setenv("USERPROFILE", "relative/windows-profile")
    monkeypatch.setattr(
        admitted.Path, "home",
        classmethod(lambda _cls: (_ for _ in ()).throw(RuntimeError("no home"))),
    )
    assert admitted._trusted_keys_dir_at_start_v1() is None

    monkeypatch.setenv("USERPROFILE", "/absolute/windows-profile")
    assert admitted._trusted_keys_dir_at_start_v1() == Path(
        "/absolute/windows-profile/.config/metnos/keys",
    )
