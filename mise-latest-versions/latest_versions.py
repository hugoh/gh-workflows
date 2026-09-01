"""Emit a JSON array of the newest N version series at a given granularity.

Reads newline-separated version strings on stdin (e.g. the output of
`mise ls-remote <tool>`) and prints a JSON array, ascending, of the newest
`count` distinct series truncated to `level`:

  level=major  ->  ["1", "2"]
  level=minor  ->  ["0.42", "0.43", "0.44"]
  level=patch  ->  ["0.43.0", "0.43.1", "0.44.0"]

Lines that don't parse as `MAJOR[.MINOR[.PATCH]]` (optionally `v`-prefixed)
to the requested depth are ignored; a prerelease like `0.43.0-rc.1` folds
into its release series.
"""

import json
import sys

LEVELS = {"major": 1, "minor": 2, "patch": 3}


def latest_series(lines, level, count):
    width = LEVELS[level]
    seen = {}
    for raw in lines:
        parts = raw.strip().lstrip("v").split(".")
        nums = []
        for part in parts[:width]:
            head = part.split("-", 1)[0]
            if not head.isdigit():
                break
            nums.append(int(head))
        if len(nums) == width:
            seen[tuple(nums)] = None
    picked = sorted(seen)[-count:] if count > 0 else []
    return [".".join(str(n) for n in key) for key in picked]


def main():
    level = sys.argv[1] if len(sys.argv) > 1 else "minor"
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    if level not in LEVELS:
        sys.exit(f"level must be one of {', '.join(LEVELS)}, got {level!r}")
    print(json.dumps(latest_series(sys.stdin, level, count)))


if __name__ == "__main__":
    main()
