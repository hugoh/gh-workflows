"""repokit -- a small toolkit for scripts that operate over a GitHub
account's repos.

Lists and filters them (via `asyncgh`), runs an async CLI entrypoint with
shared-client cleanup, and fans work out over them with bounded concurrency
and failure isolation (via `reconcilekit`), plus the `Repo`/`RepoResult`
shapes and a couple of CLI conveniences (`run_cli`, `as_set`).

Config-file-backed policy -- which forks to include, which repos a given
check skips, secrets -- is a caller concern by design, which is why
`list_repos`'s `include_forks` is a plain set with no file-backed default.
"""

from __future__ import annotations

from .core import (
    DEFAULT_JOBS,
    Repo,
    RepoResult,
    as_set,
    filter_repos,
    list_repos,
    run_cli,
    run_parallel,
)

__all__ = [
    "DEFAULT_JOBS",
    "Repo",
    "RepoResult",
    "as_set",
    "filter_repos",
    "list_repos",
    "run_cli",
    "run_parallel",
]
