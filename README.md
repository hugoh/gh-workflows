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
