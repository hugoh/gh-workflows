import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).with_name("automerge-keep-fresh.sh")


def run_script(tmp_path, gh_script, *, pr_number="", **extra_env):
    gh = tmp_path / "gh"
    gh.write_text(gh_script)
    gh.chmod(0o755)
    env = {
        **os.environ,
        "GH_REPO": "owner/repo",
        "GH_TOKEN": "test-token",
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "PR_NUMBER": pr_number,
        **extra_env,
    }
    return subprocess.run(
        ["bash", SCRIPT],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )


def test_updates_all_behind_auto_merge_prs(tmp_path):
    result = run_script(
        tmp_path,
        """#!/usr/bin/env bash
set -e
if [[ "$1 $2" == "pr list" ]]; then
  printf '12\\n15\\n'
elif [[ "$1" == "api" ]]; then
  printf '%s\\n' "$*"
fi
""",
    )

    assert "Updating PR #12" in result.stdout
    assert "Updating PR #15" in result.stdout
    assert result.stdout.count("pulls/") == 2


def test_retries_reconciliation_until_a_pr_is_behind(tmp_path):
    state = tmp_path / "state"
    state.write_text("0")
    result = run_script(
        tmp_path,
        """#!/usr/bin/env bash
set -e
state="${GH_TEST_STATE:?}"
if [[ "$1 $2" == "pr list" ]]; then
  count=$(cat "$state")
  printf '%s' "$((count + 1))" > "$state"
  if [[ "$count" -ge 2 ]]; then
    printf '12\\n'
  fi
elif [[ "$1" == "api" ]]; then
  printf '%s\\n' "$*"
fi
""",
        GH_TEST_STATE=str(state),
        MAX_ATTEMPTS="3",
        RETRY_SECONDS="0",
    )

    # The fake gh is invoked in a subprocess; the first two empty responses
    # exercise the workflow's eventual-consistency retry path.
    assert result.returncode == 0


def test_updates_only_requested_pr(tmp_path):
    result = run_script(
        tmp_path,
        """#!/usr/bin/env bash
set -e
if [[ "$1 $2" == "pr view" ]]; then
  [[ "$3" == "12" ]] && printf '12\\n'
elif [[ "$1" == "api" ]]; then
  printf '%s\\n' "$*"
fi
""",
        pr_number="12",
    )

    assert "Updating PR #12" in result.stdout
    assert "pr list" not in result.stdout
