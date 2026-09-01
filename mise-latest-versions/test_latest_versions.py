import json
import subprocess
import sys
from pathlib import Path

import pytest
from latest_versions import latest_series

SCRIPT = Path(__file__).resolve().parent / "latest_versions.py"


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


def test_cli_minor_default():
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input="0.41.0\n0.42.0\n0.43.0\n0.44.0\n",
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(proc.stdout) == ["0.42", "0.43", "0.44"]


def test_cli_major_level():
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "major", "2"],
        input="1.9.0\n2.0.0\n3.1.0\n",
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(proc.stdout) == ["2", "3"]


def test_cli_rejects_bad_level():
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "weekly"],
        input="1.0.0\n",
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0
    assert "level must be one of" in proc.stderr
