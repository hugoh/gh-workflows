import subprocess

import lib
import pytest
from lib import (
    GhError,
    Repo,
    RepoResult,
    Status,
    filter_repos,
    run_cli,
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


def test_run_cli_runs_entrypoint_and_closes_client(monkeypatch):
    closed = []
    monkeypatch.setattr(lib, "aclose_client", _record_close(closed))

    async def entrypoint(args):
        assert args == "ARGS"
        return 0

    assert run_cli(entrypoint, "ARGS") == 0
    assert closed == [True]


def test_run_cli_closes_client_even_when_entrypoint_raises(monkeypatch):
    closed = []
    monkeypatch.setattr(lib, "aclose_client", _record_close(closed))

    async def entrypoint(args):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        run_cli(entrypoint, None)
    assert closed == [True]


def test_run_cli_converts_gh_error_to_exit_code_1(monkeypatch, capsys):
    monkeypatch.setattr(lib, "aclose_client", _record_close([]))

    async def entrypoint(args):
        raise GhError("nope")

    assert run_cli(entrypoint, None) == 1
    assert "nope" in capsys.readouterr().err


def _record_close(sink):
    async def _aclose():
        sink.append(True)

    return _aclose
