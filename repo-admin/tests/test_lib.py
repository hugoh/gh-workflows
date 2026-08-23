import asyncio
import io
import subprocess

import httpx
import lib
import pytest
import respx
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
    public_repos_json,
    result_line,
    run_parallel,
    unmatched_include_forks,
)
from nacl import encoding, public
from rich.console import Console

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


def test_default_pages_domains_reads_mapping_from_file(tmp_path, monkeypatch):
    domains_file = tmp_path / "pages-domains.yaml"
    domains_file.write_text("awesome-jj: awesome-jj.larve.net\nhrd: hrd.larve.net\n")
    monkeypatch.setattr(lib, "PAGES_DOMAINS_FILE", domains_file)
    assert lib.default_pages_domains() == {
        "awesome-jj": "awesome-jj.larve.net",
        "hrd": "hrd.larve.net",
    }


def test_default_pages_domains_ignores_comments(tmp_path, monkeypatch):
    domains_file = tmp_path / "pages-domains.yaml"
    domains_file.write_text("# a comment\nawesome-jj: awesome-jj.larve.net\n")
    monkeypatch.setattr(lib, "PAGES_DOMAINS_FILE", domains_file)
    assert lib.default_pages_domains() == {"awesome-jj": "awesome-jj.larve.net"}


def test_default_branch_protection_exclude_reads_names_from_file(tmp_path, monkeypatch):
    exclude_file = tmp_path / "branch-protection-exclude.txt"
    exclude_file.write_text("homebrew-tap\n")
    monkeypatch.setattr(lib, "BRANCH_PROTECTION_EXCLUDE_FILE", exclude_file)
    monkeypatch.delenv("GH_BRANCH_PROTECTION_EXCLUDE", raising=False)
    assert lib.default_branch_protection_exclude() == {"homebrew-tap"}


def test_default_branch_protection_exclude_ignores_comments_and_blank_lines(
    tmp_path, monkeypatch
):
    exclude_file = tmp_path / "branch-protection-exclude.txt"
    exclude_file.write_text("# a comment\n\nhomebrew-tap\n")
    monkeypatch.setattr(lib, "BRANCH_PROTECTION_EXCLUDE_FILE", exclude_file)
    monkeypatch.delenv("GH_BRANCH_PROTECTION_EXCLUDE", raising=False)
    assert lib.default_branch_protection_exclude() == {"homebrew-tap"}


def test_default_branch_protection_exclude_env_override(tmp_path, monkeypatch):
    exclude_file = tmp_path / "branch-protection-exclude.txt"
    exclude_file.write_text("homebrew-tap\n")
    monkeypatch.setattr(lib, "BRANCH_PROTECTION_EXCLUDE_FILE", exclude_file)
    monkeypatch.setenv("GH_BRANCH_PROTECTION_EXCLUDE", "other-repo,another-repo")
    assert lib.default_branch_protection_exclude() == {"other-repo", "another-repo"}


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


def test_progress_bar_disabled_when_console_is_not_a_terminal():
    non_tty_console = Console(file=io.StringIO())
    assert lib.progress_bar(console=non_tty_console).disable is True


def test_progress_bar_enabled_when_console_is_a_terminal():
    tty_console = Console(file=io.StringIO(), force_terminal=True)
    assert lib.progress_bar(console=tty_console).disable is False


def test_progress_bar_prints_nothing_when_disabled():
    output = io.StringIO()
    non_tty_console = Console(file=output)
    with lib.progress_bar(console=non_tty_console) as bar:
        task = bar.add_task("working", total=2)
        bar.advance(task)
        bar.advance(task)
    assert output.getvalue() == ""


async def test_run_parallel_returns_worker_results_for_every_repo():
    repos = [
        Repo(name=f"repo{i}", default_branch="main", is_private=False, is_fork=False)
        for i in range(5)
    ]

    async def worker(repo):
        return RepoResult(repo=repo, line=f"{repo.name} done")

    results = await run_parallel(repos, worker, jobs=3)
    assert sorted(r.repo.name for r in results) == sorted(r.name for r in repos)


