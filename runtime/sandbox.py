"""sandbox.py — sandbox bubblewrap per gli executor (Metnos v1.1).

Sostituisce la pseudo-sandbox del POC (filtro path/host nel runtime) con un
wrapping reale via bubblewrap quando disponibile. Fallback graceful: se
`bwrap` non e' installato, il comando viene eseguito senza wrapping (la
pseudo-sandbox di `agent_runtime` resta attiva come prima).

Filosofia (cap. 6 Architettura, strato 3):
- Niente <code>subprocess.run</code> diretto al codice dell'executor: si
  passa sempre da `wrap_command()`.
- I flag derivati dal manifest dell'executor (capabilities + hint).
- Tre profili candidati (readonly / supervised / full) corrispondenti ai
  tre livelli di autonomia del cap. 12. Per MVP, si deriva tutto dal
  manifest; i profili separati arriveranno con `policy.html` v1.1.

Limiti v1.1:
- Niente landlock (richiede kernel >= 5.13 e syscalls); rinviato.
- Niente Docker namespace (rinviato per casi che richiedono isolamento
  ancora piu' severo).
- Niente seccomp custom: si usa il default di bwrap.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

# --- detection -------------------------------------------------------------

def bwrap_available() -> bool:
    """True se `bwrap` e' nel PATH e funziona. Cached al primo accesso."""
    return shutil.which("bwrap") is not None


def sandbox_disabled() -> bool:
    """True se l'utente ha disabilitato esplicitamente la sandbox via env.

    Utile per debug locale senza bwrap, o per CI.
    """
    return os.environ.get("METNOS_SANDBOX", "").lower() in ("0", "off", "no", "false")


# --- helpers ---------------------------------------------------------------

def _expand_hints_to_paths(hints: list[str]) -> list[Path]:
    """Da glob-like hint (es. '~/notes/**', '/tmp/**') agli ancestor effettivi.

    Per il bind, prendiamo l'ancestor della radice del glob (es. '~/notes').
    Bwrap montera' l'intera radice, non solo i file matching.
    """
    out: list[Path] = []
    seen: set[str] = set()
    for h in hints or []:
        if not isinstance(h, str):
            continue
        # Tronca al primo segmento glob
        for sep in ("/**", "/*", "**"):
            idx = h.find(sep)
            if idx > 0:
                h = h[:idx]
                break
        h = os.path.expanduser(h)
        if not h.startswith("/"):
            continue
        if h in seen:
            continue
        seen.add(h)
        out.append(Path(h))
    return out


def _capability_kind(cap: dict | str) -> str:
    """Estrae la famiglia da entrambe le notazioni storiche ``.`` e ``:``."""
    if isinstance(cap, dict):
        name = cap.get("name", "")
    else:
        name = cap or ""
    positions = [pos for pos in (name.find(":"), name.find(".")) if pos >= 0]
    return name[:min(positions)] if positions else name


def _capability_mode(cap: dict | str) -> str:
    """Estrae la modalita' della capability: read|write|http|exec|...

    fs:read   -> read
    fs:write  -> write
    network:http -> http
    code:exec -> exec
    """
    if isinstance(cap, dict):
        name = cap.get("name", "")
    else:
        name = cap or ""
    positions = [pos for pos in (name.find(":"), name.find(".")) if pos >= 0]
    return name[min(positions) + 1:] if positions else ""


