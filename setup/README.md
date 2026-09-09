# setup

Checks out the repo and sets up mise. The first step of most CI jobs in the
fleet — pair it with [`hk-check`](../hk-check/) and put any extra steps
(running a build) in between.

`apt-packages` are installed (and cached) first, before checkout — for
packages a job needs on every run (e.g. headers mise needs to build a tool
from source), not one-off steps.

`extra-cache-key-cmd` / `extra-cache-paths` cache a subset of mise's installed
tools keyed on the stdout of a caller-supplied command instead of the whole
mise config. For tools that are expensive to install (built from source, heavy
postinstall hooks), hashing the whole config busts the cache on every unrelated
version bump, so the caller instead extracts just the relevant bits of its
`mise.toml` (e.g. one tool's pinned version plus its `[hooks]` block) and names
the paths to cache under that key. No-ops when `extra-cache-key-cmd` is empty.
`.github/workflows/hk.yml` exposes the same two inputs for its own inlined
checkout+mise step.

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

## Inputs

<!-- AUTO-DOC-INPUT:START - Do not remove or modify this section -->

|        INPUT        | REQUIRED | DEFAULT |                                  DESCRIPTION                                   |
|---------------------|----------|---------|--------------------------------------------------------------------------------|
|    apt-packages     |  false   |         |             Space-separated apt packages the job needs (cached)               |
| extra-cache-key-cmd |  false   |         | Shell command whose stdout determines an extra cache key. Leave empty to skip. |
|  extra-cache-paths  |  false   |         |                Newline-separated paths to cache under that key.                |
|     fetch-depth     |  false   |  `"1"`  |                 Number of commits to fetch (0 = full history)                  |

<!-- AUTO-DOC-INPUT:END -->

## Outputs

<!-- AUTO-DOC-OUTPUT:START - Do not remove or modify this section -->
No outputs.
<!-- AUTO-DOC-OUTPUT:END -->
