# repo-admin

Bulk-applies account-wide repo settings across all of hugoh's non-archived
repos, via the GitHub REST API (authenticated through `gh auth token`, so it
reuses `gh`'s existing login rather than managing a separate credential). A
single Python CLI (`repo_admin.py`, run through `uv`) with `<resource> <verb>`
subcommands, `gh`/`aws`/`docker`-style; repos are processed in parallel
(`GH_JOBS`, default 6). Config data lives under `repo-admin/config/`, separate
from the `.py` source. The stateless fetch-diff-apply reconcile loop each
`sync` command runs on (`run_reconcile` / `run_parallel` / `Status`) lives in
[`reconcilekit/`](../reconcilekit/README.md), a domain-agnostic uv-workspace
package; the GitHub REST + GraphQL transport (`api_json` / `graphql` /
pagination / auth via `gh`) lives in a second one,
[`asyncgh/`](../asyncgh/README.md). Forks are excluded by default — except
those listed in `config/include-forks.txt`; edit that file to add more, or
override per-run with `GH_INCLUDE_FORKS` (comma-separated). Every subcommand
accepts trailing repo names to scope to a subset (default: every repo) and
`--skip name1,name2` to exclude instead; `GH_OWNER` overrides the default
owner.

```text
cd repo-admin
uv run repo_admin.py <resource> <verb> [repo ...] \
  [--dry-run] [--verbose] [--skip name1,name2]
```

- **`repos list`** — lists repos as a table: name, default branch, private,
  fork
- **`merge sync [--dry-run]`** — enables auto-merge,
  delete-branch-on-merge, and PR-branch auto-update (the last one matters
  because branch protection requires PR branches to be up to date before
  merging; without auto-update, auto-merge PRs stall needing a manual
  "Update branch" click)
- **`protection sync [--dry-run] [--clear-stale-checks]`** — requires
  status checks to pass and a
  PR (0 approvals needed, no direct pushes) before merging, matching the
  convention `go-tools`' `mise run gh-repo-setup` already established.
  Required contexts are detected from the most recent pull request's check
  runs (not the default branch tip — see `repo_admin.py`'s header comment
  for why), limited to jobs from the repo's own `.github/workflows/` files:
  third-party apps and GitHub-managed setups (CodeQL default setup's
  "Analyze (...)", the "github-advanced-security" check) never become merge
  gates. `--dry-run` diffs each repo's current protection against the
  baseline and prints "unchanged: ..." or "would update", rather than just
  showing what would be required. A repo that already requires checks but
  yields none to sample (the latest PR's workflow runs aged out, a stale
  pre-CI PR bumped to the top by a comment) keeps its existing contexts
  instead of having the merge gate cleared; `--clear-stale-checks` drops
  them for a repo that genuinely retired its CI. Private repos on a plan without
  branch-protection access are reported, not failed. Repos listed in
  `config/branch-protection-exclude.txt` (e.g. `homebrew-tap`, which has no
  CI/PR workflow) are always skipped; override per-run with
  `GH_BRANCH_PROTECTION_EXCLUDE`.
- **`security sync [--dry-run]`** — enables Dependabot vulnerability
  alerts (all repos, free), plus secret scanning, secret scanning push
  protection, Dependabot security updates, private vulnerability
  reporting, and CodeQL code scanning default setup (public repos only —
  private repos need GitHub Advanced Security, a paid add-on this
  account's plan doesn't include; such repos are reported as unavailable,
  not failed, as are repos with no CodeQL-supported language).
- **`sync [--dry-run]`** — runs `merge sync`, `protection sync`, then
  `security sync` in sequence. One command failing doesn't stop the
  others; the exit code is nonzero if any of them failed.
- **`pages sync [--dry-run]`** — sets each repo's GitHub Pages custom
  domain from the repo → domain mapping in `config/pages-domains.yaml` (the
  same file `iac/cloudflare`'s OpenTofu config reads to generate the
  matching DNS records), and points the repo's homepage URL at
  `https://<domain>` so the "website" link tracks the custom domain. Only
  repos listed in the mapping are touched —
  trailing repo names narrow that set further rather than expanding it, and
  error if given a repo not in the mapping. `https_enforced` is only ever
  turned on, and only once GitHub reports the domain's certificate as
  issued; a freshly-set domain needs a later rerun to pick that up once
  DNS/cert issuance catches up.
- **`pages status`** — read-only: lists every repo with GitHub Pages enabled
  and its current custom domain/HTTPS state and homepage URL, flagging any
  that aren't yet in `config/pages-domains.yaml`.
- **`pages config --domain <domain>`** — prints
  `config/pages-domains.yaml`-formatted entries to stdout: `<repo>.<domain>`
  per repo, dots in the repo name replaced with dashes (e.g.
  `AudioPilot.spoon` → `AudioPilot-spoon.<domain>`, since a raw dot would
  split the hostname into an extra DNS label). Given repo names, generates
  for exactly those with no API calls; with none, auto-discovers repos with
  Pages enabled but missing from `config/pages-domains.yaml` (the same set
  `pages status` flags) and suggests entries for those.
- **`secrets sync [--dry-run] [--secret name1,name2]`** — pushes shared
  GitHub Actions secrets (name → target-repo list in `config/secrets.yaml`,
  values sops-encrypted in `config/secrets.enc.yaml`) to each configured
  repo via GitHub's REST API, encrypting each value in-process with PyNaCl
  for the target repo's public key — the plaintext value is never written
  to disk or passed as a subprocess argument. Trailing repo names / `--skip`
  filter repo names *within* each secret's configured repo list, same as
  every other command; `--secret` narrows which secret names from
  `config/secrets.yaml` to sync (defaults to all of them). GitHub's API
  never returns a secret's existing value, so there's no unchanged/changed
  detection — `--dry-run` just reports which repos would receive each
  secret. Decrypting `config/secrets.enc.yaml` requires `sops` on `PATH`
  with access to the shared age key; encrypt a new/updated value yourself,
  e.g.:

  ```text
  cd repo-admin
  sops --encrypt --input-type yaml --output-type yaml \
    <(echo "TAP_GITHUB_TOKEN: <value>") > config/secrets.enc.yaml
  ```

- **`secrets edit`** — opens `config/secrets.enc.yaml` in `sops` for
  interactive editing (decrypts to `$EDITOR`, re-encrypts on save) — an
  alternative to the `sops --encrypt` one-liner above. The first time, seeds
  the file pre-populated with every `config/secrets.yaml` key (empty values)
  so there's something to fill in instead of hand-writing sops' metadata
  block. After editing, warns (doesn't fail) about drift against
  `config/secrets.yaml`: a configured secret left with no value, or a value
  left over from a removed/renamed secret.
- **`activity`** — reports recent commit activity per repo, ranked by an
  exponential recency-decay score. See `activity.py`'s header comment.

Run a mutating command with `--dry-run` first and review the output before
applying; by default only changed/failed repos print live, with unchanged
ones counted in a summary line — pass `--verbose` to see every repo.

## `digest.py` — activity digest

Builds an HTML digest of an account's repo activity — still-open PRs and
issues, releases, and recently closed PRs and issues — fetched via GraphQL,
and optionally emails it through an SMTP relay. `email_delivery.py` (`gh
auth token`, `send_email` / `send_email_from_env`) is split out from the
fetch/render logic so delivery can vary independently — that split is what
makes [`../digest-action/`](../digest-action/README.md), the reusable GitHub
Action wrapping this script, possible.

```text
cd repo-admin
uv run digest.py [repo ...] [--skip name1,name2] [--open-days 365]
  [--release-days 7] [--closed-days 7] [--out FILE] [--no-send]
```

Same trailing-repo-names / `--skip` scoping as `repo_admin.py`. Reads SMTP
settings and the recipient from the environment: `SMTP_HOST`, `SMTP_PORT`,
`SMTP_USERNAME`, `SMTP_PASSWORD`, `DIGEST_FROM_EMAIL`, `DIGEST_TO_EMAIL`.

## Tests

`mise run test` (all workspace packages), or `cd repo-admin && uv run pytest`.
