"""모델·실제 DB 없이 Tool 스키마와 HTTP 연결·실패 처리를 검증한다."""

import httpx
import pytest
from langchain_core.tools import ToolException
from pydantic import ValidationError

from jamkkanjeju_agent.tools.stored_locations import create_get_stored_locations

URL = "https://spring.test/api/v2/agent/locations"
TOKEN = "private-test-token"
LOCATION = {
    "locationId": 1, "contentId": 2853982, "title": "라토커피제주",
    "address": "제주시", "category": "음식", "radius": 100,
    "latitude": 33.4987305369, "longitude": 126.5281672061,
}


def make_tool(handler, **settings):
    return create_get_stored_locations(
        api_url=settings.get("api_url", URL), agent_token=settings.get("agent_token", TOKEN),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_calls_get_with_auth_and_preserves_page_and_location_data():
    def handler(request):
        assert request.method == "GET"
        assert request.headers["X-Agent-Token"] == TOKEN
        assert "Authorization" not in request.headers
        assert dict(request.url.params) == {"page": "2", "size": "10"}
        return httpx.Response(200, json={"locations": [LOCATION], "locationsPage": {"page": 2, "size": 10, "hasNext": True}})

    result = make_tool(handler).invoke({"page": 2, "size": 10})
    assert result == {"locations": [LOCATION], "locationsPage": {"page": 2, "size": 10, "hasNext": True}}
    assert TOKEN not in str(result)


def test_empty_page_is_a_valid_result():
    result = make_tool(lambda _: httpx.Response(200, json={"locations": [], "locationsPage": {"page": 0, "size": 100, "hasNext": False}})).invoke({})
    assert result["locations"] == []
    assert result["locationsPage"]["hasNext"] is False


@pytest.mark.parametrize("input", [{"page": -1}, {"size": 0}, {"size": 101}, {"page": "1"}, {"page": True}, {"agent_token": "not-an-input"}])
def test_invalid_model_input_never_makes_a_request(input):
    def handler(_):
        pytest.fail("잘못된 입력으로 서버를 호출하면 안 됩니다.")
    with pytest.raises(ValidationError):
        make_tool(handler).invoke(input)


@pytest.mark.parametrize("status", [401, 403, 404, 500, 302])
def test_http_failure_is_not_an_empty_success_or_secret_leak(status):
    def handler(_):
        return httpx.Response(status, json={"message": TOKEN}, headers={"Location": "https://other.test"})
    with pytest.raises(ToolException) as error:
        make_tool(handler).invoke({})
    assert TOKEN not in str(error.value)


@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ReadTimeout])
def test_transport_failure_is_explicit(failure):
    def handler(request):
        raise failure("private-detail", request=request)
    with pytest.raises(ToolException) as error:
        make_tool(handler).invoke({})
    assert "private-detail" not in str(error.value)


@pytest.mark.parametrize("data", [
    {}, {"locations": [LOCATION], "locationsPage": {"page": 1, "size": 100, "hasNext": False}},
    {"locations": [{**LOCATION, "latitude": 200}], "locationsPage": {"page": 0, "size": 100, "hasNext": False}},
])
def test_invalid_response_does_not_become_success(data):
    with pytest.raises(ToolException):
        make_tool(lambda _: httpx.Response(200, json=data)).invoke({})


def test_non_json_response_fails():
    with pytest.raises(ToolException):
        make_tool(lambda _: httpx.Response(200, text="not JSON")).invoke({})


@pytest.mark.parametrize("settings", [{"api_url": ""}, {"api_url": "file:///local"}, {"agent_token": ""}])
def test_missing_settings_fail_before_network(settings):
    def handler(_):
        pytest.fail("설정이 없으면 서버를 호출하면 안 됩니다.")
    with pytest.raises(ToolException):
        make_tool(handler, **settings).invoke({})


def test_only_page_and_size_are_exposed_to_the_model():
    tool = make_tool(lambda _: httpx.Response(500))
    assert tool.name == "get_stored_locations"
    assert set(tool.args) == {"page", "size"}
    assert "읽기 전용" in tool.description


def test_reads_fixed_token_from_environment(monkeypatch):
    monkeypatch.setenv("STORED_LOCATIONS_API_URL", URL)
    monkeypatch.setenv("AGENT_API_TOKEN", TOKEN)
    def handler(request):
        assert request.headers["X-Agent-Token"] == TOKEN
        assert "Authorization" not in request.headers
        return httpx.Response(200, json={"locations": [], "locationsPage": {"page": 0, "size": 100, "hasNext": False}})
    tool = create_get_stored_locations(client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert tool.invoke({})["locations"] == []
