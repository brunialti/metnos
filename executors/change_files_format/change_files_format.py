#!/usr/bin/env python3
"""change_files_format — converte file da un formato a un altro.

Dispatch deterministico (§7.9) via tabella `_CONVERTERS`:
chiave `(src_ext_lower, dst_ext_lower)` -> builder fn (argv list[str]).
Estendibile aggiungendo entry.

NON copre PDF/HTML -> txt: quello e' `read_files_pdf`/`read_files_html`
(vocab §2.2: estrarre contenuto da sorgente = `read`, non `change`).

Backend supportati ORA (binary disponibili sul host):
- `ffmpeg`: image (jpg/png/webp/gif), audio (mp3/wav/flac/ogg/aac),
   video (mp4/webm/mkv/mov) inter-conversion.

Backend opzionali (chiedere apt install se servono):
- imagemagick `convert`: heic/tiff/svg/bmp -> raster, png<->ico
- pandoc: docx/odt/md/html/tex inter-conversion
- libheif `heif-convert`: heic/heif -> jpg/png nativo
- libreoffice headless: doc/docx/odt/xls/xlsx/ppt -> pdf

Output: `results: list[{src, dst, ok, error?, error_class?, elapsed_ms,
size_bytes_in, size_bytes_out}]` + `summary` + truncation visibility §2.7.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from messages import get as _msg  # noqa: E402
import time
from pathlib import Path
from typing import Any


_FFMPEG_IMG_FMTS = {"jpg", "jpeg", "png", "webp", "gif", "bmp", "tiff", "tif"}
_FFMPEG_AUDIO_FMTS = {"mp3", "wav", "flac", "ogg", "aac", "m4a", "opus", "wma"}
_FFMPEG_VIDEO_FMTS = {"mp4", "webm", "mkv", "mov", "avi", "wmv", "flv", "m4v"}


def _ffmpeg_cmd(src: str, dst: str, src_ext: str, dst_ext: str,
                  quality: int | None) -> list[str]:
    """Compone argv ffmpeg context-aware (image/audio/video)."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", src]
    is_img = dst_ext in _FFMPEG_IMG_FMTS
    is_audio_out = dst_ext in _FFMPEG_AUDIO_FMTS
    is_video_out = dst_ext in _FFMPEG_VIDEO_FMTS
    src_is_video = src_ext in _FFMPEG_VIDEO_FMTS
    if is_img:
        q = quality if (quality is not None and 1 <= quality <= 31) else 2
        cmd += ["-q:v", str(q)]
    elif is_audio_out:
        bitrate = f"{quality}k" if (quality is not None and 32 <= quality <= 512) else "192k"
        if src_is_video:
            cmd += ["-vn"]
        cmd += ["-b:a", bitrate]
    elif is_video_out:
        if dst_ext == "webm":
            cmd += ["-c:v", "libvpx-vp9", "-c:a", "libopus", "-b:v", "1M"]
        elif dst_ext == "mp4":
            cmd += ["-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart",
                     "-pix_fmt", "yuv420p"]
    cmd += [dst]
    return cmd


def _heif_convert_cmd(src: str, dst: str, _src_ext: str, dst_ext: str,
                       quality: int | None) -> list[str]:
    """libheif-examples: heif-convert per HEIC/HEIF -> jpg/png."""
    cmd = ["heif-convert", src, dst]
    if dst_ext in ("jpg", "jpeg") and quality is not None and 1 <= quality <= 100:
        cmd = ["heif-convert", "-q", str(quality), src, dst]
    return cmd


def _imagemagick_cmd(src: str, dst: str, _src_ext: str, _dst_ext: str,
                      quality: int | None) -> list[str]:
    """imagemagick `convert` per TIFF/SVG/BMP/PSD/EPS/ICO inter-conversion."""
    cmd = ["convert", src]
    if quality is not None and 1 <= quality <= 100:
        cmd += ["-quality", str(quality)]
    cmd += [dst]
    return cmd


def _pandoc_cmd(src: str, dst: str, _src_ext: str, _dst_ext: str,
                  _quality: int | None) -> list[str]:
    """pandoc: docx/odt/md/html/tex/rst inter-conversion."""
    return ["pandoc", src, "-o", dst]


