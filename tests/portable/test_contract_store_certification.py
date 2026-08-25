"""Real cross-platform certification for the RM-0007 M2 contract store.

These tests use distinct spawned processes and the local filesystem.  The
crash probes terminate with ``os._exit`` after the real filesystem operation;
they do not emulate a crash with an exception or mock filesystem semantics.
Windows-only tests additionally exercise NTFS sharing and reparse points.
"""
from __future__ import annotations

import ctypes
import hashlib
import multiprocessing
import os
import subprocess
import threading
import time
import tomllib
from pathlib import Path
from queue import Empty
from typing import Any, Mapping

from ctypes import wintypes

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

import contract_store as store_module
from contract_store import (
    BINDING_FILE,
    GENERATION_FILES,
    ContractStoreError,
    contract_storage_key,
    current_manifest,
    diagnose_store,
    generation_directory_name,
    generation_id,
    publish_signed_source,
    read_binding,
)
from i18n_materializer import encode_language_state
from manifest_inventory import (
    ManifestOrigin,
    ManifestRef,
    ManifestSource,
    inventory_manifests,
)
from sign import sign_manifest_bytes


_CRASH_EXIT = 73
_NO_CRASH_EXIT = 74
_WORKER_ERROR_EXIT = 75


def _text_hash(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _manifest_text(
    name: str,
    code_file: str,
    code_digest: str,
    *,
    variant: str = "base",
) -> str:
    return f'''manifest_format = "1.0"
executor_standard = "metnos.executor/1.0"
name = "{name}"
version = "1.0.0"
technical_variant = "{variant}"

[description]
en = "SCOPO: certify publication. PATTERN: {name}(). NON: mutate. OUT: ok."
it = "SCOPO: certificare la pubblicazione. PATTERN: {name}(). NON: modificare. OUT: ok."

[code]
files = ["{code_file}"]
digest = "{code_digest}"

[output]
schema_inline = "{{ ok: bool, results: list }}"

[[capabilities]]
name = "compute:pure"
hint = []

[[tests]]
name = "portable"
input = {{}}
expect = {{ ok = true }}

[args]
type = "object"
required = []

[args.properties.query]
type = "string"

[args.properties.query.description]
en = "Text to inspect."
it = "Testo da esaminare."
'''


def _language_state(manifest: Mapping[str, Any]) -> dict[str, Any]:
    tables = {
        "description": manifest["description"],
        "args.properties.query.description": (
            manifest["args"]["properties"]["query"]["description"]
        ),
    }
    return {
        "schema_version": 1,
        "selectors": {
            selector: {
                language: {
                    "version_hash": _text_hash(text),
                    "source_lang": None,
                    "source_hash": None,
                }
                for language, text in table.items()
            }
            for selector, table in tables.items()
        },
    }


def _make_source(
    base: Path,
    *,
    name: str = "portable_sample",
    variant: str = "base",
) -> tuple[ManifestRef, Ed25519PrivateKey]:
    root = base / "sources"
    manifest_dir = root / name
    manifest_dir.mkdir(parents=True)
    code = manifest_dir / f"{name}.py"
    code.write_text("def invoke(args):\n    return {'ok': True}\n", encoding="utf-8")
    code_digest = "sha256:" + hashlib.sha256(code.read_bytes()).hexdigest()
    manifest = manifest_dir / "manifest.toml"
    manifest.write_text(
        _manifest_text("read_files", code.name, code_digest, variant=variant),
        encoding="utf-8",
    )
    parsed = tomllib.loads(manifest.read_text(encoding="utf-8"))
    (manifest_dir / "manifest.lang_state.json").write_bytes(
        encode_language_state(_language_state(parsed), manifest=parsed)
    )
    private = Ed25519PrivateKey.generate()
    (manifest_dir / "manifest.toml.sig").write_bytes(
        sign_manifest_bytes(manifest.read_bytes(), private_key=private)
    )
    inventory = inventory_manifests((ManifestSource(
        ManifestOrigin.EXPLICIT,
        root,
        min_depth=1,
        max_depth=1,
        allowed_code_roots=(root,),
    ),))
    assert not inventory.problems
    ref = next(item for item in inventory.manifests if item.name == "read_files")
    return ref, private


def _raw_public(private: Ed25519PrivateKey) -> bytes:
    return private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def _trusted_from_raw(
    entries: tuple[tuple[str, bytes], ...],
) -> tuple[tuple[str, Ed25519PublicKey], ...]:
    return tuple(
        (name, Ed25519PublicKey.from_public_bytes(raw))
        for name, raw in entries
    )


def _payloads(ref: ManifestRef) -> dict[str, bytes]:
    return {
        name: (ref.manifest_dir / name).read_bytes()
        for name in GENERATION_FILES
    }


def _generation_path(store: Path, ref: ManifestRef, identifier: str) -> Path:
    return (
        store
        / contract_storage_key(ref.contract_id)
        / "generations"
        / generation_directory_name(identifier)
    )


def _assert_complete_current(
    ref: ManifestRef,
    *,
    trusted: tuple[tuple[str, Ed25519PublicKey], ...],
    store: Path,
    allowed: set[str],
) -> str:
    snapshot = current_manifest(
        ref,
        trusted_publics=trusted,
        store_root=store,
    )
    assert snapshot.generation_id in allowed
    generation = _generation_path(store, ref, str(snapshot.generation_id))
    assert {path.name for path in generation.iterdir()} == set(GENERATION_FILES)
    assert all(path.is_file() and not path.is_symlink() for path in generation.iterdir())
    assert generation_id({
        name: (generation / name).read_bytes() for name in GENERATION_FILES
    }) == snapshot.generation_id
    return str(snapshot.generation_id)


def _hold_lock_worker(contract_id, store: str, ready, release) -> None:
    with store_module._writer_lock(
        contract_id, store_root=Path(store), timeout=5.0,
    ):
        ready.set()
        release.wait(10.0)


def _publish_worker(
    ref: ManifestRef,
    trusted_raw: tuple[tuple[str, bytes], ...],
    store: str,
    expected_generation_id: str | None,
    ready,
    start,
    results,
) -> None:
    ready.set()
    start.wait(10.0)
    try:
        result = publish_signed_source(
            ref,
            expected_generation_id=expected_generation_id,
            trusted_publics=_trusted_from_raw(trusted_raw),
            store_root=Path(store),
            lock_timeout=5.0,
        )
    except BaseException as exc:  # returned to the parent as certification evidence
        results.put(("error", type(exc).__name__, str(exc)))
    else:
        results.put(("ok", result.repeated, result.current_generation_id))


def _crash_publish_worker(
    ref: ManifestRef,
    trusted_raw: tuple[tuple[str, bytes], ...],
    store: str,
    expected_generation_id: str | None,
    boundary: str,
) -> None:
    """Instrument a real operation and terminate immediately after it."""
    if boundary == "post_binding_pre_generation":
        real_ensure_binding = store_module._ensure_binding_locked

        def ensure_binding_then_crash(*args, **kwargs):
            real_ensure_binding(*args, **kwargs)
            os._exit(_CRASH_EXIT)

        store_module._ensure_binding_locked = ensure_binding_then_crash
    elif boundary in {"temp_binding", "temp_generation", "temp_current"}:
        real_write_new = store_module._write_new_file

        def write_then_crash(path, payload, *, mode=0o600):
            real_write_new(path, payload, mode=mode)
            path = Path(path)
            if boundary == "temp_binding" and (
                path.name.startswith(f".{BINDING_FILE}.")
                and path.name.endswith(".tmp")
            ):
                os._exit(_CRASH_EXIT)
            if (
                boundary == "temp_generation"
                and path.parent.name.startswith(".generation-")
                and path.name == GENERATION_FILES[0]
            ):
                os._exit(_CRASH_EXIT)
            if boundary == "temp_current" and (
                path.name.startswith(".current.") and path.name.endswith(".tmp")
            ):
                os._exit(_CRASH_EXIT)

        store_module._write_new_file = write_then_crash
    elif boundary == "post_rename_pre_current":
        real_install = store_module._install_generation

        def install_then_crash(*args, **kwargs):
            real_install(*args, **kwargs)
            os._exit(_CRASH_EXIT)

        store_module._install_generation = install_then_crash
    elif boundary == "post_replace":
        real_replace = store_module._replace_retry

        def replace_then_crash(source, destination, *, timeout):
            real_replace(source, destination, timeout=timeout)
            if Path(destination).name == "current":
                os._exit(_CRASH_EXIT)

        store_module._replace_retry = replace_then_crash
    else:
        os._exit(_WORKER_ERROR_EXIT)

    try:
        publish_signed_source(
            ref,
            expected_generation_id=expected_generation_id,
            trusted_publics=_trusted_from_raw(trusted_raw),
            store_root=Path(store),
            lock_timeout=5.0,
        )
    except BaseException:
        os._exit(_WORKER_ERROR_EXIT)
    os._exit(_NO_CRASH_EXIT)


def _join_process(process, *, timeout: float = 12.0) -> None:
    process.join(timeout)
    if process.is_alive():
        process.terminate()
        process.join(3.0)
        pytest.fail("portable subprocess did not terminate within its deadline")


def _new_context():
    return multiprocessing.get_context("spawn")


def _continuous_reader_worker(
    ref: ManifestRef,
    trusted_raw: tuple[tuple[str, bytes], ...],
    store: str,
    allowed_generation_ids: frozenset[str],
    ready,
    stop,
    events,
) -> None:
    """Continuously verify the selected immutable generation in another process."""
    trusted = _trusted_from_raw(trusted_raw)
    store_path = Path(store)
    last_identifier: str | None = None
    samples = 0
    transitions = 0
    try:
        while not stop.is_set():
            snapshot = current_manifest(
                ref,
                trusted_publics=trusted,
                store_root=store_path,
            )
            identifier = str(snapshot.generation_id)
            if identifier not in allowed_generation_ids:
                raise AssertionError(f"unexpected generation: {identifier}")
            generation = _generation_path(store_path, ref, identifier)
            entries = tuple(generation.iterdir())
            if {entry.name for entry in entries} != set(GENERATION_FILES):
                raise AssertionError(f"partial generation: {identifier}")
            if any(not entry.is_file() or entry.is_symlink() for entry in entries):
                raise AssertionError(f"non-regular generation entry: {identifier}")
            payloads = {
                name: (generation / name).read_bytes()
                for name in GENERATION_FILES
            }
            if generation_id(payloads) != identifier:
                raise AssertionError(f"generation digest mismatch: {identifier}")
            samples += 1
            if identifier != last_identifier:
                transitions += 1
                events.put(("seen", identifier))
                last_identifier = identifier
            ready.set()
            time.sleep(0.001)
    except BaseException as exc:
        events.put(("error", type(exc).__name__, str(exc)))
        ready.set()
        return
    events.put(("done", samples, transitions))


def _wait_for_reader_generation(events, identifier: str) -> None:
    deadline = time.monotonic() + 10.0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            pytest.fail(f"reader did not observe generation {identifier}")
        try:
            event = events.get(timeout=remaining)
        except Empty:
            pytest.fail(f"reader did not observe generation {identifier}")
        if event[0] == "error":
            pytest.fail(f"reader failed: {event[1]}: {event[2]}")
        if event[0] == "seen" and event[1] == identifier:
            return


def test_real_multiprocess_lock_has_a_finite_timeout(tmp_path: Path) -> None:
    ref, _private = _make_source(tmp_path)
    store = tmp_path / "shadow" / "lock" / "v1"
    context = _new_context()
    ready = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_lock_worker,
        args=(ref.contract_id, str(store), ready, release),
    )
    process.start()
    try:
        assert ready.wait(8.0)
        started = time.monotonic()
        with pytest.raises(ContractStoreError, match="lock_timeout"):
            with store_module._writer_lock(
                ref.contract_id, store_root=store, timeout=0.20,
            ):
                pass
        elapsed = time.monotonic() - started
        assert 0.12 <= elapsed < 2.0
    finally:
        release.set()
        _join_process(process)
    assert process.exitcode == 0
    lock_file = store / contract_storage_key(ref.contract_id) / "writer.lock"
    assert lock_file.read_bytes() == b"\0"


