"""repokit -- repo listing/filtering and CLI-entrypoint plumbing shared by
repo-admin's scripts.

Fetches repos via `asyncgh` and runs bounded-parallel work over them via
`reconcilekit`; adds a `Repo`/`RepoResult` shape and a couple of CLI
conveniences (`run_cli`, `as_set`) on top. No config-file loading, no sops,
no fork/exclude policy -- those stay with the caller, which is why
`list_repos`'s `include_forks` defaults to none included rather than to any
file-backed default.
"""

from __future__ import annotations

from .core import (
    DEFAULT_JOBS,
    DEFAULT_OWNER,
    Repo,
    RepoResult,
    as_set,
    filter_repos,
    list_repos,
    run_cli,
    run_parallel,
)
from .email import send_email, send_email_from_env

__all__ = [
    "DEFAULT_JOBS",
    "DEFAULT_OWNER",
    "Repo",
    "RepoResult",
    "as_set",
    "filter_repos",
    "list_repos",
    "run_cli",
    "run_parallel",
    "send_email",
    "send_email_from_env",
]
