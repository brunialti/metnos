# SPDX-License-Identifier: AGPL-3.0-only
"""Streaming HTTP download with progress + sha256 verify.

Built on httpx so we get HTTP/2 and decent timeout semantics for free.
Resumable downloads via Range header when the server supports it and we
already have a partial file on disk; otherwise re-downloads from zero.

A failed sha256 results in the partial file being deleted, so a re-run
starts fresh rather than perpetuating a corrupted file.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

import httpx

from . import ui


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
        # No checksum to verify against → trust an existing same-size file
        if self.size and self.dest.stat().st_size == self.size:
            return True
        return False


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


def fetch(asset: Asset, *, timeout: float = 60.0) -> bool:
    """Download ``asset`` to disk with a progress bar.

    Returns True on success, False on failure (caller decides whether
    failure is fatal). On checksum mismatch, the partial file is
    deleted. Rejects non-https URLs and warns loudly when no checksum
    is available to verify against.
    """
    if not _require_https(asset.url, asset.name):
        return False
    if not asset.sha256:
        ui.warn(f"{asset.name}: NO sha256 to verify — integrity NOT guaranteed")

    if asset.already_present():
        ui.ok(f"{asset.name}: already present at {asset.dest}")
        return True

    asset.dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = asset.dest.with_suffix(asset.dest.suffix + ".part")

    # Detect partial download for resume
    resume_from = 0
    headers: dict[str, str] = {"User-Agent": "metnos-installer/1.0"}
    if tmp.exists():
        resume_from = tmp.stat().st_size
        headers["Range"] = f"bytes={resume_from}-"

    try:
        with httpx.stream("GET", asset.url, headers=headers, timeout=timeout, follow_redirects=True) as r:
            if str(r.url).lower().startswith("http://"):
                # A redirect downgraded https→http: refuse the body.
                ui.warn(f"{asset.name}: redirect downgraded to insecure http — aborting")
                return False
            if r.status_code not in (200, 206):
                ui.warn(f"{asset.name}: HTTP {r.status_code}")
                return False

            # Total size: from Content-Length (when 200) or Content-Range (when 206 resume)
            total = asset.size
            if total is None:
                if r.status_code == 206 and "content-range" in r.headers:
                    # "bytes 1024-12345/12346" → total = 12346
                    try:
                        total = int(r.headers["content-range"].split("/")[-1])
                    except (ValueError, IndexError):
                        total = None
                else:
                    cl = r.headers.get("content-length")
                    total = int(cl) + resume_from if cl else None

            mode = "ab" if r.status_code == 206 and resume_from > 0 else "wb"
            if mode == "wb" and resume_from > 0:
                # Server ignored our Range → restart from 0
                resume_from = 0

            with ui.progress() as p:
                task = p.add_task(asset.name, total=total, completed=resume_from)
                with tmp.open(mode) as f:
                    for chunk in r.iter_bytes(chunk_size=64 * 1024):
                        f.write(chunk)
                        p.update(task, advance=len(chunk))
    except (httpx.RequestError, OSError) as e:
        ui.warn(f"{asset.name}: download failed — {type(e).__name__}: {e}")
        return False

    # Verify
    if asset.sha256:
        digest = _sha256_file(tmp)
        if digest != asset.sha256.lower():
            ui.warn(f"{asset.name}: sha256 mismatch (got {digest[:16]}…, expected {asset.sha256[:16]}…) — deleting")
            tmp.unlink(missing_ok=True)
            return False

    # Atomic rename
    os.replace(tmp, asset.dest)
    ui.ok(f"{asset.name}: {asset.dest.stat().st_size:,} bytes → {asset.dest}")
    return True


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
