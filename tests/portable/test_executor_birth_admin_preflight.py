"""Compact portable oracles for the autonomous RM-0008 preflight."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import executor_birth_admin_preflight as preflight
from contract_boundary_guard import (
    BIRTH_CLOSED_COORDINATOR_STORE_OWNERS,
    BIRTH_CLOSED_EXCEPTION_SCOPES,
    BIRTH_CLOSED_GUARD_VERSION,
    BIRTH_CLOSED_OWNER,
    BIRTH_CLOSED_SCHEMA,
    BIRTH_CLOSED_SEALED_MODULES,
    SCAN_ROOTS,
    SCHEMA as BOUNDARY_INVENTORY_SCHEMA,
)


LINUX_ONLY = pytest.mark.skipif(
    sys.platform != "linux", reason="requires POSIX handle-bound filesystem proof",
)


def _compiled_boundary_inventory_fixture():
    entries = {}

    def add(scope_key, role, capability, closed_exception=None):
        path, scope = scope_key.split(":", 1)
        entry = {
            "capabilities": [capability], "destination": "fixture",
            "path": path, "phase": "M4", "role": role, "scope": scope,
        }
        if closed_exception is not None:
            entry["closed_exception"] = closed_exception
        entries[scope_key] = entry

    add(BIRTH_CLOSED_OWNER, "birth_owner", "birth")
    for scope_key in sorted(BIRTH_CLOSED_COORDINATOR_STORE_OWNERS):
        add(scope_key, "store_owner", "store_write")
    capability_by_exception = {
        "localization_only": "publish_localization",
        "retirement_only": "retire",
        "offline_nonproductive_authoring": "sign",
    }
    for scope_key, exception in sorted(BIRTH_CLOSED_EXCEPTION_SCOPES.items()):
        add(
            scope_key, "offline_authoring", capability_by_exception[exception],
            exception,
        )
    return {
        "birth_closed": {
            "coordinator_store_owners": sorted(
                BIRTH_CLOSED_COORDINATOR_STORE_OWNERS,
            ),
            "exceptions": [
                {"scope": scope, "exception": exception}
                for scope, exception in sorted(BIRTH_CLOSED_EXCEPTION_SCOPES.items())
            ],
            "guard_version": BIRTH_CLOSED_GUARD_VERSION,
            "owner": BIRTH_CLOSED_OWNER,
            "schema": BIRTH_CLOSED_SCHEMA,
            "sealed_modules": list(BIRTH_CLOSED_SEALED_MODULES),
        },
        "entries": [entries[key] for key in sorted(entries)],
        "scan_roots": list(SCAN_ROOTS),
        "schema": BOUNDARY_INVENTORY_SCHEMA,
        "source_census": "public-compiled-policy-fixture",
    }


def _distribution_fixture(tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    release = tmp_path / "release"
    release.mkdir(mode=0o700)
    inventory = _compiled_boundary_inventory_fixture()
    contents = {
        "deployment/admin/preflight.py": b"#!/usr/bin/python3\n",
        "deployment/executor-birth-deployment-v1.json": b"{}",
        "deployment/executor-birth-service-catalog-v1.json": b"{}",
        "deployment/systemd/fixture.service": b"[Unit]\nDescription=fixture\n",
        "internal/reports/boundary.json": preflight._canonical_json(inventory),
        "requirements.lock": b"fixture==1\n",
        "runtime/__version__.py": b'__version__ = "1.2.3"\n',
        "runtime/contract_boundary_guard.py": b"VALUE = 1\n",
        "runtime/contract_store.py": b"VALUE = 1\n",
        "runtime/executor_birth.py": b"VALUE = 1\n",
        "runtime/executor_birth_distribution_manifest.py": b"VALUE = 1\n",
        "runtime/executor_birth_ownership_preflight.py": b"VALUE = 1\n",
        "runtime/sign.py": b"VALUE = 1\n",
    }
    roles = {
        "deployment/admin/preflight.py": "preflight",
        "deployment/executor-birth-deployment-v1.json": "deployment_descriptor",
        "deployment/executor-birth-service-catalog-v1.json": "service_catalog",
        "deployment/systemd/fixture.service": "service_unit",
        "internal/reports/boundary.json": "boundary_inventory",
        "requirements.lock": "dependency_lock",
        "runtime/__version__.py": "product_version",
        "runtime/contract_boundary_guard.py": "boundary_guard",
        "runtime/executor_birth_distribution_manifest.py": "preflight",
        "runtime/executor_birth_ownership_preflight.py": "preflight",
    }
    files = []
    for relative in sorted(contents, key=lambda value: value.encode("utf-8")):
        path = release / relative
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        current = path.parent
        while current.is_relative_to(release):
            current.chmod(0o700)
            if current == release:
                break
            current = current.parent
        path.write_bytes(contents[relative])
        path.chmod(0o600)
        files.append({
            "content_hash": preflight.distribution_file_hash_v1(relative, contents[relative]),
            "path": relative, "role": roles.get(relative, "runtime_code"),
            "size": len(contents[relative]),
        })
    private = Ed25519PrivateKey.generate()
    raw_public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )
    key_id = (
        "distribution-ed25519-v1-sha256-"
        + preflight.hashlib.sha256(raw_public).hexdigest()
    )
    manifest = {
        "architecture": "x86_64",
        "boundary_guard_version": preflight._BIRTH_CLOSED_GUARD_VERSION,
        "boundary_inventory_hash": preflight._digest(
            preflight.BOUNDARY_INVENTORY_DOMAIN,
            contents["internal/reports/boundary.json"],
        ),
        "boundary_inventory_path": "internal/reports/boundary.json",
        "certificate_directory": "/var/lib/metnos/executor-birth",
        "closed_build_id": None,
        "files": files,
        "installation_root": "/var/lib/metnos/executor-birth/releases-v1/00000000000000000001",
        "platform": "linux",
        "preflight_entrypoint": "deployment/admin/preflight.py",
        "previous_closed_build_id": None,
        "product_version": "1.2.3",
        "release_sequence": 1,
        "schema_version": 1,
        "signing_key_id": key_id,
    }
    unsigned = dict(manifest)
    unsigned.pop("closed_build_id")
    manifest["closed_build_id"] = preflight._digest(
        preflight.BUILD_ID_DOMAIN, preflight._canonical_json(unsigned),
    )
    encoded = preflight._canonical_json(manifest)
    signature = private.sign(preflight.SIGNATURE_DOMAIN + encoded)
    registry = preflight._canonical_json({
        "authority": "distribution", "first_release_sequence": 1,
        "key_id": key_id, "last_release_sequence": None,
        "public_key": preflight.base64.b64encode(raw_public).decode("ascii"),
        "purposes": ["closed_distribution_v1"], "schema_version": 1,
    })
    temporary = tmp_path / "openssl-temporary"
    temporary.mkdir(mode=0o700)
    return release, encoded, signature, registry, temporary


def _invalid(callable_, *args, **kwargs) -> preflight.PreflightError:
    with pytest.raises(preflight.PreflightError) as failure:
        callable_(*args, **kwargs)
    assert failure.value.code == preflight.CODE_INVALID
    assert failure.value.exit_status == preflight.EXIT_INVALID
    return failure.value


def _recovery(callable_, *args, **kwargs) -> preflight.PreflightError:
    with pytest.raises(preflight.PreflightError) as failure:
        callable_(*args, **kwargs)
    assert failure.value.code == preflight.CODE_RECOVERY
    assert failure.value.exit_status == preflight.EXIT_RECOVERY
    return failure.value


def test_script_loads_with_isolated_standard_library_only() -> None:
    script = str(Path(preflight.__file__).resolve())
    completed = subprocess.run(
        [
            sys.executable, "-I", "-S", "-c",
            f"import runpy; runpy.run_path({script!r}, run_name='preflight_probe')",
        ],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=10, check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode(
        "utf-8", errors="replace",
    )
    assert completed.stdout == b""
    assert completed.stderr == b""


def test_cli_accepts_only_three_exact_forms() -> None:
    assert preflight.parse_cli_v1(["check-all"]) == ("check-all", None)
    assert preflight.parse_cli_v1(
        ["check", "--entry-id", "service-http"],
    ) == ("check", "service-http")
    assert preflight.parse_cli_v1(
        ["launch", "--entry-id", "entry-installer"],
    ) == ("launch", "entry-installer")
    for argv in (
        [], ["--help"], ["check-all", "extra"],
        ["check", "--entry", "service-http"],
        ["check", "--entry-id", "service-http", "--entry-id", "x"],
        ["launch", "service-http"], ["CHECK-ALL"],
    ):
        _invalid(preflight.parse_cli_v1, argv)


def test_platform_guard_is_a_stable_early_denial(monkeypatch) -> None:
    monkeypatch.setattr(preflight.sys, "platform", "win32")
    with pytest.raises(preflight.PreflightError) as failure:
        preflight.require_linux_before_io_v1()
    assert (failure.value.code, failure.value.exit_status) == (
        preflight.CODE_PLATFORM, preflight.EXIT_PLATFORM,
    )


@LINUX_ONLY
def test_bounded_reader_rejects_link_mode_hardlink_and_replacement(
    tmp_path, monkeypatch,
) -> None:
    tmp_path.chmod(0o700)
    trusted = tmp_path / "trusted"
    trusted.mkdir(mode=0o700)
    target = trusted / "record.json"
    target.write_bytes(b"evidence")
    target.chmod(0o600)
    uid, gid = os.getuid(), os.getgid()
    assert preflight._read_bounded_regular_v1(
        target, 8, uid=uid, gid=gid, mode=0o600, chain_stop=tmp_path,
    ) == b"evidence"

    target.chmod(0o622)
    _invalid(
        preflight._read_bounded_regular_v1,
        target, 8, uid=uid, gid=gid, mode=0o600, chain_stop=tmp_path,
    )
    target.chmod(0o600)
    hardlink = trusted / "second-name"
    os.link(target, hardlink)
    _invalid(
        preflight._read_bounded_regular_v1,
        target, 8, uid=uid, gid=gid, mode=0o600, chain_stop=tmp_path,
    )
    hardlink.unlink()
    symlink = trusted / "link.json"
    symlink.symlink_to(target.name)
    _invalid(
        preflight._read_bounded_regular_v1,
        symlink, 8, uid=uid, gid=gid, mode=0o600, chain_stop=tmp_path,
    )
    _invalid(
        preflight._read_bounded_regular_v1,
        target, 7, uid=uid, gid=gid, mode=0o600, chain_stop=tmp_path,
    )
    _invalid(
        preflight._read_bounded_regular_v1,
        target, 8, uid=uid, gid=gid, mode=0o600,
        chain_stop=Path("relative"),
    )

    original_read = preflight.os.read
    replaced = False

    def replace_live_name(descriptor, size):
        nonlocal replaced
        chunk = original_read(descriptor, size)
        if chunk and not replaced:
            replaced = True
            target.rename(trusted / "old-record.json")
            target.write_bytes(b"evidence")
            target.chmod(0o600)
        return chunk

    monkeypatch.setattr(preflight.os, "read", replace_live_name)
    _invalid(
        preflight._read_bounded_regular_v1,
        target, 8, uid=uid, gid=gid, mode=0o600, chain_stop=tmp_path,
    )


@LINUX_ONLY
def test_distribution_registry_manifest_openssl_and_tree_are_one_binding(
    tmp_path, monkeypatch,
) -> None:
    release, encoded, signature, registry, temporary = _distribution_fixture(tmp_path)
    record = preflight._authenticate_distribution_for_test_v1(
        encoded, signature, registry,
        openssl_executable=Path("/usr/bin/openssl"), temporary_root=temporary,
    )
    assert list(temporary.iterdir()) == []
    preflight._verify_installed_distribution_for_test_v1(record, release)
    assert isinstance(record.facts, tuple) and isinstance(record.files, tuple)
    with pytest.raises(AttributeError):
        record.facts.release_sequence = 2
    with pytest.raises(preflight.PreflightError):
        preflight._AuthenticatedDistributionForTestV1(
            record.facts, record.files, record.encoded, record.signature,
            b"x" * 32,
        )

    profiles = (
        subprocess.CompletedProcess((), 0, b"Signature Verified Successfully\n", b""),
        subprocess.CompletedProcess(
            (), 0, b"Signature Verified Successfully\n",
            b"Using configuration from /dev/null\nextra\n",
        ),
        subprocess.CompletedProcess(
            (), 0, b"Signature Verified Successfully\nextra\n",
            b"Using configuration from /dev/null\n",
        ),
        subprocess.CompletedProcess(
            (), 1, b"", b"Using configuration from /dev/null\n",
        ),
    )
    for completed in profiles:
        with monkeypatch.context() as patcher:
            patcher.setattr(
                preflight, "_run_openssl_bounded_v1",
                lambda _argv: (
                    completed.returncode, completed.stdout, completed.stderr,
                ),
            )
            _invalid(
                preflight._authenticate_distribution_for_test_v1,
                encoded, signature, registry,
                openssl_executable=Path("/usr/bin/openssl"), temporary_root=temporary,
            )
        assert list(temporary.iterdir()) == []

    _invalid(
        preflight._authenticate_distribution_for_test_v1,
        encoded, bytes([signature[0] ^ 1]) + signature[1:], registry,
        openssl_executable=Path("/usr/bin/openssl"), temporary_root=temporary,
    )
    assert list(temporary.iterdir()) == []
    target = release / "runtime" / "executor_birth.py"
    target.write_bytes(b"ALTERED\n")
    target.chmod(0o600)
    _invalid(preflight._verify_installed_distribution_for_test_v1, record, release)


@LINUX_ONLY
def test_exact_tree_rejects_extra_empty_link_hardlink_special_and_bytecode(tmp_path) -> None:
    release, encoded, signature, registry, temporary = _distribution_fixture(tmp_path)
    record = preflight._authenticate_distribution_for_test_v1(
        encoded, signature, registry,
        openssl_executable=Path("/usr/bin/openssl"), temporary_root=temporary,
    )
    target = release / "runtime" / "executor_birth.py"

    extra = release / "hidden.py"
    extra.write_bytes(b"hidden\n")
    extra.chmod(0o600)
    _invalid(preflight._verify_installed_distribution_for_test_v1, record, release)
    extra.unlink()

    empty = release / "empty"
    empty.mkdir(mode=0o700)
    _invalid(preflight._verify_installed_distribution_for_test_v1, record, release)
    empty.rmdir()

    outside_link = tmp_path / "outside-hardlink"
    os.link(target, outside_link)
    _invalid(preflight._verify_installed_distribution_for_test_v1, record, release)
    outside_link.unlink()

    saved = tmp_path / "saved-source"
    target.rename(saved)
    target.symlink_to(saved)
    _invalid(preflight._verify_installed_distribution_for_test_v1, record, release)
    target.unlink()
    saved.rename(target)

    target.rename(saved)
    os.mkfifo(target, mode=0o600)
    _invalid(preflight._verify_installed_distribution_for_test_v1, record, release)
    target.unlink()
    saved.rename(target)

    for bytecode_path in (
        "runtime/__pycache__/x.pyc", "runtime/x.pyo", "runtime/X.PYC",
    ):
        bytecode = preflight.DistributionFileV1(
            bytecode_path, 0,
            preflight.distribution_file_hash_v1(bytecode_path, b""),
            "runtime_code",
        )
        _invalid(preflight._distribution_trie_v1, (bytecode,))


@LINUX_ONLY
def test_exact_tree_detects_live_name_substitution_during_bytes(tmp_path, monkeypatch) -> None:
    fixture_root = tmp_path / "substitution"
    fixture_root.mkdir(mode=0o700)
    release, encoded, signature, registry, temporary = _distribution_fixture(fixture_root)
    record = preflight._authenticate_distribution_for_test_v1(
        encoded, signature, registry,
        openssl_executable=Path("/usr/bin/openssl"), temporary_root=temporary,
    )
    target = release / "runtime" / "executor_birth.py"
    original_content = target.read_bytes()
    original_read = preflight.os.read
    replaced = False

    def replace_open_name(descriptor, size):
        nonlocal replaced
        chunk = original_read(descriptor, size)
        try:
            live_name = os.readlink(f"/proc/self/fd/{descriptor}")
        except OSError:
            live_name = ""
        if chunk and not replaced and live_name == str(target):
            replaced = True
            target.rename(target.with_suffix(".old"))
            target.write_bytes(original_content)
            target.chmod(0o600)
        return chunk

    monkeypatch.setattr(preflight.os, "read", replace_open_name)
    _invalid(preflight._verify_installed_distribution_for_test_v1, record, release)
    assert replaced

    monkeypatch.setattr(preflight.os, "read", original_read)
    semantic_root = tmp_path / "semantic-substitution"
    semantic_root.mkdir(mode=0o700)
    release, encoded, signature, registry, temporary = _distribution_fixture(semantic_root)
    record = preflight._authenticate_distribution_for_test_v1(
        encoded, signature, registry,
        openssl_executable=Path("/usr/bin/openssl"), temporary_root=temporary,
    )
    target = release / "runtime" / "executor_birth.py"
    original_content = target.read_bytes()
    validate_boundary = preflight._validate_boundary_inventory_v1
    replaced = False

    def replace_during_semantics(content):
        nonlocal replaced
        value = validate_boundary(content)
        if not replaced:
            replaced = True
            target.rename(target.with_suffix(".old"))
            target.write_bytes(original_content)
            target.chmod(0o600)
        return value

    monkeypatch.setattr(
        preflight, "_validate_boundary_inventory_v1", replace_during_semantics,
    )
    _invalid(preflight._verify_installed_distribution_for_test_v1, record, release)
    assert replaced


def test_distribution_codecs_reject_registry_role_boundary_and_import_mutants(tmp_path) -> None:
    release, encoded, signature, registry, _temporary = _distribution_fixture(tmp_path)
    registry_value = json.loads(registry)
    registry_value["purposes"] = ["ownership_head_v1"]
    _invalid(
        preflight._decode_distribution_registry_v1,
        preflight._canonical_json(registry_value),
    )

    manifest = json.loads(encoded)
    manifest["files"] = [
        item for item in manifest["files"] if item["role"] != "service_unit"
    ]
    unsigned = dict(manifest)
    unsigned.pop("closed_build_id")
    manifest["closed_build_id"] = preflight._digest(
        preflight.BUILD_ID_DOMAIN, preflight._canonical_json(unsigned),
    )
    _invalid(
        preflight._parse_distribution_manifest_v1,
        preflight._canonical_json(manifest),
    )

    inventory = json.loads((release / "internal/reports/boundary.json").read_bytes())
    inventory["birth_closed"]["sealed_modules"] = []
    _invalid(
        preflight._validate_boundary_inventory_v1,
        preflight._canonical_json(inventory),
    )

    hidden = release / "runtime" / "hidden.py"
    hidden.write_bytes(b"VALUE = 1\n")
    hidden.chmod(0o600)
    files = (preflight.DistributionFileV1(
        "runtime/executor_birth.py", len(b"import hidden as alias\n"),
        preflight.distribution_file_hash_v1(
            "runtime/executor_birth.py", b"import hidden as alias\n",
        ), "runtime_code",
    ),)
    _invalid(
        preflight._verify_local_import_closure_v1,
        release, files, {"runtime/executor_birth.py": b"import hidden as alias\n"},
    )

    windows = json.loads(encoded)
    windows.update({
        "platform": "windows", "installation_root": "C:\\Metnos\\release",
        "certificate_directory": "C:\\Metnos\\ownership",
    })
    unsigned = dict(windows)
    unsigned.pop("closed_build_id")
    windows["closed_build_id"] = preflight._digest(
        preflight.BUILD_ID_DOMAIN, preflight._canonical_json(unsigned),
    )
    parsed, _files = preflight._parse_distribution_manifest_v1(
        preflight._canonical_json(windows),
    )
    assert parsed["installation_root"] == "C:\\Metnos\\release"


def test_manifest_final_bounds_and_fixed_admin_preflight(tmp_path, monkeypatch) -> None:
    _release, encoded, _signature, _registry, _temporary = _distribution_fixture(tmp_path)
    manifest = json.loads(encoded)
    monkeypatch.setattr(preflight, "MAX_MANIFEST_FILES", len(manifest["files"]) - 1)
    _invalid(preflight._parse_distribution_manifest_v1, encoded)
    monkeypatch.setattr(preflight, "MAX_MANIFEST_FILES", 20_000)
    total = sum(item["size"] for item in manifest["files"])
    monkeypatch.setattr(preflight, "MAX_MANIFEST_TOTAL_BYTES", total - 1)
    _invalid(preflight._parse_distribution_manifest_v1, encoded)
    monkeypatch.setattr(
        preflight, "MAX_MANIFEST_TOTAL_BYTES", 2 * 1024 * 1024 * 1024,
    )

    manifest["preflight_entrypoint"] = "runtime/executor_birth_ownership_preflight.py"
    unsigned = dict(manifest)
    unsigned.pop("closed_build_id")
    manifest["closed_build_id"] = preflight._digest(
        preflight.BUILD_ID_DOMAIN, preflight._canonical_json(unsigned),
    )
    _invalid(
        preflight._parse_distribution_manifest_v1,
        preflight._canonical_json(manifest),
    )


def test_real_boundary_policy_snapshot_is_exact_and_entry_schema_is_closed() -> None:
    raw = _compiled_boundary_inventory_fixture()
    encoded = preflight._canonical_json(raw)
    parsed = preflight._validate_boundary_inventory_v1(encoded)
    assert len(parsed["birth_closed"]["coordinator_store_owners"]) == 62
    assert len(parsed["birth_closed"]["exceptions"]) == 16
    for mutate in ("owners", "exceptions", "entry"):
        mutant = json.loads(encoded)
        if mutate == "owners":
            mutant["birth_closed"]["coordinator_store_owners"] = (
                mutant["birth_closed"]["coordinator_store_owners"][:-1]
            )
        elif mutate == "exceptions":
            mutant["birth_closed"]["exceptions"][0]["exception"] = "retirement_only"
        else:
            mutant["entries"][0]["unexpected"] = True
        _invalid(
            preflight._validate_boundary_inventory_v1,
            preflight._canonical_json(mutant),
        )


@LINUX_ONLY
def test_productive_consumption_reauthenticates_before_tree(monkeypatch, tmp_path) -> None:
    _release, encoded, signature, registry, temporary = _distribution_fixture(tmp_path)
    test_record = preflight._authenticate_distribution_for_test_v1(
        encoded, signature, registry,
        openssl_executable=Path("/usr/bin/openssl"), temporary_root=temporary,
    )
    product_record = preflight.AuthenticatedDistributionV1(
        test_record.facts, test_record.files, test_record.encoded,
        test_record.signature, test_record.artifact_binding,
    )
    calls = []

    def stop_after_fixed_reauthentication(authenticated, authenticated_signature):
        calls.append((authenticated, authenticated_signature))
        raise preflight.PreflightError(
            preflight.CODE_INVALID, preflight.EXIT_INVALID, "fixed trust sentinel",
        )

    monkeypatch.setattr(
        preflight, "authenticate_distribution_v1", stop_after_fixed_reauthentication,
    )
    monkeypatch.setattr(
        preflight, "_verify_installed_distribution_core_v1",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("tree reached before fixed reauthentication")
        ),
    )
    _invalid(preflight.verify_installed_distribution_v1, product_record)
    assert calls == [(encoded, signature)]


@LINUX_ONLY
@pytest.mark.parametrize(("descriptor", "size"), ((1, 4096), (2, 4096)))
def test_openssl_stream_exact_bound_is_accepted(descriptor, size) -> None:
    returncode, stdout, stderr = preflight._run_openssl_bounded_v1((
        sys.executable, "-I", "-S", "-c",
        f"import os; os.write({descriptor}, b'x' * {size})",
    ))
    assert returncode == 0
    assert len(stdout if descriptor == 1 else stderr) == size


@LINUX_ONLY
@pytest.mark.parametrize("descriptor", (1, 2))
def test_openssl_overflow_kills_and_reaps_process(
    monkeypatch, descriptor,
) -> None:
    original = preflight.subprocess.Popen
    pids = []

    def capture(*args, **kwargs):
        process = original(*args, **kwargs)
        pids.append(process.pid)
        return process

    monkeypatch.setattr(preflight.subprocess, "Popen", capture)
    _invalid(
        preflight._run_openssl_bounded_v1,
        (
            sys.executable, "-I", "-S", "-c",
            f"import os,time; os.write({descriptor}, b'x' * 4097); time.sleep(30)",
        ),
    )
    assert len(pids) == 1
    with pytest.raises(ChildProcessError):
        os.waitpid(pids[0], os.WNOHANG)


@LINUX_ONLY
def test_openssl_timeout_is_bounded_and_reaps_process(monkeypatch) -> None:
    original = preflight.subprocess.Popen
    pids = []

    def capture(*args, **kwargs):
        process = original(*args, **kwargs)
        pids.append(process.pid)
        return process

    monkeypatch.setattr(preflight.subprocess, "Popen", capture)
    monkeypatch.setattr(preflight, "OPENSSL_TIMEOUT_SECONDS", 0.05)
    started = time.monotonic()
    _invalid(
        preflight._run_openssl_bounded_v1,
        (sys.executable, "-I", "-S", "-c", "import time; time.sleep(30)"),
    )
    assert time.monotonic() - started < 2
    with pytest.raises(ChildProcessError):
        os.waitpid(pids[0], os.WNOHANG)


@LINUX_ONLY
def test_openssl_teardown_timeout_closes_streams_and_requires_recovery() -> None:
    events = []

    class FakeStream:
        def __init__(self, label):
            self.label = label

        def close(self):
            events.append(("close", self.label))

    class FakeProcess:
        stdout = FakeStream("stdout")
        stderr = FakeStream("stderr")

        def poll(self):
            events.append(("poll", None))
            return None

        def kill(self):
            events.append(("kill", None))

        def wait(self, *, timeout):
            events.append(("wait", timeout))
            raise subprocess.TimeoutExpired(("openssl",), timeout)

    _recovery(preflight._teardown_openssl_process_v1, FakeProcess())
    assert events == [
        ("poll", None),
        ("kill", None),
        ("wait", preflight.OPENSSL_TEARDOWN_TIMEOUT_SECONDS),
        ("close", "stdout"),
        ("close", "stderr"),
        ("poll", None),
    ]


@LINUX_ONLY
@pytest.mark.parametrize("valid_openssl_result", (False, True))
def test_openssl_cleanup_attempts_all_resources_and_residue_blocks_retry(
    monkeypatch, tmp_path, valid_openssl_result,
) -> None:
    temporary = tmp_path / "openssl-temporary"
    temporary.mkdir(mode=0o700)
    original_unlink = Path.unlink
    original_rmdir = Path.rmdir
    attempts = []
    failed_once = False

    def fail_first_unlink(path, *args, **kwargs):
        nonlocal failed_once
        attempts.append(("unlink", path.name))
        if path.name == "public-key.pem" and not failed_once:
            failed_once = True
            raise OSError("injected unlink failure")
        return original_unlink(path, *args, **kwargs)

    def record_rmdir(path, *args, **kwargs):
        attempts.append(("rmdir", path.name))
        return original_rmdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_first_unlink)
    monkeypatch.setattr(Path, "rmdir", record_rmdir)
    monkeypatch.setattr(
        preflight, "_run_openssl_bounded_v1",
        lambda _argv: (
            (
                0,
                b"Signature Verified Successfully\n",
                b"Using configuration from /dev/null\n",
            )
            if valid_openssl_result else
            (1, b"", b"Using configuration from /dev/null\n")
        ),
    )
    failure = _recovery(
        preflight._verify_ed25519_openssl_core_v1,
        b"k" * 32, b"payload", b"s" * 64,
        openssl_executable=Path("/usr/bin/openssl"), temporary_root=temporary,
        temporary_uid=os.getuid(), temporary_gid=os.getgid(),
        chain_stop=temporary,
    )
    if valid_openssl_result:
        assert failure.__cause__ is None
    else:
        assert isinstance(failure.__cause__, preflight.PreflightError)
        assert failure.__cause__.code == preflight.CODE_INVALID
    assert {item for item in attempts if item[0] == "unlink"} == {
        ("unlink", "public-key.pem"),
        ("unlink", "payload.bin"),
        ("unlink", "signature.bin"),
    }
    assert any(operation == "rmdir" for operation, _name in attempts)

    with monkeypatch.context() as patcher:
        patcher.setattr(
            preflight, "_run_openssl_bounded_v1",
            lambda _argv: (_ for _ in ()).throw(
                AssertionError("OpenSSL reached before residue denial")
            ),
        )
        _recovery(
            preflight._verify_ed25519_openssl_core_v1,
            b"k" * 32, b"payload", b"s" * 64,
            openssl_executable=Path("/usr/bin/openssl"), temporary_root=temporary,
            temporary_uid=os.getuid(), temporary_gid=os.getgid(),
            chain_stop=temporary,
        )


def test_product_distribution_entries_deny_non_linux_before_io(monkeypatch) -> None:
    monkeypatch.setattr(preflight.sys, "platform", "win32")
    monkeypatch.setattr(
        preflight, "_parse_distribution_manifest_v1",
        lambda *_args: (_ for _ in ()).throw(AssertionError("I/O path reached")),
    )
    for callable_, args in (
        (preflight._load_product_distribution_registry_v1, ()),
        (preflight.authenticate_distribution_v1, (b"", b"")),
        (preflight.verify_installed_distribution_v1, (object(),)),
    ):
        with pytest.raises(preflight.PreflightError) as failure:
            callable_(*args)
        assert failure.value.exit_status == preflight.EXIT_PLATFORM


def test_canonical_json_rejects_duplicate_noncanonical_and_noninteger() -> None:
    encoded = b'{"a":1,"text":"caf\\u00e9"}'
    assert preflight.decode_canonical_json_v1(encoded, len(encoded)) == {
        "a": 1, "text": "caf\N{LATIN SMALL LETTER E WITH ACUTE}",
    }
    for mutant in (
        b'{"a":1,"a":1}', b'{"a": 1}', b'{"a":1.0}',
        b'{"a":NaN}', b'{"text":"caf\xc3\xa9"}',
    ):
        _invalid(preflight.decode_canonical_json_v1, mutant, 1024)
    _invalid(preflight.decode_canonical_json_v1, encoded, len(encoded) - 1)
    _invalid(preflight.decode_canonical_json_v1, encoded, True)
    huge_integer = b'{"a":' + b"9" * 5000 + b"}"
    _invalid(
        preflight.decode_canonical_json_v1, huge_integer, len(huge_integer),
    )
    too_deep = b"[" * 1100 + b"0" + b"]" * 1100
    _invalid(preflight.decode_canonical_json_v1, too_deep, len(too_deep))


def test_canonical_json_accepts_manifest_maximum_node_shape() -> None:
    digest = "sha256:" + "a" * 64
    value = {
        "files": [
            {"content_hash": digest, "path": f"f/{index}", "role": "runtime_code", "size": 0}
            for index in range(20_000)
        ],
        "schema_version": 1,
    }
    encoded = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")
    assert preflight.decode_canonical_json_v1(encoded, 16 * 1024 * 1024) == value


def test_closed_identifier_and_path_grammars() -> None:
    digest = "sha256:" + "a" * 64
    assert preflight.validate_digest_v1(digest) == digest
    assert preflight.validate_entry_id_v1("service-http") == "service-http"
    exact_unit = "a" * 184 + ".service"
    assert len(exact_unit.encode()) == 192
    assert preflight.validate_unit_name_v1(exact_unit) == exact_unit
    assert preflight.validate_absolute_path_v1("/usr/bin/systemctl") == (
        "/usr/bin/systemctl"
    )
    assert preflight.validate_absolute_path_v1("/") == "/"
    assert preflight.validate_relative_path_v1("pkg/main.py") == "pkg/main.py"
    for value in ("SHA256:" + "a" * 64, "sha256:" + "g" * 64, None):
        _invalid(preflight.validate_digest_v1, value)
    for value in ("-bad", "Bad", "a" * 65):
        _invalid(preflight.validate_entry_id_v1, value)
    for value in ("a" * 185 + ".service", "name.socket", "../a.service"):
        _invalid(preflight.validate_unit_name_v1, value)
    for value in (
        "//", "//usr/bin/x", "/usr/../bin/x", "/usr//bin/x",
        "/tmp/a\\b", "/tmp/cafe\N{COMBINING ACUTE ACCENT}", "usr/bin/x",
    ):
        _invalid(preflight.validate_absolute_path_v1, value)
    for value in (
        ".", "/pkg/main.py", "../main.py", "pkg//main.py",
        "pkg\\main.py", "cafe\N{COMBINING ACUTE ACCENT}.py",
    ):
        _invalid(preflight.validate_relative_path_v1, value)


def test_systemctl_argv_is_closed_and_dash_safe() -> None:
    assert preflight.systemctl_show_argv_v1(
        "/usr/bin/systemctl", None, ("Version",),
    ) == (
        "/usr/bin/systemctl", "--no-pager", "--plain", "--all", "show",
        "--property=Version",
    )
    assert preflight.systemctl_show_argv_v1(
        "/usr/bin/systemctl", "-.slice", ("Id", "LoadState"),
    )[-2:] == ("--", "-.slice")
    for unit in (
        "home.mount", "systemd-journald.socket", "system.slice",
        "dev-sda.device", "init.scope",
    ):
        assert preflight.systemctl_show_argv_v1(
            "/usr/bin/systemctl", unit, ("Id", "LoadState"),
        )[-1] == unit
    _invalid(
        preflight.systemctl_show_argv_v1,
        "usr/bin/systemctl", None, ("Version",),
    )
    _invalid(
        preflight.systemctl_show_argv_v1,
        "/", None, ("Version",),
    )
    _invalid(
        preflight.systemctl_show_argv_v1,
        "/usr/bin/systemctl", "a.service", ("LoadState", "Id"),
    )
    _invalid(
        preflight.systemctl_show_argv_v1,
        "/usr/bin/systemctl", r"bad\q.socket", ("Id", "LoadState"),
    )
    _invalid(
        preflight.systemctl_show_argv_v1,
        "/usr/bin/systemctl", "a.service", ("Id", 1),
    )


def test_systemctl_show_parser_preserves_only_allowed_repetition() -> None:
    properties = ("LoadState", "TimersMonotonic")
    parsed = preflight.parse_systemctl_show_v1(
        b"LoadState=loaded\n"
        b"TimersMonotonic={ OnUnitActiveUSec=1d ; next_elapse=2w }\n"
        b"TimersMonotonic={ OnBootUSec=15min ; next_elapse=15min }\n",
        properties,
    )
    assert parsed["LoadState"] == ("loaded",)
    assert len(parsed["TimersMonotonic"]) == 2
    _invalid(
        preflight.parse_systemctl_show_v1,
        b"LoadState=loaded\nLoadState=loaded\n", properties,
    )
    for output in (
        b"Unknown=x\n", b"LoadState=loaded", b"LoadState=loaded\r\n",
        b"LoadState=loaded\n\n", b"LoadState=\xff\n",
    ):
        _invalid(preflight.parse_systemctl_show_v1, output, properties)


def test_manager_version_is_exact() -> None:
    assert preflight.parse_systemd_manager_version_v1(
        b"Version=255.4-1ubuntu8.17\n",
    ) == "255.4-1ubuntu8.17"
    _invalid(
        preflight.parse_systemd_manager_version_v1,
        b"Version=255.4-1ubuntu8.18\n",
    )


def test_systemd_word_tokenizer_is_canonical_and_bounded() -> None:
    assert preflight.tokenize_systemd_words_v1(
        'one "two\\swords" three\\x2dfour',
    ) == ("one", "two words", "three-four")
    for value in (
        " one", "one ", "one  two", '"unterminated', "bad\\q",
        "bad\\x00", "bad\\xc3",
    ):
        _invalid(preflight.tokenize_systemd_words_v1, value)


@pytest.mark.parametrize(("raw", "expected"), [
    ("100ms", "100000"), ("90s", "90000000"),
    ("1min 30s", "90000000"), ("1.5s", "1500000"),
])
def test_duration_normalization_uses_integer_microseconds(
    raw: str, expected: str,
) -> None:
    assert preflight.normalize_systemd_duration_usec_v1(raw) == expected


@pytest.mark.parametrize("raw", [
    "1.0000001s", "0.1us", "1s  2s", "1 s", "-1s", "infinity",
])
def test_duration_normalization_rejects_ambiguous_values(raw: str) -> None:
    _invalid(preflight.normalize_systemd_duration_usec_v1, raw)


def _exec_value(*, extended: bool, flags: str, argv: str = "/bin/x arg") -> str:
    field = "flags" if extended else "ignore_errors"
    return (
        f"{{ path=/bin/x ; argv[]={argv} ; {field}={flags} ; "
        "start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; "
        "status=0/0 }"
    )


def test_exec_pair_preserves_privileged_prefix_and_argv() -> None:
    historical = (_exec_value(extended=False, flags="no"),)
    privileged = (_exec_value(extended=True, flags="no-setuid"),)
    result = preflight.validate_exec_property_pair_v1(
        historical, privileged, ("no-setuid",),
    )
    assert result[0] == {
        "path": "/bin/x", "argv": ("/bin/x", "arg"),
        "flags": ("no-setuid",),
    }
    assert preflight.validate_exec_property_pair_v1(
        historical, (_exec_value(extended=True, flags=""),), (),
    )[0]["flags"] == ()
    for extended in (
        (_exec_value(extended=True, flags="privileged"),),
        (_exec_value(extended=True, flags="ambient"),),
        (_exec_value(extended=True, flags="no-setuid,privileged"),),
    ):
        _invalid(
            preflight.validate_exec_property_pair_v1,
            historical, extended, ("no-setuid",),
        )
    _invalid(
        preflight.validate_exec_property_pair_v1,
        historical,
        (_exec_value(extended=True, flags="no-setuid", argv="/bin/x other"),),
        ("no-setuid",),
    )


def test_exec_pair_accepts_completed_process_and_rejects_mixed_state() -> None:
    historical = _exec_value(extended=False, flags="no").replace(
        "stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0",
        "stop_time=[Fri 2026-08-28 03:38:07 CEST] ; pid=2766513 ; "
        "code=exited ; status=0",
    )
    extended = _exec_value(extended=True, flags="").replace(
        "stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0",
        "stop_time=[Fri 2026-08-28 03:38:07 CEST] ; pid=2766513 ; "
        "code=exited ; status=0",
    )
    assert preflight.validate_exec_property_pair_v1(
        (historical,), (extended,), (),
    )[0]["path"] == "/bin/x"
    _invalid(
        preflight.validate_exec_property_pair_v1,
        (historical,), (extended.replace("status=0", "status=0/0"),), (),
    )
    _invalid(
        preflight.validate_exec_property_pair_v1,
        (historical,), (extended.replace("pid=2766513", "pid=2766514"),), (),
    )
    _invalid(
        preflight.parse_systemd_exec_v1,
        historical.replace(
            "start_time=[n/a]", "start_time=[n/a] ; injected=[x]",
        ),
        extended=False,
    )


def test_timer_parser_matches_real_repeated_systemd_255_shape() -> None:
    observed = preflight.parse_systemd_timer_properties_v1(
        (
            "{ OnUnitActiveUSec=1d ; next_elapse=2w 16min 42.105129s }",
            "{ OnBootUSec=15min ; next_elapse=15min }",
        ),
        ("{ OnCalendar=*-*-* 06,18:00:00 ; next_elapse=[n/a] }",),
    )
    assert observed == {
        "OnUnitActiveUSec": "86400000000",
        "OnBootUSec": "900000000",
        "OnCalendar": "*-*-* 06,18:00:00",
    }
    _invalid(
        preflight.parse_systemd_timer_properties_v1,
        (
            "{ OnBootUSec=15min ; next_elapse=15min }",
            "{ OnBootUSec=15min ; next_elapse=15min }",
        ), (),
    )
    _invalid(
        preflight.parse_systemd_timer_properties_v1,
        ("{ OnBootUSec=15min ; next_elapse=[n/a] ; injected=yes }",), (),
    )
