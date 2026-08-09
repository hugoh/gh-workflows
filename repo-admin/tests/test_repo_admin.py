import argparse

import repo_admin
from lib import GhError, Repo, Status

REPO = Repo(name="repo", default_branch="main", is_private=False, is_fork=False)


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


async def test_cmd_list_prints_a_row_per_repo(monkeypatch, capsys):
    async def fake_list_repos(owner, *, only=None, skip=None):
        return [
            Repo(name="repo-a", default_branch="main", is_private=False, is_fork=False),
            Repo(name="repo-b", default_branch="master", is_private=True, is_fork=True),
        ]

    monkeypatch.setattr(repo_admin, "list_repos", fake_list_repos)
    args = argparse.Namespace(only=None, skip=None)
    assert await repo_admin.cmd_list(args) == 0
    out = capsys.readouterr().out
    assert "repo-a" in out
    assert "repo-b" in out
    assert "NAME" in out
    assert "DEFAULT BRANCH" in out


async def test_cmd_list_passes_through_only_and_skip_filters(monkeypatch):
    seen = {}

    async def fake_list_repos(owner, *, only=None, skip=None):
        seen["only"] = only
        seen["skip"] = skip
        return []

    monkeypatch.setattr(repo_admin, "list_repos", fake_list_repos)
    args = argparse.Namespace(only="repo-a", skip="repo-b")
    await repo_admin.cmd_list(args)
    assert seen == {"only": {"repo-a"}, "skip": {"repo-b"}}


# ---------------------------------------------------------------------------
# merge-settings
# ---------------------------------------------------------------------------


def test_merge_settings_dry_run_line_up_to_date():
    current = {
        "allow_auto_merge": True,
        "delete_branch_on_merge": True,
        "allow_update_branch": True,
    }
    assert repo_admin.merge_settings_dry_run_line(
        "repo", current, Status.UNCHANGED
    ) == (f"{'repo':<30} unchanged: {current}")


def test_merge_settings_dry_run_line_lists_disabled_in_field_order():
    current = {
        "allow_auto_merge": False,
        "delete_branch_on_merge": True,
        "allow_update_branch": False,
    }
    line = repo_admin.merge_settings_dry_run_line("repo", current, Status.OK)
    assert line == f"{'repo':<30} would enable: allow_auto_merge, allow_update_branch"


def test_merge_settings_apply_line_unchanged():
    settings = {
        "allow_auto_merge": True,
        "delete_branch_on_merge": True,
        "allow_update_branch": True,
    }
    assert repo_admin.merge_settings_apply_line(
        "repo", settings, settings, Status.UNCHANGED
    ) == (f"{'repo':<30} unchanged: {settings}")


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
    line = repo_admin.merge_settings_apply_line("repo", before, after, Status.OK)
    assert line == f"{'repo':<30} {before} -> {after}"


async def test_merge_settings_worker_dry_run(monkeypatch):
    async def fake_merge_settings(owner, name):
        return {
            "allow_auto_merge": False,
            "delete_branch_on_merge": True,
            "allow_update_branch": True,
        }

    monkeypatch.setattr(repo_admin, "_merge_settings", fake_merge_settings)
    worker = repo_admin.make_merge_settings_worker(owner="hugoh", dry_run=True)
    result = await worker(REPO)
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


async def test_merge_settings_worker_dry_run_unchanged_when_at_target(monkeypatch):
    async def fake_merge_settings(owner, name):
        return {
            "allow_auto_merge": True,
            "delete_branch_on_merge": True,
            "allow_update_branch": True,
        }

    monkeypatch.setattr(repo_admin, "_merge_settings", fake_merge_settings)
    worker = repo_admin.make_merge_settings_worker(owner="hugoh", dry_run=True)
    assert (await worker(REPO)).status == Status.UNCHANGED


async def test_merge_settings_worker_dry_run_ok_when_would_reach_target(monkeypatch):
    async def fake_merge_settings(owner, name):
        return {
            "allow_auto_merge": False,
            "delete_branch_on_merge": True,
            "allow_update_branch": True,
        }

    monkeypatch.setattr(repo_admin, "_merge_settings", fake_merge_settings)
    worker = repo_admin.make_merge_settings_worker(owner="hugoh", dry_run=True)
    assert (await worker(REPO)).status == Status.OK


def _merge_settings_apply_worker(monkeypatch, before, after):
    calls = iter([before, after])

    async def fake_merge_settings(owner, name):
        return next(calls)

    async def fake_api_json(*a, **k):
        return {}

    monkeypatch.setattr(repo_admin, "_merge_settings", fake_merge_settings)
    monkeypatch.setattr(repo_admin, "api_json", fake_api_json)
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