def _managed_local_resource_paths(hints: list[str], *, writable: bool) -> list[Path]:
    """Risolve hint semantici `<resource>:local` in storage canonico.

    Le capability `metnos:read/write/create` non sono path filesystem grezzi:
    dichiarano una risorsa amministrata da Metnos. Il resolver centralizza la
    traduzione, così gli executor non devono conoscere i bind bubblewrap.
    """
    try:
        import config as _C
        # Each semantic resource expands to an explicit closed set of paths.
        # The profile view intentionally groups two read-only SQLite stores;
        # it never grants the surrounding data directory or a credential vault.
        resources = {
            "spreadsheet": ((Path(_C.PATH_USER_DATA) / "spreadsheets",), True),
            "persons_registry": ((Path(_C.PATH_USER_DATA) / "persons.sqlite",), True),
            "identity_profile": ((
                Path(_C.PATH_USER_DATA) / "persons.sqlite",
                Path(_C.PATH_USER_DATA) / "users.db",
            ), False),
            "introvertiva_proposals": ((
                Path(_C.PATH_AUDIT),
                Path(_C.PATH_USER_STATE) / "proposals_state.db",
            ), False),
        }
    except Exception:
        return []
    out: list[Path] = []
    for hint in hints or []:
        if not isinstance(hint, str) or not hint.endswith(":local"):
            continue
        resource = hint.split(":", 1)[0]
        spec = resources.get(resource)
        if spec is None:
            continue
        paths, allows_write = spec
        if writable and not allows_write:
            continue
        candidates: list[Path] = []
        for path in paths:
            if writable:
                try:
                    target_dir = path.parent if path.suffix else path
                    target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                except OSError:
                    continue
            candidates.append(path)
            # A read-only SQLite connection may need already-existing WAL/SHM
            # sidecars. Bind exact files, never PATH_USER_DATA itself.
            if not writable and path.suffix in {".db", ".sqlite"}:
                candidates.extend([
                    path.with_name(path.name + "-wal"),
                    path.with_name(path.name + "-shm"),
                ])
        for candidate in candidates:
            if candidate.exists() and candidate not in out:
                out.append(candidate)
    return out


def _index_resource_paths(hints: list[str]) -> list[Path]:
    """Resolve a closed semantic index hint to its canonical storage.

    An index capability is not a free-form filesystem path.  In particular,
    ``image`` exposes only the image-index subtree and honours
    ``METNOS_INDEX_ROOT`` through ``config.PATH_INDEX_IMAGE``; an unknown or
    historical path-like hint grants nothing.
    """
    try:
        import config as _C
    except Exception:
        return []
    resources = {
        "image": Path(_C.PATH_INDEX_IMAGE),
    }
    out: list[Path] = []
    for hint in hints or []:
        if not isinstance(hint, str):
            continue
        path = resources.get(hint)
        if path is not None and path.exists() and path not in out:
            out.append(path)
    return out


def filesystem_extras(executor, args) -> list[Path]:
    """Resolve signed ``fs:read`` hints of the form ``arg:<name>``.

    The manifest chooses which typed argument may carry filesystem authority;
    the invocation can only narrow that declaration to concrete existing
    paths.  Other arguments and unknown hints never create a bind.  Traditional
    absolute/glob hints remain handled by ``_build_bwrap_args`` for migrated
    executors that intentionally expose a fixed filesystem scope.
    """
    from capabilities import effective_capabilities

    invocation = args if isinstance(args, dict) else {}
    effective = effective_capabilities(
        getattr(executor, "capabilities", None) or [],
        getattr(executor, "args_schema", None) or {},
        invocation,
    )
    selected: list[Path] = []
    seen: set[str] = set()
    for capability in effective:
        if capability.get("name") != "fs:read":
            continue
        for hint in capability.get("hint", []) or []:
            if not isinstance(hint, str) or not hint.startswith("arg:"):
                continue
            arg_name = hint[4:]
            if not arg_name or ":" in arg_name:
                continue
            raw = invocation.get(arg_name)
            values = raw if isinstance(raw, list) else [raw]
            for value in values:
                if not isinstance(value, str) or not value.strip():
                    continue
                path = Path(os.path.expanduser(value))
                if not path.is_absolute():
                    path = (Path.cwd() / path).resolve()
                if not path.exists():
                    continue
                key = str(path)
                if key in seen:
                    continue
                seen.add(key)
                selected.append(path)
    return selected


