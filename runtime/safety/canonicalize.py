"""canonicalize.py — argv → signature, deterministic and pure Python.

The lists used here (privilege wrappers, benign flags, subcommand-style
binaries, target-kind hints) are NOT hardcoded: they live in the JSON
companion file `canonicalize_rules.json` and are loaded at import time.
Adding a new binary or flag rule requires editing only that file.
The actual safety policy (whitelist/blacklist/...) lives in the SQLite
DB, populated from `safety_seeds/v*.toml` — never in this module.


A signature is a colon-separated string with three parts:

    binary : subcommand_or_flag : target_kind

Examples:

    ls : * : fs:user                  (any subcommand of ls on user fs)
    systemctl : status : *            (status of any unit)
    systemctl : restart : *           (restart of any unit, sudo, ask first)
    rm : rf : fs:user                 (rm -rf on user paths, irreversible)
    rm : rf : /                       (rm -rf on root, FORBIDDEN)
    dd : * : block_device             (dd against any block device, FORBIDDEN)

The canonicalisation is intentionally *coarse*: it ignores benign flags
(--quiet, --no-pager, -h/--help) and reduces verbose forms to a stable
short form. Two argvs that do «the same thing» yield the same signature.

This module is the canonical implementation of the rules; the seed file
relies on the same conventions.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path


# ── Rules loader (data, not policy) ───────────────────────────────────
_RULES_PATH = Path(__file__).parent / "canonicalize_rules.json"


def _load_rules(path: Path = _RULES_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


_RULES = _load_rules()


# ── Target kind taxonomy (fixed, ADR 0071, esteso 4/5/2026 ADR 0087) ──
TARGET_KINDS = (
    "*",                # any
    "fs:user",          # /home/<user>/**, ~, ~/...
    "fs:system",        # /etc, /var, /usr, /bin, /lib, ... (excluding user)
    "fs:tmp",           # /tmp, /var/tmp, /run/user/<uid>
    "fs:root",          # the literal root "/" (special-cased; very dangerous)
    "unit",             # systemd unit (service, timer, socket, ...)
    "pkg",              # apt/dpkg package name
    "block_device",     # /dev/sd*, /dev/nvme*, /dev/disk*, /dev/loop*
    "network_iface",    # eth0, wlan0, lo, ...
    "process_pid",      # numeric pid
    "url",              # http(s)://...
    "fs-mount-cifs",    # remote CIFS/SMB share mount source (//host/share)
    "fs-mount-nfs",     # remote NFS share mount source (host:/path)
    "fs-mount",         # generic local mount (loopback, bind, tmpfs, ...)
    "literal",          # falls through (literal string, no abstraction)
)

# Patterns of remote/local mount sources, used by the `mount` family
# canonicalisation. We detect the source token (first positional non-flag
# in `mount [SRC] [DEST]`) and route to the right target kind. Distinct
# enough to gate `cifs` differently from a local bind mount.
_CIFS_SOURCE_RE = re.compile(r"^//[^/]+/.+$")    # //host/share[/path]
_NFS_SOURCE_RE = re.compile(r"^[^/:][^:]*:/.+$") # host:/exported/path

_BENIGN_FLAGS: frozenset[str] = frozenset(_RULES["benign_flags"])
_SUDO_WRAPPERS: frozenset[str] = frozenset(_RULES["sudo_wrappers"])
_SUDO_OWN_FLAGS_VALUE: frozenset[str] = frozenset(_RULES["sudo_own_flags_value"])
_SUDO_OWN_FLAGS_BOOLEAN: frozenset[str] = frozenset(_RULES["sudo_own_flags_boolean"])
_SUBCMD_BINS: frozenset[str] = frozenset(_RULES["subcommand_style_binaries"])
_NO_SUBCOMMAND_BINS: frozenset[str] = frozenset(_RULES["no_subcommand_binaries"])
_FLAG_AGG_BINS: frozenset[str] = frozenset(_RULES["flag_aggregating_binaries"])
_BINARY_TARGET_HINTS: dict[tuple[str, str], str] = {
    (h["binary"], h["subcommand"]): h["target_kind"]
    for h in _RULES["binary_target_hints"]
}

# Single-letter flags often combined into one token, like `rm -rf` → `-rf`.
_FLAG_RE = re.compile(r"^-[A-Za-z][A-Za-z0-9-]*$")
_LONG_FLAG_RE = re.compile(r"^--[a-z][a-z0-9-]*(=.*)?$")

# Block device patterns.
_BLOCK_RE = re.compile(r"^/dev/(sd[a-z]\d*|nvme\d+n\d+(p\d+)?|disk\d+|loop\d+)$")
# Numeric pid (also tolerates leading minus for kill).
_PID_RE = re.compile(r"^-?\d+$")
# URL.
_URL_RE = re.compile(r"^(https?|ftp|sftp)://", re.IGNORECASE)
# Network interface (loose; fine for the canonicalisation purpose).
_NETIFACE_RE = re.compile(
    r"^(lo|eth\d+|en[ospx]\w+|wl[opx]\w+|wlan\d+|wlp\w+|tun\d+|tap\d+|br\d+|docker\d+|virbr\d+)$"
)
# Home di QUALUNQUE utente: /home/<user>[/...]. Un comando distruttivo sull'home
# di un utente non-runtime NON deve scivolare a 'literal' e bypassare il gate
# graylist (sicurezza). Generalizza il check sul solo home runtime.
_USER_HOME_RE = re.compile(r"^/home/[^/]+(/.*)?$")


@dataclass(frozen=True)
class Signature:
    """Structured signature. The string form is `binary:subcmd:target`."""

    binary: str
    subcommand_or_flag: str
    target_kind: str

    def __str__(self) -> str:
        return f"{self.binary}:{self.subcommand_or_flag}:{self.target_kind}"

    @classmethod
    def parse(cls, s: str) -> "Signature":
        parts = s.split(":")
        if len(parts) < 3:
            raise ValueError(f"signature must have at least 3 colon-separated parts: {s!r}")
        # tolerate target kinds with embedded colon (fs:user, fs:system, ...)
        binary = parts[0]
        subcmd = parts[1]
        target = ":".join(parts[2:])
        return cls(binary=binary, subcommand_or_flag=subcmd, target_kind=target)


def classify_target(token: str, *, home: str | None = None) -> str:
    """Classify a single argv token into a `target_kind`.

    The logic is:
      - URL?         → 'url'
      - block device path?  → 'block_device'
      - looks like a pid?   → 'process_pid'
      - looks like a network interface? → 'network_iface'
      - is the literal '/' or empty?    → 'fs:root'
      - is under /tmp or /var/tmp or /run/user? → 'fs:tmp'
      - is under user home (resolved)?  → 'fs:user'
      - is under /etc, /var, /usr, /bin, /sbin, /lib, /opt, /boot? → 'fs:system'
      - otherwise → 'literal'

    `home` defaults to the runtime user home directory.
    """
    if not isinstance(token, str):
        return "literal"
    home_dir = home or str(Path.home())

    # `key=value` style (dd if=…, tar create form, etc.). NOT applied to long
    # flags `--key=value`. We classify the *value* and return its kind if
    # better than 'literal'.
    if "=" in token and not token.startswith("-") and not token.startswith("/"):
        head, _, value = token.partition("=")
        if head and value:
            sub = classify_target(value, home=home)
            if sub != "literal":
                return sub

    # CIFS/SMB share source: //host/share[/subpath] (deve precedere classify
    # per fs:* perche' inizia con '/' come i path filesystem).
    if _CIFS_SOURCE_RE.match(token):
        return "fs-mount-cifs"
    # NFS share source: host:/exported/path (l'host non puo' contenere ':').
    if _NFS_SOURCE_RE.match(token):
        return "fs-mount-nfs"
    # URL
    if _URL_RE.match(token):
        return "url"
    # Block device
    if _BLOCK_RE.match(token):
        return "block_device"
    # Network iface (heuristic; only matches if token looks like an iface name)
    if _NETIFACE_RE.match(token):
        return "network_iface"
    # PID (numeric token, treated as pid only when in argv slots that suggest it;
    # caller may decide; here we just classify token shape)
    if _PID_RE.match(token):
        return "process_pid"
    # Filesystem paths
    if token == "/" or token == "":
        return "fs:root"
    # Resolve relative paths against cwd? We don't, to stay deterministic on
    # the caller side. Just look at the prefix.
    if token.startswith(home_dir + "/") or token == home_dir or token.startswith("~"):
        return "fs:user"
    # Home di un ALTRO utente (o /home stesso): classifica fs:user, non literal,
    # cosi' il gate graylist scatta anche fuori dall'home dell'utente runtime.
    if token == "/home" or _USER_HOME_RE.match(token):
        return "fs:user"
    if token.startswith("/tmp/") or token == "/tmp" or token.startswith("/var/tmp"):
        return "fs:tmp"
    if token.startswith("/run/user/"):
        return "fs:tmp"
    for sysprefix in (
        "/etc", "/var", "/usr", "/bin", "/sbin", "/lib", "/opt",
        "/boot", "/root", "/proc", "/sys", "/dev",
    ):
        if token == sysprefix or token.startswith(sysprefix + "/"):
            return "fs:system"
    # Doesn't look like anything we recognise → literal.
    return "literal"


def _strip_benign_flags(args: list[str]) -> list[str]:
    """Remove benign flags from an argv tail."""
    out: list[str] = []
    skip_next = False
    for a in args:
        if skip_next:
            skip_next = False
            continue
        if a in _BENIGN_FLAGS:
            continue
        # Some benign flags take a value (--color always|auto|never); strip the
        # flag and its value when the flag itself is on the benign list and is
        # followed by a non-flag.
        if a.startswith("--color") and "=" in a:
            continue
        out.append(a)
    return out


def _extract_subcommand_or_flag(binary: str, rest: list[str]) -> str:
    """Pick the most informative «verb» of the command after the binary.

    Heuristics by binary family:
      - subcommand-style binaries (systemctl, apt, git, docker, ...):
        first non-flag token is the subcommand (status, install, ...).
      - no-subcommand binaries (cat, ls, head, ...): the first non-flag
        token is normally a filename/argument, NOT a verb. Subcommand = '*'.
      - flag-aggregating binaries (rm, cp, mv, chmod, ...): collapse
        single-letter flags into a stable sorted string ('-rf' → 'fr').
      - default: if first arg is a flag, return the flag (sorted),
        else fall back to '*' (we don't treat random tokens as subcommands).
    """
    if not rest:
        return "*"

    # ── Family `mount`: -t TYPE drives the subcommand (cifs, nfs, ext4...).
    # Senza -t TYPE, mount usa tipo auto: subcommand = "auto".
    # Vedi ADR 0087: la signature mount:cifs:fs-mount-cifs gate il vaglio.
    if binary == "mount":
        for i, tok in enumerate(rest):
            if tok in ("-t", "--types") and i + 1 < len(rest):
                return rest[i + 1]
            if tok.startswith("-t="):
                return tok[3:]
            if tok.startswith("--types="):
                return tok[len("--types="):]
        return "auto"
    if binary == "umount":
        # umount non ha subcommand utile: ritorna "*" e il target_kind
        # discrimina (fs:user, fs:root, fs-mount-cifs, ...).
        return "*"

    # Subcommand-style binaries: the first non-flag IS the subcommand.
    if binary in _SUBCMD_BINS:
        for tok in rest:
            if not tok.startswith("-"):
                return tok
        return "*"

    # No-subcommand binaries: don't pick up a filename as subcommand.
    if binary in _NO_SUBCOMMAND_BINS:
        # If the first token is a flag, surface it as a hint subcommand;
        # otherwise '*'.
        for tok in rest:
            if tok.startswith("--"):
                return tok.lstrip("-").split("=", 1)[0]
            if tok.startswith("-") and len(tok) > 1:
                # collapse single-letter combos
                chars = [c for c in tok[1:] if c.isalpha()]
                if chars:
                    return "".join(sorted(set(chars)))
        return "*"

    # Flag-aggregating binaries (rm, cp, mv, chmod, ...).
    if binary in _FLAG_AGG_BINS:
        flags: list[str] = []
        for tok in rest:
            if not tok.startswith("-"):
                continue
            if tok.startswith("--"):
                flags.append(tok.lstrip("-").split("=", 1)[0])
            else:
                # collapse single-letter combos: -rf → r, f
                for ch in tok[1:]:
                    if ch.isalpha():
                        flags.append(ch)
        if not flags:
            return "*"
        # sort + dedup for stability
        return "".join(sorted(set(flags)))

    # Default: first arg
    first = rest[0]
    if first.startswith("--"):
        return first.lstrip("-").split("=", 1)[0]
    if first.startswith("-"):
        # collapse single-letter combos
        chars = [c for c in first[1:] if c.isalpha()]
        if chars:
            return "".join(sorted(set(chars)))
        return first
    return first


def _pick_target_kind(
    binary: str,
    subcommand: str,
    rest: list[str],
    *,
    home: str | None = None,
) -> str:
    """Determine the target_kind from the argv tail.

    Strategy:
      1. If `(binary, subcommand)` has a known hint in `_BINARY_TARGET_HINTS`,
         and at least one positional argument is present, use the hint.
         (e.g. `systemctl restart nginx` → 'unit', not 'literal'.)
      2. Otherwise, look for the *most specific* (= least permissive) target
         kind among the non-flag tokens.

    Specificity (most → least):
        fs:root > block_device > fs:system > fs:tmp > fs:user >
        process_pid > network_iface > url > pkg > unit > literal > *
    """
    SPECIFICITY = [
        "fs-mount-cifs", "fs-mount-nfs",
        "fs:root", "block_device", "fs:system", "fs:tmp", "fs:user",
        "process_pid", "network_iface", "url", "pkg", "unit",
        "fs-mount", "literal", "*",
    ]

    # Per-binary hint
    hint = _BINARY_TARGET_HINTS.get((binary, subcommand))
    if hint is not None:
        # Make sure there's at least one positional arg to anchor the hint.
        for tok in rest:
            if not tok.startswith("-"):
                return hint
        return "*"

    found: set[str] = set()
    for tok in rest:
        if tok.startswith("-"):
            continue
        kind = classify_target(tok, home=home)
        found.add(kind)
    if not found:
        return "*"
    for k in SPECIFICITY:
        if k in found:
            return k
    return "*"


def _strip_sudo_wrapper(argv: list[str]) -> tuple[list[str], bool]:
    """If `argv[0]` is a privilege wrapper, strip it and its own flags.

    Returns `(stripped_argv, was_wrapped)`. `was_wrapped=True` means the
    caller originally invoked the command via sudo/doas/pkexec.
    """
    if not argv or os.path.basename(argv[0]) not in _SUDO_WRAPPERS:
        return argv, False
    out: list[str] = []
    rest = argv[1:]
    i = 0
    while i < len(rest):
        tok = rest[i]
        # value-bearing sudo flag (-u alice, --user alice)
        if tok in _SUDO_OWN_FLAGS_VALUE:
            i += 2
            continue
        # value-bearing sudo flag in --opt=value form
        if any(tok.startswith(f + "=") for f in _SUDO_OWN_FLAGS_VALUE):
            i += 1
            continue
        # boolean sudo flag
        if tok in _SUDO_OWN_FLAGS_BOOLEAN:
            i += 1
            continue
        # short form combined flags starting with `-`: stop only if not a
        # known sudo flag (the wrapper's flags must come before the wrapped
        # binary, so any unknown flag means we've reached the binary).
        # We've already checked the known sudo flags above; if we get here
        # the first non-sudo-flag token is the wrapped binary.
        out = list(rest[i:])
        break
    else:
        out = []  # argv was just `sudo` with no following command
    return out, True


def compute_signature(argv: list[str], *, home: str | None = None) -> Signature:
    """Reduce an argv to a canonical signature.

    `argv` is a list of strings as it would be passed to subprocess; the first
    element is treated as the binary (basename), the rest is the tail.

    Privilege wrappers (`sudo`, `doas`, `pkexec`) at argv[0] are stripped
    so the signature reflects the *wrapped* binary. The fact that sudo was
    used is metadata for the caller (sudoer) to track separately, not part
    of the signature: `sudo systemctl restart nginx` and
    `systemctl restart nginx` reduce to the same signature.
    """
    if not argv:
        raise ValueError("argv must not be empty")
    stripped, _was_sudo = _strip_sudo_wrapper(argv)
    if not stripped:
        # Either argv was empty after stripping (sudo with no command) or
        # the original argv is a single token: fall back to argv as-is for
        # robustness.
        stripped = argv
    binary = os.path.basename(stripped[0])
    rest = list(stripped[1:])

    # Strip benign flags before extracting subcommand: avoids classifying
    # `systemctl --no-pager status nginx` as having subcommand `--no-pager`.
    rest_clean = _strip_benign_flags(rest)

    subcmd = _extract_subcommand_or_flag(binary, rest_clean)
    target = _pick_target_kind(binary, subcmd, rest_clean, home=home)

    return Signature(binary=binary, subcommand_or_flag=subcmd, target_kind=target)


def has_sudo_wrapper(argv: list[str]) -> bool:
    """Return True if argv begins with a privilege wrapper (sudo/doas/pkexec)."""
    return bool(argv) and os.path.basename(argv[0]) in _SUDO_WRAPPERS


def signature_matches(sig: Signature, pattern: str | Signature) -> bool:
    """Test if a concrete signature matches a (possibly-wildcarded) pattern.

    A '*' in the pattern's `subcommand_or_flag` or `target_kind` matches anything.
    The binary part is always literal (matching `ls` to `ls`, never `ls` to `*`).
    """
    p = pattern if isinstance(pattern, Signature) else Signature.parse(pattern)
    if p.binary != sig.binary:
        return False
    if p.subcommand_or_flag != "*" and p.subcommand_or_flag != sig.subcommand_or_flag:
        return False
    if p.target_kind != "*" and p.target_kind != sig.target_kind:
        return False
    return True
