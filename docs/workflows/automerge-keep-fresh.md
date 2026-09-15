# `.github/workflows/automerge-keep-fresh.yml`

`workflow_call` wrapper that, on push to the default branch, finds open PRs
with auto-merge enabled whose `mergeStateStatus` is `BEHIND` and calls the
GitHub `update-branch` API on each. Fixes the case where a PR has auto-merge
enabled and all checks pass, but branch protection's "require branches up to
date" leaves it stuck — GitHub doesn't update stale branches on its own.

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
    permissions:
      contents: write
      pull-requests: write
```

## Inputs

<!-- AUTO-DOC-INPUT:START - Do not remove or modify this section -->
No inputs.
<!-- AUTO-DOC-INPUT:END -->

## Outputs

<!-- AUTO-DOC-OUTPUT:START - Do not remove or modify this section -->
No outputs.
<!-- AUTO-DOC-OUTPUT:END -->
