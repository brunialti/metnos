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


def _one_fetch(url: str, start: int, end: int, timeout: float) -> bytes | None:
    """Un singolo fetch COMPLETO del range [start,end] → bytes validati, o None
    (reset, troppo corto/lungo, status≠206, range sbagliato). Niente resume: un
    reset a metà fa ri-scaricare tutto il chunk (8 MB = trasferimento breve)."""
    want = end - start + 1
    try:
        with httpx.stream("GET", url, headers={**_UA, "Range": f"bytes={start}-{end}"},
                          timeout=timeout, follow_redirects=True) as r:
            # A ranged request MUST get 206; a 200 = Range ignored (whole file).
            if r.status_code != 206:
                return None
            # The proxy must serve EXACTLY the range asked for; a caching middlebox
            # can answer a stale/shifted range under the same 206.
            if not r.headers.get("content-range", "").startswith(f"bytes {start}-{end}/"):
                return None
            buf = bytearray()
            for b in r.iter_bytes(chunk_size=256 * 1024):
                buf += b
                if len(buf) > want:        # over-read → reject
                    return None
            return bytes(buf) if len(buf) == want else None
    except (httpx.RequestError, OSError):
        return None


def _fetch_chunk(url: str, fd: int, start: int, end: int, timeout: float,
                 *, consensus: bool) -> bool:
    """Scarica [start,end] e lo scrive con un unico `os.pwrite` posizionato
    (atomico, sicuro fra thread su regioni adiacenti).

    `consensus`: accetta il chunk solo quando DUE fetch indipendenti coincidono
    (sha256). Una corruzione NON deterministica del proxy/ISP (contenuto sbagliato
    sotto TLS valido, diverso a ogni passata) non si ripete identica → non passa
    il consenso. Senza `consensus` basta un fetch (rete pulita: 1× banda)."""
    for _ in range(_CHUNK_ATTEMPTS):
        a = _one_fetch(url, start, end, timeout)
        if a is None:
            time.sleep(0.3)
            continue
        if not consensus:
            os.pwrite(fd, a, start)
            return True
        b = _one_fetch(url, start, end, timeout)
        if b is not None and hashlib.sha256(a).digest() == hashlib.sha256(b).digest():
            os.pwrite(fd, a, start)
            return True
        time.sleep(0.3)
    return False


def _download_parallel(url: str, tmp: Path, total: int, *, label: str,
                       timeout: float, consensus: bool = False) -> bool:
    """Scarica `url`→`tmp` (preallocato a `total`) con chunk paralleli. Robusto
    ai reset per-flusso: nessuna singola connessione deve reggere tutto. I worker
    condividono UN fd e scrivono con `os.pwrite` (regioni adiacenti non
    block-aligned: i file object bufferizzati con seek+write si pestavano sul
    blocco di confine). `consensus` → doppio-fetch concorde per chunk."""
    fd = os.open(tmp, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        os.ftruncate(fd, total)
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
                futs = {ex.submit(_fetch_chunk, url, fd, a, b, timeout,
                                  consensus=consensus): (a, b)
                        for a, b in ranges}
                # DENTRO il `with`: avanza la barra man mano che i chunk
                # completano (fuori, l'executor.__exit__ aspetta tutto → barra
                # ferma a 0% poi salto a 100% su GGUF multi-GB).
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
    finally:
        os.close(fd)


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
    """Scarica `url`→`dest` resiliente ai reset per-flusso (chunk paralleli;
    stream singolo come fallback) E alla corruzione non deterministica del
    proxy/ISP (escalation a doppio-fetch concorde se lo sha finale non torna).
    Verifica sha256 se dato; su mismatch dopo consenso il parziale è cancellato.
    Core condiviso da `fetch` (embedder, asset) e `llm_manager` (GGUF)."""
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
    parallel = bool(ranges_ok and total and total > _PARALLEL_THRESHOLD)

    # Adaptive integrity: try the cheap single-fetch parallel path first (clean
    # networks pay 1× bandwidth). If the end-to-end sha then mismatches, a proxy/
    # ISP is mangling content non-deterministically under TLS (full-size file,
    # wrong bytes — see INSTALL_NOTES). Retry with per-chunk CONSENSUS (two
    # agreeing fetches), which random corruption cannot survive. 2 passes max.
    for attempt in range(2):
        consensus = attempt == 1
        if parallel:
            ok = _download_parallel(url, tmp, total, label=label,
                                    timeout=timeout, consensus=consensus)
        else:
            ok = _download_stream(url, tmp, total, label=label, timeout=timeout)
        if not ok:
            # incomplete (chunks never finished): a consensus retry can still
            # finish them on a flaky link; otherwise give up.
            if parallel and not consensus:
                ui.warn(f"{label}: transfer incomplete — retrying with consensus")
                continue
            return False
        if not sha256:
            break  # nothing to verify against — accept (already warned)
        digest = _sha256_file(tmp)
        if digest == sha256.lower():
            break  # verified
        if consensus:
            ui.warn(f"{label}: sha256 mismatch AFTER consensus (got {digest[:16]}…, "
                    f"expected {sha256[:16]}…) — deleting")
            tmp.unlink(missing_ok=True)
            return False
        ui.warn(f"{label}: sha256 mismatch (got {digest[:16]}…) — network is "
                f"mangling content; retrying with per-chunk consensus")

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
