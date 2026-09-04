import base64
import json

import httpx
import respx
from nacl import encoding, public

from asyncgh import (
    API_BASE,
    encrypt_secret_value,
    fetch_repos,
    get_repo_public_key,
    public_repos,
    set_repo_secret,
)


async def test_fetch_repos_uses_authenticated_user_repos_when_owner_matches(
    httpx2_mock: respx.Router,
):
    httpx2_mock.get(f"{API_BASE}/user").mock(
        return_value=httpx.Response(200, json={"login": "hugoh"})
    )
    httpx2_mock.get(f"{API_BASE}/user/repos").mock(
        return_value=httpx.Response(200, json=[{"name": "a"}])
    )
    assert await fetch_repos("hugoh") == [{"name": "a"}]


async def test_fetch_repos_falls_back_to_public_repos_for_other_owners(
    httpx2_mock: respx.Router,
):
    httpx2_mock.get(f"{API_BASE}/user").mock(
        return_value=httpx.Response(200, json={"login": "hugoh"})
    )
    httpx2_mock.get(f"{API_BASE}/users/someorg/repos").mock(
        return_value=httpx.Response(200, json=[{"name": "b"}])
    )
    assert await fetch_repos("someorg") == [{"name": "b"}]


async def test_fetch_repos_follows_pagination_link_header(
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
    assert await fetch_repos("hugoh") == [{"name": "page1"}, {"name": "page2"}]


async def test_public_repos_uses_public_users_endpoint_even_for_self(
    httpx2_mock: respx.Router,
):
    # /users/{owner}/repos only ever returns public repos, even when owner is
    # the authenticated user -- unlike fetch_repos, no /user call is
    # needed to check whether owner is the viewer.
    httpx2_mock.get(f"{API_BASE}/users/hugoh/repos").mock(
        return_value=httpx.Response(200, json=[{"name": "public-repo"}])
    )
    assert await public_repos("hugoh") == [{"name": "public-repo"}]
    assert not any(call.request.url.path == "/user" for call in httpx2_mock.calls)


async def test_public_repos_follows_pagination_link_header(
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
    assert await public_repos("hugoh") == [{"name": "page1"}, {"name": "page2"}]


def test_encrypt_secret_value_round_trips_through_sealed_box():
    private_key = public.PrivateKey.generate()
    public_key_b64 = private_key.public_key.encode(encoding.Base64Encoder).decode(
        "utf-8"
    )

    ciphertext_b64 = encrypt_secret_value(public_key_b64, "super-secret-value")

    decrypted = public.SealedBox(private_key).decrypt(base64.b64decode(ciphertext_b64))
    assert decrypted == b"super-secret-value"


def _sealed_box_key() -> tuple[public.PrivateKey, str]:
    private_key = public.PrivateKey.generate()
    public_key_b64 = private_key.public_key.encode(encoding.Base64Encoder).decode(
        "utf-8"
    )
    return private_key, public_key_b64


def _assert_put_body_encrypts(
    put_route: respx.Route, private_key: public.PrivateKey, expected_value: bytes
) -> None:
    body = json.loads(put_route.calls[0].request.content)
    assert body["key_id"] == "key-id-123"
    decrypted = public.SealedBox(private_key).decrypt(
        base64.b64decode(body["encrypted_value"])
    )
    assert decrypted == expected_value


async def test_get_repo_public_key_returns_key_and_id(httpx2_mock: respx.Router):
    httpx2_mock.get(f"{API_BASE}/repos/hugoh/repo/actions/secrets/public-key").mock(
        return_value=httpx.Response(
            200, json={"key": "the-key", "key_id": "key-id-123"}
        )
    )
    assert await get_repo_public_key("hugoh", "repo") == {
        "key": "the-key",
        "key_id": "key-id-123",
    }


async def test_set_repo_secret_encrypts_and_puts_with_key_id(
    httpx2_mock: respx.Router,
):
    private_key, public_key_b64 = _sealed_box_key()
    httpx2_mock.get(f"{API_BASE}/repos/hugoh/repo/actions/secrets/public-key").mock(
        return_value=httpx.Response(
            200, json={"key": public_key_b64, "key_id": "key-id-123"}
        )
    )
    put_route = httpx2_mock.put(
        f"{API_BASE}/repos/hugoh/repo/actions/secrets/NAME"
    ).mock(return_value=httpx.Response(201))

    await set_repo_secret("hugoh", "repo", "NAME", "the-value")

    assert put_route.call_count == 1
    _assert_put_body_encrypts(put_route, private_key, b"the-value")


async def test_set_repo_secret_reuses_a_passed_in_public_key(
    httpx2_mock: respx.Router,
):
    # Deliberately no GET .../public-key route registered: if set_repo_secret
    # fetched the key despite being given one, respx would fail the request
    # as unmocked rather than silently letting it through.
    private_key, public_key_b64 = _sealed_box_key()
    put_route = httpx2_mock.put(
        f"{API_BASE}/repos/hugoh/repo/actions/secrets/NAME"
    ).mock(return_value=httpx.Response(201))

    await set_repo_secret(
        "hugoh",
        "repo",
        "NAME",
        "the-value",
        public_key={"key": public_key_b64, "key_id": "key-id-123"},
    )

    _assert_put_body_encrypts(put_route, private_key, b"the-value")
