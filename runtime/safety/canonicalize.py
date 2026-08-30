"""canonicalize.py — argv → signature, deterministic and pure Python.

The command-canonicalisation lists (privilege wrapper names, benign flags,
subcommand-style binaries, target-kind hints) live in the JSON companion
file `canonicalize_rules.json`.  The much smaller privilege-wrapper option
grammar is intentionally closed in this module: changing which wrapper modes
may cross the execution boundary requires code review, not a data-only edit.
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
import hashlib
import os
import posixpath
import re
import unicodedata
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
_SUBCMD_BINS: frozenset[str] = frozenset(_RULES["subcommand_style_binaries"])
_NO_SUBCOMMAND_BINS: frozenset[str] = frozenset(_RULES["no_subcommand_binaries"])
_FLAG_AGG_BINS: frozenset[str] = frozenset(_RULES["flag_aggregating_binaries"])
_BINARY_TARGET_HINTS: dict[tuple[str, str], str] = {
    (h["binary"], h["subcommand"]): h["target_kind"]
    for h in _RULES["binary_target_hints"]
}


def command_grammar_binaries() -> frozenset[str]:
    """Return every binary with an explicit canonicalisation grammar.

    The planner uses this inventory only to make the guarded ``admin``
    fallback visible when a request names a real command.  It is deliberately
    not a permission check: whitelist/graylist/blacklist decisions remain the
    responsibility of the safety store at execution time.

    Keeping the inventory here means that adding a command family to
    ``canonicalize_rules.json`` automatically makes it discoverable without a
    second, inevitably incomplete list in the prefilter.
    """
    return frozenset().union(
        _SUBCMD_BINS,
        _NO_SUBCOMMAND_BINS,
        _FLAG_AGG_BINS,
        # These two families have specialised canonicalisation below rather
        # than entries in the JSON family lists.
        {"mount", "umount"},
    )

# Single-letter flags often combined into one token, like `rm -rf` → `-rf`.
_FLAG_RE = re.compile(r"^-[A-Za-z][A-Za-z0-9-]*$")
_LONG_FLAG_RE = re.compile(r"^--[a-z][a-z0-9-]*(=.*)?$")

# Block device patterns.
_BLOCK_RE = re.compile(
    r"^/dev/(?:"
    r"sd[a-z]\d*|vd[a-z]\d*|xvd[a-z]\d*|"
    r"nvme\d+n\d+(?:p\d+)?|mmcblk\d+(?:p\d+)?|"
    r"md\d+(?:p\d+)?|dm-\d+|disk\d+|loop\d+|"
    r"mapper/[^/]+|disk/by-(?:id|path|uuid|label|partuuid|partlabel)/[^/]+"
    r")$"
)
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


class ArgvValidationError(ValueError):
    """The privilege boundary cannot determine one exact command argv."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class ValidatedArgv:
    """Immutable result of the argv safety boundary.

    ``argv`` is the normalized tuple that may be executed, ``command_argv``
    is the same tuple with the (optional) privilege wrapper removed, and
    ``argv_json`` is the exact framing used by approval capabilities.  Keeping
    these values in one object prevents signature/card/fire parser drift.
    """

    argv: tuple[str, ...]
    command_argv: tuple[str, ...]
    signature: Signature
    wrapper: str | None
    argv_json: str

    @property
    def requires_privilege(self) -> bool:
        return self.wrapper is not None

    @property
    def binary(self) -> str:
        return self.signature.binary


_ARGV_MAX_TOKENS = 256
_ARGV_MAX_TOKEN_CHARS = 8192
_ARGV_MAX_JSON_CHARS = 65536
_ARGV_DISPLAY_MAX_CHARS = 4096


def _has_forbidden_control(value: str) -> bool:
    """Return whether *value* contains an argv-ambiguous Unicode control."""

    return any(unicodedata.category(character) in {"Cc", "Cf"}
               for character in value)


