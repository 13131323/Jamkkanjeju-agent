"""서비스별 클라이언트 공유와 대화 종료 시 연결 정리를 검증한다."""

from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

import jamkkanjeju_agent.clients as runtime
from jamkkanjeju_agent.chat import run_chat
from jamkkanjeju_agent.clients.spring_client import SpringClient
from jamkkanjeju_agent.tools import get_stored_locations, schedule_maintenance, synchronize_locations, compare_locations
from jamkkanjeju_agent.services.sync_approval import approval_scope


def test_spring_tools_share_one_client_and_connection(monkeypatch):
    monkeypatch.delenv("KTO_SERVICE_KEY", raising=False)
    monkeypatch.delenv("MAINTENANCE_API_URL", raising=False)
    seen = []
    row = {"contentId": 2, "title": "장소", "address": "제주시", "category": "음식",
           "radius": 100, "latitude": 33.5, "longitude": 126.5}
    def stored(self):
        seen.append(self)
        return []
    def schedule(self, message, *, startsAt=None):
        from jamkkanjeju_agent.clients.spring_client import MaintenanceReservation
        seen.append(self)
        return MaintenanceReservation(maintenanceId=1, message=message, status="SCHEDULED",
            startsAt="2026-11-18T15:00:00", endsAt="2026-11-18T16:00:00")
    def synchronize(self, plan):
        seen.append(self)
        return {"requestId": plan["requestId"], "status": "COMPLETED", "addedCount": 1,
                "deletedCount": 0, "addedContentIds": [2], "deletedContentIds": []}
    monkeypatch.setattr(SpringClient, "get_stored_locations", stored)
    monkeypatch.setattr(SpringClient, "schedule_maintenance", schedule)
    monkeypatch.setattr(SpringClient, "synchronize_locations", synchronize)
    connections = []
    original = httpx.Client
    def connection():
        http = original(transport=httpx.MockTransport(lambda _: pytest.fail("통신 불필요")))
        connections.append(http)
        return http
    monkeypatch.setattr(runtime.httpx, "Client", connection)
    with runtime.client_scope() as clients:
        assert connections == []
        db = get_stored_locations.invoke({})
        from jamkkanjeju_agent.memory.store import snapshot_store
        tour = snapshot_store.save_complete("TOURAPI", [row])
        plan = compare_locations.invoke({"storedSnapshotId": db["snapshotId"],
                                         "tourapiSnapshotId": tour["snapshotId"]})
        schedule_maintenance.invoke({})
        with approval_scope(lambda _: True):
            synchronize_locations.invoke({"planId": plan["planId"]})
        assert len(seen) == 3 and all(client is clients.spring for client in seen)
        assert len(connections) == 1 and not connections[0].is_closed
    assert connections[0].is_closed


def test_same_service_is_initialized_once_across_threads():
    clients = runtime.AgentClients()
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            instances = list(pool.map(lambda _: clients.spring, range(32)))
        assert all(client is instances[0] for client in instances)
    finally:
        clients.close()
    assert instances[0]._http.is_closed


def test_nested_scope_reuses_connections_and_closes_on_error():
    with pytest.raises(ValueError, match="중단"):
        with runtime.client_scope() as outer:
            spring = outer.spring
            with runtime.client_scope() as inner:
                assert inner is outer and inner.spring is spring
            assert not spring._http.is_closed
            raise ValueError("중단")
    assert spring._http.is_closed


def test_spring_and_tourapi_have_separate_connections():
    with runtime.client_scope() as clients:
        spring = clients.spring
        tourapi = clients.tourapi
        assert spring._http is not tourapi._http
        assert clients.tourapi is tourapi
    assert spring._http.is_closed and tourapi._http.is_closed


def test_cli_closes_shared_connections_when_user_exits():
    instances = []
    class Agent:
        def stream(self, *args, **kwargs):
            from langchain_core.messages import AIMessage
            instances.append(runtime.get_clients().spring)
            yield {"model": {"messages": [AIMessage(content="완료")]}}
    prompts = iter(["조회", "조회", "/exit"])
    assert run_chat(Agent(), read=lambda _: next(prompts), write=lambda _: None) == 0
    assert len(instances) == 2 and instances[0] is instances[1]
    assert instances[0]._http.is_closed
