# SPDX-License-Identifier: AGPL-3.0-only
"""Streaming HTTP download with progress + sha256 verify.

Built on httpx so we get HTTP/2 and decent timeout semantics for free.

Resilient to per-flow resets (some ISPs/CGNAT/middleboxes reset a single long
TCP transfer after a few tens of MB while short transfers and parallel flows
succeed): a large Range-capable file is fetched with MANY small parallel chunks,
each with intra-chunk resume, so no single connection has to survive the whole
file. Falls back to a single resumable stream when the server ignores Range or
the file is small. A failed sha256 deletes the partial file so a re-run starts
fresh rather than perpetuating a corrupted file.
"""

from __future__ import annotations

import hashlib
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import httpx

from . import ui

# Sotto questa soglia il parallelo non vale: stream singolo. Sopra, chunk
# paralleli. Chunk piccoli (8 MB) stanno sotto la soglia di reset per-flusso
# tipica; il resume per-chunk recupera comunque il drop occasionale.
_PARALLEL_THRESHOLD = 16_000_000
_CHUNK_BYTES = 8_000_000
_WORKERS = 12
_CHUNK_ATTEMPTS = 10
_UA = {"User-Agent": "metnos-installer/1.0"}


@dataclass
class Asset:
    """One downloadable file."""
    name: str           # human label shown to user
    url: str            # absolute https URL
    dest: Path          # local target path (will be overwritten if size matches)
    sha256: str | None  # hex digest; None disables verification (NOT recommended)
    size: int | None = None   # expected bytes, for progress bar; None → use Content-Length

    def already_present(self) -> bool:
        if not self.dest.exists():
            return False
        if self.sha256:
            return _sha256_file(self.dest) == self.sha256.lower()
        # No checksum to verify against → trust an existing same-size file…
        if self.size:
            return self.dest.stat().st_size == self.size
        # …or, with neither sha256 nor size pinned (pre-release placeholders),
        # trust any existing non-empty file so re-runs stay idempotent and a
        # pre-seeded model is not re-downloaded. Integrity is the release
        # pipeline's job (it pins sha256); here we only avoid wasted bandwidth.
        return self.dest.stat().st_size > 0