def test_two_real_publishers_commit_one_generation(tmp_path: Path) -> None:
    ref, private = _make_source(tmp_path)
    store = tmp_path / "shadow" / "publishers" / "v1"
    trusted_raw = (("author", _raw_public(private)),)
    trusted = _trusted_from_raw(trusted_raw)
    context = _new_context()
    ready = (context.Event(), context.Event())
    start = context.Event()
    results = context.Queue()
    processes = tuple(
        context.Process(
            target=_publish_worker,
            args=(ref, trusted_raw, str(store), None, ready[index], start, results),
        )
        for index in range(2)
    )
    for process in processes:
        process.start()
    try:
        assert all(event.wait(8.0) for event in ready)
        start.set()
        try:
            observed = [results.get(timeout=10.0) for _ in processes]
        except Empty:
            pytest.fail("publisher subprocess did not report its result")
    finally:
        start.set()
        for process in processes:
            _join_process(process)
        results.close()
        results.join_thread()

    assert all(process.exitcode == 0 for process in processes)
    assert sorted(item[:2] for item in observed) == [
        ("ok", False),
        ("ok", True),
    ]
    assert len({item[2] for item in observed}) == 1
    identifier = _assert_complete_current(
        ref,
        trusted=trusted,
        store=store,
        allowed={observed[0][2]},
    )
    contract_dir = store / contract_storage_key(ref.contract_id)
    assert read_binding(contract_dir).contract_id == ref.contract_id
    assert {path.name for path in contract_dir.iterdir()} == {
        BINDING_FILE,
        "current",
        "generations",
        "writer.lock",
    }
    assert identifier == observed[0][2]


