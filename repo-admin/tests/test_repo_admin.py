import argparse

import repo_admin
from lib import GhError, Repo, Status

REPO = Repo(name="repo", default_branch="main", is_private=False, is_fork=False)
PRIVATE_REPO = Repo(name="repo", default_branch="main", is_private=True, is_fork=False)


class _MissingPath:
    name = "secrets.enc.yaml"

    def exists(self):
        return False


class _PresentPath:
    def exists(self):
        return True


def _capturing_list_repos():
    seen = {}

    async def fake_list_repos(owner, *, only=None, skip=None):
        seen["only"] = only
        seen["skip"] = skip
        return []

    return seen, fake_list_repos


# ---------------------------------------------------------------------------
# repos list
# ---------------------------------------------------------------------------


async def test_cmd_repos_list_prints_a_row_per_repo(monkeypatch, capsys):
    async def fake_list_repos(owner, *, only=None, skip=None):
        return [
            Repo(name="repo-a", default_branch="main", is_private=False, is_fork=False),
            Repo(name="repo-b", default_branch="master", is_private=True, is_fork=True),
        ]

    monkeypatch.setattr(repo_admin, "list_repos", fake_list_repos)
    args = argparse.Namespace(repos=[], skip=None)
    assert await repo_admin.cmd_repos_list(args) == 0
    out = capsys.readouterr().out
    assert "repo-a" in out
    assert "repo-b" in out
    assert "NAME" in out
    assert "DEFAULT BRANCH" in out


async def test_cmd_repos_list_passes_through_repos_and_skip_filters(monkeypatch):
    seen, fake_list_repos = _capturing_list_repos()
    monkeypatch.setattr(repo_admin, "list_repos", fake_list_repos)
    args = argparse.Namespace(repos=["repo-a"], skip="repo-b")
    await repo_admin.cmd_repos_list(args)
    assert seen == {"only": {"repo-a"}, "skip": {"repo-b"}}


# ---------------------------------------------------------------------------
# merge sync
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


def _merge_settings_dry_run_worker(monkeypatch, settings):
    async def fake_merge_settings(owner, name):
        return settings

    monkeypatch.setattr(repo_admin, "_merge_settings", fake_merge_settings)
    return repo_admin.make_merge_settings_worker(owner="hugoh", dry_run=True)


async def test_merge_settings_worker_dry_run(monkeypatch):
    worker = _merge_settings_dry_run_worker(
        monkeypatch,
        {
            "allow_auto_merge": False,
            "delete_branch_on_merge": True,
            "allow_update_branch": True,
        },
    )
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
    worker = _merge_settings_dry_run_worker(
        monkeypatch,
        {
            "allow_auto_merge": True,
            "delete_branch_on_merge": True,
            "allow_update_branch": True,
        },
    )
    assert (await worker(REPO)).status == Status.UNCHANGED


async def test_merge_settings_worker_dry_run_ok_when_would_reach_target(monkeypatch):
    worker = _merge_settings_dry_run_worker(
        monkeypatch,
        {
            "allow_auto_merge": False,
            "delete_branch_on_merge": True,
            "allow_update_branch": True,
        },
    )
    assert (await worker(REPO)).status == Status.OK


def test_merge_settings_dry_run_line_marks_gated_field_unavailable():
    current = {
        "allow_auto_merge": False,
        "delete_branch_on_merge": True,
        "allow_update_branch": True,
    }
    line = repo_admin.merge_settings_dry_run_line(
        "repo", current, Status.LIMITED_UNCHANGED, is_private=True
    )
    assert "would enable" not in line
    assert "(unavailable: allow_auto_merge)" in line


def test_merge_settings_dry_run_line_would_enable_and_unavailable_together():
    current = {
        "allow_auto_merge": False,
        "delete_branch_on_merge": False,
        "allow_update_branch": True,
    }
    line = repo_admin.merge_settings_dry_run_line(
        "repo", current, Status.LIMITED, is_private=True
    )
    assert "would enable: delete_branch_on_merge" in line
    assert "(unavailable: allow_auto_merge)" in line


async def test_merge_settings_worker_dry_run_limited_unchanged_on_private_repo_gate(
    monkeypatch,
):
    worker = _merge_settings_dry_run_worker(
        monkeypatch,
        {
            "allow_auto_merge": False,
            "delete_branch_on_merge": True,
            "allow_update_branch": True,
        },
    )
    result = await worker(PRIVATE_REPO)
    assert result.status == Status.LIMITED_UNCHANGED
    assert "would enable" not in result.line
    assert "unavailable: allow_auto_merge" in result.line


async def test_merge_settings_worker_dry_run_limited_when_private_repo_has_fixable_field(
    monkeypatch,
):
    worker = _merge_settings_dry_run_worker(
        monkeypatch,
        {
            "allow_auto_merge": False,
            "delete_branch_on_merge": False,
            "allow_update_branch": True,
        },
    )
    result = await worker(PRIVATE_REPO)
    assert result.status == Status.LIMITED
    assert "would enable: delete_branch_on_merge" in result.line
    assert "unavailable: allow_auto_merge" in result.line


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
# security sync
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
        repo_json,
        vuln_alerts_enabled=True,
        pvr_json={"enabled": True},
        codeql_json={"state": "configured"},
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
        repo_json,
        vuln_alerts_enabled=False,
        pvr_json={"enabled": True},
        codeql_json={"state": "not-configured"},
    )
    assert set(summary["would_enable"]) == {
        "vuln_alerts",
        "secret_scanning",
        "code_scanning",
    }
    assert summary["unavailable"] == []


