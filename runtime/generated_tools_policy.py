# SPDX-License-Identifier: MIT
"""What the person decided once about generated tools (ADR 0240).

One preference: whether Metnos may use the external model to finish a tool
after every local attempt failed. Asked each time by default; answered once
here, the question stops coming back.
"""
from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path

ASK = "ask"
NEVER = "never"
ALWAYS = "always"
EXTERNAL_MODEL_CHOICES = (ASK, NEVER, ALWAYS)
EXTERNAL_MODEL_DEFAULT = ASK


def _path() -> Path:
    import config
    return Path(config.PATH_USER_CONFIG) / "generated-tools.json"


def external_model() -> str:
    """``ask`` (default), ``never`` or ``always``. An unreadable or foreign
    file is the default: it can never widen what the person allowed."""
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        with os.fdopen(os.open(_path(), flags), "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                return EXTERNAL_MODEL_DEFAULT
            document = json.loads(handle.read(4097))
    except (OSError, ValueError, UnicodeError):
        return EXTERNAL_MODEL_DEFAULT
    value = document.get("external_model") if isinstance(document, dict) else None
    return value if value in EXTERNAL_MODEL_CHOICES else EXTERNAL_MODEL_DEFAULT


def set_external_model(value: str, *, by: str) -> None:
    """Record the person's choice, with who made it and when."""
    if value not in EXTERNAL_MODEL_CHOICES:
        raise ValueError("generated tools preference invalid")
    import config
    config.write_private_text(_path(), json.dumps({
        "external_model": value, "by": str(by)[:120],
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }) + "\n")
