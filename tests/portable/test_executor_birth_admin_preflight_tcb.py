"""Fast discriminating tests for the autonomous administrative OpenSSL TCB."""
from __future__ import annotations

import hashlib
import json
import os
import struct
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import NamedTuple

import pytest

import executor_birth_admin_preflight as preflight


LINUX_ONLY = pytest.mark.skipif(
    sys.platform != "linux", reason="requires Linux ELF and POSIX filesystem",
)


def _independent_framed_hash(domain: bytes, path: str, content: bytes) -> str:
    encoded_path = path.encode("utf-8")
    framing = (
        len(encoded_path).to_bytes(8, "big") + encoded_path
        + len(content).to_bytes(8, "big") + content
    )
    return "sha256:" + hashlib.sha256(domain + framing).hexdigest()


def _minimal_elf64(
    architecture: str,
    interpreter: str = "/trusted/lib/ld-linux.so",
    *,
    interpreter_count: int = 1,
    terminated: bool = True,
) -> bytes:
    machine = {"x86_64": 62, "aarch64": 183}[architecture]
    ident = bytearray(16)
    ident[:7] = b"\x7fELF\x02\x01\x01"
    program_count = max(1, interpreter_count)
    header_size = 64
    program_size = 56
    payload = interpreter.encode("utf-8") + (b"\0" if terminated else b"")
    payload_offset = header_size + program_count * program_size
    header = struct.pack(
        "<16sHHIQQQIHHHHHH",
        bytes(ident), 3, machine, 1, 0, header_size, 0, 0,
        header_size, program_size, program_count, 0, 0, 0,
    )
    programs = []
    for index in range(program_count):
        program_type = 3 if index < interpreter_count else 1
        programs.append(struct.pack(
            "<IIQQQQQQ",
            program_type, 4, payload_offset, 0, 0,
            len(payload), len(payload), 1,
        ))
    return header + b"".join(programs) + payload


def _elf_mutant(kind: str) -> bytes:
    if kind == "no-interpreter":
        return _minimal_elf64("x86_64", interpreter_count=0)
    if kind == "two-interpreters":
        return _minimal_elf64("x86_64", interpreter_count=2)
    if kind == "relative-interpreter":
        return _minimal_elf64("x86_64", "ld-linux.so")
    if kind == "unterminated-interpreter":
        return _minimal_elf64("x86_64", terminated=False)

    value = bytearray(_minimal_elf64("x86_64"))
    if kind == "magic":
        value[0] = 0
    elif kind == "class":
        value[4] = 1
    elif kind == "endianness":
        value[5] = 2
    elif kind == "machine":
        struct.pack_into("<H", value, 18, 183)
    elif kind == "interpreter-bounds":
        struct.pack_into("<Q", value, 64 + 32, len(value) + 1)
    else:  # pragma: no cover - the parametrization is closed below
        raise AssertionError(kind)
    return bytes(value)


def _assert_invalid(call, *args) -> preflight.PreflightError:
    with pytest.raises(preflight.PreflightError) as failure:
        call(*args)
    assert failure.value.code == preflight.CODE_INVALID
    return failure.value


