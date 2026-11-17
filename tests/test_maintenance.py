"""미구현 Spring API 대신 HTTP 대역으로 예약 Tool과 클라이언트를 검증한다."""

import json

import httpx
import pytest
from langchain_core.tools import ToolException
from pydantic import ValidationError

from jamkkanjeju_agent.clients.maintenance import MaintenanceApiError, MaintenanceClient
from jamkkanjeju_agent.tools import maintenance as module


URL = "https://spring.test/api/v2/agent/maintenances"
TOKEN = "private-agent-token"
RESPONSE = {
    "maintenanceId": 11,
    "message": module.DEFAULT_MAINTENANCE_MESSAGE,
    "startsAt": "2026-11-18T02:00:00",
    "endsAt": "2026-11-18T03:00:00",
    "status": "SCHEDULED",
}


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    monkeypatch.setenv("MAINTENANCE_API_URL", URL)
    monkeypatch.setenv("AGENT_API_TOKEN", TOKEN)


def install_http(monkeypatch, handler):
    http = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(module.httpx, "Client", lambda: http)
    return http


@pytest.mark.parametrize("args", [{}, {"message": None}, {"message": ""}, {"message": " \n "}])
def test_tool_sends_default_message_and_returns_confirmed_reservation(monkeypatch, args):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == "POST"
        assert str(request.url) == URL
        assert request.headers["X-Agent-Token"] == TOKEN
        assert "Authorization" not in request.headers
        assert json.loads(request.content) == {"message": module.DEFAULT_MAINTENANCE_MESSAGE}
        return httpx.Response(201, json=RESPONSE)

    http = install_http(monkeypatch, handler)
    assert module.schedule_maintenance.invoke(args) == RESPONSE
    assert len(calls) == 1
    assert http.is_closed


def test_custom_message_is_trimmed_and_sent_without_client_side_times(monkeypatch):
    def handler(request):
        assert json.loads(request.content) == {"message": "지정한 공지"}
        return httpx.Response(201, json={**RESPONSE, "message": "지정한 공지"})
    install_http(monkeypatch, handler)
    assert module.schedule_maintenance.invoke({"message": "  지정한 공지  "})["message"] == "지정한 공지"


@pytest.mark.parametrize("args", [{"message": "가" * 501}, {"message": 123}, {"endsAt": "2026-11-18T03:00:00"}, {"agent_token": TOKEN}])
def test_invalid_input_never_creates_http_client(monkeypatch, args):
    monkeypatch.setattr(module.httpx, "Client", lambda: pytest.fail("입력 검증 전 호출 금지"))
    with pytest.raises(ValidationError):
        module.schedule_maintenance.invoke(args)


def test_message_max_length_is_500_characters(monkeypatch):
    install_http(monkeypatch, lambda _: httpx.Response(201, json={**RESPONSE, "message": "가" * 500}))
    assert len(module.schedule_maintenance.invoke({"message": "가" * 500})["message"]) == 500


@pytest.mark.parametrize("start", ["2026-11-17T15:27:00", None])
def test_optional_start_time_is_forwarded_only_when_present(monkeypatch, start):
    def handler(request):
        expected = {"message": module.DEFAULT_MAINTENANCE_MESSAGE}
        if start is not None:
            expected["startsAt"] = start
        assert json.loads(request.content) == expected
        return httpx.Response(201, json={**RESPONSE,
                "startsAt": start or RESPONSE["startsAt"],
                "endsAt": "2026-11-17T16:27:00" if start else RESPONSE["endsAt"]})
    install_http(monkeypatch, handler)
    assert module.schedule_maintenance.invoke({"startsAt": start})["status"] == "SCHEDULED"


@pytest.mark.parametrize("start", ["오늘 오후 3시", "2026-02-30T15:27:00", "2026-11-17T25:00:00",
                                   "2026-11-17T15:27:00+09:00", "2026-11-17", 123])
def test_invalid_start_time_never_sends_post(monkeypatch, start):
    monkeypatch.setattr(module.httpx, "Client", lambda: pytest.fail("잘못된 시각으로 예약 금지"))
    with pytest.raises(ValidationError):
        module.schedule_maintenance.invoke({"startsAt": start})


@pytest.mark.parametrize("status", [200, 204, 302, 400, 401, 403, 404, 405, 409, 429, 500, 503])
def test_http_error_or_unexpected_success_never_retries_or_leaks_response(monkeypatch, status):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, content=TOKEN, headers={"Location": "https://other.test"})
    install_http(monkeypatch, handler)
    with pytest.raises(ToolException) as error:
        module.schedule_maintenance.invoke({})
    assert len(calls) == 1
    assert TOKEN not in str(error.value)


@pytest.mark.parametrize("failure", [httpx.ReadTimeout, httpx.ConnectError])
def test_transport_error_is_unknown_outcome_without_retry(monkeypatch, failure):
    calls = []
    def handler(request):
        calls.append(request)
        raise failure(TOKEN, request=request)
    install_http(monkeypatch, handler)
    with pytest.raises(ToolException, match="저장 여부가 미확인") as error:
        module.schedule_maintenance.invoke({})
    assert len(calls) == 1
    assert TOKEN not in str(error.value)


@pytest.mark.parametrize("changes", [
    {"maintenanceId": 0}, {"status": "IN_PROGRESS"}, {"message": None},
    {"startsAt": "not-a-date"}, {"endsAt": RESPONSE["startsAt"]},
    {"startsAt": "2026-11-18T02:00:00+09:00"},
])
def test_invalid_saved_response_is_not_reported_as_confirmed_success(monkeypatch, changes):
    install_http(monkeypatch, lambda _: httpx.Response(201, json={**RESPONSE, **changes}))
    with pytest.raises(ToolException, match="성공 여부가 미확인"):
        module.schedule_maintenance.invoke({})


def test_non_json_saved_response_is_not_success(monkeypatch):
    install_http(monkeypatch, lambda _: httpx.Response(201, text="not JSON"))
    with pytest.raises(ToolException):
        module.schedule_maintenance.invoke({})


@pytest.mark.parametrize("setting,value", [
    ("MAINTENANCE_API_URL", ""), ("MAINTENANCE_API_URL", "https://user:pass@spring.test/api"),
    ("MAINTENANCE_API_URL", URL + "?token=secret"), ("AGENT_API_TOKEN", ""), ("AGENT_API_TOKEN", "bad\nheader"),
])
def test_invalid_settings_fail_before_request(monkeypatch, setting, value):
    monkeypatch.setenv(setting, value)
    with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("설정 오류로 요청 금지"))) as http:
        with pytest.raises(MaintenanceApiError):
            MaintenanceClient(http)