def _sha256_file(p: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _require_https(url: str, label: str) -> bool:
    """Fail-closed: only https:// is acceptable for downloaded artifacts.

    A plaintext http:// URL (or one that resolves to http after redirect)
    leaves the artifact open to MITM tampering — and we run/sign these
    artifacts. Reject before any byte is fetched.
    """
    if not url.lower().startswith("https://"):
        ui.warn(f"{label}: insecure URL rejected (must be https://): {url[:80]}")
        return False
    return True


def _probe(url: str, timeout: float) -> tuple[int | None, bool]:
    """(total_bytes, range_supported) via una richiesta di 1 byte. Il server
    risponde 206 + Content-Range se supporta i Range → parallelo possibile."""
    try:
        with httpx.stream("GET", url, headers={**_UA, "Range": "bytes=0-0"},
                          timeout=timeout, follow_redirects=True) as r:
            if str(r.url).lower().startswith("http://"):
                return None, False
            if r.status_code == 206 and "content-range" in r.headers:
                try:
                    return int(r.headers["content-range"].split("/")[-1]), True
                except (ValueError, IndexError):
                    return None, True
            cl = r.headers.get("content-length")
            return (int(cl) if cl else None), False
    except (httpx.RequestError, OSError):
        return None, False


def _fetch_chunk(url: str, tmp: Path, start: int, end: int,
                 timeout: float) -> bool:
    """Scarica il range [start,end] in `tmp` all'offset giusto, con RESUME
    interno: ogni tentativo riprende da quanto già scritto (`got`). Così anche
    un chunk che viene resettato a metà completa in pochi tentativi."""
    want = end - start + 1
    got = 0
    for _ in range(_CHUNK_ATTEMPTS):
        if got >= want:
            return True
        try:
            hdr = {**_UA, "Range": f"bytes={start + got}-{end}"}
            with httpx.stream("GET", url, headers=hdr, timeout=timeout,
                              follow_redirects=True) as r:
                if r.status_code not in (206, 200):
                    continue
                with open(tmp, "r+b") as f:
                    f.seek(start + got)
                    for b in r.iter_bytes(chunk_size=256 * 1024):
                        f.write(b)
                        got += len(b)
        except (httpx.RequestError, OSError):
            pass
        if got < want:
            time.sleep(0.3)
    return got >= want


def _download_parallel(url: str, tmp: Path, total: int, *, label: str,
                       timeout: float) -> bool:
    """Scarica `url`→`tmp` (preallocato a `total`) con chunk paralleli. Robusto
    ai reset per-flusso: nessuna singola connessione deve reggere tutto."""
    with open(tmp, "wb") as f:
        f.truncate(total)
    ranges: list[tuple[int, int]] = []
    s = 0
    while s < total:
        e = min(s + _CHUNK_BYTES - 1, total - 1)
        ranges.append((s, e))
        s = e + 1
    ok_all = True
    with ui.progress() as p:
        task = p.add_task(label, total=total)
        with ThreadPoolExecutor(max_workers=_WORKERS) as ex:
            futs = {ex.submit(_fetch_chunk, url, tmp, a, b, timeout): (a, b)
                    for a, b in ranges}
            for fut in as_completed(futs):
                a, b = futs[fut]
                try:
                    ok = fut.result()
                except Exception:  # noqa: BLE001 — un chunk morto non uccide il resto
                    ok = False
                if ok:
                    p.update(task, advance=(b - a + 1))
                else:
                    ok_all = False
    return ok_all and tmp.exists() and tmp.stat().st_size == total


def _download_stream(url: str, tmp: Path, total: int | None, *, label: str,
                     timeout: float) -> bool:
    """Stream singolo con resume da `tmp.part` (fallback: server senza Range o
    file piccoli)."""
    resume_from = tmp.stat().st_size if tmp.exists() else 0
    headers = dict(_UA)
    if resume_from:
        headers["Range"] = f"bytes={resume_from}-"
    try:
        with httpx.stream("GET", url, headers=headers, timeout=timeout,
                          follow_redirects=True) as r:
            if str(r.url).lower().startswith("http://"):
                ui.warn(f"{label}: redirect downgraded to insecure http — aborting")
                return False
            if r.status_code not in (200, 206):
                ui.warn(f"{label}: HTTP {r.status_code}")
                return False
            mode = "ab" if (r.status_code == 206 and resume_from > 0) else "wb"
            if mode == "wb":
                resume_from = 0
            with ui.progress() as p:
                task = p.add_task(label, total=total, completed=resume_from)
                with tmp.open(mode) as f:
                    for chunk in r.iter_bytes(chunk_size=64 * 1024):
                        f.write(chunk)
                        p.update(task, advance=len(chunk))
    except (httpx.RequestError, OSError) as e:
        ui.warn(f"{label}: download failed — {type(e).__name__}: {e}")
        return False
    return True


def robust_fetch(url: str, dest: Path, *, sha256: str | None = None,
                 label: str | None = None, size: int | None = None,
                 timeout: float = 60.0) -> bool:
    """Scarica `url`→`dest` resiliente ai reset per-flusso (chunk paralleli con
    resume per-chunk; stream singolo come fallback). Verifica sha256 se dato.
    Ritorna True/False; su mismatch sha il parziale è cancellato. È il core
    condiviso da `fetch` (embedder, asset) e da `llm_manager` (GGUF)."""
    label = label or dest.name
    if not _require_https(url, label):
        return False
    if not sha256:
        ui.warn(f"{label}: NO sha256 to verify — integrity NOT guaranteed")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")

    total, ranges_ok = _probe(url, timeout)
    if total is None:
        total = size

    if ranges_ok and total and total > _PARALLEL_THRESHOLD:
        ok = _download_parallel(url, tmp, total, label=label, timeout=timeout)
    else:
        ok = _download_stream(url, tmp, total, label=label, timeout=timeout)
    if not ok:
        return False

    if sha256:
        digest = _sha256_file(tmp)
        if digest != sha256.lower():
            ui.warn(f"{label}: sha256 mismatch (got {digest[:16]}…, "
                    f"expected {sha256[:16]}…) — deleting")
            tmp.unlink(missing_ok=True)
            return False

    os.replace(tmp, dest)
    ui.ok(f"{label}: {dest.stat().st_size:,} bytes → {dest}")
    return True


def fetch(asset: Asset, *, timeout: float = 60.0) -> bool:
    """Download ``asset`` to disk with a progress bar. Returns True on success.

    Idempotent: skips an already-present (sha- or size-verified) file. Delegates
    the transfer to `robust_fetch` (parallel-chunk, reset-resilient)."""
    if asset.already_present():
        ui.ok(f"{asset.name}: already present at {asset.dest}")
        return True
    return robust_fetch(asset.url, asset.dest, sha256=asset.sha256,
                        label=asset.name, size=asset.size, timeout=timeout)


def fetch_all(assets: list[Asset]) -> tuple[int, int]:
    """Download every asset. Returns (successful, failed)."""
    ok_count = 0
    fail_count = 0
    for a in assets:
        if fetch(a):
            ok_count += 1
        else:
            fail_count += 1
    return ok_count, fail_count
