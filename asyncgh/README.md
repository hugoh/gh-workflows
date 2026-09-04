# asyncgh

[![PyPI](https://img.shields.io/pypi/v/asyncgh)](https://pypi.org/project/asyncgh/)

A minimal async GitHub API transport — the plumbing an account-wide config
tool needs before any of its domain logic: authentication, a connection,
retrying error handling, REST pagination, and GraphQL.

`GitHubClient` is the actual client. For a script that only ever talks to
one account, module-level functions (`api_json`, `graphql`, `fetch_repos`,
...) wrap one shared default instance, so there's nothing to construct --
they read the token `gh auth login` already stored and go. Construct a
`GitHubClient` directly to talk to a second account in the same process, use
your own retry budget, or as an async context manager.

## How this compares

| Library | Async | Shape |
|---|---|---|
| [PyGithub](https://github.com/PyGithub/PyGithub) | no | typed objects |
| [githubkit](https://github.com/yanyongyu/githubkit) | yes | Pydantic models, generated from GitHub's OpenAPI spec |
| [gidgethub](https://github.com/gidgethub/gidgethub) | yes | sans-I/O, bring-your-own HTTP client |
| [ghapi](https://ghapi.fast.ai) (fast.ai) | no | OpenAPI-generated, dynamic attribute access |
| **asyncgh** | yes | plain `dict`s, ~550 lines |

`githubkit` gives you typed responses and full API coverage, at the cost of
codegen'd models and a `pydantic` dependency. `gidgethub` is closest in
spirit -- sans-I/O, no models -- but leaves auth, retry, and the client
itself to the caller. `asyncgh` picks a specific point in that space: an
async client with almost no code between you and the wire, no generated
models or schema (you read GitHub's own docs and index the JSON), retry and
GraphQL built in rather than bolted on.

## API

Full API reference, generated from the docstrings:
[hugoh.github.io/gh-workflows/asyncgh](https://hugoh.github.io/gh-workflows/asyncgh/)
(rebuilt on every push that touches this package -- see
`.github/workflows/docs.yml`).

## Auth

`Authorization: Bearer $(gh auth token)`, resolved lazily on the first
request and cached for the client's lifetime. `gh` must be installed and
logged in -- or pass `GitHubClient(token=...)` to skip the `gh` dependency
entirely and use your own.

## Retries

`api_raw` retries transport errors (DNS, timeout, reset) and transient
statuses — 429, 500, 502, 503, 504 — via [`stamina`](https://stamina.hynek.me).
A `Retry-After` header sets the exact wait; otherwise exponential backoff with
jitter. `max_retries` (default from `GH_MAX_RETRIES`, else 3) caps the
retries per `GitHubClient` instance; there is no wall-clock timeout, so a
minute-long `Retry-After` is honoured in full. When the retries are spent the
last response is returned unchanged for the caller to judge.

`graphql()` retries the same cases (transport errors, 429/5xx) plus one more,
in the same single retry loop: GitHub answers GraphQL rate limiting with HTTP
200 and an `errors[].type == "RATE_LIMITED"` body, invisible to a
status-based retry. Any other GraphQL error (bad query, not found) is not
transient and raises immediately, with the failing error's `type` on
`GhError.error_type`.

## Consumers

`repo-admin/` (in this repo) uses it for every account-wide GitHub command --
`repo_admin.py`'s `sync` subcommands and `activity.py` via `api_raw`/
`api_json`/pagination; `digest.py` (wrapped by `../digest-action/`) is the
one driving `graphql()`.