def _soffice_cmd(src: str, dst: str, _src_ext: str, dst_ext: str,
                   _quality: int | None) -> list[str]:
    """LibreOffice headless: office (doc/docx/odt/xls/xlsx/ppt/pptx) -> pdf.
    Output va in `dst_dir` con nome originale + nuova estensione: caller
    gestisce rename se dst_suffix != ''."""
    dst_parent = str(Path(dst).parent)
    return ["soffice", "--headless", "--convert-to", dst_ext,
             "--outdir", dst_parent, src]


_CONVERTERS: dict[tuple[str, str], Any] = {}

# Famiglia 1: immagini raster ffmpeg-supported.
for _a in _FFMPEG_IMG_FMTS:
    for _b in _FFMPEG_IMG_FMTS:
        if _a != _b:
            _CONVERTERS[(_a, _b)] = _ffmpeg_cmd

# Famiglia 2: audio + video + mix (ffmpeg).
for _a in _FFMPEG_AUDIO_FMTS | _FFMPEG_VIDEO_FMTS:
    for _b in _FFMPEG_AUDIO_FMTS | _FFMPEG_VIDEO_FMTS:
        if _a != _b:
            _CONVERTERS[(_a, _b)] = _ffmpeg_cmd

# Famiglia 3: HEIC/HEIF -> raster (libheif-examples; binary heif-convert).
# Quando manca, install-on-demand propone `libheif-examples`.
for _src_heif in ("heic", "heif"):
    for _dst_raster in ("jpg", "png"):
        _CONVERTERS[(_src_heif, _dst_raster)] = _heif_convert_cmd

# Famiglia 4: TIFF/SVG/BMP/PSD/EPS/ICO inter-conversion (imagemagick).
# convert e' superset di ffmpeg per immagini, ma sovrappone i pair:
# pongo SOLO i pair non coperti da ffmpeg.
for _src_im in ("svg", "psd", "eps", "ico"):
    for _dst_im in ("png", "jpg"):
        _CONVERTERS[(_src_im, _dst_im)] = _imagemagick_cmd

# Famiglia 5: documenti testuali (pandoc).
for _src_pd in ("docx", "odt", "md", "rst", "html", "tex"):
    for _dst_pd in ("md", "html", "docx", "odt", "rst", "tex"):
        if _src_pd != _dst_pd:
            _CONVERTERS[(_src_pd, _dst_pd)] = _pandoc_cmd

# Famiglia 6: office -> pdf (libreoffice-core, soffice headless).
for _src_off in ("doc", "docx", "odt", "xls", "xlsx", "ppt", "pptx",
                   "rtf", "ods", "odp"):
    _CONVERTERS[(_src_off, "pdf")] = _soffice_cmd


def _norm_ext(s: str) -> str:
    s = s.lower().lstrip(".").strip()
    return "jpg" if s == "jpeg" else s


def _ext_of(path: str) -> str:
    return _norm_ext(Path(path).suffix)


def _dst_path(src: str, to_format: str, dst_dir: str | None,
                dst_suffix: str) -> str:
    p = Path(src)
    stem = p.stem + dst_suffix
    parent = Path(dst_dir) if dst_dir else p.parent
    return str(parent / f"{stem}.{to_format}")


# Binary missing check: delega al helper centrale `system_binaries`
# (pattern §7.3, install-on-demand 17/5/2026). Il PYTHONPATH del service
# include `<install_root>/runtime` (settato da agent_runtime via METNOS_RUNTIME
# env + agent_runtime._run_executor PYTHONPATH augmentato), quindi l'import e' diretto.
try:
    from system_binaries import check_binary as _check_binary_central
except ImportError:
    _check_binary_central = None  # type: ignore


def _binary_missing(cmd_argv: list[str]) -> dict | None:
    """Ritorna dict {missing_binary, package, suggested_install,
    error_class, error} se il binary manca, None se installato.

    Delega a `system_binaries.check_binary`. Fallback shutil.which
    se il helper non e' importabile (test stand-alone).
    """
    bin_name = cmd_argv[0]
    if _check_binary_central is not None:
        return _check_binary_central(bin_name)
    # Fallback
    if shutil.which(bin_name) is None:
        return {
            "error_class": "binary_missing",
            "missing_binary": bin_name,
            "package": bin_name,
            "suggested_install": f"sudo apt install -y {bin_name}",
            "error": _msg("ERR_PACKAGE_NOT_FOUND", name=bin_name),
        }
    return None


