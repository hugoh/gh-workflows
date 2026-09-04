"""asyncgh -- the GitHub REST + GraphQL API transport shared by repo-admin's
scripts.

`GitHubClient` is the actual client -- auth, connection, retry, and the
request / pagination / GraphQL methods every endpoint wrapper is built on.
The module-level functions below (`api_json`, `graphql`, `fetch_repos`,
...) are convenience wrappers over one shared default `GitHubClient`, for
scripts that only ever talk to one account and don't want to construct
anything. No config-file loading, no repo filtering -- those stay with the
caller.
"""

from __future__ import annotations

from .client import (
    API_BASE,
    GhError,
    GitHubClient,
    aclose_client,
    api_json,
    api_raw,
    error_message,
    graphql,
    paginated,
)
from .endpoints import (
    RepoJSON,
    encrypt_secret_value,
    fetch_repos,
    get_repo_public_key,
    public_repos,
    set_repo_secret,
)

__all__ = [
    "API_BASE",
    "GhError",
    "GitHubClient",
    "RepoJSON",
    "aclose_client",
    "api_json",
    "api_raw",
    "encrypt_secret_value",
    "error_message",
    "fetch_repos",
    "get_repo_public_key",
    "graphql",
    "paginated",
    "public_repos",
    "set_repo_secret",
]