def undo_history_extras(executor, *, turn_id=None) -> list[Path]:
    """Expose only the invocation's managed undo-blob directory as RW.

    The authority comes from the signed ``restore_blob_backup`` reverse
    pattern, while ``turn_id`` narrows it to one runtime-owned directory.  A
    manifest cannot request another history path and an invocation argument
    cannot widen this bind.
    """
    patterns = getattr(executor, "reverse_pattern", None)
    if isinstance(patterns, str):
        patterns = [patterns]
    if (not isinstance(patterns, list)
            or "restore_blob_backup" not in patterns):
        return []

    key = str(turn_id) if turn_id is not None else "no_turn"
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", key):
        return []
    try:
        import config as _C
        history_root = Path(os.environ.get("METNOS_HISTORY_DIR") or (
            Path(_C.PATH_USER_DATA) / "_history"))
        blob_dir = history_root.expanduser() / key / "blob"
        blob_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        return [blob_dir]
    except OSError:
        # The executor will report the backup failure and will not delete.
        return []


_SYSTEM_READ_HINTS = frozenset({
    "processes", "health", "network_interfaces", "service_status",
    "executables",
})


def _system_read_resources(hints: list[str]) -> tuple[bool, list[Path]]:
    """Resolve read-only system-introspection hints.

    The return value is ``(needs_host_network, read_only_paths)``. Unknown
    hints grant nothing, so extending an executor cannot widen the sandbox by
    inventing a resource name.
    """
    requested = {
        hint for hint in (hints or [])
        if isinstance(hint, str) and hint in _SYSTEM_READ_HINTS
    }
    paths: list[Path] = []
    if "service_status" in requested:
        candidates = [
            Path("/run/systemd"), Path("/run/dbus"),
            Path(f"/run/user/{os.getuid()}"),
        ]
        paths = [path for path in candidates if path.exists()]
    return "network_interfaces" in requested, paths


# --- core ------------------------------------------------------------------

# Path system minimi montati read-only in ogni sandbox.
# Bwrap fallisce se uno di questi manca; aggiungiamo solo quelli che esistono.
_SYSTEM_RO_PATHS = (
    "/usr", "/bin", "/sbin", "/lib", "/lib64", "/lib32",
    "/etc", "/opt", "/var/lib/python3",
    # /sys READ-ONLY (9/7): info descrittive hardware (GPU /sys/class/drm,
    # USB /sys/bus/usb, block /sys/block) per get_processes health. Info-only:
    # in RO non si scrive nulla; standard nelle sandbox info-gathering.
    "/sys",
)


