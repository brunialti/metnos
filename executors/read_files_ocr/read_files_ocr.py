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

Tempo: la chiamata si ferma da sola prima del tempo che il runtime le concede
e dichiara quanti file restano (`truncated`, `cap_field="time_budget_s"`); i
file già letti restano nel risultato.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from messages import get as _msg  # noqa: E402
from executor_helpers import (  # noqa: E402
    coerce_cap, granted_seconds, run_stdio, vector_result,
)
from agentic_executor import (  # noqa: E402
    AgenticContext, AgenticLimits, AgenticProposal,
    deterministic_then_fallback_sync,
)
import prompt_loader  # noqa: E402
import vlm_client  # noqa: E402

IMG_EXTS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".gif", ".webp", ".heic"}

# The ceiling the manifest declares (`timeout_s`): used when the runtime does
# not say how long this call may last. A test keeps the two equal.
CEILING_S = 540
# Kept free at the end to write the result.
_MARGIN_S = 15
# A step is not started with less time than this: it could not finish.
_MIN_STEP_S = 5
_PAGE_TIMEOUT_S = 60
_PDF_TIMEOUT_S = 120


class _OutOfTime(Exception):
    """The time granted to this call ended before this step could finish."""


def _run(cmd, nominal_s, stop_at):
    """Run one step inside the time left; a step never outlives the call."""
    left = stop_at - time.monotonic()
    if left < _MIN_STEP_S:
        raise _OutOfTime
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=min(nominal_s, left))
    except subprocess.TimeoutExpired:
        if left < nominal_s:
            raise _OutOfTime from None
        return None


def _ocr_is_insufficient(content: str) -> bool:
    compact = "".join(ch for ch in str(content or "") if ch.isalnum())
    return len(compact) < 8


def _improve_ocr_with_agentic_fallback(path: Path, content: str,
                                        response_lang: str, stop_at: float) -> str:
    """Use the local VLM only when deterministic OCR produced little text."""
    def propose(_ctx):
        prompt = prompt_loader.get("agentic_ocr_extract", response_lang)
        result = vlm_client.describe_image(
            path, lang=response_lang, prompt=prompt, max_tokens=1024,
            deadline_at=stop_at)
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


def _ocr_image(path, lang, stop_at):
    proc = _run(["tesseract", str(path), "-", "-l", lang, "--psm", "3"],
                _PAGE_TIMEOUT_S, stop_at)
    if proc is None:
        return None, f"tesseract timed out after {_PAGE_TIMEOUT_S}s"
    if proc.returncode != 0:
        return None, f"tesseract failed: {proc.stderr.strip()[:200]}"
    return proc.stdout, None


def _ocr_pdf(path, lang, stop_at):
    if not shutil.which("pdftoppm"):
        return None, "pdftoppm not installed (apt install poppler-utils)"
    with tempfile.TemporaryDirectory() as td:
        prefix = os.path.join(td, "page")
        proc = _run(["pdftoppm", "-r", "200", str(path), prefix, "-png"],
                    _PDF_TIMEOUT_S, stop_at)
        if proc is None:
            return None, f"pdftoppm timed out after {_PDF_TIMEOUT_S}s"
        if proc.returncode != 0:
            return None, f"pdftoppm failed: {proc.stderr.strip()[:200]}"
        pages = sorted(Path(td).glob("page-*.png"))
        if not pages:
            return None, "pdftoppm produced no pages"
        out_parts = []
        for n, page in enumerate(pages, 1):
            content, err = _ocr_image(page, lang, stop_at)
            if err is not None:
                return None, f"page {n}: {err}"
            out_parts.append(f"--- page {n} ---\n{content.strip()}")
        return "\n\n".join(out_parts), None


def _read_one(path_arg, lang, stop_at):
    path = Path(os.path.expanduser(path_arg)).resolve()
    if not path.exists():
        return None, "path does not exist"
    if not path.is_file():
        return None, "path is not a file"
    ext = path.suffix.lower()
    if ext == ".pdf":
        content, err = _ocr_pdf(path, lang, stop_at)
    elif ext in IMG_EXTS:
        content, err = _ocr_image(path, lang, stop_at)
    else:
        return None, f"unsupported extension '{ext}': only images (jpg/png/tiff/...) and pdf"
    if err is not None:
        return None, err
    return content, None


