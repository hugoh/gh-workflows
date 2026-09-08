# trim-releases

Deletes old GitHub releases with `gh`: keeps the newest `keep-full` full
releases and the newest `keep-prereleases` prereleases, and drops those kept
prereleases once they pass `prerelease-cutoff-days`. Set `dry-run: true` to
only print.

Needs `gh` on `PATH` (default on GitHub runners) and a `contents: write` token
(`github-token`, defaults to `github.token`); no checkout required. Callers
keep their own `schedule` / `workflow_dispatch` triggers.

## Usage

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

## Inputs

<!-- AUTO-DOC-INPUT:START - Do not remove or modify this section -->

|         INPUT          | REQUIRED |         DEFAULT         |                      DESCRIPTION                       |
|------------------------|----------|-------------------------|--------------------------------------------------------|
|        dry-run         |  false   |        `"false"`        |            Only print what would be deleted            |
|      github-token      |  false   | `"${{ github.token }}"` |     Token with contents:write for the target repo      |
|       keep-full        |  false   |         `"10"`          |         Number of newest full releases to keep         |
|    keep-prereleases    |  false   |          `"3"`          |          Number of newest prereleases to keep          |
| prerelease-cutoff-days |  false   |         `"14"`          | Delete kept prereleases once older than this many days |

<!-- AUTO-DOC-INPUT:END -->