def test_two_distinct_candidates_from_one_expected_generation_conflict(
    tmp_path: Path,
) -> None:
    contract_name = "portable_conflict"
    baseline_ref, baseline_private = _make_source(
        tmp_path / "baseline",
        name=contract_name,
        variant="baseline",
    )
    first_ref, first_private = _make_source(
        tmp_path / "candidate-first",
        name=contract_name,
        variant="candidate-first",
    )
    second_ref, second_private = _make_source(
        tmp_path / "candidate-second",
        name=contract_name,
        variant="candidate-second",
    )
    assert baseline_ref.contract_id == first_ref.contract_id == second_ref.contract_id
    trusted_raw = (
        ("baseline", _raw_public(baseline_private)),
        ("first", _raw_public(first_private)),
        ("second", _raw_public(second_private)),
    )
    trusted = _trusted_from_raw(trusted_raw)
    store = tmp_path / "shadow" / "conflict" / "v1"
    baseline = publish_signed_source(
        baseline_ref,
        expected_generation_id=None,
        trusted_publics=trusted,
        store_root=store,
    )
    candidate_by_id = {
        generation_id(_payloads(first_ref)): first_ref,
        generation_id(_payloads(second_ref)): second_ref,
    }
    assert len(candidate_by_id) == 2

    context = _new_context()
    ready = (context.Event(), context.Event())
    start = context.Event()
    results = context.Queue()
    processes = tuple(
        context.Process(
            target=_publish_worker,
            args=(
                ref,
                trusted_raw,
                str(store),
                baseline.current_generation_id,
                ready[index],
                start,
                results,
            ),
        )
        for index, ref in enumerate((first_ref, second_ref))
    )
    for process in processes:
        process.start()
    try:
        assert all(event.wait(8.0) for event in ready)
        start.set()
        try:
            observed = [results.get(timeout=10.0) for _ in processes]
        except Empty:
            pytest.fail("candidate publisher did not report its result")
    finally:
        start.set()
        for process in processes:
            _join_process(process)
        results.close()
        results.join_thread()

    assert all(process.exitcode == 0 for process in processes)
    committed = [item for item in observed if item[0] == "ok"]
    conflicted = [item for item in observed if item[0] == "error"]
    assert len(committed) == 1
    assert committed[0][1] is False
    assert len(conflicted) == 1
    assert conflicted[0][1] == "ContractStoreError"
    assert str(conflicted[0][2]).startswith("commit_conflict:")

    winner = str(committed[0][2])
    assert winner in candidate_by_id
    loser = next(identifier for identifier in candidate_by_id if identifier != winner)
    assert _assert_complete_current(
        candidate_by_id[winner],
        trusted=trusted,
        store=store,
        allowed={winner},
    ) == winner
    assert not _generation_path(store, candidate_by_id[loser], loser).exists()
    generations = (
        store
        / contract_storage_key(baseline_ref.contract_id)
        / "generations"
    )
    assert {path.name for path in generations.iterdir()} == {
        generation_directory_name(baseline.current_generation_id),
        generation_directory_name(winner),
    }


