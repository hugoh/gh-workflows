"""Thin wrappers over the handful of GitHub REST endpoints shared across
repo-admin's scripts: account-wide repo listing and Actions secrets.
"""

from __future__ import annotations

import base64

from nacl import encoding, public

from .client import api_json, paginated


async def fetch_repos_json(owner: str) -> list[dict]:
    """Lists every repo for `owner`. When `owner` is the authenticated `gh`
    user, uses /user/repos so private repos are included; otherwise falls
    back to /users/{owner}/repos, which only ever returns public repos.
    """
    viewer = (await api_json("GET", "/user")).get("login")
    if owner == viewer:
        return await paginated(
            "GET", "/user/repos", params={"affiliation": "owner", "per_page": "100"}
        )
    return await paginated("GET", f"/users/{owner}/repos", params={"per_page": "100"})


async def public_repos_json(owner: str) -> list[dict]:
    """Lists `owner`'s public repos only, via /users/{owner}/repos -- unlike
    fetch_repos_json, this always excludes private repos even when `owner`
    is the authenticated user, so no /user call is needed to branch on it.
    """
    return await paginated("GET", f"/users/{owner}/repos", params={"per_page": "100"})


def encrypt_secret_value(public_key_b64: str, value: str) -> str:
    """Encrypts `value` for GitHub's Actions secrets API using a repo's
    public key, per GitHub's documented libsodium sealed-box scheme.
    """
    public_key = public.PublicKey(
        public_key_b64.encode("utf-8"), encoding.Base64Encoder
    )
    encrypted = public.SealedBox(public_key).encrypt(value.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")


async def set_repo_secret(
    owner: str, repo_name: str, secret_name: str, value: str
) -> None:
    """Sets one repo's Actions secret via GitHub's REST API: fetches the
    repo's current public key, encrypts `value` for it, and PUTs the
    result. The plaintext value never leaves this process -- it's encrypted
    in memory before the request body is built.
    """
    key_data = await api_json(
        "GET", f"/repos/{owner}/{repo_name}/actions/secrets/public-key"
    )
    encrypted_value = encrypt_secret_value(key_data["key"], value)
    await api_json(
        "PUT",
        f"/repos/{owner}/{repo_name}/actions/secrets/{secret_name}",
        json={"encrypted_value": encrypted_value, "key_id": key_data["key_id"]},
    )