def test_tcb_hashes_and_document_match_independent_normative_framing() -> None:
    path = "/trusted/bin/openssl"
    content = b"\x00openssl\xff"
    executable_hash = _independent_framed_hash(
        b"metnos.executor-birth.administrative-executable/v1\0",
        path, content,
    )
    file_hash = _independent_framed_hash(
        b"metnos.executor-birth.openssl-tcb-file/v1\0", path, content,
    )

    assert preflight._administrative_executable_hash_v1(
        path, content,
    ) == executable_hash
    assert preflight._openssl_tcb_file_hash_v1(path, content) == file_hash
    assert executable_hash != file_hash

    files = (
        preflight._OpenSslTcbFileV1(path, len(content), file_hash),
    )
    encoded, observed_hash = preflight._openssl_tcb_document_v1(
        "/trusted/lib/ld-linux.so", "/trusted/lib/ossl-modules", files,
    )
    expected_value = {
        "schema_version": 1,
        "command_profile": "ed25519-pkeyutl-v1",
        "config_path": "/dev/null",
        "provider": "default",
        "elf_loader": "/trusted/lib/ld-linux.so",
        "module_directory": "/trusted/lib/ossl-modules",
        "files": [{
            "path": path, "size": len(content), "content_hash": file_hash,
        }],
    }
    expected_encoded = json.dumps(
        expected_value, ensure_ascii=True, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("ascii")
    expected_hash = "sha256:" + hashlib.sha256(
        b"metnos.executor-birth.openssl-tcb/v1\0" + expected_encoded
    ).hexdigest()
    assert encoded == expected_encoded
    assert observed_hash == expected_hash

    assert preflight._administrative_executable_hash_v1(
        path + "-other", content,
    ) != executable_hash
    assert preflight._administrative_executable_hash_v1(
        path, content + b"!",
    ) != executable_hash


@pytest.mark.parametrize(
    ("architecture", "machine"), (("x86_64", 62), ("aarch64", 183)),
)
def test_minimal_elf64_extracts_the_only_signed_architecture_interpreter(
    architecture: str, machine: int,
) -> None:
    encoded = _minimal_elf64(architecture)
    assert struct.unpack_from("<H", encoded, 18)[0] == machine
    assert preflight._parse_elf64_interpreter_v1(
        encoded, architecture,
    ) == "/trusted/lib/ld-linux.so"


@pytest.mark.parametrize(
    "kind",
    (
        "magic", "class", "endianness", "machine", "no-interpreter",
        "two-interpreters", "relative-interpreter",
        "unterminated-interpreter", "interpreter-bounds",
    ),
)
def test_minimal_elf64_structural_mutants_are_rejected(kind: str) -> None:
    _assert_invalid(
        preflight._parse_elf64_interpreter_v1,
        _elf_mutant(kind), "x86_64",
    )


def test_loader_and_module_directory_outputs_have_one_closed_grammar() -> None:
    output = (
        b"\tlinux-vdso.so.1 (0x00007fff)\n"
        b"libssl.so.3 => /trusted/lib/libssl.so.3 (0x1a)\n"
        b"/trusted/lib/ld-linux.so (0x2B)\n"
    )
    dependencies = preflight._parse_loader_list_v1(output, "x86_64")
    assert tuple((item.name, item.path) for item in dependencies) == (
        ("linux-vdso.so.1", None),
        ("libssl.so.3", "/trusted/lib/libssl.so.3"),
        (None, "/trusted/lib/ld-linux.so"),
    )
    assert preflight._parse_openssl_module_directory_v1(
        b'MODULESDIR: "/trusted/lib/ossl-modules"\n'
    ) == "/trusted/lib/ossl-modules"


@pytest.mark.parametrize(
    "output",
    (
        b"libssl.so.3 => not found\n",
        b"libssl.so.3 => relative/libssl.so.3 (0x1)\n",
        b"linux-vdso.so.1 (0x1) trailing\n",
        b"linux-vdso.so.1 (0x1)\r\n",
        b"",
    ),
)
def test_loader_output_mutants_are_rejected(output: bytes) -> None:
    _assert_invalid(preflight._parse_loader_list_v1, output, "x86_64")


@pytest.mark.parametrize(
    "output",
    (
        b'MODULESDIR: "relative/modules"\n',
        b'MODULESDIR: "/trusted/modules"\nextra\n',
        b'MODULESDIR: "/trusted/modules"',
        b'MODULESDIR: "/"\n',
    ),
)
def test_module_directory_output_mutants_are_rejected(output: bytes) -> None:
    _assert_invalid(preflight._parse_openssl_module_directory_v1, output)


@pytest.mark.parametrize(
    "result",
    ((1, b"", b""), (0, b"valid stdout\n", b"unexpected stderr\n")),
)
def test_checked_tcb_command_requires_zero_exit_and_empty_stderr(
    monkeypatch: pytest.MonkeyPatch,
    result: tuple[int, bytes, bytes],
) -> None:
    observed = []

    def run(argv, *, maximum):
        observed.append((argv, maximum))
        return result

    monkeypatch.setattr(preflight, "_run_openssl_bounded_v1", run)
    argv = ("/trusted/bin/openssl", "version", "-m")
    failure = _assert_invalid(preflight._run_checked_tcb_command_v1, argv)
    assert failure.detail == "OpenSSL TCB command"
    assert observed == [(argv, preflight.MAX_TCB_SUBPROCESS_STREAM_BYTES_V1)]


class _SyntheticTcbTree(NamedTuple):
    root: Path
    links: tuple[Path, Path, Path, Path]
    python: Path
    openssl: Path
    systemctl: Path
    systemd_analyze: Path
    loader: Path
    library: Path
    library_alias: Path
    second_library: Path
    modules: Path
    module_files: tuple[Path, ...]


def _write_fixture_file(path: Path, content: bytes, mode: int) -> None:
    path.write_bytes(content)
    path.chmod(mode)


def _synthetic_tcb_tree(tmp_path: Path) -> _SyntheticTcbTree:
    root = tmp_path / "trusted"
    binary = root / "bin"
    library_root = root / "lib"
    modules = library_root / "ossl-modules"
    for directory in (root, binary, library_root, modules):
        directory.mkdir(mode=0o700 if directory == root else 0o755)

    python = binary / "python3"
    openssl = binary / "openssl"
    systemctl = binary / "systemctl"
    systemd_analyze = binary / "systemd-analyze"
    loader = library_root / "ld-linux.so"
    library = library_root / "libssl.so.3"
    library_alias = library_root / "libssl-alias.so.3"
    second_library = library_root / "libcrypto.so.3"
    module_files = (modules / "default.so", modules / "legacy.so")

    _write_fixture_file(python, b"python-v1", 0o755)
    _write_fixture_file(
        openssl,
        _minimal_elf64("x86_64", loader.as_posix()),
        0o755,
    )
    _write_fixture_file(systemctl, b"systemctl-v1", 0o755)
    _write_fixture_file(systemd_analyze, b"analyze-v1", 0o755)
    _write_fixture_file(loader, b"loader-v1", 0o755)
    _write_fixture_file(library, b"libssl-v1", 0o644)
    library_alias.symlink_to(library.name)
    _write_fixture_file(second_library, b"libcrypto-v1", 0o644)
    _write_fixture_file(module_files[1], b"legacy-provider", 0o644)
    _write_fixture_file(module_files[0], b"default-provider", 0o644)
    return _SyntheticTcbTree(
        root, (python, openssl, systemctl, systemd_analyze),
        python, openssl, systemctl, systemd_analyze, loader, library,
        library_alias, second_library, modules, module_files,
    )


def _loader_output(tree: _SyntheticTcbTree, *, collision: bool = False) -> bytes:
    second_path = tree.second_library if collision else tree.library_alias
    second_name = "libssl.so.3" if collision else "libssl.so.3"
    return (
        b"linux-vdso.so.1 (0x1)\n"
        + f"libssl.so.3 => {tree.library.as_posix()} (0x2)\n".encode()
        + f"{second_name} => {second_path.as_posix()} (0x3)\n".encode()
        + f"libcrypto.so.3 => {tree.second_library.as_posix()} (0x4)\n".encode()
        + f"{tree.loader.as_posix()} (0x5)\n".encode()
    )


def _install_fake_tcb_runner(
    monkeypatch: pytest.MonkeyPatch,
    tree: _SyntheticTcbTree,
    *,
    collision: bool = False,
    mutate: str | None = None,
) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []

    def run(argv: tuple[str, ...]) -> bytes:
        calls.append(argv)
        if argv == (
            tree.loader.as_posix(), "--list", tree.openssl.as_posix(),
        ):
            if len([item for item in calls if item[1] == "--list"]) == 1:
                if mutate == "openssl":
                    tree.openssl.write_bytes(b"changed-openssl")
                elif mutate == "loader":
                    tree.loader.write_bytes(b"changed-loader")
            return _loader_output(tree, collision=collision)
        if argv == (tree.openssl.as_posix(), "version", "-m"):
            if mutate == "library":
                tree.library.write_bytes(b"changed-library")
            return (
                f'MODULESDIR: "{tree.modules.as_posix()}"\n'.encode("ascii")
            )
        raise AssertionError(f"unexpected TCB command: {argv!r}")

    monkeypatch.setattr(preflight, "_run_checked_tcb_command_v1", run)
    return calls


@LINUX_ONLY
def test_synthetic_collector_closes_commands_aliases_inventory_and_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = _synthetic_tcb_tree(tmp_path)
    calls = _install_fake_tcb_runner(monkeypatch, tree)

    captured = preflight._capture_administrative_tcb_for_test_v1(
        tree.links, architecture="x86_64", trusted_root=tree.root,
    )
    capture = captured.capture
    snapshot = capture.openssl_tcb
    assert calls == [
        (tree.loader.as_posix(), "--list", tree.openssl.as_posix()),
        (tree.loader.as_posix(), "--list", tree.openssl.as_posix()),
        (tree.openssl.as_posix(), "version", "-m"),
    ]

    expected_paths = tuple(sorted(
        {
            tree.openssl.as_posix(), tree.loader.as_posix(),
            tree.library.as_posix(), tree.second_library.as_posix(),
            *(item.as_posix() for item in tree.module_files),
        },
        key=lambda item: item.encode("utf-8"),
    ))
    assert tuple(item.path for item in snapshot.files) == expected_paths
    assert snapshot.elf_loader == tree.loader.as_posix()
    assert snapshot.module_directory == tree.modules.as_posix()
    for item in snapshot.files:
        content = Path(item.path).read_bytes()
        assert item.size == len(content)
        assert item.content_hash == _independent_framed_hash(
            b"metnos.executor-birth.openssl-tcb-file/v1\0",
            item.path, content,
        )
    assert snapshot.openssl_tcb_hash == "sha256:" + hashlib.sha256(
        b"metnos.executor-birth.openssl-tcb/v1\0" + snapshot.encoded
    ).hexdigest()

    executables = capture.executables
    for captured_file, observed_hash in (
        (executables.python, executables.python_binary_hash),
        (executables.openssl, executables.openssl_binary_hash),
        (executables.systemctl, executables.systemctl_binary_hash),
        (executables.systemd_analyze, executables.systemd_analyze_binary_hash),
    ):
        assert observed_hash == _independent_framed_hash(
            b"metnos.executor-birth.administrative-executable/v1\0",
            captured_file.resolved.canonical_path, captured_file.content,
        )


@LINUX_ONLY
def test_loader_same_name_cannot_resolve_to_two_canonical_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = _synthetic_tcb_tree(tmp_path)
    _install_fake_tcb_runner(monkeypatch, tree, collision=True)
    with pytest.raises(preflight.PreflightError) as collision_failure:
        preflight._capture_administrative_tcb_for_test_v1(
            tree.links, architecture="x86_64", trusted_root=tree.root,
        )
    assert collision_failure.value.code == preflight.CODE_INVALID
    assert collision_failure.value.detail == "OpenSSL loader name collision"


@LINUX_ONLY
def test_loader_closure_cannot_change_between_valid_observations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = _synthetic_tcb_tree(tmp_path)
    calls = 0

    def run(argv: tuple[str, ...]) -> bytes:
        nonlocal calls
        if argv != (
            tree.loader.as_posix(), "--list", tree.openssl.as_posix(),
        ):
            raise AssertionError(argv)
        calls += 1
        library = tree.library if calls == 1 else tree.second_library
        return (
            b"linux-vdso.so.1 (0x1)\n"
            + f"libssl.so.3 => {library.as_posix()} (0x2)\n".encode()
            + f"{tree.loader.as_posix()} (0x3)\n".encode()
        )

    monkeypatch.setattr(preflight, "_run_checked_tcb_command_v1", run)
    with pytest.raises(preflight.PreflightError) as failure:
        preflight._capture_administrative_tcb_for_test_v1(
            tree.links, architecture="x86_64", trusted_root=tree.root,
        )
    assert calls == 2
    assert failure.value.detail == "OpenSSL loader closure changed"


def _module_directory_resolution(
    root: Path, modules: Path,
) -> preflight._TrustedResolvedPathV1:
    return preflight._resolve_trusted_path_core_v1(
        modules, kind="directory", executable=False,
        uid=os.getuid(), gid=os.getgid(), chain_stop=root,
        require_single_link=False,
    )


@LINUX_ONLY
@pytest.mark.parametrize(
    ("mutation", "detail"),
    (
        ("symlink", "OpenSSL module entry"),
        ("subdirectory", "OpenSSL module entry"),
        ("special", "OpenSSL module entry"),
        ("count", "OpenSSL module inventory bound"),
        ("bytes", "OpenSSL module inventory bound"),
    ),
)
def test_module_inventory_rejects_type_and_first_over_limit_mutants(
    tmp_path: Path, mutation: str, detail: str,
) -> None:
    root = tmp_path / "trusted"
    modules = root / "modules"
    root.mkdir(mode=0o700)
    modules.mkdir(mode=0o755)
    if mutation == "symlink":
        target = modules / "target.so"
        _write_fixture_file(target, b"provider", 0o644)
        (modules / "alias.so").symlink_to(target.name)
    elif mutation == "subdirectory":
        (modules / "nested").mkdir(mode=0o755)
    elif mutation == "special":
        os.mkfifo(modules / "provider.fifo", 0o600)
    elif mutation == "count":
        for index in range(preflight.MAX_OPENSSL_MODULE_FILES_V1 + 1):
            _write_fixture_file(modules / f"p{index:03d}.so", b"", 0o644)
    else:
        oversized = modules / "oversized.so"
        _write_fixture_file(oversized, b"", 0o644)
        os.truncate(oversized, preflight.MAX_OPENSSL_MODULE_BYTES_V1 + 1)

    resolution = _module_directory_resolution(root, modules)
    with pytest.raises(preflight.PreflightError) as failure:
        preflight._capture_module_inventory_v1(
            resolution, uid=os.getuid(), gid=os.getgid(),
            chain_stop=root,
        )
    assert failure.value.code == preflight.CODE_INVALID
    assert failure.value.detail == detail


@LINUX_ONLY
@pytest.mark.parametrize(
    ("mutation", "detail"),
    (
        ("openssl", "administrative executable changed"),
        ("loader", "trusted file revalidation"),
        ("library", "trusted file revalidation"),
        ("module", "OpenSSL module directory rebound"),
    ),
)
def test_tcb_capture_denies_mutation_at_each_live_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    mutation: str, detail: str,
) -> None:
    tree = _synthetic_tcb_tree(tmp_path)
    runner_mutation = None if mutation == "module" else mutation
    _install_fake_tcb_runner(monkeypatch, tree, mutate=runner_mutation)

    if mutation == "module":
        original = preflight._capture_module_inventory_v1
        calls = 0

        def capture_modules(*args, **kwargs):
            nonlocal calls
            observed = original(*args, **kwargs)
            calls += 1
            if calls == 1:
                _write_fixture_file(
                    tree.modules / "added.so", b"added-provider", 0o644,
                )
            return observed

        monkeypatch.setattr(
            preflight, "_capture_module_inventory_v1", capture_modules,
        )

    with pytest.raises(preflight.PreflightError) as failure:
        preflight._capture_administrative_tcb_for_test_v1(
            tree.links, architecture="x86_64", trusted_root=tree.root,
        )
    assert failure.value.code == preflight.CODE_INVALID
    assert failure.value.detail == detail


