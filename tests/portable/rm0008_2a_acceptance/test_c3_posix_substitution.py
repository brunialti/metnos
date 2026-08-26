from __future__ import annotations

import ast
import inspect
import os
from pathlib import Path
import textwrap

import pytest

from ._support import (
    make_root,
    mkdir_private,
    lock_role_binding,
    open_session,
    private_role,
    role_binding,
    secure_fs,
    tree_snapshot,
    write_private,
    write_public,
)


CASES = (
    "swap-after-root",
    "swap-after-middle",
    "swap-final-object",
)


def _fixture_bindings(module):
    values = [lock_role_binding(module)]
    for components in (("first",), ("first", "middle"), ("first", "middle", "last")):
        values.append(
            role_binding(
                module, components, directory=True, role=private_role(module)
            )
        )
    values.append(
        role_binding(
            module,
            ("first", "middle", "last", "payload.bin"),
            directory=False,
            role=private_role(module),
        )
    )
    return tuple(values)


def _read_exact(fd: int, length: int) -> bytes:
    value = bytearray()
    while len(value) < length:
        block = os.read(fd, length - len(value))
        if not block:
            raise AssertionError("barrier pipe closed")
        value.extend(block)
    return bytes(value)


def _walk_function(source: str) -> ast.FunctionDef:
    tree = ast.parse(textwrap.dedent(source))
    functions = [
        item
        for item in ast.walk(tree)
        if isinstance(item, ast.FunctionDef) and item.name == "_directory_chain"
    ]
    assert len(functions) == 1
    return functions[0]


def _assert_uniform_component_walk(source: str) -> None:
    function = _walk_function(source)
    loops = [
        item
        for item in ast.walk(function)
        if isinstance(item, ast.For)
        and any(
            isinstance(candidate, ast.Name) and candidate.id == "components"
            for candidate in ast.walk(item.iter)
        )
    ]
    assert len(loops) == 1, "component traversal must use one common loop"
    loop = loops[0]
    assert isinstance(loop.target, ast.Name), "component traversal cannot expose depth"
    depth_names = {"components", "prefix", "index", "depth", "position", "offset"}
    for branch in ast.walk(loop):
        if isinstance(branch, (ast.Break, ast.Continue)):
            raise AssertionError("component traversal cannot exit at a special depth")
        if isinstance(branch, ast.Subscript) and isinstance(branch.value, ast.Name):
            if branch.value.id == "components":
                raise AssertionError("component traversal cannot index by depth")
        if isinstance(branch, ast.Call) and isinstance(branch.func, ast.Name):
            if branch.func.id in {"enumerate", "len"} and any(
                isinstance(candidate, ast.Name)
                and candidate.id in {"components", "prefix"}
                for candidate in ast.walk(branch)
            ):
                raise AssertionError("component traversal cannot inspect depth")
        if isinstance(branch, (ast.If, ast.IfExp, ast.Match)):
            selector = branch.subject if isinstance(branch, ast.Match) else branch.test
            referenced = {
                candidate.id
                for candidate in ast.walk(selector)
                if isinstance(candidate, ast.Name)
            }
            if referenced & depth_names:
                raise AssertionError("component traversal has a depth-specific branch")
        if any(
            isinstance(candidate, ast.Constant)
            and candidate.value in {"first", "middle", "last", "payload.bin"}
            for candidate in ast.walk(branch)
        ):
            raise AssertionError("component traversal recognizes a sentinel name")


def _assert_depth_guard_rejects_mutants() -> None:
    mutants = (
        """
        def _directory_chain(components):
            for index, component in enumerate(components):
                if index == 1:
                    return
        """,
        """
        def _directory_chain(components):
            for component in components:
                if component == components[-1]:
                    return
        """,
        """
        def _directory_chain(components):
            prefix = ()
            for component in components:
                prefix += (component,)
                if len(prefix) == 2:
                    return
        """,
        """
        def _directory_chain(components):
            for component in components:
                if component == "middle":
                    return
        """,
    )
    for source in mutants:
        with pytest.raises(AssertionError):
            _assert_uniform_component_walk(source)


def _build_complete_substitute(tmp_path: Path, case: str) -> Path:
    container = tmp_path / f"attacker-{case}"
    mkdir_private(container)
    replacement = container / "replacement"
    if case == "swap-after-root":
        make_root(replacement)
        write_public(replacement / "provisioning-v1.lock", b"0")
        mkdir_private(replacement / "first")
        mkdir_private(replacement / "first/middle")
        mkdir_private(replacement / "first/middle/last")
        write_private(
            replacement / "first/middle/last/payload.bin", b"malicious"
        )
    elif case == "swap-after-first":
        mkdir_private(replacement)
        mkdir_private(replacement / "last")
        write_private(replacement / "last/payload.bin", b"malicious")
    elif case == "swap-after-middle":
        mkdir_private(replacement)
        write_private(replacement / "payload.bin", b"malicious")
    else:
        write_private(replacement, b"malicious")
    return replacement


def _object_identities(root: Path) -> frozenset[tuple[int, int]]:
    root_value = root.stat(follow_symlinks=False)
    values = {(root_value.st_dev, root_value.st_ino)}
    if root.is_dir():
        for current, directory_names, file_names in os.walk(root):
            current_path = Path(current)
            for name in directory_names + file_names:
                value = (current_path / name).stat(follow_symlinks=False)
                values.add((value.st_dev, value.st_ino))
    return frozenset(values)


