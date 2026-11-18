"""대화 명령과 실제 LangChain 그래프의 도구 실행·기록 관리를 외부 모델 없이 검증한다."""

import jamkkanjeju_agent.clients as client_runtime


import json
from collections.abc import Sequence

import httpx
import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from jamkkanjeju_agent.agent import create_operations_agent
from jamkkanjeju_agent import agent as agent_module
from jamkkanjeju_agent.chat import run_chat
from types import SimpleNamespace
from jamkkanjeju_agent.clients.spring_client import SpringClient, StoredLocationsError
from jamkkanjeju_agent.clients.tourapi_client import TourApiClient
from jamkkanjeju_agent.memory.store import snapshot_store
from jamkkanjeju_agent.memory import store as snapshots_module
from jamkkanjeju_agent.tools import get_stored_locations
from jamkkanjeju_agent.tools import maintenance_tools as maintenance_tool


class ScriptedModel(BaseChatModel):
    responses: list[AIMessage]
    seen: list[list[BaseMessage]] = Field(default_factory=list, exclude=True)

    @property
    def _llm_type(self):
        return "scripted-test"

    def bind_tools(self, tools: Sequence, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=self.responses.pop(0))])


def interact(agent, prompts):
    inputs = iter(prompts)
    output = []
    def read(_):
        try:
            return next(inputs)
        except StopIteration:
            raise EOFError from None
    code = run_chat(agent, read=read, write=output.append)
    return code, output


@pytest.mark.parametrize("decision,post_count", [("y", 1), ("n", 0)])
def test_cli_real_input_is_required_before_sync_post(monkeypatch, decision, post_count):
    from jamkkanjeju_agent.tools import synchronize_locations

    def row(content_id):
        return {"contentId": content_id, "title": f"동기화 장소 {content_id}", "address": "제주시",
                "category": "음식", "radius": 100, "latitude": 33.5, "longitude": 126.5}
    db = snapshot_store.save_complete("DB", [{**row(1), "locationId": 11}])
    tour = snapshot_store.save_complete("TOURAPI", [row(2)])
    monkeypatch.setattr(TourApiClient, "get_location", lambda self, content_id: None)
    from jamkkanjeju_agent.tools.location_tools import compare_locations
    compared = compare_locations.invoke({"storedSnapshotId": db["snapshotId"], "tourapiSnapshotId": tour["snapshotId"], "resultType": "all"})
    args = {"planId": compared["planId"]}
    calls = []
    original = httpx.Client
    def handler(request):
        calls.append(request)
        plan = json.loads(request.content)
        return httpx.Response(200, json={"requestId": plan["requestId"], "status": "COMPLETED",
                "addedCount": 1, "deletedCount": 1, "addedContentIds": [2], "deletedContentIds": [1]})
    monkeypatch.setattr(client_runtime.httpx, "Client", lambda: original(transport=httpx.MockTransport(handler)))
    model = ScriptedModel(responses=[
        AIMessage(content="", tool_calls=[{"name": "synchronize_locations", "args": args, "id": "sync"}]),
        AIMessage(content="실행 결과를 확인했습니다."),
    ])
    _, output = interact(create_operations_agent(model=model, tools=[synchronize_locations]),
                         ["장소를 동기화해 줘", decision, "/exit"])
    assert len(calls) == post_count
    assert any("[장소 동기화 실행 전 확인]" in line for line in output)
    assert any('[추가]' in line and '동기화 장소 2' in line for line in output)
    assert any('[삭제]' in line and '동기화 장소 1' in line for line in output)
    result = next(message for message in model.seen[1] if isinstance(message, ToolMessage))
    assert json.loads(result.content)["status"] == ("COMPLETED" if post_count else "CANCELLED")