async def test_run_parallel_prints_each_worker_result_line(capsys):
    repos = [
        Repo(name="repo-a", default_branch="main", is_private=False, is_fork=False)
    ]

    async def worker(repo):
        return RepoResult(repo=repo, line=f"{repo.name} line")

    await run_parallel(repos, worker, jobs=1)
    assert "repo-a line" in capsys.readouterr().out


async def test_run_parallel_runs_workers_concurrently():
    repos = [
        Repo(name=f"repo{i}", default_branch="main", is_private=False, is_fork=False)
        for i in range(4)
    ]
    barrier = asyncio.Barrier(4)

    async def worker(repo):
        await barrier.wait()
        return RepoResult(repo=repo, line=repo.name)

    # If run_parallel executed workers serially, the barrier would never
    # release with only 1 worker present at a time and this would time out.
    await asyncio.wait_for(run_parallel(repos, worker, jobs=4), timeout=2)


async def test_run_parallel_one_failure_does_not_block_others():
    repos = [
        Repo(name=f"repo{i}", default_branch="main", is_private=False, is_fork=False)
        for i in range(3)
    ]
    completed = []

    async def worker(repo):
        if repo.name == "repo1":
            raise RuntimeError("boom")
        await asyncio.sleep(0.05)
        completed.append(repo.name)
        return RepoResult(repo=repo, line=repo.name)

    with pytest.raises(GhError):
        await run_parallel(repos, worker, jobs=3)

    assert sorted(completed) == ["repo0", "repo2"]


async def test_run_parallel_reports_failed_repo_names_in_error(capsys):
    repos = [
        Repo(name="bad-repo", default_branch="main", is_private=False, is_fork=False)
    ]

    async def worker(repo):
        raise RuntimeError("network error")

    with pytest.raises(GhError, match="bad-repo"):
        await run_parallel(repos, worker, jobs=1)
    assert "bad-repo" in capsys.readouterr().out


async def test_run_parallel_hides_unchanged_lines_by_default(capsys):
    repos = [
        Repo(name="repo-a", default_branch="main", is_private=False, is_fork=False)
    ]

    async def worker(repo):
        return RepoResult(repo=repo, line=f"{repo.name} line", status=Status.UNCHANGED)

    await run_parallel(repos, worker, jobs=1)
    out = capsys.readouterr().out
    assert "repo-a line" not in out
    assert "1 unchanged" in out


async def test_run_parallel_verbose_shows_unchanged_lines_without_summary(capsys):
    repos = [
        Repo(name="repo-a", default_branch="main", is_private=False, is_fork=False)
    ]

    async def worker(repo):
        return RepoResult(repo=repo, line=f"{repo.name} line", status=Status.UNCHANGED)

    await run_parallel(repos, worker, jobs=1, verbose=True)
    out = capsys.readouterr().out
    assert "repo-a line" in out
    assert "unchanged" not in out.replace("repo-a line", "")


@pytest.mark.parametrize("status", [Status.OK, Status.FAILED, Status.LIMITED])
async def test_run_parallel_always_shows_non_unchanged_lines(capsys, status):
    repos = [
        Repo(name="repo-a", default_branch="main", is_private=False, is_fork=False)
    ]

    async def worker(repo):
        if status is Status.FAILED:
            raise RuntimeError("boom")
        return RepoResult(repo=repo, line=f"{repo.name} line", status=status)

    if status is Status.FAILED:
        with pytest.raises(GhError):
            await run_parallel(repos, worker, jobs=1)
        out = capsys.readouterr().out
        assert "repo-a" in out
    else:
        await run_parallel(repos, worker, jobs=1)
        out = capsys.readouterr().out
        assert "repo-a line" in out
    assert "unchanged" not in out


async def test_run_parallel_hides_limited_unchanged_lines_by_default(capsys):
    repos = [
        Repo(name="repo-a", default_branch="main", is_private=False, is_fork=False)
    ]

    async def worker(repo):
        return RepoResult(
            repo=repo, line=f"{repo.name} line", status=Status.LIMITED_UNCHANGED
        )

    await run_parallel(repos, worker, jobs=1)
    out = capsys.readouterr().out
    assert "repo-a line" not in out
    assert "1 unchanged" in out


