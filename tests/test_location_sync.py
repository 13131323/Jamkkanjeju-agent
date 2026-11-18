"""사용자 승인 전 변경 금지와 추후 Spring API 계약을 실제 외부 호출 없이 검증한다."""

import jamkkanjeju_agent.clients as client_runtime


import json

import httpx
import pytest
from langchain_core.tools import ToolException
from pydantic import ValidationError

from jamkkanjeju_agent.clients.tourapi_client import TourApiClient
from jamkkanjeju_agent.memory.store import snapshot_store
from jamkkanjeju_agent.services.sync_approval import approval_scope
from jamkkanjeju_agent.tools import location_tools as module
from jamkkanjeju_agent.tools.location_tools import compare_locations


def row(content_id, **extra):
    return {"contentId": content_id, "title": f"장소 {content_id}", "address": "제주시 테스트 주소",
            "category": "음식", "radius": 100, "latitude": 33.5, "longitude": 126.5, **extra}


def plan_args():
    db = snapshot_store.save_complete("DB", [row(1, locationId=11), row(2, locationId=12), row(4, locationId=14)])
    tour = snapshot_store.save_complete("TOURAPI", [row(2), row(3)])
    return {"planId": compare_locations.invoke({"storedSnapshotId": db["snapshotId"], "tourapiSnapshotId": tour["snapshotId"], "resultType": "all"})["planId"]}


@pytest.fixture
def backend(monkeypatch):
    calls = []
    original = httpx.Client
    monkeypatch.setenv("LOCATION_SYNC_API_URL", "https://spring.test/api/v2/agent/locations/sync")
    monkeypatch.setattr(TourApiClient, "get_location", lambda self, content_id:
                        {"contentId": 4, "title": "실제로 존재"} if content_id == 4 else None)
    def transport(handler):
        def wrapped(request):
            calls.append(request)
            return handler(request)
        monkeypatch.setattr(client_runtime.httpx, "Client", lambda: original(transport=httpx.MockTransport(wrapped)))
    return calls, transport


def completed(request):
    plan = json.loads(request.content)
    return httpx.Response(200, json={"requestId": plan["requestId"], "status": "COMPLETED",
        "addedCount": len(plan["addLocations"]), "deletedCount": len(plan["deleteLocations"]),
        "addedContentIds": [r["contentId"] for r in plan["addLocations"]],
        "deletedContentIds": [r["contentId"] for r in plan["deleteLocations"]]})


def test_without_real_approval_channel_never_posts(backend):
    calls, setup = backend
    setup(completed)
    with pytest.raises(ToolException, match="승인 경로가 없습니다"):
        module.synchronize_locations.invoke(plan_args())
    assert calls == []


def test_decline_never_posts(backend):
    calls, setup = backend
    setup(completed)
    with approval_scope(lambda report: False):
        result = module.synchronize_locations.invoke(plan_args())
    assert result["status"] == "CANCELLED"
    assert calls == []


def test_execution_does_not_repeat_listing_comparison_or_detail_lookup(backend, monkeypatch):
    calls, setup = backend
    setup(completed)
    args = plan_args()
    def forbidden(*args, **kwargs):
        pytest.fail("동기화 실행 Tool에서 TourAPI 재조회·삭제 재확인을 하면 안 됩니다.")
    monkeypatch.setattr(TourApiClient, "get_locations", forbidden)
    monkeypatch.setattr(TourApiClient, "get_location", forbidden)
    with approval_scope(lambda report: True):
        assert module.synchronize_locations.invoke(args)["status"] == "COMPLETED"
    assert len(calls) == 1


def test_cancelled_plan_cannot_ask_for_approval_again(backend):
    calls, setup = backend
    setup(completed)
    args = plan_args()
    with approval_scope(lambda report: False):
        assert module.synchronize_locations.invoke(args)["status"] == "CANCELLED"
    with approval_scope(lambda report: pytest.fail("같은 계획의 승인을 반복하면 안 됩니다.")):
        assert module.synchronize_locations.invoke(args)["alreadyCancelled"] is True
    assert calls == []


