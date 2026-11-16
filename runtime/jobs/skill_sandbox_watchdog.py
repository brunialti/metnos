"""skill_sandbox_watchdog — the reminder about a sandbox per imported integration.

Daily check of the night (ADR 0159, layer 4). Every tool already runs inside
the common sandbox; a tighter one for each integration written by others is
planned and not built. It would serve only where such code is installed, so
the reminder fires only then:

- at least one enabled integration written by others (imported, not
  first-party), and
- at least one registered guest, or five such integrations or more.

A guest alone, with only first-party integrations, asks for nothing more than
the common sandbox (Roberto, 16/11/2026: «only when a sandbox would serve»).
The person is told once per state of the counts, in the words of the catalog
(`MSG_SKILL_SANDBOX_THRESHOLD`). Fail-soft: the check only reports, it never
blocks or changes anything. Building that sandbox is the person's decision.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

THRESHOLD_N_SKILLS = 5
THRESHOLD_N_GUESTS = 1


import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as _C  # §7.11 — rispetta METNOS_USER_DATA


def _count_third_party_integrations() -> int:
    """Enabled integrations whose code was written by others: imported, not
    first-party. Those are what a sandbox of their own would fence; a
    first-party integration is Metnos code wherever its files are installed
    (`SkillInfo.is_builtin`)."""
    from skill_registry import list_skills
    return sum(1 for info in list_skills() if info.enabled and not info.is_builtin)


def _count_guest_users() -> int:
    """Guests registered on the instance (``users.role``).

    Until 15/11/2026 this opened a database that does not exist
    (``users.sqlite``, the store is ``users.db``) and asked for a column the
    channels table does not have; a broad ``except`` read both as «no
    guests», so this trigger could never fire.
    """
    import users

    return len(users.list_users(role="guest"))


def _audit_summary() -> dict:
    """Summary of the audit log of integration calls."""
    try:
        from skill_audit import stats
        return stats()
    except Exception:
        return {}


def check_threshold() -> dict[str, Any]:
    """The counts and whether a sandbox per integration would serve now."""
    n_third_party = _count_third_party_integrations()
    n_guests = _count_guest_users()
    triggered = n_third_party >= 1 and (
        n_guests >= THRESHOLD_N_GUESTS or n_third_party >= THRESHOLD_N_SKILLS)
    return {
        "ts": time.time(),
        "triggered": triggered,
        "n_third_party": n_third_party,
        "n_guests": n_guests,
        "threshold_n_skills": THRESHOLD_N_SKILLS,
        "threshold_n_guests": THRESHOLD_N_GUESTS,
        "audit_stats": _audit_summary(),
    }


def task_skill_sandbox_watchdog() -> dict:
    """Entry point of the nightly sequence: tells the person when a count is reached."""
    report = check_threshold()
    if not report["triggered"]:
        return {
            "ok": True,
            "summary": (
                f"sandbox watchdog: {report['n_third_party']} third-party, "
                f"{report['n_guests']} guests: not needed"
            ),
            "report": report,
        }
    # Reached: the counts are kept beside the instance data for an administrator.
    marker = _C.PATH_USER_DATA / "sandbox_threshold_triggered.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    # Said in the words of the catalog, in the language of the instance.
    from messages import get as _msg
    msg = _msg("MSG_SKILL_SANDBOX_THRESHOLD",
               n_third_party=report["n_third_party"], n_guests=report["n_guests"])
    try:
        from notify_admin import notify
        # Once per state of the triggers, not every night.
        notify(msg, kind="sandbox_threshold", event_key=(
            f"sandbox_needed:{report['n_third_party'] >= THRESHOLD_N_SKILLS}:"
            f"{report['n_guests'] >= THRESHOLD_N_GUESTS}"))
    except Exception:
        import logging
        logging.getLogger(__name__).warning(msg)
    return {
        "ok": True,
        "summary": msg,
        "report": report,
        "triggered": True,
    }


if __name__ == "__main__":
    import json as _j
    rep = check_threshold()
    print(_j.dumps(rep, indent=2, ensure_ascii=False))
