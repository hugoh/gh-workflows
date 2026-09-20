# `automerge-keep-fresh`

Updates branches for open auto-merge pull requests that are behind the
repository's default branch.

## Usage

```yaml
steps:
  - uses: hugoh/gh-workflows/automerge-keep-fresh@<pinned-sha>
    with:
      pull-request-number: ${{ github.event.pull_request.number }}
    env:
      GH_TOKEN: ${{ github.token }}
      GH_REPO: ${{ github.repository }}
```

## Inputs

<!-- AUTO-DOC-INPUT:START - Do not remove or modify this section -->

|        INPUT        | REQUIRED | DEFAULT |                       DESCRIPTION                        |
|---------------------|----------|---------|----------------------------------------------------------|
| pull-request-number |  false   |         | PR number to inspect instead of reconciling all open PRs |

<!-- AUTO-DOC-INPUT:END -->