def invoke(args):
    if not isinstance(args, dict):
        return {
            "ok": False,
            "error": _msg("ERR_ARGS_NOT_OBJECT"),
            "error_class": "invalid_input",
            "error_code": "args_not_object",
        }
    paths = args.get("paths")
    source = args.get("source")
    source_id = (
        str(source.get("source_id")).strip()
        if isinstance(source, dict) and source.get("source_id") is not None
        else ""
    )
    lang = args.get("lang") or "ita+eng"
    response_lang = str(
        args.get("_lang") or os.environ.get("METNOS_LANG") or "it"
    ).strip().lower().replace("_", "-")
    if not re.fullmatch(r"[a-z]{2,3}(?:-[a-z0-9]{2,8}){0,3}", response_lang):
        response_lang = "it"
    max_files = coerce_cap(args, "max_files", 20, maximum=100)
    if paths is None or not isinstance(paths, list):
        return {"ok": False, "error": _msg("ERR_ARG_NOT_LIST", arg="paths")}
    if not isinstance(lang, str):
        return {"ok": False, "error": _msg("ERR_ARG_NOT_STRING", arg="lang")}
    if not shutil.which("tesseract"):
        return {"ok": False, "error": _msg("ERR_TESSERACT_MISSING")}

    available_total = len(paths)
    selected_paths = paths[:max_files]
    granted = granted_seconds(CEILING_S)
    stop_at = time.monotonic() + granted - _MARGIN_S
    handled = len(selected_paths)
    entries, failed = [], []
    for i, p in enumerate(selected_paths):
        try:
            _read_into(entries, failed, i, p, lang, response_lang, source_id, stop_at)
        except _OutOfTime:
            handled = i
            if i == 0:
                # It had the whole time to itself and did not fit: said of
                # this file, not left among those still to read.
                failed.append(_failed_item(
                    i, p, _msg("ERR_EXECUTOR_TIMEOUT", tool="read_files_ocr",
                               seconds=int(granted)),
                    source_id, error_class="timeout"))
                handled = 1
            break

    out = vector_result(entries, failed)
    if source_id:
        out["source_id"] = source_id
    if handled < len(selected_paths):
        out.update({
            "truncated": True,
            "truncated_what": _msg("MSG_OBJECT_PATHS"),
            "used": handled,
            "available_total": available_total,
            "cap_field": "time_budget_s",
            "cap_value": int(granted),
        })
    elif available_total > len(selected_paths):
        out.update({
            "truncated": True,
            "truncated_what": _msg("MSG_OBJECT_PATHS"),
            "used": len(selected_paths),
            "available_total": available_total,
            "cap_field": "max_files",
            "cap_value": max_files,
        })
    return out


def _failed_item(i, p, error, source_id, **extra):
    item = {"index": i, "error": error, **extra}
    if source_id:
        item["source_id"] = source_id
    else:
        item["path"] = (str(Path(os.path.expanduser(p)).resolve())
                        if isinstance(p, str) and p else p)
    return item


def _read_into(entries, failed, i, p, lang, response_lang, source_id, stop_at):
    """Read one path into ``entries`` or ``failed``; ``_OutOfTime`` passes up."""
    if not isinstance(p, str) or not p:
        failed.append(_failed_item(
            i, p, _msg("ERR_ARG_NOT_NONEMPTY_STRING", arg="path"), source_id))
        return
    content, err = _read_one(p, lang, stop_at)
    if err is not None:
        failed.append(_failed_item(i, p, err, source_id))
        return
    resolved_path = Path(os.path.expanduser(p)).resolve()
    if resolved_path.suffix.lower() in IMG_EXTS:
        content = _improve_ocr_with_agentic_fallback(
            resolved_path, content, response_lang, stop_at)
    entry = {
        "content": content,
        "char_count": len(content),
        "lang": lang,
    }
    if source_id:
        entry["source_id"] = source_id
    else:
        entry["path"] = str(resolved_path)
    entries.append(entry)


def main():
    run_stdio(invoke)


if __name__ == "__main__":
    main()
