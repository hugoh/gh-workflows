from lib import Repo

import repo_admin

REPO = Repo(name="repo", default_branch="main", is_private=False, is_fork=False)


# ---------------------------------------------------------------------------
# merge-settings
# ---------------------------------------------------------------------------


def test_merge_settings_dry_run_line_up_to_date():
    current = {
        "allow_auto_merge": True,
        "delete_branch_on_merge": True,
        "allow_update_branch": True,
    }
    assert (
        repo_admin.merge_settings_dry_run_line("repo", current)
        == f"{'repo':<30} up to date"
    )


def test_merge_settings_dry_run_line_lists_disabled_in_field_order():
    current = {
        "allow_auto_merge": False,
        "delete_branch_on_merge": True,
        "allow_update_branch": False,
    }
    line = repo_admin.merge_settings_dry_run_line("repo", current)
    assert line == f"{'repo':<30} would enable: allow_auto_merge, allow_update_branch"


def test_merge_settings_apply_line_unchanged():
    settings = {
        "allow_auto_merge": True,
        "delete_branch_on_merge": True,
        "allow_update_branch": True,
    }
    assert (
        repo_admin.merge_settings_apply_line("repo", settings, settings)
        == f"{'repo':<30} unchanged {settings}"
    )


def test_merge_settings_apply_line_changed():
    before = {
        "allow_auto_merge": False,
        "delete_branch_on_merge": True,
        "allow_update_branch": True,
    }
    after = {
        "allow_auto_merge": True,
        "delete_branch_on_merge": True,
        "allow_update_branch": True,
    }
    line = repo_admin.merge_settings_apply_line("repo", before, after)
    assert line == f"{'repo':<30} {before} -> {after}"


def test_merge_settings_worker_dry_run(monkeypatch):
    monkeypatch.setattr(
        repo_admin,
        "_merge_settings",
        lambda owner, name: {
            "allow_auto_merge": False,
            "delete_branch_on_merge": True,
            "allow_update_branch": True,
        },
    )
    worker = repo_admin.make_merge_settings_worker(owner="hugoh", dry_run=True)
    result = worker(REPO)
    assert "would enable: allow_auto_merge" in result.line


# ---------------------------------------------------------------------------
# security-features
# ---------------------------------------------------------------------------


def test_security_summarize_all_available_and_enabled():
    repo_json = {
        "security_and_analysis": {
            "secret_scanning": {"status": "enabled"},
            "secret_scanning_push_protection": {"status": "enabled"},
            "dependabot_security_updates": {"status": "enabled"},
        }
    }
    summary = repo_admin.security_summarize(
        repo_json, vuln_alerts_enabled=True, pvr_json={"enabled": True}
    )
    assert summary == {"would_enable": [], "unavailable": []}


def test_security_summarize_reports_would_enable():
    repo_json = {
        "security_and_analysis": {
            "secret_scanning": {"status": "disabled"},
            "secret_scanning_push_protection": {"status": "enabled"},
            "dependabot_security_updates": {"status": "enabled"},
        }
    }
    summary = repo_admin.security_summarize(
        repo_json, vuln_alerts_enabled=False, pvr_json={"enabled": True}
    )
    assert set(summary["would_enable"]) == {"vuln_alerts", "secret_scanning"}
    assert summary["unavailable"] == []


def test_security_summarize_reports_unavailable_for_private_repo():
    # Private repos without GitHub Advanced Security: these keys are absent
    # from security_and_analysis, and private-vulnerability-reporting 404s.
    repo_json = {"security_and_analysis": {}}
    summary = repo_admin.security_summarize(
        repo_json, vuln_alerts_enabled=True, pvr_json=None
    )
    assert set(summary["unavailable"]) == {
        "secret_scanning",
        "push_protection",
        "dependabot_updates",
        "private_vuln_reporting",
    }
    assert summary["would_enable"] == []


def test_security_dry_run_line_up_to_date():
    line = repo_admin.security_dry_run_line(
        "repo", {"would_enable": [], "unavailable": []}
    )
    assert line == f"{'repo':<30} up to date"


def test_security_dry_run_line_would_enable_and_unavailable():
    line = repo_admin.security_dry_run_line(
        "repo", {"would_enable": ["vuln_alerts"], "unavailable": ["push_protection"]}
    )
    assert (
        line == f"{'repo':<30} would enable: vuln_alerts (unavailable: push_protection)"
    )


# ---------------------------------------------------------------------------
# branch-protection
# ---------------------------------------------------------------------------


def test_branch_protection_up_to_date_matches_baseline():
    current = {
        "required_status_checks": {"contexts": ["build", "test"], "strict": True},
        "enforce_admins": {"enabled": True},
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
        "required_pull_request_reviews": {"required_approving_review_count": 0},
    }
    assert repo_admin.branch_protection_up_to_date(current, ["test", "build"]) is True


def test_branch_protection_up_to_date_false_when_contexts_differ():
    current = {
        "required_status_checks": {"contexts": ["build"], "strict": True},
        "enforce_admins": {"enabled": True},
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
        "required_pull_request_reviews": {"required_approving_review_count": 0},
    }
    assert repo_admin.branch_protection_up_to_date(current, ["test", "build"]) is False


def test_branch_protection_up_to_date_false_when_unprotected():
    assert repo_admin.branch_protection_up_to_date(None, ["build"]) is False


def test_branch_protection_payload():
    payload = repo_admin.branch_protection_payload(["build", "test"])
    assert payload == {
        "required_status_checks": {"strict": True, "contexts": ["build", "test"]},
        "enforce_admins": True,
        "required_pull_request_reviews": {"required_approving_review_count": 0},
        "restrictions": None,
        "allow_force_pushes": False,
        "allow_deletions": False,
    }
