"""admin — verb-unique builtin orchestrator for shell-like intents (ADR 0070).

The admin runs the four-act flow inside its body:

    [pre-1] syntactic gate (no LLM)         — reject literal shell command
    [1+2+3] single LLM call (`admin.intent_translate` → middle) — kind ∈ {literal_command,
                                                       translated, unknown,
                                                       impossible}
    [4]    deterministic safety tools       — forbidden, blacklist, whitelist
    [5]    approval card (only on miss)     — approve / reject_once /
                                              block_forever

The admin produces a *validated argv plus an approval token*; it does
NOT execute. Execution is the job of `sudoer` (ADR 0070).

Modificato 4/5/2026 (ADR 0088): `admin` diventa visibile al PLANNER come
tool ordinario (`EXPOSE_TO_PLANNER=True`). Ogni call dal PLANNER passa
comunque per il vaglio always-on: la decisione di esecuzione resta
all'utente via carta dialog manager. Sudoer rimane invisibile (solo
admin puo' invocarlo). Vedi `MANIFEST_VIRTUAL` per la signature
visibile al PLANNER e `invoke()` per l'entrypoint dal runtime.
"""
from __future__ import annotations

import json
import hashlib
import os
import re
import sqlite3
import stat
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from string import Formatter
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from logging_setup import get_logger
from messages import get as _msg
import i18n as _i18n

from safety.canonicalize import (
    ArgvValidationError,
    ValidatedArgv,
    render_argv_for_display,
    validate_argv,
)
from safety.storage import SafetyStore

log = get_logger(__name__)


# ── Manifest fingerprint (ADR 0069/0088 enforcement) ──────────────────
# `admin` e' ora ESPOSTO al PLANNER (4/5/2026, ADR 0088): l'utente puo'
# chiedere mount/kill/systemctl/chmod via chat e il PLANNER seleziona
# admin come tool ordinario. La sicurezza resta invariata: ogni call
# emette carta vaglio (no auto-call). Il sudoer (verb='sudoer') resta
# invisibile e invocabile solo dal modulo admin stesso.
NOT_IN_VOCAB = True
EXPOSE_TO_PLANNER = True
AUTHORISED_CALLERS = (
    "runtime.dispatcher", "agent_runtime",  # PLANNER lo invoca via runtime
    "builtins.admin", "builtins.sudoer",
)
VERB = "admin"


# ── Manifest virtuale per il PLANNER (ADR 0088) ───────────────────────
# Equivalente in-code del manifest TOML degli executor handcrafted: il
# loader lo importa per costruire l'`Executor` dataclass visibile al
# PLANNER. Description ~1500 char, leggibile da LLM medium (modello locale
# planner): pattern DEVI/NON DEVI/OK/ERRORE come da CLAUDE.md §6.
MANIFEST_VIRTUAL = {
    "name": "admin",
    "version": "1.1.0",
    "description": (
        "Esegue UN comando shell privilegiato (mount/umount, kill, "
        "systemctl, chmod, chown, ifconfig, apt-get, journalctl, mkdir su "
        "/mnt o /etc, ...). Usalo SOLO quando nessun executor del catalogo "
        "copre l'intento. Ogni call passa per: gate sintattico, classifier "
        "safety (whitelist/graylist/blacklist), carta vaglio all'utente. "
        "Dopo approvazione, esegue via sudoer (che materializza credenziali "
        "e sudo dietro le quinte). Non esegue mai senza approvazione "
        "esplicita o whitelist hit. "
        "DEVI: passare `intent` (1 frase IT/EN, cosa fare) e "
        "`command_proposed` (argv lineare, 1 comando solo, token separati "
        "da spazi). "
        "DEVI: usare path assoluti letterali (`/home/user/...`, NON `~`, "
        "NON `$HOME`). UID hardcoded (es. `uid=1000`), NON `$(id -u)` "
        "(bloccato dal gate). "
        "DEVI: per mount CIFS, usare come mountpoint di default "
        "`/home/user/.local/share/metnos/<nome>` dove `<nome>` e' l'ULTIMO "
        "componente dello share remoto (es. `Immagini` da `//host/Public/media/"
        "Immagini`). Override solo se l'utente specifica un path diverso. "
        "DEVI: prependi `sudo` per comandi che richiedono root (mount, "
        "umount, systemctl, apt, chmod su /etc, chown, ifconfig, "
        "journalctl, mkdir su /mnt o /etc). NON prependere `sudo` per "
        "comandi user-space (mkdir su $HOME, ls). "
        "DEVI: usare placeholder `${METNOS_CIFS_CREDS}` per credenziali; "
        "il sudoer le materializza al fire time. "
        "NON DEVI: combinare comandi con `&&`, `||`, `;`, `|`, `>`, `<`, "
        "`$()`, backtick. Emetti UNA admin per comando: prima `mkdir`, poi "
        "`mount` se serve la dir. "
        "NON DEVI: includere user/password letterali nel command_proposed. "
        "NON DEVI: chiamare admin per operazioni coperte da executor "
        "(find_files, send_messages, get_processes, ...) — quelli vincono. "
        "NON CONFONDERE CON `request_new_executor`: admin e' per il SINGOLO "
        "comando shell privilegiato qui-e-ora; request_new_executor "
        "sintetizza un nuovo verbo riusabile (richiesta ricorrente). "
        "OK: utente \"monta share \\\\NAS\\Public\" → admin(intent=\"mount "
        "cifs share NAS\", command_proposed=\"sudo mount -t cifs "
        "//NAS/Public /mnt/nas -o credentials=${METNOS_CIFS_CREDS}\"). "
        "Backslash → forward slash. "
        "ERRORE: admin(command_proposed=\"mkdir /mnt/x && mount -t cifs "
        "...\") → gate sintattico blocca. "
        "ERRORE: admin per \"trova file *.py\" — usa find_files. "
        "Output: `{ok, decision in [reject|approval_required|"
        "execute_silent|needs_inputs], signature, argv, approval_required, "
        "approval_card, summary}`. `needs_inputs` (ADR 0091) significa che "
        "il runtime deve orchestrare un `get_inputs` per raccogliere "
        "credenziali mancanti e poi ri-invocare admin: avviene tutto "
        "lato runtime, il PLANNER NON deve chiamare get_inputs manualmente."
    ),
    "affinity": [
        # core shell verbs (IT + EN)
        "shell", "comando", "command", "esegui", "execute",
        # mount / umount
        "mount", "monta", "smonta", "share", "nas", "cifs",
        # process control
        "kill", "uccidi", "termina",
        # systemd
        "systemctl", "servizio", "service", "restart", "riavvia",
        # permessi + pacchetti + log + filesystem
        "chmod", "chown", "permessi",
        "apt", "pacchetto", "package", "installa", "install",
        "journalctl", "log", "logs",
        "mkdir",
    ],
    "args": {
        "type": "object",
        "required": ["intent", "command_proposed"],
        "properties": {
            "intent": {
                "type": "string",
                "description": (
                    "Descrizione naturale (1 frase, IT o EN) di cosa "
                    "l'utente vuole fare a livello di sistema. Es. "
                    "\"montare lo share CIFS del NAS sotto /mnt/nas\". "
                    "NO null/vuoto."
                ),
            },
            "command_proposed": {
                "type": "string",
                "description": (
                    "argv di UN comando shell, token separati da spazi. "
                    "DEVI: comando singolo. Path assoluti letterali. "
                    "Placeholder `${METNOS_CIFS_CREDS}` per credenziali. "
                    "UID hardcoded (es. `uid=1000`). "
                    "NON DEVI: `&&`, `||`, `;`, `|`, `>`, `<`, `$()`, "
                    "backtick. "
                    "Es: \"mount -t cifs //192.0.2.20/Public /mnt/nas "
                    "-o credentials=${METNOS_CIFS_CREDS},uid=1000\"."
                ),
            },
            "credentials_domain": {
                "type": "string",
                "description": (
                    "Opzionale. Nome dominio credenziali salvate, formato "
                    "\"<binding>_<host>\" (es. \"cifs_192.0.2.20\"). "
                    "Default auto-derivato dal command_proposed quando "
                    "omesso."
                ),
            },
            "actor_consent_token": {
                "type": "string",
                "description": (
                    "NON impostare manualmente. Il runtime lo inietta al "
                    "rilancio dopo conferma utente sulla carta vaglio. "
                    "Quando presente, admin salta la carta e procede a "
                    "sudoer."
                ),
            },
        },
    },
    "capabilities": [
        {"name": "system:admin",
         "hint": ["mount", "umount", "kill", "systemctl",
                  "chmod", "chown", "ifconfig", "apt", "journalctl"]},
    ],
    "revertible": False,
    "lifecycle": "active",
    # Hint per il loader: l'`Executor` dataclass costruito da questo
    # manifest virtuale e' speciale (no manifest.toml su disco, no
    # firma su file Python). Vedi loader._register_admin_as_executor.
    "is_verb_unique_builtin": True,
}


# ── Pre-filter: syntactic gate against literal shell input ────────────
_SHELL_LITERAL_PATTERNS = [
    re.compile(r"\bsudo\s"),         # explicit sudo invocation
    re.compile(r"\bdoas\s"),
    re.compile(r"\bpkexec\s"),
    re.compile(r";\s*\S"),           # command separator
    re.compile(r"&&"),
    re.compile(r"\|\|"),
    re.compile(r"(?<!\|)\|(?!\|)"),  # pipe (not part of ||)
    re.compile(r"\$\("),             # command substitution $(...)
    re.compile(r"`[^`]*`"),          # backtick subshell
    re.compile(r"^\s*(/|\./)\S"),    # leading absolute or relative path
    re.compile(r">\s*\S"),           # redirection
    re.compile(r"<\s*\S"),
]
_KNOWN_BIN_AT_START = re.compile(
    r"^\s*(rm|mv|cp|dd|mkfs|systemctl|journalctl|apt|apt-get|"
    r"chmod|chown|kill|killall|ps|ls|cat|grep|awk|sed)\b"
)


def _looks_like_literal_shell(text: str, *, allow_sudo_wrapper: bool = False) -> Optional[str]:
    """Return a short reason if the text looks like a literal shell command,
    else None.

    `allow_sudo_wrapper`: True quando l'argv arriva dal PLANNER (path
    `_decide_for_argv`). In quel caso `sudo`/`doas`/`pkexec` come PRIMO
    token e' legittimo (wrapper di privilegi richiesto per mount/systemctl/
    apt/...). Restano vietati pipe, redirect, substitution, separator.
    Default False (path NL→shell legacy, dove sudo come literal e' rifiutato).
    """
    if not text:
        return None
    # 15/5/2026: strip REDACTED placeholders (ADR 0082 scrubber) PRIMA
    # del check shell-meta. Bug live (turn 49418a8a): credenziali inline
    # `username=Admin,password=Jundo@195,...` vengono redacted a
    # `<REDACTED:cred>` ma il `>` matcha redirect pattern. False positive.
    text = re.sub(r"<REDACTED:[^>]+>", "REDACTED", text)
    for pat in _SHELL_LITERAL_PATTERNS:
        # sudo/doas/pkexec come wrapper sono legittimi in PLANNER-path
        if allow_sudo_wrapper and pat.pattern in (r"\bsudo\s", r"\bdoas\s", r"\bpkexec\s"):
            continue
        if pat.search(text):
            return f"matches literal-shell pattern: {pat.pattern!r}"
    if not allow_sudo_wrapper and _KNOWN_BIN_AT_START.match(text):
        return "starts with a known shell binary"
    return None


