# SPDX-License-Identifier: AGPL-3.0-only
"""Metnos installer — orchestrator.

Invoked by ``install/bootstrap.sh`` after the venv is ready, OR directly
as ``python -m install`` from a clone when the venv is already populated.

Six phases per ADR 0145::

  1 bootstrap   pre-flight + python deps + runtime dirs
  2 infra       BGE-M3 model + optional services (llama.cpp, VLM, photon, …)
  3 code        Metnos source skeleton + i18n.sqlite import
  4 secrets     interactive dialog for admin / Telegram / IMAP / API keys
  5 systemd     user units + reachability tests
  6 firstboot   admin onboarding link + optional sample index

Each phase runs at most once unless its sentinel is missing or
``--force-phase N`` is passed.
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys
import time
from dataclasses import dataclass

from . import disclaimer, i18n, state, ui

# Phase registry: (number, module-name, human-name)
_PHASES = [
    (1, "phase1_bootstrap", "Bootstrap"),
    (2, "phase2_infra",     "Infrastructure"),
    (3, "phase3_code",      "Metnos code"),
    (4, "phase4_secrets",   "Sensitive data"),
    (5, "phase5_systemd",   "Systemd services"),
    (6, "phase6_firstboot", "First boot"),
]


@dataclass
class Args:
    check: bool
    resume: bool
    force: bool
    force_phase: int | None
    only_phase: int | None
    yes: bool
    enable: list[str]
    skip: list[str]


def _parse() -> Args:
    p = argparse.ArgumentParser(
        prog="metnos-installer",
        description="Install Metnos on this machine. Idempotent — safe to re-run.",
    )
    p.add_argument("--check", action="store_true",
                   help="Pre-flight only: run system checks and exit, writing nothing.")
    p.add_argument("--resume", action="store_true",
                   help="Skip phases whose sentinel exists (default behaviour).")
    p.add_argument("--force", action="store_true",
                   help="Continue past non-fatal pre-flight warnings.")
    p.add_argument("--force-phase", type=int, choices=range(0, 7), metavar="N",
                   help="Re-run phase N (0 = disclaimer) by clearing its sentinel first.")
    p.add_argument("--only-phase", type=int, choices=range(1, 7), metavar="N",
                   help="Run only phase N and stop. Other phases unchanged.")
    p.add_argument("--yes", "-y", action="store_true",
                   help="Auto-confirm every yes/no prompt (non-interactive).")
    p.add_argument("--enable", action="append", default=[], metavar="COMPONENT",
                   help="Force an optional component on (e.g. --enable vlm).")
    p.add_argument("--skip", action="append", default=[], metavar="COMPONENT",
                   help="Force an optional component off (e.g. --skip photon).")
    ns = p.parse_args()
    return Args(
        check=ns.check,
        resume=ns.resume,
        force=ns.force,
        force_phase=ns.force_phase,
        only_phase=ns.only_phase,
        yes=ns.yes,
        enable=list(ns.enable),
        skip=list(ns.skip),
    )


def _welcome() -> None:
    ui.banner(i18n.t("main_welcome_title"), i18n.t("main_welcome_subtitle"))
    ui.console().print(i18n.t("main_welcome_intro"))
    rows = state.summary()
    ui.summary_panel(rows)


def _gate_language_and_disclaimer(args: Args) -> str:
    """Ask language, show the POC disclaimer, demand explicit acceptance.

    Runs once per fresh install (sentinel under $METNOS_STATE). On
    re-run, skips to the next phase. Aborts the install if the user
    does not type the expected acceptance token.

    Returns the chosen locale ('en' or 'it').
    """
    existing = disclaimer.read_locale()
    if existing and disclaimer.already_accepted():
        ui.info(i18n.t("main_disclaimer_already", existing=existing))
        return existing

    if args.yes:
        ui.fail(i18n.t("main_disclaimer_needs_interactive"))

    lang = disclaimer.ask_language()
    if not disclaimer.show_and_confirm(lang):
        ui.fail(i18n.t("main_disclaimer_not_accepted"), exit_code=3)
    ui.ok(i18n.t("main_disclaimer_accepted", lang=lang))
    return lang


def _confirm_proceed(args: Args) -> bool:
    if args.yes:
        return True
    return ui.confirm(i18n.t("main_confirm_proceed"), default=True)


def _run_phase(num: int, mod_name: str, human_name: str, args: Args) -> bool:
    if state.is_done(num) and args.force_phase != num:
        ui.info(i18n.t("main_phase_already_done", num=num, human_name=human_name))
        return True
    if args.force_phase == num:
        state.clear(num)
        ui.info(i18n.t("main_phase_force", num=num))

    rec = state.start(num, human_name)
    try:
        mod = importlib.import_module(f"install.phases.{mod_name}")
    except ImportError as e:
        ui.warn(i18n.t("main_phase_not_implemented", num=num, mod_name=mod_name, err=e))
        return False  # Not fatal — we may be on an early dev cut

    try:
        notes = mod.run(args) or {}
    except KeyboardInterrupt:
        ui.warn(i18n.t("main_phase_interrupted", num=num))
        return False
    except Exception as e:
        ui.fail(i18n.t("main_phase_failed", num=num, etype=type(e).__name__, err=e))
        return False

    state.commit(rec, notes)
    ui.ok(i18n.t("main_phase_complete", num=num, human_name=human_name))
    return True


def main() -> int:
    args = _parse()

    # --check: pre-flight only. Runs the resource checks and exits WITHOUT
    # writing anything (no disclaimer sentinel, no state dirs, no models) — runs
    # before _welcome()/the disclaimer gate exactly so it stays read-only.
    if args.check:
        from . import preflight
        ui.banner("Metnos installer · pre-flight", "system checks only — nothing is written")
        ok = preflight.run_all(min_disk_gb=8)
        ui.ok("Pre-flight passed — system looks ready.") if ok else \
            ui.warn("Pre-flight found issues (see above). Nothing was written.")
        return 0 if ok else 1

    _welcome()

    # Disclaimer + language must be accepted before anything else.
    # On re-run, skipped via the disclaimer sentinel.
    if args.force_phase == 0:
        # Clear sentinel to force re-show
        from pathlib import Path
        Path(os.environ.get("METNOS_USER_STATE", str(Path.home() / ".local" / "state" / "metnos"))
             ).joinpath("install", "disclaimer.accepted").unlink(missing_ok=True)
    locale = _gate_language_and_disclaimer(args)
    os.environ["METNOS_LOCALE"] = locale

    if not _confirm_proceed(args):
        ui.info(i18n.t("main_aborted"))
        return 0

    phases_to_run = _PHASES
    if args.only_phase is not None:
        phases_to_run = [p for p in _PHASES if p[0] == args.only_phase]

    for num, mod_name, human_name in phases_to_run:
        if not _run_phase(num, mod_name, human_name, args):
            ui.warn("Aborting at this phase. Re-run installer to resume.")
            return 2
        time.sleep(0.2)  # let stdout flush between phases

    # Final summary
    ui.banner("Install complete", "Run `systemctl --user status metnos-http` to verify")
    ui.summary_panel(state.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
