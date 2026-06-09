# SPDX-License-Identifier: AGPL-3.0-only
"""Terminal UI helpers for the Metnos installer.

Built on ``rich`` (installed by bootstrap.sh into the venv before this
module is imported). Provides:

- ``banner()`` — large phase heading with subtitle
- ``step()`` / ``ok()`` / ``warn()`` / ``fail()`` — single-line status
- ``progress()`` — context manager around ``rich.progress.Progress``
- ``ask()`` / ``confirm()`` / ``choice()`` — interactive prompts
- ``summary_panel()`` — final/intermediate phase status table

All output goes to stdout via the singleton ``Console`` so it composes
correctly with rich's progress bars. No global state beyond the console.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from typing import Iterable

from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.prompt import Confirm, Prompt
from rich.rule import Rule

_console: Console = Console()

# A single restrained accent runs through the whole installer: linear,
# elegant, no heavy filled bars or boxes. Status is carried by thin rules
# and aligned glyph lines, not panels.
_ACCENT = "#3B82C4"


def banner(title: str, subtitle: str = "") -> None:
    """Phase heading: a thin accent rule with the title, dim subtitle."""
    _console.print()
    _console.print(Rule(f"[bold {_ACCENT}]{title}[/]", style=_ACCENT, align="left"))
    if subtitle:
        _console.print(f"  [dim]{subtitle}[/dim]")
    _console.print()


def step(msg: str) -> None:
    _console.print(f"  [{_ACCENT}]›[/] {msg}")


def ok(msg: str) -> None:
    _console.print(f"  [green]✓[/green] {msg}")


def warn(msg: str) -> None:
    _console.print(f"  [yellow]![/yellow] {msg}")


def fail(msg: str, *, exit_code: int = 1) -> None:
    _console.print(f"  [red]✗[/red] {msg}", style="red")
    sys.exit(exit_code)


def info(msg: str) -> None:
    """Indented secondary info (less prominent than ``step``)."""
    _console.print(f"    [dim]{msg}[/dim]")


@contextmanager
def progress(description: str = ""):
    """Context manager returning a rich ``Progress``.

    Use ``progress.add_task()`` inside the ``with`` block. Supports both
    indeterminate spinners and determinate bars (for downloads).

    Example::

        with progress() as p:
            tid = p.add_task("Downloading model", total=size_bytes)
            for chunk in stream:
                p.update(tid, advance=len(chunk))
    """
    cols = [
        SpinnerColumn(style="blue"),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
    ]
    with Progress(*cols, console=_console, transient=False) as p:
        yield p


def confirm(question: str, *, default: bool = True) -> bool:
    """Yes/no prompt. Default is honored on empty Enter."""
    return Confirm.ask(f"  [bold]{question}[/bold]", default=default, console=_console)


def ask(question: str, *, default: str | None = None, password: bool = False) -> str:
    """Free-form input. Returns the string the user typed (or default)."""
    return Prompt.ask(
        f"  [bold]{question}[/bold]",
        default=default,
        password=password,
        console=_console,
    )


def choice(question: str, options: Iterable[str], *, default: str | None = None) -> str:
    """Single-choice prompt with rich-validated input."""
    opts = list(options)
    return Prompt.ask(
        f"  [bold]{question}[/bold]",
        choices=opts,
        default=default or opts[0],
        console=_console,
    )


def summary_panel(rows: list[dict]) -> None:
    """Phase status, rendered linearly (no box): one aligned line per phase."""
    _console.print()
    _console.print(Rule("[dim]install state[/dim]", style="dim", align="left"))
    for r in rows:
        glyph = "[green]✓[/green]" if r["done"] else "[dim]·[/dim]"
        name = (r.get("name") or "—")
        notes = ", ".join(f"{k}={v}" for k, v in (r.get("notes") or {}).items())
        line = f"  {glyph} [bold]{r['phase']}[/bold]  {name:<22}"
        if notes:
            line += f" [dim]{notes}[/dim]"
        _console.print(line)
    _console.print()


def console() -> Console:
    """Access the singleton console (for advanced use)."""
    return _console
