"""Copy the most recent browser screenshots where the working user can read them.

Read-only on the service store. It never prints page content, only file names.

    sudo /usr/bin/python3.12 internal/tools/copy_recent_sites_shots.py [count]
"""
import os
import shutil
import sys
from pathlib import Path

SHOTS = Path("/var/lib/metnos-service/.local/share/metnos/sites-shots")
DESTINATION = Path("/tmp/metnos-shots")


def main(argv):
    count = int(argv[1]) if len(argv) > 1 else 3
    if not 1 <= count <= 20:
        raise SystemExit("count must be between 1 and 20")
    if os.geteuid() != 0:
        raise SystemExit("REFUSED: run this with sudo")
    found = sorted(
        (path for path in SHOTS.glob("*/*") if path.is_file()),
        key=lambda path: path.stat().st_mtime, reverse=True,
    )[:count]
    if not found:
        raise SystemExit("no screenshot found")
    owner = Path(__file__).resolve().stat()
    DESTINATION.mkdir(mode=0o755, exist_ok=True)
    os.chown(DESTINATION, owner.st_uid, owner.st_gid)
    for path in found:
        target = DESTINATION / path.name
        shutil.copyfile(path, target)
        os.chmod(target, 0o644)
        os.chown(target, owner.st_uid, owner.st_gid)
        print("SHOT", target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
