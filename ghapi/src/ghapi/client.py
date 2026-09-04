"""GitHub REST API transport: auth, the shared async client, and the
request / pagination helpers every endpoint wrapper is built on.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from typing import Any

import httpx2
import stamina

from reconcilekit import ReconcileError

API_BASE = "https://api.github.com"

# Retry transport failures and transient statuses (429 + 5xx). `attempts` is
# total tries, so MAX_RETRIES=3 means one call plus three retries. There is no
# wall-clock timeout: GitHub's Retry-After on a secondary rate limit is often
# a minute or more, and honouring it is the whole point.
MAX_RETRIES = int(os.environ.get("GH_MAX_RETRIES", "3"))
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
RETRY_WAIT_INITIAL = 1.0
RETRY_WAIT_MAX = 60.0
RETRY_WAIT_JITTER = 1.0


class GhError(ReconcileError):
    """A GitHub API call -- or a caller's worker function -- failed
    unexpectedly.

    status_code is set for HTTP errors raised by api_json(), so callers can
    branch on the real status code (e.g. 403 vs 404) instead of
    string-matching an error message.
    """

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


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


_client: httpx2.AsyncClient | None = None
_client_lock = asyncio.Lock()


async def _get_client() -> httpx2.AsyncClient:
    # One shared AsyncClient rather than one per thread: there's no thread
    # pool, and gh auth token only needs to be paid once per process.
    global _client
    if _client is None:
        async with _client_lock:
            if _client is None:
                _client = httpx2.AsyncClient(
                    headers={
                        "Authorization": f"Bearer {_auth_token()}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                    timeout=30,
                )
    return _client


async def aclose_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


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


async def api_request(
    method: str, path: str, *, json: Any = None, params: dict | None = None
) -> httpx2.Response:
    """Makes one GitHub REST API call and returns the raw Response --
    callers decide what a given status means for their endpoint (e.g. a 404
    means "feature disabled" for vulnerability-alerts but "not found"
    everywhere else). Raises GhError only for genuine transport failures
    (DNS, timeout, connection reset); HTTP error statuses are returned, not
    raised.

    Transport errors and transient statuses (429, 5xx) are retried up to
    MAX_RETRIES times, honouring `Retry-After`. GitHub's writes here are
    idempotent (secret PUTs replace), so retrying any method is safe.
    """
    url = path if path.startswith("http") else f"{API_BASE}{path}"
    http = await _get_client()
    try:
        async for attempt in stamina.retry_context(
            on=_should_retry,
            attempts=MAX_RETRIES + 1,
            timeout=None,
            wait_initial=RETRY_WAIT_INITIAL,
            wait_max=RETRY_WAIT_MAX,
            wait_jitter=RETRY_WAIT_JITTER,
        ):
            with attempt:
                try:
                    response = await http.request(method, url, json=json, params=params)
                except httpx2.TransportError:
                    raise
                except httpx2.HTTPError as exc:
                    raise GhError(str(exc)) from exc
                if response.status_code in RETRY_STATUSES:
                    response.raise_for_status()
                return response
    except httpx2.HTTPStatusError as exc:
        return exc.response  # retryable status, retries spent -- let the caller judge
    except httpx2.TransportError as exc:
        raise GhError(str(exc)) from exc
    raise GhError("api_request retry loop exited without a response")  # unreachable


def error_message(response: httpx2.Response) -> str:
    """Extracts GitHub's own `message` field from an error response body,
    falling back to the raw response text if the body isn't JSON.
    """
    try:
        return response.json().get("message", response.text)
    except ValueError:
        return response.text


async def api_json(
    method: str, path: str, *, json: Any = None, params: dict | None = None
) -> dict:
    """Like api_request, but raises GhError (with status_code and GitHub's
    own error message) on any non-2xx response, and returns the parsed JSON
    body -- or {} for a body-less response like 204 No Content -- on success.
    """
    response = await api_request(method, path, json=json, params=params)
    if not response.is_success:
        raise GhError(error_message(response), status_code=response.status_code)
    return response.json() if response.content else {}


async def paginated(
    method: str, path: str, *, params: dict | None = None
) -> list[dict]:
    """Follows GitHub's `Link: rel="next"` header, concatenating every page's
    JSON array into one list.
    """
    items = []
    url = path
    query = params
    while url:
        response = await api_request(method, url, params=query)
        if not response.is_success:
            raise GhError(error_message(response), status_code=response.status_code)
        items.extend(response.json())
        url = response.links.get("next", {}).get("url")
        query = None  # the "next" link already carries the full query string
    return items
