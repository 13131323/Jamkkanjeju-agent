"""대화 명령과 실제 LangChain 그래프의 도구 실행·기록 관리를 외부 모델 없이 검증한다."""

import json
from collections.abc import Sequence

import httpx
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from jamkkanjeju_agent.agent import create_operations_agent
from jamkkanjeju_agent.chat import run_chat
from jamkkanjeju_agent.tools.stored_locations import create_get_stored_locations


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


def test_agent_executes_location_tool_and_remembers_follow_up():
    requests = []
    def handler(request):
        requests.append(request)
        page = int(request.url.params["page"])
        return httpx.Response(200, json={"locations": [], "locationsPage": {"page": page, "size": 2, "hasNext": page == 0}})
    tool = create_get_stored_locations(api_url="http://spring.test/api/v2/agent/locations", agent_token="private-test-token",
                                       client=httpx.Client(transport=httpx.MockTransport(handler)))
    model = ScriptedModel(responses=[
        AIMessage(content="", tool_calls=[{"name": "get_stored_locations", "args": {"page": 0, "size": 2}, "id": "first"}]),
        AIMessage(content="첫 페이지를 확인했습니다."),
        AIMessage(content="", tool_calls=[{"name": "get_stored_locations", "args": {"page": 1, "size": 2}, "id": "second"}]),
        AIMessage(content="다음 페이지를 확인했습니다."),
    ])
    code, output = interact(create_operations_agent(model=model, tools=[tool]), ["장소 2개 조회", "다음 페이지", "/exit"])
    assert code == 0
    assert [r.url.params["page"] for r in requests] == ["0", "1"]
    assert all(r.headers["X-Agent-Token"] == "private-test-token" for r in requests)
    assert any(isinstance(m, HumanMessage) and m.content == "장소 2개 조회" for m in model.seen[2])
    assert any(isinstance(m, ToolMessage) and json.loads(m.content)["locationsPage"]["page"] == 0 for m in model.seen[2])
    assert any("[도구 호출] get_stored_locations" in line for line in output)
    assert any("[도구 완료]" in line for line in output)
    assert any("다음 페이지를 확인했습니다." in line for line in output)


def test_tool_failure_is_reported_and_next_question_is_allowed():
    def handler(request):
        raise httpx.ConnectError("private-network-detail", request=request)
    tool = create_get_stored_locations(api_url="http://spring.test/api/v2/agent/locations", agent_token="private-test-token",
                                       client=httpx.Client(transport=httpx.MockTransport(handler)))
    model = ScriptedModel(responses=[
        AIMessage(content="", tool_calls=[{"name": "get_stored_locations", "args": {}, "id": "failed"}]),
        AIMessage(content="Spring 서버 연결에 실패해 장소를 확인하지 못했습니다."),
        AIMessage(content="추가 질문에 답변드립니다."),
    ])
    _, output = interact(create_operations_agent(model=model, tools=[tool]), ["장소 조회", "안녕", "/exit"])
    assert any("[도구 오류]" in line for line in output)
    assert any("추가 질문에 답변드립니다." in line for line in output)
    assert "private-network-detail" not in "\n".join(output)
    assert any(isinstance(m, ToolMessage) and m.status == "error" for m in model.seen[1])


def test_clear_removes_previous_conversation():
    model = ScriptedModel(responses=[AIMessage(content="첫 답변"), AIMessage(content="새 답변")])
    interact(create_operations_agent(model=model, tools=[]), ["이전 질문", "/clear", "새 질문", "/exit"])
    humans = [m.content for m in model.seen[1] if isinstance(m, HumanMessage)]
    assert humans == ["새 질문"]


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
