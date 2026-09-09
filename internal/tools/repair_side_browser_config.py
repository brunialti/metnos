#!/usr/bin/python3.12
"""Create only the missing Chrome configuration leaf on the affected host.

No service restart, existing directory changes or credential access.
The product fix gives child browsers explicit application-scoped XDG roots.
"""
from __future__ import annotations

import os
from pathlib import Path
import pwd
import stat
import sys


def main() -> None:
    if os.geteuid() != 0 or len(sys.argv) != 1:
        raise RuntimeError("root and no arguments required")
    account = pwd.getpwnam("metnos")
    home = Path(account.pw_dir)
    if home != Path("/var/lib/metnos-service"):
        raise RuntimeError("unexpected service home")
    parent = home / ".config"
    for path in (parent, *parent.parents):
        info = path.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0
                or info.st_gid != 0 or info.st_mode & 0o022):
            raise RuntimeError("unprotected configuration parent")
    target = parent / "google-chrome-for-testing"
    if not os.path.lexists(target):
        target.mkdir(mode=0o700)
        os.chown(target, account.pw_uid, account.pw_gid, follow_symlinks=False)
    info = target.lstat()
    if (not stat.S_ISDIR(info.st_mode)
            or (info.st_uid, info.st_gid) != (account.pw_uid, account.pw_gid)
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise RuntimeError("unexpected Chrome configuration leaf")
    print("SIDE_BROWSER_CONFIG_READY")


if __name__ == "__main__":
    main()