def _minimal_bound_materials(
    capture: preflight._CapturedAdministrativeTcbV1,
    *, mutate_hash: str | None = None,
    external_target: tuple[str, bytes] | None = None,
) -> preflight._BoundPreflightMaterialsForTestV1:
    executables = capture.executables
    hashes = {
        "python_binary_hash": executables.python_binary_hash,
        "openssl_binary_hash": executables.openssl_binary_hash,
        "openssl_tcb_hash": capture.openssl_tcb.openssl_tcb_hash,
        "systemctl_binary_hash": executables.systemctl_binary_hash,
        "systemd_analyze_binary_hash": executables.systemd_analyze_binary_hash,
    }
    if mutate_hash is not None:
        hashes[mutate_hash] = "sha256:" + "f" * 64
    descriptor = SimpleNamespace(
        python_executable=executables.python.resolved.canonical_path,
        openssl_executable=executables.openssl.resolved.canonical_path,
        systemctl_executable=executables.systemctl.resolved.canonical_path,
        systemd_analyze_executable=(
            executables.systemd_analyze.resolved.canonical_path
        ),
        installation_root="/signed-release",
    )
    entries = ()
    if external_target is not None:
        declared_path, content = external_target
        entries = (SimpleNamespace(
            target_executable=declared_path,
            target_executable_hash=preflight._target_executable_hash_v1(
                declared_path, content,
            ),
        ),)
    materials = preflight._BoundPreflightMaterialsV1(
        SimpleNamespace(facts=SimpleNamespace(architecture="x86_64")),
        None,
        SimpleNamespace(entries=entries),
        descriptor,
        SimpleNamespace(**hashes),
        None,
        (),
        "sha256:" + "a" * 64,
        "sha256:" + "b" * 64,
    )
    return preflight._BoundPreflightMaterialsForTestV1(materials)


