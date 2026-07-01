#!/usr/bin/env python3
"""
compute_files_loc — conta linee di codice (LOC) in directory/file di codice.

Sostituisce il synth `read_files_lines` (4/5/2026), che leggeva binari come
testo e produceva conteggi ~7x il valore reale (no skip estensioni).

Caratteristiche:
- Vettoriale: `paths` e' SEMPRE una lista (caso degenere = 1 elemento).
- Per ogni path passato: se directory, esplora ricorsivamente; se file, lo
  considera direttamente (deve comunque rispettare include_ext).
- Skippa binari (sniff: byte 0x00 nei primi 8 KB) → contati come fail.
- Skippa file fuori dalle estensioni di interesse (silenziosamente).
- Skippa path che includono substring di esclusione (`__pycache__`, ecc.).
- Comment detection deterministica per estensione (no parser):
    * `.py` `.sh` `.toml` `.yaml` `.yml` → riga inizia con `#`
    * `.rs`                                → riga inizia con `#` o `//`
    * `.js` `.css`                         → riga inizia con `//` o `/*`
    * `.html` `.md`                        → no comment detection
- count_blank: false default → blank ESCLUSE da total_lines.
- count_comments: true default → comment INCLUSE in total_lines (l'utente
  conta "tutte le linee non vuote"). count_comments=false esclude i commenti.
- truncated: true se max_files raggiunto (cap_field/cap_value esposti).
"""
from __future__ import annotations

import json
import multiprocessing
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from messages import get as _msg  # noqa: E402
from executor_helpers import run_stdio  # noqa: E402

# Parallelismo (ADR 0100). I/O FS dominante: thread bastano (open+read
# rilascia il GIL). SSD locale gestisce 4-8 letture in parallelo senza
# saturare bandwidth; bilanciamo con cpu*2.
_LOC_WORKERS = int(os.environ.get(
    "METNOS_COMPUTE_LOC_WORKERS",
    min(16, max(2, multiprocessing.cpu_count() * 2))
))


# ── Default di set ──────────────────────────────────────────────────────
DEFAULT_INCLUDE_EXT = (
    ".py", ".md", ".html", ".toml", ".sh",
    ".js", ".css", ".yaml", ".yml", ".rs",
)

DEFAULT_EXCLUDE_SUBSTRINGS = (
    "__pycache__", ".venv", ".git", "node_modules",
    "target", "dist", "build", ".cache",
)


# Comment detection, per estensione. None = nessuna detection.
# Ogni voce e' una tupla di prefissi che, se la riga lstrip()-ata inizia
# con uno di essi, marca la riga come commento.
_COMMENT_PREFIXES = {
    ".py":   ("#",),
    ".sh":   ("#",),
    ".toml": ("#",),
    ".yaml": ("#",),
    ".yml":  ("#",),
    ".rs":   ("#", "//"),
    ".js":   ("//", "/*"),
    ".css":  ("//", "/*"),
    ".html": None,
    ".md":   None,
}


def _is_binary(path: Path) -> bool:
    """Sniff di binarieta': leggi i primi 8 KB e cerca byte 0x00.
    Nessuna euristica «mostly-printable»: il null byte e' segnale solido."""
    try:
        with path.open("rb") as f:
            chunk = f.read(8192)
        return b"\x00" in chunk
    except OSError:
        # Errore di lettura → trattalo come binario/illeggibile (fail_count).
        return True


def _excluded_by_substring(path_str: str, exclude: tuple[str, ...]) -> bool:
    return any(sub in path_str for sub in exclude)


