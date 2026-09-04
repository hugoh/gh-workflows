# digest-action

Builds — and optionally emails — an HTML digest of a GitHub account's repo
activity: still-open PRs and issues, releases, and recently closed PRs and
issues. Wraps [`repo-admin/digest.py`](../repo-admin/README.md), fetching via
GraphQL in batches of 10 repos per query.

Self-contained: installs its own pinned `uv` ([`astral-sh/setup-uv`](https://github.com/astral-sh/setup-uv)),
so it doesn't need `setup` run first or the caller's `mise.toml`.

## Inputs

| Input | Required | Default | Purpose |
|---|---|---|---|
| `owner` | yes | — | GitHub account/org to report on |
| `github-token` | yes | — | Token `gh` (and so `gh auth token`) should use — needs read access to every targeted repo, and to list repos for `owner` account-wide, which `GITHUB_TOKEN` cannot do outside its own repo. Passed through as `GH_TOKEN`. |
| `only` | no | *(all)* | Comma-separated repo names to limit to |
| `skip` | no | *(none)* | Comma-separated repo names to exclude |
| `open-days` | no | `365` | How far back to look for still-open PRs/issues |
| `closed-days` | no | `7` | How far back to look for closed PRs/issues |
| `release-days` | no | `7` | How far back to look for published releases |
| `out` | no | *(none)* | Also write the rendered HTML to this path (relative to the runner's workspace) — sets the `html` output. Required if `send-email` is `false`, since otherwise the digest is built and immediately discarded. |
| `send-email` | no | `true` | Whether to email the digest — requires the `smtp-*`/`digest-*-email` inputs below |
| `smtp-host` / `smtp-port` / `smtp-username` / `smtp-password` | when `send-email` | — | SMTP relay settings |
| `digest-from-email` / `digest-to-email` | when `send-email` | — | Envelope From / recipient |
| `uv-version` | no | `0.12.7` | `uv` version to install |

SMTP settings are passed as **inputs**, not job-level `env:` — `ghalint`'s
`job_secrets` policy flags job-level env holding secrets as over-broad
exposure to every step in the job, since composite-action steps don't see
env set on the calling step, only on the job.

## Outputs

| Output | Set when | Value |
|---|---|---|
| `html` | `out` is given | the `out` path |

## Usage

```yaml
jobs:
  digest:
    runs-on: ubuntu-latest
    steps:
      - uses: hugoh/gh-workflows/digest-action@<pinned-sha>
        with:
          owner: my-org
          github-token: ${{ secrets.DIGEST_PAT }}
          smtp-host: ${{ secrets.SMTP_HOST }}
          smtp-port: ${{ secrets.SMTP_PORT }}
          smtp-username: ${{ secrets.SMTP_USERNAME }}
          smtp-password: ${{ secrets.SMTP_PASSWORD }}
          digest-from-email: ${{ secrets.DIGEST_FROM_EMAIL }}
          digest-to-email: ${{ secrets.DIGEST_TO_EMAIL }}
```

Rendering only, no email (e.g. to upload as a workflow artifact instead):

```yaml
steps:
  - uses: hugoh/gh-workflows/digest-action@<pinned-sha>
    with:
      owner: my-org
      github-token: ${{ secrets.DIGEST_PAT }}
      send-email: "false"
      out: digest.html
  - uses: actions/upload-artifact@<pinned-sha>
    with:
      name: digest
      path: digest.html
```

## See also

This repo's own weekly digest, `.github/workflows/digest.yml`, dogfoods this
action via the local-path form (`uses: ./digest-action`) rather than a
remote tag, matching the rest of this repo's own CI.
