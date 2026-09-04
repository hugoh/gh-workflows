import pytest

from ghapi import client


@pytest.fixture(autouse=True)
def _reset_http_client():
    # httpx2.AsyncClient is bound to the event loop it was created under, and
    # pytest-asyncio's default function-scoped loop means a client surviving
    # across tests would raise "attached to a different loop" errors.
    client._client = None
    yield
    client._client = None


@pytest.fixture(autouse=True)
def fake_auth_token(monkeypatch):
    # Avoids every test shelling out to the real `gh auth token` -- client
    # creation is lazy, so this just needs to be in place before the first
    # api_request/api_json call.
    monkeypatch.setattr(client, "_auth_token", lambda: "fake-token")
