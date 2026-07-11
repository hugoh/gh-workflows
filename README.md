# gh-workflows

Shared, reusable GitHub Actions for hugoh's repos — the checkout + mise + `hk
check` sequence that most repos run in CI, split into two composable actions
so repos with extra setup steps (installing an apt package, running a build)
can insert them in the right place.

## Actions

- **`setup`** — checks out the repo and sets up mise
- **`hk-check`** — runs `hk check --no-progress --profile ci --all`, dumping
  the log on failure

## Usage

```yaml
jobs:
  lint:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: read
      statuses: write
    steps:
      - uses: hugoh/gh-workflows/setup@<pinned-sha>
      - uses: hugoh/gh-workflows/hk-check@<pinned-sha>
```

With extra steps in between (e.g. a build that must run before `hk check`):

```yaml
steps:
  - uses: hugoh/gh-workflows/setup@<pinned-sha>
  - run: npm ci && npm run build
  - uses: hugoh/gh-workflows/hk-check@<pinned-sha>
```

## Why two actions instead of one reusable workflow

`go-tools` and `spoon-tools` each host a single `workflow_call` reusable
workflow for their own cluster, because every repo in those clusters runs the
exact same steps. The remaining repos aren't uniform — a couple need an extra
step interleaved between mise setup and the `hk check` — so this repo splits
the same logic into two composable actions instead, which callers can wrap
their own steps around.

## `repo-admin/` scripts

Bulk-apply account-wide repo settings across all of hugoh's non-archived
repos, using `gh` + `jq`. Forks are excluded by default — except those
listed in `include-forks.txt`; edit that file to add more, or override per-run
with `GH_INCLUDE_FORKS` (comma-separated). Each script supports
`--only name1,name2` / `--skip name1,name2` to scope to a subset; `GH_OWNER`
overrides the default owner.

- **`list-repos.sh`** — lists repos as a table: name, default branch,
  private, fork
- **`set-merge-settings.sh [--dry-run]`** — enables auto-merge,
  delete-branch-on-merge, and PR-branch auto-update (the last one matters
  because branch protection requires PR branches to be up to date before
  merging; without auto-update, auto-merge PRs stall needing a manual
  "Update branch" click)
- **`set-branch-protection.sh [--dry-run]`** — requires status checks to pass
  and a PR (0 approvals needed, no direct pushes) before merging, matching
  the convention `go-tools`' `mise run gh-repo-setup` already established.
  Required contexts are detected from the most recent pull request's check
  runs (not the default branch tip — see the script's header comment for
  why). `--dry-run` diffs each repo's current protection against the
  baseline and prints "up to date" or "would update", rather than just
  showing what would be required. Private repos on a plan without
  branch-protection access are reported, not failed.
- **`set-security-features.sh [--dry-run]`** — enables Dependabot
  vulnerability alerts (all repos, free), plus secret scanning, secret
  scanning push protection, Dependabot security updates, and private
  vulnerability reporting (public repos only — private repos need GitHub
  Advanced Security, a paid add-on this account's plan doesn't include; such
  repos are reported as unavailable, not failed).

Run with `--dry-run` first and review the output before applying.
