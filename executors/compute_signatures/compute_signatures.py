"""compute_signatures — unified compute tool (ADR 0071).

Three operations:
  - 'command':       argv → canonical signature
  - 'reversibility': signature → reversibility class + undo hint
  - 'seed_apply':    idempotent seed bootstrap
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "runtime"))

from messages import get as _msg  # noqa: E402
from safety.canonicalize import Signature, compute_signature, has_sudo_wrapper
from safety.seed_bootstrap import bootstrap_safety_seed


_IRREVERSIBLE_BINARIES = frozenset({
    "rm", "dd", "shred", "wipefs",
    "mkfs", "mkfs.ext4", "mkfs.ext3", "mkfs.ext2",
    "mkfs.xfs", "mkfs.btrfs", "mkfs.fat", "mkfs.vfat",
    "fdisk", "parted", "sgdisk",
})

_REVERSIBLE_HINTS: dict[tuple[str, str], str] = {
    ("systemctl", "start"):   "systemctl stop <unit>",
    ("systemctl", "stop"):    "systemctl start <unit>",
    ("systemctl", "restart"): "systemctl stop <unit>",
    ("systemctl", "enable"):  "systemctl disable <unit>",
    ("systemctl", "disable"): "systemctl enable <unit>",
    ("apt", "install"):       "apt remove <pkg>",
    ("apt", "remove"):        "apt install <pkg>",
    ("apt", "purge"):         "apt install <pkg> (config files lost)",
    ("apt-get", "install"):   "apt-get remove <pkg>",
    ("apt-get", "remove"):    "apt-get install <pkg>",
    ("timedatectl", "set-timezone"): "timedatectl set-timezone <prev_tz>",
    ("timedatectl", "set-ntp"):      "timedatectl set-ntp <prev_value>",
    ("ln", "*"):              "rm <link_path>",
}


def _op_command(argv: list[str]) -> dict:
    if not isinstance(argv, list) or not argv:
        return {"ok": False, "error": _msg("ERR_ARG_NOT_LIST", arg="argv")}
    if not all(isinstance(a, str) for a in argv):
        return {"ok": False, "error": _msg("ERR_ARG_NOT_LIST_OF", arg="argv", of="strings")}
    sig = compute_signature(argv)
    return {
        "ok": True,
        "signature": str(sig),
        "binary": sig.binary,
        "subcommand_or_flag": sig.subcommand_or_flag,
        "target_kind": sig.target_kind,
        "requires_sudo": has_sudo_wrapper(argv),
    }


def _op_reversibility(signature: str) -> dict:
    try:
        sig = Signature.parse(signature)
    except ValueError as e:
        return {"ok": False, "error": _msg("ERR_ARG_INVALID", arg="signature", reason=str(e))}

    if sig.binary in _IRREVERSIBLE_BINARIES:
        return {
            "ok": True,
            "class": "irreversible",
            "undo_hint": None,
            "reason": _msg("MSG_SIG_DESTRUCTIVE_BINARY", binary=sig.binary),
        }
    hint = _REVERSIBLE_HINTS.get((sig.binary, sig.subcommand_or_flag))
    if hint is None:
        hint = _REVERSIBLE_HINTS.get((sig.binary, "*"))
    if hint is not None:
        return {
            "ok": True,
            "class": "reversible",
            "undo_hint": hint,
            "reason": _msg("MSG_SIG_KNOWN_REVERSIBLE"),
        }
    return {
        "ok": True,
        "class": "unknown",
        "undo_hint": None,
        "reason": _msg("MSG_SIG_NO_RULE"),
    }


def _op_seed_apply(seed_path: str | None) -> dict:
    p = Path(seed_path) if seed_path else None
    res = bootstrap_safety_seed(seed_path=p)
    return {
        "ok": True,
        "applied": res.applied,
        "skipped": res.skipped,
        "seed_version": res.seed_version,
        "db_version": res.db_version,
        "upgraded": res.upgraded,
        "skipped_signatures": res.skipped_signatures,
        "summary": res.summary_line(),
    }


def invoke(args: dict, ctx: dict | None = None) -> dict:
    op = args.get("op")
    if not op:
        return {"ok": False, "error": _msg("ERR_ARG_MISSING", arg="op")}

    if op == "command":
        argv = args.get("argv")
        return _op_command(argv if isinstance(argv, list) else [])
    if op == "reversibility":
        sig = args.get("signature")
        if not sig:
            return {"ok": False, "error": _msg("ERR_ARG_MISSING", arg="signature")}
        return _op_reversibility(sig)
    if op == "seed_apply":
        return _op_seed_apply(args.get("seed_path"))
    return {"ok": False, "error": _msg("ERR_ARG_ENUM", arg="op", allowed=str(op))}



if __name__ == "__main__":  # pragma: no cover
    import json, sys
    raw = sys.stdin.read() or "{}"
    args = json.loads(raw)
    print(json.dumps(invoke(args), default=str, ensure_ascii=False))
