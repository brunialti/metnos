#!/usr/bin/env python3
"""asset_discovery.py — discovery di sub-directory popolate per dominio.

Funzione: scan ricorsivo di un `scope_root` (default lo spazio Metnos
~/.local/share/metnos/), conta i file per estensione, ritorna la lista
delle directory che contengono almeno `min_files` asset di un dato dominio,
ordinate per popolazione decrescente.

Pattern estendibile: stessa firma per ogni dominio (image, document,
video, audio). Logica condivisa in `_discover_dirs_by_ext`.

Vincoli (the design guide):
  - §7.9: deterministico, no LLM in critical path.
  - §7.3: niente hardcoding di nomi speciali (es. "Immagini"). Lo scope
    e' una directory radice; la sub-dir piu' popolata vince per misura
    quantitativa.
  - §2.4: confine NL→determinismo robusto (skip dir nascoste/cache,
    case-insensitive su estensioni).

Uso tipico (da find_images_indices con base_path=None):

    from asset_discovery import discover_image_dirs
    from config import PATH_USER_DATA
    dirs = discover_image_dirs(PATH_USER_DATA, min_files=5)
    # dirs = [Path("/home/r/.local/share/metnos/Immagini/2024"),
    #         Path("/home/r/.local/share/metnos/foto-vacanze"), ...]

Per i domini non-image (document/video/audio), oggi stub a TODO. La
firma e' identica: `discover_<dom>_dirs(scope_root, min_files=N)`.
"""
from __future__ import annotations

import os
from pathlib import Path

# Estensioni canoniche per dominio. Sets congelati: nuove estensioni si
# aggiungono per discussione esplicita (non e' strict di tipo, e' solo
# qualifier euristico per "questa dir e' una dir di foto").
IMAGE_EXTS: frozenset[str] = frozenset({
    ".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".tiff", ".tif",
    ".gif", ".bmp",
})

DOCUMENT_EXTS: frozenset[str] = frozenset({
    ".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md", ".epub",
})

VIDEO_EXTS: frozenset[str] = frozenset({
    ".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".wmv", ".flv",
})

AUDIO_EXTS: frozenset[str] = frozenset({
    ".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma",
})

# Directory da skippare durante il walk (case-insensitive). Sono cache,
# storage interno, controllo versione, dati di indice/storage Metnos.
_SKIP_DIRS: frozenset[str] = frozenset({
    "thumbcache", "__pycache__", "_history", ".git", "_pending",
    "node_modules", ".cache", ".trash",
})


def _discover_dirs_by_ext(
    scope_root: Path,
    exts: frozenset[str],
    min_files: int = 5,
    skip_dirs: frozenset[str] = _SKIP_DIRS,
) -> list[Path]:
    """Walk ricorsivo, conta file con estensione in `exts` per directory.

    Ritorna la lista di Path con conteggio >= `min_files`, ordinata per
    popolazione decrescente (piu' popolata first). Tie-break alfabetico.
    Idempotente; deterministico.

    Skip:
      - dir nascoste (start con `.`),
      - dir in `skip_dirs` (case-insensitive),
      - scope_root inesistente → [].

    Estensioni: confronto case-insensitive (`.JPG` == `.jpg`).
    """
    scope = Path(scope_root).expanduser()
    if not scope.exists() or not scope.is_dir():
        return []

    counts: dict[Path, int] = {}
    skip_lower = {s.lower() for s in skip_dirs}

    for root, dirs, files in os.walk(scope):
        # Modifica `dirs` in-place per pruning del walk: skip dir nascoste
        # e dir in skip set (case-insensitive su nome). Garantisce che
        # `os.walk` non discenda in ramo escluso.
        dirs[:] = [
            d for d in dirs
            if not d.startswith(".") and d.lower() not in skip_lower
        ]

        n = 0
        for f in files:
            # Suffix case-insensitive: `.JPG` riconosciuto come `.jpg`.
            sfx = Path(f).suffix.lower()
            if sfx in exts:
                n += 1

        if n >= min_files:
            counts[Path(root)] = n

    # Ordina per popolazione desc, tie-break alfabetico per determinismo.
    return sorted(counts.keys(), key=lambda p: (-counts[p], str(p)))


def discover_top_level_image_corpora(
    scope_root: Path, min_files: int = 5
) -> list[Path]:
    """Restituisce SOLO le top-level corpora di immagini (figli diretti di
    scope_root con >= min_files immagini contate ricorsivamente nel sottoalbero).

    Differenza con `discover_image_dirs`: questa NON discende a sub-dirs.
    Ogni corpus = una directory top-level = un sha8(base_path) = un indice.
    Usata da `find_images_indices` quando `base_path` e' None: scope_root
    tipico = `~/.local/share/metnos/`, output = ['Immagini'] (eventualmente
    + altre corpora top-level se l'utente le ha aggiunte/symlinkate).

    Evita il bug "654 sub-corpora" diagnosticato 8/5/2026 sera: la discovery
    ricorsiva di `discover_image_dirs` enumerava ogni sotto-anno/sotto-evento
    di Immagini come corpus distinto, generando 654 build pending invece di
    riconoscere il singolo indice esistente per Immagini/.

    Sort: popolazione desc, tie-break alfabetico (deterministico).
    """
    scope = Path(scope_root).expanduser()
    if not scope.exists() or not scope.is_dir():
        return []
    skip_lower = {s.lower() for s in _SKIP_DIRS}
    counts: dict[Path, int] = {}
    for child in scope.iterdir():
        if not child.is_dir():
            continue
        if child.name.startswith(".") or child.name.lower() in skip_lower:
            continue
        n = 0
        for root, dirs, files in os.walk(child):
            dirs[:] = [
                d for d in dirs
                if not d.startswith(".") and d.lower() not in skip_lower
            ]
            for f in files:
                if Path(f).suffix.lower() in IMAGE_EXTS:
                    n += 1
        if n >= min_files:
            counts[child] = n
    return sorted(counts.keys(), key=lambda p: (-counts[p], str(p)))


def discover_image_dirs(scope_root: Path, min_files: int = 5) -> list[Path]:
    """Discovery di sub-directory con almeno `min_files` immagini.

    Walk ricorsivo da `scope_root`. Estensioni: `.jpg/.jpeg/.png/.heic/
    .heif/.webp/.tiff/.tif/.gif/.bmp` (case-insensitive). Skip dir nascoste,
    `thumbcache/`, `__pycache__/`, `_history/`, `.git/`, `_pending/`,
    `node_modules/`, `.cache/`, `.trash/`.

    Ritorna lista Path ordinata per numero foto desc (piu' popolata first).
    Tie-break alfabetico. Lista vuota se scope_root inesistente o non-dir.
    """
    return _discover_dirs_by_ext(scope_root, IMAGE_EXTS, min_files=min_files)


def discover_document_dirs(scope_root: Path, min_files: int = 5) -> list[Path]:
    """TODO: stub. Stessa firma di `discover_image_dirs` per documenti."""
    return _discover_dirs_by_ext(scope_root, DOCUMENT_EXTS, min_files=min_files)


def discover_video_dirs(scope_root: Path, min_files: int = 5) -> list[Path]:
    """TODO: stub. Stessa firma per video."""
    return _discover_dirs_by_ext(scope_root, VIDEO_EXTS, min_files=min_files)


def discover_audio_dirs(scope_root: Path, min_files: int = 5) -> list[Path]:
    """TODO: stub. Stessa firma per audio."""
    return _discover_dirs_by_ext(scope_root, AUDIO_EXTS, min_files=min_files)
