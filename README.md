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

## `repo-admin/` — bulk repo settings

Bulk-applies account-wide repo settings across all of hugoh's non-archived
repos, via the GitHub REST API (authenticated through `gh auth token`, so it
reuses `gh`'s existing login rather than managing a separate credential). A
single Python CLI (`repo_admin.py`, run through `uv`) with one subcommand per
operation; repos are processed in parallel (`GH_JOBS`, default 6). Forks are
excluded by default — except those listed in
`include-forks.txt`; edit that file to add more, or override per-run with
`GH_INCLUDE_FORKS` (comma-separated). Every subcommand supports
`--only name1,name2` / `--skip name1,name2` to scope to a subset; `GH_OWNER`
overrides the default owner.

```text
cd repo-admin
uv run repo_admin.py <command> [--dry-run] [--only name1,name2] [--skip name1,name2]
```

- **`list`** — lists repos as a table: name, default branch, private, fork
- **`merge-settings [--dry-run]`** — enables auto-merge,
  delete-branch-on-merge, and PR-branch auto-update (the last one matters
  because branch protection requires PR branches to be up to date before
  merging; without auto-update, auto-merge PRs stall needing a manual
  "Update branch" click)
- **`branch-protection [--dry-run]`** — requires status checks to pass and a
  PR (0 approvals needed, no direct pushes) before merging, matching the
  convention `go-tools`' `mise run gh-repo-setup` already established.
  Required contexts are detected from the most recent pull request's check
  runs (not the default branch tip — see `repo_admin.py`'s header comment
  for why). `--dry-run` diffs each repo's current protection against the
  baseline and prints "unchanged: ..." or "would update", rather than just
  showing what would be required. Private repos on a plan without
  branch-protection access are reported, not failed. Repos listed in
  `branch-protection-exclude.txt` (e.g. `homebrew-tap`, which has no CI/PR
  workflow) are always skipped; override per-run with
  `GH_BRANCH_PROTECTION_EXCLUDE`.
- **`security-features [--dry-run]`** — enables Dependabot vulnerability
  alerts (all repos, free), plus secret scanning, secret scanning push
  protection, Dependabot security updates, and private vulnerability
  reporting (public repos only — private repos need GitHub Advanced
  Security, a paid add-on this account's plan doesn't include; such repos
  are reported as unavailable, not failed).
- **`all [--dry-run]`** — runs `merge-settings`, `branch-protection`, then
  `security-features` in sequence. One command failing doesn't stop the
  others; the exit code is nonzero if any of them failed.
- **`pages-domain [--dry-run]`** — sets each repo's GitHub Pages custom
  domain from the repo → domain mapping in `pages-domains.yaml` (the same
  file `iac/cloudflare`'s OpenTofu config reads to generate the matching DNS
  records). Only repos listed in the mapping are touched — `--only` narrows
  that set further rather than expanding it, and errors if given a repo not
  in the mapping. `https_enforced` is only ever turned on, and only once
  GitHub reports the domain's certificate as issued; a freshly-set domain
  needs a later rerun to pick that up once DNS/cert issuance catches up.
- **`pages-status`** — read-only: lists every repo with GitHub Pages enabled
  and its current custom domain/HTTPS state, flagging any that aren't yet in
  `pages-domains.yaml`.
- **`pages-domain-config --domain <domain>`** — prints
  `pages-domains.yaml`-formatted entries to stdout: `<repo>.<domain>` per
  repo, dots in the repo name replaced with dashes (e.g. `AudioPilot.spoon`
  → `AudioPilot-spoon.<domain>`, since a raw dot would split the hostname
  into an extra DNS label). With `--only`, generates for exactly those repos
  with no API calls; with neither `--only`/`--skip`, auto-discovers repos
  with Pages enabled but missing from `pages-domains.yaml` (the same set
  `pages-status` flags) and suggests entries for those.

Run a mutating command with `--dry-run` first and review the output before
applying. Tests: `cd repo-admin && uv run pytest`.
