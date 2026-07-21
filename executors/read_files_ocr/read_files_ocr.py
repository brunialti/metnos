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
from executor_helpers import run_stdio  # noqa: E402
from agentic_executor import (  # noqa: E402
    AgenticContext, AgenticLimits, AgenticProposal,
    deterministic_then_fallback_sync,
)
import prompt_loader  # noqa: E402
import vlm_client  # noqa: E402

IMG_EXTS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".gif", ".webp", ".heic"}


def _ocr_is_insufficient(content: str) -> bool:
    compact = "".join(ch for ch in str(content or "") if ch.isalnum())
    return len(compact) < 8


def _improve_ocr_with_agentic_fallback(path: Path, content: str,
                                        response_lang: str) -> str:
    """Use the local VLM only when deterministic OCR produced little text."""
    def propose(_ctx):
        prompt = prompt_loader.get("agentic_ocr_extract", response_lang)
        result = vlm_client.describe_image(
            path, lang=response_lang, prompt=prompt, max_tokens=1024)
        if result.get("_vlm_error"):
            return None
        text = str(result.get("description") or "").strip()
        return AgenticProposal(text) if text else None

    return deterministic_then_fallback_sync(
        deterministic=lambda: content,
        needs_fallback=_ocr_is_insufficient,
        context=lambda primary: AgenticContext(
            goal={"operation": "verbatim_ocr"},
            observed={"path_suffix": path.suffix.lower(),
                      "deterministic_char_count": len(primary or "")},
            constraints={"verbatim_only": True},
        ),
        propose=propose,
        execute=lambda proposal, _ctx: str(proposal.action),
        validate=lambda proposal, _ctx: bool(str(proposal.action).strip()),
        limits=AgenticLimits(max_attempts=1),
        postcondition=lambda result, _ctx: (
            not _ocr_is_insufficient(str(result or ""))),
    )


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
    response_lang = str(args.get("_lang") or "it").split("-", 1)[0].lower()
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
        resolved_path = Path(os.path.expanduser(p)).resolve()
        if resolved_path.suffix.lower() in IMG_EXTS:
            content = _improve_ocr_with_agentic_fallback(
                resolved_path, content, response_lang)
        entries.append({
            "path": str(resolved_path),
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
    run_stdio(invoke)


if __name__ == "__main__":
    main()
