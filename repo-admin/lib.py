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

import os
import subprocess
import sys
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
    DEFAULT_OWNER,
    Repo,
    RepoResult,
    as_set,
    filter_repos,
    run_cli,
    run_parallel,
)

__all__ = [  # re-exported from asyncgh / reconcilekit / repokit for repo-admin's modules
    "API_BASE",
    "DEFAULT_JOBS",
    "DEFAULT_OWNER",
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
    external-trusted-CLI style as asyncgh's `gh auth token` call.
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


async def list_repos(
    owner: str = DEFAULT_OWNER,
    *,
    only: set[str] | None = None,
    skip: set[str] | None = None,
    include_forks: set[str] | None = None,
) -> list[Repo]:
    """repokit.filter_repos over a fresh fetch, defaulting include_forks to
    default_include_forks() (config/include-forks.txt, or GH_INCLUDE_FORKS)
    when the caller doesn't pass one -- repokit itself has no file-backed
    default, since that's repo-admin-specific policy. Warns (once, using
    this same fetch) about any include-forks entry matching no repo.
    """
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
    return filter_repos(repos_json, only=only, skip=skip, include_forks=include_forks)
