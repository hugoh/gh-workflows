"""GitHub REST + GraphQL transport: auth, the client, and the request /
pagination helpers every endpoint wrapper is built on.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from typing import Any, Self

import httpx2
import stamina

API_BASE = "https://api.github.com"

# Retry transport failures and transient statuses (429 + 5xx). `attempts` is
# total tries, so max_retries=3 means one call plus three retries. There is
# no wall-clock timeout: GitHub's Retry-After on a secondary rate limit is
# often a minute or more, and honouring it is the whole point.
DEFAULT_MAX_RETRIES = 3
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
RETRY_WAIT_INITIAL = 1.0
RETRY_WAIT_MAX = 60.0
RETRY_WAIT_JITTER = 1.0


class GhError(RuntimeError):
    """A GitHub API call -- or a caller's worker function -- failed
    unexpectedly.

    status_code is set for HTTP errors raised by api_json(), so callers can
    branch on the real status code (e.g. 403 vs 404) instead of
    string-matching an error message. error_type is its GraphQL equivalent,
    set from GitHub's own `errors[].type` (e.g. "NOT_FOUND", "FORBIDDEN")
    when graphql() raises. Plain RuntimeError rather than a
    reconcilekit.ReconcileError subclass -- asyncgh has no dependency on
    reconcilekit, so it stays installable standalone; reconcilekit's own
    run_parallel(..., error_cls=) only requires type[Exception], so GhError
    still works there unchanged.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_type: str | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type