async def test_merge_settings_worker_apply_ok_when_reaches_target(monkeypatch):
    worker = _merge_settings_apply_worker(monkeypatch, NOT_AT_TARGET, AT_TARGET)
    assert (await worker(REPO)).status == Status.OK


async def test_merge_settings_worker_apply_unchanged_when_already_at_target(
    monkeypatch,
):
    worker = _merge_settings_apply_worker(monkeypatch, AT_TARGET, AT_TARGET)
    assert (await worker(REPO)).status == Status.UNCHANGED


async def test_merge_settings_worker_apply_limited_when_still_not_at_target(
    monkeypatch,
):
    worker = _merge_settings_apply_worker(monkeypatch, NOT_AT_TARGET, NOT_AT_TARGET)
    assert (await worker(REPO)).status == Status.LIMITED_UNCHANGED


async def test_merge_settings_worker_apply_limited_when_partially_fixed(monkeypatch):
    worker = _merge_settings_apply_worker(
        monkeypatch, NOT_AT_TARGET, PARTIALLY_FIXED_NOT_AT_TARGET
    )
    assert (await worker(REPO)).status == Status.LIMITED


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
        "repo", {"would_enable": [], "unavailable": []}, Status.UNCHANGED
    )
    assert line == f"{'repo':<30} unchanged: enabled"


