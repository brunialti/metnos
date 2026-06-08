"""system_binaries — helper centrale per check dei binari shell mancanti
e whitelist pacchetti installabili tramite admin (ADR install-on-demand,
17/5/2026).

Pattern §7.3 generale: qualsiasi executor che chiama un binary di
sistema (ffmpeg, pdftotext, tesseract, heif-convert, pandoc, ...)
DOVREBBE usare `check_binary()` invece di `shutil.which()` raw. Cosi'
quando il binary manca, l'executor ritorna nel result un record
strutturato che `agent_runtime` detecta deterministicamente per
attivare il flow install-on-demand:

  1. executor result: error_class="binary_missing" + suggested_install
  2. agent_runtime detecta -> persiste pending_install_resume cross-turn
  3. inject admin(cmd=suggested_install) -> CARD HMAC consent
  4. utente approva -> admin esegue via sudoer -> install ok
  5. agent_runtime auto-resume executor originale con args identici
  6. final con esito reale

Whitelist auto-derivata dal registry _BINARY_TO_PACKAGE + override
utente `~/.config/metnos/installable_packages.json`. `admin` guard
rifiuta install di pacchetti NON in whitelist anche se l'utente
approva la card (defense in depth contro pkg-injection via prompt).

Estensione: per ogni nuovo executor con dipendenze shell, AGGIUNGI
qui le entry binary->package. Non duplicare la mappa per-executor.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path


# Registry centrale binary -> pacchetto Debian/Ubuntu.
# Estendibile: aggiungi qui quando un nuovo executor introduce
# una dipendenza shell. Mantieni ordinato per dominio.
_BINARY_TO_PACKAGE: dict[str, str] = {
    # change_files_format (conversioni multimediali)
    "ffmpeg": "ffmpeg",
    "pdftotext": "poppler-utils",
    "heif-convert": "libheif-examples",
    "convert": "imagemagick",
    "magick": "imagemagick",
    "pandoc": "pandoc",
    "soffice": "libreoffice-core",
    "libreoffice": "libreoffice-core",
    # read_files_ocr
    "tesseract": "tesseract-ocr",
    # admin/sysadmin helpers (gia' installati sul .33 ma censiti)
    "mount": "util-linux",
    "umount": "util-linux",
    "timedatectl": "systemd",
    # Networking diagnostics
    "nmap": "nmap",
    "iperf3": "iperf3",
    "mtr": "mtr-tiny",
}


import config as _C  # §7.11
_USER_WHITELIST_FILE = _C.PATH_USER_CONFIG / "installable_packages.json"


def check_binary(name: str) -> dict | None:
    """Check se un binary e' nel PATH.

    Returns:
        None se installato (caller procede normalmente).
        dict con `missing_binary`, `package`, `suggested_install`,
        `error_class="binary_missing"` se mancante (caller copia il
        dict nel proprio result entry).

    Esempio uso in un executor::

        from system_binaries import check_binary
        missing = check_binary("heif-convert")
        if missing:
            results.append({"src": src, "dst": None, "ok": False, **missing})
            continue
    """
    if shutil.which(name) is not None:
        return None
    pkg = _BINARY_TO_PACKAGE.get(name, name)
    return {
        "error_class": "binary_missing",
        "missing_binary": name,
        "package": pkg,
        "suggested_install": f"sudo apt install -y {pkg}",
        "error": (f"binary '{name}' non installato. "
                   f"Installa con: sudo apt install -y {pkg}"),
    }


def installable_packages_whitelist() -> set[str]:
    """Pacchetti che `admin` puo' installare auto via apt.

    Source 1: tutti i valori di _BINARY_TO_PACKAGE (auto-derived).
    Source 2: file utente (extra pkgs, override per estensioni custom).

    Defense in depth: anche se il PLANNER emette
    `admin(cmd="sudo apt install -y X")` per un pacchetto non in
    whitelist (es. injection via prompt), admin rifiuta il fire.
    """
    pkgs: set[str] = set(_BINARY_TO_PACKAGE.values())
    if _USER_WHITELIST_FILE.exists():
        try:
            extra = json.loads(_USER_WHITELIST_FILE.read_text(encoding="utf-8"))
            if isinstance(extra, list):
                pkgs.update(str(p) for p in extra if isinstance(p, str))
        except (json.JSONDecodeError, OSError):
            pass
    return pkgs


def is_package_installable(pkg: str) -> bool:
    """True se il pacchetto puo' essere installato auto (in whitelist)."""
    return pkg in installable_packages_whitelist()


def parse_apt_install_pkg(cmd: str) -> str | None:
    """Estrae il pacchetto da una stringa `sudo apt install -y <pkg>`.

    Ritorna None se la stringa NON e' un apt install ben formato
    (esempio: pipe, redirect, multi-pkg, repo aggiunto, ecc).
    Determinismo §7.9: zero LLM, regex semplice.

    Used by admin guard: validate che il cmd da eseguire sia un
    install canonical su SINGOLO pacchetto whitelisted.
    """
    if not isinstance(cmd, str):
        return None
    tokens = cmd.strip().split()
    # Pattern atteso: [sudo] (apt|apt-get) install [-y] <pkg>
    if not tokens:
        return None
    i = 0
    if tokens[i] == "sudo":
        i += 1
    if i >= len(tokens) or tokens[i] not in ("apt", "apt-get"):
        return None
    i += 1
    if i >= len(tokens) or tokens[i] != "install":
        return None
    i += 1
    # Skip flag opzionali (-y, --yes, --no-install-recommends)
    while i < len(tokens) and tokens[i].startswith("-"):
        i += 1
    if i >= len(tokens):
        return None
    pkg = tokens[i]
    # Resto deve essere vuoto o solo flag (refuse multi-pkg per evitare
    # whitelist bypass: se chain di pkg, controllare uno per uno e' fragile).
    extra = [t for t in tokens[i + 1:] if not t.startswith("-")]
    if extra:
        return None
    # Validazione minima formato pkg (no path, no pipe, no shell chars)
    if any(ch in pkg for ch in "|&;`$()<>{}[]\\\"'*?~"):
        return None
    return pkg
