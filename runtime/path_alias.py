# SPDX-License-Identifier: AGPL-3.0-only
"""path_alias.py — Resolution alias bilingue IT↔EN per path utente.

Modulo riusabile: usato da `backends/files/local.py` (find, find_dirs,
write, move, create_dirs, delete_*) e da executor standalone come
`list_dirs.py`. ADR D.1 (alias bilingue) + D.3 (sicurezza mutating).

API pubblica:
    resolve_path_with_alias(base_path) -> (Path, note | None)
        Per LETTURA: auto-resolve scegliendo il candidato piu' grande
        se ce ne sono multipli.

    list_alias_candidates(name) -> list[dict]
        Lista TUTTI i candidati esistenti per un name, no auto-pick.

    check_mutating_path_ambiguity(input_path, target_must_exist) -> dict | None
        Per MUTATING: ritorna error ERR_AMBIGUOUS_PATH se >0 candidati,
        None se path OK o nessun candidato.

    home_dir_suggestions(missing_name, limit) -> list[str]
        Cartelle home esistenti per arricchire ERR_PATH_NOT_FOUND.

    candidate_roots() -> list[Path]
        Root da scansionare: HOME + NAS mount + /mnt + /media.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional


# Alias bilingue IT↔EN per i path utente standard (XDG user-dirs). Quando
# l'utente IT scrive "Immagini" su un sistema con LANG=en_US la cartella
# vera e' "Pictures": senza questo mapping find_files fallisce e il planner
# ritenta inutilmente lo stesso step (bug osservato turn d39e16bb).
USER_DIR_ALIASES = {
    # IT lowercase → candidati ordinati per probabilita'
    "immagini":   ["Pictures", "Immagini", "Foto", "Images", "images"],
    "foto":       ["Pictures", "Foto", "Immagini", "images"],
    "documenti":  ["Documents", "Documenti", "Docs"],
    "musica":     ["Music", "Musica"],
    "video":      ["Videos", "Video", "Movies"],
    "scaricati":  ["Downloads", "Scaricati", "Download"],
    "scrivania":  ["Desktop", "Scrivania"],
    "modelli":    ["Templates", "Modelli"],
    "pubblici":   ["Public", "Pubblici"],
    # EN lowercase → candidati (caso utente IT che chiede in EN o opposto)
    "pictures":   ["Pictures", "Immagini", "Foto"],
    "documents":  ["Documents", "Documenti"],
    "music":      ["Music", "Musica"],
    "videos":     ["Videos", "Video", "Movies"],
    "movies":     ["Movies", "Videos", "Video"],
    "downloads":  ["Downloads", "Scaricati"],
    "desktop":    ["Desktop", "Scrivania"],
    "templates":  ["Templates", "Modelli"],
    "public":     ["Public", "Pubblici"],
    "images":     ["Pictures", "Immagini", "images"],
}


# Workspace utente Metnos (convention 22/5/2026): default per path relativi
# quando il planner non specifica un path esplicito. Convention utente:
# "se non specifico un path esplicitamente, /home/user/.local/share/metnos".
# Lazy (function) per supportare mocking di Path.home() nei test.
def workspace_default() -> Path:
    return Path.home() / ".local" / "share" / "metnos"


def candidate_roots() -> list[Path]:
    """Root da cui cercare alias di user-dirs. Ordine = priorita':
    1. workspace Metnos (~/.local/share/metnos) — default utente 22/5/2026
    2. HOME (XDG canonico)
    3. NAS mount path (/tmp/nas_public/media — convention .33 ADR 0087)
    4. /mnt e /media (mount tradizionali Linux)
    Filtra root inesistenti per evitare lookup futili.
    """
    cands = [
        workspace_default(),
        Path.home(),
        Path("/tmp/nas_public/media"),
        Path("/mnt"),
        Path("/media"),
    ]
    return [p for p in cands if p.is_dir()]


def normalize_input_path(input_path: str) -> Path:
    """Normalizza un input path tollerando varianti del planner.

    Regola utente 22/5/2026: se il path non e' specificato esplicitamente
    (assoluto o ~), e' relativo al workspace Metnos `~/.local/share/metnos`,
    NON al CWD del servizio metnos-http (`/opt/metnos`).

    Es. `find_files("Immagini")` resolve a `~/.local/share/metnos/Immagini`
    invece di `/opt/metnos/Immagini` (CWD). Se non esiste lì, l'alias
    resolver continua a cercare in HOME, NAS, /mnt, /media.

    Caso live (turn 8ee09ca0): planner passa `base_path="Immagini"` →
    senza questo fix risolve a CWD `/opt/metnos/Immagini` → ERR_NOT_FOUND.
    Con questo fix → workspace → alias bilingue → trova archive reale.
    """
    if not input_path:
        return Path()
    s = str(input_path).strip()
    if s.startswith("/"):
        return Path(s).resolve()
    if s.startswith("~"):
        # Usa Path.home() invece di os.path.expanduser per supportare mocking
        # dei test e per coerenza con candidate_roots/workspace_default.
        rest = s[1:].lstrip("/")
        if not rest:
            return Path.home().resolve()
        return (Path.home() / rest).resolve()
    # Path relativo: workspace Metnos di default.
    return (workspace_default() / s).resolve()


def count_files_recursive(path: Path, cap: int = 10000) -> int:
    """Conta file ricorsivi (cap per evitare scan di TB). Per ranking match."""
    try:
        n = 0
        for entry in path.rglob("*"):
            try:
                if entry.is_file():
                    n += 1
                    if n >= cap:
                        return n
            except OSError:
                continue
        return n
    except (OSError, PermissionError):
        return 0


def resolve_path_with_alias(base_path: str) -> tuple[Path, Optional[str]]:
    """Risolve `base_path` provando alias bilingue e root multipli.

    Strategia (verbi LETTURA, auto-pick OK):
    1. Se base_path esiste cosi' com'e' → usa quello.
    2. Altrimenti, per ogni root in `candidate_roots()` e ogni alias
       bilingue, cerca match esistenti.
    3. Se 1 candidate: usa quello.
    4. Se >1 candidate: sceglie quello con piu' file (probabile "vero"
       archivio), annota nel note "trovati N candidati".

    Ritorna (resolved_path, alias_note | None).
    """
    expanded = normalize_input_path(base_path)
    if expanded.exists():
        return expanded, None
    name_key = expanded.name.lower()
    aliases = USER_DIR_ALIASES.get(name_key, [])
    if not aliases:
        return expanded, None
    candidates: list[Path] = []
    for root in candidate_roots():
        for alias in aliases:
            cand = root / alias
            try:
                if cand.is_dir():
                    candidates.append(cand)
            except (OSError, PermissionError):
                continue
    if not candidates:
        return expanded, None
    if len(candidates) == 1:
        chosen = candidates[0]
        note = (f"path '{base_path}' non esiste; risolto a '{chosen}' "
                f"(alias bilingue IT/EN per xdg user-dirs)")
        return chosen, note
    ranked = sorted(
        ((c, count_files_recursive(c)) for c in candidates),
        key=lambda kv: -kv[1],
    )
    chosen = ranked[0][0]
    others = ", ".join(f"'{p}' ({n} files)" for p, n in ranked[1:])
    note = (
        f"path '{base_path}' non esiste; trovati {len(candidates)} candidati: "
        f"scelto '{chosen}' ({ranked[0][1]} files, piu' grande). "
        f"Altri: {others}"
    )
    return chosen, note


def list_alias_candidates(name: str) -> list[dict]:
    """Lista path esistenti per `name` via alias bilingue, ordinati per
    plausibilita' (n_files desc). Per verbi MUTATING (no auto-pick).

    Differisce da `resolve_path_with_alias`: NON sceglie, restituisce
    tutti i candidati per disambiguazione utente via `get_inputs`.
    """
    name_key = name.lower()
    aliases = USER_DIR_ALIASES.get(name_key, [])
    if not aliases:
        return []
    candidates: list[dict] = []
    seen: set = set()
    for root in candidate_roots():
        for alias in aliases:
            cand = root / alias
            try:
                if cand.is_dir():
                    cand_str = str(cand)
                    if cand_str in seen:
                        continue
                    seen.add(cand_str)
                    candidates.append({
                        "path": cand_str,
                        "n_files": count_files_recursive(cand),
                    })
            except (OSError, PermissionError):
                continue
    candidates.sort(key=lambda c: -c["n_files"])
    return candidates


def check_mutating_path_ambiguity(
    input_path: str, *, target_must_exist: bool,
) -> Optional[dict]:
    """Verifica ambiguita' alias per verbi MUTATING.

    Sicurezza §2.9 (move never implicit delete) estesa al name-resolution:
    non scegliamo silenziosamente un path quando ce ne sono multipli che
    matchano l'alias bilingue.

    Args:
      input_path: path passato dall'utente/planner.
      target_must_exist: True per verbi che richiedono path gia' esistente
        (move src, delete_*). False per verbi che creano il path (write,
        create_dirs): in quel caso si controlla l'esistenza del PARENT.

    Ritorna error dict con `ERR_AMBIGUOUS_PATH` se il check fallisce e
    ci sono candidates alias da disambiguare. Ritorna None altrimenti
    (caller usa ERR_PATH_NOT_FOUND normale).
    """
    p = normalize_input_path(input_path)
    check_path = p if target_must_exist else p.parent
    if check_path.exists():
        return None
    candidates = list_alias_candidates(check_path.name)
    if not candidates:
        return None
    from messages import get as _msg  # §11 i18n
    return {
        "ok": False,
        "error_code": "ERR_AMBIGUOUS_PATH",
        "error": _msg("ERR_AMBIGUOUS_PATH", path=input_path, n=len(candidates)),
        "candidates": candidates,
        "input_path": input_path,
        "hint": _msg("ERR_AMBIGUOUS_PATH_HINT"),
    }


def home_dir_suggestions(missing_name: str, limit: int = 6) -> list[str]:
    """Quando un path utente non esiste, suggerisce cartelle in HOME che
    potrebbero essere ragionevoli alternative. Output sorted per pertinenza:
    1. Cartelle XDG user-dirs esistenti.
    2. Altre cartelle non-hidden in home.
    """
    home = Path.home()
    if not home.is_dir():
        return []
    xdg_set = {"Pictures", "Documents", "Music", "Videos", "Downloads",
               "Desktop", "Templates", "Public",
               "Immagini", "Documenti", "Musica", "Video", "Scaricati",
               "Scrivania", "Modelli", "Pubblici", "Foto"}
    out_xdg: list[str] = []
    out_other: list[str] = []
    try:
        for entry in sorted(home.iterdir(), key=lambda p: p.name.lower()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            if entry.name in xdg_set:
                out_xdg.append(str(entry))
            else:
                out_other.append(str(entry))
    except (PermissionError, OSError):
        return []
    return (out_xdg + out_other)[:limit]