def test_continuous_reader_sees_only_complete_generations_during_publication(
    tmp_path: Path,
) -> None:
    contract_name = "portable_reader"
    old_ref, old_private = _make_source(
        tmp_path / "old-source",
        name=contract_name,
        variant="old",
    )
    new_ref, new_private = _make_source(
        tmp_path / "new-source",
        name=contract_name,
        variant="new",
    )
    assert old_ref.contract_id == new_ref.contract_id
    trusted_raw = (
        ("old", _raw_public(old_private)),
        ("new", _raw_public(new_private)),
    )
    trusted = _trusted_from_raw(trusted_raw)
    old_identifier = generation_id(_payloads(old_ref))
    new_identifier = generation_id(_payloads(new_ref))
    assert old_identifier != new_identifier
    store = tmp_path / "shadow" / "continuous-reader" / "v1"
    initial = publish_signed_source(
        old_ref,
        expected_generation_id=None,
        trusted_publics=trusted,
        store_root=store,
    )
    assert initial.current_generation_id == old_identifier

    context = _new_context()
    ready = context.Event()
    stop = context.Event()
    events = context.Queue()
    reader = context.Process(
        target=_continuous_reader_worker,
        args=(
            old_ref,
            trusted_raw,
            str(store),
            frozenset((old_identifier, new_identifier)),
            ready,
            stop,
            events,
        ),
    )
    reader.start()
    done = None
    try:
        assert ready.wait(8.0)
        _wait_for_reader_generation(events, old_identifier)
        expected = old_identifier
        publications = tuple(
            (new_ref, new_identifier) if index % 2 == 0 else (old_ref, old_identifier)
            for index in range(8)
        )
        for ref, desired in publications:
            result = publish_signed_source(
                ref,
                expected_generation_id=expected,
                trusted_publics=trusted,
                store_root=store,
            )
            assert not result.repeated
            assert result.current_generation_id == desired
            _wait_for_reader_generation(events, desired)
            expected = desired
        stop.set()
        _join_process(reader)
        try:
            done = events.get(timeout=3.0)
        except Empty:
            pytest.fail("continuous reader did not report its final counters")
    finally:
        stop.set()
        if reader.is_alive():
            _join_process(reader)
        events.close()
        events.join_thread()

    assert reader.exitcode == 0
    assert done is not None and done[0] == "done"
    assert done[1] >= len(publications) + 1
    assert done[2] == len(publications) + 1
    assert _assert_complete_current(
        old_ref,
        trusted=trusted,
        store=store,
        allowed={old_identifier},
    ) == old_identifier


