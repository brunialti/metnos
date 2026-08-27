"""Report which refusal a standard user receives from the Birth primitive.

Temporary diagnostic: the frozen worker returns only a numeric outcome, so the
stable code never reaches the cell that observes it.  This probe performs the
same flow and writes the code where the parent can read it.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
REPORT = Path(os.environ.get("SystemRoot", "C:\\Windows")) / "Temp" / "rm0008-nonelevated.txt"


def main(argv: list[str]) -> int:
    sys.path.insert(0, str(REPOSITORY / "runtime"))
    sys.path.insert(0, str(REPOSITORY / "tests" / "windows_identity"))
    sys.path.insert(0, str(REPOSITORY / "tests" / "windows_identity" / "rm0008_2a_acceptance"))
    import _windows_support as support

    product = support.product()
    root = Path(argv[3])
    sid = support.identity_oracle().current_token_facts().user_sid
    bindings = support.explicit_role_bindings(
        product, (("never-log-this-secret.bin",), False, "birth_confidential")
    )
    step = "session"
    try:
        with support.session(
            root, authenticated_sid=sid, create_root=False, role_bindings=bindings
        ) as active:
            step = "lock"
            with active.global_lock(exclusive=True, create=False):
                step = "create"
                support.create_file(
                    active,
                    ("never-log-this-secret.bin",),
                    b"never-log-this-secret",
                    "birth_confidential",
                )
    except product.BirthSecureFSError as refusal:
        REPORT.write_text(f"{step} {refusal.code}\n", encoding="utf-8")
        return 40 if refusal.code == "birth_provisioning_elevation_required" else 20
    except BaseException as unexpected:  # noqa: BLE001 - diagnostic
        REPORT.write_text(f"{step} {type(unexpected).__name__} {unexpected}\n", encoding="utf-8")
        return 30
    REPORT.write_text(f"{step} nessun rifiuto\n", encoding="utf-8")
    return 20


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