# ── LLM bridge (single call, JSON-schema-guided) ──────────────────────

LLM_PROMPT_TEMPLATE = """\
You are the intent-to-shell translator inside Metnos's `admin` builtin.

The user has typed (in natural language, possibly in Italian or English):

  >>> {user_text}

Your job is to:
  1. detect if the user is trying to pass a literal shell command;
  2. otherwise, translate the intent into a single shell argv list,
     following these constraints:
       MUST emit a JSON list of strings (argv).
       MUST NOT use pipe (|), redirection (>, <), substitution ($(), ``),
                  command chaining (;, &&, ||).
       MUST split each argument as a separate list element.
       MAY prepend "sudo" if root privileges are required.
       MUST NEVER include literal passwords, tokens or secrets in the argv.
  3. or say you cannot translate / it is impossible.

CIFS / SMB mount (NAS share):
       DEVI emettere argv shape:
         ["sudo","mount","-t","cifs","//<host>/<share>","<mountpoint>",
          "-o","credentials=${{METNOS_CIFS_CREDS}},uid=<uid>"].
       NON DEVI emettere `username=...,password=...` o `pass=...` nel argv:
         le credenziali sono iniettate al fire time da `cifs_helper.py`
         tramite il placeholder `${{METNOS_CIFS_CREDS}}`.
       OK:    "credentials=${{METNOS_CIFS_CREDS}},uid=1000,iocharset=utf8"
       ERRORE:"username=alice,password=hunter2,uid=1000".

Respond with ONE JSON object, exactly one of these shapes:

  {{"kind": "literal_command", "reason": "<short reason>"}}
  {{"kind": "translated", "argv": ["bin","arg1","arg2"]}}
  {{"kind": "unknown", "reason": "<why you don't know>"}}
  {{"kind": "impossible", "reason": "<why this can't be done in shell>"}}
"""


def _default_llm_call(prompt: str) -> str:
    """Bridge to the runtime LLM router. Falls back to ok-but-empty in dev.

    The default tier is ``fast``. Its provider and generation policy belong
    to the tier configuration; this bridge must not keep a second, hidden
    local-model profile.
    """
    try:
        from llm_router import LLMRouter
        from llm_workloads import tier_for

        result = LLMRouter().chat(
            "", prompt, tier=tier_for("admin.intent_translate"),
            max_tokens=400)
        return str(getattr(result, "text", "") or "")
    except Exception as e:  # pragma: no cover (dev fallback)
        log.warning("admin LLM bridge unavailable, returning unknown (%s)", e)
        return '{"kind": "unknown"}'


# ── Wait-prompt emitter ───────────────────────────────────────────────

WAIT_LOW = "MSG_SYSTEM_ADMIN_WAIT_LOW"
WAIT_MEDIUM = "MSG_SYSTEM_ADMIN_WAIT_MEDIUM"
WAIT_HIGH = "MSG_SYSTEM_ADMIN_WAIT_HIGH"


# ── Decision dataclasses ──────────────────────────────────────────────

@dataclass
class AdminDecision:
    """Outcome of `admin.decide()`. One of these mutually-exclusive states.

    - kind='reject':         reject with a reason (gate / forbidden / blacklist
                              hit / impossible / unknown / user_block / user_reject).
    - kind='execute_silent': proceed to sudoer with the validated argv.
                              age_class: 'permanent' or 'graylist'.
    - kind='ask_user':       show the approval card and wait for a reply.
                              The card_payload is rendered by the channel.
    - kind='needs_inputs':   credenziali (o altro input strutturato) mancanti.
                              Il runtime auto-orchestra `get_inputs(fmt='auto')`
                              con `on_complete` callback che salva le credenziali
                              cifrate e ri-invoca admin con args originali (ADR
                              0091, 5/5/2026). `needs_inputs_payload` contiene
                              title/description/dialog/fmt/on_complete.
    """
    kind: str
    argv: list[str] = field(default_factory=list)
    signature: str = ""
    reason: str = ""
    age_class: Optional[str] = None
    severity: Optional[str] = None
    requires_sudo: bool = False
    reversibility: Optional[str] = None
    undo_hint: Optional[str] = None
    card_payload: Optional[dict] = None
    needs_inputs_payload: Optional[dict] = None
    audit: dict = field(default_factory=dict)
    validated_argv: Optional[ValidatedArgv] = field(default=None, repr=False)


# ── Reversibility classifier (kept private to avoid duplicating
#    compute_signatures; tiny static map is enough for the decision flow) ─

_IRREVERSIBLE_BINARIES = {
    "rm", "dd", "shred", "wipefs", "mkswap", "blkdiscard",
    "mkfs", "mkfs.ext4", "mkfs.ext3", "mkfs.ext2",
    "mkfs.xfs", "mkfs.btrfs", "mkfs.fat", "mkfs.vfat",
    "fdisk", "parted", "sgdisk",
}
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
}


def _classify_reversibility(sig) -> tuple[str, Optional[str]]:
    if sig.binary in _IRREVERSIBLE_BINARIES or sig.binary.startswith("mkfs."):
        return "irreversible", None
    hint = _REVERSIBLE_HINTS.get((sig.binary, sig.subcommand_or_flag))
    if hint:
        return "reversible", hint
    return "unknown", None


def _severity_for_reversibility(reversibility: str) -> str:
    """Map classifier output to the closed SafetyStore severity domain."""
    if reversibility in {"irreversible", "reversible"}:
        return reversibility
    return "dangerous"


# ── Forbidden check (raw argv, complementary to canonical blacklist) ──

_FORBIDDEN_DESTRUCTIVE_BINS = frozenset({
    "rm", "mv", "cp", "dd", "mkfs", "shred", "wipefs",
    "mkfs.ext4", "mkfs.ext3", "mkfs.ext2",
    "mkfs.xfs", "mkfs.btrfs", "mkfs.fat", "mkfs.vfat",
    "mkfs.exfat", "mkfs.ntfs", "mkswap", "blkdiscard",
    "fdisk", "parted", "sgdisk",
})
_FORBIDDEN_PATHS = frozenset({
    "/", "/etc", "/boot", "/proc", "/sys", "/usr", "/lib", "/lib64",
})
_BLOCK_DEVICE_RE = re.compile(r"^/dev(?:/|$)")
_RAW_DISK_PRIMITIVES = frozenset({
    "dd", "mkfs", "wipefs", "mkswap", "blkdiscard",
    "fdisk", "parted", "sgdisk",
})


def _validated(argv: ValidatedArgv | list[str] | tuple[str, ...]) -> ValidatedArgv:
    return argv if isinstance(argv, ValidatedArgv) else validate_argv(argv)


def _forbidden_argv_target(
    argv: ValidatedArgv | list[str] | tuple[str, ...],
) -> tuple[str, str, str] | None:
    try:
        validated = _validated(argv)
    except ArgvValidationError as exc:
        return "wrapper", exc.detail, "invalid_argv"
    binary = validated.binary
    if binary in _RAW_DISK_PRIMITIVES or binary.startswith("mkfs."):
        # These programs open raw storage after the generic approval boundary.
        # No path snapshot can make that operation TOCTOU-safe; a dedicated,
        # handle-bound executor is required instead.
        return binary, binary, "raw_disk_primitive"
    if (binary not in _FORBIDDEN_DESTRUCTIVE_BINS
            and not binary.startswith("mkfs.")):
        return None
    for tok in validated.command_argv[1:]:
        value = tok.partition("=")[2] if "=" in tok else tok
        if not value.startswith("/"):
            continue
        if value in _FORBIDDEN_PATHS:
            return binary, value, "destructive_path"
        if _BLOCK_DEVICE_RE.match(value):
            return binary, value, "block_device"
        current = Path("/")
        for component in Path(value).parts[1:]:
            current /= component
            try:
                mode = os.lstat(current).st_mode
            except (FileNotFoundError, NotADirectoryError):
                break
            except OSError:
                # An uninspectable path component is ambiguous; destructive
                # commands fail closed at both plan and fire time.
                return binary, value, "ambiguous_target"
            if stat.S_ISLNK(mode):
                return binary, value, "ambiguous_target"
            if current == Path(value) and stat.S_ISBLK(mode):
                return binary, value, "block_device"
    return None


def _check_forbidden_argv(
    argv: ValidatedArgv | list[str] | tuple[str, ...],
) -> tuple[bool, Optional[str]]:
    """Returns (negate, stable audit reason) for raw path-level bombs."""
    target = _forbidden_argv_target(argv)
    if target is None:
        return False, None
    binary, value, kind = target
    if kind == "invalid_argv":
        return True, f"invalid privilege argv: {value}"
    if kind == "raw_disk_primitive":
        return True, f"raw-disk primitive '{binary}' is forbidden (Law 1)"
    if kind == "block_device":
        return True, f"destructive '{binary}' on block device '{value}' (Law 1)"
    if kind == "ambiguous_target":
        return True, f"destructive '{binary}' on ambiguous target '{value}' (Law 1)"
    return True, f"destructive '{binary}' on '{value}' (Law 1)"


def _localized_forbidden_reason(
    argv: ValidatedArgv | list[str] | tuple[str, ...],
) -> str:
    target = _forbidden_argv_target(argv)
    if target is None:
        return _msg("MSG_SYSTEM_ADMIN_POLICY_BLOCKED")
    binary, value, kind = target
    if kind == "invalid_argv":
        return _msg("MSG_SYSTEM_ADMIN_POLICY_BLOCKED")
    if kind == "raw_disk_primitive":
        return _msg("MSG_SYSTEM_ADMIN_POLICY_BLOCKED")
    return _msg(
        "MSG_SYSTEM_ADMIN_FORBIDDEN_BLOCK_DEVICE" if kind == "block_device"
        else "MSG_SYSTEM_ADMIN_FORBIDDEN_DESTRUCTIVE_PATH",
        binary=render_argv_for_display([binary]),
        target=render_argv_for_display([value]),
    )


# ── Approval card UX (22/5/2026): role-aware + danger summary ────────
#
# Whitelist miss → carta vaglio. Differenziamo per ruolo dell'attore:
#
# - actor == 'host'  (admin/proprietario di Metnos):
#     opzioni = [approve_once, approve_and_whitelist, reject_once, block_forever]
#     danger_summary spiega cosa fa il comando. L'admin puo' aggiungerlo
#     permanentemente in whitelist (no carta ogni volta).
#
# - actor == 'guest_<id>'  (utente invitato, autonomy_level<3):
#     opzioni = [run_externally, request_admin_whitelist, reject_once]
#     Metnos non esegue il comando: l'utente lo lancia da solo, oppure
#     richiede al proprietario di aggiungerlo in whitelist (queue su disco).

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as _C  # §7.11
_REQUEST_WHITELIST_QUEUE = _C.PATH_USER_DATA / "admin_whitelist_requests.jsonl"


