# `.github/workflows/automerge-keep-fresh.yml`

`workflow_call` wrapper that finds open PRs with auto-merge enabled whose
`mergeStateStatus` is `BEHIND` and calls the GitHub `update-branch` API on each.
When a PR number is supplied, it only inspects that PR. Without one, it retries
the repository-wide reconciliation for up to one minute to account for delayed
merge-status updates.

## Usage

```yaml
name: keep-automerge-fresh
on:
  push:
    branches: [main]
permissions: {}
jobs:
  update-behind-prs:
    uses: hugoh/gh-workflows/.github/workflows/automerge-keep-fresh.yml@<pinned-sha>
    with:
      pull-request-number: ${{ github.event.pull_request.number }}
    permissions:
      contents: write
      pull-requests: write
```

## Inputs

<!-- AUTO-DOC-INPUT:START - Do not remove or modify this section -->

|        INPUT        | REQUIRED | DEFAULT |                       DESCRIPTION                        |
|---------------------|----------|---------|----------------------------------------------------------|
| pull-request-number |  false   |         | PR number to inspect instead of reconciling all open PRs |

<!-- AUTO-DOC-INPUT:END -->

## Outputs

<!-- AUTO-DOC-OUTPUT:START - Do not remove or modify this section -->
No outputs.
<!-- AUTO-DOC-OUTPUT:END -->
