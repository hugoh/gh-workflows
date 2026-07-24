import repo_admin
from lib import Repo, Status

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


def test_merge_settings_at_target_true_when_all_enabled():
    assert repo_admin.merge_settings_at_target(
        {
            "allow_auto_merge": True,
            "delete_branch_on_merge": True,
            "allow_update_branch": True,
        }
    )


def test_merge_settings_at_target_false_when_any_disabled():
    assert not repo_admin.merge_settings_at_target(
        {
            "allow_auto_merge": True,
            "delete_branch_on_merge": False,
            "allow_update_branch": True,
        }
    )


def test_merge_settings_worker_dry_run_unchanged_when_at_target(monkeypatch):
    monkeypatch.setattr(
        repo_admin,
        "_merge_settings",
        lambda owner, name: {
            "allow_auto_merge": True,
            "delete_branch_on_merge": True,
            "allow_update_branch": True,
        },
    )
    worker = repo_admin.make_merge_settings_worker(owner="hugoh", dry_run=True)
    assert worker(REPO).status == Status.UNCHANGED


def test_merge_settings_worker_dry_run_ok_when_would_reach_target(monkeypatch):
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
    assert worker(REPO).status == Status.OK


def _merge_settings_apply_worker(monkeypatch, before, after):
    calls = iter([before, after])
    monkeypatch.setattr(repo_admin, "_merge_settings", lambda owner, name: next(calls))
    monkeypatch.setattr(repo_admin, "api_json", lambda *a, **k: {})
    return repo_admin.make_merge_settings_worker(owner="hugoh", dry_run=False)


AT_TARGET = {
    "allow_auto_merge": True,
    "delete_branch_on_merge": True,
    "allow_update_branch": True,
}
NOT_AT_TARGET = {
    "allow_auto_merge": False,
    "delete_branch_on_merge": True,
    "allow_update_branch": True,
}
PARTIALLY_FIXED_NOT_AT_TARGET = {
    "allow_auto_merge": False,
    "delete_branch_on_merge": True,
    "allow_update_branch": False,
}


def test_merge_settings_worker_apply_ok_when_reaches_target(monkeypatch):
    worker = _merge_settings_apply_worker(monkeypatch, NOT_AT_TARGET, AT_TARGET)
    assert worker(REPO).status == Status.OK


def test_merge_settings_worker_apply_unchanged_when_already_at_target(monkeypatch):
    worker = _merge_settings_apply_worker(monkeypatch, AT_TARGET, AT_TARGET)
    assert worker(REPO).status == Status.UNCHANGED


def test_merge_settings_worker_apply_limited_when_still_not_at_target(monkeypatch):
    worker = _merge_settings_apply_worker(monkeypatch, NOT_AT_TARGET, NOT_AT_TARGET)
    assert worker(REPO).status == Status.LIMITED_UNCHANGED


def test_merge_settings_worker_apply_limited_when_partially_fixed(monkeypatch):
    worker = _merge_settings_apply_worker(
        monkeypatch, NOT_AT_TARGET, PARTIALLY_FIXED_NOT_AT_TARGET
    )
    assert worker(REPO).status == Status.LIMITED


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


FULLY_ENABLED_REPO_JSON = {
    "security_and_analysis": {
        "secret_scanning": {"status": "enabled"},
        "secret_scanning_push_protection": {"status": "enabled"},
        "dependabot_security_updates": {"status": "enabled"},
    }
}
UNAVAILABLE_REPO_JSON = {"security_and_analysis": {}}


class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code
        self.ok = status_code < 400

    def json(self):
        return {}


def _security_worker(
    monkeypatch, *, repo_json, vuln_alerts_enabled, pvr_json, dry_run, api_responses=()
):
    monkeypatch.setattr(
        repo_admin,
        "_fetch_security_state",
        lambda owner, name: (repo_json, vuln_alerts_enabled, pvr_json),
    )
    responses_iter = iter(api_responses)
    monkeypatch.setattr(repo_admin, "api_json", lambda *a, **k: {})
    monkeypatch.setattr(repo_admin, "api_request", lambda *a, **k: next(responses_iter))
    return repo_admin.make_security_features_worker(owner="hugoh", dry_run=dry_run)


def test_security_worker_dry_run_unchanged_when_fully_enabled(monkeypatch):
    worker = _security_worker(
        monkeypatch,
        repo_json=FULLY_ENABLED_REPO_JSON,
        vuln_alerts_enabled=True,
        pvr_json={"enabled": True},
        dry_run=True,
    )
    assert worker(REPO).status == Status.UNCHANGED


def test_security_worker_dry_run_ok_when_would_enable(monkeypatch):
    worker = _security_worker(
        monkeypatch,
        repo_json=FULLY_ENABLED_REPO_JSON,
        vuln_alerts_enabled=False,
        pvr_json={"enabled": True},
        dry_run=True,
    )
    assert worker(REPO).status == Status.OK


