#!/usr/bin/env python3
"""Build the Italian and English Metnos Quick Tour PDFs from their HTML masters."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
DOCUMENTS = {
    "it": DOCS / "it" / "Metnos_QuickTour.html",
    "en": DOCS / "en" / "Metnos_QuickTour.html",
}
PLAYWRIGHT_ROOTS = (
    Path.home() / ".local" / "share" / "metnos" / "playwright-browsers",
    Path.home() / ".cache" / "ms-playwright",
)


def find_chrome(explicit: str | None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    env_chrome = os.environ.get("METNOS_QUICKTOUR_CHROME")
    if env_chrome:
        candidates.append(Path(env_chrome).expanduser())
    for executable in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        resolved = shutil.which(executable)
        if resolved:
            candidates.append(Path(resolved))
    for root in PLAYWRIGHT_ROOTS:
        candidates.extend(
            sorted(
                root.glob("chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"),
                reverse=True,
            )
        )
        candidates.extend(
            sorted(root.glob("chromium-*/chrome-linux64/chrome"), reverse=True)
        )
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    raise SystemExit(
        "Chromium non trovato. Passa --chrome PATH oppure imposta "
        "METNOS_QUICKTOUR_CHROME."
    )


def build(language: str, chrome: Path) -> Path:
    source = DOCUMENTS[language]
    output = source.with_suffix(".pdf")
    if not source.is_file():
        raise SystemExit(f"Master mancante: {source}")
    with tempfile.TemporaryDirectory(prefix="metnos-quicktour-") as profile:
        command = [
            str(chrome),
            "--headless",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--allow-file-access-from-files",
            "--no-pdf-header-footer",
            f"--user-data-dir={profile}",
            f"--print-to-pdf={output}",
            source.resolve().as_uri(),
        ]
        completed = subprocess.run(command, text=True, capture_output=True, timeout=180)
    if completed.returncode != 0:
        sys.stderr.write(completed.stdout)
        sys.stderr.write(completed.stderr)
        raise SystemExit(f"Generazione {language.upper()} fallita ({completed.returncode})")
    if not output.is_file() or output.stat().st_size < 50_000:
        raise SystemExit(f"PDF {language.upper()} assente o troppo piccolo: {output}")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Genera i Quick Tour PDF tipografici dai master HTML IT/EN."
    )
    parser.add_argument(
        "languages",
        nargs="*",
        choices=tuple(DOCUMENTS),
        help="Lingue da generare (default: it en).",
    )
    parser.add_argument("--chrome", help="Percorso esplicito del binario Chromium.")
    args = parser.parse_args()
    chrome = find_chrome(args.chrome)
    print(f"Chromium: {chrome}")
    languages = args.languages or list(DOCUMENTS)
    for language in languages:
        output = build(language, chrome)
        print(f"{language.upper()}: {output} ({output.stat().st_size:,} byte)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