def render_argv_for_display(
    argv: ValidatedArgv | list[str] | tuple[str, ...],
) -> str:
    """Render argv as bounded, inert JSON for user-facing surfaces.

    This representation is deliberately separate from ``ValidatedArgv.argv``:
    it is NFKC-normalized and markup-sensitive ASCII characters are escaped,
    but it is never parsed back or passed to a subprocess.
    """

    values = argv.argv if isinstance(argv, ValidatedArgv) else tuple(argv)
    display_values = [
        unicodedata.normalize("NFKC", value)
        if isinstance(value, str) else "<invalid-token>"
        for value in values
    ]
    rendered = json.dumps(
        display_values, ensure_ascii=True, separators=(",", ":"),
    )
    rendered = (rendered.replace("`", r"\u0060")
                .replace("<", r"\u003c")
                .replace(">", r"\u003e")
                .replace("&", r"\u0026"))
    if len(rendered) <= _ARGV_DISPLAY_MAX_CHARS:
        return rendered
    digest = hashlib.sha256(rendered.encode("ascii")).hexdigest()
    return json.dumps(
        [f"<argv omitted: {len(values)} tokens; sha256:{digest}>"],
        ensure_ascii=True, separators=(",", ":"),
    ).replace("<", r"\u003c").replace(">", r"\u003e")


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


_ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_TRUSTED_EXECUTABLE_DIRS = frozenset({
    "/bin", "/sbin", "/usr/bin", "/usr/sbin",
    "/usr/local/bin", "/usr/local/sbin",
})

# Closed grammars: options not listed here are denied instead of being
# mistaken for the wrapped program.  Shell/login/environment-preservation
# modes are deliberately absent because they destroy the one-argv invariant.
_WRAPPER_VALUE_FLAGS: dict[str, frozenset[str]] = {
    "sudo": frozenset({
        "-u", "--user", "-g", "--group", "-h", "--host",
        "-p", "--prompt", "-C", "--close-from",
    }),
    "doas": frozenset({"-u"}),
    "pkexec": frozenset({"-u", "--user"}),
}
_WRAPPER_BOOLEAN_FLAGS: dict[str, frozenset[str]] = {
    "sudo": frozenset({
        "-H", "--set-home", "-n", "--non-interactive", "-S", "--stdin",
    }),
    "doas": frozenset({"-n"}),
    "pkexec": frozenset({"--disable-internal-agent"}),
}


def _parse_privilege_wrapper(argv: list[str]) -> tuple[list[str], str | None]:
    if not argv:
        raise ArgvValidationError("ERR_ARGV_EMPTY", "argv must not be empty")
    wrapper = os.path.basename(argv[0])
    if wrapper not in _SUDO_WRAPPERS:
        return list(argv), None

    value_flags = _WRAPPER_VALUE_FLAGS[wrapper]
    boolean_flags = _WRAPPER_BOOLEAN_FLAGS[wrapper]
    i = 1
    options_ended = False
    while i < len(argv):
        token = argv[i]
        if not options_ended and token == "--":
            options_ended = True
            i += 1
            break
        if not options_ended and token.startswith("-"):
            if token in boolean_flags:
                i += 1
                continue
            if token in value_flags:
                if i + 1 >= len(argv) or not argv[i + 1]:
                    raise ArgvValidationError(
                        "ERR_ARGV_WRAPPER_VALUE", f"{wrapper} option {token} needs a value",
                    )
                i += 2
                continue
            matched = False
            for flag in value_flags:
                if flag.startswith("--") and token.startswith(flag + "="):
                    if token == flag + "=":
                        raise ArgvValidationError(
                            "ERR_ARGV_WRAPPER_VALUE", f"{wrapper} option {flag} needs a value",
                        )
                    matched = True
                    break
                if (len(flag) == 2 and flag.startswith("-")
                        and not token.startswith("--") and token.startswith(flag)
                        and len(token) > 2):
                    matched = True
                    break
            if matched:
                i += 1
                continue
            raise ArgvValidationError(
                "ERR_ARGV_WRAPPER_OPTION",
                f"unsupported {wrapper} option {token}",
            )
        break

    command = list(argv[i:])
    if not command:
        raise ArgvValidationError(
            "ERR_ARGV_WRAPPER_COMMAND", f"{wrapper} has no command",
        )
    target = os.path.basename(command[0])
    if command[0].startswith("-"):
        raise ArgvValidationError(
            "ERR_ARGV_WRAPPER_COMMAND", "wrapped command cannot be an option",
        )
    if target in _SUDO_WRAPPERS:
        raise ArgvValidationError(
            "ERR_ARGV_NESTED_WRAPPER", "nested privilege wrappers are forbidden",
        )
    if target == "env" or _ENV_ASSIGNMENT_RE.match(command[0]):
        raise ArgvValidationError(
            "ERR_ARGV_WRAPPER_ENV", "environment indirection after wrapper is forbidden",
        )
    return command, wrapper