def _is_admin_actor(actor: str) -> bool:
    """Solo 'host' (proprietario unico di Metnos) e' admin §10.6.
    I guest hanno autonomy_level<3 e non vedono la stessa carta."""
    return actor == "host"


_DANGER_MESSAGE_KEY_BY_BINARY = {
    "rm": "MSG_SYSTEM_ADMIN_DANGER_RM",
    "dd": "MSG_SYSTEM_ADMIN_DANGER_DD",
    "mkfs": "MSG_SYSTEM_ADMIN_DANGER_MKFS",
    "mkfs.ext4": "MSG_SYSTEM_ADMIN_DANGER_MKFS",
    "mkfs.ext3": "MSG_SYSTEM_ADMIN_DANGER_MKFS",
    "mkfs.ext2": "MSG_SYSTEM_ADMIN_DANGER_MKFS",
    "mkfs.xfs": "MSG_SYSTEM_ADMIN_DANGER_MKFS",
    "mkfs.btrfs": "MSG_SYSTEM_ADMIN_DANGER_MKFS",
    "mkfs.fat": "MSG_SYSTEM_ADMIN_DANGER_MKFS",
    "mkfs.vfat": "MSG_SYSTEM_ADMIN_DANGER_MKFS",
    "mkfs.exfat": "MSG_SYSTEM_ADMIN_DANGER_MKFS",
    "mkfs.ntfs": "MSG_SYSTEM_ADMIN_DANGER_MKFS",
    "mkswap": "MSG_SYSTEM_ADMIN_DANGER_MKFS",
    "blkdiscard": "MSG_SYSTEM_ADMIN_DANGER_WIPEFS",
    "shred": "MSG_SYSTEM_ADMIN_DANGER_SHRED",
    "wipefs": "MSG_SYSTEM_ADMIN_DANGER_WIPEFS",
    "fdisk": "MSG_SYSTEM_ADMIN_DANGER_PARTITIONS",
    "parted": "MSG_SYSTEM_ADMIN_DANGER_PARTITIONS",
    "sgdisk": "MSG_SYSTEM_ADMIN_DANGER_PARTITIONS",
    "iptables": "MSG_SYSTEM_ADMIN_DANGER_IPTABLES",
    "ip": "MSG_SYSTEM_ADMIN_DANGER_IP",
    "modprobe": "MSG_SYSTEM_ADMIN_DANGER_MODPROBE",
    "sysctl": "MSG_SYSTEM_ADMIN_DANGER_SYSCTL",
    "mount": "MSG_SYSTEM_ADMIN_DANGER_MOUNT",
    "umount": "MSG_SYSTEM_ADMIN_DANGER_UMOUNT",
    "systemctl": "MSG_SYSTEM_ADMIN_DANGER_SYSTEMCTL",
    "kill": "MSG_SYSTEM_ADMIN_DANGER_KILL",
    "killall": "MSG_SYSTEM_ADMIN_DANGER_KILLALL",
    "chmod": "MSG_SYSTEM_ADMIN_DANGER_CHMOD",
    "chown": "MSG_SYSTEM_ADMIN_DANGER_CHOWN",
    "apt": "MSG_SYSTEM_ADMIN_DANGER_PACKAGES",
    "apt-get": "MSG_SYSTEM_ADMIN_DANGER_PACKAGES",
    "dpkg": "MSG_SYSTEM_ADMIN_DANGER_DPKG",
    "useradd": "MSG_SYSTEM_ADMIN_DANGER_USERADD",
    "userdel": "MSG_SYSTEM_ADMIN_DANGER_USERDEL",
}


_APPROVAL_TEMPLATE_FIELDS: dict[str, frozenset[str]] = {
    **{
        key: frozenset()
        for key in set(_DANGER_MESSAGE_KEY_BY_BINARY.values())
    },
    "MSG_SYSTEM_ADMIN_DANGER_EMPTY": frozenset(),
    "MSG_SYSTEM_ADMIN_DANGER_UNKNOWN_BINARY": frozenset({"binary"}),
    "MSG_SYSTEM_ADMIN_DANGER_NOTE_FORCE": frozenset(),
    "MSG_SYSTEM_ADMIN_DANGER_NOTE_RECURSIVE": frozenset(),
    "MSG_SYSTEM_ADMIN_DANGER_NOTE_ROOT": frozenset(),
    "MSG_SYSTEM_ADMIN_DANGER_NOTE_IRREVERSIBLE": frozenset(),
    "MSG_SYSTEM_ADMIN_DANGER_NOTE_DANGEROUS": frozenset(),
    "MSG_SYSTEM_ADMIN_APPROVAL_WARNING_HOST": frozenset({"danger_summary"}),
    "MSG_SYSTEM_ADMIN_APPROVAL_WARNING_GUEST": frozenset(
        {"command", "danger_summary"}
    ),
}
_APPROVAL_I18N_ERROR = "ERR_SYSTEM_ADMIN_APPROVAL_CATALOG_UNAVAILABLE"
_APPROVAL_CARD_ERROR = "ERR_ADMIN_APPROVAL_CARD_MISMATCH"
_CONSENT_STORE_ERROR = "ERR_ADMIN_CONSENT_STORE_UNAVAILABLE"
_ADMIN_APPROVAL_OPTIONS = (
    "approve_once", "approve_and_whitelist", "reject_once", "block_forever",
)
_GUEST_APPROVAL_OPTIONS = (
    "run_externally", "request_admin_whitelist", "reject_once",
)


class _ApprovalCatalogError(RuntimeError):
    """The complete approval-card locale is not ready and coherent."""


class _ConsentStoreUnavailable(RuntimeError):
    """The exact-argv capability store could not be read atomically."""


def _approval_options_for_actor(actor: str) -> tuple[str, ...]:
    """Return the closed choice set derived only from server-side identity."""

    return _ADMIN_APPROVAL_OPTIONS if _is_admin_actor(actor) else _GUEST_APPROVAL_OPTIONS


def _canonical_target_binary(
    argv: ValidatedArgv | list[str] | tuple[str, ...],
) -> str:
    """Use the safety canonicalizer as the privilege-wrapper boundary."""
    try:
        return _validated(argv).binary
    except ArgvValidationError:
        return ""


def _danger_message_keys(
    argv: ValidatedArgv | list[str] | tuple[str, ...], severity: str | None,
) -> list[str]:
    try:
        validated = _validated(argv)
    except ArgvValidationError:
        return ["MSG_SYSTEM_ADMIN_DANGER_EMPTY"]
    binary = validated.binary
    danger_key = _DANGER_MESSAGE_KEY_BY_BINARY.get(binary)
    if danger_key is None and binary.startswith("mkfs."):
        danger_key = "MSG_SYSTEM_ADMIN_DANGER_MKFS"
    keys = [danger_key or "MSG_SYSTEM_ADMIN_DANGER_UNKNOWN_BINARY"]
    flags = [token for token in validated.command_argv[1:]
             if token.startswith("-")]

    def _has_short(letter: str, flag: str) -> bool:
        return (flag.startswith("-") and not flag.startswith("--")
                and letter in flag[1:])

    if any(flag == "--force" or _has_short("f", flag) for flag in flags):
        keys.append("MSG_SYSTEM_ADMIN_DANGER_NOTE_FORCE")
    if any(flag == "--recursive" or _has_short("r", flag)
           or _has_short("R", flag) for flag in flags):
        keys.append("MSG_SYSTEM_ADMIN_DANGER_NOTE_RECURSIVE")
    if validated.requires_privilege:
        keys.append("MSG_SYSTEM_ADMIN_DANGER_NOTE_ROOT")
    if severity == "irreversible":
        keys.append("MSG_SYSTEM_ADMIN_DANGER_NOTE_IRREVERSIBLE")
    elif severity == "dangerous":
        keys.append("MSG_SYSTEM_ADMIN_DANGER_NOTE_DANGEROUS")
    return keys


def _template_fields(template: str) -> frozenset[str]:
    try:
        fields = set()
        for _, field, format_spec, conversion in Formatter().parse(template):
            if field is None:
                continue
            root = str(field).split(".", 1)[0].split("[", 1)[0]
            if field != root or format_spec or conversion:
                raise _ApprovalCatalogError("unsafe approval placeholder")
            fields.add(root)
    except ValueError as exc:
        raise _ApprovalCatalogError("malformed approval template") from exc
    return frozenset(fields)


def _approval_catalog_snapshot(keys: list[str]) -> dict[str, str]:
    """Read and validate one complete, ready-only locale in one SQL snapshot.

    An entirely unmaterialized instance locale may use the signed bootstrap
    English family.  A partially materialized, pending, stale or malformed
    locale must not create a hybrid authorization card.
    """
    wanted = tuple(dict.fromkeys(keys))
    active = _i18n.current_lang()
    bootstrap = _i18n.normalize_language(_i18n._C.BOOTSTRAP_LANGUAGE) or "en"
    languages = (active,) if active == bootstrap else (active, bootstrap)
    placeholders = ",".join("?" for _ in wanted)
    lang_placeholders = ",".join("?" for _ in languages)
    rows = _i18n._open().execute(
        "SELECT key,lang,text,needs_translation,source_lang,version_hash,"
        "source_text_hash FROM i18n WHERE key IN (" + placeholders + ") "
        "AND lang IN (" + lang_placeholders + ")",
        (*wanted, *languages),
    ).fetchall()
    by_lang = {
        lang: {row[0]: row for row in rows if row[1] == lang}
        for lang in languages
    }
    active_rows = by_lang[active]
    selected = active
    if active != bootstrap and not active_rows:
        selected = bootstrap
    elif len(active_rows) != len(wanted):
        raise _ApprovalCatalogError("partial approval locale")
    selected_rows = by_lang[selected]
    if len(selected_rows) != len(wanted):
        raise _ApprovalCatalogError("missing approval resources")

    bootstrap_rows = by_lang.get(bootstrap, {})
    if len(bootstrap_rows) != len(wanted):
        raise _ApprovalCatalogError("missing approval source resources")
    source_versions: dict[str, str] = {}
    for key in wanted:
        source_row = bootstrap_rows[key]
        source_text = source_row[2]
        source_version = (
            "sha256:" + hashlib.sha256(source_text.encode("utf-8")).hexdigest()
            if isinstance(source_text, str) else ""
        )
        if (not isinstance(source_text, str) or not source_text.strip()
                or int(source_row[3] or 0) != 0
                or source_row[4] not in (None, "")
                or source_row[6] not in (None, "")
                or source_row[5] != source_version):
            raise _ApprovalCatalogError("approval source is not ready")
        source_versions[key] = source_version
    snapshot: dict[str, str] = {}
    for key in wanted:
        row = selected_rows[key]
        text = row[2]
        if (not isinstance(text, str) or not text.strip()
                or int(row[3] or 0) != 0):
            raise _ApprovalCatalogError("approval resource is not ready")
        version = "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
        if row[5] != version:
            raise _ApprovalCatalogError("approval resource hash mismatch")
        if selected != bootstrap:
            if row[4] != bootstrap or row[6] != source_versions.get(key):
                raise _ApprovalCatalogError("approval source provenance mismatch")
        if _template_fields(text) != _APPROVAL_TEMPLATE_FIELDS[key]:
            raise _ApprovalCatalogError("approval placeholder mismatch")
        snapshot[key] = text
    return snapshot


