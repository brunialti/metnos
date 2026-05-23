"""runtime.agent_mirror — mirror server-side per il client metnos-client.

Espone tre famiglie di endpoint dietro `/agent/`:

- `/agent/pypi/simple/{package}/` → indice PEP 503 con link locali ai wheel
- `/agent/pypi/files/{package}/{filename}` → wheel binario (cache hash-keyed,
  download lazy da PyPI alla prima richiesta, hash verificato)
- `/agent/runtime/{filename}` → tarball python-build-standalone (file statico
  pre-popolato in MIRROR_RUNTIME_DIR)
- `/agent/client/manifest.json` + `/agent/client/{filename}` → binari del
  metnos-client (file statici in MIRROR_CLIENT_DIR)

Tutti gli endpoint sono read-only e idempotenti. Audit log JSONL per ogni
hit/miss/serve. Single-flight per filename via asyncio.Lock per evitare
download paralleli dello stesso wheel.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web

import config as _C  # §7.11

log = logging.getLogger("metnos.mirror")

# Storage roots
MIRROR_ROOT = Path(os.environ.get(
    "METNOS_MIRROR_ROOT",
    str(_C.PATH_USER_DATA / "mirror"),
))
WHEEL_CACHE_DIR = MIRROR_ROOT / "wheel-cache"
INDEX_CACHE_DIR = MIRROR_ROOT / "wheel-index-cache"
MIRROR_RUNTIME_DIR = MIRROR_ROOT / "runtime"
MIRROR_CLIENT_DIR = MIRROR_ROOT / "client"
AUDIT_LOG = MIRROR_ROOT / "mirror-audit.jsonl"

# Upstream
UPSTREAM_PYPI = os.environ.get("METNOS_UPSTREAM_PYPI", "https://pypi.org/simple")
INDEX_TTL_S = int(os.environ.get("METNOS_INDEX_TTL_S", "3600"))

# PEP 503 normalisation
_NORM_RE = re.compile(r"[-_.]+")


def _normalize(name: str) -> str:
    return _NORM_RE.sub("-", name).lower()


_PACKAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_FILENAME_RE = re.compile(r"^[A-Za-z0-9._+-]+$")


def _safe_package(name: str) -> str:
    if not _PACKAGE_RE.match(name):
        raise web.HTTPBadRequest(reason="invalid package name")
    return _normalize(name)


def _safe_filename(name: str) -> str:
    if not _FILENAME_RE.match(name) or ".." in name or "/" in name:
        raise web.HTTPBadRequest(reason="invalid filename")
    return name


# --- audit ----------------------------------------------------------------

def _audit(event: str, **kw: Any) -> None:
    try:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                  "event": event, **kw}
        with open(AUDIT_LOG, "a") as f:
            f.write(json.dumps(record, separators=(",", ":")) + "\n")
    except Exception:
        log.exception("audit write failed")


# --- single-flight per nome file -----------------------------------------

class _FileLocks:
    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    async def get(self, key: str) -> asyncio.Lock:
        async with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            return lock


_locks = _FileLocks()


# --- PEP 503 simple index handler ----------------------------------------

async def simple_index(request: web.Request) -> web.Response:
    package_raw = request.match_info["package"]
    package = _safe_package(package_raw)

    cache_path = INDEX_CACHE_DIR / f"{package}.json"
    fresh = (
        cache_path.exists()
        and (time.time() - cache_path.stat().st_mtime) < INDEX_TTL_S
    )

    if not fresh:
        try:
            data = await _fetch_simple_json(package)
            INDEX_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            tmp = cache_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data))
            tmp.replace(cache_path)
            _audit("index_fetch", package=package, files=len(data.get("files", [])))
        except web.HTTPException:
            raise
        except Exception:
            log.exception("upstream index fetch failed for %s", package)
            if not cache_path.exists():
                raise web.HTTPBadGateway(reason="upstream index unreachable")
            _audit("index_stale_served", package=package)
    try:
        data = json.loads(cache_path.read_text())
    except Exception:
        raise web.HTTPInternalServerError(reason="index cache corrupted")

    html = _render_pep503_index(package, data.get("files", []))
    _audit("index_serve", package=package)
    return web.Response(
        text=html, status=200,
        headers={
            "Content-Type": "text/html; charset=utf-8",
            "Cache-Control": "no-store",
        },
    )


async def _fetch_simple_json(package: str) -> dict:
    url = f"{UPSTREAM_PYPI}/{package}/"
    headers = {"Accept": "application/vnd.pypi.simple.v1+json"}
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        async with s.get(url, headers=headers) as r:
            if r.status == 404:
                raise web.HTTPNotFound(reason="package not found upstream")
            if r.status >= 400:
                raise web.HTTPBadGateway(reason=f"upstream {r.status}")
            return await r.json(content_type=None)


def _render_pep503_index(package: str, files: list[dict]) -> str:
    """Genera HTML PEP 503 con link locali. files: lista {filename, url, hashes}."""
    out = [
        "<!DOCTYPE html>",
        "<html><head>",
        f"<title>Links for {package}</title>",
        '<meta name="pypi:repository-version" content="1.0">',
        "</head><body>",
        f"<h1>Links for {package}</h1>",
    ]
    for f in files:
        fname = f.get("filename", "")
        if not fname or "/" in fname or ".." in fname:
            continue
        sha = (f.get("hashes") or {}).get("sha256", "")
        local = f"/agent/pypi/files/{package}/{fname}"
        if sha:
            local += f"#sha256={sha}"
        requires_python = f.get("requires-python")
        attrs = ""
        if requires_python:
            # PEP 503 vuole l'attributo data-requires-python con HTML escape
            esc = requires_python.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
            attrs = f' data-requires-python="{esc}"'
        out.append(f'<a href="{local}"{attrs}>{fname}</a><br>')
    out.append("</body></html>")
    return "\n".join(out)


# --- wheel file handler ---------------------------------------------------

async def wheel_file(request: web.Request) -> web.Response:
    package = _safe_package(request.match_info["package"])
    filename = _safe_filename(request.match_info["filename"])

    cache_path = WHEEL_CACHE_DIR / package / filename
    if cache_path.exists():
        _audit("wheel_hit", package=package, filename=filename, size=cache_path.stat().st_size)
        return _serve_file(cache_path)

    # cache miss: trova URL e hash dall'index
    info = await _lookup_wheel(package, filename)
    if info is None:
        raise web.HTTPNotFound(reason="wheel not found in upstream index")
    upstream_url = info["url"]
    expected_sha = (info.get("hashes") or {}).get("sha256")

    lock = await _locks.get(f"{package}/{filename}")
    async with lock:
        if cache_path.exists():
            _audit("wheel_hit_after_lock", package=package, filename=filename)
            return _serve_file(cache_path)
        try:
            await _download_wheel(upstream_url, cache_path, expected_sha)
        except web.HTTPException:
            raise
        except Exception as e:
            log.exception("wheel download failed for %s/%s", package, filename)
            raise web.HTTPBadGateway(reason="wheel download failed") from e
    _audit("wheel_miss_fetched", package=package, filename=filename,
           size=cache_path.stat().st_size, sha256=expected_sha)
    return _serve_file(cache_path)


async def _lookup_wheel(package: str, filename: str) -> dict | None:
    cache_path = INDEX_CACHE_DIR / f"{package}.json"
    fresh = (
        cache_path.exists()
        and (time.time() - cache_path.stat().st_mtime) < INDEX_TTL_S
    )
    if not fresh:
        with contextlib.suppress(Exception):
            data = await _fetch_simple_json(package)
            INDEX_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            tmp = cache_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data))
            tmp.replace(cache_path)

    if not cache_path.exists():
        return None
    try:
        data = json.loads(cache_path.read_text())
    except Exception:
        return None
    for f in data.get("files", []):
        if f.get("filename") == filename:
            return f
    return None


async def _download_wheel(url: str, dest: Path, expected_sha: str | None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    h = hashlib.sha256()
    timeout = aiohttp.ClientTimeout(total=600)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        async with s.get(url) as r:
            if r.status >= 400:
                raise web.HTTPBadGateway(reason=f"upstream wheel {r.status}")
            with open(tmp, "wb") as out:
                async for chunk in r.content.iter_chunked(64 * 1024):
                    h.update(chunk)
                    out.write(chunk)
    got = h.hexdigest()
    if expected_sha and got != expected_sha:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
        raise web.HTTPBadGateway(reason="wheel hash mismatch")
    tmp.replace(dest)


def _serve_file(path: Path, content_type: str | None = None) -> web.Response:
    return web.FileResponse(path, headers={
        "Cache-Control": "public, max-age=31536000, immutable",
        "X-Metnos-Cache": "hit",
        **({"Content-Type": content_type} if content_type else {}),
    })


# --- runtime tarball + client binaries (static) --------------------------

async def runtime_file(request: web.Request) -> web.Response:
    filename = _safe_filename(request.match_info["filename"])
    p = MIRROR_RUNTIME_DIR / filename
    if not p.is_file():
        raise web.HTTPNotFound(reason="runtime file not present")
    _audit("runtime_serve", filename=filename, size=p.stat().st_size)
    return _serve_file(p)


async def client_manifest(request: web.Request) -> web.Response:
    p = MIRROR_CLIENT_DIR / "manifest.json"
    if not p.is_file():
        raise web.HTTPNotFound(reason="client manifest not present")
    _audit("client_manifest_serve")
    return web.FileResponse(p, headers={
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
    })


async def client_file(request: web.Request) -> web.Response:
    filename = _safe_filename(request.match_info["filename"])
    p = MIRROR_CLIENT_DIR / filename
    if not p.is_file():
        raise web.HTTPNotFound(reason="client binary not present")
    _audit("client_serve", filename=filename, size=p.stat().st_size)
    return _serve_file(p)


# --- registration helper --------------------------------------------------

def register_routes(app: web.Application) -> None:
    """Aggancia gli handler del mirror a una app aiohttp esistente."""
    app.router.add_get("/agent/pypi/simple/{package}/", simple_index)
    app.router.add_get("/agent/pypi/files/{package}/{filename}", wheel_file)
    app.router.add_get("/agent/runtime/{filename}", runtime_file)
    app.router.add_get("/agent/client/manifest.json", client_manifest)
    app.router.add_get("/agent/client/{filename}", client_file)
