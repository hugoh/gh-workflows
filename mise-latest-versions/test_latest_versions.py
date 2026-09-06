import io
import json

import pytest
from latest_versions import latest_series, main


@pytest.mark.parametrize(
    ("versions", "level", "count", "expected"),
    [
        (
            ["0.37.0", "0.42.0", "0.43.0", "0.44.0"],
            "minor",
            3,
            ["0.42", "0.43", "0.44"],
        ),
        (["0.44.0", "0.42.1", "0.43.0", "0.42.0"], "minor", 2, ["0.43", "0.44"]),
        (["1.2.0", "1.2.1", "1.2.9", "1.3.0"], "minor", 3, ["1.2", "1.3"]),
        (
            ["v0.42.0", "0.43.0-rc.1", "0.43.0", "0.44.0"],
            "minor",
            3,
            ["0.42", "0.43", "0.44"],
        ),
        (["", "nope", "0.43.0", "  ", "0.44.0"], "minor", 3, ["0.43", "0.44"]),
        (["2.0.0", "2.1.0"], "minor", 5, ["2.0", "2.1"]),
        ([], "minor", 3, []),
        (["1.0.0"], "minor", 0, []),
        (["1.9.0", "1.10.0", "2.0.0", "2.3.1", "3.0.0"], "major", 2, ["2", "3"]),
        (["1.19.0"], "major", 5, ["1"]),
        (
            ["0.43.0", "0.43.1", "0.43.2", "0.44.0"],
            "patch",
            3,
            ["0.43.1", "0.43.2", "0.44.0"],
        ),
        (["1.2.0", "1.2"], "patch", 2, ["1.2.0"]),  # "1.2" lacks a patch component
    ],
)
def test_latest_series(versions, level, count, expected):
    assert latest_series(versions, level, count) == expected


@pytest.fixture
def run_main(monkeypatch, capsys):
    def run(argv, stdin):
        monkeypatch.setattr("sys.argv", ["latest_versions.py", *argv])
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
        main()
        return capsys.readouterr().out

    return run


def test_cli_minor_default(run_main):
    out = run_main([], "0.41.0\n0.42.0\n0.43.0\n0.44.0\n")
    assert json.loads(out) == ["0.42", "0.43", "0.44"]


def test_cli_major_level(run_main):
    out = run_main(["major", "2"], "1.9.0\n2.0.0\n3.1.0\n")
    assert json.loads(out) == ["2", "3"]


def test_cli_rejects_bad_level(run_main):
    with pytest.raises(SystemExit) as excinfo:
        run_main(["weekly"], "1.0.0\n")
    assert "level must be one of" in str(excinfo.value)
