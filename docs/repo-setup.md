# New-repo setup

What every repo in the fleet is expected to have. Most of it is applied in
bulk by [`repo-admin`](../repo-admin/README.md); the rest is a handful of
files that get scaffolded once and then maintained by Renovate.

## Applied by `repo-admin` (account-wide, no per-repo work)

Run from `gh-workflows/`:

```text
./repo-admin.sh sync            # merge + protection + security
./repo-admin.sh pages sync      # only for repos in config/pages-domains.yaml
```

- **`merge sync`** — auto-merge, delete-branch-on-merge, PR-branch auto-update.
- **`protection sync`** — require status checks + a PR (0 approvals, no direct
  pushes) before merging. Required contexts are sampled from the latest PR's
  own-workflow check runs.
- **`security sync`** — Dependabot alerts + (public repos) secret scanning,
  Dependabot security updates, private vulnerability reporting, CodeQL default
  setup.

## Per-repo files (scaffold once, Renovate maintains)

| File | Purpose | Notes |
|---|---|---|
| `.github/workflows/hk.yml` | lint / conventional-commit check | thin caller of `hugoh/gh-workflows/.github/workflows/hk.yml` |
| `.github/workflows/release.yml` | tag + GitHub release | thin caller of `hugoh/gh-workflows/.github/workflows/release.yml`; **omit** if the repo isn't released |
| `.github/workflows/rerun-transient-failures.yml` | retry transient CI failures | optional; `workflow_run` trigger → [`hugoh/rerun-transient-failures`](https://github.com/hugoh/rerun-transient-failures) |
| `.renovaterc.json` | dependency updates | `{"extends": ["github>hugoh/renovate-config"]}` — nothing else unless the repo needs an override |
| `hk.pkl` | lint ruleset | `amends "package://github.com/hugoh/hk-config/..."` |
| `mise.toml` | toolchain | repo-specific tools; `hk` line is Renovate-managed via hk-config's preset |
| `cog.toml` | release config | **only if overriding** — monorepo `[packages]`, a custom changelog template, or `pre_bump_hooks`. The reusable `release.yml` supplies the canonical config otherwise. |

Once these exist, Renovate keeps every version line current — the reusable
workflow `@<sha>` pins, action pins, tool pins. Structural changes to the
templates ship inside the reusable workflows and arrive via the `@<sha>` bump;
they don't need a re-scaffold.

### Standard caller shapes

`hk.yml`:

```yaml
name: hk
on:
  push: { branches: [main] }
  pull_request:
permissions: {}
jobs:
  hk:
    uses: hugoh/gh-workflows/.github/workflows/hk.yml@<pinned-sha>
    permissions: { contents: read, packages: read, statuses: write }
    # with:
    #   pre-hk: bun install --frozen-lockfile   # only where a build must run first
    #   apt-packages: libreadline-dev
```

`release.yml`:

```yaml
name: release
on:
  push: { branches: [main] }
permissions:
  contents: write
jobs:
  release:
    uses: hugoh/gh-workflows/.github/workflows/release.yml@<pinned-sha>
    permissions: { contents: write }
```

## Do NOT

- Add `renovate/**` to any `push:` trigger. Renovate opens PRs and merges them
  itself (`renovate-config` sets `platformAutomerge: false`); a `renovate/**`
  push trigger just double-builds.
- Hand-copy the reusable workflow bodies. If a repo genuinely can't use the
  thin caller (bespoke interleaved steps), use the `setup` + `hk-check`
  composite actions directly and keep the standardized `on:` / `concurrency:`
  / `permissions:` header.

## Automating the scaffold

`repo-admin` covers settings; the per-repo files are a planned
`repo-admin files sync` command (fetch-diff-apply via `reconcilekit`, like
`pages sync`) that renders the canonical caller set from this repo's templates
into a target repo and opens a PR. Until then, copy the shapes above.
