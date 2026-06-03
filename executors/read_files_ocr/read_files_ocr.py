#!/usr/bin/env python3
"""read_files_ocr — executor di Metnos v1.1.

Estrae testo da immagini o PDF scansionati via OCR (Tesseract).
Vettoriale per costruzione: una sola call processa una lista di paths.

Backend: Tesseract (open-source, self-hosted; `apt install tesseract-ocr
tesseract-ocr-ita`). Lingue installate visibili con `tesseract --list-langs`.

Per PDF: convertiti via `pdftoppm` (poppler-utils) in immagini PNG,
ognuna passata a tesseract, poi i risultati vengono concatenati con
"\\n\\n--- page N ---\\n\\n" come separatore.

Contratto:
    stdin:  JSON {paths: list[str], lang?: str = "ita+eng"}
    stdout: JSON {ok, ok_count, fail_count, entries, failed}
            entries[i] = {path, content: str, char_count: int, lang}
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from messages import get as _msg  # noqa: E402

IMG_EXTS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".gif", ".webp", ".heic"}


def _ocr_image(path, lang):
    proc = subprocess.run(
        ["tesseract", str(path), "-", "-l", lang, "--psm", "3"],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        return None, f"tesseract failed: {proc.stderr.strip()[:200]}"
    return proc.stdout, None


def _ocr_pdf(path, lang):
    if not shutil.which("pdftoppm"):
        return None, "pdftoppm not installed (apt install poppler-utils)"
    with tempfile.TemporaryDirectory() as td:
        prefix = os.path.join(td, "page")
        proc = subprocess.run(
            ["pdftoppm", "-r", "200", str(path), prefix, "-png"],
            capture_output=True, text=True, timeout=120,
        )
        if proc.returncode != 0:
            return None, f"pdftoppm failed: {proc.stderr.strip()[:200]}"
        pages = sorted(Path(td).glob("page-*.png"))
        if not pages:
            return None, "pdftoppm produced no pages"
        out_parts = []
        for n, page in enumerate(pages, 1):
            content, err = _ocr_image(page, lang)
            if err is not None:
                return None, f"page {n}: {err}"
            out_parts.append(f"--- page {n} ---\n{content.strip()}")
        return "\n\n".join(out_parts), None


def _read_one(path_arg, lang):
    path = Path(os.path.expanduser(path_arg)).resolve()
    if not path.exists():
        return None, "path does not exist"
    if not path.is_file():
        return None, "path is not a file"
    ext = path.suffix.lower()
    if ext == ".pdf":
        content, err = _ocr_pdf(path, lang)
    elif ext in IMG_EXTS:
        content, err = _ocr_image(path, lang)
    else:
        return None, f"unsupported extension '{ext}': only images (jpg/png/tiff/...) and pdf"
    if err is not None:
        return None, err
    return content, None


def invoke(args):
    paths = args.get("paths")
    lang = args.get("lang") or "ita+eng"
    if paths is None or not isinstance(paths, list):
        return {"ok": False, "error": _msg("ERR_ARG_NOT_LIST", arg="paths")}
    if not isinstance(lang, str):
        return {"ok": False, "error": _msg("ERR_ARG_NOT_STRING", arg="lang")}
    if not shutil.which("tesseract"):
        return {"ok": False, "error": _msg("ERR_TESSERACT_MISSING")}

    entries, failed = [], []
    for i, p in enumerate(paths):
        if not isinstance(p, str) or not p:
            failed.append({"index": i, "path": p, "error": _msg("ERR_ARG_NOT_NONEMPTY_STRING", arg="path")})
            continue
        content, err = _read_one(p, lang)
        if err is not None:
            failed.append({"index": i, "path": str(Path(os.path.expanduser(p)).resolve()), "error": err})
            continue
        entries.append({
            "path": str(Path(os.path.expanduser(p)).resolve()),
            "content": content,
            "char_count": len(content),
            "lang": lang,
        })

    return {
        "ok": len(failed) == 0,
        "ok_count": len(entries),
        "fail_count": len(failed),
        "entries": entries,
        "failed": failed,
    }


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({"ok": False, "error": _msg("ERR_JSON_INVALID")}))
        return
    sys.stdout.write(json.dumps(invoke(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
