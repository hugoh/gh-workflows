# `.github/workflows/hk.yml`

`workflow_call` wrapper around [`setup`](../../setup/) + [`hk-check`](../../hk-check/)
for repos in a cluster that all run the exact same lint job. Repos that need an
extra step interleaved between mise setup and `hk check` should compose the two
actions directly instead — see
[Why two actions instead of one reusable workflow](../../README.md#why-two-actions-instead-of-one-reusable-workflow).

`pre-hk` runs after mise setup, before `hk check`. `apt-packages` are installed
(and cached) first. `extra-cache-key-cmd` / `extra-cache-paths` behave as in
[`setup`](../../setup/).

## Usage

```yaml
jobs:
  hk:
    uses: hugoh/gh-workflows/.github/workflows/hk.yml@<pinned-sha>
    permissions:
      contents: read
      packages: read
      statuses: write
```

## Inputs

<!-- AUTO-DOC-INPUT:START - Do not remove or modify this section -->

|        INPUT        | REQUIRED | DEFAULT |                                  DESCRIPTION                                   |
|---------------------|----------|---------|--------------------------------------------------------------------------------|
|    apt-packages     |  false   |         |            Space-separated apt packages the lint job needs (cached)            |
| extra-cache-key-cmd |  false   |         | Shell command whose stdout determines an extra cache key. Leave empty to skip. |
|  extra-cache-paths  |  false   |         |                Newline-separated paths to cache under that key.                |
|     fetch-depth     |  false   |  `"1"`  |    Commits to fetch (0 = full history, for cog/conventional-commit checks)     |
|       pre-hk        |  false   |         |    Shell command to run after mise setup, before `hk check` (e.g. a build)     |

<!-- AUTO-DOC-INPUT:END -->