def test_security_summarize_reports_unavailable_for_private_repo():
    # Private repos without GitHub Advanced Security: these keys are absent
    # from security_and_analysis, and private-vulnerability-reporting and
    # code-scanning default setup both 404.
    repo_json = {"security_and_analysis": {}}
    summary = repo_admin.security_summarize(
        repo_json, vuln_alerts_enabled=True, pvr_json=None, codeql_json=None
    )
    assert set(summary["unavailable"]) == {
        "secret_scanning",
        "push_protection",
        "dependabot_updates",
        "private_vuln_reporting",
        "code_scanning",
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
CONFIGURED_CODEQL_JSON = {"state": "configured"}


class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code
        self.is_success = status_code < 400

    def json(self):
        return {}


class _FakeResponseWithJson(_FakeResponse):
    def __init__(self, status_code, body):
        super().__init__(status_code)
        self._body = body

    def json(self):
        return self._body


def _security_worker(
    monkeypatch,
    *,
    repo_json,
    vuln_alerts_enabled,
    pvr_json,
    dry_run,
    codeql_json=None,
    api_responses=(),
):
    async def fake_fetch_security_state(owner, name):
        return repo_json, vuln_alerts_enabled, pvr_json, codeql_json

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
        codeql_json=CONFIGURED_CODEQL_JSON,
        dry_run=True,
    )
    assert (await worker(REPO)).status == Status.UNCHANGED


async def test_security_worker_dry_run_ok_when_would_enable(monkeypatch):
    worker = _security_worker(
        monkeypatch,
        repo_json=FULLY_ENABLED_REPO_JSON,
        vuln_alerts_enabled=False,
        pvr_json={"enabled": True},
        codeql_json=CONFIGURED_CODEQL_JSON,
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
        codeql_json=CONFIGURED_CODEQL_JSON,
        dry_run=False,
        api_responses=[_FakeResponse(200), _FakeResponse(200), _FakeResponse(200)],
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
        codeql_json=CONFIGURED_CODEQL_JSON,
        dry_run=False,
        api_responses=[_FakeResponse(200), _FakeResponse(200), _FakeResponse(200)],
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
        codeql_json=None,
        dry_run=False,
        api_responses=[_FakeResponse(422), _FakeResponse(404), _FakeResponse(404)],
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
        codeql_json=None,
        dry_run=False,
        api_responses=[_FakeResponse(422), _FakeResponse(404), _FakeResponse(404)],
    )
    assert (await worker(REPO)).status == Status.LIMITED


async def test_security_worker_apply_reports_code_scanning_unavailable_for_no_supported_language(
    monkeypatch,
):
    worker = _security_worker(
        monkeypatch,
        repo_json=FULLY_ENABLED_REPO_JSON,
        vuln_alerts_enabled=True,
        pvr_json={"enabled": True},
        codeql_json=None,
        dry_run=False,
        api_responses=[_FakeResponse(200), _FakeResponse(200), _FakeResponse(422)],
    )
    result = await worker(REPO)
    assert result.status == Status.LIMITED_UNCHANGED
    assert "code scanning" in result.line


# ---------------------------------------------------------------------------
# protection sync
# ---------------------------------------------------------------------------


def _check_runs_response(runs):
    return {"check_runs": runs}


def _run(name, conclusion, app_slug="github-actions", suite=1):
    return {
        "name": name,
        "conclusion": conclusion,
        "app": {"slug": app_slug},
        "check_suite": {"id": suite},
    }


def _patch_own_suites(monkeypatch, suite_ids):
    async def fake(owner, name, sha):
        return set(suite_ids)

    monkeypatch.setattr(repo_admin, "_own_workflow_check_suite_ids", fake)


async def test_check_run_contexts_excludes_third_party_apps(monkeypatch):
    async def fake_api_json(*a, **k):
        return _check_runs_response(
            [
                _run("goci / goci", "success"),
                _run("DeepSource: Analysis", "success", app_slug="deepsource-io"),
            ]
        )

    monkeypatch.setattr(repo_admin, "api_json", fake_api_json)
    _patch_own_suites(monkeypatch, [1])
    assert await repo_admin._check_run_contexts("hugoh", "repo", ["sha"]) == [
        "goci / goci"
    ]


async def test_check_run_contexts_excludes_github_managed_setup_checks(monkeypatch):
    async def fake_api_json(method, path, **kwargs):
        if path.endswith("/check-runs"):
            return _check_runs_response(
                [
                    _run("lint", "success", suite=10),
                    _run("Analyze (python)", "success", suite=20),
                    _run("Analyze (actions)", "success", suite=20),
                    _run("github-advanced-security", "failure", suite=30),
                ]
            )
        if path.endswith("/actions/runs"):
            return {
                "workflow_runs": [
                    {"check_suite_id": 10, "path": ".github/workflows/hk.yml"},
                    {
                        "check_suite_id": 20,
                        "path": "dynamic/github-code-scanning/codeql",
                    },
                    {
                        "check_suite_id": 30,
                        "path": "dynamic/agents/github-advanced-security",
                    },
                ]
            }
        raise AssertionError(path)

    monkeypatch.setattr(repo_admin, "api_json", fake_api_json)
    assert await repo_admin._check_run_contexts("hugoh", "repo", ["sha"]) == ["lint"]


async def test_own_workflow_check_suite_ids_keeps_only_repo_workflow_paths(monkeypatch):
    async def fake_api_json(method, path, **kwargs):
        return {
            "workflow_runs": [
                {"check_suite_id": 10, "path": ".github/workflows/hk.yml"},
                {"check_suite_id": 20, "path": "dynamic/github-code-scanning/codeql"},
            ]
        }

    monkeypatch.setattr(repo_admin, "api_json", fake_api_json)
    assert await repo_admin._own_workflow_check_suite_ids("hugoh", "repo", "sha") == {
        10
    }


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
    _patch_own_suites(monkeypatch, [1])
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
    _patch_own_suites(monkeypatch, [1])
    contexts = await repo_admin._check_run_contexts("hugoh", "repo", ["sha1"])
    assert contexts == ["hk / hk", "pages"]