def _render_danger_summary(argv: ValidatedArgv | list[str] | tuple[str, ...], severity: str | None,
                           snapshot: dict[str, str]) -> str:
    keys = _danger_message_keys(argv, severity)
    binary = _canonical_target_binary(argv) if argv else ""
    binary = render_argv_for_display([binary])
    base = snapshot[keys[0]].format(binary=binary)
    notes = [snapshot[key] for key in keys[1:]]
    return " ".join([base, *notes])


def _explain_command_dangers(argv: ValidatedArgv | list[str] | tuple[str, ...], severity: str | None) -> str:
    """Spiegazione testuale dei rischi del comando (1-2 frasi). Deterministica,
    basata sul binario + flags + severity dalla policy. Niente LLM.
    """
    keys = _danger_message_keys(argv, severity)
    snapshot = _approval_catalog_snapshot(keys)
    return _render_danger_summary(argv, severity, snapshot)


def _build_approval_card(argv: ValidatedArgv | list[str] | tuple[str, ...], sig, requires_sudo: bool,
                          rev_class: str, undo_hint: str | None,
                          intent_text: str, actor: str,
                          severity: str | None = None) -> dict:
    """Carta vaglio role-aware. Vedi modulo header per spec opzioni."""
    is_admin = _is_admin_actor(actor)
    try:
        validated = _validated(argv)
        rendered_argv = list(validated.argv)
    except ArgvValidationError:
        validated = None
        rendered_argv = list(argv) if not isinstance(argv, ValidatedArgv) else list(argv.argv)
    argv_display = render_argv_for_display(validated or rendered_argv)
    warning_key = (
        "MSG_SYSTEM_ADMIN_APPROVAL_WARNING_HOST" if is_admin
        else "MSG_SYSTEM_ADMIN_APPROVAL_WARNING_GUEST"
    )
    keys = [*_danger_message_keys(validated or rendered_argv, severity), warning_key]
    try:
        snapshot = _approval_catalog_snapshot(keys)
        danger_summary = _render_danger_summary(validated or rendered_argv, severity, snapshot)
    except (_ApprovalCatalogError, sqlite3.Error, OSError) as exc:
        log.error("approval card i18n denied: %s", exc)
        return {
            "type": "approval_card",
            "argv_rendered": argv_display,
            "signature": str(sig),
            "requires_sudo": requires_sudo,
            "reversibility": rev_class,
            "undo_hint": undo_hint,
            "intent_text": intent_text,
            "actor_role": "admin" if is_admin else "guest",
            "danger_summary": "",
            "warning": "",
            "options": [],
            "error_class": "dependency_unavailable",
            "error_code": _APPROVAL_I18N_ERROR,
        }
    if is_admin:
        options = list(_approval_options_for_actor(actor))
        warning = snapshot[warning_key].format(danger_summary=danger_summary)
    else:
        options = list(_approval_options_for_actor(actor))
        warning = snapshot[warning_key].format(
            command=argv_display, danger_summary=danger_summary,
        )
    return {
        "type": "approval_card",
        "argv_rendered": argv_display,
        "signature": str(sig),
        "requires_sudo": requires_sudo,
        "reversibility": rev_class,
        "undo_hint": undo_hint,
        "intent_text": intent_text,
        "actor_role": "admin" if is_admin else "guest",
        "danger_summary": danger_summary,
        "warning": warning,
        "options": options,
    }


def _enqueue_whitelist_request(*, signature: str, argv: list[str],
                                requester: str, intent_text: str) -> None:
    """Append una richiesta di whitelisting al queue file. L'admin la
    rivede via `metnos-cli admin whitelist-queue` o analogo (ADR pending).
    File JSONL append-only; niente race condition perche' append e'
    atomico per single-line POSIX (< PIPE_BUF=4096).
    """
    import datetime
    import json as _json
    _REQUEST_WHITELIST_QUEUE.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "requester": requester,
        "signature": signature,
        "argv": argv,
        "intent": intent_text,
        "status": "pending",
    }
    with open(_REQUEST_WHITELIST_QUEUE, "a", encoding="utf-8") as fh:
        fh.write(_json.dumps(entry, ensure_ascii=False) + "\n")


# ── Main flow ─────────────────────────────────────────────────────────

def _evaluate_validated_argv(
    validated: ValidatedArgv,
    *,
    audit: dict,
    intent_text: str,
    actor: str,
    emit_wait: Optional[Callable[[str], None]] = None,
) -> AdminDecision:
    """Run every authorization decision against one immutable argv view."""
    argv = list(validated.argv)
    sig = validated.signature
    audit["argv"] = argv
    audit["argv_json"] = validated.argv_json
    audit["signature"] = str(sig)
    audit["requires_sudo"] = validated.requires_privilege

    forbidden, forbidden_reason = _check_forbidden_argv(validated)
    if forbidden:
        audit["safety"] = "forbidden_hit"
        audit["safety_reason"] = forbidden_reason
        audit["error_code"] = "ERR_PERMISSION_DENIED"
        return AdminDecision(
            kind="reject", argv=argv, signature=str(sig),
            reason=_msg(
                "MSG_SYSTEM_ADMIN_FORBIDDEN",
                reason=_localized_forbidden_reason(validated),
            ),
            audit=audit, validated_argv=validated,
        )

    store = SafetyStore()
    try:
        from safety.canonicalize import signature_matches
        for kind_tag in ("blacklist", "forbidden"):
            for row in store.find_by_kind(kind_tag):
                if signature_matches(sig, row.signature):
                    audit["safety"] = "blacklist_hit"
                    audit["matched_pattern"] = row.signature
                    audit["safety_reason"] = row.reason
                    audit["error_code"] = "ERR_PERMISSION_DENIED"
                    return AdminDecision(
                        kind="reject", argv=argv, signature=str(sig),
                        reason=_msg("MSG_SYSTEM_ADMIN_POLICY_BLOCKED"),
                        severity=row.severity, audit=audit,
                        validated_argv=validated,
                    )

        whitelisted_row = None
        whitelisted_kind = None
        for kind_tag in ("whitelist", "graylist"):
            for row in store.find_by_kind(kind_tag):
                if signature_matches(sig, row.signature):
                    whitelisted_row = row
                    whitelisted_kind = kind_tag
                    break
            if whitelisted_row:
                break

        rev_class, undo_hint = _classify_reversibility(sig)
        if whitelisted_row is not None and _is_admin_actor(actor):
            new_uses = store.record_use(whitelisted_row.signature)
            age_class = (
                "permanent" if whitelisted_kind == "whitelist" else "graylist"
            )
            audit["safety"] = "whitelist_hit"
            audit["age_class"] = age_class
            audit["matched_pattern"] = whitelisted_row.signature
            audit["uses"] = new_uses
            return AdminDecision(
                kind="execute_silent", argv=argv, signature=str(sig),
                age_class=age_class, severity=whitelisted_row.severity,
                requires_sudo=validated.requires_privilege,
                reversibility=rev_class, undo_hint=undo_hint, audit=audit,
                validated_argv=validated,
            )

        if whitelisted_row is not None:
            audit["guest_policy"] = "card_required_no_execution"

        if emit_wait is not None:
            emit_wait(_msg(WAIT_HIGH))
        audit["safety"] = "unknown"
        severity = _severity_for_reversibility(rev_class)
        card = _build_approval_card(
            argv=validated, sig=sig,
            requires_sudo=validated.requires_privilege,
            rev_class=rev_class, undo_hint=undo_hint,
            intent_text=intent_text, actor=actor, severity=severity,
        )
        return AdminDecision(
            kind="ask_user", argv=argv, signature=str(sig), severity=severity,
            requires_sudo=validated.requires_privilege,
            reversibility=rev_class, undo_hint=undo_hint,
            card_payload=card, audit=audit, validated_argv=validated,
        )
    finally:
        store.close()

def decide(
    user_text: str,
    *,
    actor: str = "host",
    emit_wait: Optional[Callable[[str], None]] = None,
    llm_call: Optional[Callable[[str], str]] = None,
) -> AdminDecision:
    """Run the four-act flow on the user utterance.

    Args:
      user_text: raw natural-language utterance from the user.
      actor:     'host' or 'guest_<id>' (ADR 0035).
      emit_wait: callable that receives one of WAIT_LOW/MEDIUM/HIGH and
                 sends it on the user's channel; if None, no wait prompts
                 are emitted (useful for testing).
      llm_call:  callable(prompt: str) -> str, returning the LLM's JSON
                 answer; if None, uses the default fast-tier router.

    Returns: AdminDecision describing what to do next.
    """
    if emit_wait is None:
        emit_wait = lambda _msg: None  # noqa: E731
    if llm_call is None:
        llm_call = _default_llm_call

    audit: dict = {"actor": actor, "user_text": user_text}

    # Act [pre-1]: syntactic gate
    literal_reason = _looks_like_literal_shell(user_text)
    if literal_reason:
        audit["gate"] = "literal_command_rejected"
        audit["gate_reason"] = literal_reason
        return AdminDecision(
            kind="reject",
            reason=_msg("MSG_SYSTEM_ADMIN_REJECT_LITERAL"),
            audit=audit,
        )

    emit_wait(_msg(WAIT_LOW))

    # Act [1+2+3]: single LLM call (intent → argv translation)
    try:
        raw = llm_call(LLM_PROMPT_TEMPLATE.format(user_text=user_text))
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, AttributeError) as e:
        audit["llm_error"] = str(e)
        return AdminDecision(
            kind="reject",
            reason=_msg("MSG_SYSTEM_ADMIN_REFORMULATE"),
            audit=audit,
        )

    kind = data.get("kind")
    if kind == "literal_command":
        audit["llm_kind"] = "literal_command"
        audit["llm_reason"] = data.get("reason")
        return AdminDecision(
            kind="reject",
            reason=_msg("MSG_SYSTEM_ADMIN_REJECT_LITERAL"),
            audit=audit,
        )
    if kind in ("unknown", "impossible"):
        audit["llm_kind"] = kind
        audit["llm_reason"] = data.get("reason")
        return AdminDecision(
            kind="reject",
            reason=_msg(
                "MSG_SYSTEM_ADMIN_UNKNOWN" if kind == "unknown"
                else "MSG_SYSTEM_ADMIN_IMPOSSIBLE"
            ),
            audit=audit,
        )
    if kind != "translated":
        audit["llm_kind"] = "malformed"
        return AdminDecision(
            kind="reject",
            reason=_msg("MSG_SYSTEM_ADMIN_REFORMULATE"),
            audit=audit,
        )

    argv = data.get("argv")
    if not isinstance(argv, list) or not argv or not all(
        isinstance(a, str) for a in argv
    ):
        audit["llm_kind"] = "malformed_argv"
        return AdminDecision(
            kind="reject",
            reason=_msg("MSG_SYSTEM_ADMIN_INVALID_TRANSLATION"),
            audit=audit,
        )

    try:
        validated = validate_argv(argv)
    except ArgvValidationError as exc:
        audit.update({
            "argv": argv, "gate": "argv_validation_rejected",
            "error_code": exc.code, "gate_reason": exc.detail,
        })
        return AdminDecision(
            kind="reject", argv=argv,
            reason=_msg("MSG_SYSTEM_ADMIN_POLICY_BLOCKED"), audit=audit,
        )
    return _evaluate_validated_argv(
        validated, audit=audit, intent_text=user_text, actor=actor,
        emit_wait=emit_wait,
    )