@pytest.mark.parametrize(
    ("boundary", "visible_after_crash"),
    (
        ("temp_generation", "old"),
        ("post_rename_pre_current", "old"),
        ("temp_current", "old"),
        ("post_replace", "new"),
    ),
)
def test_process_crash_exposes_only_a_complete_old_or_new_generation(
    tmp_path: Path,
    boundary: str,
    visible_after_crash: str,
) -> None:
    ref, first_private = _make_source(tmp_path)
    second_private = Ed25519PrivateKey.generate()
    trusted_raw = (
        ("first", _raw_public(first_private)),
        ("second", _raw_public(second_private)),
    )
    trusted = _trusted_from_raw(trusted_raw)
    store = tmp_path / "shadow" / boundary / "v1"
    initial = publish_signed_source(
        ref,
        expected_generation_id=None,
        trusted_publics=trusted,
        store_root=store,
    )
    (ref.manifest_dir / "manifest.toml.sig").write_bytes(sign_manifest_bytes(
        ref.manifest_path.read_bytes(),
        private_key=second_private,
    ))
    desired = generation_id(_payloads(ref))
    assert desired != initial.current_generation_id

    context = _new_context()
    process = context.Process(
        target=_crash_publish_worker,
        args=(
            ref,
            trusted_raw,
            str(store),
            initial.current_generation_id,
            boundary,
        ),
    )
    process.start()
    _join_process(process)
    assert process.exitcode == _CRASH_EXIT

    expected_visible = (
        initial.current_generation_id
        if visible_after_crash == "old"
        else desired
    )
    visible = _assert_complete_current(
        ref,
        trusted=trusted,
        store=store,
        allowed={initial.current_generation_id, desired},
    )
    assert visible == expected_visible
    if boundary == "temp_current":
        staged_current = tuple(
            (store / contract_storage_key(ref.contract_id)).glob(
                ".current.*.tmp"
            )
        )
        assert len(staged_current) == 1
        assert staged_current[0].read_bytes() == (desired + "\n").encode("ascii")

    desired_path = _generation_path(store, ref, desired)
    before_reuse: dict[str, tuple[int, int, int]] | None = None
    if boundary == "post_rename_pre_current":
        assert desired_path.is_dir()
        before_reuse = {
            name: (
                (desired_path / name).stat().st_ino,
                (desired_path / name).stat().st_mtime_ns,
                (desired_path / name).stat().st_size,
            )
            for name in GENERATION_FILES
        }

    recovered = publish_signed_source(
        ref,
        expected_generation_id=initial.current_generation_id,
        trusted_publics=trusted,
        store_root=store,
    )
    assert recovered.current_generation_id == desired
    assert recovered.repeated is (boundary == "post_replace")
    assert not tuple(
        (store / contract_storage_key(ref.contract_id)).glob(".current.*.tmp")
    )
    assert _assert_complete_current(
        ref,
        trusted=trusted,
        store=store,
        allowed={desired},
    ) == desired

    if before_reuse is not None:
        after_reuse = {
            name: (
                (desired_path / name).stat().st_ino,
                (desired_path / name).stat().st_mtime_ns,
                (desired_path / name).stat().st_size,
            )
            for name in GENERATION_FILES
        }
        assert after_reuse == before_reuse

    # Recovery may retain non-authoritative crash debris, but diagnostics must
    # be read-only and the selected generation must remain fully verifiable.
    before = {
        str(path.relative_to(store)): (
            path.lstat().st_mode,
            path.read_bytes() if path.is_file() else None,
        )
        for path in sorted(store.rglob("*"))
    }
    diagnose_store((ref,), trusted_publics=trusted, store_root=store)
    after = {
        str(path.relative_to(store)): (
            path.lstat().st_mode,
            path.read_bytes() if path.is_file() else None,
        )
        for path in sorted(store.rglob("*"))
    }
    assert after == before


