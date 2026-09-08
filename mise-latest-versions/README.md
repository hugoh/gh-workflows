# mise-latest-versions

Emits a `matrix` output: a JSON array of the newest N version series of a mise
tool, from `mise ls-remote <tool>`, for a version-compatibility test matrix.
Needs `mise` on `PATH` — run [`setup`](../setup/) first.

- `level: minor` → `["0.42","0.43","0.44"]`
- `level: major, count: 2` → `["1","2"]`

`go-tools`' `go-tool-compat.yml` reusable workflow wraps this.

## Usage

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

## Inputs

<!-- AUTO-DOC-INPUT:START - Do not remove or modify this section -->

| INPUT | REQUIRED |  DEFAULT  |                DESCRIPTION                 |
|-------|----------|-----------|--------------------------------------------|
| count |  false   |   `"3"`   |   how many of the newest series to emit    |
| level |  false   | `"minor"` | series granularity: major, minor, or patch |
| tool  |   true   |           |  mise tool name (e.g. jj, go, terraform)   |

<!-- AUTO-DOC-INPUT:END -->

## Outputs

<!-- AUTO-DOC-OUTPUT:START - Do not remove or modify this section -->

| OUTPUT |                   DESCRIPTION                   |
|--------|-------------------------------------------------|
| matrix | JSON array of version-series strings, ascending |

<!-- AUTO-DOC-OUTPUT:END -->