@LINUX_ONLY
def test_binding_uses_every_signed_administrative_tcb_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = _synthetic_tcb_tree(tmp_path)
    _install_fake_tcb_runner(monkeypatch, tree)
    captured = preflight._capture_administrative_tcb_for_test_v1(
        tree.links, architecture="x86_64", trusted_root=tree.root,
    )
    observed = preflight._bind_administrative_tcb_for_test_v1(
        _minimal_bound_materials(captured.capture), captured, tree.links,
        trusted_root=tree.root,
    )
    assert type(observed) is preflight._ObservedAdministrativeTcbForTestV1

    for field in (
        "python_binary_hash", "openssl_binary_hash", "openssl_tcb_hash",
        "systemctl_binary_hash", "systemd_analyze_binary_hash",
    ):
        with pytest.raises(preflight.PreflightError) as failure:
            preflight._bind_administrative_tcb_for_test_v1(
                _minimal_bound_materials(
                    captured.capture, mutate_hash=field,
                ),
                captured,
                tree.links,
                trusted_root=tree.root,
            )
        assert failure.value.code == preflight.CODE_INVALID, field
        assert failure.value.detail == "administrative TCB signed binding", field


@LINUX_ONLY
def test_binding_measures_declared_external_target_and_revalidates_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = _synthetic_tcb_tree(tmp_path)
    _install_fake_tcb_runner(monkeypatch, tree)
    java = tree.root / "bin" / "java-real"
    java_link = tree.root / "bin" / "java"
    java_content = b"java-v1"
    _write_fixture_file(java, java_content, 0o755)
    java_link.symlink_to(java.name)
    captured = preflight._capture_administrative_tcb_for_test_v1(
        tree.links, architecture="x86_64", trusted_root=tree.root,
    )
    revalidated: list[str] = []
    original = preflight._revalidate_captured_file_v1

    def revalidate(captured_file, **keywords):
        revalidated.append(captured_file.resolved.requested_path)
        return original(captured_file, **keywords)

    monkeypatch.setattr(
        preflight, "_revalidate_captured_file_v1", revalidate,
    )
    observed = preflight._bind_administrative_tcb_for_test_v1(
        _minimal_bound_materials(
            captured.capture,
            external_target=(java_link.as_posix(), java_content),
        ),
        captured, tree.links, trusted_root=tree.root,
    ).observation
    assert tuple(
        (item.declared_path, item.target_hash)
        for item in observed.external_targets
    ) == ((
        java_link.as_posix(),
        preflight._target_executable_hash_v1(
            java_link.as_posix(), java_content,
        ),
    ),)
    assert java_link.as_posix() in revalidated
    assert observed.external_targets[0].captured.resolved.canonical_path == (
        java.as_posix()
    )


