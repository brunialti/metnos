"""A declared reverse must be implemented, one way or the other.

§2.3 makes the pattern catalogue CLOSED, but a manifest may instead point at a
`reverse()` the module implements itself. Both are legitimate; what is not
legitimate is declaring a name that is in neither place, because then the
executor ships looking reversible and the undo quietly reports "skipped".

The first draft of this guard checked only the closed catalogue and would have
flagged `delete_persons` and `delete_events`, which both implement `reverse()`
properly. Checking the wrong invariant would have been worse than not checking:
a guard that cries at correct code teaches people to silence guards.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import reverse_patterns


REPOSITORY = Path(__file__).resolve().parents[3]
_DECLARED_RE = re.compile(r'^\s*reverse_pattern\s*=\s*"([^"]+)"', re.MULTILINE)
_REVERSE_DEF_RE = re.compile(r"^def reverse\(", re.MULTILINE)


def _declarations() -> list[tuple[Path, str]]:
    found: list[tuple[Path, str]] = []
    for root in ("executors", "runtime/builtin_executor_contracts"):
        directory = REPOSITORY / root
        if not directory.is_dir():
            continue
        for manifest in sorted(directory.glob("*/manifest.toml")):
            text = manifest.read_text(encoding="utf-8")
            for declared in _DECLARED_RE.findall(text):
                found.append((manifest, declared))
    return found


def test_some_manifest_declares_a_reverse_pattern() -> None:
    """An empty inventory would make the guard below pass for free."""
    assert len(_declarations()) >= 5


@pytest.mark.parametrize(
    ("manifest", "declared"), _declarations(),
    ids=lambda item: item.parent.name if isinstance(item, Path) else str(item),
)
def test_every_declared_reverse_is_implemented(
    manifest: Path, declared: str,
) -> None:
    """Either the runtime owns the pattern, or the module owns a `reverse()`."""
    if declared in reverse_patterns.PATTERNS:
        return
    sources = sorted(manifest.parent.glob("*.py"))
    assert sources, f"{manifest.parent.name} declares '{declared}' and ships no code"
    implemented = any(
        _REVERSE_DEF_RE.search(source.read_text(encoding="utf-8"))
        for source in sources
    )
    assert implemented, (
        f"{manifest.parent.name} declares '{declared}', which is neither in the "
        f"closed catalogue {sorted(reverse_patterns.PATTERNS)} nor implemented "
        f"as a module-level `reverse()`"
    )