def test_sync_full_flow_compares_once_then_reports_and_accepts_one_yes_no_stage(monkeypatch):
    calls = []
    monkeypatch.setattr(snapshots_module, "uuid4", lambda: SimpleNamespace(hex="a" * 32))
    def row(content_id):
        return {"contentId": content_id, "title": f"장소 {content_id}", "address": "제주시",
                "category": "음식", "radius": 100, "latitude": 33.5, "longitude": 126.5}
    def db(self):
        calls.append("DB")
        return [{**row(1), "locationId": 11}]
    def tour(self):
        calls.append("TOURAPI")
        return [row(2)]
    def detail(self, content_id):
        calls.append("DETAIL")
        return None
    def execute(self, plan):
        calls.append("SPRING")
        return {"requestId": plan["requestId"], "status": "COMPLETED", "addedCount": 1,
                "deletedCount": 1, "addedContentIds": [2], "deletedContentIds": [1]}
    monkeypatch.setattr(SpringClient, "get_stored_locations", db)
    monkeypatch.setattr(TourApiClient, "get_locations", tour)
    monkeypatch.setattr(TourApiClient, "get_location", detail)
    monkeypatch.setattr(SpringClient, "synchronize_locations", execute)
    model = ScriptedModel(responses=[
        AIMessage(content="", tool_calls=[{"name": "get_stored_locations", "args": {}, "id": "db"}]),
        AIMessage(content="", tool_calls=[{"name": "get_tourapi_locations", "args": {}, "id": "tour"}]),
        AIMessage(content="", tool_calls=[{"name": "compare_locations", "args": {
            "storedSnapshotId": "db-" + "a" * 32, "tourapiSnapshotId": "tourapi-" + "a" * 32, "resultType": "all"}, "id": "compare"}]),
        AIMessage(content="", tool_calls=[{"name": "synchronize_locations", "args": {"planId": "sync-plan-" + "a" * 32}, "id": "sync"}]),
        AIMessage(content="동기화 완료"),
    ])
    prompts = []
    inputs = iter(["장소 동기화해줘", "y", "/exit"])
    output = []
    def read(prompt):
        prompts.append(prompt)
        return next(inputs)
    run_chat(create_operations_agent(model=model), read=read, write=output.append)
    assert calls == ["DB", "TOURAPI", "DETAIL", "SPRING"]
    assert len([prompt for prompt in prompts if "(y/n)" in prompt]) == 1
    assert any('[추가]' in line for line in output) and any('[삭제]' in line for line in output)


def test_invalid_approval_input_is_reprompted_instead_of_cancelling(monkeypatch):
    from jamkkanjeju_agent.services.sync_approval import request_sync_approval
    class ApprovalAgent:
        def stream(self, *args, **kwargs):
            approved = request_sync_approval({"requestId": "test", "addLocations": [],
                "deleteLocations": [], "deletedLocationsReport": [], "impact": "영향"})
            assert approved is True
            yield {"model": {"messages": [AIMessage(content="완료")]}}
    _, output = interact(ApprovalAgent(), ["동기화해줘", "승이", " Y ", "/exit"])
    assert sum("y 또는 n" in line for line in output) == 1


def test_model_receives_fresh_kst_date_for_relative_time_on_every_turn(monkeypatch):
    times = iter(["2026-11-17T15:20:00+09:00", "2026-11-18T00:01:00+09:00"])
    monkeypatch.setattr(agent_module, "current_kst_time", lambda: next(times))
    model = ScriptedModel(responses=[AIMessage(content="첫 답변"), AIMessage(content="다음 답변")])
    interact(create_operations_agent(model=model, tools=[]), ["오늘 날짜", "지금 날짜", "/exit"])
    assert "2026-11-17T15:20:00+09:00" in model.seen[0][0].content
    assert "2026-11-18T00:01:00+09:00" in model.seen[1][0].content
    assert "2026-11-17T15:20:00+09:00" not in model.seen[1][0].content