def _protection_state(*, contexts=("build", "test"), **overrides):
    state = {
        "required_status_checks": {"contexts": list(contexts), "strict": True},
        "enforce_admins": {"enabled": True},
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
        "required_pull_request_reviews": {"required_approving_review_count": 0},
    }
    state.update(overrides)
    return state


def test_branch_protection_up_to_date_matches_baseline():
    current = _protection_state(contexts=["build", "test"])
    assert repo_admin.branch_protection_up_to_date(current, ["test", "build"]) is True


def test_branch_protection_up_to_date_false_when_contexts_differ():
    current = _protection_state(contexts=["build"])
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
    current = _protection_state(required_status_checks=None)
    assert repo_admin.branch_protection_up_to_date(current, []) is True


def _branch_protection_worker(
    monkeypatch,
    *,
    dry_run,
    shas=(),
    contexts=(),
    current=None,
    status_code=200,
    track_calls=False,
):
    async def fake_shas(owner, name):
        return list(shas)

    async def fake_contexts(owner, name, shas):
        return list(contexts)

    if current is not None:

        class _ProtectionResponse(_FakeResponse):
            def json(self):
                return current

        response = _ProtectionResponse(status_code)
    else:
        response = _FakeResponse(status_code)

    calls = [] if track_calls else None

    async def fake_api_request(method, *a, **k):
        if calls is not None:
            calls.append(method)
        return response

    monkeypatch.setattr(repo_admin, "_recent_pr_head_shas", fake_shas)
    monkeypatch.setattr(repo_admin, "_check_run_contexts", fake_contexts)
    monkeypatch.setattr(repo_admin, "api_request", fake_api_request)
    worker = repo_admin.make_branch_protection_worker(owner="hugoh", dry_run=dry_run)
    return (worker, calls) if track_calls else worker


async def test_branch_protection_worker_dry_run_ok_when_no_prs(monkeypatch):
    worker = _branch_protection_worker(monkeypatch, dry_run=True, status_code=404)
    result = await worker(REPO)
    assert result.status == Status.OK
    assert "no pull requests found yet" in result.line
    assert "(none yet)" in result.line


async def test_branch_protection_worker_dry_run_ok_when_no_check_runs(monkeypatch):
    worker = _branch_protection_worker(
        monkeypatch, dry_run=True, shas=["sha"], status_code=404
    )
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
    worker = _branch_protection_worker(
        monkeypatch, dry_run=True, shas=["sha"], contexts=["build"], status_code=403
    )
    result = await worker(REPO)
    assert result.status == Status.LIMITED_UNCHANGED
    assert result.line == (
        f"{'repo':<30} unchanged: private repo, plan does not allow branch protection"
    )


async def test_branch_protection_worker_apply_limited_unchanged_when_plan_gated(
    monkeypatch,
):
    worker = _branch_protection_worker(
        monkeypatch, dry_run=False, shas=["sha"], contexts=["build"], status_code=403
    )
    result = await worker(REPO)
    assert result.status == Status.LIMITED_UNCHANGED
    assert result.line == (
        f"{'repo':<30} unchanged: private repo, plan does not allow branch protection"
    )


async def test_branch_protection_worker_dry_run_unchanged_line(monkeypatch):
    worker = _branch_protection_worker(
        monkeypatch,
        dry_run=True,
        shas=["sha"],
        contexts=["build", "test"],
        current=_protection_state(contexts=["build", "test"]),
    )
    result = await worker(REPO)
    assert result.status == Status.UNCHANGED
    assert result.line == f"{'repo':<30} unchanged: build, test"


async def test_branch_protection_worker_dry_run_shows_old_contexts(monkeypatch):
    worker = _branch_protection_worker(
        monkeypatch,
        dry_run=True,
        shas=["sha"],
        contexts=["build", "test"],
        current=_protection_state(contexts=["build"]),
    )
    result = await worker(REPO)
    assert result.status == Status.OK
    assert result.line == (
        f"{'repo':<30} would update -> require: build, test (was: build)"
    )


async def test_branch_protection_worker_apply_unchanged_when_already_protected(
    monkeypatch,
):
    worker, calls = _branch_protection_worker(
        monkeypatch,
        dry_run=False,
        shas=["sha"],
        contexts=["build", "test"],
        current=_protection_state(contexts=["build", "test"]),
        track_calls=True,
    )
    result = await worker(REPO)
    assert result.status == Status.UNCHANGED
    assert result.line == f"{'repo':<30} unchanged: build, test"
    assert result.tag == repo_admin.Tag.APPLIED
    assert "PUT" not in calls


async def test_branch_protection_worker_apply_ok_when_updating_from_stale_state(
    monkeypatch,
):
    worker = _branch_protection_worker(
        monkeypatch,
        dry_run=False,
        shas=["sha"],
        contexts=["build", "test"],
        current=_protection_state(contexts=["build"]),
    )
    result = await worker(REPO)
    assert result.status == Status.OK
    assert result.line == f"{'repo':<30} protected (build, test)"
    assert result.tag == repo_admin.Tag.APPLIED


async def test_cmd_protection_sync_merges_exclude_list_into_skip(monkeypatch):
    seen, fake_list_repos = _capturing_list_repos()
    monkeypatch.setattr(repo_admin, "list_repos", fake_list_repos)
    monkeypatch.setattr(
        repo_admin, "default_branch_protection_exclude", lambda: {"homebrew-tap"}
    )
    args = argparse.Namespace(repos=[], skip="other-repo", dry_run=True, verbose=False)
    await repo_admin.cmd_protection_sync(args)
    assert seen["skip"] == {"homebrew-tap", "other-repo"}


async def test_cmd_protection_sync_excludes_even_without_explicit_skip(monkeypatch):
    seen, fake_list_repos = _capturing_list_repos()
    monkeypatch.setattr(repo_admin, "list_repos", fake_list_repos)
    monkeypatch.setattr(
        repo_admin, "default_branch_protection_exclude", lambda: {"homebrew-tap"}
    )
    args = argparse.Namespace(repos=[], skip=None, dry_run=True, verbose=False)
    await repo_admin.cmd_protection_sync(args)
    assert seen["skip"] == {"homebrew-tap"}