def _build_bwrap_args(
    code_path: Path,
    capabilities: list,
    *,
    autonomy: str = "supervised",
    extra_ro: list[Path] | None = None,
    extra_rw: list[Path] | None = None,
    force_net: bool = False,
) -> list[str]:
    """Costruisce gli argomenti di bwrap a partire da un manifest.

    capabilities: lista di dict con `name` (es. 'fs:read') e `hint` (paths).
    autonomy: 'readonly' | 'supervised' | 'full' (per ora informativo).
    """
    args: list[str] = []

    # Path system read-only (solo quelli esistenti)
    for p in _SYSTEM_RO_PATHS:
        if Path(p).exists():
            args += ["--ro-bind", p, p]

    # /tmp privato + /proc + /dev minimal
    args += ["--proc", "/proc"]
    args += ["--dev", "/dev"]
    args += ["--tmpfs", "/tmp"]

    # Codice dell'executor: deve essere leggibile (read-only)
    code_dir = code_path.parent
    args += ["--ro-bind", str(code_dir), str(code_dir)]

    # Gli executor importano gli helper condivisi (`messages`,
    # `executor_helpers`, client di dominio) dalla runtime canonica del daemon.
    # `/opt` e' gia' visibile tra i path di sistema, ma una installazione
    # relocabile puo' vivere in qualunque directory (per esempio sotto HOME):
    # il bind esplicito mantiene identico il confine della sandbox senza
    # esporre l'intera radice dell'installazione.
    runtime_dir = Path(__file__).resolve().parent
    if runtime_dir.exists():
        args += ["--ro-bind", str(runtime_dir), str(runtime_dir)]

    # Gli executor devono vedere lo stesso ambiente Python del core. In una
    # installazione standard ``sys.executable`` vive nella .venv Metnos e i
    # pacchetti non sono presenti nel Python di sistema.
    runtime_prefix = Path(sys.prefix)
    if sys.prefix != sys.base_prefix and runtime_prefix.exists():
        args += ["--ro-bind", str(runtime_prefix), str(runtime_prefix)]
    else:
        # Developer/system-Python runs may resolve required wheels from the
        # standard per-user site directory (for example numpy). Bind that
        # package root read-only, never the surrounding HOME tree.
        try:
            import site
            user_site = Path(site.getusersitepackages())
            if user_site.exists():
                args += ["--ro-bind", str(user_site), str(user_site)]
        except (AttributeError, OSError):
            pass

    # §7.13: i DB i18n + detection_lexicon read-only, così gli executor
    # risolvono le stringhe user-facing (messages.get / lessici) invece di
    # `<missing:KEY>` (l'executor gira SENZA questi DB nel filesystem privato
    # bwrap). DELETE-mode (no -wal/-shm) → basta il single-file; l'executor li
    # apre immutable read-only. No-op se assenti (fresh install pre-seed).
    try:
        from config import DB_I18N as _DBI, DB_DETECTION as _DBD
        for _db in (_DBI, _DBD):
            if Path(_db).exists():
                args += ["--ro-bind", str(_db), str(_db)]
    except Exception:  # noqa: BLE001 — best-effort, mai bloccare la sandbox
        pass

    # Per ogni capability, deriva bind / network policy
    has_network = force_net
    capability_names = {
        (cap.get("name", "") if isinstance(cap, dict) else str(cap or ""))
        for cap in (capabilities or [])
    }
    if "metnos:credentials_metadata_only" in capability_names:
        # Semantic capability: bind the canonical vault paths from config,
        # never a manifest's home-specific hint. Writers may bootstrap the
        # empty vault directory; all consumers need the master key read-only.
        try:
            import credentials as _credentials
            writable = "metnos:write" in capability_names
            vault_dir = Path(_credentials.CRED_DIR)
            if writable and not vault_dir.exists():
                vault_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            if vault_dir.exists():
                args += ["--bind" if writable else "--ro-bind",
                         str(vault_dir), str(vault_dir)]
            admin_key = Path(_credentials.ADMIN_KEY_PATH)
            if admin_key.exists():
                args += ["--ro-bind", str(admin_key), str(admin_key)]
        except (ImportError, OSError):
            # The executor reports the missing vault/key honestly downstream.
            pass
    for cap in capabilities or []:
        kind = _capability_kind(cap)
        mode = _capability_mode(cap)
        hints = cap.get("hint", []) if isinstance(cap, dict) else []
        if kind == "fs":
            paths = _expand_hints_to_paths(hints)
            for p in paths:
                if not p.exists():
                    continue
                if mode == "read":
                    args += ["--ro-bind", str(p), str(p)]
                else:  # write o altro
                    args += ["--bind", str(p), str(p)]
        elif kind == "metnos" and mode in {"read", "write", "create"}:
            writable = mode in {"write", "create"}
            for p in _managed_local_resource_paths(hints, writable=writable):
                args += ["--bind" if writable else "--ro-bind",
                         str(p), str(p)]
        elif kind == "index" and mode == "read":
            for p in _index_resource_paths(hints):
                args += ["--ro-bind", str(p), str(p)]
        elif kind in ("network", "net"):
            # Entrambe le grafie esistono nei manifest (`network:http`,
            # `net:read`): tolleranza al confine §2.4 — il kind `net` ignorato
            # lasciava --unshare-net a executor che dichiaravano rete.
            has_network = True
        elif kind == "systemd" and mode == "read":
            # Capability informativa: rende visibili solo socket e cataloghi
            # necessari a interrogare systemd. Il controllo lifecycle rimane
            # nel core HTTP e non viene mai delegato a un executor.
            candidates = [Path("/run/systemd"), Path("/run/dbus")]
            candidates.append(Path(f"/run/user/{os.getuid()}"))
            for path in candidates:
                if path.exists():
                    args += ["--ro-bind", str(path), str(path)]
        elif kind == "system" and mode == "read":
            needs_network, paths = _system_read_resources(hints)
            has_network = has_network or needs_network
            for path in paths:
                args += ["--ro-bind", str(path), str(path)]
        elif kind == "skill":
            # Famiglia DICHIARATIVA `skill:<binding>` (10/7): l'executor dipende
            # da una skill con credenziali → home skill RW (il refresh OAuth
            # RISCRIVE il token) + rete. Stesso effetto di `skill_extras` ma
            # dichiarato nel manifest.
            home = _skill_home_path(mode)
            if home is not None and home.exists():
                args += ["--bind", str(home), str(home)]
            has_network = True
        elif kind == "code":
            # code:exec eredita /usr/bin per i tool consueti; nessun bind aggiuntivo
            pass
        # altre famiglie (mail, time, ...) non richiedono bind

    # Path extra forniti dal chiamante
    for p in extra_ro or []:
        if Path(p).exists():
            args += ["--ro-bind", str(p), str(p)]
    for p in extra_rw or []:
        if Path(p).exists():
            args += ["--bind", str(p), str(p)]

    # Network: se nessuna capability (o extra del chiamante) lo richiede, isola
    if not has_network:
        args += ["--unshare-net"]

    # Isolamento utente/IPC/uts: sempre on
    args += ["--unshare-user", "--unshare-ipc", "--unshare-uts"]

    # Niente nuovi privilegi
    args += ["--die-with-parent"]

    return args


