# gh-workflows

Shared, reusable GitHub Actions and workflows for hugoh's repos — the
checkout, mise, and `hk check` sequence that most repos run in CI, split into
composable actions so repos with extra setup steps (installing an apt package,
running a build) can insert them in the right place.

The `repo-admin` CLI, the scaffold templates, and the
`asyncgh`/`reconcilekit`/`repokit` packages that used to live here are now in
[`hugoh/fleet-tools`](https://github.com/hugoh/fleet-tools).

## Actions

Each links to its own README with the full input/output reference (generated
from `action.yml` by `mise run gen`).

| Action | What it does |
|---|---|
| [`setup`](setup/) | checkout + mise |
| [`hk-check`](hk-check/) | runs `hk check --all` |
| [`tool-bumps`](tool-bumps/) | JSON map of which `mise.toml` tools changed since a base ref, for gating jobs |
| [`mise-latest-versions`](mise-latest-versions/) | JSON array of the newest N version series of a mise tool, for a test matrix |
| [`trim-releases`](trim-releases/) | delete old GitHub releases on a retention policy |

Two more actions live in their own repos (GitHub Marketplace only publishes an
Action from a repository root):

- [`hugoh/cog-bump`](https://github.com/hugoh/cog-bump) — `cog bump` with the
  canonical fleet `cog.toml` (`tag_prefix = "v"`, `disable_changelog`,
  `disable_bump_commit` → tag-only, no commit). Used by
  [`release.yml`](docs/workflows/release.md) and `spoon-tools`' `spoon-tag.yml`.
- [`hugoh/digest-action`](https://github.com/hugoh/digest-action) — builds (and
  optionally emails) an HTML digest of a GitHub account's repo activity;
  depends on the `repokit`/`asyncgh` PyPI packages from
  [`hugoh/fleet-tools`](https://github.com/hugoh/fleet-tools).

## Reusable workflows

| Workflow | What it does |
|---|---|
| [`.github/workflows/hk.yml`](docs/workflows/hk.md) | `workflow_call` wrapper around `setup` + `hk-check` |
| [`.github/workflows/release.yml`](docs/workflows/release.md) | `workflow_call` tag + GitHub Release via `hugoh/cog-bump` |
| [`.github/workflows/secret-scan.yml`](docs/workflows/secret-scan.md) | `workflow_call` TruffleHog OSS verified-secret scan |
| [`.github/workflows/semantic-pr.yml`](docs/workflows/semantic-pr.md) | `workflow_call` Conventional-Commit PR-title lint via `amannn/action-semantic-pull-request` |

## Usage

The canonical lint job — `setup`, then `hk-check`, with any extra steps
(a build, an apt install) in between:

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
      - run: npm ci && npm run build   # optional
      - uses: hugoh/gh-workflows/hk-check@<pinned-sha>
```

See each action's or workflow's README (linked above) for its own usage
example and input reference.

## Why two actions instead of one reusable workflow

`go-tools` and `spoon-tools` each host a single `workflow_call` reusable
workflow for their own cluster, because every repo in those clusters runs the
exact same steps. The remaining repos aren't uniform — a couple need an extra
step interleaved between mise setup and the `hk check` — so this repo splits
the same logic into two composable actions instead, which callers can wrap
their own steps around. [`hk.yml`](docs/workflows/hk.md) is there for the repos
that _are_ uniform.
