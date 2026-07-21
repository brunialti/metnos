"""find_signatures — unified lookup tool over the safety store (ADR 0071).

Single executor, dispatched by the `kind` argument. Replaces six earlier
executors (find_signatures_blacklist/whitelist/graylist/forbidden/
seed_diff/promotion_candidates) to keep the surface visible to a
medium-tier LLM small and easy to choose from.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "runtime"))

from messages import get as _msg  # noqa: E402
from executor_helpers import run_stdio  # noqa: E402

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

from safety.canonicalize import Signature, signature_matches
from safety.storage import SafetyStore
from safety.seed_bootstrap import DEFAULT_SEED_PATH


_SUDO_WRAPPERS = {"sudo", "doas", "pkexec"}
_FORBIDDEN_BINARIES_DESTRUCTIVE = frozenset({
    "rm", "mv", "cp", "dd", "mkfs", "shred", "wipefs",
    "mkfs.ext4", "mkfs.ext3", "mkfs.ext2",
    "mkfs.xfs", "mkfs.btrfs", "mkfs.fat", "mkfs.vfat",
})
_FORBIDDEN_PATHS = frozenset({
    "/", "/etc", "/boot",
    "/proc", "/sys", "/usr", "/lib", "/lib64",
})
_BLOCK_DEVICE_RE = re.compile(
    r"^/dev/(sd[a-z]\d*|nvme\d+n\d+(p\d+)?|disk\d+|loop\d+|mmcblk\d+(p\d+)?)$"
)


def _age_days(last_used_at: str | None) -> int | None:
    if not last_used_at:
        return None
    try:
        s = last_used_at.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return int((datetime.now(timezone.utc) - dt).total_seconds() // 86400)
    except (ValueError, TypeError):
        return None


def _check_blacklist(store: SafetyStore, signature: str) -> dict:
    try:
        sig = Signature.parse(signature)
    except ValueError as e:
        return {"ok": False, "error": _msg("ERR_ARG_INVALID", arg="signature", reason=str(e))}
    for kind in ("blacklist", "forbidden"):
        for row in store.find_by_kind(kind):
            if signature_matches(sig, row.signature):
                return {
                    "ok": True,
                    "negate": True,
                    "matched_pattern": row.signature,
                    "severity": row.severity,
                    "reason": row.reason,
                }
    return {"ok": True, "negate": False, "matched_pattern": None}


def _check_whitelist(
    store: SafetyStore, signature: str, record_use: bool
) -> dict:
    try:
        sig = Signature.parse(signature)
    except ValueError as e:
        return {"ok": False, "error": _msg("ERR_ARG_INVALID", arg="signature", reason=str(e))}
    for kind in ("whitelist", "graylist"):
        for row in store.find_by_kind(kind):
            if signature_matches(sig, row.signature):
                uses = (
                    store.record_use(row.signature) if record_use else row.uses
                )
                age_class = "permanent" if kind == "whitelist" else "graylist"
                return {
                    "ok": True,
                    "allow": True,
                    "age_class": age_class,
                    "uses": uses,
                    "last_used_at": row.last_used_at,
                    "matched_pattern": row.signature,
                    "severity": row.severity,
                }
    return {
        "ok": True,
        "allow": False,
        "age_class": None,
        "uses": 0,
        "last_used_at": None,
    }


def _check_forbidden(argv: list[str]) -> dict:
    if not argv:
        return {"ok": False, "error": _msg("ERR_ARG_NOT_LIST", arg="argv")}
    binary = os.path.basename(argv[0])
    rest = argv[1:]
    if binary in _SUDO_WRAPPERS and rest:
        for i, t in enumerate(rest):
            if not t.startswith("-"):
                binary = os.path.basename(t)
                rest = rest[i + 1:]
                break
    if binary not in _FORBIDDEN_BINARIES_DESTRUCTIVE:
        return {"ok": True, "negate": False, "matched_path": None}
    for tok in rest:
        value = (
            tok.split("=", 1)[-1]
            if "=" in tok and not tok.startswith("-")
            else tok
        )
        if value in _FORBIDDEN_PATHS:
            return {
                "ok": True,
                "negate": True,
                "matched_path": value,
                "reason": _msg("MSG_SIG_LAW1_DESTRUCTIVE", binary=binary, value=value),
            }
        if _BLOCK_DEVICE_RE.match(value):
            return {
                "ok": True,
                "negate": True,
                "matched_path": value,
                "reason": _msg("MSG_SIG_LAW1_BLOCKDEV", binary=binary, value=value),
            }
    return {"ok": True, "negate": False, "matched_path": None}


def _list_graylist(store: SafetyStore) -> dict:
    rows = store.find_by_kind("graylist")
    entries = [
        {
            "signature": r.signature,
            "severity": r.severity,
            "source": r.source,
            "uses": r.uses,
            "last_used_at": r.last_used_at,
            "age_days_since_last_use": _age_days(r.last_used_at),
            "reason": r.reason,
        }
        for r in rows
    ]
    return {"ok": True, "count": len(entries), "entries": entries}


def _list_promotion_candidates(
    store: SafetyStore, min_uses: int, max_age_days: int
) -> dict:
    rows = store.find_promotion_candidates(
        min_uses=min_uses, max_age_days=max_age_days
    )
    entries = [
        {
            "signature": r.signature,
            "uses": r.uses,
            "last_used_at": r.last_used_at,
            "severity": r.severity,
            "reason": r.reason,
            "suggested_action": (
                f"promote to whitelist (uses={r.uses})"
            ),
        }
        for r in rows
    ]
    return {
        "ok": True,
        "count": len(entries),
        "entries": entries,
        "threshold": {"min_uses": min_uses, "max_age_days": max_age_days},
    }


def _list_all(store: SafetyStore) -> dict:
    entries = [
        {
            "signature": r.signature,
            "kind": r.kind,
            "severity": r.severity,
            "source": r.source,
            "uses": r.uses,
            "last_used_at": r.last_used_at,
        }
        for r in store.all_signatures()
    ]
    return {"ok": True, "count": len(entries), "entries": entries}


def _seed_diff(store: SafetyStore, seed_path: Path) -> dict:
    if not seed_path.exists():
        return {"ok": False, "error": _msg("ERR_PATH_NOT_FOUND", path=seed_path)}
    with open(seed_path, "rb") as f:
        seed = tomllib.load(f)
    seed_entries = {e["sig"]: e for e in seed.get("signatures", [])}
    db_entries = {r.signature: r for r in store.all_signatures()}
    diff = {
        "added": [],
        "user_overridden": [],
        "modified": [],
        "removed": [],
        "user_only": [],
        "auto_promoted": [],
    }
    for sig, entry in seed_entries.items():
        db = db_entries.get(sig)
        if db is None:
            diff["added"].append({"signature": sig, "kind": entry["kind"]})
        elif db.source == "user":
            diff["user_overridden"].append({
                "signature": sig,
                "seed_kind": entry["kind"],
                "user_kind": db.kind,
            })
        elif db.kind != entry["kind"] or db.severity != entry.get("severity"):
            diff["modified"].append({
                "signature": sig,
                "old": {"kind": db.kind, "severity": db.severity},
                "new": {"kind": entry["kind"], "severity": entry.get("severity")},
            })
    for sig, db in db_entries.items():
        if db.source == "seed" and sig not in seed_entries:
            diff["removed"].append({"signature": sig, "kind": db.kind})
        elif db.source == "user" and sig not in seed_entries:
            diff["user_only"].append({"signature": sig, "kind": db.kind})
        elif db.source == "auto-promoted":
            diff["auto_promoted"].append({
                "signature": sig, "kind": db.kind, "uses": db.uses,
            })
    return {
        "ok": True,
        "seed_path": str(seed_path),
        "seed_version": seed.get("version"),
        "db_version": store.latest_seed_version(),
        "diff": diff,
        "totals": {k: len(v) for k, v in diff.items()},
    }


def invoke(args: dict, ctx: dict | None = None) -> dict:
    kind = args.get("kind")
    if not kind:
        return {"ok": False, "error": _msg("ERR_ARG_MISSING", arg="kind")}

    store = SafetyStore()
    try:
        if kind == "blacklist":
            sig = args.get("signature")
            if not sig:
                return {"ok": False, "error": _msg("ERR_ARG_MISSING", arg="signature")}
            return _check_blacklist(store, sig)
        if kind == "whitelist":
            sig = args.get("signature")
            if not sig:
                return {"ok": False, "error": _msg("ERR_ARG_MISSING", arg="signature")}
            return _check_whitelist(
                store, sig, bool(args.get("record_use", False))
            )
        if kind == "forbidden":
            argv = args.get("argv")
            if not isinstance(argv, list):
                return {"ok": False, "error": _msg("ERR_ARG_MISSING", arg="argv")}
            return _check_forbidden(argv)
        if kind == "graylist":
            return _list_graylist(store)
        if kind == "promotion_candidates":
            return _list_promotion_candidates(
                store,
                int(args.get("min_uses", 5)),
                int(args.get("max_age_days", 30)),
            )
        if kind == "all":
            return _list_all(store)
        if kind == "seed_diff":
            seed_path = Path(args.get("seed_path") or DEFAULT_SEED_PATH)
            return _seed_diff(store, seed_path)
        return {"ok": False, "error": _msg("ERR_ARG_ENUM", arg="kind", allowed=str(kind))}
    finally:
        store.close()



def main():
    run_stdio(invoke, default=str, allow_empty=True)


if __name__ == "__main__":
    main()