def apply_user_decision(
    *,
    decision: AdminDecision,
    user_choice: str,
    actor: str = "host",
) -> AdminDecision:
    """Apply the user's reply to an `ask_user` decision.

    Opzioni admin (actor=='host'):
      - approve_once / approve: execute this exact argv once; no broad rule.
      - approve_and_whitelist:  insert in whitelist permanente, execute.
      - reject_once:            reject senza side effect.
      - block_forever:          insert in blacklist, reject.

    Opzioni guest (actor!='host'):
      - run_externally:          drop request, no execution (utente lancia
                                  manualmente fuori da Metnos).
      - request_admin_whitelist: append richiesta a queue file; admin
                                  rivede e decide. Niente execute.
      - reject_once:             drop request senza side effect.
    """
    if decision.kind != "ask_user":
        raise ValueError("apply_user_decision called on non-ask decision")
    sig = decision.signature
    audit = dict(decision.audit)
    audit["user_choice"] = user_choice
    audit["actor_decided"] = actor

    # A caller cannot bypass a failed localization snapshot by replaying or
    # fabricating an approval choice.  No safety-store write is reachable.
    if (decision.card_payload or {}).get("error_code"):
        audit["approval_denied"] = "catalog_unavailable"
        audit["error_code"] = _APPROVAL_I18N_ERROR
        return AdminDecision(
            kind="reject", argv=decision.argv, signature=sig,
            reason="", severity=decision.severity, audit=audit,
            validated_argv=decision.validated_argv,
        )

    is_admin = _is_admin_actor(actor)
    card = decision.card_payload or {}
    expected_role = "admin" if is_admin else "guest"
    expected_options = list(_approval_options_for_actor(actor))
    if (card.get("actor_role") != expected_role
            or card.get("options") != expected_options):
        audit.update({
            "approval_denied": "card_identity_mismatch",
            "error_code": _APPROVAL_CARD_ERROR,
        })
        return AdminDecision(
            kind="reject", argv=decision.argv, signature=sig,
            reason=_msg("MSG_SYSTEM_ADMIN_POLICY_BLOCKED"),
            severity=decision.severity, audit=audit,
            validated_argv=decision.validated_argv,
        )
    normalized_choice = {
        "approve": "approve_once", "reject": "reject_once",
    }.get(user_choice, user_choice)
    allowed = set(expected_options)
    if normalized_choice not in allowed:
        raise ValueError(f"choice {user_choice!r} is not allowed for {expected_role}")
    user_choice = normalized_choice

    # Opzioni indipendenti da ruolo: reject senza side effect.
    if user_choice in ("reject_once", "reject"):
        return AdminDecision(
            kind="reject",
            argv=decision.argv, signature=sig,
            reason=_msg("MSG_SYSTEM_ADMIN_REJECT_ONCE"),
            audit=audit, validated_argv=decision.validated_argv,
        )

    # Opzioni guest-only (niente safety store mutation).
    if user_choice == "run_externally":
        return AdminDecision(
            kind="reject",
            argv=decision.argv, signature=sig,
            reason=_msg("MSG_SYSTEM_ADMIN_RUN_EXTERNALLY"),
            audit=audit, validated_argv=decision.validated_argv,
        )
    if user_choice == "request_admin_whitelist":
        _enqueue_whitelist_request(
            signature=sig, argv=decision.argv,
            requester=actor,
            intent_text=audit.get("user_text") or "",
        )
        return AdminDecision(
            kind="reject",
            argv=decision.argv, signature=sig,
            reason=_msg("MSG_SYSTEM_ADMIN_WHITELIST_REQUESTED"),
            audit=audit, validated_argv=decision.validated_argv,
        )

    validated_for_execution: ValidatedArgv | None = None
    if user_choice in {"approve_once", "approve_and_whitelist"}:
        try:
            validated_for_execution = (
                decision.validated_argv or validate_argv(decision.argv)
            )
        except ArgvValidationError as exc:
            audit.update({"gate": "argv_validation_rejected", "error_code": exc.code})
            return AdminDecision(
                kind="reject", argv=decision.argv, signature=sig,
                reason=_msg("MSG_SYSTEM_ADMIN_POLICY_BLOCKED"), audit=audit,
            )
        if (str(validated_for_execution.signature) != sig
                or list(validated_for_execution.argv) != decision.argv):
            audit.update({"gate": "approval_snapshot_mismatch",
                          "error_code": "ERR_ADMIN_APPROVAL_SNAPSHOT_MISMATCH"})
            return AdminDecision(
                kind="reject", argv=decision.argv, signature=sig,
                reason=_msg("MSG_SYSTEM_ADMIN_POLICY_BLOCKED"), audit=audit,
            )
        forbidden, forbidden_reason = _check_forbidden_argv(validated_for_execution)
        if forbidden:
            audit.update({
                "safety": "forbidden_hit", "safety_reason": forbidden_reason,
                "error_code": "ERR_PERMISSION_DENIED",
            })
            return AdminDecision(
                kind="reject", argv=decision.argv, signature=sig,
                reason=_msg("MSG_SYSTEM_ADMIN_POLICY_BLOCKED"), audit=audit,
                validated_argv=validated_for_execution,
            )

    if user_choice == "approve_once":
        audit["approved_once"] = True
        return AdminDecision(
            kind="execute_silent", argv=list(validated_for_execution.argv), signature=sig,
            age_class="one_shot", severity=decision.severity,
            requires_sudo=decision.requires_sudo,
            reversibility=decision.reversibility, undo_hint=decision.undo_hint,
            audit=audit, validated_argv=validated_for_execution,
        )

    store = SafetyStore()
    try:
        if user_choice == "block_forever":
            store.upsert_user(
                sig, "blacklist",
                severity="dangerous",
                reason="user blocked from approval card",
                created_by=actor,
            )
            return AdminDecision(
                kind="reject",
                argv=decision.argv, signature=sig,
                reason=_msg("MSG_SYSTEM_ADMIN_BLOCKED_FOREVER"),
                audit=audit, validated_argv=decision.validated_argv,
            )
        if user_choice == "approve_and_whitelist":
            store.upsert_user(
                sig, "whitelist",
                severity=decision.severity or "reversible",
                reason="admin promoted to permanent whitelist from approval card",
                created_by=actor,
            )
            new_uses = store.record_use(sig)
            audit["whitelist_uses"] = new_uses
            audit["promoted_to_whitelist"] = True
            return AdminDecision(
                kind="execute_silent",
                argv=decision.argv, signature=sig,
                age_class="permanent",
                severity=decision.severity,
                requires_sudo=decision.requires_sudo,
                reversibility=decision.reversibility,
                undo_hint=decision.undo_hint,
                audit=audit, validated_argv=validated_for_execution,
            )
        raise ValueError(f"unknown user_choice: {user_choice}")
    finally:
        store.close()


# ── Planner-facing invoke() — entrypoint per il PLANNER (ADR 0088) ────
#
# Quando il PLANNER chiama il tool `admin` con args {intent, command_proposed},
# il runtime instrada qui via `loader.invoke_verb_unique`. La funzione:
#   1. valuta `command_proposed` con il flow di decide() — gate, LLM
#      bypassed (input gia' argv concreto), safety lookup, approval card.
#   2. se `actor_consent_token` e' presente e valido (HMAC firmato dal
#      runtime al turno precedente), skippa la carta e procede a sudoer.
#   3. ritorna un dict piatto consumabile dal PLANNER, con campo
#      `approval_required` (bool), `approval_card` (dict), `signature`,
#      `decision`, `summary` per la final_answer.

import hmac
import time as _time

_CONSENT_LOCK = threading.Lock()

# Chiave HMAC per actor_consent_token. Persistente fra restart: scritta
# in `~/.local/share/metnos/.admin_consent_key` la prima volta, riusata
# sempre dopo. Niente sync fra nodi (carry-over).
def _consent_key() -> bytes:
    import config as _C  # §7.11
    key_path = _C.PATH_USER_DATA / ".admin_consent_key"
    key_path.parent.mkdir(parents=True, exist_ok=True)

    def _read_existing() -> bytes:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(key_path, flags)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise RuntimeError("admin consent key path is not a regular file")
            os.fchmod(fd, 0o600)
            key = os.read(fd, 33)
        finally:
            os.close(fd)
        if len(key) != 32:
            raise RuntimeError("invalid admin consent key")
        return key

    with _CONSENT_LOCK:
        if key_path.exists():
            return _read_existing()
        import secrets
        key = secrets.token_bytes(32)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(key_path, flags, 0o600)
        except FileExistsError:
            return _read_existing()
        with os.fdopen(fd, "wb") as stream:
            stream.write(key)
            stream.flush()
            os.fsync(stream.fileno())
        return key


def _consent_db_path() -> Path:
    return _C.PATH_USER_DATA / ".admin_consent.sqlite3"


def _open_consent_db() -> sqlite3.Connection:
    path = _consent_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        mode = os.lstat(path).st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise RuntimeError("admin consent database path is not a regular file")
    conn = sqlite3.connect(path, timeout=5, isolation_level=None)
    try:
        path.chmod(0o600)
    except OSError:
        conn.close()
        raise
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS consent_nonce("
        "nonce TEXT PRIMARY KEY, actor TEXT NOT NULL, argv_json TEXT NOT NULL,"
        "expires INTEGER NOT NULL, consumed_at INTEGER)"
    )
    return conn