@pytest.mark.parametrize("status", [201, 404])
def test_default_agent_routes_schedule_tool_and_reports_real_result(monkeypatch, status):
    monkeypatch.setenv("MAINTENANCE_API_URL", "http://spring.test/api/v2/agent/maintenances")
    monkeypatch.setenv("AGENT_API_TOKEN", "private-agent-token")
    requests = []
    result = {
        "maintenanceId": 1, "message": maintenance_tool.DEFAULT_MAINTENANCE_MESSAGE,
        "startsAt": "2026-11-18T02:00:00", "endsAt": "2026-11-18T03:00:00", "status": "SCHEDULED",
    }

    def handler(request):
        requests.append(request)
        return httpx.Response(status, json=result if status == 201 else {"message": "not implemented"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(client_runtime.httpx, "Client", lambda: http)
    model = ScriptedModel(responses=[
        AIMessage(content="", tool_calls=[{"name": "schedule_maintenance", "args": {}, "id": "schedule"}]),
        AIMessage(content="예약 등록을 확인했습니다." if status == 201 else "예약 API가 아직 제공되지 않습니다."),
    ])
    # 도구 목록을 주입하지 않아 실제 기본 에이전트에 등록됐는지도 함께 검증한다.
    _, output = interact(create_operations_agent(model=model), ["점검 예약해 줘", "/exit"])
    assert len(requests) == 1
    assert json.loads(requests[0].content) == {"message": maintenance_tool.DEFAULT_MAINTENANCE_MESSAGE}
    message = next(m for m in model.seen[1] if isinstance(m, ToolMessage))
    if status == 201:
        assert json.loads(message.content) == result
        assert any("[도구 완료] schedule_maintenance" in line for line in output)
    else:
        assert message.status == "error"
        assert "점검 예약 API가 제공되지 않습니다" in message.content
        assert any("[도구 오류]" in line for line in output)


def test_agent_calls_db_then_tourapi_then_snapshot_comparison(monkeypatch):
    calls=[]
    monkeypatch.setattr(snapshots_module,"uuid4",lambda:SimpleNamespace(hex="fixed"))
    def db(self):
        calls.append("DB")
        return [{"contentId":1,"title":"DB 장소"}]
    def tour(self):
        calls.append("TOURAPI")
        return [{"contentId":2,"title":"추가 장소"}]
    def detail(self,content_id):
        calls.append("DETAIL")
        return None
    monkeypatch.setattr(SpringClient,"get_stored_locations",db)
    monkeypatch.setattr(TourApiClient,"get_locations",tour)
    monkeypatch.setattr(TourApiClient,"get_location",detail)
    model=ScriptedModel(responses=[
        AIMessage(content="",tool_calls=[{"name":"get_stored_locations","args":{},"id":"db"}]),
        AIMessage(content="",tool_calls=[{"name":"get_tourapi_locations","args":{},"id":"tour"}]),
        AIMessage(content="",tool_calls=[{"name":"compare_locations","args":{"storedSnapshotId":"db-fixed","tourapiSnapshotId":"tourapi-fixed","resultType":"deletions"},"id":"compare"}]),
        AIMessage(content="DB 장소는 개별 상세 조회에서도 0건으로 확인됐습니다."),
        AIMessage(content="앞선 결과를 확인했습니다."),
    ])
    code,output=interact(create_operations_agent(model=model),["삭제될 장소 뽑아줘","앞선 결과 설명해줘","/exit"])
    assert code==0
    assert calls==["DB","TOURAPI","DETAIL"]
    for name in ("get_stored_locations","get_tourapi_locations","compare_locations"):
        assert any("[도구 호출] "+name in line for line in output)
    summaries=[json.loads(m.content) for m in model.seen[2] if isinstance(m,ToolMessage)]
    assert [m["snapshotId"] for m in summaries]==["db-fixed","tourapi-fixed"]
    assert all("locations" not in m for m in summaries)
    comparison=next(json.loads(m.content) for m in model.seen[3] if isinstance(m,ToolMessage) and m.name=="compare_locations")
    assert comparison["confirmedMissingLocations"]==[{"contentId":1,"title":"DB 장소"}]
    assert "addedLocations" not in comparison
    assert any(isinstance(m,HumanMessage) and m.content=="삭제될 장소 뽑아줘" for m in model.seen[4])


def test_tool_failure_is_reported_and_no_snapshot_comparison_is_called(monkeypatch):
    def fail_collection(self):
        raise StoredLocationsError("Spring 서버 연결 실패")
    monkeypatch.setattr(SpringClient,"get_stored_locations",fail_collection)
    monkeypatch.setattr(TourApiClient,"get_locations",lambda self:pytest.fail("DB 실패 후 TourAPI 조회 금지"))
    model=ScriptedModel(responses=[
        AIMessage(content="",tool_calls=[{"name":"get_stored_locations","args":{},"id":"failed"}]),
        AIMessage(content="DB 조회 실패로 비교를 중단했습니다."),
        AIMessage(content="추가 질문에 답변드립니다."),
    ])
    _,output=interact(create_operations_agent(model=model),["삭제될 장소 조회","안녕","/exit"])
    assert any("[도구 오류]" in line for line in output)
    assert any("추가 질문에 답변드립니다." in line for line in output)
    assert any(isinstance(m,ToolMessage) and m.status=="error" for m in model.seen[1])


def test_clear_removes_previous_conversation():
    old_snapshot=snapshot_store.save_complete("DB",[])
    model = ScriptedModel(responses=[AIMessage(content="첫 답변"), AIMessage(content="새 답변")])
    interact(create_operations_agent(model=model, tools=[]), ["이전 질문", "/clear", "새 질문", "/exit"])
    humans = [m.content for m in model.seen[1] if isinstance(m, HumanMessage)]
    assert humans == ["새 질문"]
    from langchain_core.tools import ToolException
    with pytest.raises(ToolException):
        snapshot_store.get(old_snapshot["snapshotId"],"DB")


def test_help_empty_input_and_exit_do_not_call_model():
    model = ScriptedModel(responses=[])
    code, _ = interact(create_operations_agent(model=model, tools=[]), ["", "/help", "/unknown", "exit"])
    assert code == 0
    assert model.seen == []


def test_failed_turn_does_not_leave_incomplete_tool_calls_in_history():
    class InterruptedAgent:
        def __init__(self):
            self.calls = []
        def stream(self, state, **_):
            self.calls.append(state["messages"])
            if len(self.calls) == 1:
                yield {"model": {"messages": [AIMessage(content="", tool_calls=[{"name": "tool", "args": {}, "id": "unfinished"}])]}}
                raise RuntimeError("secret-debug-detail")
            yield {"model": {"messages": [AIMessage(content="복구 완료")]}}
    agent = InterruptedAgent()
    _, output = interact(agent, ["실패 요청", "다음 요청"])
    assert len(agent.calls[1]) == 1
    assert agent.calls[1][0].content == "다음 요청"
    assert "secret-debug-detail" not in "\n".join(output)
    assert any("복구 완료" in line for line in output)


def test_session_retains_whole_turns_and_caps_history():
    model = ScriptedModel(responses=[AIMessage(content=f"답변 {i}") for i in range(12)])
    interact(create_operations_agent(model=model, tools=[]), [f"질문 {i}" for i in range(12)])
    humans = [m.content for m in model.seen[-1] if isinstance(m, HumanMessage)]
    assert humans == [f"질문 {i}" for i in range(1, 12)]


def test_terminal_output_redacts_configured_secrets(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "private-key-sentinel")
    model = ScriptedModel(responses=[AIMessage(content="private-key-sentinel")])
    _, output = interact(create_operations_agent(model=model, tools=[]), ["질문"])
    assert "private-key-sentinel" not in "\n".join(output)
    assert any("[비밀 정보 제외]" in line for line in output)