async def test_cmd_protection_sync_passes_verbose_to_run_parallel(monkeypatch):
    seen = {}

    async def fake_list_repos(owner, *, only=None, skip=None):
        return [REPO]

    async def fake_run_parallel(repos, worker, *, verbose=False, jobs=None):
        seen["verbose"] = verbose
        return []

    monkeypatch.setattr(repo_admin, "list_repos", fake_list_repos)
    monkeypatch.setattr(repo_admin, "run_parallel", fake_run_parallel)
    monkeypatch.setattr(repo_admin, "default_branch_protection_exclude", set)
    args = argparse.Namespace(repos=[], skip=None, dry_run=True, verbose=True)
    await repo_admin.cmd_protection_sync(args)
    assert seen["verbose"] is True


# ---------------------------------------------------------------------------
# sync (meta)
# ---------------------------------------------------------------------------


async def test_cmd_sync_runs_merge_protection_security_in_order(
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

    monkeypatch.setattr(repo_admin, "cmd_merge_sync", fake_merge_settings)
    monkeypatch.setattr(repo_admin, "cmd_protection_sync", fake_branch_protection)
    monkeypatch.setattr(repo_admin, "cmd_security_sync", fake_security_features)
    args = argparse.Namespace(dry_run=True, repos=[], skip=None, verbose=False)
    assert await repo_admin.cmd_sync(args) == 0
    assert calls == ["merge-settings", "branch-protection", "security-features"]


async def test_cmd_sync_continues_after_a_command_fails_and_returns_nonzero(
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

    monkeypatch.setattr(repo_admin, "cmd_merge_sync", failing)
    monkeypatch.setattr(repo_admin, "cmd_protection_sync", fake_branch_protection)
    monkeypatch.setattr(repo_admin, "cmd_security_sync", fake_security_features)
    args = argparse.Namespace(dry_run=True, repos=[], skip=None, verbose=False)
    assert await repo_admin.cmd_sync(args) == 1
    assert calls == ["merge-settings", "branch-protection", "security-features"]


async def test_cmd_sync_returns_nonzero_when_a_command_returns_nonzero(monkeypatch):
    async def fake_one(args):
        return 1

    async def fake_zero(args):
        return 0

    monkeypatch.setattr(repo_admin, "cmd_merge_sync", fake_one)
    monkeypatch.setattr(repo_admin, "cmd_protection_sync", fake_zero)
    monkeypatch.setattr(repo_admin, "cmd_security_sync", fake_zero)
    args = argparse.Namespace(dry_run=True, repos=[], skip=None, verbose=False)
    assert await repo_admin.cmd_sync(args) == 1


def test_sync_subcommand_is_registered_in_parser():
    args = repo_admin.build_parser().parse_args(["sync", "--dry-run"])
    assert args.func == repo_admin.cmd_sync
    assert args.dry_run is True


# ---------------------------------------------------------------------------
# pages sync
# ---------------------------------------------------------------------------

DOMAIN = "awesome-jj.larve.net"


def test_pages_domain_up_to_date_true_when_cname_and_https_match():
    assert repo_admin.pages_domain_up_to_date(
        {"cname": DOMAIN, "https_enforced": True}, DOMAIN
    )


def test_pages_domain_up_to_date_false_when_cname_differs():
    assert not repo_admin.pages_domain_up_to_date(
        {"cname": "other.larve.net", "https_enforced": True}, DOMAIN
    )


def test_pages_domain_up_to_date_false_when_https_not_enforced():
    assert not repo_admin.pages_domain_up_to_date(
        {"cname": DOMAIN, "https_enforced": False}, DOMAIN
    )


def test_pages_domain_https_ready_true_when_cert_approved():
    assert repo_admin.pages_domain_https_ready(
        {"https_certificate": {"state": "approved"}}
    )


def test_pages_domain_https_ready_false_when_cert_pending():
    assert not repo_admin.pages_domain_https_ready(
        {"https_certificate": {"state": "pending"}}
    )


def test_pages_domain_https_ready_false_when_no_certificate():
    assert not repo_admin.pages_domain_https_ready({})


def test_pages_domain_dry_run_line_unchanged():
    current = {"cname": DOMAIN, "https_enforced": True}
    line = repo_admin.pages_domain_dry_run_line(
        "repo", current, DOMAIN, Status.UNCHANGED
    )
    assert line == f"{'repo':<30} unchanged: cname={DOMAIN}, https enforced"


def test_pages_domain_dry_run_line_would_set_cname():
    current = {"cname": None, "https_enforced": False}
    line = repo_admin.pages_domain_dry_run_line("repo", current, DOMAIN, Status.OK)
    assert line == f"{'repo':<30} would set cname -> {DOMAIN}"


def test_pages_domain_dry_run_line_would_enable_https():
    current = {
        "cname": DOMAIN,
        "https_enforced": False,
        "https_certificate": {"state": "approved"},
    }
    line = repo_admin.pages_domain_dry_run_line("repo", current, DOMAIN, Status.OK)
    assert line == f"{'repo':<30} cname={DOMAIN}; would enable https_enforced"


def test_pages_domain_dry_run_line_cert_pending():
    current = {
        "cname": DOMAIN,
        "https_enforced": False,
        "https_certificate": {"state": "pending"},
    }
    line = repo_admin.pages_domain_dry_run_line(
        "repo", current, DOMAIN, Status.LIMITED_UNCHANGED
    )
    assert line == f"{'repo':<30} unchanged: cname={DOMAIN}; https cert pending"


def test_pages_domain_apply_line_unchanged_https_enforced():
    settings = {"cname": DOMAIN, "https_enforced": True}
    line = repo_admin.pages_domain_apply_line(
        "repo", settings, settings, DOMAIN, Status.UNCHANGED
    )
    assert line == f"{'repo':<30} unchanged: cname={DOMAIN}, https enforced"


def test_pages_domain_apply_line_unchanged_cert_pending():
    settings = {"cname": DOMAIN, "https_enforced": False}
    line = repo_admin.pages_domain_apply_line(
        "repo", settings, settings, DOMAIN, Status.LIMITED_UNCHANGED
    )
    assert line == f"{'repo':<30} unchanged: cname={DOMAIN}; https cert pending"


def test_pages_domain_apply_line_cname_changed():
    before = {"cname": None, "https_enforced": False}
    after = {"cname": DOMAIN, "https_enforced": False}
    line = repo_admin.pages_domain_apply_line("repo", before, after, DOMAIN, Status.OK)
    assert line == f"{'repo':<30} cname -> {DOMAIN}"


def test_pages_domain_apply_line_https_enabled():
    before = {"cname": DOMAIN, "https_enforced": False}
    after = {"cname": DOMAIN, "https_enforced": True}
    line = repo_admin.pages_domain_apply_line("repo", before, after, DOMAIN, Status.OK)
    assert line == f"{'repo':<30} https_enforced -> true"


async def test_pages_domain_worker_dry_run_unchanged(monkeypatch):
    async def fake_pages_config(owner, name):
        return {"cname": DOMAIN, "https_enforced": True}

    monkeypatch.setattr(repo_admin, "_pages_config", fake_pages_config)
    worker = repo_admin.make_pages_domain_worker(
        owner="hugoh", dry_run=True, domains={"repo": DOMAIN}
    )
    result = await worker(REPO)
    assert result.status == Status.UNCHANGED


async def test_pages_domain_worker_dry_run_would_set_cname(monkeypatch):
    async def fake_pages_config(owner, name):
        return {"cname": None, "https_enforced": False}

    monkeypatch.setattr(repo_admin, "_pages_config", fake_pages_config)
    worker = repo_admin.make_pages_domain_worker(
        owner="hugoh", dry_run=True, domains={"repo": DOMAIN}
    )
    result = await worker(REPO)
    assert result.status == Status.LIMITED
    assert "would set cname" in result.line


async def test_pages_domain_worker_dry_run_limited_unchanged_when_cert_pending(
    monkeypatch,
):
    async def fake_pages_config(owner, name):
        return {
            "cname": DOMAIN,
            "https_enforced": False,
            "https_certificate": {"state": "pending"},
        }

    monkeypatch.setattr(repo_admin, "_pages_config", fake_pages_config)
    worker = repo_admin.make_pages_domain_worker(
        owner="hugoh", dry_run=True, domains={"repo": DOMAIN}
    )
    result = await worker(REPO)
    assert result.status == Status.LIMITED_UNCHANGED


def _pages_domain_apply_worker(monkeypatch, responses):
    calls = []
    response_iter = iter(responses)

    async def fake_pages_config(owner, name):
        return next(response_iter)

    async def fake_api_json(method, path, **kwargs):
        calls.append((method, path, kwargs.get("json")))
        return {}

    monkeypatch.setattr(repo_admin, "_pages_config", fake_pages_config)
    monkeypatch.setattr(repo_admin, "api_json", fake_api_json)
    worker = repo_admin.make_pages_domain_worker(
        owner="hugoh", dry_run=False, domains={"repo": DOMAIN}
    )
    return worker, calls


async def test_pages_domain_worker_apply_sets_cname(monkeypatch):
    worker, calls = _pages_domain_apply_worker(
        monkeypatch,
        [
            {"cname": None, "https_enforced": False},
            {"cname": DOMAIN, "https_enforced": False},
        ],
    )
    result = await worker(REPO)
    assert result.status == Status.LIMITED
    assert calls == [("PUT", "/repos/hugoh/repo/pages", {"cname": DOMAIN})]


async def test_pages_domain_worker_apply_enables_https_when_cert_ready(monkeypatch):
    worker, calls = _pages_domain_apply_worker(
        monkeypatch,
        [
            {
                "cname": DOMAIN,
                "https_enforced": False,
                "https_certificate": {"state": "approved"},
            },
            {"cname": DOMAIN, "https_enforced": True},
        ],
    )
    result = await worker(REPO)
    assert result.status == Status.OK
    assert calls == [("PUT", "/repos/hugoh/repo/pages", {"https_enforced": True})]


async def test_pages_domain_worker_apply_unchanged_when_already_at_target(monkeypatch):
    async def fake_pages_config(owner, name):
        return {"cname": DOMAIN, "https_enforced": True}

    async def fake_api_json(*a, **k):
        raise AssertionError("should not call the API when already at target")

    monkeypatch.setattr(repo_admin, "_pages_config", fake_pages_config)
    monkeypatch.setattr(repo_admin, "api_json", fake_api_json)
    worker = repo_admin.make_pages_domain_worker(
        owner="hugoh", dry_run=False, domains={"repo": DOMAIN}
    )
    result = await worker(REPO)
    assert result.status == Status.UNCHANGED


async def test_cmd_pages_sync_defaults_to_mapped_repos(monkeypatch):
    monkeypatch.setattr(
        repo_admin.lib, "default_pages_domains", lambda: {"awesome-jj": DOMAIN}
    )
    seen, fake_list_repos = _capturing_list_repos()
    monkeypatch.setattr(repo_admin, "list_repos", fake_list_repos)
    args = argparse.Namespace(dry_run=True, repos=[], skip=None, verbose=False)
    assert await repo_admin.cmd_pages_sync(args) == 0
    assert seen == {"only": {"awesome-jj"}, "skip": None}


async def test_cmd_pages_sync_errors_on_unmapped_repo(monkeypatch, capsys):
    monkeypatch.setattr(
        repo_admin.lib, "default_pages_domains", lambda: {"awesome-jj": DOMAIN}
    )
    args = argparse.Namespace(
        dry_run=True, repos=["not-mapped"], skip=None, verbose=False
    )
    assert await repo_admin.cmd_pages_sync(args) == 1
    assert "not-mapped" in capsys.readouterr().err


def test_pages_sync_subcommand_is_registered_in_parser():
    args = repo_admin.build_parser().parse_args(["pages", "sync", "--dry-run"])
    assert args.func == repo_admin.cmd_pages_sync
    assert args.dry_run is True


# ---------------------------------------------------------------------------
# pages status
# ---------------------------------------------------------------------------


async def test_cmd_pages_status_lists_enabled_repos_and_flags_unmapped(
    monkeypatch, capsys
):
    mapped_repo = Repo(
        name="awesome-jj", default_branch="main", is_private=False, is_fork=False
    )
    unmapped_repo = Repo(
        name="other-repo", default_branch="main", is_private=False, is_fork=False
    )
    disabled_repo = Repo(
        name="no-pages", default_branch="main", is_private=False, is_fork=False
    )

    async def fake_list_repos(owner, *, only=None, skip=None):
        return [mapped_repo, unmapped_repo, disabled_repo]

    pages_configs = {
        "awesome-jj": _FakeResponseWithJson(
            200,
            {
                "cname": DOMAIN,
                "https_enforced": True,
                "html_url": f"https://{DOMAIN}",
            },
        ),
        "other-repo": _FakeResponseWithJson(
            200,
            {
                "cname": None,
                "https_enforced": False,
                "html_url": "https://hugoh.github.io/other-repo/",
            },
        ),
        "no-pages": _FakeResponseWithJson(404, {}),
    }

    async def fake_api_request(method, path, **kwargs):
        name = path.split("/")[3]
        return pages_configs[name]

    monkeypatch.setattr(repo_admin, "list_repos", fake_list_repos)
    monkeypatch.setattr(repo_admin, "api_request", fake_api_request)
    monkeypatch.setattr(
        repo_admin.lib, "default_pages_domains", lambda: {"awesome-jj": DOMAIN}
    )

    args = argparse.Namespace(repos=[], skip=None)
    assert await repo_admin.cmd_pages_status(args) == 0
    out = capsys.readouterr().out
    assert "awesome-jj" in out
    assert "other-repo" in out
    assert "no-pages" not in out
    assert f"https://{DOMAIN}" in out
    assert "https://hugoh.github.io/other-repo/" in out
    assert "not in config/pages-domains.yaml" in out
    assert "other-repo" in out.split("not in config/pages-domains.yaml")[1]


def test_pages_status_subcommand_is_registered_in_parser():
    args = repo_admin.build_parser().parse_args(["pages", "status"])
    assert args.func == repo_admin.cmd_pages_status


# ---------------------------------------------------------------------------
# pages config
# ---------------------------------------------------------------------------


def test_pages_domain_suggest_appends_domain():
    assert repo_admin.pages_domain_suggest("awesome-jj", "larve.net") == (
        "awesome-jj.larve.net"
    )


def test_pages_domain_suggest_replaces_dots_in_repo_name_with_dashes():
    assert repo_admin.pages_domain_suggest("AppBadgeWatcher.spoon", "larve.net") == (
        "AppBadgeWatcher-spoon.larve.net"
    )


async def test_cmd_pages_config_uses_explicit_repos_without_api_calls(
    monkeypatch, capsys
):
    async def fail_list_repos(*a, **k):
        raise AssertionError("should not query GitHub when --only is given")

    monkeypatch.setattr(repo_admin, "list_repos", fail_list_repos)
    args = argparse.Namespace(
        domain="larve.net", repos=["awesome-jj", "hrd"], skip=None
    )
    assert await repo_admin.cmd_pages_config(args) == 0
    out = capsys.readouterr().out
    assert "awesome-jj: awesome-jj.larve.net" in out
    assert "hrd: hrd.larve.net" in out


async def test_cmd_pages_config_auto_discovers_unmapped_enabled_repos(
    monkeypatch, capsys
):
    mapped_repo = Repo(
        name="awesome-jj", default_branch="main", is_private=False, is_fork=False
    )
    unmapped_repo = Repo(
        name="AppBadgeWatcher.spoon",
        default_branch="main",
        is_private=False,
        is_fork=False,
    )

    async def fake_list_repos(owner, *, only=None, skip=None):
        return [mapped_repo, unmapped_repo]

    async def fake_pages_enabled_repos(repos):
        return [
            (mapped_repo, {"cname": DOMAIN}),
            (unmapped_repo, {"cname": None}),
        ]

    monkeypatch.setattr(repo_admin, "list_repos", fake_list_repos)
    monkeypatch.setattr(repo_admin, "_pages_enabled_repos", fake_pages_enabled_repos)
    monkeypatch.setattr(
        repo_admin.lib, "default_pages_domains", lambda: {"awesome-jj": DOMAIN}
    )

    args = argparse.Namespace(domain="larve.net", repos=[], skip=None)
    assert await repo_admin.cmd_pages_config(args) == 0
    out = capsys.readouterr().out
    assert "AppBadgeWatcher-spoon.larve.net" in out
    assert "awesome-jj" not in out


def test_pages_config_subcommand_is_registered_in_parser():
    args = repo_admin.build_parser().parse_args(
        ["pages", "config", "--domain", "larve.net"]
    )
    assert args.func == repo_admin.cmd_pages_config
    assert args.domain == "larve.net"


# ---------------------------------------------------------------------------
# secrets sync
# ---------------------------------------------------------------------------


def test_secrets_sync_line_dry_run():
    assert repo_admin.secrets_sync_line("NAME", "repo", dry_run=True) == (
        f"{'repo':<30} would set NAME"
    )


def test_secrets_sync_line_apply():
    assert repo_admin.secrets_sync_line("NAME", "repo", dry_run=False) == (
        f"{'repo':<30} set NAME"
    )


async def test_secrets_sync_worker_dry_run_does_not_call_set_repo_secret(monkeypatch):
    async def fail_set_repo_secret(*a, **k):
        raise AssertionError("dry-run should not call set_repo_secret")

    monkeypatch.setattr(repo_admin.lib, "set_repo_secret", fail_set_repo_secret)
    worker = repo_admin.make_secrets_sync_worker(
        owner="hugoh", dry_run=True, secret_name="NAME", value="the-value"
    )
    result = await worker(REPO)
    assert result.status == Status.OK
    assert "would set NAME" in result.line


async def test_secrets_sync_worker_apply_calls_set_repo_secret(monkeypatch):
    calls = []

    async def fake_set_repo_secret(owner, repo_name, secret_name, value):
        calls.append((owner, repo_name, secret_name, value))

    monkeypatch.setattr(repo_admin.lib, "set_repo_secret", fake_set_repo_secret)
    worker = repo_admin.make_secrets_sync_worker(
        owner="hugoh", dry_run=False, secret_name="NAME", value="the-value"
    )
    result = await worker(REPO)
    assert result.status == Status.OK
    assert calls == [("hugoh", "repo", "NAME", "the-value")]


def _recording_list_repos():
    seen_only = []

    async def fake_list_repos(owner, *, only=None, skip=None):
        seen_only.append(only)
        return []

    return seen_only, fake_list_repos


async def test_cmd_secrets_sync_defaults_to_all_configured_secrets(monkeypatch):
    monkeypatch.setattr(
        repo_admin.lib,
        "default_secrets",
        lambda: {"NAME_A": ["repo-a"], "NAME_B": ["repo-b"]},
    )
    monkeypatch.setattr(
        repo_admin.lib, "decrypt_secrets", lambda: {"NAME_A": "va", "NAME_B": "vb"}
    )
    seen_only, fake_list_repos = _recording_list_repos()
    monkeypatch.setattr(repo_admin, "list_repos", fake_list_repos)
    args = argparse.Namespace(
        dry_run=False, repos=[], skip=None, secret=None, verbose=False
    )
    assert await repo_admin.cmd_secrets_sync(args) == 0
    assert sorted(seen_only, key=str) == [{"repo-a"}, {"repo-b"}]


async def test_cmd_secrets_sync_skips_secret_when_filters_leave_no_repos(monkeypatch):
    monkeypatch.setattr(
        repo_admin.lib, "default_secrets", lambda: {"NAME_A": ["repo-a"]}
    )
    monkeypatch.setattr(repo_admin.lib, "decrypt_secrets", lambda: {"NAME_A": "va"})

    async def fake_list_repos(owner, *, only=None, skip=None):
        raise AssertionError(
            "must not call list_repos when the target repo set is empty"
        )

    monkeypatch.setattr(repo_admin, "list_repos", fake_list_repos)
    args = argparse.Namespace(
        dry_run=False, repos=[], skip="repo-a", secret=None, verbose=False
    )
    assert await repo_admin.cmd_secrets_sync(args) == 0


async def test_cmd_secrets_sync_errors_on_unknown_secret_name(monkeypatch, capsys):
    monkeypatch.setattr(
        repo_admin.lib, "default_secrets", lambda: {"NAME_A": ["repo-a"]}
    )

    def fail_decrypt_secrets():
        raise AssertionError("should not decrypt when --secret is unknown")

    monkeypatch.setattr(repo_admin.lib, "decrypt_secrets", fail_decrypt_secrets)
    args = argparse.Namespace(
        dry_run=True, repos=[], skip=None, secret="NOT_CONFIGURED", verbose=False
    )
    assert await repo_admin.cmd_secrets_sync(args) == 1
    assert "NOT_CONFIGURED" in capsys.readouterr().err


async def test_cmd_secrets_sync_only_filters_within_each_secrets_repo_list(monkeypatch):
    monkeypatch.setattr(
        repo_admin.lib,
        "default_secrets",
        lambda: {
            "NAME_A": ["repo-a", "repo-shared"],
            "NAME_B": ["repo-b", "repo-shared"],
        },
    )
    monkeypatch.setattr(
        repo_admin.lib, "decrypt_secrets", lambda: {"NAME_A": "va", "NAME_B": "vb"}
    )
    seen_only, fake_list_repos = _recording_list_repos()
    monkeypatch.setattr(repo_admin, "list_repos", fake_list_repos)
    args = argparse.Namespace(
        dry_run=False, repos=["repo-shared"], skip=None, secret=None, verbose=False
    )
    assert await repo_admin.cmd_secrets_sync(args) == 0
    assert sorted(seen_only, key=str) == [{"repo-shared"}, {"repo-shared"}]


async def test_cmd_secrets_sync_skips_secret_missing_from_encrypted_file(
    monkeypatch, capsys
):
    monkeypatch.setattr(
        repo_admin.lib,
        "default_secrets",
        lambda: {"NAME_A": ["repo-a"], "NAME_B": ["repo-b"]},
    )
    monkeypatch.setattr(repo_admin.lib, "decrypt_secrets", lambda: {"NAME_B": "vb"})
    processed, fake_list_repos = _recording_list_repos()
    monkeypatch.setattr(repo_admin, "list_repos", fake_list_repos)
    args = argparse.Namespace(
        dry_run=False, repos=[], skip=None, secret=None, verbose=False
    )
    assert await repo_admin.cmd_secrets_sync(args) == 1
    err = capsys.readouterr().err
    assert "NAME_A" in err
    assert processed == [{"repo-b"}]


async def test_cmd_secrets_sync_dry_run_never_calls_decrypt_secrets(monkeypatch):
    monkeypatch.setattr(
        repo_admin.lib, "default_secrets", lambda: {"NAME_A": ["repo-a"]}
    )

    def fail_decrypt_secrets():
        raise AssertionError("dry-run should not call decrypt_secrets")

    monkeypatch.setattr(repo_admin.lib, "decrypt_secrets", fail_decrypt_secrets)

    async def fake_list_repos(owner, *, only=None, skip=None):
        return []

    monkeypatch.setattr(repo_admin, "list_repos", fake_list_repos)
    args = argparse.Namespace(
        dry_run=True, repos=[], skip=None, secret=None, verbose=False
    )
    assert await repo_admin.cmd_secrets_sync(args) == 0


def test_secrets_sync_subcommand_is_registered_in_parser():
    args = repo_admin.build_parser().parse_args(
        ["secrets", "sync", "--dry-run", "--secret", "NAME"]
    )
    assert args.func == repo_admin.cmd_secrets_sync
    assert args.secret == "NAME"


# ---------------------------------------------------------------------------
# secrets edit
# ---------------------------------------------------------------------------


def test_secrets_edit_template_seeds_empty_values_for_each_name():
    assert repo_admin.secrets_edit_template({"NAME_B", "NAME_A"}) == (
        "NAME_A: ''\nNAME_B: ''\n"
    )


async def test_cmd_secrets_edit_errors_when_no_secrets_configured(monkeypatch, capsys):
    monkeypatch.setattr(repo_admin.lib, "default_secrets", dict)
    args = argparse.Namespace()
    assert await repo_admin.cmd_secrets_edit(args) == 1
    assert "no secrets configured" in capsys.readouterr().err


async def test_cmd_secrets_edit_seeds_file_when_missing(monkeypatch):
    monkeypatch.setattr(repo_admin.lib, "default_secrets", lambda: {"NAME": ["repo-a"]})
    monkeypatch.setattr(repo_admin.lib, "SECRETS_ENC_FILE", _MissingPath())
    seeded = []
    monkeypatch.setattr(repo_admin.lib, "init_secrets_file", seeded.append)
    monkeypatch.setattr(repo_admin.lib, "edit_secrets_file", lambda: 0)
    monkeypatch.setattr(repo_admin.lib, "decrypt_secrets", lambda: {"NAME": "v"})

    args = argparse.Namespace()
    assert await repo_admin.cmd_secrets_edit(args) == 0
    assert seeded == ["NAME: ''\n"]


async def test_cmd_secrets_edit_does_not_seed_file_when_already_present(monkeypatch):
    monkeypatch.setattr(repo_admin.lib, "default_secrets", lambda: {"NAME": ["repo-a"]})
    monkeypatch.setattr(repo_admin.lib, "SECRETS_ENC_FILE", _PresentPath())

    def fail_init(*a, **k):
        raise AssertionError("should not seed when the file already exists")

    monkeypatch.setattr(repo_admin.lib, "init_secrets_file", fail_init)
    monkeypatch.setattr(repo_admin.lib, "edit_secrets_file", lambda: 0)
    monkeypatch.setattr(repo_admin.lib, "decrypt_secrets", lambda: {"NAME": "v"})

    args = argparse.Namespace()
    assert await repo_admin.cmd_secrets_edit(args) == 0


async def test_cmd_secrets_edit_returns_error_when_sops_exits_nonzero(
    monkeypatch, capsys
):
    monkeypatch.setattr(repo_admin.lib, "default_secrets", lambda: {"NAME": ["repo-a"]})
    monkeypatch.setattr(repo_admin.lib, "SECRETS_ENC_FILE", _PresentPath())
    monkeypatch.setattr(repo_admin.lib, "edit_secrets_file", lambda: 1)

    def fail_decrypt():
        raise AssertionError("should not validate when the edit itself failed")

    monkeypatch.setattr(repo_admin.lib, "decrypt_secrets", fail_decrypt)

    args = argparse.Namespace()
    assert await repo_admin.cmd_secrets_edit(args) == 1
    assert "sops" in capsys.readouterr().err


async def test_cmd_secrets_edit_warns_about_missing_and_stale_keys(monkeypatch, capsys):
    monkeypatch.setattr(
        repo_admin.lib,
        "default_secrets",
        lambda: {"NAME_A": ["repo-a"], "NAME_B": ["repo-b"]},
    )
    monkeypatch.setattr(repo_admin.lib, "SECRETS_ENC_FILE", _PresentPath())
    monkeypatch.setattr(repo_admin.lib, "edit_secrets_file", lambda: 0)
    monkeypatch.setattr(
        repo_admin.lib, "decrypt_secrets", lambda: {"NAME_A": "v", "OLD_NAME": "stale"}
    )

    args = argparse.Namespace()
    assert await repo_admin.cmd_secrets_edit(args) == 0
    err = capsys.readouterr().err
    assert "NAME_B" in err
    assert "OLD_NAME" in err


def test_secrets_edit_subcommand_is_registered_in_parser():
    args = repo_admin.build_parser().parse_args(["secrets", "edit"])
    assert args.func == repo_admin.cmd_secrets_edit


# ---------------------------------------------------------------------------
# activity
# ---------------------------------------------------------------------------


def test_activity_subcommand_is_registered_in_parser():
    args = repo_admin.build_parser().parse_args(["activity"])
    assert args.func == repo_admin.cmd_activity
    assert args.window_months == 12
    assert args.half_life_days == 30
    assert args.limit == 20


def test_activity_subcommand_accepts_custom_knobs():
    args = repo_admin.build_parser().parse_args(
        ["activity", "--window-months", "3", "--half-life-days", "7", "--limit", "5"]
    )
    assert args.window_months == 3
    assert args.half_life_days == 7
    assert args.limit == 5


async def test_cmd_activity_delegates_to_activity_run(monkeypatch):
    seen = {}

    async def fake_run(args):
        seen["args"] = args
        return 0

    monkeypatch.setattr("activity.run", fake_run)
    args = argparse.Namespace(window_months=12, half_life_days=30, limit=20)
    assert await repo_admin.cmd_activity(args) == 0
    assert seen["args"] is args
