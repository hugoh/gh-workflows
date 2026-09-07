# gh-workflows

Shared, reusable GitHub Actions and workflows for hugoh's repos — the
checkout, mise, and `hk check` sequence that most repos run in CI, split into
composable actions so repos with extra setup steps (installing an apt package,
running a build) can insert them in the right place.

The `repo-admin` CLI, the scaffold templates, and the
`asyncgh`/`reconcilekit`/`repokit` packages that used to live here are now in
[`hugoh/fleet-tools`](https://github.com/hugoh/fleet-tools).

## Contents

Actions ([usage](#usage)):

- [`setup`](#actions) — checkout + mise
- [`hk-check`](#actions) — runs `hk check`
- [`tool-bumps`](#actions) — mise tool-bump detection for gating jobs
- [`mise-latest-versions`](#actions) — version matrix for a mise tool
- [`trim-releases`](#actions) — delete old GitHub releases on a retention policy
- [`hugoh/cog-bump`](https://github.com/hugoh/cog-bump) — `cog bump` with the
  canonical fleet `cog.toml` (separate repo)
- [`hugoh/digest-action`](https://github.com/hugoh/digest-action) — account
  activity digest (separate repo)

Reusable workflows:

- `.github/workflows/hk.yml` — `workflow_call` wrapper around `setup` +
  `hk-check` (inputs: `fetch-depth`, `pre-hk`, `apt-packages`,
  `extra-cache-key-cmd`, `extra-cache-paths` — see `setup`)
- `.github/workflows/release.yml` — `workflow_call` tag + GitHub Release via
  `hugoh/cog-bump` (inputs: `tag`, `notes`, `major-tag`)

## Actions

- **`setup`** — checks out the repo and sets up mise. Inputs: `fetch-depth`,
  `extra-cache-key-cmd`, `extra-cache-paths`. The latter two cache a subset
  of mise's installed tools, keyed on the stdout of a caller-supplied
  `extra-cache-key-cmd` instead of the whole mise config: for tools that are
  expensive to install (built from source, heavy postinstall hooks), hashing
  the whole config busts the cache on every unrelated tool version bump, so
  the caller instead extracts just the relevant bits of its `mise.toml`
  (e.g. one tool's pinned version plus its `[hooks]` block) and passes the
  paths to cache under that key (`extra-cache-paths`, newline-separated).
  No-ops if `extra-cache-key-cmd` is empty. `.github/workflows/hk.yml`
  exposes the same two inputs for its own (inlined) checkout+mise step.
- **`hk-check`** — runs `hk check --no-progress --profile ci --all --no-fail-fast`
  (every check runs even after one fails, so all failures surface in one pass)
- **`tool-bumps`** — emits a `tools` output: a JSON map of which `mise.toml`
  `[tools]` entries changed since a base ref, for gating downstream jobs on a
  specific tool's version bump. Needs the repo checked out with full history
  (`fetch-depth: 0`).
- **`mise-latest-versions`** — emits a `matrix` output: a JSON array of the
  newest N version series of a mise tool, from `mise ls-remote <tool>`, for a
  version-compatibility test matrix. Inputs: `tool` (required, e.g. `jj`),
  `level` (`major`/`minor`/`patch`, default `minor`), `count` (default `3`) —
  e.g. `level: minor` → `["0.42","0.43","0.44"]`, `level: major, count: 2` →
  `["1","2"]`. Needs `mise` on `PATH` (run `setup` first). `go-tools`'
  `go-tool-compat.yml` reusable workflow wraps this.
- **`trim-releases`** — deletes old GitHub releases with `gh`: keeps the
  newest `keep-full` full releases (default `10`) and the newest
  `keep-prereleases` prereleases (default `3`), and drops those kept
  prereleases once older than `prerelease-cutoff-days` (default `14`). Set
  `dry-run: true` to only print. Needs `gh` on `PATH` (default on GitHub
  runners) and a `contents: write` token (`github-token`, defaults to
  `github.token`); no checkout required. Callers keep their own `schedule` /
  `workflow_dispatch` triggers.
- **[`hugoh/cog-bump`](https://github.com/hugoh/cog-bump)** — `cog bump` with
  the canonical fleet `cog.toml` (`tag_prefix = "v"`, `disable_changelog`,
  `disable_bump_commit` → tag-only, no commit). Pushes the tag by default;
  `tag` output is empty when nothing is releasable. Needs the repo checked out
  with `fetch-depth: 0`. Used by the reusable `release.yml` and `spoon-tools`'
  `spoon-tag.yml`. A separate repo (not part of this one) since GitHub
  Marketplace only publishes an Action from a repository root.
- **[`hugoh/digest-action`](https://github.com/hugoh/digest-action)** —
  builds (and optionally emails) an HTML digest of a GitHub account's repo
  activity. A separate repo (not part of this one) since GitHub Marketplace
  only publishes an Action from a repository root; depends on the
  `repokit`/`asyncgh` PyPI packages from
  [`hugoh/fleet-tools`](https://github.com/hugoh/fleet-tools).

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

Building a jj-version test matrix:

```yaml
jobs:
  versions:
    runs-on: ubuntu-latest
    outputs:
      matrix: ${{ steps.pick.outputs.matrix }}
    steps:
      - uses: hugoh/gh-workflows/setup@<pinned-sha>
      - uses: hugoh/gh-workflows/mise-latest-versions@<pinned-sha>
        id: pick
        with:
          tool: jj
```

Gating a job on a tool bump:

```yaml
jobs:
  changes:
    runs-on: ubuntu-latest
    outputs:
      copier: ${{ fromJSON(steps.bumps.outputs.tools).copier == true }}
    steps:
      - uses: hugoh/gh-workflows/setup@<pinned-sha>
        with:
          fetch-depth: 0
      - uses: hugoh/gh-workflows/tool-bumps@<pinned-sha>
        id: bumps
```

Trimming old releases on a schedule:

```yaml
name: Trim Old Releases
on:
  workflow_dispatch:
    inputs:
      dry_run:
        type: boolean
        default: false
  schedule:
    - cron: "0 8 * * 0"
permissions:
  contents: write
jobs:
  cleanup:
    runs-on: ubuntu-latest
    steps:
      - uses: hugoh/gh-workflows/trim-releases@<pinned-sha>
        with:
          dry-run: ${{ inputs.dry_run }}
```

## Why two actions instead of one reusable workflow

`go-tools` and `spoon-tools` each host a single `workflow_call` reusable
workflow for their own cluster, because every repo in those clusters runs the
exact same steps. The remaining repos aren't uniform — a couple need an extra
step interleaved between mise setup and the `hk check` — so this repo splits
the same logic into two composable actions instead, which callers can wrap
their own steps around.
