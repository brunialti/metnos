# SPDX-License-Identifier: MIT
"""Provision fresh public Birth registries without exposing private keys.

This is an administrator procedure, separate from the six-phase installer.
Private operator and reviewer keys remain below a root-only host directory;
the Metnos service account receives only the two canonical registries and the
reviewer's public key.  Re-entry verifies and reuses an exact prior result.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import pwd
import stat
import sys
import tempfile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


PRIVATE_BASE = Path("/var/lib/metnos-operator-authority")
PRIVATE_FILES = frozenset({"operator-key.priv", "review-key.priv"})
PUBLIC_FILES = frozenset({
    "approval-authority.json", "semantic-authority.json", "semantic-public",
})


class OperatorAuthorityError(RuntimeError):
    """Stable refusal from the public operator procedure."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def _status(path: Path) -> os.stat_result:
    try:
        result = path.lstat()
    except OSError as exc:
        raise OperatorAuthorityError("operator_authority_io_error", str(path)) from exc
    if stat.S_ISLNK(result.st_mode):
        raise OperatorAuthorityError("operator_authority_unsafe_path", str(path))
    return result


def _require_directory(
    path: Path, *, owner: tuple[int, int], modes: frozenset[int],
) -> None:
    result = _status(path)
    if (
        not stat.S_ISDIR(result.st_mode)
        or (result.st_uid, result.st_gid) != owner
        or stat.S_IMODE(result.st_mode) not in modes
    ):
        raise OperatorAuthorityError("operator_authority_unsafe_path", str(path))


def _ensure_directory(
    path: Path, *, owner: tuple[int, int], mode: int,
    existing_modes: frozenset[int] | None = None,
) -> None:
    try:
        path.mkdir(mode=mode)
        os.chown(path, *owner)
        path.chmod(mode)
    except FileExistsError:
        pass
    except OSError as exc:
        raise OperatorAuthorityError("operator_authority_io_error", str(path)) from exc
    _require_directory(
        path, owner=owner, modes=existing_modes or frozenset({mode}),
    )


