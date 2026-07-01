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
)


def _build_bwrap_args(
    code_path: Path,
    capabilities: list,
    *,
    autonomy: str = "supervised",
    extra_ro: list[Path] | None = None,
    extra_rw: list[Path] | None = None,
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

    # Per ogni capability, deriva bind / network policy
    has_network = False
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
        elif kind == "network":
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

    # Network: se nessuna capability lo richiede, isola
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
) -> list[str]:
    """Wrappa un comando in bubblewrap se disponibile e non disabilitato.

    `executor` deve avere `code_path` (Path) e `capabilities` (lista
    di dict o str, formato manifest).

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
    )
    return ["bwrap", *bwrap_args, "--", *command]


# --- introspection (per dashboard / debug) ---------------------------------

def status() -> dict:
    """Stato della sandbox: bwrap installato? disabilitato? versione?"""
    return {
        "bwrap_available": bwrap_available(),
        "bwrap_path": shutil.which("bwrap"),
        "disabled_via_env": sandbox_disabled(),
        "active": bwrap_available() and not sandbox_disabled(),
    }
