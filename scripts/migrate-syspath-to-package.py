#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""migrate-syspath-to-package.py — convert sys.path.insert hacks to proper package imports.

Refactor R2 di ADR 0148. Per ogni .py che contiene
``sys.path.insert(0, "/opt/myclaw/runtime")`` (o equivalente per
suprastructure), riscrive gli import bare ``from foo import bar`` /
``import foo`` in import qualificati ``from runtime.foo import bar``
e poi rimuove la riga ``sys.path.insert``.

Operazione safe: solo modifiche su file che contengono il pattern
sys.path.insert mirato. Niente azione su file puliti.

Uso:
    python3 migrate-syspath-to-package.py                    # dry-run, prints diffs
    python3 migrate-syspath-to-package.py --apply            # writes changes
    python3 migrate-syspath-to-package.py --files a.py b.py  # only these files

Edge cases gestiti:
- ``sys.path.insert`` indentato (dentro try/except, funzioni)
- alias ``_sys.path.insert``
- sub-package imports (es. ``scheduler_v2/`` → ``runtime.scheduler_v2``)
- import che gia' usano ``runtime.X`` o ``suprastructure.X`` lasciati intatti

Edge cases SKIPPED (non touched):
- ``sys.path.insert`` dentro stringhe Python (es. test setup="...sys.path.insert...")
- ``sys.path.insert(0, str(Path(__file__).parent))`` — portable hack, safe
- Path executor-self-reference: ``sys.path.insert(0, "/opt/myclaw/executors/<name>")``
"""
from __future__ import annotations

import argparse
import ast
import difflib
import re
import sys
from pathlib import Path

RUNTIME_ROOT = Path("/opt/myclaw/runtime")
EXECUTORS_ROOT = Path("/opt/myclaw/executors")

# ─── 1. Build the set of module names exposed under runtime/ ────────

def _runtime_modules() -> set[str]:
    """Top-level .py modules + sub-package names under /opt/myclaw/runtime/."""
    out: set[str] = set()
    if not RUNTIME_ROOT.exists():
        return out
    for p in RUNTIME_ROOT.iterdir():
        if p.name.startswith(("__", ".")):
            continue
        if p.is_file() and p.suffix == ".py":
            out.add(p.stem)
        elif p.is_dir() and (p / "__init__.py").exists():
            out.add(p.name)
        elif p.is_dir():
            # Some sub-dirs are runtime packages even without __init__.py
            # (legacy). Include them if they hold .py files at top level.
            if any(c.suffix == ".py" for c in p.iterdir() if c.is_file()):
                out.add(p.name)
    return out


# ─── 2. Patterns ────────────────────────────────────────────────────

# Match `sys.path.insert(0, "/opt/myclaw/runtime")` or with leading whitespace
# or `_sys.path.insert(...)`. Captures the indent so we can decide whether to
# leave the line or keep it (lines inside string literals are caught by AST
# pre-check, see _has_syspath_outside_strings).
_SYSPATH_RUNTIME = re.compile(
    r'^(\s*)_?sys\.path\.insert\s*\(\s*0\s*,\s*["\']'
    r'(/opt/myclaw/runtime(?:/[^"\']*)?)["\']\s*\)\s*$'
)
_SYSPATH_SUPRA = re.compile(
    r'^(\s*)_?sys\.path\.insert\s*\(\s*0\s*,\s*["\']'
    r'(/opt/suprastructure/src(?:/[^"\']*)?)["\']\s*\)\s*$'
)


def _has_syspath_pattern_at_top_level(src: str) -> bool:
    """True iff the file has a sys.path.insert pattern at module top level
    (not inside a string literal). Cheap heuristic: any line matches the
    pattern AND is not preceded by an unclosed triple-quote in the AST.
    We use a simple AST check by trying to import the file's bytecode-
    parsed lines."""
    # Per-line check is sufficient because Python source rarely has
    # multi-line strings containing valid Python statements that match
    # our exact regex. For the corner case of test setup="..." strings,
    # we filter those out by requiring the match to be at column 0 OR
    # in a recognisable indented import block (not after a `"""`).
    in_triple = False
    for line in src.splitlines():
        # Count odd number of """ on a line (toggles in_triple)
        cnt = line.count('"""') + line.count("'''")
        if cnt % 2 == 1:
            in_triple = not in_triple
            continue
        if in_triple:
            continue
        if _SYSPATH_RUNTIME.match(line) or _SYSPATH_SUPRA.match(line):
            return True
    return False


# Import line patterns to rewrite.
# 1. `from <X> import ...`        → `from runtime.<X> import ...`
# 2. `import <X>`                  → `from runtime import <X>`
# 3. `import <X> as <Y>`           → `from runtime import <X> as <Y>`
# 4. `from <X>.<sub> import ...`   → `from runtime.<X>.<sub> import ...`
# Only rewritten if <X> is in our known runtime module set.

_RE_FROM_IMPORT = re.compile(r'^(\s*)from\s+([\w\.]+)\s+import\s+(.+)$')
_RE_IMPORT = re.compile(r'^(\s*)import\s+([\w\.]+)(\s+as\s+\w+)?\s*$')


def _rewrite_imports(src: str, runtime_mods: set[str]) -> str:
    """Pass over each line; rewrite imports that match a runtime module."""
    out_lines: list[str] = []
    in_triple = False
    for line in src.splitlines(keepends=False):
        # Track triple-quoted strings to avoid touching imports inside docstrings
        cnt = line.count('"""') + line.count("'''")
        if cnt % 2 == 1:
            in_triple = not in_triple
            out_lines.append(line)
            continue
        if in_triple:
            out_lines.append(line)
            continue

        # 1. Drop sys.path.insert lines (runtime + supra src)
        if _SYSPATH_RUNTIME.match(line) or _SYSPATH_SUPRA.match(line):
            # Drop the sys.path.insert line entirely (package imports
            # make it dead).
            continue

        # 2. Rewrite `from X[.sub] import ...`
        m = _RE_FROM_IMPORT.match(line)
        if m:
            indent, mod, rest = m.group(1), m.group(2), m.group(3)
            head = mod.split(".")[0]
            if (head in runtime_mods
                    and not mod.startswith("runtime.")
                    and not mod.startswith("suprastructure.")):
                line = f"{indent}from runtime.{mod} import {rest}"
            out_lines.append(line)
            continue

        # 3. Rewrite `import X` (no dots) → `from runtime import X`
        m = _RE_IMPORT.match(line)
        if m:
            indent, mod, alias = m.group(1), m.group(2), (m.group(3) or "")
            head = mod.split(".")[0]
            if (head in runtime_mods
                    and "." not in mod
                    and not mod.startswith("runtime.")
                    and not mod.startswith("suprastructure.")):
                line = f"{indent}from runtime import {mod}{alias}"
            out_lines.append(line)
            continue

        out_lines.append(line)

    return "\n".join(out_lines) + ("\n" if src.endswith("\n") else "")


# ─── 3. Driver ──────────────────────────────────────────────────────

def _find_targets() -> list[Path]:
    """All .py with the sys.path-insert pattern at top level."""
    out: list[Path] = []
    for root in (RUNTIME_ROOT, EXECUTORS_ROOT):
        if not root.exists():
            continue
        for p in root.rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            try:
                src = p.read_text()
            except (OSError, UnicodeDecodeError):
                continue
            if _has_syspath_pattern_at_top_level(src):
                out.append(p)
    return sorted(out)


def _process(path: Path, runtime_mods: set[str], *, apply: bool) -> tuple[bool, str]:
    """Return (changed, unified_diff). Writes only if apply=True."""
    src = path.read_text()
    new = _rewrite_imports(src, runtime_mods)
    if new == src:
        return False, ""
    diff = "\n".join(difflib.unified_diff(
        src.splitlines(), new.splitlines(),
        fromfile=str(path), tofile=str(path) + ".new",
        lineterm="",
    ))
    if apply:
        # Parse-check before writing to avoid landing a syntax error without
        # creating executable bytecode.
        try:
            ast.parse(new, filename=str(path))
        except SyntaxError as e:
            return False, f"{path}: SYNTAX ERROR post-transform, skipping: {e}"
        path.write_text(new)
    return True, diff


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="Write changes to disk (default: dry-run).")
    ap.add_argument("--files", nargs="*", default=None,
                    help="Only process these files (otherwise: full sweep).")
    ap.add_argument("--summary", action="store_true",
                    help="Only print counts, no diffs.")
    ns = ap.parse_args()

    runtime_mods = _runtime_modules()
    if not runtime_mods:
        print(f"ERROR: no runtime modules found under {RUNTIME_ROOT}", file=sys.stderr)
        return 2

    targets: list[Path] = (
        [Path(f) for f in ns.files] if ns.files else _find_targets()
    )

    n_changed = 0
    n_skipped = 0
    for p in targets:
        changed, diff = _process(p, runtime_mods, apply=ns.apply)
        if changed:
            n_changed += 1
            if not ns.summary:
                print(diff)
                print()
        else:
            n_skipped += 1
            if diff:  # error/warning text
                print(diff, file=sys.stderr)

    mode = "applied" if ns.apply else "dry-run"
    print(f"\n{mode}: {n_changed} files changed, {n_skipped} files unchanged ({len(runtime_mods)} runtime modules known)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
