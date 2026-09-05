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
| `.github/workflows/hk.yml` | lint / conventional-commit check | thin caller of `hugoh/gh-workflows/.github/workflows/hk.yml` (inputs: `pre-hk`, `apt-packages`, `fetch-depth`) |
| `.github/workflows/release.yml` | tag + GitHub release | thin caller of `hugoh/gh-workflows/.github/workflows/release.yml` (`mathieudutour` + `gh release create`); **omit** if the repo isn't released |
| `.github/workflows/rerun-transient-failures.yml` | retry transient CI failures | optional; `workflow_run` trigger → [`hugoh/rerun-transient-failures`](https://github.com/hugoh/rerun-transient-failures) |
| `.renovaterc.json` | dependency updates | `{"extends": ["github>hugoh/renovate-config"]}` — nothing else unless the repo needs an override |
| `hk.pkl` | lint ruleset | `amends "package://github.com/hugoh/hk-config/..."` |
| `mise.toml` | toolchain | repo-specific tools; `hk` line is Renovate-managed via hk-config's preset |
| `cocogitto` | release tooling | not needed today — the reusable `release.yml` uses `mathieudutour/github-tag-action`. A future migration to `cog` is tracked separately. |

### How Renovate keeps a scaffolded repo current

Once the repo exists with `.renovaterc.json` → `github>hugoh/renovate-config`:

| What | Manager | Cadence |
|---|---|---|
| `mise.toml` tool versions | Renovate `mise` manager (built-in) | monthly, grouped |
| `jdx/hk` + `hugoh/hk-config` in `hk.pkl`, `hk` line in `mise.toml` | `hk-config`'s regex managers (inherited via the preset) | debounced chain |
| `uses:` pins in the workflow files | Renovate `github-actions` manager | monthly |
| `hugoh/gh-workflows` (and `go-tools` / `spoon-tools`) pins | same, but a dedicated `renovate-config` rule | within a day, automerged |

So a scaffolded repo is self-maintaining from day one. Structural changes to
the templates don't propagate — a re-scaffold (`--force` on the affected
files) or a hand-patch handles those; they're rare by design.

### How the templates themselves stay current

`templates/` carries **no version numbers**. `repo scaffold` reads the tool
versions out of `gh-workflows`'s own `mise.toml` and `hk.pkl` at render time —
those are this repo's canonical toolchain, which Renovate already keeps
current here. One source of truth, no template drift, no custom managers.
The workflow templates reference `hugoh/gh-workflows` at a ref `repo scaffold`
resolves to the latest release; third-party actions in them (`actions/checkout`
etc.) are floating-major seeds that the scaffold's `pinact run` step pins in
the new repo before first commit.

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
- Hand-write a workflow from scratch. Run `repo scaffold` (below), or for a
  bespoke case use the `setup` + `hk-check` composite actions and keep the
  standardized `on:` / `concurrency:` / `permissions:` header.

## Scaffolding a new repo

```text
cd gh-workflows
./repo-admin.sh repo scaffold ../my-new-repo --release --tests python
/project-setup                       # from the new repo: jj policy + CLAUDE.md
# review, jj commit
gh repo create hugoh/my-new-repo --private --source ../my-new-repo --push
./repo-admin.sh sync my-new-repo     # merge / protection / security
```

Flags: `--release`, `--pages`, `--action` (Marketplace/action repo — adds
`action.yml` + major-tag move), `--rerun-transient`, `--tests {none,python}`,
`--shell`, `--apt-packages`, `--pre-hk`, `--default-branch`. `--dry-run` to
preview. Templates live in [`../templates/`](../templates/).

An ongoing `repo-admin files sync` (reconcile existing repos against the
templates, like `pages sync`) is still planned; for now a re-scaffold with
`--force` on the drifted files does the job.