def _auth_token() -> str:
    """Reads the token `gh` already has -- keychain storage, SSO, and 2FA are
    already solved by `gh auth login`, so this reuses that instead of
    managing a separate credential.
    """
    result = subprocess.run(
        ["gh", "auth", "token"], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise GhError(f"gh auth token failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _default_max_retries() -> int:
    raw = os.environ.get("GH_MAX_RETRIES")
    if raw is None:
        return DEFAULT_MAX_RETRIES
    try:
        return int(raw)
    except ValueError as exc:
        raise GhError(f"GH_MAX_RETRIES must be an integer, got {raw!r}") from exc


def _should_retry(exc: Exception) -> bool | float:
    """stamina backoff hook. A `Retry-After` header (GitHub sends one on
    secondary rate limits) sets the exact wait; otherwise transport errors
    and RETRY_STATUSES responses retry on stamina's default backoff, and
    everything else propagates.
    """
    response = getattr(exc, "response", None)
    if response is not None:
        header = response.headers.get("retry-after")
        if header:
            try:
                return float(header)
            except ValueError:
                pass
        return response.status_code in RETRY_STATUSES
    return isinstance(exc, httpx2.TransportError)


class _GraphQLRateLimited(Exception):
    """Internal marker: GraphQL answered 200 with errors[].type ==
    RATE_LIMITED, which the transport/5xx retry predicate never sees since
    it only looks at exceptions and HTTP status. Retried via this instead.
    """


def _should_retry_graphql(exc: Exception) -> bool | float:
    if isinstance(exc, _GraphQLRateLimited):
        return True
    return _should_retry(exc)


def error_message(response: httpx2.Response) -> str:
    """Extracts GitHub's own `message` field from an error response body,
    falling back to the raw response text if the body isn't JSON, or isn't
    a JSON object (e.g. a bare array or string, which `.get()` can't handle).
    """
    try:
        body = response.json()
    except ValueError:
        return response.text
    if not isinstance(body, dict):
        return response.text
    return body.get("message", response.text)


class GitHubClient:
    """One GitHub REST + GraphQL client: auth, connection, retry, and the
    request / pagination helpers every endpoint wrapper is built on.

    `token` defaults to the token `gh auth login` already holds, resolved
    lazily on the first request (so constructing a client never itself
    shells out). Pass an explicit token to talk to a second account, or to
    avoid the `gh` subprocess dependency entirely. `max_retries` defaults to
    `GH_MAX_RETRIES` (env, default 3); pass it explicitly to override
    per-instance rather than per-process.

    Also usable as an async context manager (`async with GitHubClient() as
    gh: ...`), which closes the underlying connection on exit.
    """

    def __init__(
        self, *, token: str | None = None, max_retries: int | None = None
    ) -> None:
        self._token = token
        self._max_retries = (
            max_retries if max_retries is not None else _default_max_retries()
        )
        self._http: httpx2.AsyncClient | None = None
        self._lock = asyncio.Lock()

    async def _get_http(self) -> httpx2.AsyncClient:
        if self._http is None:
            async with self._lock:
                if self._http is None:
                    token = self._token if self._token is not None else _auth_token()
                    self._http = httpx2.AsyncClient(
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Accept": "application/vnd.github+json",
                            "X-GitHub-Api-Version": "2022-11-28",
                        },
                        timeout=30,
                    )
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def _do_request(
        self, method: str, path: str, *, json: Any = None, params: dict | None = None
    ) -> httpx2.Response:
        """One bare HTTP call, no retry -- the shared primitive api_raw()
        and graphql() each wrap in their own single retry loop, rather than
        one calling the other and compounding two loops into one.
        """
        url = path if path.startswith("http") else f"{API_BASE}{path}"
        http = await self._get_http()
        try:
            return await http.request(method, url, json=json, params=params)
        except httpx2.TransportError:
            raise
        except httpx2.HTTPError as exc:
            raise GhError(str(exc)) from exc

    async def api_raw(
        self, method: str, path: str, *, json: Any = None, params: dict | None = None
    ) -> httpx2.Response:
        """Makes one GitHub REST API call and returns the raw Response --
        callers decide what a given status means for their endpoint (e.g. a
        404 means "feature disabled" for vulnerability-alerts but "not
        found" everywhere else). Raises GhError only for genuine transport
        failures (DNS, timeout, connection reset); HTTP error statuses are
        returned, not raised.

        Transport errors and transient statuses (429, 5xx) are retried up to
        max_retries times, honouring `Retry-After`. GitHub's writes here are
        idempotent (secret PUTs replace), so retrying any method is safe.
        """
        try:
            async for attempt in stamina.retry_context(
                on=_should_retry,
                attempts=self._max_retries + 1,
                timeout=None,
                wait_initial=RETRY_WAIT_INITIAL,
                wait_max=RETRY_WAIT_MAX,
                wait_jitter=RETRY_WAIT_JITTER,
            ):
                with attempt:
                    response = await self._do_request(
                        method, path, json=json, params=params
                    )
                    if response.status_code in RETRY_STATUSES:
                        response.raise_for_status()
                    return response
        except httpx2.HTTPStatusError as exc:
            return (
                exc.response
            )  # retryable status, retries spent -- let the caller judge
        except httpx2.TransportError as exc:
            raise GhError(str(exc)) from exc
        raise GhError("api_raw retry loop exited without a response")  # unreachable

    async def api_json(
        self, method: str, path: str, *, json: Any = None, params: dict | None = None
    ) -> dict:
        """Like api_raw, but raises GhError (with status_code and GitHub's
        own error message) on any non-2xx response, and returns the parsed
        JSON body -- or {} for a body-less response like 204 No Content --
        on success.
        """
        response = await self.api_raw(method, path, json=json, params=params)
        if not response.is_success:
            raise GhError(error_message(response), status_code=response.status_code)
        return response.json() if response.content else {}

    async def graphql(self, query: str, variables: dict | None = None) -> dict:
        """Runs one GraphQL query and returns its `data` object.

        GraphQL always answers HTTP 200, even for query errors, so success
        lives in the body: `errors` present (with `data` null or partial)
        raises GhError with the joined messages and the first error's
        `type` as `error_type` -- callers have no partial-data story, so
        partial loss is worse than a loud failure. RATE_LIMITED errors
        retry (same backoff as api_raw) since they're the one transient
        GraphQL failure mode; other GraphQL errors (bad query, not found)
        are not transient and raise immediately. One retry loop covers
        transport errors, retryable HTTP statuses, and RATE_LIMITED
        together -- calling api_raw here (which retries on its own) would
        compound two retry loops into up to max_retries**2 real requests.
        """
        try:
            async for attempt in stamina.retry_context(
                on=_should_retry_graphql,
                attempts=self._max_retries + 1,
                timeout=None,
                wait_initial=RETRY_WAIT_INITIAL,
                wait_max=RETRY_WAIT_MAX,
                wait_jitter=RETRY_WAIT_JITTER,
            ):
                with attempt:
                    response = await self._do_request(
                        "POST",
                        "/graphql",
                        json={"query": query, "variables": variables or {}},
                    )
                    if response.status_code in RETRY_STATUSES:
                        response.raise_for_status()
                    if not response.is_success:
                        raise GhError(
                            error_message(response), status_code=response.status_code
                        )
                    body = response.json()
                    errors = body.get("errors")
                    if not errors:
                        return body["data"]
                    if any(error.get("type") == "RATE_LIMITED" for error in errors):
                        raise _GraphQLRateLimited(
                            "; ".join(
                                error.get("message", str(error)) for error in errors
                            )
                        )
                    raise GhError(
                        "; ".join(error.get("message", str(error)) for error in errors),
                        error_type=errors[0].get("type"),
                    )
        except _GraphQLRateLimited as exc:
            raise GhError(str(exc)) from exc
        except httpx2.HTTPStatusError as exc:
            raise GhError(
                error_message(exc.response), status_code=exc.response.status_code
            ) from exc
        except httpx2.TransportError as exc:
            raise GhError(str(exc)) from exc
        raise GhError("graphql retry loop exited without a response")  # unreachable

    async def paginated(
        self, method: str, path: str, *, params: dict | None = None
    ) -> list[dict]:
        """Follows GitHub's `Link: rel="next"` header, concatenating every
        page's JSON array into one list.
        """
        items: list[dict] = []
        url = path
        query = params
        while url:
            response = await self.api_raw(method, url, params=query)
            if not response.is_success:
                raise GhError(error_message(response), status_code=response.status_code)
            items.extend(response.json())
            url = response.links.get("next", {}).get("url")
            query = None  # the "next" link already carries the full query string
        return items


_default_client: GitHubClient | None = None


def _get_default_client() -> GitHubClient:
    # One shared default GitHubClient rather than requiring every script to
    # construct one -- gh auth token only needs to be paid once per process,
    # and most callers only ever talk to one account.
    global _default_client
    if _default_client is None:
        _default_client = GitHubClient()
    return _default_client


async def aclose_client() -> None:
    """Closes the default client and clears it, so the next request opens a
    fresh one. Callers should call this once at shutdown; `lib.run_cli` (in
    repo-admin) does this in a `finally` so it always runs. Only affects the
    module-level default client -- a GitHubClient constructed directly has
    its own `aclose()` / `async with`.
    """
    global _default_client
    if _default_client is not None:
        await _default_client.aclose()
        _default_client = None


async def api_raw(
    method: str, path: str, *, json: Any = None, params: dict | None = None
) -> httpx2.Response:
    return await _get_default_client().api_raw(method, path, json=json, params=params)


async def api_json(
    method: str, path: str, *, json: Any = None, params: dict | None = None
) -> dict:
    return await _get_default_client().api_json(method, path, json=json, params=params)


async def graphql(query: str, variables: dict | None = None) -> dict:
    return await _get_default_client().graphql(query, variables)


async def paginated(
    method: str, path: str, *, params: dict | None = None
) -> list[dict]:
    return await _get_default_client().paginated(method, path, params=params)
