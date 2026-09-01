#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Private core that performs one retirement plan, idempotently and in place.

The plan is decided elsewhere (`executor_birth_legacy_retirement`), and the
separation is deliberate: this module never decides WHAT to retire, only
carries out a plan it is handed and reports what it observed afterwards.

It exposes no productive neutraliser. The authority that will call it belongs
to the group 7 wrapper, which holds the three locks; a caller cannot obtain
neutralisation by holding a path, and the productive graph does not reach here.

Everything happens under a root the caller injects. A module that could write
to `/etc/systemd/system` by default would be one typo away from retiring the
running system while proving something about a fixture.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence


NEUTRALIZER_DOMAIN_V1 = b"metnos.executor-birth.legacy-neutralizer/v1\0"
MASK_TARGET_V1 = "/dev/null"

# A masked unit is a symlink to /dev/null: systemd's own convention, and the
# only form that survives a daemon-reload without the unit being resurrected
# by a package or an enable.
_MASK_ACTIONS_V1 = frozenset({"mask_user_unit", "mask_system_unit"})
_REVOKE_ACTIONS_V1 = frozenset({"revoke_repository_entrypoint"})
_PRESERVE_ACTION_V1 = "preserve_replaced_system_unit"
# Named for what it is — the extension a retired entry point receives —
# and not with a `SUFFIX` in the name: that word makes the lexicon census
# read the symbol as a linguistic marker, which this extension is not.
RETIRED_EXTENSION_V1 = ".retired-v1"
PRESERVED_EXTENSION_V1 = ".pre-rm0008-v1"


class LegacyNeutralizerError(RuntimeError):
    """One stable denial class; detail never reaches an operator stream."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail or code)


def _invalid(code: str, detail: str = "") -> LegacyNeutralizerError:
    return LegacyNeutralizerError(code, detail)


def _require_supported_platform_v1() -> None:
    """Windows denies first, before any path is resolved.

    Masking is a systemd concept: a symlink to `/dev/null` that the manager
    reads as "this unit does not exist". Neither the target nor the manager
    exists on Windows, and emulating the shape would make the denial depend on
    how well the emulation held — which is not a property this boundary can
    prove. The Windows retirement, when it is needed, is a different mechanism
    and deserves its own module rather than a branch inside this one.
    """
    if sys.platform.startswith("win"):
        raise _invalid("neutralizer_unsupported_platform", sys.platform)


@dataclass(frozen=True, slots=True)
class NeutralizedEntryV1:
    """What the filesystem said AFTER the action, not what was intended."""

    legacy_id: str
    action: str
    observed: str
    content_hash: str
    mode: int
    uid: int
    gid: int
    device: int
    inode: int
    size: int
    repeated: bool


@dataclass(frozen=True, slots=True)
class _FileEvidenceV1:
    content_hash: str
    mode: int
    uid: int
    gid: int
    device: int
    inode: int
    size: int


def _require_contained_v1(root: Path, locator: str) -> Path:
    """Resolve a locator strictly inside the injected root."""
    if type(locator) is not str or not locator or locator.startswith("/"):
        raise _invalid("neutralizer_locator_invalid", locator)
    candidate = root / locator
    try:
        resolved_parent = candidate.parent.resolve(strict=True)
    except OSError as exc:
        raise _invalid("neutralizer_locator_invalid", locator) from exc
    if not resolved_parent.is_relative_to(root.resolve(strict=True)):
        raise _invalid("neutralizer_locator_escape", locator)
    return resolved_parent / candidate.name


def _rename_sibling_no_replace_v1(source: Path, destination: Path) -> None:
    """Use the repository's one atomic no-replace primitive."""
    from executor_birth_secure_fs import (
        BirthSecureFSError, _renameat2_no_replace,
    )

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    directory_fd = os.open(source.parent, flags)
    try:
        _renameat2_no_replace(
            directory_fd, source.name, directory_fd, destination.name,
        )
        os.fsync(directory_fd)
    except BirthSecureFSError as exc:
        raise _invalid("neutralizer_atomic_move_failed", source.name) from exc
    finally:
        os.close(directory_fd)


def _revoke_v1(path: Path) -> tuple[str, bool]:
    """Rename the entry point aside, never delete it.

    A deleted entry point cannot be inspected after the fact, and the whole
    retirement is meant to be auditable. The rename is no-replace, so a second
    run recognises its own work instead of clobbering it.
    """
    retired = path.with_name(path.name + RETIRED_EXTENSION_V1)
    if not path.exists() and retired.exists():
        return retired.name, True
    if not path.is_file() or path.is_symlink():
        raise _invalid("neutralizer_entrypoint_invalid", path.name)
    if retired.exists():
        raise _invalid("neutralizer_entrypoint_occupied", retired.name)
    try:
        _rename_sibling_no_replace_v1(path, retired)
    except (LegacyNeutralizerError, OSError) as exc:
        raise _invalid("neutralizer_revoke_failed", str(exc)) from exc
    return retired.name, False