def _normalize_absolute_token(token: str, *, binary: str) -> str:
    """Lexically normalize absolute path values without dereferencing them."""
    if _URL_RE.match(token):
        return token
    # CIFS sources use a normative leading double slash, unlike filesystem
    # aliases such as //dev/sda or //tmp/../ which must collapse to one root.
    if binary == "mount" and _CIFS_SOURCE_RE.match(token):
        return token

    prefix = ""
    value = token
    if "=" in token:
        head, sep, tail = token.partition("=")
        if tail.startswith("/"):
            prefix, value = head + sep, tail
    if value.startswith("/"):
        normalized = posixpath.normpath("/" + value.lstrip("/"))
        return prefix + normalized
    return token


def _signature_for_command(command: list[str], *, home: str | None = None) -> Signature:
    binary = os.path.basename(command[0])
    rest_clean = _strip_benign_flags(list(command[1:]))
    subcmd = _extract_subcommand_or_flag(binary, rest_clean)
    target = _pick_target_kind(binary, subcmd, rest_clean, home=home)
    return Signature(binary=binary, subcommand_or_flag=subcmd, target_kind=target)


def _require_trusted_executable_token(token: str, *, role: str) -> None:
    if "/" not in token:
        return
    path = Path(token)
    if not path.is_absolute() or str(path.parent) not in _TRUSTED_EXECUTABLE_DIRS:
        raise ArgvValidationError(
            "ERR_ARGV_EXECUTABLE_PATH",
            f"{role} executable path is outside trusted binary directories",
        )


def validate_argv(argv: list[str] | tuple[str, ...], *, home: str | None = None) -> ValidatedArgv:
    """Return the single immutable argv snapshot used by every safety stage."""
    if (not isinstance(argv, (list, tuple)) or not argv
            or not all(isinstance(token, str) and token and "\x00" not in token
                       for token in argv)):
        raise ArgvValidationError("ERR_ARGV_STRUCTURE", "argv must contain non-empty strings")
    if (len(argv) > _ARGV_MAX_TOKENS
            or any(len(token) > _ARGV_MAX_TOKEN_CHARS for token in argv)):
        raise ArgvValidationError(
            "ERR_ARGV_BOUNDS", "argv exceeds the administrative boundary",
        )
    if any(_has_forbidden_control(token) for token in argv):
        raise ArgvValidationError(
            "ERR_ARGV_CONTROL", "argv contains control or format characters",
        )
    raw = list(argv)
    command, wrapper = _parse_privilege_wrapper(raw)
    binary = os.path.basename(command[0])
    normalized = [_normalize_absolute_token(token, binary=binary) for token in raw]
    normalized_command, normalized_wrapper = _parse_privilege_wrapper(normalized)
    if normalized_wrapper is not None:
        _require_trusted_executable_token(normalized[0], role="wrapper")
    _require_trusted_executable_token(normalized_command[0], role="command")
    if os.path.basename(normalized_command[0]) == "env":
        raise ArgvValidationError(
            "ERR_ARGV_WRAPPER_ENV", "environment command indirection is forbidden",
        )
    signature = _signature_for_command(normalized_command, home=home)
    frozen = tuple(normalized)
    canonical_json = json.dumps(
        list(frozen), ensure_ascii=False, separators=(",", ":"),
    )
    if len(canonical_json.encode("utf-8")) > _ARGV_MAX_JSON_CHARS:
        raise ArgvValidationError(
            "ERR_ARGV_BOUNDS", "argv exceeds the administrative boundary",
        )
    return ValidatedArgv(
        argv=frozen,
        command_argv=tuple(normalized_command),
        signature=signature,
        wrapper=normalized_wrapper,
        argv_json=canonical_json,
    )


def _strip_sudo_wrapper(argv: list[str]) -> tuple[list[str], bool]:
    """Compatibility helper backed by the closed privilege grammar."""
    command, wrapper = _parse_privilege_wrapper(argv)
    return command, wrapper is not None


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
    return validate_argv(argv, home=home).signature


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
