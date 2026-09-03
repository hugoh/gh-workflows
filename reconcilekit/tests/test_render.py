import io

import pytest
from reconcilekit.render import _STATUS_DISPLAY
from rich.console import Console

from reconcilekit import Status, print_status, progress_bar, result_line


def test_result_line_prefixes_unchanged_status():
    assert result_line("t", "up to date detail", Status.UNCHANGED) == (
        f"{'t':<30} unchanged: up to date detail"
    )


def test_result_line_prefixes_limited_unchanged_status():
    assert result_line("t", "capped detail", Status.LIMITED_UNCHANGED) == (
        f"{'t':<30} unchanged: capped detail"
    )


@pytest.mark.parametrize("status", [Status.OK, Status.LIMITED, Status.FAILED])
def test_result_line_does_not_prefix_changed_statuses(status):
    assert result_line("t", "detail", status) == f"{'t':<30} detail"


def test_print_status_prints_line_to_stdout(capsys):
    print_status(Status.OK, "target-a done")
    assert "target-a done" in capsys.readouterr().out


def test_print_status_does_not_interpret_brackets_in_line_as_markup(capsys):
    print_status(Status.OK, "t {'allow_auto_merge': True} -> [oops]")
    assert "{'allow_auto_merge': True} -> [oops]" in capsys.readouterr().out


def test_status_display_pairs_are_unique_per_status():
    # (symbol, color) pairs -- not symbols alone, since e.g. UNCHANGED and
    # LIMITED_UNCHANGED intentionally share the "•" symbol and differ only by
    # color.
    pairs = list(_STATUS_DISPLAY.values())
    assert len(pairs) == len(set(pairs)) == len(list(Status))


def test_progress_bar_disabled_when_console_is_not_a_terminal():
    non_tty_console = Console(file=io.StringIO())
    assert progress_bar(console=non_tty_console).disable is True


def test_progress_bar_enabled_when_console_is_a_terminal():
    tty_console = Console(file=io.StringIO(), force_terminal=True)
    assert progress_bar(console=tty_console).disable is False


def test_progress_bar_prints_nothing_when_disabled():
    output = io.StringIO()
    non_tty_console = Console(file=output)
    with progress_bar(console=non_tty_console) as bar:
        task = bar.add_task("working", total=2)
        bar.advance(task)
        bar.advance(task)
    assert output.getvalue() == ""