def _sign_consent_token(
    argv: ValidatedArgv | list[str] | tuple[str, ...],
    actor: str,
    ttl_s: int = 600,
) -> str:
    """Issue a persistent, one-shot capability for one exact normalized argv."""
    import base64
    import secrets
    if not _is_admin_actor(actor):
        raise PermissionError("guest actors cannot receive admin consent tokens")
    validated = _validated(argv)
    if not isinstance(ttl_s, int) or ttl_s < 1 or ttl_s > 600:
        raise ValueError("consent ttl must be between 1 and 600 seconds")
    exp = int(_time.time()) + ttl_s
    nonce = secrets.token_urlsafe(24)
    payload_obj = {
        "actor": actor, "argv": list(validated.argv),
        "exp": exp, "nonce": nonce,
    }
    payload = json.dumps(
        payload_obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    mac = hmac.new(_consent_key(), payload, hashlib.sha256).digest()
    conn = _open_consent_db()
    try:
        conn.execute(
            "INSERT INTO consent_nonce(nonce,actor,argv_json,expires,consumed_at) "
            "VALUES(?,?,?,?,NULL)",
            (nonce, actor, validated.argv_json, exp),
        )
        conn.execute(
            "DELETE FROM consent_nonce WHERE expires < ?", (int(_time.time()) - 60,)
        )
    finally:
        conn.close()
    payload_b64 = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    mac_b64 = base64.urlsafe_b64encode(mac).decode("ascii").rstrip("=")
    return payload_b64 + "." + mac_b64


def _verify_consent_token(
    token: str,
    argv: ValidatedArgv | list[str] | tuple[str, ...],
    actor: str,
) -> bool:
    """Verify and atomically consume a one-shot exact-argv capability."""
    import base64
    if not _is_admin_actor(actor) or not token or token.count(".") != 1:
        return False
    try:
        validated = _validated(argv)
        payload_b64, mac_b64 = token.split(".", 1)
        payload = base64.urlsafe_b64decode(
            payload_b64 + "=" * (-len(payload_b64) % 4)
        )
        provided = base64.urlsafe_b64decode(
            mac_b64 + "=" * (-len(mac_b64) % 4)
        )
        data = json.loads(payload.decode("utf-8"))
        exp = int(data["exp"])
        nonce = data["nonce"]
    except (ValueError, TypeError, KeyError, UnicodeError, json.JSONDecodeError,
            ArgvValidationError):
        return False
    if (set(data) != {"actor", "argv", "exp", "nonce"}
            or data["actor"] != actor
            or data["argv"] != list(validated.argv)
            or not isinstance(nonce, str) or not nonce
            or exp < int(_time.time())):
        return False
    conn: sqlite3.Connection | None = None
    try:
        expected = hmac.new(_consent_key(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, provided):
            return False
        conn = _open_consent_db()
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            "UPDATE consent_nonce SET consumed_at=? WHERE nonce=? AND actor=? "
            "AND argv_json=? AND expires=? AND consumed_at IS NULL AND expires>=?",
            (int(_time.time()), nonce, actor, validated.argv_json, exp,
             int(_time.time())),
        )
        accepted = cursor.rowcount == 1
        conn.execute("COMMIT")
        return accepted
    except (sqlite3.Error, OSError, RuntimeError) as exc:
        if conn is not None:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
        raise _ConsentStoreUnavailable("admin consent store unavailable") from exc
    finally:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error as exc:
                raise _ConsentStoreUnavailable(
                    "admin consent store unavailable"
                ) from exc


def _decide_for_argv(argv: list[str], *, intent_text: str,
                     actor: str = "host",
                     _validated_snapshot: ValidatedArgv | None = None) -> AdminDecision:
    """Variante di decide() che salta lo stage LLM: l'argv arriva GIA'
    concreto dal PLANNER (campo `command_proposed`). Esegue solo gate +
    safety lookup + (eventuale) approval card.

    Coerente con la rimozione del traduttore NL→shell quando il PLANNER
    e' a monte e ha gia' fatto il lavoro di intento. Riusa _check_forbidden_argv
    e SafetyStore. Se il primo token ha shape "comando intero come stringa"
    (es. l'LLM ha emesso uno string singolo), splittiamo su whitespace.
    """
    if isinstance(argv, str):
        argv = argv.split()
    audit: dict = {
        "actor": actor,
        "user_text": intent_text,
        "source": "planner_argv",
    }

    # gate sintattico sul JOIN (intent_text NON e' command line)
    # PLANNER-path: sudo/doas/pkexec come wrapper sono legittimi (e necessari
    # per mount/systemctl/apt/...). Restano vietati gli altri shell-meta.
    rendered = " ".join(argv)
    literal_reason = _looks_like_literal_shell(rendered, allow_sudo_wrapper=True)
    if literal_reason:
        audit["gate"] = "literal_command_rejected"
        audit["gate_reason"] = literal_reason
        return AdminDecision(
            kind="reject",
            reason=_msg(
                "MSG_SYSTEM_ADMIN_INVALID_SHELL_META",
            ),
            audit=audit,
        )

    # validazione struttura argv
    if not argv or not all(isinstance(a, str) and a for a in argv):
        return AdminDecision(
            kind="reject",
            reason=_msg("MSG_SYSTEM_ADMIN_EMPTY_COMMAND"),
            audit=audit,
        )
    try:
        validated = _validated_snapshot or validate_argv(argv)
    except ArgvValidationError as exc:
        audit.update({
            "argv": list(argv), "gate": "argv_validation_rejected",
            "gate_reason": exc.detail, "error_code": exc.code,
        })
        return AdminDecision(
            kind="reject", argv=list(argv),
            reason=_msg("MSG_SYSTEM_ADMIN_POLICY_BLOCKED"), audit=audit,
        )
    return _evaluate_validated_argv(
        validated, audit=audit, intent_text=intent_text, actor=actor,
    )


def _detect_credentials_placeholder(argv: list[str]) -> tuple[str | None, dict]:
    """Strato 2 (ADR 0089): rileva placeholder `${METNOS_<KIND>_CREDS}` in argv
    e deriva il dominio canonico atteso (binding + host).

    Ritorna (domain, context):
      - domain: chiave di store attesa (es. "cifs_192.0.2.20"); None se
        non c'e' placeholder.
      - context: dict con binding/host/share quando derivabili dall'argv.
    """
    if not argv:
        return None, {}
    placeholder_re = re.compile(r"\$\{METNOS_([A-Z]+)_CREDS\}")
    binding = None
    for tok in argv:
        if not isinstance(tok, str):
            continue
        m = placeholder_re.search(tok)
        if m:
            binding = m.group(1).lower()  # "cifs", "web", "ssh", ...
            break
    if binding is None:
        return None, {}
    # Deriva host dal argv: per CIFS, cerca //host/share; per altri, primo
    # token con shape host-like.
    host = ""
    share = ""
    if binding == "cifs":
        for tok in argv:
            if not isinstance(tok, str):
                continue
            mm = re.match(r"//([^/]+)/(.+)", tok)
            if mm:
                host = mm.group(1).lower()
                share = mm.group(2)
                break
    if not host:
        # Fallback: cerca un token che assomigli a un host (FQDN o IP).
        host_re = re.compile(
            r"^((?:\d{1,3}\.){3}\d{1,3}|(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})$"
        )
        for tok in argv:
            if isinstance(tok, str) and host_re.match(tok):
                host = tok.lower()
                break
    domain = f"{binding}_{host}" if host else f"{binding}_unknown"
    ctx = {"binding": binding, "host": host}
    if share:
        ctx["share"] = share
    return domain, ctx


def _format_credentials_required(domain: str, ctx: dict) -> str:
    """FALLBACK (ADR 0091, 5/5/2026): testo plain-text «mi servono credenziali».

    Dal 5/5/2026 il path principale per raccogliere credenziali e' l'auto-
    orchestrazione `get_inputs(fmt='auto')` via `on_complete` callback (ADR
    0091). Questa funzione resta come FALLBACK quando l'orchestratore non
    riesce a invocare get_inputs (es. modulo dialog_pending mancante,
    storage non scrivibile). NON e' piu' il path UX principale.

    Pattern dialog manager (CLAUDE.md §10.6, project_dialog_manager_authorization_ux):
    riga 1 = «cosa serve», riga 2-3 = parametri tecnici, riga 4 = come fornirle,
    riga 5 = alternative.
    """
    binding = ctx.get("binding", "?")
    host = ctx.get("host", "?")
    share = ctx.get("share", "")
    share_line = f"\n  share:   {share}" if share else ""
    return _msg(
        "MSG_SYSTEM_ADMIN_CREDENTIALS_REQUIRED",
        domain=domain, binding=binding, host=host, share_line=share_line,
    )


def _format_cli_instructions(domain: str, ctx: dict) -> str:
    """Strato 3 (ADR 0089) — fallback CLI: istruzioni per inserire le credenziali
    via `metnos-cli credentials add` da un terminale.

    Resta valida indipendentemente da ADR 0091: lo Strato 3 e' la via per
    chi non vuole digitare credenziali in chat ne' compilare il form HTTP.
    Viene esposta sia dal fallback `_format_credentials_required` sia dal
    completion screen del form HTTP.
    """
    binding = ctx.get("binding", "")
    host = ctx.get("host", "")
    extra = ""
    if binding:
        extra += f" --binding {binding}"
    if host:
        extra += f" --host {host}"
    return _msg(
        "MSG_SYSTEM_ADMIN_CREDENTIALS_CLI", domain=domain, extra=extra,
    )


# ── Catalog-name guard helper (12/5/2026) ─────────────────────────────
#
# Bug 1f82a766 (11/5/2026): PLANNER ha invocato `admin(command_proposed=
# "get_now", ...)`. admin ha passato l'argv a sudoer → subprocess.run(
# ["get_now"]) → FileNotFoundError. Causa: admin trattava qualunque token
# come potenziale binario. Difesa in profondita': se argv[0] (saltando
# wrapper sudo/doas/pkexec) e' un executor del catalogo, e' un instradamento
# errato del PLANNER, non un comando shell. Rejection chirurgica con
# messaggio che indica il fix all'LLM (la carta vaglio sarebbe inutile).

def _executor_name_in_argv(
    argv: ValidatedArgv | list[str] | tuple[str, ...],
) -> Optional[str]:
    """Ritorna il nome dell'executor se argv[0] (saltando sudo/doas/pkexec)
    matcha un executor presente nel catalogo runtime, altrimenti None.

    Lookup deterministico O(N) sul catalogo importato lazy: rispetta i
    rejected (synth scartati per affinity overlap / signature drift / GC).
    Caching minimo per evitare reflection ripetuta nello stesso processo.
    """
    try:
        command = _validated(argv).command_argv
    except ArgvValidationError:
        return None
    candidate = Path(command[0]).name
    if not candidate or "/" in candidate:
        return None
    # Lookup catalog via loader (lazy import + cache interna ADR 0099)
    try:
        from loader import load_catalog  # type: ignore
        catalog = load_catalog()
    except (ImportError, AttributeError, RuntimeError):
        return None
    executor = catalog.get(candidate)
    if executor is None:
        return None
    # Verb_unique builtins (admin, sudoer) non sono "executor del catalogo"
    # nel senso utile per questo guard: argv[0]=admin sarebbe ricorsivo e
    # admin shell-literal e' gia' bloccato dal gate sintattico esistente.
    if candidate in ("admin", "sudoer"):
        return None
    return candidate


def _invoke_impl(*, intent: str, command_proposed: str,
                 credentials_domain: str | None = None,
                 actor_consent_token: str | None = None,
                 actor: str = "host",
                 **_extra) -> dict:
    """Entrypoint per il PLANNER (ADR 0088).

    Questo e' il punto di ingresso registrato nel `VERB_UNIQUE_REGISTRY`
    quando il loader chiama `boot_register_verb_unique_builtins()`. Il
    runtime `agent_runtime` invoca via
    `loader.invoke_verb_unique("admin", caller="agent_runtime",
        intent=..., command_proposed=..., actor=...)`.

    Ritorna un dict piatto consumabile dal PLANNER:
      ok: bool
      decision: 'approval_required' | 'execute_silent' | 'reject' |
                'needs_inputs' (ADR 0091; admin chiede al runtime di
                 orchestrare un get_inputs per raccogliere credenziali
                 mancanti e ri-invocare con args originali).
      signature: str
      argv: list[str]
      approval_required: bool
      approval_card: dict | None
      consent_token: str | None    # quando approval_required, il runtime
                                    #  lo passa nel cap_pending; al rilancio
                                    #  l'utente non vede il token.
      needs_inputs: dict | None    # quando decision='needs_inputs', payload
                                    #  con title/description/dialog/fmt/on_complete
                                    #  per l'orchestratore runtime (ADR 0091).
      summary: str   # 1-2 frasi user-facing per final_answer.
    """
    audit_actor = actor or "host"
    argv = (command_proposed or "").split() if isinstance(command_proposed, str) else []

    # ── Placeholder guard §7.3 (24/5/2026): se `command_proposed` (o
    # `intent`) contiene placeholder letterali `<name>` non risolti, il
    # PLANNER ha ricevuto una query con segnaposto dell'utente (es.
    # «mount //<ip>/share») e ha propagato i placeholder nei suoi args
    # invece di chiederli. Eseguire produrrebbe "DNS resolution failed"
    # o simili; il LLM classifier interno ritornerebbe `kind=unknown`
    # ciclico (bug iter 4/5: «monta share \\<ip>\Public» → 2× admin
    # unknown → loop_break con messaggio criptico). Reject deterministico
    # §7.9 con summary specifico: il PLANNER al prossimo step emette
    # final_answer onesto chiedendo i valori reali — niente loop.
    import re as _re_ph
    _placeholder_re = _re_ph.compile(r"<([a-zA-Z_][a-zA-Z0-9_-]*)>")
    _cp_text = command_proposed if isinstance(command_proposed, str) else ""
    _in_text = intent if isinstance(intent, str) else ""
    _placeholders = sorted(set(
        _placeholder_re.findall(_cp_text)
        + _placeholder_re.findall(_in_text)
    ))
    if _placeholders:
        _ph_list = ", ".join("`<" + p + ">`" for p in _placeholders)
        return {
            "ok": False,
            "decision": "reject",
            "signature": "",
            "argv": argv,
            "approval_required": False,
            "approval_card": None,
            "summary": _msg(
                "MSG_SYSTEM_ADMIN_UNRESOLVED_PLACEHOLDERS",
                placeholders=_ph_list,
            ),
            "error_class": "unresolved_placeholders",
            "audit": {
                "actor": audit_actor,
                "user_text": intent or "",
                "source": "planner_argv",
                "argv": argv,
                "gate": "placeholder_rejected",
                "placeholders": _placeholders,
                "command_proposed": command_proposed,
            },
        }

    try:
        validated = validate_argv(argv)
    except ArgvValidationError as exc:
        return {
            "ok": False, "decision": "reject", "signature": "",
            "argv": argv, "approval_required": False, "approval_card": None,
            "summary": _msg("MSG_SYSTEM_ADMIN_POLICY_BLOCKED"),
            "error_class": "invalid_args", "error_code": exc.code,
            "audit": {
                "actor": audit_actor, "user_text": intent or "",
                "source": "planner_argv", "argv": argv,
                "gate": "argv_validation_rejected",
                "gate_reason": exc.detail, "error_code": exc.code,
            },
        }
    argv = list(validated.argv)

    if actor_consent_token and not _is_admin_actor(audit_actor):
        return {
            "ok": False, "decision": "reject", "signature": str(validated.signature),
            "argv": argv, "approval_required": False, "approval_card": None,
            "summary": _msg("MSG_SYSTEM_ADMIN_POLICY_BLOCKED"),
            "error_class": "permission_denied",
            "error_code": "ERR_ADMIN_GUEST_CONSENT_DENIED",
            "audit": {
                "actor": audit_actor, "user_text": intent or "",
                "source": "planner_argv", "argv": argv,
                "gate": "guest_consent_rejected",
                "error_code": "ERR_ADMIN_GUEST_CONSENT_DENIED",
            },
        }

    # ── Catalog-name guard (12/5/2026): se `argv[0]` (saltando i wrapper
    # sudo/doas/pkexec) coincide con il nome di un executor del catalog,
    # il PLANNER ha sbagliato strada: admin e' per UN comando shell
    # privilegiato; gli executor del catalog (get_now, set_events, ...)
    # si invocano direttamente come tool ordinari. subprocess.run(["get_now"])
    # produrrebbe FileNotFoundError perche' non c'e' nessun binario "get_now"
    # nel PATH. Rifiutiamo qui con messaggio chiaro, evitando di emettere
    # una carta vaglio inutile (l'utente non puo' "approvare" qualcosa che
    # non puo' funzionare). Determinismo §7.9.
    catalog_hit = _executor_name_in_argv(validated)
    if catalog_hit is not None:
        return {
            "ok": False,
            "decision": "reject",
            "signature": "",
            "argv": argv,
            "approval_required": False,
            "approval_card": None,
            "summary": _msg(
                "MSG_SYSTEM_ADMIN_EXECUTOR_AS_COMMAND", executor=catalog_hit,
            ),
            "audit": {
                "actor": audit_actor,
                "user_text": intent or "",
                "source": "planner_argv",
                "argv": argv,
                "gate": "catalog_name_rejected",
                "catalog_name": catalog_hit,
            },
        }

    # ── Pkg whitelist guard (17/5/2026, install-on-demand pattern §7.3):
    # se il comando e' `[sudo] apt[-get] install [-y] <pkg>`, verifica che
    # <pkg> sia in `system_binaries.installable_packages_whitelist()`. Reject
    # se non in lista. Defense in depth: anche se PLANNER + utente approvano
    # via card HMAC, NON installiamo pkg non whitelisted (protezione contro
    # pkg-injection via prompt). Whitelist auto-deriva da _BINARY_TO_PACKAGE
    # + override `~/.config/metnos/installable_packages.json`.
    try:
        import sys as _sys
        from pathlib import Path as _Path
        _rt = str(_Path(__file__).resolve().parent.parent)
        if _rt not in _sys.path:
            _sys.path.insert(0, _rt)
        from system_binaries import (
            parse_apt_install_pkg as _parse_apt_pkg,
            is_package_installable as _is_pkg_ok,
            installable_packages_whitelist as _wl,
        )
        _pkg = _parse_apt_pkg(command_proposed or "")
        if _pkg is not None and not _is_pkg_ok(_pkg):
            _allowed = sorted(_wl())
            return {
                "ok": False,
                "decision": "reject",
                "signature": "",
                "argv": argv,
                "approval_required": False,
                "approval_card": None,
                "summary": _msg(
                    "MSG_SYSTEM_ADMIN_PACKAGE_NOT_ALLOWED",
                    package=_pkg,
                    allowed=(", ".join(_allowed[:8])
                             + (" ..." if len(_allowed) > 8 else "")),
                ),
                "audit": {
                    "actor": audit_actor,
                    "user_text": intent or "",
                    "source": "planner_argv",
                    "argv": argv,
                    "gate": "pkg_not_whitelisted",
                    "package": _pkg,
                },
            }
    except ImportError:
        # system_binaries non disponibile (test stand-alone): salta guard
        pass

    # ── ADR 0091 (5/5/2026): se il command_proposed contiene un placeholder
    # ${METNOS_<KIND>_CREDS} ma il dominio NON e' ancora salvato, NON emettere
    # piu' una stringa testuale "credentials_required" (vecchio Strato 2 ad-hoc).
    # Emetti `decision="needs_inputs"` con payload strutturato: title +
    # description + dialog (2 step canonici user/pwd) + on_complete callback
    # che salva le credenziali e ri-invoca admin con gli args originali.
    # Il runtime (agent_runtime) auto-orchestra `get_inputs(fmt="auto")`
    # senza delegare al PLANNER: piu' affidabile con LLM medium e niente
    # round-trip in piu'.
    derived_domain, derived_ctx = _detect_credentials_placeholder(argv)
    if derived_domain is not None:
        try:
            import sys as _s
            from pathlib import Path as _P
            _s.path.insert(0, str(_P(__file__).parent.parent))
            import credentials as _cred
            known = set(_cred.list_domains())
        except ImportError:
            known = set()
        # Use credentials_domain user-supplied first, fall back to derived.
        target_domain = credentials_domain or derived_domain
        if target_domain not in known:
            binding = derived_ctx.get("binding", "?")
            host = derived_ctx.get("host", "?")
            share = derived_ctx.get("share", "")
            payload = {
                "title": _msg(
                    "MSG_SYSTEM_ADMIN_CREDENTIALS_TITLE", domain=target_domain,
                ),
                "description": _msg(
                    "MSG_SYSTEM_ADMIN_CREDENTIALS_DESCRIPTION",
                    binding=binding, host=host,
                    share=(f" · share {share}" if share else ""),
                ),
                "dialog": [
                    {"var": "username", "prompt": _msg(
                        "MSG_SYSTEM_ADMIN_CREDENTIALS_USERNAME_PROMPT"),
                     "schema": {"kind": "text"}},
                    {"var": "password", "prompt": _msg(
                        "MSG_SYSTEM_ADMIN_CREDENTIALS_PASSWORD_PROMPT"),
                     "schema": {"kind": "credentials", "secret": True}},
                ],
                "fmt": "auto",
                "on_complete": {
                    "type": "save_credentials_and_resume",
                    "credentials_domain": target_domain,
                    "credentials_context": derived_ctx,
                    "resume_call": "admin",
                    "resume_args": {
                        "intent": intent or "",
                        "command_proposed": command_proposed or "",
                        "credentials_domain": target_domain,
                        # actor_consent_token NON va qui: l'orchestratore
                        # NON simula consent. Al resume, admin emette una
                        # carta vaglio standard (signature mount.cifs:
                        # graylist seeded), e l'utente conferma con "sì".
                    },
                },
            }
            return {
                "ok": True,
                "decision": "needs_inputs",
                "signature": "",
                "argv": argv,
                "approval_required": False,
                "approval_card": None,
                "needs_inputs": payload,
                # Campi legacy mantenuti come metadati di servizio (i test
                # del flow E2E e l'orchestratore li usano per build-up degli
                # audit / eventuali fallback). NIENTE summary plain text in
                # path principale: il runtime genera la carta UI da
                # get_inputs.final_message_hint.
                "credentials_domain": target_domain,
                "credentials_context": derived_ctx,
                "summary": "",
                "audit": {
                    "actor": audit_actor,
                    "user_text": intent or "",
                    "needs_inputs": True,
                    "domain": target_domain,
                    "context": derived_ctx,
                },
            }

    # Valuta argv (gate + safety) — niente LLM, l'argv arriva gia' concreto.
    decision = _decide_for_argv(
        argv, intent_text=intent or "", actor=audit_actor,
        _validated_snapshot=validated,
    )

    # Caso A: signature gia' whitelisted/graylisted → execute via sudoer
    # subito (niente carta). Coerente con il flow originale di admin.
    if decision.kind == "execute_silent":
        return _spawn_via_sudoer(
            decision=decision, intent_text=intent or "",
            actor=audit_actor,
        )

    # Caso B: reject (gate, forbidden, blacklist) → niente carta, esito finale.
    if decision.kind == "reject":
        return {
            "ok": False,
            "decision": "reject",
            "signature": decision.signature,
            "argv": decision.argv,
            "approval_required": False,
            "approval_card": None,
            "summary": decision.reason,
            "audit": decision.audit,
        }

    # Caso C: ask_user. Se l'utente ha gia' approvato al turno precedente
    # e il runtime ha rinjettato il consent_token, validiamo e procediamo.
    if decision.kind == "ask_user":
        card_error = (decision.card_payload or {}).get("error_code")
        if card_error:
            return {
                "ok": False,
                "decision": "reject",
                "signature": decision.signature,
                "argv": decision.argv,
                "approval_required": False,
                "approval_card": decision.card_payload,
                "consent_token": None,
                "summary": "",
                "error_class": "dependency_unavailable",
                "error_code": card_error,
                "audit": decision.audit,
            }

        def _consent_store_failure(exc: BaseException) -> dict:
            log.error("admin consent store denied approval: %s", exc)
            return {
                "ok": False,
                "decision": "reject",
                "signature": decision.signature,
                "argv": decision.argv,
                "approval_required": False,
                "approval_card": decision.card_payload,
                "consent_token": None,
                "summary": "",
                "error_class": "dependency_unavailable",
                "error_code": _CONSENT_STORE_ERROR,
                "audit": {
                    **decision.audit,
                    "gate": "consent_store_unavailable",
                    "error_code": _CONSENT_STORE_ERROR,
                },
            }

        try:
            consent_accepted = bool(
                actor_consent_token
                and _verify_consent_token(
                    actor_consent_token, validated, audit_actor,
                )
            )
        except _ConsentStoreUnavailable as exc:
            return _consent_store_failure(exc)

        if consent_accepted:
            promoted = apply_user_decision(
                decision=decision, user_choice="approve_once", actor=audit_actor,
            )
            if promoted.kind == "execute_silent":
                return _spawn_via_sudoer(
                    decision=promoted, intent_text=intent or "",
                    actor=audit_actor,
                )
            # fallback: caso degenere, non dovrebbe capitare
            return {
                "ok": False, "decision": "reject",
                "signature": promoted.signature, "argv": promoted.argv,
                "approval_required": False, "approval_card": None,
                "summary": _msg("MSG_SYSTEM_ADMIN_CONSENT_PROMOTION_FAILED"),
                "audit": promoted.audit,
            }

        # Niente token valido → esponi carta + emetti consent_token nel campo
        # response. Il runtime lo metterà in cap_pending; l'utente non lo
        # vede direttamente.
        try:
            token = (
                _sign_consent_token(validated, audit_actor)
                if _is_admin_actor(audit_actor) else None
            )
        except (OSError, sqlite3.Error, RuntimeError) as exc:
            return _consent_store_failure(exc)
        return {
            "ok": True,
            "decision": "approval_required",
            "signature": decision.signature,
            "argv": decision.argv,
            "approval_required": True,
            "approval_card": decision.card_payload,
            "consent_token": token,
            "summary": _format_card_summary(
                decision, intent_text=intent or "",
            ),
            "audit": decision.audit,
        }

    # Sicurezza: kind non gestito
    return {
        "ok": False, "decision": "reject",
        "signature": decision.signature, "argv": decision.argv,
        "approval_required": False, "approval_card": None,
        "summary": _msg(
            "MSG_SYSTEM_ADMIN_UNEXPECTED_STATE", kind=decision.kind,
        ),
        "audit": decision.audit,
    }


def _standardize_result(result: object) -> dict:
    """Apply the Executor Standard terminal envelope to every admin branch."""
    if not isinstance(result, dict):
        return {
            "ok": False,
            "decision": "reject",
            "approval_required": False,
            "summary": _msg("MSG_SYSTEM_ADMIN_INVALID_INTERNAL_RESULT"),
            "error_class": "internal_error",
            "error_code": "ERR_BUILTIN_INVALID_RESULT",
        }
    out = dict(result)
    if out.get("ok") is not False:
        return out
    audit = out.get("audit") if isinstance(out.get("audit"), dict) else {}
    gate = str(audit.get("gate") or "")
    safety = str(audit.get("safety") or "")
    error_class = str(out.get("error_class") or "").strip()
    explicit_code = str(out.get("error_code") or audit.get("error_code") or "").strip()
    if error_class == "unresolved_placeholders" or gate == "placeholder_rejected":
        error_class = "invalid_args"
        error_code = "ERR_ARG_UNRESOLVED_PLACEHOLDER"
    elif gate == "catalog_name_rejected":
        error_class = "invalid_args"
        error_code = "ERR_ARG_EXECUTOR_AS_COMMAND"
    elif gate == "argv_validation_rejected":
        error_class = "invalid_args"
        error_code = explicit_code or "ERR_ARG_INVALID"
    elif gate == "guest_consent_rejected":
        error_class = "permission_denied"
        error_code = explicit_code or "ERR_ADMIN_GUEST_CONSENT_DENIED"
    elif gate == "pkg_not_whitelisted" or safety in {
            "forbidden_hit", "blacklist_hit"}:
        error_class = "permission_denied"
        error_code = "ERR_PERMISSION_DENIED"
    else:
        error_class = error_class or "invalid_args"
        error_code = explicit_code or "ERR_ARG_INVALID"
    out["error_class"] = error_class
    out.setdefault("error_code", error_code)
    return out


def invoke(*, intent: str, command_proposed: str,
           credentials_domain: str | None = None,
           actor_consent_token: str | None = None,
           actor: str = "host",
           **_extra) -> dict:
    return _standardize_result(_invoke_impl(
        intent=intent,
        command_proposed=command_proposed,
        credentials_domain=credentials_domain,
        actor_consent_token=actor_consent_token,
        actor=actor,
        **_extra,
    ))


def _format_card_summary(decision: AdminDecision, *, intent_text: str) -> str:
    """Genera una summary ~3 righe della carta vaglio per il PLANNER.

    Il PLANNER la ricicla nella final_answer del turno (cap-pending fase 1).
    Pattern dialog manager (CLAUDE.md §10.6, project_dialog_manager_authorization_ux):
    riga 1 = «cosa», riga 2 = «come», riga 3 = «scelte».
    """
    argv_pretty = render_argv_for_display(
        decision.validated_argv or decision.argv,
    )
    sig = decision.signature
    rev = decision.reversibility or _msg("MSG_SYSTEM_ADMIN_UNKNOWN_VALUE")
    sudo_marker = (
        _msg("MSG_SYSTEM_ADMIN_SUDO_MARKER")
        if decision.requires_sudo else ""
    )
    return _msg(
        "MSG_SYSTEM_ADMIN_CARD_SUMMARY",
        intent=intent_text, command=argv_pretty, sudo_marker=sudo_marker,
        signature=sig, reversibility=rev,
    )


def _spawn_via_sudoer(*, decision: AdminDecision, intent_text: str,
                     actor: str) -> dict:
    """Invoca sudoer con l'argv validato e formatta l'esito per il PLANNER."""
    from loader import invoke_verb_unique

    try:
        validated = decision.validated_argv or validate_argv(decision.argv)
    except ArgvValidationError as exc:
        return {
            "ok": False, "decision": "reject", "signature": decision.signature,
            "argv": decision.argv, "approval_required": False,
            "approval_card": None, "summary": _msg("MSG_SYSTEM_ADMIN_POLICY_BLOCKED"),
            "error_class": "invalid_args", "error_code": exc.code,
            "audit": {**decision.audit, "gate": "argv_validation_rejected",
                      "error_code": exc.code},
        }
    if (str(validated.signature) != decision.signature
            or list(validated.argv) != decision.argv):
        return {
            "ok": False, "decision": "reject", "signature": decision.signature,
            "argv": decision.argv, "approval_required": False,
            "approval_card": None, "summary": _msg("MSG_SYSTEM_ADMIN_POLICY_BLOCKED"),
            "error_class": "permission_denied",
            "error_code": "ERR_ADMIN_APPROVAL_SNAPSHOT_MISMATCH",
            "audit": {**decision.audit, "gate": "approval_snapshot_mismatch",
                      "error_code": "ERR_ADMIN_APPROVAL_SNAPSHOT_MISMATCH"},
        }
    execution_argv = list(validated.argv)

    try:
        exec_res = invoke_verb_unique(
            "sudoer",
            caller="builtins.admin",
            validated_argv=validated,
            intent_text=intent_text,
            scheduler_delay_minutes=0,
            reversibility=decision.reversibility or "unknown",
            secret=None,  # sudo password slot non gestito da PLANNER (ADR 0070)
        )
    except (PermissionError, KeyError, RuntimeError) as e:
        return {
            "ok": False, "decision": "reject",
            "signature": decision.signature, "argv": execution_argv,
            "approval_required": False, "approval_card": None,
            "summary": _msg("MSG_SYSTEM_ADMIN_SUDOER_UNAVAILABLE", error=e),
            "error_class": "dependency_unavailable",
            "error_code": "ERR_ADMIN_SUDOER_UNAVAILABLE",
            "audit": decision.audit,
        }

    # exec_res ha attributi: ok, status, exit_code, stdout, stderr, ...
    snippet_out = (exec_res.stdout or "").strip()[:600]
    snippet_err = (exec_res.stderr or "").strip()[:600]
    if exec_res.ok:
        summary = _msg(
            "MSG_SYSTEM_ADMIN_EXECUTED",
            command=render_argv_for_display(validated),
            exit_code=exec_res.exit_code,
        )
        if snippet_out:
            summary += _msg("MSG_SYSTEM_ADMIN_OUTPUT", output=snippet_out)
    else:
        exit_suffix = (
            _msg("MSG_SYSTEM_ADMIN_EXIT_SUFFIX", exit_code=exec_res.exit_code)
            if exec_res.exit_code is not None else ""
        )
        summary = _msg(
            "MSG_SYSTEM_ADMIN_EXECUTION_FAILED",
            command=render_argv_for_display(validated), status=exec_res.status,
            exit_suffix=exit_suffix,
        )
        if snippet_err:
            summary += _msg("MSG_SYSTEM_ADMIN_STDERR", stderr=snippet_err)

    return {
        "ok": exec_res.ok,
        "decision": "execute_silent",
        "signature": decision.signature,
        "argv": execution_argv,
        "approval_required": False,
        "approval_card": None,
        "exit_code": exec_res.exit_code,
        "stdout": snippet_out,
        "stderr": snippet_err,
        "duration_ms": exec_res.duration_ms,
        "summary": summary,
        **({} if exec_res.ok else {
            "error_class": "operation_failed",
            "error_code": "ERR_ADMIN_EXECUTION_FAILED",
        }),
        "audit": decision.audit,
    }
