"""Repo listing/filtering and the CLI-entrypoint plumbing shared by
repo-admin's scripts.

Fetches repos via `asyncgh.fetch_repos` and runs bounded-parallel work over
them via `reconcilekit.run_parallel` -- this module adds nothing beyond
that except the `Repo`/`RepoResult` shapes and a couple of CLI conveniences
(`run_cli`, `as_set`). Config-file-backed policy (which forks to include,
which repos to exclude from a specific check, secrets) is a caller concern,
not this package's -- `include_forks` here is just a plain set, with no
default derived from any file.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar, cast

from asyncgh import GhError, aclose_client, fetch_repos
from reconcilekit import Status
from reconcilekit import run_parallel as _run_parallel

DEFAULT_OWNER = os.environ.get("GH_OWNER", "hugoh")
DEFAULT_JOBS = int(os.environ.get("GH_JOBS", "6"))

_Args = TypeVar("_Args")


@dataclass(frozen=True)
class Repo:
    name: str
    default_branch: str
    is_private: bool
    is_fork: bool
    homepage: str = ""


@dataclass
class RepoResult:
    repo: Repo
    line: str
    status: Status = Status.OK
    tag: str | None = None


def as_set(value: str | None) -> set[str] | None:
    """Splits a comma-separated string into a set, or `None` if `value` is
    falsy -- the CLI-argument convention every repo-admin script's
    `--skip`/repo-list arguments share.
    """
    if not value:
        return None
    return {v.strip() for v in value.split(",") if v.strip()}


def filter_repos(
    repos_json: list[dict],
    *,
    only: set[str] | None = None,
    skip: set[str] | None = None,
    include_forks: set[str] | None = None,
) -> list[Repo]:
    """Keeps every non-archived repo that's either not a fork or is listed in
    include_forks, then applies only/skip.
    """
    include_forks = include_forks or set()
    repos = []
    for entry in repos_json:
        if entry["archived"]:
            continue
        name = entry["name"]
        if entry["fork"] and name not in include_forks:
            continue
        if only and name not in only:
            continue
        if skip and name in skip:
            continue
        repos.append(
            Repo(
                name=name,
                default_branch=entry.get("default_branch") or "",
                is_private=entry["private"],
                is_fork=entry["fork"],
                homepage=entry.get("homepage") or "",
            )
        )
    return repos


async def list_repos(
    owner: str = DEFAULT_OWNER,
    *,
    only: set[str] | None = None,
    skip: set[str] | None = None,
    include_forks: set[str] | None = None,
) -> list[Repo]:
    """Fetches every repo for `owner` and applies `filter_repos`.
    `include_forks` defaults to none included -- callers with a
    file/env-backed fork policy (like repo-admin's) resolve it themselves
    and pass it in.
    """
    # RepoJSON (a TypedDict) isn't assignable to plain dict per ty -- these
    # helpers work on repo JSON generically, not asyncgh's specific shape.
    repos_json = cast("list[dict]", await fetch_repos(owner))
    return filter_repos(repos_json, only=only, skip=skip, include_forks=include_forks)


def run_cli(
    entrypoint: Callable[[_Args], Awaitable[int]],
    args: _Args,
) -> int:
    """Runs an async CLI entrypoint under asyncio, always closing the shared
    HTTP client afterwards and turning a GhError into a stderr message plus
    exit code 1 -- the wrapper every repo-admin-style script's main() shares.
    """

    async def _run() -> int:
        try:
            return await entrypoint(args)
        finally:
            await aclose_client()

    try:
        return asyncio.run(_run())
    except GhError as exc:
        print(exc, file=sys.stderr)
        return 1


async def run_parallel(
    repos: list[Repo], worker, *, jobs: int = DEFAULT_JOBS, verbose: bool = False
) -> list[RepoResult]:
    """reconcilekit.run_parallel with GhError bound as the failure exception
    type. See reconcilekit.kernel for the concurrency, failure-isolation,
    and quiet-suppression behaviour.
    """
    return await _run_parallel(
        repos, worker, jobs=jobs, verbose=verbose, error_cls=GhError
    )
