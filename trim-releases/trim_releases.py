"""Delete old GitHub releases via `gh`.

Keeps the newest `--keep-full` full releases and the newest
`--keep-prereleases` prereleases; drops everything else, plus any of those
newest prereleases older than `--prerelease-cutoff-days`.
"""

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta


def select_tags_to_delete(
    releases: list[dict],
    now: datetime,
    keep_full: int,
    keep_prereleases: int,
    prerelease_cutoff_days: int,
) -> list[str]:
    cutoff = (now - timedelta(days=prerelease_cutoff_days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    full = sorted(
        (r for r in releases if not r["isPrerelease"]),
        key=lambda r: r["createdAt"],
        reverse=True,
    )
    pre = sorted(
        (r for r in releases if r["isPrerelease"]),
        key=lambda r: r["createdAt"],
        reverse=True,
    )

    to_delete = (
        full[keep_full:]
        + pre[keep_prereleases:]
        + [r for r in pre[:keep_prereleases] if r["createdAt"] < cutoff]
    )
    return [r["tagName"] for r in to_delete]


def fetch_releases() -> list[dict]:
    result = subprocess.run(
        [
            "gh",
            "release",
            "list",
            "--limit",
            "1000",
            "--json",
            "tagName,isPrerelease,createdAt",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def delete_release(tag: str) -> None:
    subprocess.run(["gh", "release", "delete", tag, "--yes"], check=True)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", "--dry-run", action="store_true")
    parser.add_argument("--keep-full", type=int, default=10)
    parser.add_argument("--keep-prereleases", type=int, default=3)
    parser.add_argument("--prerelease-cutoff-days", type=int, default=14)
    args = parser.parse_args(argv)

    to_delete = select_tags_to_delete(
        fetch_releases(),
        datetime.now(UTC),
        args.keep_full,
        args.keep_prereleases,
        args.prerelease_cutoff_days,
    )

    if not to_delete:
        print("No releases to delete.")
        return 0

    for tag in to_delete:
        if args.dry_run:
            print(f"Would delete release {tag}")
        else:
            print(f"Deleting release {tag}")
            delete_release(tag)

    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
