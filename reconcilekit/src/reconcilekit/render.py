"""Default terminal renderer for reconcile runs: a status symbol per line above
a live progress bar, silenced when stdout isn't a terminal.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.progress import Progress

from .status import Status

console = Console()

_STATUS_DISPLAY: dict[Status, tuple[str, str]] = {
    Status.OK: ("✓", "green"),
    Status.UNCHANGED: ("•", "green"),
    Status.LIMITED: ("○", "yellow"),
    Status.LIMITED_UNCHANGED: ("•", "yellow"),
    Status.FAILED: ("✗", "red"),
}


def progress_bar(*, console: Console = console, **kwargs: Any) -> Progress:
    """A Progress bound to `console` (module-level `console` by default),
    silenced when its console isn't a terminal -- e.g. `cmd > file` or piping
    into another command -- so its status text (which Progress still renders as
    plain lines even without a tty) doesn't end up mixed into redirected output.
    """
    return Progress(console=console, disable=not console.is_terminal, **kwargs)


def result_line(name: str, detail: str, status: Status) -> str:
    """Formats one target's result line: name padded for alignment, an
    "unchanged: " prefix for the two quiet statuses so a suppressed line
    still reads correctly if a caller prints it anyway (e.g. --verbose),
    then the worker's own detail text.
    """
    prefix = (
        "unchanged: " if status in (Status.UNCHANGED, Status.LIMITED_UNCHANGED) else ""
    )
    return f"{name:<30} {prefix}{detail}"


def print_status(status: Status, line: str) -> None:
    """Prints one result line through the shared `console`, color-coded and
    symbol-prefixed by `status`.

    Always goes through the single module-level `console` -- the same one
    `run_parallel` passes to `Progress(console=...)` -- rather than a
    Console of the caller's own: a second Console writing to a separate
    stream isn't coordinated by rich's Live redraw bookkeeping and can
    visually corrupt output on a real terminal.

    `markup=False`: `line` can contain target/error text with a literal "["
    (dict reprs, error messages) that would otherwise be parsed as rich
    markup. `highlight=False`: rich's default ReprHighlighter recolors
    numbers, paths, etc. within the line (e.g. a target named "foo-410"),
    fighting with the single status color wanted for the whole line.
    """
    symbol, color = _STATUS_DISPLAY[status]
    console.print(f"{symbol} {line}", style=color, markup=False, highlight=False)
