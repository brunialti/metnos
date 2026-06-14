"""photo_endpoint — signing HMAC, resolver da TurnLog, thumbnail cache.

Foundation di Opzione 1 (5/5/2026): URL signed di durata 24h che mappano
`(turn_id, idx, size)` al path locale di una foto risultata in chat.
Esposto via `GET /agent/photos/<turn_id>/<idx>?size=thumb|full&t=<sig>`.

Resolver: legge i JSONL `~/.local/share/metnos/turns/<date>.jsonl` e cerca
il turno per `turn_id`. Sceglie il path da `result.attachments[idx].path`
(executor-emesso) con fallback a `result.entries[idx].path`. Cap a 7
giorni di history per non scandire indefinitamente.

Thumbnail cache: `~/.local/share/metnos/thumbcache/<sha8(path,size,mtime)>.jpg`.
Pillow JPEG q85, fit-inside 256x256 per `thumb`, 1600x1600 per `full`.
Ricostruita se cancellata, mai expirata (mtime nel digest invalida da sé).

Niente LLM (the design guide §7.9): tutto deterministico.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import time
from datetime import date, timedelta
from pathlib import Path

from logging_setup import get_logger
import config as _C  # §7.11

log = get_logger(__name__)

TURNS_DIR = _C.PATH_USER_DATA / "turns"
THUMB_CACHE_DIR = _C.PATH_USER_DATA / "thumbcache"
TOKEN_TTL_S = 86400  # 24h

VALID_SIZES = ("thumb", "full")
SIZE_DIMS = {"thumb": (256, 256), "full": (1600, 1600)}

TURNS_HISTORY_DAYS = 7  # quanto indietro scansionare i JSONL


# --- Signing -----------------------------------------------------------------

def _photo_secret(admin_key: str) -> bytes:
    """Secret derivato dalla admin key, namespace `photo:` per evitare
    cross-protocol confusion col cookie admin (che usa `cookie:`)."""
    return hashlib.sha256(("photo:" + admin_key).encode()).digest()


def _sign(turn_id: str, idx: int, size: str, exp: int, admin_key: str) -> str:
    msg = f"{turn_id}|{idx}|{size}|{exp}".encode()
    return hmac.new(_photo_secret(admin_key), msg, hashlib.sha256).hexdigest()[:32]


def make_url(turn_id: str, idx: int, size: str, admin_key: str,
             *, base: str = "/agent/photos") -> str:
    """Costruisce l'URL signed per (turn_id, idx, size). TTL 24h."""
    if size not in VALID_SIZES:
        raise ValueError(f"size must be one of {VALID_SIZES}")
    exp = int(time.time()) + TOKEN_TTL_S
    sig = _sign(turn_id, idx, size, exp, admin_key)
    return f"{base}/{turn_id}/{idx}?size={size}&exp={exp}&t={sig}"


def verify(turn_id: str, idx: int, size: str, exp: int, token: str,
           admin_key: str) -> bool:
    """True se il token è valido e non scaduto."""
    if size not in VALID_SIZES:
        return False
    if exp < int(time.time()):
        return False
    expected = _sign(turn_id, idx, size, exp, admin_key)
    return hmac.compare_digest(expected, token)


# --- Resolve (turn_id, idx) → path locale --------------------------------

def _candidate_jsonl_files() -> list[Path]:
    """JSONL turns scritti negli ultimi TURNS_HISTORY_DAYS giorni, ordinati
    dal più recente al più vecchio (oggi prima)."""
    if not TURNS_DIR.exists():
        return []
    today = date.today()
    out = []
    for delta in range(TURNS_HISTORY_DAYS):
        d = today - timedelta(days=delta)
        p = TURNS_DIR / f"{d.isoformat()}.jsonl"
        if p.exists():
            out.append(p)
    return out