def wrap_command(
    executor,
    command: list[str],
    *,
    autonomy: str = "supervised",
    extra_ro: list | None = None,
    extra_rw: list | None = None,
    force_net: bool = False,
) -> list[str]:
    """Wrappa un comando in bubblewrap se disponibile e non disabilitato.

    `executor` deve avere `code_path` (Path) e `capabilities` (lista
    di dict o str, formato manifest). `force_net=True` NON isola la rete
    anche senza capability network (usato con `skill_extras`).

    Ritorna la lista comando wrappata (es. ['bwrap', '--ro-bind', ..., '--',
    'python3', 'read_files.py']) oppure il comando invariato se bwrap manca o
    `METNOS_SANDBOX=0` e' settato.
    """
    if sandbox_disabled() or not bwrap_available():
        return list(command)

    code_path = Path(getattr(executor, "code_path", "."))
    capabilities = getattr(executor, "capabilities", []) or []

    bwrap_args = _build_bwrap_args(
        code_path, capabilities,
        autonomy=autonomy,
        extra_ro=[Path(p) for p in (extra_ro or [])],
        extra_rw=[Path(p) for p in (extra_rw or [])],
        force_net=force_net,
    )
    return ["bwrap", *bwrap_args, "--", *command]


# --- skill-backed invocations (10/7/2026) -----------------------------------
# Root cause del «OAuth in loop» post-9/7 (installazione bubblewrap): gli
# executor che parlano con un provider via skill (google-workspace/github)
# giravano in bwrap SENZA la skill home (token OAuth invisibile) e i
# dispatcher `metnos:*` anche SENZA rete → ogni op chiedeva il setup da capo.
# SoT dell'identità provider→skill: `vocab.PROVIDER_SKILLS`. Qui SOLO la
# rilevazione deterministica (§7.9) e la traduzione in bind/rete.


def _skill_home_path(binding: str):
    """Home della skill via `skill_wrapper._skill_home` (SoT, rispetta
    METNOS_SKILL_HOME). Lazy + fail-soft: None se non risolvibile."""
    try:
        from skill_wrapper import _skill_home
        return _skill_home(binding)
    except Exception:  # noqa: BLE001 — mai bloccare la sandbox per un helper
        return None


