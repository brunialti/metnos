"""The Birth policy facts that belong to the code, not to a configuration.

Two values used to be chosen by ``bootstrap.json``: the policy version, which
is stamped on every admission receipt, and the receipt lifetime, which decides
how long one of those receipts still means something.  A file that can choose
them is a file that can choose an authoritative fact, which is exactly what
RM-0008 removes.

They live here because they are properties of the code that decides, not of
the installation that runs it: they move when the rules move, and they are the
same on every machine.  Decision taken with Roberto on 27/8/2026.
"""
from __future__ import annotations

BIRTH_POLICY_VERSION_V1 = "birth-policy-v1"

# The bounds are the ones the previous validator already enforced; they are
# kept so a future signed preference has a declared range to sit in, and so a
# value out of range is a defect rather than a surprise.
BIRTH_RECEIPT_TTL_SECONDS_V1 = 3600
BIRTH_RECEIPT_TTL_MINIMUM_V1 = 60
BIRTH_RECEIPT_TTL_MAXIMUM_V1 = 86400


def birth_receipt_ttl_seconds_v1() -> int:
    """The receipt lifetime, checked against its own declared range."""
    ttl = BIRTH_RECEIPT_TTL_SECONDS_V1
    if (
        type(ttl) is not int
        or not BIRTH_RECEIPT_TTL_MINIMUM_V1 <= ttl <= BIRTH_RECEIPT_TTL_MAXIMUM_V1
    ):
        raise ValueError("birth_receipt_ttl_invalid")
    return ttl


__all__ = [
    "BIRTH_POLICY_VERSION_V1",
    "BIRTH_RECEIPT_TTL_MAXIMUM_V1",
    "BIRTH_RECEIPT_TTL_MINIMUM_V1",
    "BIRTH_RECEIPT_TTL_SECONDS_V1",
    "birth_receipt_ttl_seconds_v1",
]
