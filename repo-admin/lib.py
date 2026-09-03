"""Shared helpers for repo-admin/*.py scripts. Not meant to be run directly."""

from __future__ import annotations

import asyncio
import base64
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx2
import yaml
from nacl import encoding, public
from reconcilekit.render import console

from reconcilekit import (
    ReconcileError,
    Status,
    classify_status,
    partition_fields,
    print_status,
    progress_bar,
    result_line,
    run_reconcile,
    summary_status,
    unavailable_suffix,
)
from reconcilekit import run_parallel as _run_parallel

__all__ = [  # re-exported from reconcilekit for repo-admin's other modules
    "Status",
    "classify_status",
    "console",
    "partition_fields",
    "print_status",
    "progress_bar",
    "result_line",
    "run_reconcile",
    "summary_status",
    "unavailable_suffix",
]

LIB_DIR = Path(__file__).resolve().parent
CONFIG_DIR = LIB_DIR / "config"
API_BASE = "https://api.github.com"
DEFAULT_OWNER = os.environ.get("GH_OWNER", "hugoh")
DEFAULT_JOBS = int(os.environ.get("GH_JOBS", "6"))
PAGES_DOMAINS_FILE = CONFIG_DIR / "pages-domains.yaml"
BRANCH_PROTECTION_EXCLUDE_FILE = CONFIG_DIR / "branch-protection-exclude.txt"
SECRETS_FILE = CONFIG_DIR / "secrets.yaml"
SECRETS_ENC_FILE = CONFIG_DIR / "secrets.enc.yaml"
SOPS_CONFIG_FILE = CONFIG_DIR / ".sops.yaml"