def invocation_skills(executor, args) -> list[str]:
    """Skill (binding) di cui QUESTA invocazione ha bisogno. Deterministico §7.9.

    Gli executor conformi derivano l'autorita' esclusivamente dalle capability
    ``provider:access`` effettive. Solo gli executor legacy conservano i cinque
    segnali storici come ripiego compatibile:
      1. `provenance.skill_id` — tool importati da skill (ADR 0123);
      2. suffisso provider del nome (`vocab.PROVIDER_SUFFIXES`→`PROVIDER_SKILLS`,
         es. `write_images_google_photos`);
      3. `args.client` = provider non-locale (builtin client-arg, ADR 0165 —
         es. `find_files(client='google_workspace')`);
      4. `client` DICHIARATO single-provider nel manifest (dispatcher come
         `read_files_doc`: enum=['google_workspace'] anche quando l'arg non
         viaggia nel piano — il default lo applica l'executor);
      5. capability famiglia `skill:<binding>` nel manifest.
    Ritorna la lista dei binding skill (dedup, ordine stabile)."""
    from capabilities import effective_capabilities
    from vocab import PROVIDER_SKILLS, PROVIDER_SUFFIXES
    skills: dict[str, None] = {}   # dict = set ordinato

    known_bindings = set(PROVIDER_SKILLS.values())
    effective = effective_capabilities(
        getattr(executor, "capabilities", None) or [],
        getattr(executor, "args_schema", None) or {},
        args,
    )
    for capability in effective:
        if capability.get("name") != "provider:access":
            continue
        for binding in capability.get("hint", []) or []:
            if isinstance(binding, str) and binding in known_bindings:
                skills[binding] = None

    standard_state = getattr(executor, "standard_state", "legacy") or "legacy"
    if standard_state == "declared":
        return list(skills)
    if standard_state != "legacy":
        return list(skills)

    prov = getattr(executor, "provenance", None) or {}
    skill_id = prov.get("skill_id") if isinstance(prov, dict) else None
    if isinstance(skill_id, str) and skill_id.strip():
        skills[skill_id.strip()] = None

    name = getattr(executor, "name", "") or ""
    for suffix in sorted(PROVIDER_SUFFIXES):
        if name.endswith("_" + suffix) and suffix in PROVIDER_SKILLS:
            skills[PROVIDER_SKILLS[suffix]] = None

    client = (args or {}).get("client") if isinstance(args, dict) else None
    if isinstance(client, str) and client in PROVIDER_SKILLS:
        skills[PROVIDER_SKILLS[client]] = None

    schema = getattr(executor, "args_schema", None) or {}
    client_prop = ((schema.get("properties") or {}).get("client") or {}
                   if isinstance(schema, dict) else {})
    enum = client_prop.get("enum") if isinstance(client_prop, dict) else None
    if (isinstance(enum, list) and enum
            and all(isinstance(e, str) and e in PROVIDER_SKILLS for e in enum)):
        for e in enum:
            skills[PROVIDER_SKILLS[e]] = None

    for cap in effective:
        if _capability_kind(cap) == "skill":
            binding = _capability_mode(cap)
            if binding:
                skills[binding] = None

    return list(skills)


def skill_extras(skills) -> tuple[list, bool]:
    """(extra_rw, force_net) per `wrap_command` da una lista di skill binding.

    Home skill in RW (il refresh OAuth RISCRIVE il token via os.replace) e
    rete abilitata. Solo le home ESISTENTI (skill assente = niente bind: il
    needs_inputs onesto arriva a valle)."""
    paths = []
    for s in skills or []:
        home = _skill_home_path(s)
        if home is not None and Path(home).exists():
            paths.append(Path(home))
    return paths, bool(skills)


def _safe_mail_accounts(raw) -> tuple[list[str], bool]:
    """Return safe account names and whether ``all`` was requested.

    Account values are used only to derive canonical credential filenames.
    Reject path syntax rather than sanitising it: an invocation argument may
    narrow declared authority, never redirect a bind outside the mail stores.
    """
    if raw is None:
        values = ["metnos_system"]
    elif isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, list):
        values = raw
    else:
        return [], False

    out: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        account = value.strip()
        if not account:
            continue
        if account.casefold() == "all":
            return [], True
        if (account.startswith(".") or ".." in account
                or any(char in account for char in ("/", "\\", "\x00"))):
            continue
        if account not in out:
            out.append(account)
    return out, False


