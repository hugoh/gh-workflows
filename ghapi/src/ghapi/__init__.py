"""ghapi -- the GitHub REST API transport shared by repo-admin's scripts.

Auth via the token `gh` already holds, one shared async client, raw and
raise-on-error request helpers, `Link`-header pagination, and thin wrappers
over the few endpoints more than one script needs (account-wide repo
listing, Actions secrets). No config-file loading, no repo filtering -- those
stay with the caller.
"""

from __future__ import annotations

from .client import (
    API_BASE,
    GhError,
    aclose_client,
    api_json,
    api_request,
    error_message,
    paginated,
)
from .endpoints import (
    encrypt_secret_value,
    fetch_repos_json,
    public_repos_json,
    set_repo_secret,
)

__all__ = [
    "API_BASE",
    "GhError",
    "aclose_client",
    "api_json",
    "api_request",
    "encrypt_secret_value",
    "error_message",
    "fetch_repos_json",
    "paginated",
    "public_repos_json",
    "set_repo_secret",
]
