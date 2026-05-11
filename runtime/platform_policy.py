"""platform_policy.py — OS-aware filesystem policy for Metnos executors.

Scopo: centralizzare la conoscenza filesystem-OS-specifica per:
  1. Riconoscere file di sistema (Thumbs.db, .DS_Store, ecc.) — cross-mount safe.
  2. Riconoscere percorsi protetti (root system dirs) per il SO host corrente.

Usato da:
  - executor critical (move_files, write_files, delete_*) come safety net.
  - capability check (oltre ai hint del manifest, blocco system paths).

Cross-platform:
  - Server Linux puro: policy Linux.
  - Server macOS: policy macOS.
  - Metnos installato su Windows o client Rust su Windows: policy Windows.
  - Volume cross-mounted (USB NTFS, NFS, ecc.): SYSTEM_FILES_ALL contiene
    nomi di tutti i SO comuni, cosi' il safety net riconosce p.es. un
    Thumbs.db che vive su un disco Windows montato su Linux.

Per executor remoti: il client Rust mantiene la propria copia di questa
politica (replicata in linguaggio target); il server non assume nulla
sull'OS del client.
"""
import os
import platform


def current_os() -> str:
    """Identifica il SO host: 'linux' | 'macos' | 'windows' | 'unknown'."""
    s = platform.system().lower()
    if s == "linux":
        return "linux"
    if s == "darwin":
        return "macos"
    if s == "windows" or os.name == "nt":
        return "windows"
    return "unknown"


# File di sistema riconosciuti sempre, indipendentemente dal SO host.
# Cross-mount safe: un Thumbs.db trovato su un volume FAT/NTFS montato su
# Linux deve essere riconosciuto come system, perche' viene da Windows.
SYSTEM_FILES_ALL = frozenset({
    # Windows
    "Thumbs.db", "ehthumbs.db", "ehthumbs_vista.db", "desktop.ini",
    # macOS
    ".DS_Store", ".AppleDouble", ".LSOverride", "Icon\r",
    ".Spotlight-V100", ".Trashes", ".fseventsd", ".TemporaryItems",
    ".VolumeIcon.icns", ".com.apple.timemachine.donotpresent",
    ".AppleDB", ".AppleDesktop",
    # Linux (rare, ma capitano)
    ".Trash-1000", ".directory",
})


def is_system_file(name: str) -> bool:
    """True se `name` (basename) e' un file di sistema noto su qualsiasi SO."""
    return name in SYSTEM_FILES_ALL


# Percorsi protetti per SO host. Mai scrivere/spostare entries dentro questi
# alberi, anche se l'utente li include nel hint del manifest.
_PROTECTED_LINUX = (
    "/etc", "/usr", "/var", "/sys", "/proc", "/root", "/boot",
    "/dev", "/lib", "/lib64", "/sbin", "/bin",
)
_PROTECTED_MACOS = (
    "/System", "/Library", "/private", "/usr", "/etc", "/var",
    "/Applications", "/bin", "/sbin", "/cores",
)
_PROTECTED_WINDOWS = (
    "C:\\Windows", "C:\\Program Files", "C:\\Program Files (x86)",
    "C:\\ProgramData", "C:\\$Recycle.Bin",
)


def protected_paths() -> list[str]:
    """Lista di prefissi di path protetti per il SO host corrente."""
    os_id = current_os()
    if os_id == "linux":
        return list(_PROTECTED_LINUX)
    if os_id == "macos":
        return list(_PROTECTED_MACOS)
    if os_id == "windows":
        return list(_PROTECTED_WINDOWS)
    return []


def is_protected_path(path: str) -> bool:
    """True se `path` cade dentro un albero di sistema protetto del SO host."""
    os_id = current_os()
    p = os.path.normpath(path)
    if os_id == "windows":
        p_low = p.lower()
        for prot in _PROTECTED_WINDOWS:
            prot_n = os.path.normpath(prot).lower()
            if p_low == prot_n or p_low.startswith(prot_n + "\\"):
                return True
        return False
    # POSIX
    for prot in protected_paths():
        if p == prot or p.startswith(prot + "/"):
            return True
    return False