def _worker(
    root: Path,
    case: str,
    ready_fd: int,
    resume_fd: int,
    forbidden_identities: frozenset[tuple[int, int]],
) -> None:
    try:
        module = secure_fs()
        session = open_session(root, role_bindings=_fixture_bindings(module))
        original_open = os.open
        barrier_component = {
            "swap-after-first": "first",
            "swap-after-middle": "middle",
            "swap-after-last": "last",
            "swap-final-object": "payload.bin",
        }.get(case)
        fired = False

        def reject_attacker_fd(fd: int, operation: str) -> None:
            value = os.fstat(fd)
            if (value.st_dev, value.st_ino) in forbidden_identities:
                raise AssertionError(f"product {operation} attacker object")

        def barrier_open(path, flags, mode=0o777, *, dir_fd=None):
            nonlocal fired
            result = original_open(path, flags, mode, dir_fd=dir_fd)
            if path == barrier_component and dir_fd is not None and not fired:
                fired = True
                os.write(ready_fd, b"R")
                _read_exact(resume_fd, 1)
            return result

        original_read = os.read
        original_pread = os.pread
        original_scandir = os.scandir
        original_listdir = os.listdir

        def guarded_read(fd: int, length: int) -> bytes:
            reject_attacker_fd(fd, "read")
            return original_read(fd, length)

        def guarded_pread(fd: int, length: int, offset: int) -> bytes:
            reject_attacker_fd(fd, "read")
            return original_pread(fd, length, offset)

        def reject_inventory_target(path) -> None:
            if isinstance(path, int):
                reject_attacker_fd(path, "inventoried")
                return
            try:
                value = os.stat(path)
            except OSError:
                return
            if (value.st_dev, value.st_ino) in forbidden_identities:
                raise AssertionError("product inventoried attacker object")

        def guarded_scandir(path):
            reject_inventory_target(path)
            return original_scandir(path)

        def guarded_listdir(path):
            reject_inventory_target(path)
            return original_listdir(path)

        os.open = barrier_open
        os.read = guarded_read
        os.pread = guarded_pread
        os.scandir = guarded_scandir
        os.listdir = guarded_listdir
        if case == "swap-after-root":
            os.write(ready_fd, b"R")
            _read_exact(resume_fd, 1)
        try:
            with session.global_lock(exclusive=False, create=False):
                payload = session.read_file(
                    ("first", "middle", "last", "payload.bin"),
                    maximum=64,
                    role=private_role(),
                )
            os.write(ready_fd, b"B" + payload.hex().encode("ascii") + b"\n")
        except module.BirthSecureFSError as exc:
            os.write(ready_fd, b"E" + exc.code.encode("ascii") + b"\n")
        except BaseException as exc:
            os.write(ready_fd, b"X" + type(exc).__name__.encode("ascii") + b"\n")
        finally:
            session.close()
    finally:
        os._exit(0)


@pytest.mark.parametrize("case", CASES, ids=CASES)
def test_posix_component_substitution(tmp_path: Path, case: str) -> None:
    module = secure_fs()
    _assert_uniform_component_walk(
        inspect.getsource(module._SecureRootSession._directory_chain)
    )
    _assert_depth_guard_rejects_mutants()
    root = make_root(tmp_path / "birth")
    first, middle, last = root / "first", root / "first/middle", root / "first/middle/last"
    mkdir_private(first)
    mkdir_private(middle)
    mkdir_private(last)
    write_private(last / "payload.bin", b"trusted")
    replacement = _build_complete_substitute(tmp_path, case)
    forbidden_identities = _object_identities(replacement)
    with open_session(root, role_bindings=_fixture_bindings(module)) as initializer:
        with initializer.global_lock(exclusive=True, create=True):
            pass
    with open_session(
        root, role_bindings=_fixture_bindings(module)
    ) as baseline_reader:
        with baseline_reader.global_lock(exclusive=False, create=False):
            assert baseline_reader.read_file(
                ("first", "middle", "last", "payload.bin"),
                maximum=64,
                role=private_role(),
            ) == b"trusted"

    child_to_parent_r, child_to_parent_w = os.pipe()
    parent_to_child_r, parent_to_child_w = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(child_to_parent_r)
        os.close(parent_to_child_w)
        _worker(
            root,
            case,
            child_to_parent_w,
            parent_to_child_r,
            forbidden_identities,
        )
    os.close(child_to_parent_w)
    os.close(parent_to_child_r)
    assert _read_exact(child_to_parent_r, 1) == b"R"

    next_path = {
        "swap-after-root": root,
        "swap-after-first": middle,
        "swap-after-middle": last,
        "swap-after-last": last / "payload.bin",
        "swap-final-object": last / "payload.bin",
    }[case]
    saved = next_path.with_name(next_path.name + ".original")
    next_path.rename(saved)
    replacement.rename(next_path)
    attacker_before = tree_snapshot(next_path)
    os.write(parent_to_child_w, b"C")
    os.close(parent_to_child_w)
    result = bytearray()
    while not result.endswith(b"\n"):
        block = os.read(child_to_parent_r, 256)
        if not block:
            break
        result.extend(block)
    os.close(child_to_parent_r)
    waited, status = os.waitpid(pid, 0)
    assert waited == pid and os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
    assert result.startswith((b"B", b"E"))
    assert result != b"B" + b"malicious".hex().encode("ascii") + b"\n"
    if result.startswith(b"B"):
        assert bytes.fromhex(result[1:-1].decode("ascii")) == b"trusted"
    assert tree_snapshot(next_path) == attacker_before
