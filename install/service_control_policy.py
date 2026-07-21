"""Installa la policy minima per target system-level Metnos.

Le installazioni pubbliche standard usano unit user-level e non ne hanno
bisogno. Le installazioni esistenti con system unit possono eseguire:

    sudo python3 -m install.service_control_policy --user <utente>
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


def install(user: str, path: Path = POLICY_PATH) -> Path:
    if os.geteuid() != 0:
        raise PermissionError("run as root to install the polkit rule")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(services_registry.render_polkit_rule(user))
    path.chmod(0o644)
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
