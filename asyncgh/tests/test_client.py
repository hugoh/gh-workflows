import json

import httpx
import httpx2
import pytest
import respx
from asyncgh.client import GitHubClient, _should_retry

from asyncgh import API_BASE, GhError, api_json, api_raw, client, error_message, graphql


@pytest.fixture(autouse=True)
def _instant_backoff(monkeypatch):
    monkeypatch.setattr(client, "RETRY_WAIT_INITIAL", 0.0)
    monkeypatch.setattr(client, "RETRY_WAIT_MAX", 0.0)
    monkeypatch.setattr(client, "RETRY_WAIT_JITTER", 0.0)


async def test_error_message_prefers_json_message_field(httpx2_mock: respx.Router):
    httpx2_mock.get(f"{API_BASE}/x").mock(
        return_value=httpx.Response(404, json={"message": "not found"})
    )
    response = await api_raw("GET", "/x")
    assert error_message(response) == "not found"


async def test_error_message_falls_back_to_raw_text_for_non_json_body(
    httpx2_mock: respx.Router,
):
    httpx2_mock.get(f"{API_BASE}/x").mock(
        return_value=httpx.Response(
            500, text="plain text error", headers={"Content-Type": "text/plain"}
        )
    )
    response = await api_raw("GET", "/x")
    assert error_message(response) == "plain text error"


async def test_error_message_falls_back_to_raw_text_for_non_object_json_body(
    httpx2_mock: respx.Router,
):
    httpx2_mock.get(f"{API_BASE}/x").mock(
        return_value=httpx.Response(500, json=["not", "an", "object"])
    )
    response = await api_raw("GET", "/x")
    assert error_message(response) == response.text


async def test_api_json_returns_parsed_body_on_success(httpx2_mock: respx.Router):
    httpx2_mock.get(f"{API_BASE}/repos/hugoh/gh-workflows").mock(
        return_value=httpx.Response(200, json={"name": "gh-workflows"})
    )
    assert await api_json("GET", "/repos/hugoh/gh-workflows") == {
        "name": "gh-workflows"
    }


async def test_api_json_raises_gh_error_with_status_code_on_failure(
    httpx2_mock: respx.Router,
):
    httpx2_mock.get(f"{API_BASE}/repos/hugoh/nope").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )
    with pytest.raises(GhError) as exc_info:
        await api_json("GET", "/repos/hugoh/nope")
    assert exc_info.value.status_code == 404
    assert "Not Found" in str(exc_info.value)


async def test_api_json_handles_empty_204_response(httpx2_mock: respx.Router):
    httpx2_mock.put(f"{API_BASE}/repos/hugoh/gh-workflows/vulnerability-alerts").mock(
        return_value=httpx.Response(204)
    )
    assert await api_json("PUT", "/repos/hugoh/gh-workflows/vulnerability-alerts") == {}


async def test_api_raw_does_not_raise_on_http_error_status(
    httpx2_mock: respx.Router,
):
    httpx2_mock.get(
        f"{API_BASE}/repos/hugoh/private-repo/private-vulnerability-reporting"
    ).mock(return_value=httpx.Response(404))
    response = await api_raw(
        "GET", "/repos/hugoh/private-repo/private-vulnerability-reporting"
    )
    assert response.status_code == 404


async def test_api_raw_retries_transport_error_then_succeeds(
    httpx2_mock: respx.Router,
):
    route = httpx2_mock.get(f"{API_BASE}/x").mock(
        side_effect=[httpx2.ConnectError("boom"), httpx.Response(200, json={})]
    )
    response = await api_raw("GET", "/x")
    assert response.status_code == 200
    assert route.call_count == 2


async def test_api_raw_raises_gh_error_after_exhausting_transport_retries(
    httpx2_mock: respx.Router,
):
    route = httpx2_mock.get(f"{API_BASE}/x").mock(
        side_effect=httpx2.ConnectError("boom")
    )
    with pytest.raises(GhError, match="boom"):
        await api_raw("GET", "/x")
    assert route.call_count == client.DEFAULT_MAX_RETRIES + 1


async def test_api_raw_retries_retryable_status_then_returns_success(
    httpx2_mock: respx.Router,
):
    route = httpx2_mock.get(f"{API_BASE}/x").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json={})]
    )
    response = await api_raw("GET", "/x")
    assert response.status_code == 200
    assert route.call_count == 2


async def test_api_raw_returns_last_response_when_retries_exhausted(
    httpx2_mock: respx.Router,
):
    route = httpx2_mock.get(f"{API_BASE}/x").mock(return_value=httpx.Response(503))
    response = await api_raw("GET", "/x")
    assert response.status_code == 503
    assert route.call_count == client.DEFAULT_MAX_RETRIES + 1