def test_comparison_output_mutation_cannot_change_fixed_plan(backend):
    calls, setup = backend
    setup(completed)
    db = snapshot_store.save_complete("DB", [row(1, locationId=11)])
    tour = snapshot_store.save_complete("TOURAPI", [row(3)])
    compared = compare_locations.invoke({"storedSnapshotId": db["snapshotId"], "tourapiSnapshotId": tour["snapshotId"], "resultType": "all"})
    compared["addedLocations"][0]["title"] = "변조된 결과"
    compared["confirmedMissingLocations"].clear()
    with approval_scope(lambda report: True):
        module.synchronize_locations.invoke({"planId": compared["planId"]})
    payload = json.loads(calls[0].content)
    assert payload["addLocations"] == [row(3)]
    assert payload["deleteLocations"] == [{"locationId": 11, "contentId": 1}]


def test_approval_posts_exact_report_and_preserves_existing_outside_list(backend):
    calls, setup = backend
    setup(completed)
    reports = []
    def approve(report):
        reports.append(report)
        # 승인 화면 측의 사본 변경은 실제 실행 계획을 바꾸지 못한다.
        report["addLocations"][0]["title"] = "화면의 사본"
        return True
    with approval_scope(approve):
        result = module.synchronize_locations.invoke(plan_args())
    assert result["status"] == "COMPLETED"
    assert len(calls) == 1
    plan = json.loads(calls[0].content)
    assert plan["addLocations"] == [row(3)]
    assert plan["deleteLocations"] == [{"locationId": 11, "contentId": 1}]
    assert reports[0]["deletedLocationsReport"][0]["title"] == "장소 1"
    assert calls[0].headers["X-Agent-Token"] == "private-test-token"
    assert calls[0].headers["Idempotency-Key"] == plan["requestId"]
    assert "impact" not in plan and "deletedLocationsReport" not in plan


def test_completed_plan_is_not_executed_twice(backend):
    calls, setup = backend
    setup(completed)
    args = plan_args()
    with approval_scope(lambda report: True):
        first = module.synchronize_locations.invoke(args)
    second = module.synchronize_locations.invoke(args)
    assert first["requestId"] == second["requestId"]
    assert second["alreadyExecuted"] is True
    assert len(calls) == 1


@pytest.mark.parametrize("status", [401, 403, 404, 405, 409, 500, 501])
def test_http_failure_is_not_success_and_cannot_automatically_retry(backend, status):
    calls, setup = backend
    setup(lambda request: httpx.Response(status, json={"message": "server failure"}))
    args = plan_args()
    with approval_scope(lambda report: True):
        with pytest.raises(ToolException):
            module.synchronize_locations.invoke(args)
        with pytest.raises(ToolException, match="반영 여부가 미확인"):
            module.synchronize_locations.invoke(args)
    assert len(calls) == 1


def test_timeout_is_not_retried(backend):
    calls, setup = backend
    def timeout(request):
        raise httpx.ReadTimeout("private URL must not leak", request=request)
    setup(timeout)
    with approval_scope(lambda report: True):
        with pytest.raises(ToolException, match="반영 여부가 미확인") as error:
            module.synchronize_locations.invoke(plan_args())
    assert "private URL" not in str(error.value)
    assert len(calls) == 1


def test_changed_result_is_rejected(backend):
    calls, setup = backend
    def changed(request):
        response = completed(request).json()
        response["deletedContentIds"] = [999]
        return httpx.Response(200, json=response)
    setup(changed)
    with approval_scope(lambda report: True):
        with pytest.raises(ToolException, match="승인한 계획과 다릅니다"):
            module.synchronize_locations.invoke(plan_args())


def test_missing_snapshot_and_model_supplied_approval_are_rejected(backend):
    calls, setup = backend
    setup(completed)
    with pytest.raises(ToolException):
        module.synchronize_locations.invoke({"planId": "invented"})
    with pytest.raises(ValidationError):
        module.synchronize_locations.invoke({**plan_args(), "approved": True})
    assert calls == []


def test_no_changes_does_not_ask_for_approval_or_post(backend):
    calls, setup = backend
    setup(completed)
    a = snapshot_store.save_complete("DB", [row(1, locationId=11)])
    b = snapshot_store.save_complete("TOURAPI", [row(1)])
    plan_id = compare_locations.invoke({"storedSnapshotId": a["snapshotId"], "tourapiSnapshotId": b["snapshotId"], "resultType": "all"})["planId"]
    result = module.synchronize_locations.invoke({"planId": plan_id})
    assert result["status"] == "NO_CHANGES"
    assert calls == []
