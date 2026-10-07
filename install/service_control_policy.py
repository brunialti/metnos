"""Prepare the exact service-control rule before signed system activation.

The closed transition derives permissions from its authenticated catalog.
An existing conflicting or linked policy needs administrative reconciliation;
ordinary updates reuse an identical root-owned rule.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_RUNTIME = str(_ROOT / "runtime")
if _RUNTIME not in sys.path:
    sys.path.insert(0, _RUNTIME)

import services_registry  # noqa: E402

POLICY_PATH = Path("/etc/polkit-1/rules.d/49-metnos-services.rules")
POLKIT_DAEMON = Path("/usr/lib/polkit-1/polkitd")


def install(user: str, path: Path = POLICY_PATH) -> Path:
    if os.geteuid() != 0:
        raise PermissionError("run as root to install the polkit rule")
    if not POLKIT_DAEMON.is_file():
        raise FileNotFoundError("polkitd is required for system service control")
    from install.managed_infrastructure import _write_exact

    _write_exact(path, services_registry.render_polkit_rule(user))
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", required=True)
    parser.add_argument("--print", action="store_true", dest="print_only")
    args = parser.parse_args()
    if args.print_only:
        print(services_registry.render_polkit_rule(args.user), end="")
        return 0
    print(install(args.user))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
