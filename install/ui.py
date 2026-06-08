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
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

_console: Console = Console()


def banner(title: str, subtitle: str = "") -> None:
    """Phase heading. Always preceded by a blank line."""
    _console.print()
    text = Text(f" {title} ", style="bold white on #1A477A")
    _console.print(text, justify="left")
    if subtitle:
        _console.print(f"  [dim]{subtitle}[/dim]")
    _console.print()


def step(msg: str) -> None:
    _console.print(f"  [blue]•[/blue] {msg}")


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
    """Render the phase status table (used at end of install / on resume)."""
    table = Table(show_header=True, header_style="bold", border_style="dim")
    table.add_column("Phase", justify="right", width=5)
    table.add_column("Name", min_width=24)
    table.add_column("Status", width=10)
    table.add_column("Notes", overflow="fold")
    for r in rows:
        status = "[green]done[/green]" if r["done"] else "[yellow]pending[/yellow]"
        notes_compact = ", ".join(f"{k}={v}" for k, v in (r.get("notes") or {}).items())
        table.add_row(str(r["phase"]), r.get("name") or "—", status, notes_compact or "—")
    _console.print(Panel(table, title="[bold]Install state[/bold]", border_style="dim"))


def console() -> Console:
    """Access the singleton console (for advanced use)."""
    return _console