def mail_extras(executor, args) -> tuple[list[Path], bool]:
    """Read-only credential binds and network authority for local IMAP.

    The grant exists only when the signed manifest declares an effective
    ``mail:read`` capability. Invocation arguments can then *narrow* it to the
    selected channel/backend/account; they can never create it. Google mail is
    governed by ``provider:access`` and Telegram inquiry has no synchronous
    mailbox, so neither receives the local IMAP vault surface.

    The encrypted credential directory is shared with web logins. Therefore
    this resolver binds individual ``smtp_<account>.json.age`` files, never
    the whole vault. ``account='all'`` expands only the ``smtp_*`` subset and
    configured mail env files.
    """
    from capabilities import effective_capabilities

    effective = effective_capabilities(
        getattr(executor, "capabilities", None) or [],
        getattr(executor, "args_schema", None) or {},
        args,
    )
    if not any(cap.get("name") == "mail:read" for cap in effective):
        return [], False

    invocation = args if isinstance(args, dict) else {}
    channel = str(invocation.get("via_channel") or "email").casefold()
    client = str(invocation.get("client") or "metnos").casefold()
    if channel not in {"email", "mail"} or client != "metnos":
        return [], False

    accounts, all_accounts = _safe_mail_accounts(invocation.get("account"))
    try:
        import config as _config
        import credentials as _credentials
    except ImportError:
        # Keep the network decision capability-derived. The executor will
        # report missing credentials honestly if canonical paths cannot resolve.
        return [], True

    config_root = Path(_config.PATH_USER_CONFIG)
    vault_root = Path(_credentials.CRED_DIR)
    candidates: list[Path] = []

    admin_key = Path(_credentials.ADMIN_KEY_PATH)
    candidates.append(admin_key)

    if all_accounts:
        if vault_root.is_dir():
            candidates.extend(sorted(vault_root.glob("smtp_*.json.age")))
        mail_dir = config_root / "mail"
        if mail_dir.is_dir():
            candidates.extend(sorted(mail_dir.glob("*.env")))
        candidates.append(config_root / "mail.env")
        candidates.append(Path.home() / ".config" / "account_personal" / "mail.env")
    else:
        for account in accounts:
            candidates.append(vault_root / f"smtp_{account}.json.age")
            if account in {"metnos", "metnos_system", "metnos_secondary"}:
                candidates.append(config_root / "mail.env")
            elif account == "account_personal":
                candidates.append(
                    Path.home() / ".config" / "account_personal" / "mail.env"
                )
            else:
                candidates.append(config_root / "mail" / f"{account}.env")

    paths: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        if key in seen or not path.is_file():
            continue
        seen.add(key)
        paths.append(path)
    return paths, True


def dialog_extras(executor, *, actor: str | None,
                  channel: str | None) -> list[Path]:
    """Bind RW minimo per executor con ``dialog.user_input``.

    Non montiamo l'intero archivio: ogni invocazione vede solo la directory
    del proprio sender, che puo' contenere input sensibili ancora parziali.
    """
    capabilities = getattr(executor, "capabilities", None) or []
    names = {
        (cap.get("name", "") if isinstance(cap, dict) else str(cap or ""))
        for cap in capabilities
    }
    if "dialog.user_input" not in names:
        return []
    try:
        import dialog_pending as _dp
        sender = f"{channel}:{actor}" if channel else (actor or "host")
        path = _dp.DIALOG_DIR / _dp._safe_sender(sender)
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.chmod(0o700)
        return [path]
    except (OSError, ImportError):
        return []


# --- introspection (per dashboard / debug) ---------------------------------

def status() -> dict:
    """Stato della sandbox: bwrap installato? disabilitato? versione?"""
    return {
        "bwrap_available": bwrap_available(),
        "bwrap_path": shutil.which("bwrap"),
        "disabled_via_env": sandbox_disabled(),
        "active": bwrap_available() and not sandbox_disabled(),
    }