def test_first_publish_crash_in_temp_generation_recovers_staging_on_retry(
    tmp_path: Path,
) -> None:
    ref, private = _make_source(tmp_path)
    trusted_raw = (("author", _raw_public(private)),)
    trusted = _trusted_from_raw(trusted_raw)
    store = tmp_path / "shadow" / "initial-temp-generation" / "v1"
    desired = generation_id(_payloads(ref))
    context = _new_context()
    process = context.Process(
        target=_crash_publish_worker,
        args=(ref, trusted_raw, str(store), None, "temp_generation"),
    )
    process.start()
    _join_process(process)
    assert process.exitcode == _CRASH_EXIT

    contract_dir = store / contract_storage_key(ref.contract_id)
    assert read_binding(contract_dir).contract_id == ref.contract_id
    assert not (contract_dir / "current").exists()
    staging = tuple((contract_dir / "generations").glob(".generation-*"))
    assert len(staging) == 1
    staging_before = {
        str(path.relative_to(contract_dir)): (
            path.lstat().st_mode,
            path.read_bytes() if path.is_file() else None,
        )
        for path in sorted(staging[0].rglob("*"))
    }
    diagnostics = diagnose_store(
        (ref,),
        trusted_publics=trusted,
        store_root=store,
    )
    assert {item.code for item in diagnostics} >= {
        "current_missing",
        "staging_orphan",
    }
    staging_after_diagnostic = {
        str(path.relative_to(contract_dir)): (
            path.lstat().st_mode,
            path.read_bytes() if path.is_file() else None,
        )
        for path in sorted(staging[0].rglob("*"))
    }
    assert staging_after_diagnostic == staging_before

    recovered = publish_signed_source(
        ref,
        expected_generation_id=None,
        trusted_publics=trusted,
        store_root=store,
    )
    assert not recovered.repeated
    assert recovered.current_generation_id == desired
    assert not staging[0].exists()
    assert _assert_complete_current(
        ref,
        trusted=trusted,
        store=store,
        allowed={desired},
    ) == desired


def test_first_publish_crash_in_temp_binding_recovers_staging_on_retry(
    tmp_path: Path,
) -> None:
    ref, private = _make_source(tmp_path)
    trusted_raw = (("author", _raw_public(private)),)
    trusted = _trusted_from_raw(trusted_raw)
    store = tmp_path / "shadow" / "initial-temp-binding" / "v1"
    desired = generation_id(_payloads(ref))
    context = _new_context()
    process = context.Process(
        target=_crash_publish_worker,
        args=(ref, trusted_raw, str(store), None, "temp_binding"),
    )
    process.start()
    _join_process(process)
    assert process.exitcode == _CRASH_EXIT

    contract_dir = store / contract_storage_key(ref.contract_id)
    assert not (contract_dir / BINDING_FILE).exists()
    assert not (contract_dir / "current").exists()
    staging = tuple(contract_dir.glob(f".{BINDING_FILE}.*.tmp"))
    assert len(staging) == 1
    assert staging[0].read_bytes() == store_module.encode_binding(ref.contract_id)

    recovered = publish_signed_source(
        ref,
        expected_generation_id=None,
        trusted_publics=trusted,
        store_root=store,
    )
    assert recovered.previous_generation_id is None
    assert recovered.current_generation_id == desired
    assert not staging[0].exists()
    assert read_binding(contract_dir).contract_id == ref.contract_id
    assert _assert_complete_current(
        ref,
        trusted=trusted,
        store=store,
        allowed={desired},
    ) == desired


