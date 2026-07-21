"""dialog_preview — helper deterministico per preview image dei dialog
`choice_with_preview` (PR5, ADR 0090 estensione).

Le opzioni di un dialog `choice_with_preview` portano un
`preview_image_path` che e' o (a) un path assoluto a un'immagine, oppure
(b) un path con suffisso `#bbox=x,y,w,h` per ritagliare al volo (caso
face-disambiguation: stessa foto, bbox diverse).

Sicurezza (anti path-traversal §2.8): il path deve risiedere sotto:
  - ~/.local/share/metnos/  (storage canonico Metnos)
  - ~/.local/share/metnos/Immagini/  (alias scuola/foto utente)
  - /tmp/metnos_uploads/  (foto allegate al turno corrente)
Path fuori da questi prefissi → ValueError.

Determinismo §7.9: zero LLM. Solo path string + PIL crop quando bbox
presente. PIL e' gia' installato (pipeline immagini ADR 0086).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

# Prefissi assoluti consentiti (resolved). Aggiungere voci richiede
# review esplicita (commit message + ADR se cambia il modello di
# trust). Niente ~ unexpanded: il caller deve fornire path assoluti.
_ALLOWED_ROOTS_RAW: tuple[str, ...] = (
    "~/.local/share/metnos",
    "/tmp/metnos_uploads",
)


def _allowed_roots() -> list[Path]:
    """Risolve ~ in path assoluti. Cached lazily; in test si puo'
    monkeypatchare `_ALLOWED_ROOTS_RAW`."""
    out: list[Path] = []
    for r in _ALLOWED_ROOTS_RAW:
        p = Path(r).expanduser().resolve()
        out.append(p)
    return out


_BBOX_RE = re.compile(r"^(.+?)#bbox=(-?\d+),(-?\d+),(-?\d+),(-?\d+)$")


def parse_preview_path(spec: str) -> tuple[Path, tuple[int, int, int, int] | None]:
    """Decompone una `preview_image_path` in (path, bbox|None).

    Formato accettato:
      - "/abs/path.jpg"               → (Path, None)
      - "/abs/path.jpg#bbox=10,20,100,150" → (Path, (10,20,100,150))

    Solleva ValueError per:
      - spec vuoto / non stringa
      - bbox non parsabile
      - bbox con valori negativi (tutti i 4 devono essere >= 0)
      - bbox con w o h == 0
    """
    if not isinstance(spec, str) or not spec:
        raise ValueError("preview_image_path: stringa non vuota richiesta")
    m = _BBOX_RE.match(spec)
    if m is None:
        return Path(spec), None
    path_part, sx, sy, sw, sh = m.groups()
    try:
        bbox = (int(sx), int(sy), int(sw), int(sh))
    except ValueError as ex:
        raise ValueError(f"preview_image_path: bbox non int: {ex}") from ex
    if any(v < 0 for v in bbox):
        raise ValueError(f"preview_image_path: bbox valori negativi: {bbox}")
    if bbox[2] == 0 or bbox[3] == 0:
        raise ValueError(f"preview_image_path: bbox w/h == 0: {bbox}")
    return Path(path_part), bbox


def assert_safe_path(path: Path,
                     allowed_roots: Iterable[Path] | None = None) -> Path:
    """Verifica che `path` (resolved) sia sotto un root consentito.

    Ritorna `path.resolve()` o solleva ValueError. Risolve i symlink
    PRIMA del check (anti-traversal). Path inesistente: solleva
    ValueError separato (`file_not_found`).
    """
    roots = list(allowed_roots) if allowed_roots is not None else _allowed_roots()
    try:
        rp = path.expanduser().resolve()
    except OSError as ex:
        raise ValueError(f"preview_image_path: resolve fallito: {ex}") from ex
    for root in roots:
        try:
            rp.relative_to(root)
            return rp
        except ValueError:
            continue
    raise ValueError(
        f"preview_image_path '{path}' fuori dai root consentiti "
        f"({', '.join(str(r) for r in roots)})"
    )


def assert_exists(path: Path) -> Path:
    """Verifica che il file esista. Solleva ValueError altrimenti."""
    if not path.exists():
        raise ValueError(f"preview_image_path '{path}' non esiste")
    if not path.is_file():
        raise ValueError(f"preview_image_path '{path}' non e' un file regolare")
    return path


def validate_preview_spec(spec: str,
                           allowed_roots: Iterable[Path] | None = None,
                           *, require_exists: bool = True) -> tuple[Path, tuple[int, int, int, int] | None]:
    """Pipeline completa: parse + safety check + (opt) exists check.

    `require_exists=False` consente l'uso lato caller-builder dove il
    path e' costruito da dati registry (l'esistenza e' gia' garantita
    dall'enroll). Lato server (preview endpoint) e' True per defesa.
    """
    path, bbox = parse_preview_path(spec)
    safe = assert_safe_path(path, allowed_roots)
    if require_exists:
        assert_exists(safe)
    return safe, bbox


def crop_image_bytes(image_path: Path,
                     bbox: tuple[int, int, int, int] | None,
                     *, max_dim: int = 320,
                     fmt: str = "JPEG", quality: int = 82) -> bytes:
    """Apre `image_path`, opzionalmente ritaglia a `bbox=(x,y,w,h)`,
    ridimensiona la dimensione massima a `max_dim`, ritorna bytes JPEG.

    Lazy-import PIL (gia' presente per la pipeline immagini ADR 0086).
    Ridimensionamento garantito: il browser/Telegram non riceve mai
    immagini full-res 4032x3024 come thumb (sprecata bandwidth).
    """
    from PIL import Image
    import io

    with Image.open(image_path) as im:
        # EXIF transpose (rotazione foto): le foto da fotocamera mobile
        # spesso hanno orientation=6 e altrimenti la thumb appare
        # ruotata di 90 gradi rispetto al ritaglio bbox.
        try:
            from PIL import ImageOps
            im = ImageOps.exif_transpose(im)
        except Exception:
            pass
        if im.mode != "RGB":
            im = im.convert("RGB")
        if bbox is not None:
            x, y, w, h = bbox
            # Clamp ai bounds dell'immagine (bbox da modelli ML puo'
            # sforare in casi edge).
            x = max(0, min(x, im.width - 1))
            y = max(0, min(y, im.height - 1))
            w = max(1, min(w, im.width - x))
            h = max(1, min(h, im.height - y))
            im = im.crop((x, y, x + w, y + h))
        # Ridimensiona mantenendo aspect.
        im.thumbnail((max_dim, max_dim))
        buf = io.BytesIO()
        im.save(buf, format=fmt, quality=quality)
        return buf.getvalue()
