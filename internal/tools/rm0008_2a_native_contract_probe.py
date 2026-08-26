#!/usr/bin/env python3
"""Local probe of the RM-0008 2A native contract (diagnostic, never certifying).

Section 16.2 keeps diagnostic reproducers outside the acceptance manifest: this
tool colours no cell and enters no activity.  It exists because the Windows
half of the manifest can otherwise only be falsified through a public CI round
of about four minutes, while a wrong size, offset or constant is decidable
here in milliseconds.

Run it before publishing a change that touches the Windows path:

    python3 internal/tools/rm0008_2a_native_contract_probe.py

It reports three classes of line: a fact that matches section 7.3, a fact that
contradicts it, and a normative name that the product does not define yet.
"""
from __future__ import annotations

import ctypes
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "runtime"))

import executor_birth_secure_fs as product  # noqa: E402


# Section 7.3 fixes these layouts for Windows Server 2022 x64.
STRUCTURE_SIZES = {
    "_UNICODE_STRING": 16,
    "_OBJECT_ATTRIBUTES": 48,
    "_IO_STATUS_BLOCK": 16,
    "_FILE_ID_INFO": 24,
    "_OVERLAPPED": 40,
}
FIELD_OFFSETS = {
    "_UNICODE_STRING": {"Length": 0, "MaximumLength": 2, "Buffer": 8},
    "_OBJECT_ATTRIBUTES": {
        "Length": 0,
        "RootDirectory": 8,
        "ObjectName": 16,
        "Attributes": 24,
        "SecurityDescriptor": 32,
        "SecurityQualityOfService": 40,
    },
    "_IO_STATUS_BLOCK": {"Information": 8},
}
CONSTANTS = {
    "_FILE_OPEN": 0x00000001,
    "_FILE_CREATE": 0x00000002,
    "_FILE_DIRECTORY_FILE": 0x00000001,
    "_FILE_WRITE_THROUGH": 0x00000002,
    "_FILE_SYNCHRONOUS_IO_NONALERT": 0x00000020,
    "_FILE_NON_DIRECTORY_FILE": 0x00000040,
    "_FILE_OPEN_REPARSE_POINT": 0x00200000,
    "_OBJ_CASE_INSENSITIVE": 0x00000040,
    "_FILE_PERSISTENT_ACLS": 0x00000008,
    "_DELETE": 0x00010000,
}
ACCESS_MASKS = {
    "file creation": 0x001F0083,
    "directory creation": 0x001F00A1,
}


def main() -> int:
    matched = contradicted = absent = 0
    for name, expected in sorted(STRUCTURE_SIZES.items()):
        structure = getattr(product, name, None)
        if structure is None:
            print(f"ASSENTE   {name}: la specifica ne fissa la dimensione a {expected}")
            absent += 1
            continue
        observed = ctypes.sizeof(structure)
        if observed == expected:
            print(f"conforme  {name}: {observed} byte")
            matched += 1
        else:
            print(f"DIFFORME  {name}: {observed} byte, attesi {expected}")
            contradicted += 1
        for field, offset in sorted(FIELD_OFFSETS.get(name, {}).items()):
            member = getattr(structure, field, None)
            if member is None:
                print(f"ASSENTE   {name}.{field}: offset atteso {offset}")
                absent += 1
            elif member.offset == offset:
                print(f"conforme  {name}.{field}: offset {offset}")
                matched += 1
            else:
                print(
                    f"DIFFORME  {name}.{field}: offset {member.offset}, atteso {offset}"
                )
                contradicted += 1
    for name, expected in sorted(CONSTANTS.items()):
        if not hasattr(product, name):
            print(f"ASSENTE   {name}: valore normativo {expected:#010x}")
            absent += 1
            continue
        observed = getattr(product, name)
        if observed == expected:
            print(f"conforme  {name}: {observed:#010x}")
            matched += 1
        else:
            print(f"DIFFORME  {name}: {observed:#010x}, atteso {expected:#010x}")
            contradicted += 1
    source = Path(product.__file__).read_text(encoding="utf-8")
    for label, mask in ACCESS_MASKS.items():
        if f"{mask:#010x}" in source.lower() or f"{mask:#x}" in source.lower():
            print(f"conforme  maschera di {label}: {mask:#010x} presente nel sorgente")
            matched += 1
        else:
            print(f"ASSENTE   maschera di {label}: {mask:#010x} non compare nel sorgente")
            absent += 1
    print(
        f"\nconformi {matched} · difformi {contradicted} · non ancora definiti {absent}"
    )
    print(
        "Questo strumento e' diagnostico: non certifica alcuna cella e non "
        "sostituisce le attivita' Windows della matrice pubblica."
    )
    return 1 if contradicted else 0


if __name__ == "__main__":
    raise SystemExit(main())
