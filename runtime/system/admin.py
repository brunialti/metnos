"""admin — verb-unique builtin orchestrator for shell-like intents (ADR 0070).

The admin runs the four-act flow inside its body:

    [pre-1] syntactic gate (no LLM)         — reject literal shell command
    [1+2+3] single LLM call (tier middle)   — kind ∈ {literal_command,
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
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from logging_setup import get_logger

from safety.canonicalize import compute_signature, has_sudo_wrapper
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
# PLANNER. Description ~1500 char, leggibile da LLM medium (Gemma 4 26B
# planner): pattern DEVI/NON DEVI/OK/ERRORE come da the design guide §6.
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
        {"name": "admin.shell",
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

    The default tier is `middle` (Gemma 4 26B think=false) per ADR 0026:
    intent translation is a procedural task, not a critical safety call.
    """
    try:
        from llm_router import call_middle  # type: ignore
        return call_middle(prompt, format="json", num_predict=400)
    except Exception as e:  # pragma: no cover (dev fallback)
        log.warning("admin LLM bridge unavailable, returning unknown (%s)", e)
        return '{"kind": "unknown", "reason": "LLM router unavailable"}'


# ── Wait-prompt emitter ───────────────────────────────────────────────

WAIT_LOW = (
    "Sto valutando se posso fare quello che mi chiedi senza forzare i "
    "vincoli di sicurezza, mi prendo qualche secondo."
)
WAIT_MEDIUM = (
    "Il comando che mi stai chiedendo richiede un'analisi piu' attenta "
    "del solito, ti aggiorno appena ho una proposta concreta."
)
WAIT_HIGH = (
    "Non riconosco il comando che dovrei eseguire. Ti chiedo come "
    "trattarlo, una volta sola se vuoi."
)


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


# ── Reversibility classifier (kept private to avoid duplicating
#    compute_signatures; tiny static map is enough for the decision flow) ─

