import subprocess

import httpx
import lib
import pytest
import respx
from lib import API_BASE, GhError, unmatched_include_forks

REPOS_JSON = [
    {
        "name": "public-repo",
        "fork": False,
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
]


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


async def test_default_owner_uses_gh_owner_env_without_a_network_call(monkeypatch):
    monkeypatch.setenv("GH_OWNER", "env-owner")
    assert await lib.default_owner() == "env-owner"


async def test_default_owner_falls_back_to_authenticated_user(
    monkeypatch, httpx2_mock: respx.Router
):
    monkeypatch.delenv("GH_OWNER", raising=False)
    monkeypatch.setattr("asyncgh.client._auth_token", lambda: "fake-token")
    httpx2_mock.get(f"{API_BASE}/user").mock(
        return_value=httpx.Response(200, json={"login": "authenticated-user"})
    )
    assert await lib.default_owner() == "authenticated-user"


async def test_default_owner_caches_the_resolved_value(
    monkeypatch, httpx2_mock: respx.Router
):
    monkeypatch.delenv("GH_OWNER", raising=False)
    monkeypatch.setattr("asyncgh.client._auth_token", lambda: "fake-token")
    route = httpx2_mock.get(f"{API_BASE}/user").mock(
        return_value=httpx.Response(200, json={"login": "authenticated-user"})
    )
    await lib.default_owner()
    await lib.default_owner()
    assert route.call_count == 1


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
