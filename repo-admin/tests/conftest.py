import lib
import pytest

from asyncgh import client


@pytest.fixture(autouse=True)
def _reset_http_client():
    # httpx2.AsyncClient is bound to the event loop it was created under, and
    # pytest-asyncio's default function-scoped loop means a default client
    # surviving across tests would raise "attached to a different loop"
    # errors.
    client._default_client = None
    yield
    client._default_client = None


@pytest.fixture(autouse=True)
def _default_owner(monkeypatch):
    # default_owner() falls back to a live `GET /user` call when GH_OWNER
    # isn't set, and caches the result module-globally for the process --
    # fine for one CLI invocation, but it'd leak across tests and require
    # real network/auth otherwise.
    monkeypatch.setenv("GH_OWNER", "hugoh")
    lib._default_owner = None
    yield
    lib._default_owner = None
