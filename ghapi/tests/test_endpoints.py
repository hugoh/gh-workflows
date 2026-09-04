import base64

import httpx
import respx
from nacl import encoding, public

from ghapi import (
    API_BASE,
    encrypt_secret_value,
    endpoints,
    fetch_repos_json,
    public_repos_json,
    set_repo_secret,
)


async def test_fetch_repos_json_uses_authenticated_user_repos_when_owner_matches(
    httpx2_mock: respx.Router,
):
    httpx2_mock.get(f"{API_BASE}/user").mock(
        return_value=httpx.Response(200, json={"login": "hugoh"})
    )
    httpx2_mock.get(f"{API_BASE}/user/repos").mock(
        return_value=httpx.Response(200, json=[{"name": "a"}])
    )
    assert await fetch_repos_json("hugoh") == [{"name": "a"}]


async def test_fetch_repos_json_falls_back_to_public_repos_for_other_owners(
    httpx2_mock: respx.Router,
):
    httpx2_mock.get(f"{API_BASE}/user").mock(
        return_value=httpx.Response(200, json={"login": "hugoh"})
    )
    httpx2_mock.get(f"{API_BASE}/users/someorg/repos").mock(
        return_value=httpx.Response(200, json=[{"name": "b"}])
    )
    assert await fetch_repos_json("someorg") == [{"name": "b"}]


async def test_fetch_repos_json_follows_pagination_link_header(
    httpx2_mock: respx.Router,
):
    httpx2_mock.get(f"{API_BASE}/user").mock(
        return_value=httpx.Response(200, json={"login": "hugoh"})
    )
    # respx routes are tried in registration order and a route with no
    # `params` constraint matches any query string -- the page=2 route must
    # be registered first, or the unconstrained page-1 route below would
    # swallow it too and paginated would loop on page1 forever.
    httpx2_mock.get(f"{API_BASE}/user/repos", params={"page": "2"}).mock(
        return_value=httpx.Response(200, json=[{"name": "page2"}])
    )
    httpx2_mock.get(f"{API_BASE}/user/repos").mock(
        return_value=httpx.Response(
            200,
            json=[{"name": "page1"}],
            headers={"Link": f'<{API_BASE}/user/repos?page=2>; rel="next"'},
        )
    )
    assert await fetch_repos_json("hugoh") == [{"name": "page1"}, {"name": "page2"}]


async def test_public_repos_json_uses_public_users_endpoint_even_for_self(
    httpx2_mock: respx.Router,
):
    # /users/{owner}/repos only ever returns public repos, even when owner is
    # the authenticated user -- unlike fetch_repos_json, no /user call is
    # needed to check whether owner is the viewer.
    httpx2_mock.get(f"{API_BASE}/users/hugoh/repos").mock(
        return_value=httpx.Response(200, json=[{"name": "public-repo"}])
    )
    assert await public_repos_json("hugoh") == [{"name": "public-repo"}]
    assert not any(call.request.url.path == "/user" for call in httpx2_mock.calls)


async def test_public_repos_json_follows_pagination_link_header(
    httpx2_mock: respx.Router,
):
    httpx2_mock.get(f"{API_BASE}/users/hugoh/repos", params={"page": "2"}).mock(
        return_value=httpx.Response(200, json=[{"name": "page2"}])
    )
    httpx2_mock.get(f"{API_BASE}/users/hugoh/repos").mock(
        return_value=httpx.Response(
            200,
            json=[{"name": "page1"}],
            headers={"Link": f'<{API_BASE}/users/hugoh/repos?page=2>; rel="next"'},
        )
    )
    assert await public_repos_json("hugoh") == [{"name": "page1"}, {"name": "page2"}]


def test_encrypt_secret_value_round_trips_through_sealed_box():
    private_key = public.PrivateKey.generate()
    public_key_b64 = private_key.public_key.encode(encoding.Base64Encoder).decode(
        "utf-8"
    )

    ciphertext_b64 = encrypt_secret_value(public_key_b64, "super-secret-value")

    decrypted = public.SealedBox(private_key).decrypt(base64.b64decode(ciphertext_b64))
    assert decrypted == b"super-secret-value"


async def test_set_repo_secret_encrypts_and_puts_with_key_id(monkeypatch):
    private_key = public.PrivateKey.generate()
    public_key_b64 = private_key.public_key.encode(encoding.Base64Encoder).decode(
        "utf-8"
    )
    calls = []

    async def fake_api_json(method, path, **kwargs):
        if method == "GET":
            return {"key": public_key_b64, "key_id": "key-id-123"}
        calls.append((method, path, kwargs.get("json")))
        return {}

    monkeypatch.setattr(endpoints, "api_json", fake_api_json)
    await set_repo_secret("hugoh", "repo", "NAME", "the-value")

    assert len(calls) == 1
    method, path, body = calls[0]
    assert method == "PUT"
    assert path == "/repos/hugoh/repo/actions/secrets/NAME"
    assert body["key_id"] == "key-id-123"

    decrypted = public.SealedBox(private_key).decrypt(
        base64.b64decode(body["encrypted_value"])
    )
    assert decrypted == b"the-value"