def _path_from_step_result(step_result: dict, idx: int) -> str | None:
    """Da uno step.result, ritorna il path della i-esima entry. Preferisce
    `attachments[idx].path` (lista declarativa per i canali); fallback a
    `entries[idx].path`."""
    if not isinstance(step_result, dict):
        return None
    atts = step_result.get("attachments")
    if isinstance(atts, list) and 0 <= idx < len(atts):
        item = atts[idx]
        if isinstance(item, dict) and isinstance(item.get("path"), str):
            return item["path"]
    entries = step_result.get("entries")
    if isinstance(entries, list) and 0 <= idx < len(entries):
        item = entries[idx]
        if isinstance(item, dict) and isinstance(item.get("path"), str):
            return item["path"]
    return None


def resolve_turn_record(turn_id: str) -> dict | None:
    """Cerca il turno per turn_id nei JSONL recenti. Ritorna l intero
    record (dict) se trovato, altrimenti None. Usato dalla gallery view
    che ha bisogno di iterare su tutti gli attachments del turno (non
    solo uno per indice come `resolve_path`)."""
    if not turn_id:
        return None
    for jsonl in _candidate_jsonl_files():
        try:
            with jsonl.open("r", encoding="utf-8") as fh:
                for line in fh:
                    if turn_id not in line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("turn_id") == turn_id:
                        return rec
        except OSError as e:
            log.debug("resolve_turn_record: cannot read %s: %s", jsonl, e)
            continue
    return None


def resolve_path(turn_id: str, idx: int) -> str | None:
    """Cerca il turno per turn_id nei JSONL recenti, estrae il path della
    i-esima foto. Ritorna None se non trovato."""
    if not turn_id or idx < 0:
        return None
    for jsonl in _candidate_jsonl_files():
        try:
            with jsonl.open("r", encoding="utf-8") as fh:
                for line in fh:
                    if turn_id not in line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("turn_id") != turn_id:
                        continue
                    # Scorre gli step dal più recente: l'ultimo producer di
                    # attachments/entries vince (use case realistico: un
                    # solo step find_images_indices per turno).
                    for step in reversed(rec.get("steps") or []):
                        path = _path_from_step_result(step.get("result") or {}, idx)
                        if path:
                            return path
                    return None
        except OSError as e:
            log.debug("resolve_path: cannot read %s: %s", jsonl, e)
            continue
    return None


# --- Thumbnail cache -----------------------------------------------------

def _cache_key(path: str, size: str) -> str:
    """Digest del path + size + mtime per invalidate automatica quando la
    foto cambia."""
    try:
        mtime = int(os.path.getmtime(path))
    except OSError:
        mtime = 0
    raw = f"{path}|{size}|{mtime}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def _cache_path(path: str, size: str) -> Path:
    return THUMB_CACHE_DIR / f"{_cache_key(path, size)}.jpg"


def get_or_make_thumb(path: str, size: str) -> Path | None:
    """Ritorna il path al thumbnail cached. Se manca, lo genera (Pillow JPEG q85
    fit-inside). Ritorna None se il path sorgente non è leggibile come
    immagine."""
    if size not in VALID_SIZES:
        return None
    src = Path(path)
    if not src.exists() or not src.is_file():
        return None
    cache = _cache_path(path, size)
    if cache.exists():
        return cache
    # Genera
    try:
        from PIL import Image
    except ImportError:
        log.error("Pillow non installato: impossibile generare thumb")
        return None
    THUMB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(src) as im:
            im = im.convert("RGB")
            w, h = SIZE_DIMS[size]
            im.thumbnail((w, h), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=85, optimize=True)
            cache.write_bytes(buf.getvalue())
    except Exception as e:
        log.warning("thumb generation failed for %s: %s", path, e)
        return None
    return cache


def get_thumb_bytes(turn_id: str, idx: int, size: str) -> bytes | None:
    """Comodità: resolve + get_or_make + read. Usato dal daemon Telegram per
    multipart upload (Telegram non può raggiungere URL LAN-only)."""
    path = resolve_path(turn_id, idx)
    if not path:
        return None
    thumb = get_or_make_thumb(path, size)
    if not thumb:
        return None
    try:
        return thumb.read_bytes()
    except OSError:
        return None
