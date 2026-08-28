"""Closed Group-7 administrative-operation adapter for RM-0008.

Group 6 signs the final operation vocabulary but must not make any historical
installer reachable.  The operations therefore fail before I/O until Group 7
adds the system-level implementation behind the startup authorization.
"""
from __future__ import annotations

import sys
from typing import Sequence


OPERATIONS_V1 = frozenset({
    "backup",
    "download-models",
    "install-git-hooks",
    "install-llm",
    "install-metnos",
    "install-playwright",
    "install-service-policy",
    "install-sidecar-photon",
    "install-sidecar-searxng",
    "install-sidecar-vlm",
    "migrate-syspath",
    "normalize-installed-executors",
    "post-rename-baseline",
    "post-rename-verify",
    "prompts-translator",
    "rename-myclaw",
})


def main(argv: Sequence[str] | None = None) -> int:
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    if not sys.platform.startswith("linux"):
        print("birth_ownership_platform_unsupported", file=sys.stderr)
        return 78
    if len(arguments) != 1 or arguments[0] not in OPERATIONS_V1:
        print("birth_ownership_deployment_invalid", file=sys.stderr)
        return 64
    print(
        f"birth_ownership_closed_enforcement_required: {arguments[0]}",
        file=sys.stderr,
    )
    return 78


if __name__ == "__main__":
    raise SystemExit(main())