def _read_private(path: Path, *, owner: tuple[int, int]) -> Ed25519PrivateKey:
    result = _status(path)
    if (
        not stat.S_ISREG(result.st_mode)
        or (result.st_uid, result.st_gid) != owner
        or stat.S_IMODE(result.st_mode) != 0o600
        or result.st_nlink != 1
        or result.st_size != 32
    ):
        raise OperatorAuthorityError("operator_authority_private_invalid", str(path))
    try:
        return Ed25519PrivateKey.from_private_bytes(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise OperatorAuthorityError("operator_authority_private_invalid", str(path)) from exc


def _load_or_create_private(path: Path, *, owner: tuple[int, int]) -> tuple[Ed25519PrivateKey, bool]:
    if path.exists() or path.is_symlink():
        return _read_private(path, owner=owner), False
    key = Ed25519PrivateKey.generate()
    payload = key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
        try:
            os.write(descriptor, payload)
            os.fsync(descriptor)
            os.fchown(descriptor, *owner)
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise OperatorAuthorityError("operator_authority_io_error", str(path)) from exc
    return _read_private(path, owner=owner), True


def _public_bytes(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def _documents(operator_public: bytes, reviewer_public: bytes) -> dict[str, bytes]:
    runtime_dir = Path(__file__).resolve().parents[1] / "runtime"
    if str(runtime_dir) not in sys.path:
        sys.path.insert(0, str(runtime_dir))
    from executor_birth_semantic_review import IndependentEvidenceKind

    kinds = sorted(item.value for item in IndependentEvidenceKind)
    approval = _canonical({
        "actors": {
            "operator": {"key_ids": ["operator-key"], "scopes": ["birth"]},
        },
        "keys": {"operator-key": base64.b64encode(operator_public).decode("ascii")},
        "revision": 1,
        "schema_version": 1,
    })
    semantic = _canonical({
        "evidence_dir": "evidence",
        "owners": {kind: ["independent-owner"] for kind in kinds},
        "verifiers": {
            "review-key": {"path": "public/review.pub", "status": "active"},
        },
        "versions": {kind: ["v1"] for kind in kinds},
    })
    return {
        "approval-authority.json": approval,
        "semantic-authority.json": semantic,
        "review.pub": reviewer_public,
    }


def _write_public(path: Path, payload: bytes, *, owner: tuple[int, int]) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o644)
        try:
            os.write(descriptor, payload)
            os.fsync(descriptor)
            os.fchown(descriptor, *owner)
            os.fchmod(descriptor, 0o644)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise OperatorAuthorityError("operator_authority_io_error", str(path)) from exc


def _verify_public(directory: Path, expected: dict[str, bytes], *, owner: tuple[int, int]) -> None:
    _require_directory(directory, owner=owner, modes=frozenset({0o755}))
    try:
        if {item.name for item in directory.iterdir()} != PUBLIC_FILES:
            raise OperatorAuthorityError("operator_authority_public_invalid", str(directory))
    except OSError as exc:
        raise OperatorAuthorityError("operator_authority_io_error", str(directory)) from exc
    semantic = directory / "semantic-public"
    _require_directory(semantic, owner=owner, modes=frozenset({0o755}))
    if {item.name for item in semantic.iterdir()} != {"review.pub"}:
        raise OperatorAuthorityError("operator_authority_public_invalid", str(semantic))
    for path, payload in (
        (directory / "approval-authority.json", expected["approval-authority.json"]),
        (directory / "semantic-authority.json", expected["semantic-authority.json"]),
        (semantic / "review.pub", expected["review.pub"]),
    ):
        result = _status(path)
        if (
            not stat.S_ISREG(result.st_mode)
            or (result.st_uid, result.st_gid) != owner
            or stat.S_IMODE(result.st_mode) != 0o644
            or result.st_nlink != 1
            or path.read_bytes() != payload
        ):
            raise OperatorAuthorityError("operator_authority_public_invalid", str(path))


def provision_paths(
    *, target_config: Path, private_base: Path, target_owner: tuple[int, int],
    private_owner: tuple[int, int],
) -> dict[str, object]:
    """Create or verify one fresh, separated operator authority set."""

    if not target_config.is_absolute() or not private_base.is_absolute():
        raise OperatorAuthorityError("operator_authority_unsafe_path")
    _ensure_directory(
        target_config.parent, owner=target_owner, mode=0o700,
        existing_modes=frozenset({0o700, 0o755}),
    )
    _ensure_directory(target_config, owner=target_owner, mode=0o700)
    birth = target_config / "birth"
    _ensure_directory(birth, owner=target_owner, mode=0o755)

    _ensure_directory(private_base, owner=private_owner, mode=0o700)
    private = private_base / str(target_owner[0])
    _ensure_directory(private, owner=private_owner, mode=0o700)
    unexpected = {item.name for item in private.iterdir()} - PRIVATE_FILES
    if unexpected:
        raise OperatorAuthorityError(
            "operator_authority_private_invalid", ",".join(sorted(unexpected)),
        )
    operator, operator_created = _load_or_create_private(
        private / "operator-key.priv", owner=private_owner,
    )
    reviewer, reviewer_created = _load_or_create_private(
        private / "review-key.priv", owner=private_owner,
    )
    expected = _documents(_public_bytes(operator), _public_bytes(reviewer))

    public = birth / "operator-input-v1"
    if public.exists() or public.is_symlink():
        _verify_public(public, expected, owner=target_owner)
        return {
            "status": "verified", "private_created": False,
            "private_dir": str(private), "public_dir": str(public),
        }

    temporary = Path(tempfile.mkdtemp(prefix=".operator-input-v1.", dir=birth))
    try:
        semantic = temporary / "semantic-public"
        semantic.mkdir(mode=0o755)
        _write_public(
            temporary / "approval-authority.json",
            expected["approval-authority.json"], owner=target_owner,
        )
        _write_public(
            temporary / "semantic-authority.json",
            expected["semantic-authority.json"], owner=target_owner,
        )
        _write_public(semantic / "review.pub", expected["review.pub"], owner=target_owner)
        os.chown(semantic, *target_owner)
        semantic.chmod(0o755)
        os.chown(temporary, *target_owner)
        temporary.chmod(0o755)
        os.rename(temporary, public)
    except BaseException:
        for candidate in (
            temporary / "semantic-public" / "review.pub",
            temporary / "approval-authority.json",
            temporary / "semantic-authority.json",
        ):
            try:
                candidate.unlink()
            except FileNotFoundError:
                pass
        for candidate in (temporary / "semantic-public", temporary):
            try:
                candidate.rmdir()
            except FileNotFoundError:
                pass
        raise
    _verify_public(public, expected, owner=target_owner)
    return {
        "status": "created",
        "private_created": operator_created or reviewer_created,
        "private_dir": str(private), "public_dir": str(public),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install fresh public Birth registries for one Metnos account.",
    )
    parser.add_argument("--user", required=True, help="non-root Metnos service account")
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        print(_canonical({"error": "operator_authority_requires_root"}).decode())
        return 2
    try:
        account = pwd.getpwnam(args.user)
        if account.pw_uid == 0 or not account.pw_dir.startswith("/"):
            raise OperatorAuthorityError("operator_authority_target_invalid", args.user)
        home = Path(account.pw_dir)
        home_status = _status(home)
        if not stat.S_ISDIR(home_status.st_mode) or home_status.st_uid != account.pw_uid:
            raise OperatorAuthorityError("operator_authority_target_invalid", args.user)
        result = provision_paths(
            target_config=home / ".config" / "metnos",
            private_base=PRIVATE_BASE,
            target_owner=(account.pw_uid, account.pw_gid),
            private_owner=(0, 0),
        )
    except (KeyError, OperatorAuthorityError, OSError) as exc:
        code = getattr(exc, "code", "operator_authority_target_invalid")
        print(_canonical({"error": code}).decode())
        return 1
    print(_canonical(result).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
