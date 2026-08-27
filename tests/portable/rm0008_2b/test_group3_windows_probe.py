"""One measurement of the Windows blocker, and nothing more.

Section 17.81 of the group 2 report left the cause of the refused publication
open and asked for "the minimum that decides it, when it is really needed".
It is needed now: it is the last technical item still declared not proven, and
section 14 of the group 3 plan proposes a candidate cause — the move used to
ask for WRITE_DAC and WRITE_OWNER, which no call site uses and which the
service mask of a Birth object does not grant.

This cell runs the real provisioner on Windows and reports what it says.  It
builds nothing of its own and asserts nothing about success: the three probes
group 2 built all answered for themselves before telling the truth, so this one
only carries the outcome out, with the code exactly as the product produced it.
"""
from __future__ import annotations

import os

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from . import support

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="this measurement only exists on Windows"
)


def test_windows_provisioning_reports_what_it_does(tmp_path, monkeypatch):
    """Provision once and print the outcome; never decide it here."""
    base = support.make_config(
        tmp_path, author=Ed25519PrivateKey.generate(), operator=True,
    )
    provisioning = support.provisioner()
    try:
        result = support.provision(monkeypatch, base)
    except provisioning.BirthProvisioningError as exc:
        print(f"RM0008-WINDOWS-PROBE code={exc.code}")
        raise AssertionError(
            f"provisioning refused with {exc.code}"
        ) from None
    except BaseException as exc:  # noqa: BLE001 - the point is to see it
        print(f"RM0008-WINDOWS-PROBE unexpected={type(exc).__name__}: {exc}")
        raise
    print(f"RM0008-WINDOWS-PROBE ok set_id={getattr(result, 'set_id', '?')}")
