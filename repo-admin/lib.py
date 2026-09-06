"""Shared helpers for repo-admin/*.py scripts. Not meant to be run directly.

Repo listing/filtering and CLI-entrypoint plumbing live in the `repokit`
workspace package (published separately since `digest-action` -- a
standalone GitHub Action repo -- depends on it too); GitHub REST transport
lives in `asyncgh` and the fetch-diff-apply kernel in `reconcilekit`. This
module re-exports all three for repo-admin's modules and adds repo-admin's
own config-file loading, sops glue, and fork/exclude policy -- none of
which belongs in a package with consumers outside this account.
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import cast

import yaml
from reconcilekit.render import console

from asyncgh import (
    API_BASE,
    GhError,
    aclose_client,
    api_json,
    api_raw,
    encrypt_secret_value,
    error_message,
    fetch_repos,
    graphql,
    paginated,
    public_repos,
    set_repo_secret,
    set_repo_variable,
)
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
from repokit import (
    DEFAULT_JOBS,
    Repo,
    RepoResult,
    as_set,
    filter_repos,
    run_cli,
    run_parallel,
)

_default_owner: str | None = None
_default_owner_lock = asyncio.Lock()


async def default_owner() -> str:
    """The account to operate on: GH_OWNER if set, otherwise whoever the
    current token authenticates as (`GET /user`) -- cached since it's the
    same account for the life of one invocation. Double-checked under a
    lock so concurrent callers racing the first resolution share one
    `GET /user` call instead of each firing their own.
    """
    global _default_owner
    if _default_owner is None:
        async with _default_owner_lock:
            if _default_owner is None:
                _default_owner = (
                    os.environ.get("GH_OWNER")
                    or (await api_json("GET", "/user"))["login"]
                )
    return _default_owner


__all__ = [  # re-exported from asyncgh / reconcilekit / repokit for repo-admin's modules
    "API_BASE",
    "DEFAULT_JOBS",
    "GhError",
    "ReconcileError",
    "Repo",
    "RepoResult",
    "Status",
    "aclose_client",
    "api_json",
    "api_raw",
    "as_set",
    "classify_status",
    "console",
    "default_owner",
    "encrypt_secret_value",
    "error_message",
    "fetch_repos",
    "filter_repos",
    "graphql",
    "list_repos",
    "paginated",
    "partition_fields",
    "print_status",
    "progress_bar",
    "public_repos",
    "result_line",
    "run_cli",
    "run_parallel",
    "run_reconcile",
    "set_repo_secret",
    "set_repo_variable",
    "summary_status",
    "unavailable_suffix",
    "unmatched_include_forks",
]

LIB_DIR = Path(__file__).resolve().parent
CONFIG_DIR = LIB_DIR / "config"
PAGES_DOMAINS_FILE = CONFIG_DIR / "pages-domains.yaml"
BRANCH_PROTECTION_EXCLUDE_FILE = CONFIG_DIR / "branch-protection-exclude.txt"
SECRETS_FILE = CONFIG_DIR / "secrets.yaml"
SECRETS_ENC_FILE = CONFIG_DIR / "secrets.enc.yaml"
VARIABLES_FILE = CONFIG_DIR / "variables.yaml"
VARIABLES_ENC_FILE = CONFIG_DIR / "variables.enc.yaml"
SOPS_CONFIG_FILE = CONFIG_DIR / ".sops.yaml"


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


def _load_repo_map(path: Path) -> dict[str, list[str]]:
    """The `name -> {repos: [...]}` shape shared by secrets.yaml and
    variables.yaml, flattened to `name -> [repos]`.
    """
    raw = yaml.safe_load(path.read_text()) or {}
    return {name: cfg.get("repos", []) for name, cfg in raw.items()}


def default_secrets() -> dict[str, list[str]]:
    """Secret name -> target repo list, read from secrets.yaml -- the
    plaintext half of the secrets-sync config; values live sops-encrypted
    in secrets.enc.yaml (see decrypt_secrets()).
    """
    return _load_repo_map(SECRETS_FILE)


def default_variables() -> dict[str, list[str]]:
    """Variable name -> target repo list, read from variables.yaml. Same
    shape and sops-backed value store as secrets (a repo's Actions
    variables aren't sensitive, but keeping the two configs identical means
    one bootstrap path and one `edit` command cover both).
    """
    return _load_repo_map(VARIABLES_FILE)


def _decrypt_enc(path: Path) -> dict[str, str]:
    """Decrypts a sops-encrypted `name -> value` YAML file via `sops -d`.
    Shells out rather than using a sops Python binding -- same
    external-trusted-CLI style as asyncgh's `gh auth token` call.
    """
    if not path.exists():
        raise GhError(
            f"{path.name} not found -- create it with `sops` "
            "(see repo-admin/config/.sops.yaml)"
        )
    try:
        result = subprocess.run(
            ["sops", "-d", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise GhError("sops not found on PATH") from exc
    if result.returncode != 0:
        raise GhError(f"sops -d {path.name} failed: {result.stderr.strip()}")
    data = yaml.safe_load(result.stdout) or {}
    data.pop("sops", None)  # sops metadata block, not a real entry
    return data


def decrypt_secrets() -> dict[str, str]:
    """Secret name -> value, decrypted from secrets.enc.yaml."""
    return _decrypt_enc(SECRETS_ENC_FILE)


def decrypt_variables() -> dict[str, str]:
    """Variable name -> value, decrypted from variables.enc.yaml."""
    return _decrypt_enc(VARIABLES_ENC_FILE)


def write_enc_file(path: Path, values: dict[str, str]) -> None:
    """(Re-)encrypts `values` to `path` via `sops --encrypt` -- a full
    file rewrite, not a partial sops edit, so callers merge onto the
    decrypted current contents first. Used to seed an enc file with the
    right keys (empty values) for `... edit`, and by `config bootstrap` to
    write freshly-prompted values without an interactive sops session.

    --filename-override is required here: sops picks a creation rule by
    matching .sops.yaml's path_regex against the file being encrypted, but
    the content comes from stdin (/dev/stdin), which matches nothing -- the
    override tells sops to match rules as if encrypting `path` itself.
    --config is required too: sops discovers .sops.yaml by walking up from
    the *current working directory*, not from the (overridden) file path,
    so without it this breaks whenever repo-admin.sh is invoked from
    outside repo-admin/.
    """
    try:
        result = subprocess.run(
            [
                "sops",
                "--encrypt",
                "--config",
                str(SOPS_CONFIG_FILE),
                "--filename-override",
                str(path),
                "--input-type",
                "yaml",
                "--output-type",
                "yaml",
                "/dev/stdin",
            ],
            input=yaml.safe_dump(values, sort_keys=False),
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise GhError("sops not found on PATH") from exc
    if result.returncode != 0:
        raise GhError(f"sops --encrypt failed: {result.stderr.strip()}")
    path.write_text(result.stdout)


def _edit_enc(path: Path) -> int:
    """Opens a sops-encrypted file in `sops` -- decrypts to $EDITOR,
    re-encrypts on save -- inheriting this process's stdio (not captured)
    since sops needs a real terminal/editor session. Returns sops' exit
    code.
    """
    try:
        result = subprocess.run(["sops", str(path)], check=False)
    except FileNotFoundError as exc:
        raise GhError("sops not found on PATH") from exc
    return result.returncode


def init_secrets_file(template_yaml: str) -> None:
    """Seed secrets.enc.yaml from a plaintext YAML template string."""
    write_enc_file(SECRETS_ENC_FILE, yaml.safe_load(template_yaml) or {})


def init_variables_file(template_yaml: str) -> None:
    """Seed variables.enc.yaml from a plaintext YAML template string."""
    write_enc_file(VARIABLES_ENC_FILE, yaml.safe_load(template_yaml) or {})


def edit_secrets_file() -> int:
    return _edit_enc(SECRETS_ENC_FILE)


def edit_variables_file() -> int:
    return _edit_enc(VARIABLES_ENC_FILE)


_WORKFLOW_CONFIG_REF = re.compile(r"\$\{\{\s*(secrets|vars)\.([A-Za-z_][A-Za-z0-9_]*)")


def workflow_config_names(texts: Iterable[str]) -> tuple[set[str], set[str]]:
    """Scans GitHub Actions workflow YAML for `${{ secrets.X }}` /
    `${{ vars.X }}` references, returning (secret_names, variable_names).
    GITHUB_TOKEN is dropped -- it's always injected, never configured.
    """
    secrets: set[str] = set()
    variables: set[str] = set()
    for text in texts:
        for kind, name in _WORKFLOW_CONFIG_REF.findall(text):
            if name == "GITHUB_TOKEN":
                continue
            (secrets if kind == "secrets" else variables).add(name)
    return secrets, variables


async def fetch_workflow_texts(owner: str, repo: str) -> list[str]:
    """Downloads every `.yml`/`.yaml` file under a repo's
    `.github/workflows/` (the files GitHub Actions would run), decoded to
    text. Returns [] when the repo has no workflows directory.
    """
    listing = await api_raw("GET", f"/repos/{owner}/{repo}/contents/.github/workflows")
    if listing.status_code == 404:
        return []
    if not listing.is_success:
        raise GhError(error_message(listing), status_code=listing.status_code)
    texts: list[str] = []
    for entry in listing.json():
        if entry.get("type") != "file" or not entry["name"].endswith((".yml", ".yaml")):
            continue
        blob = await api_json("GET", f"/repos/{owner}/{repo}/contents/{entry['path']}")
        texts.append(base64.b64decode(blob["content"]).decode())
    return texts


async def list_repos(
    owner: str | None = None,
    *,
    only: set[str] | None = None,
    skip: set[str] | None = None,
    include_forks: set[str] | None = None,
    require_only_match: bool = False,
) -> list[Repo]:
    """repokit.filter_repos over a fresh fetch, defaulting owner to
    default_owner() and include_forks to default_include_forks()
    (config/include-forks.txt, or GH_INCLUDE_FORKS) when the caller doesn't
    pass one -- repokit itself has no file-backed default, since that's
    repo-admin-specific policy. Warns (once, using this same fetch) about
    any include-forks entry matching no repo.

    `require_only_match=True` raises GhError if any `only` entry matches no
    fetched repo at all (a typo or nonexistent repo name), regardless of
    skip/archived/fork filtering -- opt-in since some callers deliberately
    pass an `only` drawn from a config file that may list a since-removed
    repo (e.g. cmd_pages_sync's own domains-based validation), where a hard
    failure here would be unwelcome.
    """
    if owner is None:
        owner = await default_owner()
    if include_forks is None:
        include_forks = default_include_forks()
    # RepoJSON (a TypedDict) isn't assignable to plain dict per ty -- these
    # helpers work on repo JSON generically, not asyncgh's specific shape.
    repos_json = cast("list[dict]", await fetch_repos(owner))
    for name in sorted(unmatched_include_forks(include_forks, repos_json)):
        print(
            f"warning: include-forks entry {name!r} doesn't match any repo",
            file=sys.stderr,
        )
    if require_only_match and only:
        repo_names = {entry["name"] for entry in repos_json}
        unmatched = only - repo_names
        if unmatched:
            raise GhError(f"not a known repo: {', '.join(sorted(unmatched))}")
    return filter_repos(repos_json, only=only, skip=skip, include_forks=include_forks)
