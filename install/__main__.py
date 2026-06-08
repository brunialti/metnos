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

from . import disclaimer, state, ui

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
        resume=ns.resume,
        force=ns.force,
        force_phase=ns.force_phase,
        only_phase=ns.only_phase,
        yes=ns.yes,
        enable=list(ns.enable),
        skip=list(ns.skip),
    )


def _welcome() -> None:
    ui.banner("Metnos installer", "Self-hosted AI agent · AGPL-3.0 · metnos.com")
    ui.console().print(
        "  [dim]This installer will set up Metnos in [bold]six phases[/bold]. "
        "Each phase is idempotent: you can interrupt and resume at any time.[/dim]\n"
    )
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
        ui.info(f"Disclaimer previously accepted (lang={existing}). Re-show with --force-phase 0.")
        return existing

    if args.yes:
        ui.fail(
            "The POC disclaimer must be accepted interactively at least once. "
            "Re-run without --yes, accept, then re-add --yes for subsequent runs."
        )

    lang = disclaimer.ask_language()
    if not disclaimer.show_and_confirm(lang):
        ui.fail("Disclaimer not accepted — aborting installation.", exit_code=3)
    ui.ok(f"Disclaimer accepted; locale = {lang}")
    return lang


def _confirm_proceed(args: Args) -> bool:
    if args.yes:
        return True
    return ui.confirm("Proceed with the next pending phase?", default=True)


def _run_phase(num: int, mod_name: str, human_name: str, args: Args) -> bool:
    if state.is_done(num) and args.force_phase != num:
        ui.info(f"Phase {num} ({human_name}) already done — skipping")
        return True
    if args.force_phase == num:
        state.clear(num)
        ui.info(f"--force-phase {num}: sentinel cleared, re-running")

    rec = state.start(num, human_name)
    try:
        mod = importlib.import_module(f"install.phases.{mod_name}")
    except ImportError as e:
        ui.warn(f"Phase {num} module not implemented yet ({mod_name}): {e}")
        return False  # Not fatal — we may be on an early dev cut

    try:
        notes = mod.run(args) or {}
    except KeyboardInterrupt:
        ui.warn(f"\nPhase {num} interrupted by user. State preserved; re-run to resume.")
        return False
    except Exception as e:
        ui.fail(f"Phase {num} failed: {type(e).__name__}: {e}")
        return False

    state.commit(rec, notes)
    ui.ok(f"Phase {num} ({human_name}) complete")
    return True


def main() -> int:
    args = _parse()
    _welcome()

    # Disclaimer + language must be accepted before anything else.
    # On re-run, skipped via the disclaimer sentinel.
    if args.force_phase == 0:
        # Clear sentinel to force re-show
        from pathlib import Path
        import os as _os
        Path(_os.environ.get("METNOS_STATE", str(Path.home() / ".local" / "state" / "metnos"))
             ).joinpath("install", "disclaimer.accepted").unlink(missing_ok=True)
    locale = _gate_language_and_disclaimer(args)
    os.environ["METNOS_LOCALE"] = locale

    if not _confirm_proceed(args):
        ui.info("Aborted.")
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
