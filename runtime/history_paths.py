"""Native undo-history locations, resolved without creating directories.

The legacy reader preserves its empty-override convention. Callers retain
responsibility for their own expansion and containment checks.
"""
import os
from pathlib import Path

import config


def backup_directory():
    return Path(os.environ.get("METNOS_HISTORY_DIR") or (
        config.PATH_USER_DATA / "_history"))


def legacy_blob_directory():
    return Path(os.environ.get("METNOS_HISTORY_DIR", config.PATH_USER_DATA / "_history"))
