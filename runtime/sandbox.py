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
import shutil
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
    """Estrae la 'famiglia' della capability: fs:read, fs:write, network:http, code:exec, ..."""
    if isinstance(cap, dict):
        name = cap.get("name", "")
    else:
        name = cap or ""
    return name.split(":")[0] if ":" in name else name


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
    return name.split(":", 1)[1] if ":" in name else ""


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
        elif kind in ("network", "net"):
            # Entrambe le grafie esistono nei manifest (`network:http`,
            # `net:read`): tolleranza al confine §2.4 — il kind `net` ignorato
            # lasciava --unshare-net a executor che dichiaravano rete.
            has_network = True
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

    Segnali, uniti (un executor è provider-backed se ALMENO uno vale):
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
    from vocab import PROVIDER_SKILLS, PROVIDER_SUFFIXES
    skills: dict[str, None] = {}   # dict = set ordinato

    prov = getattr(executor, "provenance", None) or {}
    skill_id = prov.get("skill_id") if isinstance(prov, dict) else None
    if isinstance(skill_id, str) and skill_id.strip():
        skills[skill_id.strip()] = None

    name = getattr(executor, "name", "") or ""
    for suffix in PROVIDER_SUFFIXES:
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

    for cap in getattr(executor, "capabilities", None) or []:
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


# --- introspection (per dashboard / debug) ---------------------------------

def status() -> dict:
    """Stato della sandbox: bwrap installato? disabilitato? versione?"""
    return {
        "bwrap_available": bwrap_available(),
        "bwrap_path": shutil.which("bwrap"),
        "disabled_via_env": sandbox_disabled(),
        "active": bwrap_available() and not sandbox_disabled(),
    }
