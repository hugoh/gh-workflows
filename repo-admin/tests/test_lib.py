import threading
import time

import lib
import pytest
import responses
from lib import (
    API_BASE,
    GhError,
    Repo,
    RepoResult,
    Status,
    api_json,
    api_request,
    classify_status,
    error_message,
    fetch_repos_json,
    filter_repos,
    print_status,
    result_line,
    run_parallel,
    unmatched_include_forks,
)

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


def test_filter_repos_handles_null_default_branch():
    repos = filter_repos(REPOS_JSON, only={"empty-repo"})
    assert repos[0].default_branch == ""


def test_unmatched_include_forks_returns_names_with_no_matching_repo():
    assert unmatched_include_forks({"maintained-fork", "typo-fork"}, REPOS_JSON) == {
        "typo-fork"
    }


def test_unmatched_include_forks_empty_when_all_match():
    assert (
        unmatched_include_forks({"maintained-fork", "public-repo"}, REPOS_JSON) == set()
    )


def test_unmatched_include_forks_empty_for_no_include_forks():
    assert unmatched_include_forks(set(), REPOS_JSON) == set()


def test_repo_result_defaults_to_ok_status():
    repo = Repo(name="repo", default_branch="main", is_private=False, is_fork=False)
    assert RepoResult(repo, "line").status == Status.OK


def test_print_status_prints_line_to_stdout(capsys):
    print_status(Status.OK, "repo-a done")
    assert "repo-a done" in capsys.readouterr().out


def test_result_line_prefixes_unchanged_status():
    assert result_line("repo", "up to date detail", Status.UNCHANGED) == (
        f"{'repo':<30} unchanged: up to date detail"
    )


def test_result_line_prefixes_limited_unchanged_status():
    assert result_line("repo", "capped detail", Status.LIMITED_UNCHANGED) == (
        f"{'repo':<30} unchanged: capped detail"
    )


@pytest.mark.parametrize("status", [Status.OK, Status.LIMITED, Status.FAILED])
def test_result_line_does_not_prefix_changed_statuses(status):
    assert result_line("repo", "detail", status) == f"{'repo':<30} detail"


def test_classify_status_at_target_and_changed_is_ok():
    assert classify_status(at_target=True, changed=True) == Status.OK


def test_classify_status_at_target_and_unchanged_is_unchanged():
    assert classify_status(at_target=True, changed=False) == Status.UNCHANGED


def test_classify_status_not_at_target_and_changed_is_limited():
    assert classify_status(at_target=False, changed=True) == Status.LIMITED


def test_classify_status_not_at_target_and_unchanged_is_limited_unchanged():
    assert classify_status(at_target=False, changed=False) == Status.LIMITED_UNCHANGED


def test_status_display_pairs_are_unique_per_status():
    # (symbol, color) pairs -- not symbols alone, since e.g. UNCHANGED and
    # LIMITED_UNCHANGED intentionally share the "•" symbol and differ only
    # by color.
    pairs = list(lib._STATUS_DISPLAY.values())
    assert len(pairs) == len(set(pairs)) == len(list(Status))


def test_print_status_does_not_interpret_brackets_in_line_as_markup(capsys):
    print_status(Status.OK, "repo {'allow_auto_merge': True} -> [oops]")
    out = capsys.readouterr().out
    assert "{'allow_auto_merge': True} -> [oops]" in out


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
    assert "bad-repo" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fake_auth_token(monkeypatch):
    # Avoids every test in this module shelling out to the real `gh auth
    # token` -- session creation is lazy and thread-local, so this just
    # needs to be in place before the first api_request/api_json call.
    monkeypatch.setattr("lib._auth_token", lambda: "fake-token")


@responses.activate
def test_error_message_prefers_json_message_field():
    responses.add(
        responses.GET, f"{API_BASE}/x", json={"message": "not found"}, status=404
    )
    response = api_request("GET", "/x")
    assert error_message(response) == "not found"


@responses.activate
def test_error_message_falls_back_to_raw_text_for_non_json_body():
    responses.add(
        responses.GET,
        f"{API_BASE}/x",
        body="plain text error",
        status=500,
        content_type="text/plain",
    )
    response = api_request("GET", "/x")
    assert error_message(response) == "plain text error"


@responses.activate
def test_api_json_returns_parsed_body_on_success():
    responses.add(
        responses.GET,
        f"{API_BASE}/repos/hugoh/gh-workflows",
        json={"name": "gh-workflows"},
        status=200,
    )
    assert api_json("GET", "/repos/hugoh/gh-workflows") == {"name": "gh-workflows"}


@responses.activate
def test_api_json_raises_gh_error_with_status_code_on_failure():
    responses.add(
        responses.GET,
        f"{API_BASE}/repos/hugoh/nope",
        json={"message": "Not Found"},
        status=404,
    )
    with pytest.raises(GhError) as exc_info:
        api_json("GET", "/repos/hugoh/nope")
    assert exc_info.value.status_code == 404
    assert "Not Found" in str(exc_info.value)


@responses.activate
def test_api_json_handles_empty_204_response():
    responses.add(
        responses.PUT,
        f"{API_BASE}/repos/hugoh/gh-workflows/vulnerability-alerts",
        status=204,
    )
    assert api_json("PUT", "/repos/hugoh/gh-workflows/vulnerability-alerts") == {}


@responses.activate
def test_api_request_does_not_raise_on_http_error_status():
    responses.add(
        responses.GET,
        f"{API_BASE}/repos/hugoh/private-repo/private-vulnerability-reporting",
        status=404,
    )
    response = api_request(
        "GET", "/repos/hugoh/private-repo/private-vulnerability-reporting"
    )
    assert response.status_code == 404


@responses.activate
def test_fetch_repos_json_uses_authenticated_user_repos_when_owner_matches():
    responses.add(
        responses.GET, f"{API_BASE}/user", json={"login": "hugoh"}, status=200
    )
    responses.add(
        responses.GET, f"{API_BASE}/user/repos", json=[{"name": "a"}], status=200
    )
    assert fetch_repos_json("hugoh") == [{"name": "a"}]


@responses.activate
def test_fetch_repos_json_falls_back_to_public_repos_for_other_owners():
    responses.add(
        responses.GET, f"{API_BASE}/user", json={"login": "hugoh"}, status=200
    )
    responses.add(
        responses.GET,
        f"{API_BASE}/users/someorg/repos",
        json=[{"name": "b"}],
        status=200,
    )
    assert fetch_repos_json("someorg") == [{"name": "b"}]


@responses.activate
def test_fetch_repos_json_follows_pagination_link_header():
    responses.add(
        responses.GET, f"{API_BASE}/user", json={"login": "hugoh"}, status=200
    )
    responses.add(
        responses.GET,
        f"{API_BASE}/user/repos",
        json=[{"name": "page1"}],
        status=200,
        headers={"Link": f'<{API_BASE}/user/repos?page=2>; rel="next"'},
    )
    responses.add(
        responses.GET,
        f"{API_BASE}/user/repos?page=2",
        json=[{"name": "page2"}],
        status=200,
    )
    assert fetch_repos_json("hugoh") == [{"name": "page1"}, {"name": "page2"}]