def _regular_file_evidence_v1(path: Path) -> _FileEvidenceV1:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise _invalid("neutralizer_evidence_invalid", path.name) from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise _invalid("neutralizer_evidence_invalid", path.name)
        digest = hashlib.sha256()
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            digest.update(block)
        return _FileEvidenceV1(
            f"sha256:{digest.hexdigest()}", stat.S_IMODE(opened.st_mode),
            opened.st_uid, opened.st_gid, opened.st_dev, opened.st_ino,
            opened.st_size,
        )
    finally:
        os.close(descriptor)


def _entry_evidence_v1(path: Path) -> _FileEvidenceV1:
    opened = path.lstat()
    if stat.S_ISLNK(opened.st_mode):
        target = os.fsencode(os.readlink(path))
        return _FileEvidenceV1(
            f"sha256:{hashlib.sha256(target).hexdigest()}",
            stat.S_IMODE(opened.st_mode), opened.st_uid, opened.st_gid,
            opened.st_dev, opened.st_ino, len(target),
        )
    return _regular_file_evidence_v1(path)


def _preservation_record_v1(
    legacy_id: str, path: Path, preserved: Path, evidence: _FileEvidenceV1,
) -> bytes:
    value = {
        "schema_version": 1,
        "legacy_id": legacy_id,
        "original_name": path.name,
        "preserved_name": preserved.name,
        "content_hash": evidence.content_hash,
        "mode": evidence.mode,
        "uid": evidence.uid,
        "gid": evidence.gid,
        "device": evidence.device,
        "inode": evidence.inode,
        "size": evidence.size,
    }
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def _evidence_from_record_v1(
    encoded: bytes, *, legacy_id: str, path: Path, preserved: Path,
) -> _FileEvidenceV1:
    try:
        value = json.loads(encoded.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _invalid("neutralizer_preservation_record_invalid", path.name) from exc
    keys = {
        "schema_version", "legacy_id", "original_name", "preserved_name",
        "content_hash", "mode", "uid", "gid", "device", "inode", "size",
    }
    if (
        type(value) is not dict or set(value) != keys
        or value.get("schema_version") != 1
        or value.get("legacy_id") != legacy_id
        or value.get("original_name") != path.name
        or value.get("preserved_name") != preserved.name
        or any(
            type(value.get(field)) is not int or value[field] < 0
            for field in ("mode", "uid", "gid", "device", "inode", "size")
        )
        or type(value.get("content_hash")) is not str
        or len(value["content_hash"]) != 71
        or not value["content_hash"].startswith("sha256:")
        or _preservation_record_v1(
            legacy_id, path, preserved, _FileEvidenceV1(
                value["content_hash"], value["mode"], value["uid"],
                value["gid"], value["device"], value["inode"], value["size"],
            ),
        ) != encoded
    ):
        raise _invalid("neutralizer_preservation_record_invalid", path.name)
    return _FileEvidenceV1(
        value["content_hash"], value["mode"], value["uid"], value["gid"],
        value["device"], value["inode"], value["size"],
    )


def _publish_preservation_record_v1(
    path: Path, encoded: bytes,
) -> Path:
    record = path.with_name(path.name + PRESERVED_EXTENSION_V1 + ".receipt.json")
    temporary = record.with_name("." + record.name + ".writing")
    if os.path.lexists(record):
        if record.is_symlink() or record.read_bytes() != encoded:
            raise _invalid("neutralizer_preservation_record_conflict", path.name)
        return record
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(temporary, flags, 0o600)
    except OSError as exc:
        raise _invalid("neutralizer_preservation_record_invalid", path.name) from exc
    try:
        observed = os.read(descriptor, len(encoded) + 1)
        if not encoded.startswith(observed):
            raise _invalid("neutralizer_preservation_record_conflict", path.name)
        position = len(observed)
        while position < len(encoded):
            position += os.write(descriptor, encoded[position:])
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        _rename_sibling_no_replace_v1(temporary, record)
    except (LegacyNeutralizerError, OSError) as exc:
        raise _invalid("neutralizer_preservation_record_conflict", path.name) from exc
    if record.is_symlink() or record.read_bytes() != encoded:
        raise _invalid("neutralizer_preservation_record_invalid", path.name)
    return record


@dataclass(frozen=True, slots=True)
class _PreservedRegularNameV1:
    preserved: Path
    expected: _FileEvidenceV1
    current_exists: bool
    moved_now: bool


def _preserve_regular_name_v1(
    legacy_id: str, path: Path, *, record_stage: str, moved_stage: str,
    _crash_seam: Callable[[str], None] | None = None,
) -> _PreservedRegularNameV1:
    """Preserve one regular name and return only durable observed state."""
    preserved = path.with_name(path.name + PRESERVED_EXTENSION_V1)
    record = path.with_name(path.name + PRESERVED_EXTENSION_V1 + ".receipt.json")
    current_exists = os.path.lexists(path)
    preserved_exists = os.path.lexists(preserved)
    if os.path.lexists(record):
        if record.is_symlink():
            raise _invalid("neutralizer_preservation_record_invalid", path.name)
        expected = _evidence_from_record_v1(
            record.read_bytes(), legacy_id=legacy_id,
            path=path, preserved=preserved,
        )
    else:
        if not current_exists or preserved_exists:
            raise _invalid("neutralizer_preservation_record_missing", path.name)
        expected = _regular_file_evidence_v1(path)
        encoded = _preservation_record_v1(
            legacy_id, path, preserved, expected,
        )
        _publish_preservation_record_v1(path, encoded)
    if _crash_seam is not None:
        _crash_seam(record_stage)
    if current_exists and not preserved_exists:
        if _regular_file_evidence_v1(path) != expected:
            raise _invalid("neutralizer_preservation_conflict", path.name)
        _rename_sibling_no_replace_v1(path, preserved)
        if _regular_file_evidence_v1(preserved) != expected:
            raise _invalid("neutralizer_preservation_unconfirmed", path.name)
        if _crash_seam is not None:
            _crash_seam(moved_stage)
        return _PreservedRegularNameV1(preserved, expected, False, True)
    if not current_exists and preserved_exists:
        if _regular_file_evidence_v1(preserved) != expected:
            raise _invalid("neutralizer_preservation_conflict", path.name)
        return _PreservedRegularNameV1(preserved, expected, False, False)
    if current_exists and preserved_exists:
        if _regular_file_evidence_v1(preserved) != expected:
            raise _invalid("neutralizer_preservation_conflict", path.name)
        return _PreservedRegularNameV1(preserved, expected, True, False)
    raise _invalid("neutralizer_preservation_missing", path.name)


def _preserve_replaced_unit_v1(
    legacy_id: str, path: Path, replacement: bytes, *,
    _crash_seam: Callable[[str], None] | None = None,
) -> tuple[str, bool]:
    """Preserve one occupied unit and recognize only the four named states."""
    if type(replacement) is not bytes or not replacement:
        raise _invalid("neutralizer_replacement_invalid", path.name)
    state = _preserve_regular_name_v1(
        legacy_id, path,
        record_stage="preservation_record_published",
        moved_stage="replaced_system_unit_preserved",
        _crash_seam=_crash_seam,
    )
    if state.current_exists:
        replacement_hash = f"sha256:{hashlib.sha256(replacement).hexdigest()}"
        observed_replacement = _regular_file_evidence_v1(path)
        if (
            observed_replacement.content_hash != replacement_hash
            or observed_replacement.size != len(replacement)
        ):
            raise _invalid("neutralizer_preservation_conflict", path.name)
        return state.preserved.name, True
    return state.preserved.name, not state.moved_now


def _mask_v1(
    legacy_id: str, path: Path, *,
    _crash_seam: Callable[[str], None] | None = None,
) -> tuple[str, bool]:
    """Preserve an occupied regular unit, then bind its name to /dev/null."""
    preserved = path.with_name(path.name + PRESERVED_EXTENSION_V1)
    record = path.with_name(path.name + PRESERVED_EXTENSION_V1 + ".receipt.json")
    has_history = os.path.lexists(preserved) or os.path.lexists(record)
    if path.is_symlink():
        if os.readlink(path) != MASK_TARGET_V1:
            raise _invalid("neutralizer_mask_occupied", path.name)
        if has_history:
            state = _preserve_regular_name_v1(
                legacy_id, path,
                record_stage="legacy_unit_record_published",
                moved_stage="legacy_unit_preserved",
                _crash_seam=_crash_seam,
            )
            if not state.current_exists:
                raise _invalid("neutralizer_mask_occupied", path.name)
        return MASK_TARGET_V1, True
    if os.path.lexists(path) or has_history:
        state = _preserve_regular_name_v1(
            legacy_id, path,
            record_stage="legacy_unit_record_published",
            moved_stage="legacy_unit_preserved",
            _crash_seam=_crash_seam,
        )
        if state.current_exists:
            raise _invalid("neutralizer_mask_occupied", path.name)
    try:
        os.symlink(MASK_TARGET_V1, path)
    except OSError as exc:
        raise _invalid("neutralizer_mask_failed", str(exc)) from exc
    if _crash_seam is not None:
        _crash_seam("legacy_unit_masked")
    return MASK_TARGET_V1, False


def _neutralize_core_v1(
    root: Path, steps: Sequence[object],
    replacement_fragments: Mapping[tuple[str, str], bytes],
    *, _crash_seam: Callable[[str], None] | None = None,
) -> tuple[NeutralizedEntryV1, ...]:
    """Perform every step, then RE-READ the filesystem for the receipt."""
    _require_supported_platform_v1()
    if not isinstance(root, Path) or not root.is_absolute() or not root.is_dir():
        raise _invalid("neutralizer_root_invalid", str(root))
    if (
        not isinstance(replacement_fragments, Mapping)
        or _crash_seam is not None and not callable(_crash_seam)
    ):
        raise _invalid("neutralizer_replacements_invalid", "shape")
    performed: list[NeutralizedEntryV1] = []
    ordered_steps = tuple(
        step for step in steps
        if getattr(step, "action", None) != _PRESERVE_ACTION_V1
    ) + tuple(
        step for step in steps
        if getattr(step, "action", None) == _PRESERVE_ACTION_V1
    )
    for step in ordered_steps:
        action = getattr(step, "action", None)
        locator = getattr(step, "locator", None)
        legacy_id = getattr(step, "legacy_id", None)
        scope = getattr(step, "scope", None)
        if not isinstance(legacy_id, str) or not legacy_id:
            raise _invalid("neutralizer_step_invalid", "legacy_id")
        path = _require_contained_v1(root, locator)
        if action in _MASK_ACTIONS_V1:
            observed, repeated = _mask_v1(
                legacy_id, path, _crash_seam=_crash_seam,
            )
            if not path.is_symlink() or os.readlink(path) != MASK_TARGET_V1:
                raise _invalid("neutralizer_mask_unconfirmed", legacy_id)
        elif action in _REVOKE_ACTIONS_V1:
            observed, repeated = _revoke_v1(path)
            if path.exists() or not path.with_name(observed).is_file():
                raise _invalid("neutralizer_revoke_unconfirmed", legacy_id)
        elif action == _PRESERVE_ACTION_V1:
            observed, repeated = _preserve_replaced_unit_v1(
                legacy_id, path,
                replacement_fragments.get((scope, locator), b""),
                _crash_seam=_crash_seam,
            )
        else:
            raise _invalid("neutralizer_action_unknown", str(action))
        evidence_path = (
            path if action in _MASK_ACTIONS_V1 else path.with_name(observed)
        )
        evidence = _entry_evidence_v1(evidence_path)
        performed.append(
            NeutralizedEntryV1(
                legacy_id, str(action), observed,
                evidence.content_hash, evidence.mode, evidence.uid,
                evidence.gid, evidence.device, evidence.inode,
                evidence.size, repeated,
            ),
        )
    return tuple(performed)


def receipt_digest_v1(entries: Sequence[NeutralizedEntryV1]) -> str:
    """Frame the receipt so no field can slide into its neighbour."""
    digest = hashlib.sha256(NEUTRALIZER_DOMAIN_V1)
    digest.update(len(entries).to_bytes(8, "big"))
    for entry in entries:
        if type(entry) is not NeutralizedEntryV1:
            raise _invalid("neutralizer_receipt_invalid", "entry")
        for field in (
            entry.legacy_id, entry.action, entry.observed, entry.content_hash,
        ):
            encoded = field.encode("utf-8")
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
        digest.update(entry.mode.to_bytes(4, "big"))
        digest.update(entry.uid.to_bytes(8, "big"))
        digest.update(entry.gid.to_bytes(8, "big"))
        digest.update(entry.device.to_bytes(8, "big"))
        digest.update(entry.inode.to_bytes(8, "big"))
        digest.update(entry.size.to_bytes(8, "big"))
        digest.update(b"\x01" if entry.repeated else b"\x00")
    return f"sha256:{digest.hexdigest()}"


@dataclass(frozen=True, slots=True)
class _TestOnlyNeutralizationCapabilityV1:
    """Nominally distinct capability; the productive graph never mints it."""

    root: Path


def neutralize_for_test_v1(
    capability: _TestOnlyNeutralizationCapabilityV1,
    steps: Sequence[object],
    *, replacement_fragments: Mapping[tuple[str, str], bytes],
    _crash_seam: Callable[[str], None] | None = None,
) -> tuple[NeutralizedEntryV1, ...]:
    """Exercise the core through a capability no productive caller can hold."""
    if type(capability) is not _TestOnlyNeutralizationCapabilityV1:
        raise _invalid("neutralizer_capability_invalid", type(capability).__name__)
    return _neutralize_core_v1(
        capability.root, steps, replacement_fragments,
        _crash_seam=_crash_seam,
    )


__all__ = [
    "MASK_TARGET_V1",
    "NEUTRALIZER_DOMAIN_V1",
    "NeutralizedEntryV1",
    "LegacyNeutralizerError",
    "RETIRED_EXTENSION_V1",
    "PRESERVED_EXTENSION_V1",
    "receipt_digest_v1",
]