def test_first_publish_crash_after_binding_reuses_the_exact_binding(
    tmp_path: Path,
) -> None:
    ref, private = _make_source(tmp_path)
    trusted_raw = (("author", _raw_public(private)),)
    trusted = _trusted_from_raw(trusted_raw)
    store = tmp_path / "shadow" / "initial-binding" / "v1"
    desired = generation_id(_payloads(ref))
    context = _new_context()
    process = context.Process(
        target=_crash_publish_worker,
        args=(ref, trusted_raw, str(store), None, "post_binding_pre_generation"),
    )
    process.start()
    _join_process(process)
    assert process.exitcode == _CRASH_EXIT

    contract_dir = store / contract_storage_key(ref.contract_id)
    binding_path = contract_dir / BINDING_FILE
    binding_before = binding_path.read_bytes()
    binding_stat_before = binding_path.stat()
    assert read_binding(contract_dir).contract_id == ref.contract_id
    assert not (contract_dir / "current").exists()
    assert not tuple((contract_dir / "generations").iterdir())

    recovered = publish_signed_source(
        ref,
        expected_generation_id=None,
        trusted_publics=trusted,
        store_root=store,
    )
    binding_stat_after = binding_path.stat()
    assert binding_path.read_bytes() == binding_before
    assert binding_stat_after.st_mtime_ns == binding_stat_before.st_mtime_ns
    assert recovered.previous_generation_id is None
    assert recovered.current_generation_id == desired
    assert _assert_complete_current(
        ref,
        trusted=trusted,
        store=store,
        allowed={desired},
    ) == desired


def test_first_publish_crash_after_generation_rename_reuses_it_on_retry(
    tmp_path: Path,
) -> None:
    ref, private = _make_source(tmp_path)
    trusted_raw = (("author", _raw_public(private)),)
    trusted = _trusted_from_raw(trusted_raw)
    store = tmp_path / "shadow" / "initial-post-rename" / "v1"
    desired = generation_id(_payloads(ref))
    context = _new_context()
    process = context.Process(
        target=_crash_publish_worker,
        args=(ref, trusted_raw, str(store), None, "post_rename_pre_current"),
    )
    process.start()
    _join_process(process)
    assert process.exitcode == _CRASH_EXIT

    contract_dir = store / contract_storage_key(ref.contract_id)
    assert not (contract_dir / "current").exists()
    generation = _generation_path(store, ref, desired)
    assert generation.is_dir()
    before_reuse = {
        name: (
            (generation / name).stat().st_ino,
            (generation / name).stat().st_mtime_ns,
            (generation / name).stat().st_size,
        )
        for name in GENERATION_FILES
    }

    recovered = publish_signed_source(
        ref,
        expected_generation_id=None,
        trusted_publics=trusted,
        store_root=store,
    )
    after_reuse = {
        name: (
            (generation / name).stat().st_ino,
            (generation / name).stat().st_mtime_ns,
            (generation / name).stat().st_size,
        )
        for name in GENERATION_FILES
    }
    assert not recovered.repeated
    assert recovered.current_generation_id == desired
    assert after_reuse == before_reuse
    assert _assert_complete_current(
        ref,
        trusted=trusted,
        store=store,
        allowed={desired},
    ) == desired


