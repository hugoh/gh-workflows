import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import lib
import pytest


@pytest.fixture(autouse=True)
def _reset_http_client():
    # httpx2.AsyncClient is bound to the event loop it was created under, and
    # pytest-asyncio's default function-scoped loop means a client surviving
    # across tests would raise "attached to a different loop" errors.
    lib._client = None
    yield
    lib._client = None