def test_security_worker_dry_run_limited_unchanged_when_unavailable_and_nothing_pending(
    monkeypatch,
):
    worker = _security_worker(
        monkeypatch,
        repo_json=UNAVAILABLE_REPO_JSON,
        vuln_alerts_enabled=True,
        pvr_json=None,
        dry_run=True,
    )
    assert worker(REPO).status == Status.LIMITED_UNCHANGED


def test_security_worker_dry_run_limited_when_unavailable_and_would_enable(
    monkeypatch,
):
    worker = _security_worker(
        monkeypatch,
        repo_json=UNAVAILABLE_REPO_JSON,
        vuln_alerts_enabled=False,
        pvr_json=None,
        dry_run=True,
    )
    assert worker(REPO).status == Status.LIMITED


def test_security_worker_apply_unchanged_when_already_fully_enabled(monkeypatch):
    worker = _security_worker(
        monkeypatch,
        repo_json=FULLY_ENABLED_REPO_JSON,
        vuln_alerts_enabled=True,
        pvr_json={"enabled": True},
        dry_run=False,
        api_responses=[_FakeResponse(200), _FakeResponse(200)],
    )
    assert worker(REPO).status == Status.UNCHANGED


def test_security_worker_apply_ok_when_newly_enabled(monkeypatch):
    worker = _security_worker(
        monkeypatch,
        repo_json=FULLY_ENABLED_REPO_JSON,
        vuln_alerts_enabled=False,
        pvr_json={"enabled": True},
        dry_run=False,
        api_responses=[_FakeResponse(200), _FakeResponse(200)],
    )
    assert worker(REPO).status == Status.OK


def test_security_worker_apply_limited_unchanged_when_unavailable_and_nothing_pending(
    monkeypatch,
):
    worker = _security_worker(
        monkeypatch,
        repo_json=UNAVAILABLE_REPO_JSON,
        vuln_alerts_enabled=True,
        pvr_json=None,
        dry_run=False,
        api_responses=[_FakeResponse(422), _FakeResponse(404)],
    )
    assert worker(REPO).status == Status.LIMITED_UNCHANGED


def test_security_worker_apply_limited_when_unavailable_and_was_pending(monkeypatch):
    worker = _security_worker(
        monkeypatch,
        repo_json=UNAVAILABLE_REPO_JSON,
        vuln_alerts_enabled=False,
        pvr_json=None,
        dry_run=False,
        api_responses=[_FakeResponse(422), _FakeResponse(404)],
    )
    assert worker(REPO).status == Status.LIMITED


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


def test_branch_protection_worker_limited_unchanged_when_no_prs(monkeypatch):
    monkeypatch.setattr(repo_admin, "_latest_pr_head_sha", lambda owner, name: None)
    worker = repo_admin.make_branch_protection_worker(owner="hugoh", dry_run=True)
    assert worker(REPO).status == Status.LIMITED_UNCHANGED


def test_branch_protection_worker_limited_unchanged_when_no_check_runs(monkeypatch):
    monkeypatch.setattr(repo_admin, "_latest_pr_head_sha", lambda owner, name: "sha")
    monkeypatch.setattr(repo_admin, "_check_run_contexts", lambda owner, name, sha: [])
    worker = repo_admin.make_branch_protection_worker(owner="hugoh", dry_run=True)
    assert worker(REPO).status == Status.LIMITED_UNCHANGED


def test_branch_protection_worker_dry_run_limited_unchanged_when_plan_gated(
    monkeypatch,
):
    monkeypatch.setattr(repo_admin, "_latest_pr_head_sha", lambda owner, name: "sha")
    monkeypatch.setattr(
        repo_admin, "_check_run_contexts", lambda owner, name, sha: ["build"]
    )
    monkeypatch.setattr(repo_admin, "api_request", lambda *a, **k: _FakeResponse(403))
    worker = repo_admin.make_branch_protection_worker(owner="hugoh", dry_run=True)
    assert worker(REPO).status == Status.LIMITED_UNCHANGED


def test_branch_protection_worker_apply_limited_unchanged_when_plan_gated(
    monkeypatch,
):
    monkeypatch.setattr(repo_admin, "_latest_pr_head_sha", lambda owner, name: "sha")
    monkeypatch.setattr(
        repo_admin, "_check_run_contexts", lambda owner, name, sha: ["build"]
    )
    monkeypatch.setattr(repo_admin, "api_request", lambda *a, **k: _FakeResponse(403))
    worker = repo_admin.make_branch_protection_worker(owner="hugoh", dry_run=False)
    assert worker(REPO).status == Status.LIMITED_UNCHANGED
