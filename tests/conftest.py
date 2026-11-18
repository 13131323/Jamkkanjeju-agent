"""외부 API를 사용하지 않는 검증과 프로세스 스냅샷 격리를 제공한다."""

import jamkkanjeju_agent.clients as client_runtime


import httpx
import pytest

from jamkkanjeju_agent.memory.store import snapshot_store
from jamkkanjeju_agent.tools.location_tools import clear_sync_executions


@pytest.fixture(autouse=True)
def isolate_snapshots_and_block_external_http(monkeypatch):
    client_runtime.close_default_clients()
    snapshot_store.clear()
    clear_sync_executions()
    monkeypatch.setenv("STORED_LOCATIONS_API_URL", "https://spring.test/api/v2/agent/locations")
    monkeypatch.setenv("AGENT_API_TOKEN", "private-test-token")
    monkeypatch.setenv("KTO_BASE_URL", "https://tour.test/KorService2")
    monkeypatch.setenv("KTO_SERVICE_KEY", "secret-tour-key")
    def reject_network(*args, **kwargs):
        pytest.fail("실제 외부 HTTP 호출은 금지되어 있습니다. MockTransport를 사용하세요.")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", reject_network)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", reject_network)
    yield
    client_runtime.close_default_clients()
    snapshot_store.clear()
    clear_sync_executions()