_IRREVERSIBLE_BINARIES = {
    "rm", "dd", "shred", "wipefs",
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
    if sig.binary in _IRREVERSIBLE_BINARIES:
        return "irreversible", None
    hint = _REVERSIBLE_HINTS.get((sig.binary, sig.subcommand_or_flag))
    if hint:
        return "reversible", hint
    return "unknown", None


# ── Forbidden check (raw argv, complementary to canonical blacklist) ──

_FORBIDDEN_DESTRUCTIVE_BINS = frozenset({
    "rm", "mv", "cp", "dd", "mkfs", "shred", "wipefs",
    "mkfs.ext4", "mkfs.ext3", "mkfs.ext2",
    "mkfs.xfs", "mkfs.btrfs", "mkfs.fat", "mkfs.vfat",
})
_FORBIDDEN_PATHS = frozenset({
    "/", "/etc", "/boot", "/proc", "/sys", "/usr", "/lib", "/lib64",
})
_BLOCK_DEVICE_RE = re.compile(
    r"^/dev/(sd[a-z]\d*|nvme\d+n\d+(p\d+)?|disk\d+|loop\d+|mmcblk\d+(p\d+)?)$"
)


def _check_forbidden_argv(argv: list[str]) -> tuple[bool, Optional[str]]:
    """Returns (negate, reason). Operates on the raw argv to catch path-level
    bombs that the canonical signature might abstract away.
    """
    if not argv:
        return False, None
    binary = Path(argv[0]).name
    rest = argv[1:]
    if binary in {"sudo", "doas", "pkexec"} and rest:
        for i, t in enumerate(rest):
            if not t.startswith("-"):
                binary = Path(t).name
                rest = rest[i + 1:]
                break
    if binary not in _FORBIDDEN_DESTRUCTIVE_BINS:
        return False, None
    for tok in rest:
        value = (
            tok.split("=", 1)[-1]
            if "=" in tok and not tok.startswith("-")
            else tok
        )
        if value in _FORBIDDEN_PATHS:
            return True, f"destructive '{binary}' on '{value}' (Law 1)"
        if _BLOCK_DEVICE_RE.match(value):
            return True, (
                f"destructive '{binary}' on block device '{value}' (Law 1)"
            )
    return False, None


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


def _explain_command_dangers(argv: list[str], severity: str | None) -> str:
    """Spiegazione testuale dei rischi del comando (1-2 frasi). Deterministica,
    basata sul binario + flags + severity dalla policy. Niente LLM.
    """
    if not argv:
        return "Comando vuoto."
    binary = argv[0].split("/")[-1]
    flags = [t for t in argv[1:] if t.startswith("-")]
    targets = [t for t in argv[1:] if not t.startswith("-")]

    danger_by_binary = {
        "rm": "Cancella file/directory in modo IRREVERSIBILE.",
        "dd": "Scrittura raw su block device. Puo' distruggere dati.",
        "mkfs": "Formatta un filesystem cancellando tutto sul device.",
        "shred": "Sovrascrive file per rendere il recupero impossibile.",
        "wipefs": "Cancella signature filesystem da un device.",
        "fdisk": "Modifica tabella partizioni — cambio struttura disco.",
        "parted": "Modifica tabella partizioni — cambio struttura disco.",
        "iptables": "Modifica firewall del kernel — puo' bloccare la rete.",
        "ip": "Modifica configurazione di rete (route/addr/link).",
        "modprobe": "Carica/scarica moduli kernel.",
        "sysctl": "Modifica parametri kernel runtime.",
        "mount": "Monta filesystem — cambia visibilita' dati.",
        "umount": "Smonta filesystem — interrompe accesso a dati.",
        "systemctl": "Gestione servizi systemd (start/stop/restart/enable).",
        "kill": "Termina processi forzatamente.",
        "killall": "Termina TUTTI i processi con nome dato.",
        "chmod": "Modifica permessi file/directory.",
        "chown": "Modifica proprietario file/directory.",
        "apt": "Installa/rimuove pacchetti dal sistema.",
        "apt-get": "Installa/rimuove pacchetti dal sistema.",
        "dpkg": "Manipola pacchetti Debian.",
        "useradd": "Aggiunge utenti al sistema.",
        "userdel": "Rimuove utenti dal sistema (e i loro file).",
    }
    base = danger_by_binary.get(binary,
        f"`{binary}` non e' nella whitelist conosciuta. Esecuzione non automaticamente sicura.")

    notes = []
    # Force flags: -f, --force, oppure 'f' all'interno di un short-flag cluster
    # tipo `-rf`/`-fr`/`-Rf` (POSIX getopt: short flags concatenati).
    def _has_short(letter: str, flag: str) -> bool:
        return (flag.startswith("-") and not flag.startswith("--")
                and letter in flag[1:])
    has_force = any(f == "--force" or _has_short("f", f) for f in flags)
    has_recursive = any(f in ("--recursive",) or _has_short("r", f)
                         or _has_short("R", f) for f in flags)
    if has_force:
        notes.append("Include flag forzanti (`-f`/`--force`) che saltano conferme.")
    if has_recursive:
        notes.append("Operazione RICORSIVA su tutta la sottostruttura.")
    sudo_required = any(t == "sudo" for t in argv)
    if sudo_required:
        notes.append("Richiede privilegi root (`sudo`).")
    if severity == "irreversible":
        notes.append("Classificato IRREVERSIBILE dalla policy di sicurezza.")
    elif severity == "dangerous":
        notes.append("Classificato PERICOLOSO dalla policy di sicurezza.")

    if notes:
        return base + " " + " ".join(notes)
    return base


def _build_approval_card(argv: list[str], sig, requires_sudo: bool,
                          rev_class: str, undo_hint: str | None,
                          intent_text: str, actor: str,
                          severity: str | None = None) -> dict:
    """Carta vaglio role-aware. Vedi modulo header per spec opzioni."""
    is_admin = _is_admin_actor(actor)
    danger_summary = _explain_command_dangers(argv, severity)
    if is_admin:
        options = ["approve_once", "approve_and_whitelist",
                   "reject_once", "block_forever"]
        warning = (
            f"⚠ Stai per autorizzare un comando NON in whitelist. "
            f"{danger_summary}"
        )
    else:
        options = ["run_externally", "request_admin_whitelist", "reject_once"]
        warning = (
            f"Il comando `{' '.join(argv)}` non e' autorizzato per il tuo "
            f"ruolo. Puoi (a) eseguirlo personalmente fuori da Metnos, "
            f"oppure (b) chiedere all'amministratore di aggiungerlo alla "
            f"whitelist. {danger_summary}"
        )
    return {
        "type": "approval_card",
        "argv_rendered": " ".join(argv),
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
                 answer; if None, uses the default (middle tier router).

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
            reason=(
                "Mi spiace, non accetto comandi diretti. Dimmi cosa "
                "intendi fare e vedo se posso farlo io senza infrangere "
                "vincoli di sicurezza."
            ),
            audit=audit,
        )

    emit_wait(WAIT_LOW)

    # Act [1+2+3]: single LLM call (intent → argv translation)
    try:
        raw = llm_call(LLM_PROMPT_TEMPLATE.format(user_text=user_text))
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, AttributeError) as e:
        audit["llm_error"] = str(e)
        return AdminDecision(
            kind="reject",
            reason="Non ho capito la tua richiesta. Puoi riformularla?",
            audit=audit,
        )

    kind = data.get("kind")
    if kind == "literal_command":
        audit["llm_kind"] = "literal_command"
        audit["llm_reason"] = data.get("reason")
        return AdminDecision(
            kind="reject",
            reason=(
                "Mi spiace, non accetto comandi diretti. Dimmi cosa "
                "intendi fare e vedo se posso farlo io senza infrangere "
                "vincoli di sicurezza."
            ),
            audit=audit,
        )
    if kind in ("unknown", "impossible"):
        audit["llm_kind"] = kind
        return AdminDecision(
            kind="reject",
            reason=data.get("reason") or (
                "Non so come fare quello che mi chiedi."
                if kind == "unknown"
                else "Quello che mi chiedi non si puo' fare via shell."
            ),
            audit=audit,
        )
    if kind != "translated":
        audit["llm_kind"] = "malformed"
        return AdminDecision(
            kind="reject",
            reason="Non ho capito la tua richiesta. Puoi riformularla?",
            audit=audit,
        )

    argv = data.get("argv")
    if not isinstance(argv, list) or not argv or not all(
        isinstance(a, str) for a in argv
    ):
        audit["llm_kind"] = "malformed_argv"
        return AdminDecision(
            kind="reject",
            reason="La traduzione del comando non e' valida.",
            audit=audit,
        )

    audit["argv"] = argv

    # Act [4]: deterministic safety tools
    sig = compute_signature(argv)
    requires_sudo = has_sudo_wrapper(argv)
    audit["signature"] = str(sig)
    audit["requires_sudo"] = requires_sudo

    forbidden, forbidden_reason = _check_forbidden_argv(argv)
    if forbidden:
        audit["safety"] = "forbidden_hit"
        audit["safety_reason"] = forbidden_reason
        return AdminDecision(
            kind="reject",
            argv=argv, signature=str(sig),
            reason=f"Vietato: {forbidden_reason}",
            audit=audit,
        )

    store = SafetyStore()
    try:
        # Blacklist
        from safety.canonicalize import signature_matches
        for kind_tag in ("blacklist", "forbidden"):
            for row in store.find_by_kind(kind_tag):
                if signature_matches(sig, row.signature):
                    audit["safety"] = "blacklist_hit"
                    audit["matched_pattern"] = row.signature
                    audit["safety_reason"] = row.reason
                    return AdminDecision(
                        kind="reject",
                        argv=argv, signature=str(sig),
                        reason=(
                            f"Comando bloccato dalle politiche: {row.reason}"
                            if row.reason else "Comando bloccato dalle politiche."
                        ),
                        severity=row.severity,
                        audit=audit,
                    )

        # Whitelist / graylist
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

        if whitelisted_row is not None:
            # Silent execute. Record the use (graylist usage counter).
            new_uses = store.record_use(whitelisted_row.signature)
            age_class = (
                "permanent" if whitelisted_kind == "whitelist" else "graylist"
            )
            audit["safety"] = "whitelist_hit"
            audit["age_class"] = age_class
            audit["matched_pattern"] = whitelisted_row.signature
            audit["uses"] = new_uses
            return AdminDecision(
                kind="execute_silent",
                argv=argv, signature=str(sig),
                age_class=age_class,
                severity=whitelisted_row.severity,
                requires_sudo=requires_sudo,
                reversibility=rev_class,
                undo_hint=undo_hint,
                audit=audit,
            )

        # Unknown: ask the user.
        emit_wait(WAIT_HIGH)
        audit["safety"] = "unknown"
        card = _build_approval_card(
            argv=argv, sig=sig, requires_sudo=requires_sudo,
            rev_class=rev_class, undo_hint=undo_hint,
            intent_text=user_text, actor=actor,
        )
        return AdminDecision(
            kind="ask_user",
            argv=argv, signature=str(sig),
            requires_sudo=requires_sudo,
            reversibility=rev_class,
            undo_hint=undo_hint,
            card_payload=card,
            audit=audit,
        )
    finally:
        store.close()


