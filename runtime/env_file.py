"""Parser minimale condiviso per file ``KEY=value`` locali.

Non interpreta shell, espansioni o comandi: questi file contengono dati e
segreti, non codice. Le righe illeggibili/malformate vengono ignorate e i
valori possono essere racchiusi fra virgolette singole o doppie.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Iterator


def iter_env(path: str | Path) -> Iterator[tuple[str, str]]:
    candidate = Path(path).expanduser()
    try:
        lines = candidate.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        yield key, value.strip().strip('"').strip("'")


def read_env(path: str | Path) -> dict[str, str]:
    """Legge un file env senza valutarlo come sintassi shell."""
    return dict(iter_env(path))


def read_first(name: str, paths: Iterable[str | Path]) -> str | None:
    """Primo valore non vuoto di ``name`` nei file, in ordine."""
    for path in paths:
        for key, value in iter_env(path):
            if key == name and value:
                return value
    return None