def _walk_paths(roots, include_ext, exclude_subs, max_files):
    """Genera Path di file candidati. Si ferma a max_files."""
    seen = 0
    for root in roots:
        p = Path(os.path.expanduser(str(root)))
        if not p.exists():
            continue
        if p.is_file():
            if _excluded_by_substring(str(p), exclude_subs):
                continue
            if p.suffix.lower() not in include_ext:
                continue
            yield p
            seen += 1
            if seen >= max_files:
                return
            continue
        # directory: rglob
        for cand in p.rglob("*"):
            try:
                if not cand.is_file():
                    continue
            except OSError:
                continue
            if cand.is_symlink():
                # Evita loop di symlink: skip silenzioso (i file di codice
                # «veri» sono raggiunti dalla rglob normale).
                continue
            if _excluded_by_substring(str(cand), exclude_subs):
                continue
            if cand.suffix.lower() not in include_ext:
                continue
            yield cand
            seen += 1
            if seen >= max_files:
                return


def _count_one(path: Path, count_blank: bool, count_comments: bool):
    """Ritorna (lines, blank, comment) per un singolo file di testo.
    `lines` rispetta i toggle: blank/comment esclusi se relativi flag false.
    blank/comment ritornati sempre come totali assoluti (per trasparenza)."""
    ext = path.suffix.lower()
    com_pref = _COMMENT_PREFIXES.get(ext)  # None = no detection
    blank = 0
    comment = 0
    counted = 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                stripped = raw.strip()
                is_blank = (stripped == "")
                is_comment = False
                if com_pref and stripped:
                    if any(stripped.startswith(p) for p in com_pref):
                        is_comment = True
                if is_blank:
                    blank += 1
                    if count_blank:
                        counted += 1
                elif is_comment:
                    comment += 1
                    if count_comments:
                        counted += 1
                else:
                    counted += 1
    except OSError as e:
        raise OSError(f"read failed: {e}") from e
    return counted, blank, comment


