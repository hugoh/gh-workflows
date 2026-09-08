# `.github/workflows/secret-scan.yml`

`workflow_call` TruffleHog OSS secret scan: diffs on push/PR, full history on
`schedule` / `workflow_dispatch`, reports only verified findings. Complements
the gitleaks step `hk check` already runs by verifying findings against the
live provider and sweeping history that predates gitleaks adoption.

## Usage

```yaml
name: secret-scan
on:
  push:
    branches: [main]
  pull_request:
  schedule:
    - cron: "11 4 * * 1"
  workflow_dispatch:
permissions: {}
jobs:
  secret-scan:
    uses: hugoh/gh-workflows/.github/workflows/secret-scan.yml@<pinned-sha>
```

## Inputs

<!-- AUTO-DOC-INPUT:START - Do not remove or modify this section -->
No inputs.
<!-- AUTO-DOC-INPUT:END -->

## Outputs

<!-- AUTO-DOC-OUTPUT:START - Do not remove or modify this section -->
No outputs.
<!-- AUTO-DOC-OUTPUT:END -->
