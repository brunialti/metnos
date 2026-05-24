"""skill_sandbox_watchdog — controlla la soglia di trigger per la
Fase C full del sandbox per-skill (ADR 0140).

Daily check (scheduler v2 @06:30): se uno dei trigger e' superato,
emette un MSG_SKILL_SANDBOX_THRESHOLD via i canali admin
(Telegram/HTTP) per attivare la migrazione a sandbox enforcement
bubblewrap per-skill (Fase C full, 12-15 gg).

Trigger:
- (a) Skill third-party installate distintamente >= 5
- (b) Almeno 1 user_channel di tipo `guest` (non host) registrato

Logica fail-soft: il watchdog ritorna solo report, mai blocca o
modifica stato. Roberto decide se attivare Fase C dopo la notifica.
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


def _count_distinct_skills() -> int:
    """Conta skill installate distinte (NON executor count).

    ADR 0160: scan `skills/` (new) + `_imports/` (legacy back-compat).
    """
    from skills_paths import existing_skill_names as _esn
    return len(_esn())


def _count_guest_users() -> int:
    """Conta user_channels con role != 'host'."""
    import sqlite3
    db_path = _C.PATH_USER_DATA / "users.sqlite"
    if not db_path.exists():
        return 0
    try:
        con = sqlite3.connect(str(db_path))
        cur = con.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM user_channels WHERE role != 'host'"
        )
        n = cur.fetchone()[0]
        con.close()
        return int(n)
    except Exception:
        return 0


def _audit_summary() -> dict:
    """Sintesi dell'audit log delle skill invocations."""
    try:
        from skill_audit import stats
        return stats()
    except Exception:
        return {}


def check_threshold() -> dict[str, Any]:
    """Ritorna dict {triggered, n_skills, n_guests, audit_stats}."""
    n_skills = _count_distinct_skills()
    n_guests = _count_guest_users()
    triggered = (n_skills >= THRESHOLD_N_SKILLS
                  or n_guests >= THRESHOLD_N_GUESTS)
    return {
        "ts": time.time(),
        "triggered": triggered,
        "n_skills": n_skills,
        "n_guests": n_guests,
        "threshold_n_skills": THRESHOLD_N_SKILLS,
        "threshold_n_guests": THRESHOLD_N_GUESTS,
        "audit_stats": _audit_summary(),
    }


def task_skill_sandbox_watchdog() -> dict:
    """Entry point per scheduler v2. Notifica admin se triggered."""
    report = check_threshold()
    if not report["triggered"]:
        return {
            "ok": True,
            "summary": (
                f"sandbox watchdog: {report['n_skills']} skill, "
                f"{report['n_guests']} guest — sotto soglia"
            ),
            "report": report,
        }
    # Triggered: scrivi marker file per CLI admin lookup
    marker = _C.PATH_USER_DATA / "sandbox_threshold_triggered.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    # Tenta notifica admin via send_messages (fallback su log se non disponibile)
    msg = (
        f"⚠️ Soglia sandbox per-skill superata: "
        f"{report['n_skills']} skill (>= {THRESHOLD_N_SKILLS}) "
        f"o {report['n_guests']} guest (>= {THRESHOLD_N_GUESTS}). "
        f"Valutare attivazione Fase C full (ADR 0140 → 0142 futuro). "
        f"Dettagli: ~/.local/share/metnos/sandbox_threshold_triggered.json"
    )
    try:
        from notify_admin import notify
        notify(msg, kind="sandbox_threshold")
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