@LINUX_ONLY
@pytest.mark.parametrize("selected_sequence", (5, 6))
def test_product_binding_rejects_test_capability_and_an_unselected_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    selected_sequence: int,
) -> None:
    tree = _synthetic_tcb_tree(tmp_path)
    _install_fake_tcb_runner(monkeypatch, tree)
    captured = preflight._capture_administrative_tcb_for_test_v1(
        tree.links, architecture="x86_64", trusted_root=tree.root,
    )
    materials = _minimal_bound_materials(captured.capture).materials
    with pytest.raises(preflight.PreflightError) as wrong_type:
        preflight._bind_administrative_tcb_v1(captured, materials)
    assert wrong_type.value.detail == "product administrative TCB"

    build_id = "sha256:" + "1" * 64
    head_id = "sha256:" + "2" * 64
    predecessor_id = "sha256:" + "3" * 64
    distribution = SimpleNamespace(facts=SimpleNamespace(
        architecture="x86_64", closed_build_id=build_id,
        release_sequence=1,
    ))
    selected_record = SimpleNamespace(
        sequence=selected_sequence, head_id=head_id,
        closed_build_id=build_id,
    )
    selected_transaction = SimpleNamespace(
        claim=SimpleNamespace(release_sequence=1),
        prefix=SimpleNamespace(records=(selected_record,)),
    )
    prerequisite = SimpleNamespace(
        **materials.prerequisite.__dict__, predecessor_id=predecessor_id,
    )
    stale_record = SimpleNamespace(sequence=4, head_id=None)
    stale_materials = materials._replace(
        distribution=distribution, transaction=stale_record,
        prerequisite=prerequisite,
    )
    snapshot = preflight._ReconciledFixedOwnershipSnapshotV1(
        (), None,
        SimpleNamespace(
            closed_build_id=build_id, release_sequence=1, head_id=head_id,
        ),
        (distribution,), (), (), (), (selected_transaction,), (),
        None, None, SimpleNamespace(predecessor_id=predecessor_id),
    )
    authenticated = preflight._AuthenticatedFixedOwnershipSnapshotV1(
        snapshot,
        preflight._CapturedAdministrativeTcbProductV1(captured.capture),
    )
    with pytest.raises(preflight.PreflightError) as unselected:
        preflight._bind_administrative_tcb_v1(
            authenticated, stale_materials,
        )
    assert unselected.value.detail == "administrative TCB ownership selection"

    selected_materials = stale_materials._replace(transaction=selected_record)
    core_observation = SimpleNamespace(bound=True)
    core_calls = []

    def bind_core(materials_arg, capture_arg, links_arg, **keywords):
        core_calls.append((materials_arg, capture_arg, links_arg, keywords))
        return core_observation

    monkeypatch.setattr(
        preflight, "_bind_administrative_tcb_core_v1", bind_core,
    )
    product = preflight._bind_administrative_tcb_v1(
        authenticated, selected_materials,
    )
    assert type(product) is preflight._ObservedAdministrativeTcbProductV1
    assert product.observation is core_observation
    assert core_calls == [(
        selected_materials, captured.capture,
        preflight._administrative_links_v1(),
        {"uid": 0, "gid": 0, "chain_stop": None},
    )]