def invoke(args):
    paths = args.get("paths")
    # §2.4 robustezza NL→determinismo: l'LLM passa spesso un singolo string per
    # un arg-lista (paths="/tmp/x" o una DIRECTORY invece di ["/tmp/x"]).
    # Coalesce a lista (caso degenere N=1, §2.1). _walk_paths espande già le
    # directory via rglob → "conta le righe dei .txt in <dir>" funziona diretto
    # senza precursore find_files (bug q23 4/6).
    if isinstance(paths, str):
        paths = [paths]
    if not isinstance(paths, list) or not paths:
        return {"ok": False, "error": _msg("ERR_ARG_NOT_LIST", arg="paths")}

    include_ext_arg = args.get("include_ext")
    if include_ext_arg is None:
        include_ext = tuple(DEFAULT_INCLUDE_EXT)
    elif isinstance(include_ext_arg, str) and include_ext_arg.strip():
        # §2.4: singolo string per arg-lista (include_ext="txt" → [".txt"]).
        _e = include_ext_arg.strip()
        include_ext = ((_e if _e.startswith(".") else "." + _e).lower(),)
    elif isinstance(include_ext_arg, list):
        # Normalizza: lowercase, garantisci il punto in testa.
        include_ext = tuple(
            (e if e.startswith(".") else "." + e).lower()
            for e in include_ext_arg
            if isinstance(e, str) and e.strip()
        )
        if not include_ext:
            return {"ok": False, "error": _msg("ERR_ARG_NOT_LIST_OF", arg="include_ext", of="strings")}
    else:
        return {"ok": False, "error": _msg("ERR_ARG_NOT_LIST_OF", arg="include_ext", of="strings")}

    exclude_subs_arg = args.get("exclude_path_substrings")
    if exclude_subs_arg is None:
        exclude_subs = tuple(DEFAULT_EXCLUDE_SUBSTRINGS)
    elif isinstance(exclude_subs_arg, list):
        exclude_subs = tuple(s for s in exclude_subs_arg if isinstance(s, str) and s)
    else:
        return {"ok": False, "error": _msg("ERR_ARG_NOT_LIST_OF", arg="exclude_path_substrings", of="strings")}

    count_blank = bool(args.get("count_blank", False))
    count_comments = bool(args.get("count_comments", True))

    max_files = args.get("max_files", 50000)
    if not isinstance(max_files, int) or max_files <= 0:
        # 0-as-placeholder (the design guide §2.4): tratta come "no cap"-ish riportandolo a default.
        if max_files == 0:
            max_files = 50000
        else:
            return {"ok": False, "error": _msg("ERR_ARG_NOT_POSITIVE_INT", arg="max_files")}

    # Walk seriale (deterministico) → lista di candidati. Cap a max_files.
    candidates: list[Path] = list(_walk_paths(paths, include_ext, exclude_subs, max_files))
    visited_files = len(candidates)

    # Processing: in parallelo (ADR 0100). Helper combina sniff binario +
    # conteggio riga in 1 sola apertura quando possibile.
    def _process_one(cand: Path) -> tuple[Path, dict | None]:
        try:
            # Sniff binarieta' dai primi 8 KB (riusa _is_binary).
            if _is_binary(cand):
                return cand, None  # → fail (binario/illeggibile)
            lines, blank, comment = _count_one(cand, count_blank, count_comments)
        except OSError:
            return cand, None
        return cand, {"lines": lines, "blank": blank, "comment": comment}

    by_ext: dict[str, dict] = {}
    # by_path mantiene l'ordine di walk per stabilita' visiva (cap 200).
    # Costruiamo una mappa pos→result e poi iteriamo in ordine candidates.
    results_map: dict[int, tuple[Path, dict | None]] = {}
    if candidates:
        if len(candidates) == 1:
            # Sync fast-path (no overhead pool).
            results_map[0] = _process_one(candidates[0])
        else:
            workers = min(_LOC_WORKERS, len(candidates))
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_process_one, c): i
                        for i, c in enumerate(candidates)}
                for fut in as_completed(futs):
                    i = futs[fut]
                    try:
                        results_map[i] = fut.result()
                    except Exception:
                        results_map[i] = (candidates[i], None)

    by_path: list[dict] = []
    total_files = 0
    total_lines = 0
    total_blank = 0
    total_comment = 0
    fail_count = 0
    for i in range(len(candidates)):
        cand, info = results_map.get(i, (candidates[i], None))
        if info is None:
            fail_count += 1
            continue
        lines = info["lines"]
        blank = info["blank"]
        comment = info["comment"]
        total_files += 1
        total_lines += lines
        total_blank += blank
        total_comment += comment
        ext = cand.suffix.lower()
        bucket = by_ext.setdefault(ext, {"files": 0, "lines": 0})
        bucket["files"] += 1
        bucket["lines"] += lines
        # by_path cap: 200 (vista di scratchpad/LLM, non e' un truncation
        # degli accumulatori globali — i totali restano corretti).
        if len(by_path) < 200:
            by_path.append({
                "path": str(cand),
                "lines": lines,
                "blank": blank,
                "comment": comment,
            })

    # truncated: se abbiamo iterato fino a max_files (visited_files == max_files
    # e la rglob avrebbe avuto altri candidati) lo dichiariamo. Distinguere e'
    # imperfetto senza un secondo sondaggio: usiamo la regola «visitati == cap»
    # come segnale (puo' essere un falso positivo se per pura coincidenza il
    # numero esatto coincide con max_files; in pratica raro e non dannoso).
    truncated = visited_files >= max_files

    out = {
        "ok": True,
        "ok_count": total_files,
        "fail_count": fail_count,
        "total_files": total_files,
        "total_lines": total_lines,
        "total_blank": total_blank,
        "total_comment": total_comment,
        "by_ext": by_ext,
        "by_path": by_path,
        "summary": (
            f"{total_files} file di codice analizzati: {total_lines} linee "
            f"({total_blank} blank, {total_comment} comment). "
            f"Skip {fail_count} file binari/illeggibili."
        ),
    }
    if truncated:
        out["truncated"] = True
        out["truncated_what"] = "file"
        out["used"] = total_files
        out["cap_field"] = "max_files"
        out["cap_value"] = max_files
    return out


def main():
    run_stdio(invoke)


if __name__ == "__main__":
    main()
