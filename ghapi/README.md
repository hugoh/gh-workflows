# ghapi

A minimal async GitHub REST API transport — the plumbing an account-wide
config tool needs before any of its domain logic: authentication, a shared
connection, error handling, and pagination.

There is no client object to construct and no config. It reads the token
`gh auth login` already stored, keeps one process-wide `httpx2.AsyncClient`,
and exposes plain functions.

## API

| Symbol | Purpose |
|---|---|
| `api_request(method, path, *, json=None, params=None)` | one call, raw `Response`; retries transport errors and 429/5xx (see below); raises `GhError` only on transport failure, never on final HTTP status |
| `api_json(method, path, *, json=None, params=None)` | same, but raises `GhError` (with `status_code`) on any non-2xx and returns the parsed body (`{}` for 204) |
| `paginated(method, path, *, params=None)` | follows `Link: rel="next"`, concatenating every page's JSON array |
| `error_message(response)` | GitHub's own `message` field, falling back to raw text |
| `fetch_repos_json(owner)` / `public_repos_json(owner)` | account-wide repo listing (with / without private repos) |
| `encrypt_secret_value(public_key_b64, value)` / `set_repo_secret(owner, repo, name, value)` | Actions secrets via GitHub's libsodium sealed-box scheme |
| `aclose_client()` | close the shared client (call once at shutdown) |
| `API_BASE` | `https://api.github.com` |
| `GhError` | the failure type, a `reconcilekit.ReconcileError` subclass so `run_parallel` can collect it |

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

## Consumers

`repo-admin/` (in this repo) uses it for every account-wide GitHub command.
It is not published to PyPI — it is consumed as a uv workspace member.
