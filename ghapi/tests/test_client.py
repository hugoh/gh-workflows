import httpx
import httpx2
import pytest
import respx
from ghapi.client import _should_retry

from ghapi import API_BASE, GhError, api_json, api_request, client, error_message


@pytest.fixture(autouse=True)
def _instant_backoff(monkeypatch):
    monkeypatch.setattr(client, "RETRY_WAIT_INITIAL", 0.0)
    monkeypatch.setattr(client, "RETRY_WAIT_MAX", 0.0)
    monkeypatch.setattr(client, "RETRY_WAIT_JITTER", 0.0)


async def test_error_message_prefers_json_message_field(httpx2_mock: respx.Router):
    httpx2_mock.get(f"{API_BASE}/x").mock(
        return_value=httpx.Response(404, json={"message": "not found"})
    )
    response = await api_request("GET", "/x")
    assert error_message(response) == "not found"


async def test_error_message_falls_back_to_raw_text_for_non_json_body(
    httpx2_mock: respx.Router,
):
    httpx2_mock.get(f"{API_BASE}/x").mock(
        return_value=httpx.Response(
            500, text="plain text error", headers={"Content-Type": "text/plain"}
        )
    )
    response = await api_request("GET", "/x")
    assert error_message(response) == "plain text error"


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


async def test_api_request_does_not_raise_on_http_error_status(
    httpx2_mock: respx.Router,
):
    httpx2_mock.get(
        f"{API_BASE}/repos/hugoh/private-repo/private-vulnerability-reporting"
    ).mock(return_value=httpx.Response(404))
    response = await api_request(
        "GET", "/repos/hugoh/private-repo/private-vulnerability-reporting"
    )
    assert response.status_code == 404


async def test_api_request_retries_transport_error_then_succeeds(
    httpx2_mock: respx.Router,
):
    route = httpx2_mock.get(f"{API_BASE}/x").mock(
        side_effect=[httpx2.ConnectError("boom"), httpx.Response(200, json={})]
    )
    response = await api_request("GET", "/x")
    assert response.status_code == 200
    assert route.call_count == 2


async def test_api_request_raises_gh_error_after_exhausting_transport_retries(
    httpx2_mock: respx.Router,
):
    route = httpx2_mock.get(f"{API_BASE}/x").mock(
        side_effect=httpx2.ConnectError("boom")
    )
    with pytest.raises(GhError, match="boom"):
        await api_request("GET", "/x")
    assert route.call_count == client.MAX_RETRIES + 1


async def test_api_request_retries_retryable_status_then_returns_success(
    httpx2_mock: respx.Router,
):
    route = httpx2_mock.get(f"{API_BASE}/x").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json={})]
    )
    response = await api_request("GET", "/x")
    assert response.status_code == 200
    assert route.call_count == 2


async def test_api_request_returns_last_response_when_retries_exhausted(
    httpx2_mock: respx.Router,
):
    route = httpx2_mock.get(f"{API_BASE}/x").mock(return_value=httpx.Response(503))
    response = await api_request("GET", "/x")
    assert response.status_code == 503
    assert route.call_count == client.MAX_RETRIES + 1


async def test_api_request_does_not_retry_non_retryable_status(
    httpx2_mock: respx.Router,
):
    route = httpx2_mock.get(f"{API_BASE}/x").mock(return_value=httpx.Response(404))
    await api_request("GET", "/x")
    assert route.call_count == 1


async def test_api_request_does_not_retry_non_transport_request_error(
    httpx2_mock: respx.Router,
):
    route = httpx2_mock.get(f"{API_BASE}/x").mock(
        side_effect=httpx2.DecodingError("bad body")
    )
    with pytest.raises(GhError, match="bad body"):
        await api_request("GET", "/x")
    assert route.call_count == 1


async def test_max_retries_zero_disables_retrying(
    httpx2_mock: respx.Router, monkeypatch
):
    monkeypatch.setattr(client, "MAX_RETRIES", 0)
    route = httpx2_mock.get(f"{API_BASE}/x").mock(return_value=httpx.Response(503))
    await api_request("GET", "/x")
    assert route.call_count == 1


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