class GhError(ReconcileError):
    """A GitHub API call -- or a repo's worker function -- failed unexpectedly.

    status_code is set for HTTP errors raised by api_json(), so callers can
    branch on the real status code (e.g. 403 vs 404) instead of
    string-matching an error message.
    """

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _auth_token() -> str:
    """Reads the token `gh` already has -- keychain storage, SSO, and 2FA are
    already solved by `gh auth login`, so this reuses that instead of
    managing a separate credential.
    """
    result = subprocess.run(
        ["gh", "auth", "token"], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise GhError(f"gh auth token failed: {result.stderr.strip()}")
    return result.stdout.strip()


_client: httpx2.AsyncClient | None = None
_client_lock = asyncio.Lock()


async def _get_client() -> httpx2.AsyncClient:
    # One shared AsyncClient rather than one per thread: there's no longer a
    # thread pool, and gh auth token only needs to be paid once per process.
    global _client
    if _client is None:
        async with _client_lock:
            if _client is None:
                _client = httpx2.AsyncClient(
                    headers={
                        "Authorization": f"Bearer {_auth_token()}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                    timeout=30,
                )
    return _client


async def aclose_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def api_request(
    method: str, path: str, *, json: Any = None, params: dict | None = None
) -> httpx2.Response:
    """Makes one GitHub REST API call and returns the raw Response --
    callers decide what a given status means for their endpoint (e.g. a 404
    means "feature disabled" for vulnerability-alerts but "not found"
    everywhere else). Raises GhError only for genuine transport failures
    (DNS, timeout, connection reset); HTTP error statuses are returned, not
    raised.
    """
    url = path if path.startswith("http") else f"{API_BASE}{path}"
    client = await _get_client()
    try:
        return await client.request(method, url, json=json, params=params)
    except httpx2.HTTPError as exc:
        raise GhError(str(exc)) from exc


def error_message(response: httpx2.Response) -> str:
    """Extracts GitHub's own `message` field from an error response body,
    falling back to the raw response text if the body isn't JSON.
    """
    try:
        return response.json().get("message", response.text)
    except ValueError:
        return response.text


async def api_json(
    method: str, path: str, *, json: Any = None, params: dict | None = None
) -> dict:
    """Like api_request, but raises GhError (with status_code and GitHub's
    own error message) on any non-2xx response, and returns the parsed JSON
    body -- or {} for a body-less response like 204 No Content -- on success.
    """
    response = await api_request(method, path, json=json, params=params)
    if not response.is_success:
        raise GhError(error_message(response), status_code=response.status_code)
    return response.json() if response.content else {}


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


async def _paginated(
    method: str, path: str, *, params: dict | None = None
) -> list[dict]:
    items = []
    url = path
    query = params
    while url:
        response = await api_request(method, url, params=query)
        if not response.is_success:
            raise GhError(error_message(response), status_code=response.status_code)
        items.extend(response.json())
        url = response.links.get("next", {}).get("url")
        query = None  # the "next" link already carries the full query string
    return items


async def fetch_repos_json(owner: str) -> list[dict]:
    """Lists every repo for `owner`. When `owner` is the authenticated `gh`
    user, uses /user/repos so private repos are included; otherwise falls
    back to /users/{owner}/repos, which only ever returns public repos.
    """
    viewer = (await api_json("GET", "/user")).get("login")
    if owner == viewer:
        return await _paginated(
            "GET", "/user/repos", params={"affiliation": "owner", "per_page": "100"}
        )
    return await _paginated("GET", f"/users/{owner}/repos", params={"per_page": "100"})


async def public_repos_json(owner: str) -> list[dict]:
    """Lists `owner`'s public repos only, via /users/{owner}/repos -- unlike
    fetch_repos_json, this always excludes private repos even when `owner`
    is the authenticated user, so no /user call is needed to branch on it.
    """
    return await _paginated("GET", f"/users/{owner}/repos", params={"per_page": "100"})


def default_include_forks() -> set[str]:
    """Forks hugoh actually maintains and wants managed like any other repo,
    read from include-forks.txt (one name per line, '#' comments and blank
    lines ignored). Override with GH_INCLUDE_FORKS (comma-separated) for a
    one-off run; edit the file to permanently add one.
    """
    env_value = os.environ.get("GH_INCLUDE_FORKS")
    if env_value is not None:
        return as_set(env_value) or set()
    forks_file = CONFIG_DIR / "include-forks.txt"
    forks = set()
    for line in forks_file.read_text().splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            forks.add(stripped)
    return forks


def unmatched_include_forks(
    include_forks: set[str], repos_json: list[dict]
) -> set[str]:
    """include-forks.txt entries (or GH_INCLUDE_FORKS) that don't match any
    fetched repo -- a typo, a rename, or a repo that's gone, silently going
    stale otherwise since filter_repos() just never matches them.
    """
    repo_names = {entry["name"] for entry in repos_json}
    return include_forks - repo_names


def default_branch_protection_exclude() -> set[str]:
    """Repos excluded from branch-protection specifically (e.g. homebrew-tap,
    which has no CI/PR workflow), read from branch-protection-exclude.txt
    (one name per line, '#' comments and blank lines ignored). Override with
    GH_BRANCH_PROTECTION_EXCLUDE (comma-separated) for a one-off run; edit
    the file to permanently add more.
    """
    env_value = os.environ.get("GH_BRANCH_PROTECTION_EXCLUDE")
    if env_value is not None:
        return as_set(env_value) or set()
    excluded = set()
    for line in BRANCH_PROTECTION_EXCLUDE_FILE.read_text().splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            excluded.add(stripped)
    return excluded


def default_pages_domains() -> dict[str, str]:
    """Repo -> GitHub Pages custom domain mapping, read from
    pages-domains.yaml -- the single source of truth also read by
    iac/cloudflare's OpenTofu config to generate matching DNS records.
    """
    return yaml.safe_load(PAGES_DOMAINS_FILE.read_text()) or {}


def default_secrets() -> dict[str, list[str]]:
    """Secret name -> target repo list, read from secrets.yaml -- the
    plaintext half of the secrets-sync config; values live sops-encrypted
    in secrets.enc.yaml (see decrypt_secrets()).
    """
    raw = yaml.safe_load(SECRETS_FILE.read_text()) or {}
    return {name: cfg.get("repos", []) for name, cfg in raw.items()}


def decrypt_secrets() -> dict[str, str]:
    """Decrypts secrets.enc.yaml via `sops -d`, returning secret name ->
    value. Shells out rather than using a sops Python binding -- same
    external-trusted-CLI style as _auth_token()'s `gh auth token` call.
    """
    if not SECRETS_ENC_FILE.exists():
        raise GhError(
            f"{SECRETS_ENC_FILE.name} not found -- create it with `sops` "
            "(see repo-admin/config/.sops.yaml)"
        )
    try:
        result = subprocess.run(
            ["sops", "-d", str(SECRETS_ENC_FILE)],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise GhError("sops not found on PATH") from exc
    if result.returncode != 0:
        raise GhError(
            f"sops -d {SECRETS_ENC_FILE.name} failed: {result.stderr.strip()}"
        )
    data = yaml.safe_load(result.stdout) or {}
    data.pop("sops", None)  # sops metadata block, not a secret
    return data


def init_secrets_file(template_yaml: str) -> None:
    """Seeds secrets.enc.yaml via `sops --encrypt`, from a plaintext YAML
    template (typically one empty value per configured secret name) --
    lets secrets-edit create the file pre-populated with the right keys
    instead of the user hand-writing sops' metadata block themselves.

    --filename-override is required here: sops picks a creation rule by
    matching .sops.yaml's path_regex against the file being encrypted, but
    the content comes from stdin (/dev/stdin), which matches nothing --
    the override tells sops to match rules as if encrypting
    SECRETS_ENC_FILE itself. --config is required too: sops discovers
    .sops.yaml by walking up from the *current working directory*, not
    from the (overridden) file path, so without it this breaks whenever
    repo-admin.sh is invoked from outside repo-admin/.
    """
    try:
        result = subprocess.run(
            [
                "sops",
                "--encrypt",
                "--config",
                str(SOPS_CONFIG_FILE),
                "--filename-override",
                str(SECRETS_ENC_FILE),
                "--input-type",
                "yaml",
                "--output-type",
                "yaml",
                "/dev/stdin",
            ],
            input=template_yaml,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise GhError("sops not found on PATH") from exc
    if result.returncode != 0:
        raise GhError(f"sops --encrypt failed: {result.stderr.strip()}")
    SECRETS_ENC_FILE.write_text(result.stdout)


def edit_secrets_file() -> int:
    """Opens secrets.enc.yaml in `sops` -- decrypts to $EDITOR, re-encrypts
    on save -- inheriting this process's stdio (not captured) since sops
    needs a real terminal/editor session. Returns sops' exit code.
    """
    try:
        result = subprocess.run(["sops", str(SECRETS_ENC_FILE)], check=False)
    except FileNotFoundError as exc:
        raise GhError("sops not found on PATH") from exc
    return result.returncode


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
    result. The plaintext value never leaves this process -- it's
    encrypted in memory before the request body is built.
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
    if include_forks is None:
        include_forks = default_include_forks()
    repos_json = await fetch_repos_json(owner)
    for name in sorted(unmatched_include_forks(include_forks, repos_json)):
        print(
            f"warning: include-forks entry {name!r} doesn't match any repo",
            file=sys.stderr,
        )
    return filter_repos(repos_json, only=only, skip=skip, include_forks=include_forks)


def as_set(value: str | None) -> set[str] | None:
    if not value:
        return None
    return {v.strip() for v in value.split(",") if v.strip()}


async def run_parallel(
    repos: list[Repo], worker, jobs: int = DEFAULT_JOBS, verbose: bool = False
) -> list[RepoResult]:
    """reconcilekit.run_parallel with repo-admin's failure exception type bound
    (so callers keep catching GhError). See reconcilekit.kernel for the
    concurrency, failure-isolation, and quiet-suppression behaviour.
    """
    return await _run_parallel(repos, worker, jobs, verbose, error_cls=GhError)