async def test_api_raw_does_not_retry_non_retryable_status(
    httpx2_mock: respx.Router,
):
    route = httpx2_mock.get(f"{API_BASE}/x").mock(return_value=httpx.Response(404))
    await api_raw("GET", "/x")
    assert route.call_count == 1


async def test_api_raw_does_not_retry_non_transport_request_error(
    httpx2_mock: respx.Router,
):
    route = httpx2_mock.get(f"{API_BASE}/x").mock(
        side_effect=httpx2.DecodingError("bad body")
    )
    with pytest.raises(GhError, match="bad body"):
        await api_raw("GET", "/x")
    assert route.call_count == 1


async def test_max_retries_zero_disables_retrying(
    httpx2_mock: respx.Router, monkeypatch
):
    monkeypatch.setattr(client, "_default_max_retries", lambda: 0)
    route = httpx2_mock.get(f"{API_BASE}/x").mock(return_value=httpx.Response(503))
    await api_raw("GET", "/x")
    assert route.call_count == 1


def test_default_max_retries_reads_env_var(monkeypatch):
    monkeypatch.setenv("GH_MAX_RETRIES", "7")
    assert client._default_max_retries() == 7


def test_default_max_retries_falls_back_when_unset(monkeypatch):
    monkeypatch.delenv("GH_MAX_RETRIES", raising=False)
    assert client._default_max_retries() == client.DEFAULT_MAX_RETRIES


def test_default_max_retries_raises_gh_error_on_malformed_value(monkeypatch):
    monkeypatch.setenv("GH_MAX_RETRIES", "not-a-number")
    with pytest.raises(GhError, match="GH_MAX_RETRIES"):
        client._default_max_retries()


def _status_error(status: int, **headers: str) -> httpx2.HTTPStatusError:
    request = httpx2.Request("GET", f"{API_BASE}/x")
    response = httpx2.Response(status, headers=headers, request=request)
    return httpx2.HTTPStatusError(str(status), request=request, response=response)


def test_should_retry_reads_retry_after_header_as_exact_wait():
    assert _should_retry(_status_error(429, **{"Retry-After": "42"})) == 42.0


def test_should_retry_true_for_5xx_without_retry_after():
    assert _should_retry(_status_error(503)) is True


def test_should_retry_false_for_4xx():
    assert _should_retry(_status_error(404)) is False


def test_should_retry_true_for_transport_error():
    assert _should_retry(httpx2.ConnectError("boom")) is True


def test_should_retry_false_for_other_exception():
    assert _should_retry(ValueError("nope")) is False


async def test_graphql_posts_query_and_variables_and_returns_data(
    httpx2_mock: respx.Router,
):
    route = httpx2_mock.post(f"{API_BASE}/graphql").mock(
        return_value=httpx.Response(200, json={"data": {"viewer": {"login": "hugoh"}}})
    )
    data = await graphql("query { viewer { login } }", {"x": 1})
    assert data == {"viewer": {"login": "hugoh"}}
    body = json.loads(route.calls[0].request.content)
    assert body == {"query": "query { viewer { login } }", "variables": {"x": 1}}


async def test_graphql_raises_gh_error_when_errors_present_and_data_null(
    httpx2_mock: respx.Router,
):
    httpx2_mock.post(f"{API_BASE}/graphql").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": None,
                "errors": [{"type": "NOT_FOUND", "message": "Could not resolve"}],
            },
        )
    )
    with pytest.raises(GhError, match="Could not resolve") as exc_info:
        await graphql("query { nope }")
    assert exc_info.value.error_type == "NOT_FOUND"


async def test_graphql_raises_gh_error_when_errors_present_alongside_partial_data(
    httpx2_mock: respx.Router,
):
    httpx2_mock.post(f"{API_BASE}/graphql").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {"r0": {"name": "a"}, "r1": None},
                "errors": [{"message": "r1 not found"}],
            },
        )
    )
    with pytest.raises(GhError, match="r1 not found"):
        await graphql("query { r0 r1 }")


async def test_graphql_retries_on_transport_error(httpx2_mock: respx.Router):
    route = httpx2_mock.post(f"{API_BASE}/graphql").mock(
        side_effect=[
            httpx2.ConnectError("boom"),
            httpx.Response(200, json={"data": {"ok": True}}),
        ]
    )
    data = await graphql("query { ok }")
    assert data == {"ok": True}
    assert route.call_count == 2


async def test_graphql_retries_retryable_status_then_succeeds(
    httpx2_mock: respx.Router,
):
    route = httpx2_mock.post(f"{API_BASE}/graphql").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, json={"data": {"ok": True}}),
        ]
    )
    data = await graphql("query { ok }")
    assert data == {"ok": True}
    assert route.call_count == 2


