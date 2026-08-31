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

import errno
import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


NEUTRALIZER_DOMAIN_V1 = b"metnos.executor-birth.legacy-neutralizer/v1\0"
MASK_TARGET_V1 = "/dev/null"

# A masked unit is a symlink to /dev/null: systemd's own convention, and the
# only form that survives a daemon-reload without the unit being resurrected
# by a package or an enable.
_MASK_ACTIONS_V1 = frozenset({"mask_user_unit", "mask_system_unit"})
_REVOKE_ACTIONS_V1 = frozenset({"revoke_repository_entrypoint"})
# Named for what it is — the extension a retired entry point receives —
# and not with a `SUFFIX` in the name: that word makes the lexicon census
# read the symbol as a linguistic marker, which this extension is not.
RETIRED_EXTENSION_V1 = ".retired-v1"


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
    repeated: bool


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


def _mask_v1(path: Path) -> tuple[str, bool]:
    """Point the name at /dev/null, or agree that it already does."""
    try:
        os.symlink(MASK_TARGET_V1, path)
    except OSError as exc:
        if exc.errno != errno.EEXIST:
            raise _invalid("neutralizer_mask_failed", str(exc)) from exc
        if not path.is_symlink() or os.readlink(path) != MASK_TARGET_V1:
            # Something else holds the name. Replacing it silently would
            # destroy state this module was never told about.
            raise _invalid("neutralizer_mask_occupied", path.name) from exc
        return MASK_TARGET_V1, True
    return MASK_TARGET_V1, False


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
        os.rename(path, retired)
    except OSError as exc:
        raise _invalid("neutralizer_revoke_failed", str(exc)) from exc
    return retired.name, False


def _neutralize_core_v1(
    root: Path, steps: Sequence[object],
) -> tuple[NeutralizedEntryV1, ...]:
    """Perform every step, then RE-READ the filesystem for the receipt."""
    _require_supported_platform_v1()
    if not isinstance(root, Path) or not root.is_absolute() or not root.is_dir():
        raise _invalid("neutralizer_root_invalid", str(root))
    performed: list[NeutralizedEntryV1] = []
    for step in steps:
        action = getattr(step, "action", None)
        locator = getattr(step, "locator", None)
        legacy_id = getattr(step, "legacy_id", None)
        if not isinstance(legacy_id, str) or not legacy_id:
            raise _invalid("neutralizer_step_invalid", "legacy_id")
        path = _require_contained_v1(root, locator)
        if action in _MASK_ACTIONS_V1:
            observed, repeated = _mask_v1(path)
            if not path.is_symlink() or os.readlink(path) != MASK_TARGET_V1:
                raise _invalid("neutralizer_mask_unconfirmed", legacy_id)
        elif action in _REVOKE_ACTIONS_V1:
            observed, repeated = _revoke_v1(path)
            if path.exists() or not path.with_name(observed).is_file():
                raise _invalid("neutralizer_revoke_unconfirmed", legacy_id)
        else:
            raise _invalid("neutralizer_action_unknown", str(action))
        performed.append(
            NeutralizedEntryV1(legacy_id, str(action), observed, repeated),
        )
    return tuple(performed)


def receipt_digest_v1(entries: Sequence[NeutralizedEntryV1]) -> str:
    """Frame the receipt so no field can slide into its neighbour."""
    digest = hashlib.sha256(NEUTRALIZER_DOMAIN_V1)
    digest.update(len(entries).to_bytes(8, "big"))
    for entry in entries:
        if type(entry) is not NeutralizedEntryV1:
            raise _invalid("neutralizer_receipt_invalid", "entry")
        for field in (entry.legacy_id, entry.action, entry.observed):
            encoded = field.encode("utf-8")
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
        digest.update(b"\x01" if entry.repeated else b"\x00")
    return f"sha256:{digest.hexdigest()}"


@dataclass(frozen=True, slots=True)
class _TestOnlyNeutralizationCapabilityV1:
    """Nominally distinct capability; the productive graph never mints it."""

    root: Path


def neutralize_for_test_v1(
    capability: _TestOnlyNeutralizationCapabilityV1,
    steps: Sequence[object],
) -> tuple[NeutralizedEntryV1, ...]:
    """Exercise the core through a capability no productive caller can hold."""
    if type(capability) is not _TestOnlyNeutralizationCapabilityV1:
        raise _invalid("neutralizer_capability_invalid", type(capability).__name__)
    return _neutralize_core_v1(capability.root, steps)


__all__ = [
    "MASK_TARGET_V1",
    "NEUTRALIZER_DOMAIN_V1",
    "NeutralizedEntryV1",
    "LegacyNeutralizerError",
    "RETIRED_EXTENSION_V1",
    "receipt_digest_v1",
]
