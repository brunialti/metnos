"""Censimento e quarantena dei residui Synt privi di contratto.

La simulazione e' il comportamento predefinito.  L'applicazione non cancella
mai dati: dopo avere scritto e verificato una ricevuta firmata, sposta il
residuo in una quarantena sullo stesso filesystem.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Callable, Iterable

import config as _C
from audit_jsonl import append_unique_jsonl
from sign import (
    DEFAULT_AUTHOR_KEY,
    TrustedPublic,
    list_trusted_publics,
    load_private,
    sign_manifest_bytes,
    verify_manifest_bytes,
)


RECEIPT_SCHEMA = "metnos.synth-orphan-quarantine/v1"
SIGNATURE_DOMAIN = b"metnos.synth-orphan-quarantine/v1\x00"
EVENT_DOMAIN = b"metnos.synth-orphan-quarantine-event/v1\x00"
DEFAULT_RECEIPTS = _C.PATH_USER_STATE / "synth-orphan-quarantine.jsonl"
DEFAULT_QUARANTINE_NAME = "_quarantine_incomplete_synth"
EXCLUDED_NAMES = frozenset({"_imports", "skills", DEFAULT_QUARANTINE_NAME})


class OrphanCleanupError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def _regular_file_snapshot(path: Path) -> tuple[os.stat_result, bytes]:
    try:
        before = path.lstat()
    except OSError as exc:
        raise OrphanCleanupError("candidate_unavailable", str(path)) from exc
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise OrphanCleanupError("candidate_file_forbidden", str(path))
    try:
        data = path.read_bytes()
        after = path.lstat()
    except OSError as exc:
        raise OrphanCleanupError("candidate_unavailable", str(path)) from exc
    identity = lambda item: (
        item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_nlink,
    )
    if identity(before) != identity(after):
        raise OrphanCleanupError("candidate_changed", str(path))
    return after, data


def _candidate(directory: Path) -> dict:
    if directory.is_symlink():
        raise OrphanCleanupError("candidate_link_forbidden", str(directory))
    try:
        directory_status = directory.stat()
        children = tuple(directory.iterdir())
    except OSError as exc:
        raise OrphanCleanupError("candidate_unavailable", str(directory)) from exc
    if not stat.S_ISDIR(directory_status.st_mode):
        raise OrphanCleanupError("candidate_path_invalid", str(directory))
    expected = directory / f"{directory.name}.py"
    if children != (expected,) and set(children) != {expected}:
        raise OrphanCleanupError("candidate_not_incomplete_orphan", str(directory))
    file_status, data = _regular_file_snapshot(expected)
    digest = hashlib.sha256(data).hexdigest()
    return {
        "name": directory.name,
        "relative_file": expected.name,
        "sha256": digest,
        "size": len(data),
        "file_dev": file_status.st_dev,
        "file_ino": file_status.st_ino,
        "file_mtime_ns": file_status.st_mtime_ns,
        "directory_dev": directory_status.st_dev,
        "directory_ino": directory_status.st_ino,
        "directory_mtime_ns": directory_status.st_mtime_ns,
    }


def discover(root: Path) -> tuple[list[dict], list[dict]]:
    """Restituisce candidati e rifiuti senza modificare il filesystem."""
    candidates: list[dict] = []
    rejected: list[dict] = []
    if not root.is_dir() or root.is_symlink():
        raise OrphanCleanupError("source_root_invalid", str(root))
    for directory in sorted(root.iterdir(), key=lambda item: item.name):
        if directory.name in EXCLUDED_NAMES or not directory.is_dir():
            continue
        try:
            candidates.append(_candidate(directory))
        except OrphanCleanupError as exc:
            rejected.append({"name": directory.name, "error": exc.code})
    return candidates, rejected


def _default_admitted_names() -> set[str]:
    try:
        from loader import load_catalog
        return {item.name for item in load_catalog(include_synth=True)}
    except Exception as exc:
        raise OrphanCleanupError(
            "verified_catalog_unavailable", type(exc).__name__,
        ) from exc


def _payload(item: dict, *, actor: str) -> dict:
    stable = {
        "schema": RECEIPT_SCHEMA,
        "action": "quarantine_incomplete_synth_candidate",
        "actor": actor,
        "name": item["name"],
        "relative_file": item["relative_file"],
        "sha256": item["sha256"],
        "size": item["size"],
        "file_mtime_ns": item["file_mtime_ns"],
        "reason": "legacy_incomplete_synth_candidate",
    }
    stable["event_id"] = "sha256:" + hashlib.sha256(
        EVENT_DOMAIN + _canonical(stable),
    ).hexdigest()
    return stable


def _signed_receipt(payload: dict, *, private_key, key_id: str) -> dict:
    signed = SIGNATURE_DOMAIN + _canonical(payload)
    signature = sign_manifest_bytes(signed, private_key=private_key)
    return {
        "event_id": payload["event_id"],
        "payload": payload,
        "signature": {
            "algorithm": "ed25519",
            "key_id": key_id,
            "value": base64.b64encode(signature).decode("ascii"),
        },
    }


def verify_receipt(receipt: dict, trusted_publics: Iterable[TrustedPublic]):
    try:
        payload = receipt["payload"]
        signature = receipt["signature"]
        encoded = signature["value"]
        if receipt["event_id"] != payload["event_id"]:
            raise ValueError("event identity mismatch")
        event_material = dict(payload)
        supplied_event_id = event_material.pop("event_id")
        expected_event_id = "sha256:" + hashlib.sha256(
            EVENT_DOMAIN + _canonical(event_material),
        ).hexdigest()
        if supplied_event_id != expected_event_id:
            raise ValueError("event digest mismatch")
        if signature["algorithm"] != "ed25519":
            raise ValueError("signature algorithm mismatch")
        raw = base64.b64decode(encoded, validate=True)
    except (KeyError, TypeError, ValueError) as exc:
        raise OrphanCleanupError("receipt_invalid", str(exc)) from exc
    signer = verify_manifest_bytes(
        SIGNATURE_DOMAIN + _canonical(payload), raw,
        trusted_publics=trusted_publics,
    )
    if signature.get("key_id") != signer.name:
        raise OrphanCleanupError("receipt_signer_mismatch", signer.name)
    return signer


def _read_exact_receipt(path: Path, event_id: str) -> dict:
    matches: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise OrphanCleanupError("receipt_read_failed", str(path)) from exc
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (TypeError, ValueError) as exc:
            raise OrphanCleanupError("receipt_store_invalid", str(path)) from exc
        if record.get("event_id") == event_id:
            matches.append(record)
    if len(matches) != 1:
        raise OrphanCleanupError("receipt_not_unique", event_id)
    return matches[0]


def _assert_unchanged(source: Path, item: dict) -> None:
    fresh = _candidate(source)
    keys = (
        "sha256", "size", "file_dev", "file_ino", "file_mtime_ns",
        "directory_dev", "directory_ino", "directory_mtime_ns",
    )
    if any(fresh[key] != item[key] for key in keys):
        raise OrphanCleanupError("candidate_changed", item["name"])


def quarantine(
    *,
    root: Path = _C.PATH_SYNTH_EXECUTORS,
    receipts_path: Path = DEFAULT_RECEIPTS,
    quarantine_root: Path | None = None,
    dry_run: bool = True,
    actor: str = "",
    private_key=None,
    key_id: str = DEFAULT_AUTHOR_KEY,
    trusted_publics: Iterable[TrustedPublic] | None = None,
    admitted_names: set[str] | None = None,
    before_rename: Callable[[Path], None] | None = None,
) -> dict:
    candidates, rejected = discover(root)
    admitted = _default_admitted_names() if admitted_names is None else admitted_names
    conflicts = sorted(item["name"] for item in candidates if item["name"] in admitted)
    if conflicts:
        raise OrphanCleanupError("candidate_is_admitted", ",".join(conflicts))
    report = {"dry_run": dry_run, "candidates": candidates, "rejected": rejected,
              "quarantined": [], "repeated": []}
    if dry_run:
        return report
    if not actor.strip():
        raise OrphanCleanupError("actor_required")
    private = load_private(key_id) if private_key is None else private_key
    trusted = tuple(list_trusted_publics() if trusted_publics is None else trusted_publics)
    if not trusted:
        raise OrphanCleanupError("trusted_keys_missing")
    destination_root = quarantine_root or (root / DEFAULT_QUARANTINE_NAME)
    if destination_root.is_symlink():
        raise OrphanCleanupError("quarantine_link_forbidden", str(destination_root))
    try:
        destination_root.mkdir(mode=0o700, parents=False, exist_ok=True)
        destination_status = destination_root.lstat()
    except OSError as exc:
        raise OrphanCleanupError(
            "quarantine_unavailable", str(destination_root),
        ) from exc
    if not stat.S_ISDIR(destination_status.st_mode):
        raise OrphanCleanupError("quarantine_path_invalid", str(destination_root))
    if root.stat().st_dev != destination_root.stat().st_dev:
        raise OrphanCleanupError("quarantine_cross_filesystem")
    for item in candidates:
        source = root / item["name"]
        destination = destination_root / f"{item['name']}--{item['sha256'][:16]}"
        if destination.exists() or destination.is_symlink():
            raise OrphanCleanupError("quarantine_conflict", item["name"])
        payload = _payload(item, actor=actor.strip())
        receipt = _signed_receipt(payload, private_key=private, key_id=key_id)
        verify_receipt(receipt, trusted)
        append_unique_jsonl(receipts_path, receipt)
        durable = _read_exact_receipt(receipts_path, payload["event_id"])
        verify_receipt(durable, trusted)
        if durable != receipt:
            raise OrphanCleanupError("receipt_mismatch", payload["event_id"])
        _assert_unchanged(source, item)
        if before_rename is not None:
            before_rename(source)
        _assert_unchanged(source, item)
        try:
            source.rename(destination)
        except OSError as exc:
            raise OrphanCleanupError("quarantine_rename_failed", item["name"]) from exc
        report["quarantined"].append(str(destination))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="scrive ricevute e sposta in quarantena")
    parser.add_argument("--actor", default="")
    args = parser.parse_args(argv)
    result = quarantine(dry_run=not args.apply, actor=args.actor)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
