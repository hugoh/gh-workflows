import json
from datetime import UTC, datetime

import pytest
import trim_releases
from trim_releases import (
    delete_release,
    fetch_releases,
    main,
    select_tags_to_delete,
)

NOW = datetime(2026, 6, 1, tzinfo=UTC)


def release(tag, prerelease, days_ago):
    created = datetime.fromtimestamp(NOW.timestamp() - days_ago * 86400, tz=UTC)
    return {
        "tagName": tag,
        "isPrerelease": prerelease,
        "createdAt": created.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def test_keeps_the_ten_newest_full_releases_and_deletes_the_rest():
    releases = [release(f"v-full-{i:02d}", False, i) for i in range(1, 13)]

    deleted = select_tags_to_delete(releases, NOW, 10, 3, 14)

    assert sorted(deleted) == ["v-full-11", "v-full-12"]


def test_keeps_the_three_newest_prereleases_within_the_cutoff():
    releases = [release(f"pre-{i}", True, i) for i in range(5)]

    deleted = select_tags_to_delete(releases, NOW, 10, 3, 14)

    assert sorted(deleted) == ["pre-3", "pre-4"]


def test_deletes_even_the_three_newest_prereleases_when_all_past_the_cutoff():
    releases = [release(f"pre-old-{i}", True, 20 + i) for i in range(3)]

    deleted = select_tags_to_delete(releases, NOW, 10, 3, 14)

    assert sorted(deleted) == ["pre-old-0", "pre-old-1", "pre-old-2"]


def test_nothing_to_delete():
    releases = [release("v-full-1", False, 0), release("v-full-2", False, 1)]

    assert select_tags_to_delete(releases, NOW, 10, 3, 14) == []


def test_retention_knobs_are_honoured():
    releases = [release(f"v-full-{i:02d}", False, i) for i in range(1, 6)]

    deleted = select_tags_to_delete(releases, NOW, 2, 3, 14)

    assert sorted(deleted) == ["v-full-03", "v-full-04", "v-full-05"]


@pytest.fixture
def mock_releases(monkeypatch):
    def _mock(releases):
        monkeypatch.setattr("trim_releases.fetch_releases", lambda: releases)
        deleted = []
        monkeypatch.setattr("trim_releases.delete_release", deleted.append)
        return deleted

    return _mock


def test_main_dry_run_reports_without_deleting(mock_releases, capsys):
    releases = [release(f"v-full-{i:02d}", False, i) for i in range(1, 13)]
    deleted = mock_releases(releases)

    assert main(["--dry-run"]) == 0
    assert deleted == []
    out = capsys.readouterr().out
    assert "Would delete release v-full-11" in out
    assert "Would delete release v-full-12" in out


def test_main_deletes_selected_tags(mock_releases, capsys):
    releases = [release(f"v-full-{i:02d}", False, i) for i in range(1, 13)]
    deleted = mock_releases(releases)

    assert main([]) == 0
    assert sorted(deleted) == ["v-full-11", "v-full-12"]


def test_fetch_releases_parses_gh_json(monkeypatch):
    payload = [
        {"tagName": "v1", "isPrerelease": False, "createdAt": "2026-01-01T00:00:00Z"}
    ]

    def fake_run(cmd, **kwargs):
        assert cmd[:3] == ["gh", "release", "list"]
        return type("R", (), {"stdout": json.dumps(payload)})

    monkeypatch.setattr(trim_releases.subprocess, "run", fake_run)

    assert fetch_releases() == payload


def test_delete_release_invokes_gh(monkeypatch):
    calls = []
    monkeypatch.setattr(
        trim_releases.subprocess, "run", lambda cmd, **kwargs: calls.append(cmd)
    )

    delete_release("v9")

    assert calls == [["gh", "release", "delete", "v9", "--yes"]]


def test_main_reports_when_nothing_to_delete(monkeypatch, capsys):
    monkeypatch.setattr(
        "trim_releases.fetch_releases",
        lambda: [release("v-full-1", False, 0)],
    )
    monkeypatch.setattr(
        "trim_releases.delete_release",
        lambda tag: pytest.fail(f"unexpected delete: {tag}"),
    )

    assert main([]) == 0
    assert "No releases to delete." in capsys.readouterr().out
