from datetime import UTC, datetime

import httpx
import pytest
import respx
from activity import (
    bucket_counts,
    decay_score,
    fetch_commit_dates,
    render_table,
)
from lib import API_BASE, Repo

NOW = datetime(2026, 8, 23, tzinfo=UTC)

REPO_A = Repo(name="repo-a", default_branch="main", is_private=False, is_fork=False)
REPO_B = Repo(name="repo-b", default_branch="main", is_private=True, is_fork=False)


@pytest.fixture(autouse=True)
def fake_auth_token(monkeypatch):
    monkeypatch.setattr("asyncgh.client._auth_token", lambda: "fake-token")


# ---------------------------------------------------------------------------
# decay_score
# ---------------------------------------------------------------------------


def test_decay_score_empty_dates_is_zero():
    assert decay_score([], NOW, half_life_days=30) == 0


def test_decay_score_commit_today_contributes_one():
    assert decay_score([NOW], NOW, half_life_days=30) == pytest.approx(1.0)


def test_decay_score_commit_one_half_life_ago_contributes_half():
    dt = datetime(2026, 7, 24, tzinfo=UTC)  # 30 days before NOW
    assert decay_score([dt], NOW, half_life_days=30) == pytest.approx(0.5)


def test_decay_score_commit_two_half_lives_ago_contributes_quarter():
    dt = datetime(2026, 6, 24, tzinfo=UTC)  # 60 days before NOW
    assert decay_score([dt], NOW, half_life_days=30) == pytest.approx(0.25)


def test_decay_score_sums_multiple_commits():
    today = NOW
    thirty_days_ago = datetime(2026, 7, 24, tzinfo=UTC)
    assert decay_score(
        [today, thirty_days_ago], NOW, half_life_days=30
    ) == pytest.approx(1.5)


def test_decay_score_smaller_half_life_decays_faster():
    dt = datetime(2026, 7, 24, tzinfo=UTC)  # 30 days before NOW
    slow = decay_score([dt], NOW, half_life_days=90)
    fast = decay_score([dt], NOW, half_life_days=7)
    assert fast < slow


# ---------------------------------------------------------------------------
# bucket_counts
# ---------------------------------------------------------------------------


def test_bucket_counts_all_zero_for_no_commits():
    assert bucket_counts([], NOW) == (0, 0, 0)


def test_bucket_counts_within_one_month_counts_in_all_three_buckets():
    dt = NOW  # today
    assert bucket_counts([dt], NOW) == (1, 1, 1)


def test_bucket_counts_between_one_and_six_months_excludes_1mo_bucket():
    dt = datetime(2026, 5, 23, tzinfo=UTC)  # ~3 months before NOW
    assert bucket_counts([dt], NOW) == (0, 1, 1)


def test_bucket_counts_between_six_and_twelve_months_only_in_12mo_bucket():
    dt = datetime(2026, 1, 1, tzinfo=UTC)  # ~8 months before NOW
    assert bucket_counts([dt], NOW) == (0, 0, 1)


def test_bucket_counts_older_than_twelve_months_excluded_entirely():
    dt = datetime(2024, 1, 1, tzinfo=UTC)
    assert bucket_counts([dt], NOW) == (0, 0, 0)


# ---------------------------------------------------------------------------
# fetch_commit_dates
# ---------------------------------------------------------------------------


def _commit(date="2026-08-20T10:00:00Z"):
    return {"commit": {"author": {"date": date}}}


async def test_fetch_commit_dates_parses_dates(httpx2_mock: respx.Router):
    httpx2_mock.get(f"{API_BASE}/repos/hugoh/repo-a/commits").mock(
        return_value=httpx.Response(200, json=[_commit(date="2026-08-20T10:00:00Z")])
    )
    dates = await fetch_commit_dates(
        "hugoh", [REPO_A], "hugoh", datetime(2025, 8, 23, tzinfo=UTC)
    )
    assert dates == {"repo-a": [datetime(2026, 8, 20, 10, 0, 0, tzinfo=UTC)]}


async def test_fetch_commit_dates_combines_multiple_repos(httpx2_mock: respx.Router):
    httpx2_mock.get(f"{API_BASE}/repos/hugoh/repo-a/commits").mock(
        return_value=httpx.Response(200, json=[_commit(date="2026-08-20T10:00:00Z")])
    )
    httpx2_mock.get(f"{API_BASE}/repos/hugoh/repo-b/commits").mock(
        return_value=httpx.Response(200, json=[_commit(date="2026-08-19T10:00:00Z")])
    )
    dates = await fetch_commit_dates(
        "hugoh", [REPO_A, REPO_B], "hugoh", datetime(2025, 8, 23, tzinfo=UTC)
    )
    assert set(dates) == {"repo-a", "repo-b"}


async def test_fetch_commit_dates_follows_pagination(httpx2_mock: respx.Router):
    httpx2_mock.get(
        f"{API_BASE}/repos/hugoh/repo-a/commits", params={"page": "2"}
    ).mock(
        return_value=httpx.Response(200, json=[_commit(date="2026-08-19T10:00:00Z")])
    )
    httpx2_mock.get(f"{API_BASE}/repos/hugoh/repo-a/commits").mock(
        return_value=httpx.Response(
            200,
            json=[_commit(date="2026-08-20T10:00:00Z") for _ in range(100)],
        )
    )
    dates = await fetch_commit_dates(
        "hugoh", [REPO_A], "hugoh", datetime(2025, 8, 23, tzinfo=UTC)
    )
    assert len(dates["repo-a"]) == 101


async def test_fetch_commit_dates_empty_repo_returns_empty_list(
    httpx2_mock: respx.Router,
):
    httpx2_mock.get(f"{API_BASE}/repos/hugoh/repo-a/commits").mock(
        return_value=httpx.Response(200, json=[])
    )
    dates = await fetch_commit_dates(
        "hugoh", [REPO_A], "hugoh", datetime(2025, 8, 23, tzinfo=UTC)
    )
    assert dates == {"repo-a": []}


async def test_fetch_commit_dates_filters_by_author_param(httpx2_mock: respx.Router):
    httpx2_mock.get(
        f"{API_BASE}/repos/hugoh/repo-a/commits", params={"author": "hugoh"}
    ).mock(return_value=httpx.Response(200, json=[]))
    await fetch_commit_dates(
        "hugoh", [REPO_A], "hugoh", datetime(2025, 8, 23, tzinfo=UTC)
    )
    request = httpx2_mock.calls[0].request
    assert request.url.params["author"] == "hugoh"


# ---------------------------------------------------------------------------
# render_table
# ---------------------------------------------------------------------------


def _row(name="repo-a", score=1.5, one=1, six=2, twelve=3, private=False):
    return {
        "repo": name,
        "score": score,
        "commits_1mo": one,
        "commits_6mo": six,
        "commits_12mo": twelve,
        "private": private,
    }


def test_render_table_includes_repo_names():
    table = render_table("Test", [_row(name="repo-a"), _row(name="repo-b")], limit=20)
    rendered = "".join(str(cell) for column in table.columns for cell in column.cells)
    assert "repo-a" in rendered
    assert "repo-b" in rendered


def test_render_table_truncates_to_limit():
    rows = [_row(name=f"repo-{i}") for i in range(5)]
    table = render_table("Test", rows, limit=2)
    assert table.row_count == 2


def test_render_table_limit_zero_means_unlimited():
    rows = [_row(name=f"repo-{i}") for i in range(5)]
    table = render_table("Test", rows, limit=0)
    assert table.row_count == 5
