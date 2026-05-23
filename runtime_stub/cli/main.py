# SPDX-License-Identifier: AGPL-3.0-only
"""``metnos`` console script — sub-command dispatcher.

Usage::

    metnos credentials add <domain>
    metnos credentials list
    metnos credentials remove <domain>
    metnos credentials fingerprint <domain>
    metnos version

This is the public entry point referenced by ``CONTRIBUTING.md`` and
the installer's phase 4. Adding new sub-commands: add a function below
with a single ``argparse.Namespace`` arg and register it in
``_DISPATCH`` at the bottom.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from runtime import credentials


def _cmd_creds_add(ns: argparse.Namespace) -> int:
    domain = ns.domain
    if not domain:
        print("domain is required", file=sys.stderr)
        return 2
    secret = getpass.getpass(f"Secret for {domain}: ")
    if not secret:
        print("empty secret, aborting", file=sys.stderr)
        return 2
    credentials.store(domain, secret, description=ns.description or "")
    print(f"stored: {domain} (fingerprint={credentials.fingerprint(domain)})")
    return 0


def _cmd_creds_list(_: argparse.Namespace) -> int:
    for d in credentials.list_domains():
        print(f"{d}  fingerprint={credentials.fingerprint(d)}")
    return 0


def _cmd_creds_remove(ns: argparse.Namespace) -> int:
    removed = credentials.delete(ns.domain)
    if removed:
        print(f"removed: {ns.domain}")
        return 0
    print(f"no such credential: {ns.domain}", file=sys.stderr)
    return 1


def _cmd_creds_fingerprint(ns: argparse.Namespace) -> int:
    print(credentials.fingerprint(ns.domain))
    return 0


def _cmd_version(_: argparse.Namespace) -> int:
    try:
        from runtime import __version__
    except ImportError:
        __version__ = "unknown"
    print(f"metnos {__version__}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="metnos", description="Metnos personal assistant — CLI.")
    sub = p.add_subparsers(dest="cmd", required=True)

    # credentials *
    p_creds = sub.add_parser("credentials", help="Manage the encrypted credential store.")
    creds_sub = p_creds.add_subparsers(dest="op", required=True)

    add_p = creds_sub.add_parser("add", help="Add or replace a credential (prompts).")
    add_p.add_argument("domain", help="Stable domain key (e.g. 'telegram_bot_token').")
    add_p.add_argument("--description", default="", help="Optional human note.")
    add_p.set_defaults(fn=_cmd_creds_add)

    list_p = creds_sub.add_parser("list", help="List domain keys (no plaintext).")
    list_p.set_defaults(fn=_cmd_creds_list)

    rm_p = creds_sub.add_parser("remove", help="Delete a credential row.")
    rm_p.add_argument("domain")
    rm_p.set_defaults(fn=_cmd_creds_remove)

    fp_p = creds_sub.add_parser("fingerprint", help="Print the non-reversible fingerprint.")
    fp_p.add_argument("domain")
    fp_p.set_defaults(fn=_cmd_creds_fingerprint)

    # version
    ver_p = sub.add_parser("version", help="Print the Metnos version.")
    ver_p.set_defaults(fn=_cmd_version)

    return p


def main() -> int:
    parser = _build_parser()
    ns = parser.parse_args()
    return ns.fn(ns)


if __name__ == "__main__":
    sys.exit(main())
