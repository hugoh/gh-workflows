import pytest

from asyncgh import GhError
from reconcilekit import Status
from repokit import Repo, RepoResult, filter_repos, run_cli

REPOS_JSON = [
    {
        "name": "public-repo",
        "fork": False,
        "archived": False,
        "private": False,
        "default_branch": "main",
    },
    {
        "name": "archived-repo",
        "fork": False,
        "archived": True,
        "private": False,
        "default_branch": "main",
    },
    {
        "name": "fork-repo",
        "fork": True,
        "archived": False,
        "private": False,
        "default_branch": "main",
    },
    {
        "name": "maintained-fork",
        "fork": True,
        "archived": False,
        "private": True,
        "default_branch": "master",
    },
    {
        "name": "empty-repo",
        "fork": False,
        "archived": False,
        "private": False,
        "default_branch": None,
    },
]


def test_filter_repos_excludes_archived():
    repos = filter_repos(REPOS_JSON)
    assert "archived-repo" not in [r.name for r in repos]


def test_filter_repos_excludes_forks_by_default():
    repos = filter_repos(REPOS_JSON)
    assert "fork-repo" not in [r.name for r in repos]
    assert "maintained-fork" not in [r.name for r in repos]


def test_filter_repos_includes_listed_forks():
    repos = filter_repos(REPOS_JSON, include_forks={"maintained-fork"})
    names = [r.name for r in repos]
    assert "maintained-fork" in names
    assert "fork-repo" not in names


def test_filter_repos_only_filter():
    repos = filter_repos(REPOS_JSON, only={"public-repo"})
    assert [r.name for r in repos] == ["public-repo"]


def test_filter_repos_skip_filter():
    repos = filter_repos(REPOS_JSON, skip={"public-repo"})
    assert "public-repo" not in [r.name for r in repos]


def test_filter_repos_fields():
    repos = filter_repos(REPOS_JSON, only={"public-repo"})
    assert repos == [
        Repo(name="public-repo", default_branch="main", is_private=False, is_fork=False)
    ]


def test_filter_repos_reads_homepage():
    repos_json = [
        {
            "name": "site-repo",
            "fork": False,
            "archived": False,
            "private": False,
            "default_branch": "main",
            "homepage": "https://example.com",
        }
    ]
    assert filter_repos(repos_json)[0].homepage == "https://example.com"


def test_filter_repos_homepage_defaults_to_empty_when_null():
    repos = filter_repos(REPOS_JSON, only={"public-repo"})
    assert repos[0].homepage == ""


def test_filter_repos_handles_null_default_branch():
    repos = filter_repos(REPOS_JSON, only={"empty-repo"})
    assert repos[0].default_branch == ""


def test_repo_result_defaults_to_ok_status():
    repo = Repo(name="repo", default_branch="main", is_private=False, is_fork=False)
    assert RepoResult(repo, "line").status == Status.OK


def test_run_cli_runs_entrypoint_and_closes_client(monkeypatch):
    closed = []
    monkeypatch.setattr("repokit.core.aclose_client", _record_close(closed))

    async def entrypoint(args):
        assert args == "ARGS"
        return 0

    assert run_cli(entrypoint, "ARGS") == 0
    assert closed == [True]


def test_run_cli_closes_client_even_when_entrypoint_raises(monkeypatch):
    closed = []
    monkeypatch.setattr("repokit.core.aclose_client", _record_close(closed))

    async def entrypoint(args):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        run_cli(entrypoint, None)
    assert closed == [True]


def test_run_cli_converts_gh_error_to_exit_code_1(monkeypatch, capsys):
    monkeypatch.setattr("repokit.core.aclose_client", _record_close([]))

    async def entrypoint(args):
        raise GhError("nope")

    assert run_cli(entrypoint, None) == 1
    assert "nope" in capsys.readouterr().err


def _record_close(sink):
    async def _aclose():
        sink.append(True)

    return _aclose
