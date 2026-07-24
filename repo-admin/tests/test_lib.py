import threading
import time

import pytest

from lib import GhError, Repo, RepoResult, filter_repos, run_parallel

REPOS_JSON = [
    {
        "name": "public-repo",
        "isFork": False,
        "isArchived": False,
        "isPrivate": False,
        "defaultBranchRef": {"name": "main"},
    },
    {
        "name": "archived-repo",
        "isFork": False,
        "isArchived": True,
        "isPrivate": False,
        "defaultBranchRef": {"name": "main"},
    },
    {
        "name": "fork-repo",
        "isFork": True,
        "isArchived": False,
        "isPrivate": False,
        "defaultBranchRef": {"name": "main"},
    },
    {
        "name": "maintained-fork",
        "isFork": True,
        "isArchived": False,
        "isPrivate": True,
        "defaultBranchRef": {"name": "master"},
    },
    {
        "name": "empty-repo",
        "isFork": False,
        "isArchived": False,
        "isPrivate": False,
        "defaultBranchRef": None,
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


def test_filter_repos_handles_null_default_branch_ref():
    repos = filter_repos(REPOS_JSON, only={"empty-repo"})
    assert repos[0].default_branch == ""


def test_run_parallel_returns_worker_results_for_every_repo():
    repos = [
        Repo(name=f"repo{i}", default_branch="main", is_private=False, is_fork=False)
        for i in range(5)
    ]

    def worker(repo):
        return RepoResult(repo=repo, line=f"{repo.name} done")

    results = run_parallel(repos, worker, jobs=3)
    assert sorted(r.repo.name for r in results) == sorted(r.name for r in repos)


def test_run_parallel_prints_each_worker_result_line(capsys):
    repos = [
        Repo(name="repo-a", default_branch="main", is_private=False, is_fork=False)
    ]

    def worker(repo):
        return RepoResult(repo=repo, line=f"{repo.name} line")

    run_parallel(repos, worker, jobs=1)
    assert "repo-a line" in capsys.readouterr().out


def test_run_parallel_runs_workers_concurrently():
    repos = [
        Repo(name=f"repo{i}", default_branch="main", is_private=False, is_fork=False)
        for i in range(4)
    ]
    barrier = threading.Barrier(4, timeout=2)

    def worker(repo):
        barrier.wait()
        return RepoResult(repo=repo, line=repo.name)

    # If run_parallel executed workers serially, the barrier would never
    # release with only 1 worker present at a time and this would time out.
    run_parallel(repos, worker, jobs=4)


def test_run_parallel_one_failure_does_not_block_others():
    repos = [
        Repo(name=f"repo{i}", default_branch="main", is_private=False, is_fork=False)
        for i in range(3)
    ]
    completed = []

    def worker(repo):
        if repo.name == "repo1":
            raise RuntimeError("boom")
        time.sleep(0.05)
        completed.append(repo.name)
        return RepoResult(repo=repo, line=repo.name)

    with pytest.raises(GhError):
        run_parallel(repos, worker, jobs=3)

    assert sorted(completed) == ["repo0", "repo2"]


def test_run_parallel_reports_failed_repo_names_in_error(capsys):
    repos = [
        Repo(name="bad-repo", default_branch="main", is_private=False, is_fork=False)
    ]

    def worker(repo):
        raise RuntimeError("network error")

    with pytest.raises(GhError, match="bad-repo"):
        run_parallel(repos, worker, jobs=1)
    assert "bad-repo" in capsys.readouterr().err
