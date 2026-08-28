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

import importlib
import os

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from . import support

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="this measurement only exists on Windows"
)


def _private_cause_summary(exc: BaseException) -> str:
    """Report only classifications and native operations, never paths."""
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen and len(parts) < 8:
        seen.add(id(current))
        code = getattr(current, "code", None)
        winerror = getattr(current, "winerror", None)
        errno = getattr(current, "errno", None)
        operation = getattr(current, "strerror", None)
        parts.append(
            f"{type(current).__name__}(code={code!r},errno={errno!r},"
            f"winerror={winerror!r},operation={operation!r})"
        )
        current = getattr(current, "_internal_cause", None)
    return " -> ".join(parts)


def _trace_only_the_native_move(monkeypatch) -> list[str]:
    """Observe which part of the real move refused, without changing it."""
    secure_fs = importlib.import_module("executor_birth_secure_fs")
    events: list[str] = []
    state = {"inside": False, "move": 0}

    original_move = secure_fs._SecureRootSession._rename_no_replace_windows_v1
    original_open = secure_fs._win_open_relative_v1
    original_profile = secure_fs._SecureRootSession._verify_windows_profile

    def traced_move(self, source, destination, directory, attempted):
        state["move"] += 1
        state["inside"] = True
        events.append(f"move-{state['move']}:enter:directory={directory}")
        try:
            result = original_move(
                self, source, destination, directory, attempted,
            )
        except BaseException as exc:  # noqa: BLE001 - diagnostic boundary
            events.append(
                f"move-{state['move']}:error:native-attempted={bool(attempted)}:"
                f"{_private_cause_summary(exc)}"
            )
            raise
        else:
            events.append(f"move-{state['move']}:ok:native-attempted={bool(attempted)}")
            return result
        finally:
            state["inside"] = False

    def traced_open(*args, **kwargs):
        if not state["inside"]:
            return original_open(*args, **kwargs)
        purpose = kwargs.get("purpose")
        label = getattr(purpose, "value", repr(purpose))
        try:
            result = original_open(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - diagnostic boundary
            events.append(
                f"move-{state['move']}:open:{label}:error:"
                f"{_private_cause_summary(exc)}"
            )
            raise
        events.append(f"move-{state['move']}:open:{label}:ok")
        return result

    def traced_profile(self, handle, *, directory, profile, components=None):
        label = getattr(profile, "value", repr(profile))
        try:
            result = original_profile(
                self,
                handle,
                directory=directory,
                profile=profile,
                components=components,
            )
        except BaseException as exc:  # noqa: BLE001 - diagnostic boundary
            if state["inside"]:
                events.append(
                    f"move-{state['move']}:profile:{label}:error:"
                    f"{_private_cause_summary(exc)}"
                )
            raise
        if state["inside"]:
            events.append(f"move-{state['move']}:profile:{label}:ok")
        return result

    monkeypatch.setattr(
        secure_fs._SecureRootSession,
        "_rename_no_replace_windows_v1",
        traced_move,
    )
    monkeypatch.setattr(secure_fs, "_win_open_relative_v1", traced_open)
    monkeypatch.setattr(
        secure_fs._SecureRootSession,
        "_verify_windows_profile",
        traced_profile,
    )
    return events


def test_windows_provisioning_reports_what_it_does(tmp_path, monkeypatch):
    """Provision once and print the outcome; never decide it here."""
    base = support.make_config(
        tmp_path, author=Ed25519PrivateKey.generate(), operator=True,
    )
    provisioning = support.provisioner()
    events = _trace_only_the_native_move(monkeypatch)
    try:
        result = support.provision(monkeypatch, base)
    except provisioning.BirthProvisioningError as exc:
        detail = _private_cause_summary(exc)
        trace = " | ".join(events)
        print(f"RM0008-WINDOWS-PROBE code={exc.code} cause={detail} trace={trace}")
        raise AssertionError(
            f"provisioning refused with {exc.code}; cause={detail}; trace={trace}"
        ) from None
    except BaseException as exc:  # noqa: BLE001 - the point is to see it
        print(f"RM0008-WINDOWS-PROBE unexpected={type(exc).__name__}: {exc}")
        raise
    print(f"RM0008-WINDOWS-PROBE ok set_id={getattr(result, 'set_id', '?')}")