@pytest.mark.parametrize("mutation", ("build", "required-head", "predecessor"))
def test_product_binding_rejects_ownership_selection_mutants(
    mutation: str,
) -> None:
    build_id = "sha256:" + "4" * 64
    other_build_id = "sha256:" + "5" * 64
    head_id = "sha256:" + "6" * 64
    other_head_id = "sha256:" + "7" * 64
    predecessor_id = "sha256:" + "8" * 64
    other_predecessor_id = "sha256:" + "9" * 64
    distribution = SimpleNamespace(facts=SimpleNamespace(
        closed_build_id=build_id, release_sequence=2,
    ))
    selected_record = SimpleNamespace(
        sequence=6, head_id=head_id, closed_build_id=build_id,
    )
    transaction = SimpleNamespace(
        claim=SimpleNamespace(release_sequence=2),
        prefix=SimpleNamespace(records=(selected_record,)),
    )
    prerequisite = SimpleNamespace(predecessor_id=(
        other_predecessor_id if mutation == "predecessor"
        else predecessor_id
    ))
    materials = preflight._BoundPreflightMaterialsV1(
        distribution, selected_record, None, None, prerequisite,
        None, (), "sha256:" + "a" * 64, "sha256:" + "b" * 64,
    )
    required_head = SimpleNamespace(
        closed_build_id=build_id, release_sequence=2,
        head_id=(other_head_id if mutation == "required-head" else head_id),
    )
    builds = (
        SimpleNamespace(facts=SimpleNamespace(
            closed_build_id=other_build_id, release_sequence=2,
        )),
    ) if mutation == "build" else (distribution,)
    snapshot = preflight._ReconciledFixedOwnershipSnapshotV1(
        (), None, required_head, builds, (), (), (), (transaction,), (),
        None, None, SimpleNamespace(predecessor_id=predecessor_id),
    )
    authenticated = preflight._AuthenticatedFixedOwnershipSnapshotV1(
        snapshot,
        preflight._CapturedAdministrativeTcbProductV1(SimpleNamespace()),
    )
    with pytest.raises(preflight.PreflightError) as failure:
        preflight._bind_administrative_tcb_v1(authenticated, materials)
    assert failure.value.detail == "administrative TCB ownership selection"


@LINUX_ONLY
def test_live_openssl_elf_loader_and_module_outputs_conform() -> None:
    openssl = Path("/usr/bin/openssl")
    if not openssl.is_file():
        pytest.skip("fixed OpenSSL executable is unavailable")
    architecture = preflight._local_g6_architecture_v1()
    openssl_canonical = openssl.resolve(strict=True)
    interpreter = preflight._parse_elf64_interpreter_v1(
        openssl_canonical.read_bytes(), architecture,
    )
    loader = Path(interpreter).resolve(strict=True)
    dependencies = preflight._parse_loader_list_v1(
        preflight._run_checked_tcb_command_v1((
            loader.as_posix(), "--list", openssl_canonical.as_posix(),
        )),
        architecture,
    )
    module_directory = preflight._parse_openssl_module_directory_v1(
        preflight._run_checked_tcb_command_v1((
            openssl_canonical.as_posix(), "version", "-m",
        ))
    )
    assert any(item.path is not None for item in dependencies)
    assert Path(module_directory).is_dir()