def test_security_dry_run_line_would_enable_and_unavailable():
    line = repo_admin.security_dry_run_line(
        "repo",
        {"would_enable": ["vuln_alerts"], "unavailable": ["push_protection"]},
        Status.LIMITED,
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
        self.is_success = status_code < 400

    def json(self):
        return {}


def _security_worker(
    monkeypatch, *, repo_json, vuln_alerts_enabled, pvr_json, dry_run, api_responses=()
):
    async def fake_fetch_security_state(owner, name):
        return repo_json, vuln_alerts_enabled, pvr_json

    responses_iter = iter(api_responses)

    async def fake_api_json(*a, **k):
        return {}

    async def fake_api_request(*a, **k):
        return next(responses_iter)

    monkeypatch.setattr(repo_admin, "_fetch_security_state", fake_fetch_security_state)
    monkeypatch.setattr(repo_admin, "api_json", fake_api_json)
    monkeypatch.setattr(repo_admin, "api_request", fake_api_request)
    return repo_admin.make_security_features_worker(owner="hugoh", dry_run=dry_run)


async def test_security_worker_dry_run_unchanged_when_fully_enabled(monkeypatch):
    worker = _security_worker(
        monkeypatch,
        repo_json=FULLY_ENABLED_REPO_JSON,
        vuln_alerts_enabled=True,
        pvr_json={"enabled": True},
        dry_run=True,
    )
    assert (await worker(REPO)).status == Status.UNCHANGED


async def test_security_worker_dry_run_ok_when_would_enable(monkeypatch):
    worker = _security_worker(
        monkeypatch,
        repo_json=FULLY_ENABLED_REPO_JSON,
        vuln_alerts_enabled=False,
        pvr_json={"enabled": True},
        dry_run=True,
    )
    assert (await worker(REPO)).status == Status.OK


async def test_security_worker_dry_run_limited_unchanged_when_unavailable_and_nothing_pending(
    monkeypatch,
):
    worker = _security_worker(
        monkeypatch,
        repo_json=UNAVAILABLE_REPO_JSON,
        vuln_alerts_enabled=True,
        pvr_json=None,
        dry_run=True,
    )
    assert (await worker(REPO)).status == Status.LIMITED_UNCHANGED


async def test_security_worker_dry_run_limited_when_unavailable_and_would_enable(
    monkeypatch,
):
    worker = _security_worker(
        monkeypatch,
        repo_json=UNAVAILABLE_REPO_JSON,
        vuln_alerts_enabled=False,
        pvr_json=None,
        dry_run=True,
    )
    assert (await worker(REPO)).status == Status.LIMITED


async def test_security_worker_apply_unchanged_when_already_fully_enabled(monkeypatch):
    worker = _security_worker(
        monkeypatch,
        repo_json=FULLY_ENABLED_REPO_JSON,
        vuln_alerts_enabled=True,
        pvr_json={"enabled": True},
        dry_run=False,
        api_responses=[_FakeResponse(200), _FakeResponse(200)],
    )
    result = await worker(REPO)
    assert result.status == Status.UNCHANGED
    assert result.line == f"{'repo':<30} unchanged: enabled"


async def test_security_worker_apply_ok_when_newly_enabled(monkeypatch):
    worker = _security_worker(
        monkeypatch,
        repo_json=FULLY_ENABLED_REPO_JSON,
        vuln_alerts_enabled=False,
        pvr_json={"enabled": True},
        dry_run=False,
        api_responses=[_FakeResponse(200), _FakeResponse(200)],
    )
    assert (await worker(REPO)).status == Status.OK


async def test_security_worker_apply_limited_unchanged_when_unavailable_and_nothing_pending(
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
    assert (await worker(REPO)).status == Status.LIMITED_UNCHANGED


async def test_security_worker_apply_limited_when_unavailable_and_was_pending(
    monkeypatch,
):
    worker = _security_worker(
        monkeypatch,
        repo_json=UNAVAILABLE_REPO_JSON,
        vuln_alerts_enabled=False,
        pvr_json=None,
        dry_run=False,
        api_responses=[_FakeResponse(422), _FakeResponse(404)],
    )
    assert (await worker(REPO)).status == Status.LIMITED


# ---------------------------------------------------------------------------
# branch-protection
# ---------------------------------------------------------------------------


def _check_runs_response(runs):
    return {"check_runs": runs}


def _run(name, conclusion, app_slug="github-actions"):
    return {"name": name, "conclusion": conclusion, "app": {"slug": app_slug}}


async def test_check_run_contexts_excludes_third_party_apps(monkeypatch):
    async def fake_api_json(*a, **k):
        return _check_runs_response(
            [
                _run("goci / goci", "success"),
                _run("DeepSource: Analysis", "success", app_slug="deepsource-io"),
            ]
        )

    monkeypatch.setattr(repo_admin, "api_json", fake_api_json)
    assert await repo_admin._check_run_contexts("hugoh", "repo", ["sha"]) == [
        "goci / goci"
    ]


async def test_check_run_contexts_replaces_skip_artifact_with_composite_alias(
    monkeypatch,
):
    responses = {
        "sha1": _check_runs_response(
            [_run("hk / hk", "failure"), _run("release", "skipped")]
        ),
        "sha2": _check_runs_response(
            [_run("hk / hk", "success"), _run("release / release", "success")]
        ),
    }

    async def fake_api_json(method, path, **kwargs):
        sha = path.rsplit("/", 2)[1]
        return responses[sha]

    monkeypatch.setattr(repo_admin, "api_json", fake_api_json)
    contexts = await repo_admin._check_run_contexts("hugoh", "repo", ["sha1", "sha2"])
    assert contexts == ["hk / hk", "release / release"]


async def test_check_run_contexts_keeps_legitimately_skipped_check(monkeypatch):
    responses = {
        "sha1": _check_runs_response(
            [_run("hk / hk", "success"), _run("pages", "skipped")]
        ),
    }

    async def fake_api_json(method, path, **k):
        return responses["sha1"]

    monkeypatch.setattr(repo_admin, "api_json", fake_api_json)
    contexts = await repo_admin._check_run_contexts("hugoh", "repo", ["sha1"])
    assert contexts == ["hk / hk", "pages"]


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


def test_branch_protection_payload_no_contexts_omits_required_status_checks():
    payload = repo_admin.branch_protection_payload([])
    assert payload["required_status_checks"] is None
    assert payload["enforce_admins"] is True
    assert payload["allow_force_pushes"] is False
    assert payload["allow_deletions"] is False


def test_branch_protection_up_to_date_true_with_no_contexts_and_none_required():
    current = {
        "required_status_checks": None,
        "enforce_admins": {"enabled": True},
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
        "required_pull_request_reviews": {"required_approving_review_count": 0},
    }
    assert repo_admin.branch_protection_up_to_date(current, []) is True


async def test_branch_protection_worker_dry_run_ok_when_no_prs(monkeypatch):
    async def fake_shas(owner, name):
        return []

    async def fake_api_request(*a, **k):
        return _FakeResponse(404)

    monkeypatch.setattr(repo_admin, "_recent_pr_head_shas", fake_shas)
    monkeypatch.setattr(repo_admin, "api_request", fake_api_request)
    worker = repo_admin.make_branch_protection_worker(owner="hugoh", dry_run=True)
    result = await worker(REPO)
    assert result.status == Status.OK
    assert "no pull requests found yet" in result.line
    assert "(none yet)" in result.line


async def test_branch_protection_worker_dry_run_ok_when_no_check_runs(monkeypatch):
    async def fake_shas(owner, name):
        return ["sha"]

    async def fake_contexts(owner, name, shas):
        return []

    async def fake_api_request(*a, **k):
        return _FakeResponse(404)

    monkeypatch.setattr(repo_admin, "_recent_pr_head_shas", fake_shas)
    monkeypatch.setattr(repo_admin, "_check_run_contexts", fake_contexts)
    monkeypatch.setattr(repo_admin, "api_request", fake_api_request)
    worker = repo_admin.make_branch_protection_worker(owner="hugoh", dry_run=True)
    result = await worker(REPO)
    assert result.status == Status.OK
    assert "no check runs found" in result.line
    assert "(none yet)" in result.line


async def test_branch_protection_worker_apply_ok_when_no_prs_yet(monkeypatch):
    async def fake_shas(owner, name):
        return []

    calls = []

    async def fake_api_request(method, *a, **k):
        calls.append(method)
        return _FakeResponse(404) if method == "GET" else _FakeResponse(200)

    monkeypatch.setattr(repo_admin, "_recent_pr_head_shas", fake_shas)
    monkeypatch.setattr(repo_admin, "api_request", fake_api_request)
    worker = repo_admin.make_branch_protection_worker(owner="hugoh", dry_run=False)
    result = await worker(REPO)
    assert result.status == Status.OK
    assert result.tag == repo_admin.Tag.APPLIED_NO_CHECKS
    assert "protected" in result.line
    assert "PUT" in calls


async def test_branch_protection_worker_dry_run_limited_unchanged_when_plan_gated(
    monkeypatch,
):
    async def fake_shas(owner, name):
        return ["sha"]

    async def fake_contexts(owner, name, shas):
        return ["build"]

    async def fake_api_request(*a, **k):
        return _FakeResponse(403)

    monkeypatch.setattr(repo_admin, "_recent_pr_head_shas", fake_shas)
    monkeypatch.setattr(repo_admin, "_check_run_contexts", fake_contexts)
    monkeypatch.setattr(repo_admin, "api_request", fake_api_request)
    worker = repo_admin.make_branch_protection_worker(owner="hugoh", dry_run=True)
    result = await worker(REPO)
    assert result.status == Status.LIMITED_UNCHANGED
    assert result.line == (
        f"{'repo':<30} unchanged: private repo, plan does not allow branch protection"
    )


async def test_branch_protection_worker_apply_limited_unchanged_when_plan_gated(
    monkeypatch,
):
    async def fake_shas(owner, name):
        return ["sha"]

    async def fake_contexts(owner, name, shas):
        return ["build"]

    async def fake_api_request(*a, **k):
        return _FakeResponse(403)

    monkeypatch.setattr(repo_admin, "_recent_pr_head_shas", fake_shas)
    monkeypatch.setattr(repo_admin, "_check_run_contexts", fake_contexts)
    monkeypatch.setattr(repo_admin, "api_request", fake_api_request)
    worker = repo_admin.make_branch_protection_worker(owner="hugoh", dry_run=False)
    result = await worker(REPO)
    assert result.status == Status.LIMITED_UNCHANGED
    assert result.line == (
        f"{'repo':<30} unchanged: private repo, plan does not allow branch protection"
    )


async def test_branch_protection_worker_dry_run_unchanged_line(monkeypatch):
    async def fake_shas(owner, name):
        return ["sha"]

    async def fake_contexts(owner, name, shas):
        return ["build", "test"]

    current = {
        "required_status_checks": {"contexts": ["build", "test"], "strict": True},
        "enforce_admins": {"enabled": True},
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
        "required_pull_request_reviews": {"required_approving_review_count": 0},
    }

    class _ProtectionResponse(_FakeResponse):
        def json(self):
            return current

    async def fake_api_request(*a, **k):
        return _ProtectionResponse(200)

    monkeypatch.setattr(repo_admin, "_recent_pr_head_shas", fake_shas)
    monkeypatch.setattr(repo_admin, "_check_run_contexts", fake_contexts)
    monkeypatch.setattr(repo_admin, "api_request", fake_api_request)
    worker = repo_admin.make_branch_protection_worker(owner="hugoh", dry_run=True)
    result = await worker(REPO)
    assert result.status == Status.UNCHANGED
    assert result.line == f"{'repo':<30} unchanged: build, test"


async def test_branch_protection_worker_apply_unchanged_when_already_protected(
    monkeypatch,
):
    async def fake_shas(owner, name):
        return ["sha"]

    async def fake_contexts(owner, name, shas):
        return ["build", "test"]

    current = {
        "required_status_checks": {"contexts": ["build", "test"], "strict": True},
        "enforce_admins": {"enabled": True},
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
        "required_pull_request_reviews": {"required_approving_review_count": 0},
    }

    class _ProtectionResponse(_FakeResponse):
        def json(self):
            return current

    calls = []

    async def fake_api_request(method, *a, **k):
        calls.append(method)
        return _ProtectionResponse(200)

    monkeypatch.setattr(repo_admin, "_recent_pr_head_shas", fake_shas)
    monkeypatch.setattr(repo_admin, "_check_run_contexts", fake_contexts)
    monkeypatch.setattr(repo_admin, "api_request", fake_api_request)
    worker = repo_admin.make_branch_protection_worker(owner="hugoh", dry_run=False)
    result = await worker(REPO)
    assert result.status == Status.UNCHANGED
    assert result.line == f"{'repo':<30} unchanged: build, test"
    assert result.tag == repo_admin.Tag.APPLIED
    assert "PUT" not in calls


async def test_branch_protection_worker_apply_ok_when_updating_from_stale_state(
    monkeypatch,
):
    async def fake_shas(owner, name):
        return ["sha"]

    async def fake_contexts(owner, name, shas):
        return ["build", "test"]

    current = {
        "required_status_checks": {"contexts": ["build"], "strict": True},
        "enforce_admins": {"enabled": True},
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
        "required_pull_request_reviews": {"required_approving_review_count": 0},
    }

    class _ProtectionResponse(_FakeResponse):
        def json(self):
            return current

    async def fake_api_request(*a, **k):
        return _ProtectionResponse(200)

    monkeypatch.setattr(repo_admin, "_recent_pr_head_shas", fake_shas)
    monkeypatch.setattr(repo_admin, "_check_run_contexts", fake_contexts)
    monkeypatch.setattr(repo_admin, "api_request", fake_api_request)
    worker = repo_admin.make_branch_protection_worker(owner="hugoh", dry_run=False)
    result = await worker(REPO)
    assert result.status == Status.OK
    assert result.line == f"{'repo':<30} protected (build, test)"
    assert result.tag == repo_admin.Tag.APPLIED


# ---------------------------------------------------------------------------
# all
# ---------------------------------------------------------------------------


async def test_cmd_all_runs_merge_settings_branch_protection_security_in_order(
    monkeypatch,
):
    calls = []

    async def fake_merge_settings(args):
        calls.append("merge-settings")
        return 0

    async def fake_branch_protection(args):
        calls.append("branch-protection")
        return 0

    async def fake_security_features(args):
        calls.append("security-features")
        return 0

    monkeypatch.setattr(repo_admin, "cmd_merge_settings", fake_merge_settings)
    monkeypatch.setattr(repo_admin, "cmd_branch_protection", fake_branch_protection)
    monkeypatch.setattr(repo_admin, "cmd_security_features", fake_security_features)
    args = argparse.Namespace(dry_run=True, only=None, skip=None)
    assert await repo_admin.cmd_all(args) == 0
    assert calls == ["merge-settings", "branch-protection", "security-features"]


async def test_cmd_all_continues_after_a_command_fails_and_returns_nonzero(
    monkeypatch,
):
    calls = []

    async def failing(args):
        calls.append("merge-settings")
        raise GhError("boom")

    async def fake_branch_protection(args):
        calls.append("branch-protection")
        return 0

    async def fake_security_features(args):
        calls.append("security-features")
        return 0

    monkeypatch.setattr(repo_admin, "cmd_merge_settings", failing)
    monkeypatch.setattr(repo_admin, "cmd_branch_protection", fake_branch_protection)
    monkeypatch.setattr(repo_admin, "cmd_security_features", fake_security_features)
    args = argparse.Namespace(dry_run=True, only=None, skip=None)
    assert await repo_admin.cmd_all(args) == 1
    assert calls == ["merge-settings", "branch-protection", "security-features"]


async def test_cmd_all_returns_nonzero_when_a_command_returns_nonzero(monkeypatch):
    async def fake_one(args):
        return 1

    async def fake_zero(args):
        return 0

    monkeypatch.setattr(repo_admin, "cmd_merge_settings", fake_one)
    monkeypatch.setattr(repo_admin, "cmd_branch_protection", fake_zero)
    monkeypatch.setattr(repo_admin, "cmd_security_features", fake_zero)
    args = argparse.Namespace(dry_run=True, only=None, skip=None)
    assert await repo_admin.cmd_all(args) == 1


def test_all_subcommand_is_registered_in_parser():
    args = repo_admin.build_parser().parse_args(["all", "--dry-run"])
    assert args.func == repo_admin.cmd_all
    assert args.dry_run is True
