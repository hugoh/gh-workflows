# gh-workflows

Shared, reusable GitHub Actions for hugoh's repos — the checkout + mise + `hk
check` sequence that most repos run in CI, split into two composable actions
so repos with extra setup steps (installing an apt package, running a build)
can insert them in the right place. It also hosts a uv workspace of Python
packages behind those repos' tooling.

## Contents

Actions ([usage](#usage)):

- [`setup`](#actions) — checkout + mise
- [`hk-check`](#actions) — runs `hk check`
- [`tool-bumps`](#actions) — mise tool-bump detection for gating jobs
- [`mise-latest-versions`](#actions) — version matrix for a mise tool
- [`hugoh/digest-action`](https://github.com/hugoh/digest-action) — account
  activity digest (separate repo)

Packages ([details](#packages)):

- [`repo-admin/`](repo-admin/README.md) — bulk repo settings + activity CLI
- [`asyncgh/`](asyncgh/README.md) — async GitHub REST/GraphQL transport
  ([PyPI](https://pypi.org/project/asyncgh/))
- [`reconcilekit/`](reconcilekit/README.md) — fetch-diff-apply reconcile
  kernel ([PyPI](https://pypi.org/project/reconcilekit/))
- [`repokit/`](repokit/README.md) — repo listing/filtering + CLI plumbing
  ([PyPI](https://pypi.org/project/hugoh-repokit/))

## Actions

- **`setup`** — checks out the repo and sets up mise
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
- **[`hugoh/digest-action`](https://github.com/hugoh/digest-action)** —
  builds (and optionally emails) an HTML digest of a GitHub account's repo
  activity. A separate repo (not part of this one) since GitHub Marketplace
  only publishes an Action from a repository root; depends on this repo's
  `repokit`/`asyncgh` PyPI packages.

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

Emailing a weekly digest — see
[`hugoh/digest-action`](https://github.com/hugoh/digest-action)'s README for
the full input reference.

## Why two actions instead of one reusable workflow

`go-tools` and `spoon-tools` each host a single `workflow_call` reusable
workflow for their own cluster, because every repo in those clusters runs the
exact same steps. The remaining repos aren't uniform — a couple need an extra
step interleaved between mise setup and the `hk check` — so this repo splits
the same logic into two composable actions instead, which callers can wrap
their own steps around.

## Packages

This repo also hosts a uv workspace of Python packages behind `repo-admin`'s
scripts, each with its own README:

- **[`repo-admin/`](repo-admin/README.md)** — bulk repo settings and the
  account-activity CLI tool
- **[`asyncgh/`](asyncgh/README.md)** — the GitHub REST + GraphQL transport
  (auth, retry, pagination) they share ([PyPI](https://pypi.org/project/asyncgh/))
- **[`reconcilekit/`](reconcilekit/README.md)** — the domain-agnostic
  fetch-diff-apply reconcile kernel `repo-admin`'s `sync` commands run on
  ([PyPI](https://pypi.org/project/reconcilekit/))
- **[`repokit/`](repokit/README.md)** — repo listing/filtering and
  CLI-entrypoint plumbing, published to PyPI
  ([`hugoh-repokit`](https://pypi.org/project/hugoh-repokit/)) so
  [`hugoh/digest-action`](https://github.com/hugoh/digest-action) (a
  separate repo) can depend on it too
