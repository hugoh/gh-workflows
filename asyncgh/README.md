# asyncgh

A minimal async GitHub API transport — the plumbing an account-wide config
tool needs before any of its domain logic: authentication, a shared
connection, retrying error handling, REST pagination, and GraphQL.

There is no client object to construct and no config. It reads the token
`gh auth login` already stored, keeps one process-wide `httpx2.AsyncClient`,
and exposes plain functions.

## Why not PyGithub / githubkit / gidgethub / fast.ai's `ghapi`?

| Library | Async | Shape | Trade-off vs `asyncgh` |
|---|---|---|---|
| [PyGithub](https://github.com/PyGithub/PyGithub) | no | typed objects | sync-only — a poor fit for anything doing concurrent, fan-out API calls |
| [githubkit](https://github.com/yanyongyu/githubkit) | yes | Pydantic models, generated from GitHub's OpenAPI spec | full, typed surface, but pulls in codegen'd models and `pydantic`; heavier than most scripts need |
| [gidgethub](https://github.com/gidgethub/gidgethub) | yes | sans-I/O, bring-your-own HTTP client | closest in spirit, but leaves auth, retry, and the client itself to the caller |
| [ghapi](https://ghapi.fast.ai) (fast.ai) | no | OpenAPI-generated, dynamic attribute access | lightweight like this package, but sync-only |
| **asyncgh** | yes | plain `dict`s, ~200 lines | no generated models, no schema — you read GitHub's own docs and index the JSON; retry and GraphQL are built in, not bolted on |

If you want typed responses and full API coverage, use `githubkit`. If you
want an async client with almost no code between you and the wire — a script
firing dozens of concurrent calls that reads a handful of fields per
response — that's what this is for.

## API

Full API reference, generated from the docstrings:
[hugoh.github.io/gh-workflows/asyncgh](https://hugoh.github.io/gh-workflows/asyncgh/)
(rebuilt on every push that touches this package -- see
`.github/workflows/docs.yml`).

## Auth

`Authorization: Bearer $(gh auth token)`, resolved lazily on the first
request and cached for the process. `gh` must be installed and logged in.

## Retries

`api_request` retries transport errors (DNS, timeout, reset) and transient
statuses — 429, 500, 502, 503, 504 — via [`stamina`](https://stamina.hynek.me).
A `Retry-After` header sets the exact wait; otherwise exponential backoff with
jitter. `GH_MAX_RETRIES` (default 3) caps the retries; there is no wall-clock
timeout, so a minute-long `Retry-After` is honoured in full. When the retries
are spent the last response is returned unchanged for the caller to judge.

`graphql()` reuses that same retry for transport errors and 429/5xx, plus one
addition: GitHub answers GraphQL rate limiting with HTTP 200 and an
`errors[].type == "RATE_LIMITED"` body, which `api_request`'s status-based
retry never sees -- `graphql()` retries that case itself, on the same
backoff. Any other GraphQL error (bad query, not found) is not transient and
raises immediately.

## Consumers

`repo-admin/` (in this repo) uses it for every account-wide GitHub command --
`repo_admin.py`'s `sync` subcommands and `activity.py` via `api_request`/
`api_json`/pagination; `digest.py` (wrapped by `../digest-action/`) is the
one driving `graphql()`.