async def test_graphql_raises_gh_error_when_retryable_status_exhausted(
    httpx2_mock: respx.Router,
):
    route = httpx2_mock.post(f"{API_BASE}/graphql").mock(
        return_value=httpx.Response(503)
    )
    with pytest.raises(GhError):
        await graphql("query { ok }")
    assert route.call_count == client.DEFAULT_MAX_RETRIES + 1


async def test_graphql_retries_rate_limited_error_then_succeeds(
    httpx2_mock: respx.Router,
):
    route = httpx2_mock.post(f"{API_BASE}/graphql").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "data": None,
                    "errors": [{"type": "RATE_LIMITED", "message": "rate limited"}],
                },
            ),
            httpx.Response(200, json={"data": {"ok": True}}),
        ]
    )
    data = await graphql("query { ok }")
    assert data == {"ok": True}
    assert route.call_count == 2


async def test_graphql_raises_gh_error_after_exhausting_rate_limited_retries(
    httpx2_mock: respx.Router,
):
    route = httpx2_mock.post(f"{API_BASE}/graphql").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": None,
                "errors": [{"type": "RATE_LIMITED", "message": "rate limited"}],
            },
        )
    )
    with pytest.raises(GhError, match="rate limited"):
        await graphql("query { ok }")
    assert route.call_count == client.DEFAULT_MAX_RETRIES + 1


async def test_graphql_total_requests_bounded_by_single_retry_budget(
    httpx2_mock: respx.Router, monkeypatch
):
    # Regression test: graphql() used to wrap a whole api_raw() call (which
    # retries transport errors on its own) in a second, outer retry loop for
    # RATE_LIMITED -- worst case (max_retries+1)**2 real requests for one
    # logical call. A transport error followed by a rate-limited response,
    # both within one graphql() call, exercises exactly the path that would
    # have hit both loops under the old nested design; the single unified
    # loop here still resolves it within max_retries+1 total attempts.
    monkeypatch.setattr(client, "_default_max_retries", lambda: 3)
    route = httpx2_mock.post(f"{API_BASE}/graphql").mock(
        side_effect=[
            httpx2.ConnectError("boom"),
            httpx.Response(
                200,
                json={
                    "data": None,
                    "errors": [{"type": "RATE_LIMITED", "message": "rate limited"}],
                },
            ),
            httpx.Response(200, json={"data": {"ok": True}}),
        ]
    )
    data = await graphql("query { ok }")
    assert data == {"ok": True}
    assert route.call_count == 3


# ---------------------------------------------------------------------------
# GitHubClient -- explicit instances, independent of the module-level default
# ---------------------------------------------------------------------------


async def test_github_client_is_independent_of_the_default_client(
    httpx2_mock: respx.Router,
):
    httpx2_mock.get(f"{API_BASE}/x").mock(
        return_value=httpx.Response(200, json={"n": 1})
    )
    gh = GitHubClient(token="explicit-token")
    response = await gh.api_raw("GET", "/x")
    assert response.json() == {"n": 1}
    assert (
        httpx2_mock.calls[0].request.headers["authorization"] == "Bearer explicit-token"
    )
    await gh.aclose()


async def test_two_github_clients_use_different_tokens(httpx2_mock: respx.Router):
    httpx2_mock.get(f"{API_BASE}/x").mock(return_value=httpx.Response(200, json={}))
    gh_a = GitHubClient(token="token-a")
    gh_b = GitHubClient(token="token-b")
    await gh_a.api_raw("GET", "/x")
    await gh_b.api_raw("GET", "/x")
    tokens = {call.request.headers["authorization"] for call in httpx2_mock.calls}
    assert tokens == {"Bearer token-a", "Bearer token-b"}
    await gh_a.aclose()
    await gh_b.aclose()


async def test_github_client_max_retries_overrides_the_default(
    httpx2_mock: respx.Router,
):
    route = httpx2_mock.get(f"{API_BASE}/x").mock(return_value=httpx.Response(503))
    gh = GitHubClient(max_retries=1)
    await gh.api_raw("GET", "/x")
    assert route.call_count == 2  # 1 retry + the initial attempt
    await gh.aclose()


async def test_github_client_as_context_manager_closes_on_exit(
    httpx2_mock: respx.Router,
):
    httpx2_mock.get(f"{API_BASE}/x").mock(return_value=httpx.Response(200, json={}))
    async with GitHubClient(token="t") as gh:
        await gh.api_raw("GET", "/x")
        http = gh._http
        assert http is not None
    assert http.is_closed