def _windows_filesystem_name(path: Path) -> str:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetVolumePathNameW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
    )
    kernel32.GetVolumePathNameW.restype = wintypes.BOOL
    kernel32.GetVolumeInformationW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR,
        wintypes.DWORD,
    )
    kernel32.GetVolumeInformationW.restype = wintypes.BOOL
    root = ctypes.create_unicode_buffer(260)
    if not kernel32.GetVolumePathNameW(str(path), root, len(root)):
        raise ctypes.WinError(ctypes.get_last_error())
    filesystem = ctypes.create_unicode_buffer(261)
    if not kernel32.GetVolumeInformationW(
        root.value,
        None,
        0,
        None,
        None,
        None,
        filesystem,
        len(filesystem),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return filesystem.value


def _windows_open_without_share_delete(path: Path) -> int:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    kernel32.CreateFileW.restype = wintypes.HANDLE
    handle = kernel32.CreateFileW(
        str(path),
        0x80000000,  # GENERIC_READ
        0x00000001,  # FILE_SHARE_READ; deliberately no FILE_SHARE_DELETE
        None,
        3,  # OPEN_EXISTING
        0x00000080,  # FILE_ATTRIBUTE_NORMAL
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if handle in {None, invalid}:
        raise ctypes.WinError(ctypes.get_last_error())
    return int(handle)


def _windows_close(handle: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    if not kernel32.CloseHandle(ctypes.c_void_p(handle)):
        raise ctypes.WinError(ctypes.get_last_error())


@pytest.mark.skipif(os.name != "nt", reason="requires a real Windows NTFS volume")
def test_windows_workspace_is_really_ntfs(tmp_path: Path) -> None:
    filesystem = _windows_filesystem_name(tmp_path)
    print(f"RM-0007 M2 filesystem certification: {filesystem}")
    assert filesystem.upper() == "NTFS"


@pytest.mark.skipif(os.name != "nt", reason="requires real Windows sharing semantics")
def test_windows_current_replace_retries_a_real_sharing_violation(
    tmp_path: Path,
) -> None:
    assert _windows_filesystem_name(tmp_path).upper() == "NTFS"
    ref, first_private = _make_source(tmp_path)
    second_private = Ed25519PrivateKey.generate()
    trusted = _trusted_from_raw((
        ("first", _raw_public(first_private)),
        ("second", _raw_public(second_private)),
    ))
    store = tmp_path / "shadow" / "sharing" / "v1"
    initial = publish_signed_source(
        ref,
        expected_generation_id=None,
        trusted_publics=trusted,
        store_root=store,
    )
    (ref.manifest_dir / "manifest.toml.sig").write_bytes(sign_manifest_bytes(
        ref.manifest_path.read_bytes(),
        private_key=second_private,
    ))
    desired = generation_id(_payloads(ref))
    current_path = store / contract_storage_key(ref.contract_id) / "current"

    held = _windows_open_without_share_delete(current_path)
    try:
        with pytest.raises(ContractStoreError, match="pointer_replace_timeout"):
            publish_signed_source(
                ref,
                expected_generation_id=initial.current_generation_id,
                trusted_publics=trusted,
                store_root=store,
                replace_timeout=0.10,
            )
    finally:
        _windows_close(held)
    assert _assert_complete_current(
        ref,
        trusted=trusted,
        store=store,
        allowed={initial.current_generation_id},
    ) == initial.current_generation_id

    held = _windows_open_without_share_delete(current_path)
    release_error: list[BaseException] = []

    def release_later() -> None:
        time.sleep(0.25)
        try:
            _windows_close(held)
        except BaseException as exc:  # surfaced in the test thread
            release_error.append(exc)

    releaser = threading.Thread(target=release_later)
    releaser.start()
    started = time.monotonic()
    result = publish_signed_source(
        ref,
        expected_generation_id=initial.current_generation_id,
        trusted_publics=trusted,
        store_root=store,
        replace_timeout=2.0,
    )
    elapsed = time.monotonic() - started
    releaser.join(3.0)
    assert not releaser.is_alive()
    assert not release_error
    assert elapsed >= 0.15
    assert result.current_generation_id == desired
    assert _assert_complete_current(
        ref,
        trusted=trusted,
        store=store,
        allowed={desired},
    ) == desired


@pytest.mark.skipif(os.name != "nt", reason="requires a real Windows junction")
def test_windows_junction_is_rejected_as_a_reparse_point(tmp_path: Path) -> None:
    assert _windows_filesystem_name(tmp_path).upper() == "NTFS"
    ref, private = _make_source(tmp_path)
    target = tmp_path / "junction-target"
    target.mkdir()
    junction = tmp_path / "junction"
    created = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(target)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert created.returncode == 0, created.stderr or created.stdout
    try:
        assert store_module._is_link_like(junction)
        with pytest.raises(ContractStoreError, match="store_root_invalid"):
            publish_signed_source(
                ref,
                expected_generation_id=None,
                trusted_publics=(("author", private.public_key()),),
                store_root=junction / "attempt" / "v1",
            )
        assert not (target / "attempt").exists()
    finally:
        os.rmdir(junction)
