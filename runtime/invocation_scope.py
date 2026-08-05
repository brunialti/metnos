"""Signed, opt-in filesystem scope preflight for live executor invocations.

Bubblewrap is the enforcement boundary when available, but an installation may
run without it.  A filesystem capability can therefore annotate the typed
arguments that carry concrete path authority:

    path_args = ["dst_dir"]
    parent_path_args = ["paths"]

The first form checks each value itself; the second checks its parent (useful
when an executor derives an output beside an input file).  Unannotated legacy
executors preserve their current behaviour.  The annotation lives inside the
signed capability and can only narrow its declared hints.
"""
from __future__ import annotations

import os
import fnmatch
from pathlib import Path


_ANNOTATIONS = ("path_args", "parent_path_args")


def _values(raw) -> list[str]:
    values = raw if isinstance(raw, list) else [raw]
    return [value for value in values
            if isinstance(value, str) and value.strip()]


def _resolved(path: str) -> Path:
    candidate = Path(os.path.expanduser(path))
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    # strict=False resolves every existing symlink component and normalizes
    # ``..`` without requiring a not-yet-created destination to exist.
    return candidate.resolve(strict=False)


def _within(path: Path, hint: str) -> bool:
    if not isinstance(hint, str) or not hint.strip() or hint.startswith("arg:"):
        return False
    expanded = os.path.expanduser(hint.strip())
    if not expanded or not os.path.isabs(expanded):
        return False
    if expanded.endswith("/**"):
        root = Path(expanded[:-3]).resolve(strict=False)
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False
    if not any(marker in expanded for marker in ("*", "?", "[")):
        return path == Path(expanded).resolve(strict=False)
    # Resolve the candidate first so an existing symlink cannot smuggle an
    # out-of-scope target through an in-scope textual prefix.
    return fnmatch.fnmatchcase(str(path), os.path.abspath(expanded))


def check_invocation_scope(executor, args, *, actor: str = "host") -> str | None:
    """Return a stable denial reason, or ``None`` when the preflight passes.

    Only annotated effective filesystem capabilities participate.  Invalid
    annotations fail closed at standard admission; this function remains
    defensive for legacy or directly-constructed executor objects.
    """
    if not isinstance(args, dict):
        return None
    from capabilities import effective_capabilities
    from workspace_policy import effective_hints

    capabilities = effective_capabilities(
        getattr(executor, "capabilities", None) or [],
        getattr(executor, "args_schema", None) or {},
        args,
    )
    for capability in capabilities:
        name = capability.get("name") or ""
        if not name.startswith("fs:"):
            continue
        annotated = any(capability.get(key) for key in _ANNOTATIONS)
        if not annotated:
            continue
        hints = capability.get("hint") or []
        scope, excludes = effective_hints(actor or "host", name, hints)
        for field in capability.get("path_args") or []:
            for raw in _values(args.get(field)):
                candidate = _resolved(raw)
                if not any(_within(candidate, hint) for hint in scope):
                    return (f"outside allowed scope: {candidate} is not "
                            f"authorized by {name}")
                if any(_within(candidate, hint) for hint in excludes):
                    return (f"outside allowed scope: {candidate} is excluded "
                            f"from {name}")
        for field in capability.get("parent_path_args") or []:
            for raw in _values(args.get(field)):
                candidate = _resolved(raw).parent
                if not any(_within(candidate, hint) for hint in scope):
                    return (f"outside allowed scope: {candidate} is not "
                            f"authorized by {name}")
                if any(_within(candidate, hint) for hint in excludes):
                    return (f"outside allowed scope: {candidate} is excluded "
                            f"from {name}")
    return None
