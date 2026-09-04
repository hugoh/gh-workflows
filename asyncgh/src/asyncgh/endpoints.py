"""Thin wrappers over the handful of GitHub REST endpoints shared across
repo-admin's scripts: account-wide repo listing and Actions secrets.

Each takes an optional `client:` -- default `None` uses the shared default
`GitHubClient` (same one the module-level transport functions in `client.py`
use), so `await fetch_repos(owner)` keeps working unchanged; pass an
explicit `GitHubClient` to talk to a second account or use your own retry
config.
"""

from __future__ import annotations

import base64
from typing import TypedDict, cast

from nacl import encoding, public

from .client import GitHubClient, _get_default_client


class RepoJSON(TypedDict, total=False):
    """The commonly-used subset of GitHub's repo JSON shape -- not
    exhaustive (GitHub's repo object has ~80 fields); add keys here as
    callers need them.
    """

    id: int
    name: str
    full_name: str
    private: bool
    fork: bool
    archived: bool
    default_branch: str
    homepage: str
    html_url: str
    description: str | None
    owner: dict


async def fetch_repos(
    owner: str, *, client: GitHubClient | None = None
) -> list[RepoJSON]:
    """Lists every repo for `owner`, as GitHub's raw JSON -- asyncgh has no
    domain model of its own, so callers parse the fields they need. When
    `owner` is the authenticated `gh` user, uses /user/repos so private
    repos are included; otherwise falls back to /users/{owner}/repos, which
    only ever returns public repos.
    """
    client = client or _get_default_client()
    viewer = (await client.api_json("GET", "/user")).get("login")
    if owner == viewer:
        repos = await client.paginated(
            "GET", "/user/repos", params={"affiliation": "owner", "per_page": "100"}
        )
    else:
        repos = await client.paginated(
            "GET", f"/users/{owner}/repos", params={"per_page": "100"}
        )
    return cast(list[RepoJSON], repos)


async def public_repos(
    owner: str, *, client: GitHubClient | None = None
) -> list[RepoJSON]:
    """Lists `owner`'s public repos only, via /users/{owner}/repos -- unlike
    fetch_repos, this always excludes private repos even when `owner` is
    the authenticated user, so no /user call is needed to branch on it.
    """
    client = client or _get_default_client()
    repos = await client.paginated(
        "GET", f"/users/{owner}/repos", params={"per_page": "100"}
    )
    return cast(list[RepoJSON], repos)


def encrypt_secret_value(public_key_b64: str, value: str) -> str:
    """Encrypts `value` for GitHub's Actions secrets API using a repo's
    public key, per GitHub's documented libsodium sealed-box scheme.
    """
    public_key = public.PublicKey(
        public_key_b64.encode("utf-8"), encoding.Base64Encoder
    )
    encrypted = public.SealedBox(public_key).encrypt(value.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")


async def get_repo_public_key(
    owner: str, repo_name: str, *, client: GitHubClient | None = None
) -> dict:
    """Fetches a repo's Actions-secrets public key (`{"key", "key_id"}`) --
    split out of set_repo_secret so a caller setting several secrets on the
    same repo can fetch it once and pass it to each call via `public_key=`.
    """
    client = client or _get_default_client()
    return await client.api_json(
        "GET", f"/repos/{owner}/{repo_name}/actions/secrets/public-key"
    )


async def set_repo_secret(
    owner: str,
    repo_name: str,
    secret_name: str,
    value: str,
    *,
    public_key: dict | None = None,
    client: GitHubClient | None = None,
) -> None:
    """Sets one repo's Actions secret via GitHub's REST API: encrypts
    `value` for the repo's public key and PUTs the result. Fetches the
    public key itself unless the caller already has it (`public_key=`, from
    get_repo_public_key) -- worth doing when setting several secrets on the
    same repo, so the key is fetched once, not once per secret. The
    plaintext value never leaves this process -- it's encrypted in memory
    before the request body is built.
    """
    client = client or _get_default_client()
    key_data = public_key or await get_repo_public_key(owner, repo_name, client=client)
    encrypted_value = encrypt_secret_value(key_data["key"], value)
    await client.api_json(
        "PUT",
        f"/repos/{owner}/{repo_name}/actions/secrets/{secret_name}",
        json={"encrypted_value": encrypted_value, "key_id": key_data["key_id"]},
    )