def apply_user_decision(
    *,
    decision: AdminDecision,
    user_choice: str,
    actor: str = "host",
) -> AdminDecision:
    """Apply the user's reply to an `ask_user` decision.

    Opzioni admin (actor=='host'):
      - approve_once / approve: insert/update graylist (uses+=1), execute.
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

    # Opzioni indipendenti da ruolo: reject senza side effect.
    if user_choice in ("reject_once", "reject"):
        return AdminDecision(
            kind="reject",
            argv=decision.argv, signature=sig,
            reason="Richiesta rifiutata per questa volta.",
            audit=audit,
        )

    # Opzioni guest-only (niente safety store mutation).
    if user_choice == "run_externally":
        return AdminDecision(
            kind="reject",
            argv=decision.argv, signature=sig,
            reason=(
                "Esegui il comando manualmente fuori da Metnos. "
                "Niente azione automatica."
            ),
            audit=audit,
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
            reason=(
                "Richiesta inviata all'amministratore. Il comando verra' "
                "rivisto manualmente e, se approvato, aggiunto in whitelist."
            ),
            audit=audit,
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
                reason="Comando bloccato per sempre.",
                audit=audit,
            )
        if user_choice == "approve_and_whitelist":
            if not _is_admin_actor(actor):
                raise ValueError(
                    "approve_and_whitelist requires admin role (actor='host')"
                )
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
                audit=audit,
            )
        if user_choice in ("approve", "approve_once"):
            existing = store.find_by_signature(sig)
            if existing is None or existing.kind != "graylist":
                store.upsert_user(
                    sig, "graylist",
                    severity=decision.severity or "reversible",
                    reason="user approved from approval card",
                    created_by=actor,
                )
            new_uses = store.record_use(sig)
            audit["graylist_uses"] = new_uses
            return AdminDecision(
                kind="execute_silent",
                argv=decision.argv, signature=sig,
                age_class="graylist",
                severity=decision.severity,
                requires_sudo=decision.requires_sudo,
                reversibility=decision.reversibility,
                undo_hint=decision.undo_hint,
                audit=audit,
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
import hashlib
import time as _time

# Chiave HMAC per actor_consent_token. Persistente fra restart: scritta
# in `~/.local/share/metnos/.admin_consent_key` la prima volta, riusata
# sempre dopo. Niente sync fra nodi (carry-over).
def _consent_key() -> bytes:
    import config as _C  # §7.11
    key_path = _C.PATH_USER_DATA / ".admin_consent_key"
    key_path.parent.mkdir(parents=True, exist_ok=True)
    if key_path.exists():
        return key_path.read_bytes()
    import secrets
    k = secrets.token_bytes(32)
    key_path.write_bytes(k)
    try:
        key_path.chmod(0o600)
    except OSError:
        pass
    return k


def _sign_consent_token(signature: str, actor: str, ttl_s: int = 600) -> str:
    """Emette un token consent firmato HMAC-SHA256.

    Forma: `<exp_epoch>.<sig_b64>` dove sig_b64 = HMAC(signature || actor || exp).
    TTL default 10 minuti — coerente con CAP_PENDING_TTL_S del daemon.
    """
    import base64
    exp = int(_time.time()) + ttl_s
    payload = f"{signature}|{actor}|{exp}".encode("utf-8")
    mac = hmac.new(_consent_key(), payload, hashlib.sha256).digest()
    return f"{exp}.{base64.urlsafe_b64encode(mac).decode('ascii')}"


def _verify_consent_token(token: str, signature: str, actor: str) -> bool:
    """Verifica un token consent. Constant-time, no logging del token."""
    import base64
    if not token or "." not in token:
        return False
    try:
        exp_str, mac_b64 = token.split(".", 1)
        exp = int(exp_str)
    except (ValueError, AttributeError):
        return False
    if exp < int(_time.time()):
        return False
    payload = f"{signature}|{actor}|{exp}".encode("utf-8")
    expected = hmac.new(_consent_key(), payload, hashlib.sha256).digest()
    try:
        provided = base64.urlsafe_b64decode(mac_b64.encode("ascii"))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(expected, provided)


def _decide_for_argv(argv: list[str], *, intent_text: str,
                     actor: str = "host") -> AdminDecision:
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
            reason=(
                "Il command_proposed contiene shell-meta vietati "
                f"(pipe/redirect/substitution/separator). Riformula come "
                f"argv lineare. Causa: {literal_reason}."
            ),
            audit=audit,
        )

    # validazione struttura argv
    if not argv or not all(isinstance(a, str) and a for a in argv):
        return AdminDecision(
            kind="reject",
            reason="command_proposed vuoto o non-string.",
            audit=audit,
        )
    audit["argv"] = argv

    # safety classification (forbidden raw + blacklist + whitelist/graylist)
    sig = compute_signature(argv)
    requires_sudo = has_sudo_wrapper(argv)
    audit["signature"] = str(sig)
    audit["requires_sudo"] = requires_sudo

    forbidden, forbidden_reason = _check_forbidden_argv(argv)
    if forbidden:
        audit["safety"] = "forbidden_hit"
        audit["safety_reason"] = forbidden_reason
        return AdminDecision(
            kind="reject", argv=argv, signature=str(sig),
            reason=f"Vietato: {forbidden_reason}",
            audit=audit,
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
                    return AdminDecision(
                        kind="reject", argv=argv, signature=str(sig),
                        reason=(
                            f"Comando bloccato dalle politiche: "
                            f"{row.reason or row.signature}"
                        ),
                        severity=row.severity,
                        audit=audit,
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

        if whitelisted_row is not None:
            new_uses = store.record_use(whitelisted_row.signature)
            age_class = (
                "permanent" if whitelisted_kind == "whitelist" else "graylist"
            )
            audit["safety"] = "whitelist_hit"
            audit["age_class"] = age_class
            audit["matched_pattern"] = whitelisted_row.signature
            audit["uses"] = new_uses
            return AdminDecision(
                kind="execute_silent",
                argv=argv, signature=str(sig),
                age_class=age_class,
                severity=whitelisted_row.severity,
                requires_sudo=requires_sudo,
                reversibility=rev_class, undo_hint=undo_hint,
                audit=audit,
            )

        # signature sconosciuta → carta vaglio role-aware
        audit["safety"] = "unknown"
        card = _build_approval_card(
            argv=argv, sig=sig, requires_sudo=requires_sudo,
            rev_class=rev_class, undo_hint=undo_hint,
            intent_text=intent_text, actor=actor,
        )
        return AdminDecision(
            kind="ask_user",
            argv=argv, signature=str(sig),
            requires_sudo=requires_sudo,
            reversibility=rev_class, undo_hint=undo_hint,
            card_payload=card,
            audit=audit,
        )
    finally:
        store.close()


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

    Pattern dialog manager (the design guide §10.6, project_dialog_manager_authorization_ux):
    riga 1 = «cosa serve», riga 2-3 = parametri tecnici, riga 4 = come fornirle,
    riga 5 = alternative.
    """
    binding = ctx.get("binding", "?")
    host = ctx.get("host", "?")
    share = ctx.get("share", "")
    lines = [
        f"Servono credenziali per {domain}",
        f"  binding: {binding}",
        f"  host:    {host}",
    ]
    if share:
        lines.append(f"  share:   {share}")
    lines += [
        "",
        "Inviamele nel prossimo messaggio:",
        "  user XXXXX pwd YYYYY",
        "",
        "[oppure] rispondi `cli` per istruzioni terminale.",
        "[oppure] rispondi `annulla` per abortire.",
    ]
    return "\n".join(lines)


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
    return (
        "Per inserire le credenziali via terminale:\n\n"
        "  ssh roberto@192.0.2.10   # se accedi da un altro host\n"
        f"  metnos-cli credentials add {domain}{extra}\n"
        "    > username: ...\n"
        "    > password: ...\n\n"
        "Quando hai finito, ripeti la richiesta originale e procedero'."
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

# Wrapper di privilegi: skip al fine di guardare il vero argv[0].
_PRIV_WRAPPERS = frozenset({"sudo", "doas", "pkexec"})


def _executor_name_in_argv(argv: list[str]) -> Optional[str]:
    """Ritorna il nome dell'executor se argv[0] (saltando sudo/doas/pkexec)
    matcha un executor presente nel catalogo runtime, altrimenti None.

    Lookup deterministico O(N) sul catalogo importato lazy: rispetta i
    rejected (synth scartati per affinity overlap / signature drift / GC).
    Caching minimo per evitare reflection ripetuta nello stesso processo.
    """
    if not argv:
        return None
    # Salta wrapper sudo/doas/pkexec e i loro flag (-S, -u user, -E, ...)
    i = 0
    while i < len(argv) and argv[i] in _PRIV_WRAPPERS:
        i += 1
        # Skip flag dopo il wrapper
        while i < len(argv) and argv[i].startswith("-"):
            tok = argv[i]
            i += 1
            # -u <user>, -p <prompt> richiedono argomento successivo
            if tok in ("-u", "-p", "-g", "-h", "-r", "-t", "-C", "-D"):
                if i < len(argv):
                    i += 1
    if i >= len(argv):
        return None
    candidate = Path(argv[i]).name
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


def invoke(*, intent: str, command_proposed: str,
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
            "summary": (
                f"Il comando contiene segnaposto non risolti: {_ph_list}. "
                f"Per procedere ho bisogno dei valori reali (es. indirizzo "
                f"IP del server, path della cartella). Riformula la "
                f"richiesta sostituendo i segnaposto."
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

    # ── Catalog-name guard (12/5/2026): se `argv[0]` (saltando i wrapper
    # sudo/doas/pkexec) coincide con il nome di un executor del catalog,
    # il PLANNER ha sbagliato strada: admin e' per UN comando shell
    # privilegiato; gli executor del catalog (get_now, set_events, ...)
    # si invocano direttamente come tool ordinari. subprocess.run(["get_now"])
    # produrrebbe FileNotFoundError perche' non c'e' nessun binario "get_now"
    # nel PATH. Rifiutiamo qui con messaggio chiaro, evitando di emettere
    # una carta vaglio inutile (l'utente non puo' "approvare" qualcosa che
    # non puo' funzionare). Determinismo §7.9.
    catalog_hit = _executor_name_in_argv(argv)
    if catalog_hit is not None:
        return {
            "ok": False,
            "decision": "reject",
            "signature": "",
            "argv": argv,
            "approval_required": False,
            "approval_card": None,
            "summary": (
                f"`{catalog_hit}` e' un executor del catalogo, non un comando "
                f"shell. Invocalo direttamente come tool (es. "
                f"`{catalog_hit}(...)`) invece di passarlo come "
                f"`command_proposed` ad admin. admin serve solo per comandi "
                f"di sistema (mount, kill, systemctl, apt, ...)."
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
                "summary": (
                    f"Pacchetto `{_pkg}` non e' nella whitelist Metnos. "
                    f"Per installarlo aggiungilo a "
                    f"`~/.config/metnos/installable_packages.json` (lista "
                    f"JSON di string), oppure registra il binary di cui ha "
                    f"bisogno in `runtime/system_binaries._BINARY_TO_PACKAGE`. "
                    f"Whitelist attuale: {', '.join(_allowed[:8])}"
                    f"{' ...' if len(_allowed) > 8 else ''}."
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
            descr_parts = [f"binding {binding}", f"host {host}"]
            if share:
                descr_parts.append(f"share {share}")
            descr_parts.append("le credenziali saranno cifrate (Fernet+HKDF).")
            payload = {
                "title": f"Credenziali per {target_domain}",
                "description": " · ".join(descr_parts),
                "dialog": [
                    {"var": "username", "prompt": "Username:",
                     "schema": {"kind": "text"}},
                    {"var": "password", "prompt": "Password:",
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
        if actor_consent_token and _verify_consent_token(
            actor_consent_token, decision.signature, audit_actor,
        ):
            # Promote a graylist + execute (riusa apply_user_decision per
            # il bookkeeping, NON manda nuova carta).
            promoted = apply_user_decision(
                decision=decision, user_choice="approve", actor=audit_actor,
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
                "summary": "Token consent valido ma promozione fallita.",
                "audit": promoted.audit,
            }

        # Niente token valido → esponi carta + emetti consent_token nel campo
        # response. Il runtime lo metterà in cap_pending; l'utente non lo
        # vede direttamente.
        token = _sign_consent_token(decision.signature, audit_actor)
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
        "summary": f"Stato admin imprevisto: {decision.kind}",
        "audit": decision.audit,
    }


def _format_card_summary(decision: AdminDecision, *, intent_text: str) -> str:
    """Genera una summary ~3 righe della carta vaglio per il PLANNER.

    Il PLANNER la ricicla nella final_answer del turno (cap-pending fase 1).
    Pattern dialog manager (the design guide §10.6, project_dialog_manager_authorization_ux):
    riga 1 = «cosa», riga 2 = «come», riga 3 = «scelte».
    """
    argv_pretty = " ".join(decision.argv)
    sig = decision.signature
    rev = decision.reversibility or "ignota"
    sudo_marker = " (richiede sudo)" if decision.requires_sudo else ""
    return (
        f"Per «{intent_text}» propongo:\n"
        f"  `{argv_pretty}`{sudo_marker}\n"
        f"  signature: {sig} · reversibilita': {rev}\n"
        f"Rispondi **sì** per approvare ed eseguire una volta, "
        f"**no** per annullare. (Auto-promozione a whitelist dopo "
        f"5 conferme della stessa signature.)"
    )


def _spawn_via_sudoer(*, decision: AdminDecision, intent_text: str,
                     actor: str) -> dict:
    """Invoca sudoer con l'argv validato e formatta l'esito per il PLANNER."""
    from loader import invoke_verb_unique

    try:
        exec_res = invoke_verb_unique(
            "sudoer",
            caller="builtins.admin",
            argv=decision.argv,
            intent_text=intent_text,
            scheduler_delay_minutes=0,
            reversibility=decision.reversibility or "unknown",
            secret=None,  # sudo password slot non gestito da PLANNER (ADR 0070)
        )
    except (PermissionError, KeyError, RuntimeError) as e:
        return {
            "ok": False, "decision": "reject",
            "signature": decision.signature, "argv": decision.argv,
            "approval_required": False, "approval_card": None,
            "summary": f"sudoer non disponibile: {e}",
            "audit": decision.audit,
        }

    # exec_res ha attributi: ok, status, exit_code, stdout, stderr, ...
    snippet_out = (exec_res.stdout or "").strip()[:600]
    snippet_err = (exec_res.stderr or "").strip()[:600]
    if exec_res.ok:
        summary = f"Eseguito: `{' '.join(decision.argv)}` (exit {exec_res.exit_code})."
        if snippet_out:
            summary += f"\nOutput: {snippet_out}"
    else:
        summary = (
            f"Esecuzione fallita: `{' '.join(decision.argv)}` "
            f"(status {exec_res.status}"
            + (f", exit {exec_res.exit_code}" if exec_res.exit_code is not None else "")
            + ")."
        )
        if snippet_err:
            summary += f"\nstderr: {snippet_err}"

    return {
        "ok": exec_res.ok,
        "decision": "execute_silent",
        "signature": decision.signature,
        "argv": decision.argv,
        "approval_required": False,
        "approval_card": None,
        "exit_code": exec_res.exit_code,
        "stdout": snippet_out,
        "stderr": snippet_err,
        "duration_ms": exec_res.duration_ms,
        "summary": summary,
        "audit": decision.audit,
    }