def invoke(args: dict | None = None, **kwargs: Any) -> dict[str, Any]:
    if args is None:
        args = kwargs
    paths = args.get("paths") or []
    to_format = args.get("to_format") or ""
    dst_dir = args.get("dst_dir")
    dst_suffix = args.get("dst_suffix", "")
    overwrite = bool(args.get("overwrite", False))
    quality = args.get("quality")
    max_files = int(args.get("max_files", 50))
    to_format = _norm_ext(to_format)
    if not to_format:
        return {"ok": False, "error": _msg("ERR_TO_FORMAT_REQUIRED"),
                  "error_class": "missing_arg"}
    if not paths:
        return {"ok": True, "results": [], "summary": "Nessun file da convertire."}

    available_total = len(paths)
    truncated = available_total > max_files
    in_paths = paths[:max_files]

    results = []
    if dst_dir:
        Path(dst_dir).mkdir(parents=True, exist_ok=True)

    for src in in_paths:
        t0 = time.time()
        src_ext = _ext_of(src)
        if not src_ext:
            results.append({"src": src, "dst": None, "ok": False,
                             "error": _msg("ERR_SRC_EXT_UNKNOWN"),
                             "error_class": "no_src_ext"})
            continue
        if src_ext == to_format:
            results.append({"src": src, "dst": src, "ok": True,
                             "error": _msg("ERR_FORMAT_SAME"),
                             "error_class": "noop_same_format",
                             "elapsed_ms": 0})
            continue
        if not Path(src).is_file():
            results.append({"src": src, "dst": None, "ok": False,
                             "error": _msg("ERR_PATH_NOT_FOUND", path=src),
                             "error_class": "src_not_found"})
            continue
        key = (src_ext, to_format)
        builder = _CONVERTERS.get(key)
        if builder is None:
            results.append({"src": src, "dst": None, "ok": False,
                             "error": _msg("ERR_FORMAT_UNSUPPORTED", src=src_ext, dst=to_format),
                             "error_class": "unsupported_pair"})
            continue
        dst = _dst_path(src, to_format, dst_dir, dst_suffix)
        if Path(dst).exists() and not overwrite:
            results.append({"src": src, "dst": dst, "ok": False,
                             "error": _msg("ERR_DST_EXISTS", path=dst),
                             "error_class": "dst_exists"})
            continue
        cmd_argv = builder(src, dst, src_ext, to_format, quality)
        missing = _binary_missing(cmd_argv)
        if missing:
            results.append({"src": src, "dst": None, "ok": False, **missing})
            continue
        try:
            proc = subprocess.run(cmd_argv, capture_output=True, text=True,
                                   timeout=300)
            if proc.returncode != 0:
                err = (proc.stderr or proc.stdout or "").strip()[:500]
                results.append({"src": src, "dst": None, "ok": False,
                                 "error": err or f"exit {proc.returncode}",
                                 "error_class": "converter_failed",
                                 "elapsed_ms": int((time.time() - t0) * 1000)})
                continue
            size_in = Path(src).stat().st_size if Path(src).exists() else 0
            size_out = Path(dst).stat().st_size if Path(dst).exists() else 0
            results.append({"src": src, "dst": dst, "ok": True,
                             "elapsed_ms": int((time.time() - t0) * 1000),
                             "size_bytes_in": size_in,
                             "size_bytes_out": size_out})
        except subprocess.TimeoutExpired:
            results.append({"src": src, "dst": None, "ok": False,
                             "error": _msg("ERR_TIMEOUT"),
                             "error_class": "timeout"})
        except OSError as e:
            results.append({"src": src, "dst": None, "ok": False,
                             "error": str(e),
                             "error_class": "os_error"})

    ok_count = sum(1 for r in results if r["ok"])
    fail_count = len(results) - ok_count
    summary = (_msg("MSG_CONVERT_SUMMARY", ok=ok_count, total=len(results), fmt=to_format)
                + (_msg("MSG_CONVERT_FAILED", n=fail_count) if fail_count else ""))

    out: dict[str, Any] = {
        "ok": True,
        "results": results,
        "ok_count": ok_count,
        "summary": summary,
    }
    if truncated:
        out.update({
            "truncated": True,
            "truncated_what": "paths",
            "used": len(in_paths),
            "available_total": available_total,
            "cap_field": "max_files",
            "cap_value": max_files,
        })
    return out


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({"ok": False, "error": _msg("ERR_JSON_INVALID")}))
        return
    sys.stdout.write(json.dumps(invoke(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