def test_default_secrets_reads_repo_list(tmp_path, monkeypatch):
    secrets_file = tmp_path / "secrets.yaml"
    secrets_file.write_text("TAP_GITHUB_TOKEN:\n  repos: [hrd, jj-trim, netcheck]\n")
    monkeypatch.setattr(lib, "SECRETS_FILE", secrets_file)
    assert lib.default_secrets() == {"TAP_GITHUB_TOKEN": ["hrd", "jj-trim", "netcheck"]}


def test_default_secrets_empty_file_returns_empty_dict(tmp_path, monkeypatch):
    secrets_file = tmp_path / "secrets.yaml"
    secrets_file.write_text("")
    monkeypatch.setattr(lib, "SECRETS_FILE", secrets_file)
    assert lib.default_secrets() == {}


@pytest.fixture
def enc_file(tmp_path, monkeypatch):
    path = tmp_path / "secrets.enc.yaml"
    monkeypatch.setattr(lib, "SECRETS_ENC_FILE", path)
    return path


def test_decrypt_secrets_calls_sops_and_parses_yaml(enc_file, monkeypatch):
    enc_file.write_text("placeholder")

    def fake_run(cmd, **kwargs):
        assert cmd == ["sops", "-d", str(enc_file)]
        return subprocess.CompletedProcess(cmd, 0, stdout="NAME: value\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert lib.decrypt_secrets() == {"NAME": "value"}


def test_decrypt_secrets_strips_sops_metadata_key(enc_file, monkeypatch):
    enc_file.write_text("placeholder")

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 0, stdout="NAME: value\nsops:\n    age: []\n", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert lib.decrypt_secrets() == {"NAME": "value"}


def test_decrypt_secrets_raises_gh_error_on_nonzero_exit(enc_file, monkeypatch):
    enc_file.write_text("placeholder")

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no key found")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(GhError, match="no key found"):
        lib.decrypt_secrets()


def test_decrypt_secrets_raises_gh_error_when_sops_not_on_path(enc_file, monkeypatch):
    enc_file.write_text("placeholder")

    def fake_run(cmd, **kwargs):
        raise FileNotFoundError("sops")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(GhError, match="sops not found"):
        lib.decrypt_secrets()


def test_decrypt_secrets_raises_gh_error_when_file_missing(enc_file, monkeypatch):
    def fail_run(cmd, **kwargs):
        raise AssertionError("should not shell out to sops when the file is missing")

    monkeypatch.setattr(subprocess, "run", fail_run)
    with pytest.raises(GhError, match="not found"):
        lib.decrypt_secrets()


def test_encrypt_secret_value_round_trips_through_sealed_box():
    private_key = public.PrivateKey.generate()
    public_key_b64 = private_key.public_key.encode(encoding.Base64Encoder).decode(
        "utf-8"
    )

    ciphertext_b64 = lib.encrypt_secret_value(public_key_b64, "super-secret-value")

    import base64

    decrypted = public.SealedBox(private_key).decrypt(base64.b64decode(ciphertext_b64))
    assert decrypted == b"super-secret-value"


def test_init_secrets_file_encrypts_template_via_sops_stdin(
    enc_file, tmp_path, monkeypatch
):
    config_file = tmp_path / ".sops.yaml"
    monkeypatch.setattr(lib, "SOPS_CONFIG_FILE", config_file)

    def fake_run(cmd, **kwargs):
        assert cmd == [
            "sops",
            "--encrypt",
            "--config",
            str(config_file),
            "--filename-override",
            str(enc_file),
            "--input-type",
            "yaml",
            "--output-type",
            "yaml",
            "/dev/stdin",
        ]
        assert kwargs["input"] == "NAME: ''\n"
        return subprocess.CompletedProcess(cmd, 0, stdout="NAME: ENC[...]\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    lib.init_secrets_file("NAME: ''\n")
    assert enc_file.read_text() == "NAME: ENC[...]\n"


def test_init_secrets_file_raises_gh_error_on_nonzero_exit(enc_file, monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no key found")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(GhError, match="no key found"):
        lib.init_secrets_file("NAME: ''\n")
    assert not enc_file.exists()


def test_init_secrets_file_raises_gh_error_when_sops_not_on_path(enc_file, monkeypatch):
    def fake_run(cmd, **kwargs):
        raise FileNotFoundError("sops")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(GhError, match="sops not found"):
        lib.init_secrets_file("NAME: ''\n")


def test_edit_secrets_file_runs_sops_on_the_file_and_returns_exit_code(
    enc_file, monkeypatch
):
    def fake_run(cmd, **kwargs):
        assert cmd == ["sops", str(enc_file)]
        assert "capture_output" not in kwargs  # inherits stdio for the editor session
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert lib.edit_secrets_file() == 0


def test_edit_secrets_file_returns_nonzero_exit_code(enc_file, monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert lib.edit_secrets_file() == 1


def test_edit_secrets_file_raises_gh_error_when_sops_not_on_path(enc_file, monkeypatch):
    def fake_run(cmd, **kwargs):
        raise FileNotFoundError("sops")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(GhError, match="sops not found"):
        lib.edit_secrets_file()


async def test_set_repo_secret_encrypts_and_puts_with_key_id(monkeypatch):
    private_key = public.PrivateKey.generate()
    public_key_b64 = private_key.public_key.encode(encoding.Base64Encoder).decode(
        "utf-8"
    )
    calls = []

    async def fake_api_json(method, path, **kwargs):
        if method == "GET":
            return {"key": public_key_b64, "key_id": "key-id-123"}
        calls.append((method, path, kwargs.get("json")))
        return {}

    monkeypatch.setattr(lib, "api_json", fake_api_json)
    await lib.set_repo_secret("hugoh", "repo", "NAME", "the-value")

    assert len(calls) == 1
    method, path, body = calls[0]
    assert method == "PUT"
    assert path == "/repos/hugoh/repo/actions/secrets/NAME"
    assert body["key_id"] == "key-id-123"

    import base64

    decrypted = public.SealedBox(private_key).decrypt(
        base64.b64decode(body["encrypted_value"])
    )
    assert decrypted == b"the-value"


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fake_auth_token(monkeypatch):
    # Avoids every test in this module shelling out to the real `gh auth
    # token` -- client creation is lazy, so this just needs to be in place
    # before the first api_request/api_json call.
    monkeypatch.setattr("lib._auth_token", lambda: "fake-token")


async def test_error_message_prefers_json_message_field(httpx2_mock: respx.Router):
    httpx2_mock.get(f"{API_BASE}/x").mock(
        return_value=httpx.Response(404, json={"message": "not found"})
    )
    response = await api_request("GET", "/x")
    assert error_message(response) == "not found"


async def test_error_message_falls_back_to_raw_text_for_non_json_body(
    httpx2_mock: respx.Router,
):
    httpx2_mock.get(f"{API_BASE}/x").mock(
        return_value=httpx.Response(
            500, text="plain text error", headers={"Content-Type": "text/plain"}
        )
    )
    response = await api_request("GET", "/x")
    assert error_message(response) == "plain text error"


async def test_api_json_returns_parsed_body_on_success(httpx2_mock: respx.Router):
    httpx2_mock.get(f"{API_BASE}/repos/hugoh/gh-workflows").mock(
        return_value=httpx.Response(200, json={"name": "gh-workflows"})
    )
    assert await api_json("GET", "/repos/hugoh/gh-workflows") == {
        "name": "gh-workflows"
    }


async def test_api_json_raises_gh_error_with_status_code_on_failure(
    httpx2_mock: respx.Router,
):
    httpx2_mock.get(f"{API_BASE}/repos/hugoh/nope").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )
    with pytest.raises(GhError) as exc_info:
        await api_json("GET", "/repos/hugoh/nope")
    assert exc_info.value.status_code == 404
    assert "Not Found" in str(exc_info.value)


async def test_api_json_handles_empty_204_response(httpx2_mock: respx.Router):
    httpx2_mock.put(f"{API_BASE}/repos/hugoh/gh-workflows/vulnerability-alerts").mock(
        return_value=httpx.Response(204)
    )
    assert await api_json("PUT", "/repos/hugoh/gh-workflows/vulnerability-alerts") == {}


async def test_api_request_does_not_raise_on_http_error_status(
    httpx2_mock: respx.Router,
):
    httpx2_mock.get(
        f"{API_BASE}/repos/hugoh/private-repo/private-vulnerability-reporting"
    ).mock(return_value=httpx.Response(404))
    response = await api_request(
        "GET", "/repos/hugoh/private-repo/private-vulnerability-reporting"
    )
    assert response.status_code == 404


async def test_fetch_repos_json_uses_authenticated_user_repos_when_owner_matches(
    httpx2_mock: respx.Router,
):
    httpx2_mock.get(f"{API_BASE}/user").mock(
        return_value=httpx.Response(200, json={"login": "hugoh"})
    )
    httpx2_mock.get(f"{API_BASE}/user/repos").mock(
        return_value=httpx.Response(200, json=[{"name": "a"}])
    )
    assert await fetch_repos_json("hugoh") == [{"name": "a"}]


async def test_fetch_repos_json_falls_back_to_public_repos_for_other_owners(
    httpx2_mock: respx.Router,
):
    httpx2_mock.get(f"{API_BASE}/user").mock(
        return_value=httpx.Response(200, json={"login": "hugoh"})
    )
    httpx2_mock.get(f"{API_BASE}/users/someorg/repos").mock(
        return_value=httpx.Response(200, json=[{"name": "b"}])
    )
    assert await fetch_repos_json("someorg") == [{"name": "b"}]


async def test_fetch_repos_json_follows_pagination_link_header(
    httpx2_mock: respx.Router,
):
    httpx2_mock.get(f"{API_BASE}/user").mock(
        return_value=httpx.Response(200, json={"login": "hugoh"})
    )
    # respx routes are tried in registration order and a route with no
    # `params` constraint matches any query string -- the page=2 route must
    # be registered first, or the unconstrained page-1 route below would
    # swallow it too and _paginated would loop on page1 forever.
    httpx2_mock.get(f"{API_BASE}/user/repos", params={"page": "2"}).mock(
        return_value=httpx.Response(200, json=[{"name": "page2"}])
    )
    httpx2_mock.get(f"{API_BASE}/user/repos").mock(
        return_value=httpx.Response(
            200,
            json=[{"name": "page1"}],
            headers={"Link": f'<{API_BASE}/user/repos?page=2>; rel="next"'},
        )
    )
    assert await fetch_repos_json("hugoh") == [{"name": "page1"}, {"name": "page2"}]


async def test_public_repos_json_uses_public_users_endpoint_even_for_self(
    httpx2_mock: respx.Router,
):
    # /users/{owner}/repos only ever returns public repos, even when owner is
    # the authenticated user -- unlike fetch_repos_json, no /user call is
    # needed to check whether owner is the viewer.
    httpx2_mock.get(f"{API_BASE}/users/hugoh/repos").mock(
        return_value=httpx.Response(200, json=[{"name": "public-repo"}])
    )
    assert await public_repos_json("hugoh") == [{"name": "public-repo"}]
    assert not any(call.request.url.path == "/user" for call in httpx2_mock.calls)


async def test_public_repos_json_follows_pagination_link_header(
    httpx2_mock: respx.Router,
):
    httpx2_mock.get(f"{API_BASE}/users/hugoh/repos", params={"page": "2"}).mock(
        return_value=httpx.Response(200, json=[{"name": "page2"}])
    )
    httpx2_mock.get(f"{API_BASE}/users/hugoh/repos").mock(
        return_value=httpx.Response(
            200,
            json=[{"name": "page1"}],
            headers={"Link": f'<{API_BASE}/users/hugoh/repos?page=2>; rel="next"'},
        )
    )
    assert await public_repos_json("hugoh") == [{"name": "page1"}, {"name": "page2"}]
