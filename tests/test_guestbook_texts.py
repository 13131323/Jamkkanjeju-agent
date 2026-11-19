"""두 번째 Tool의 선택 원문 조회·감성 그룹 유지·일괄 요청 실패를 검증한다."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from langchain_core.tools import ToolException
from pydantic import ValidationError

from jamkkanjeju_agent.clients.spring_client import SpringClient, GuestbooksError
from jamkkanjeju_agent.memory.store import analysis_store
from jamkkanjeju_agent.tools import guestbook_tools as tools


@pytest.fixture
def snapshot():
    analysis_store.clear()
    result = analysis_store.save([{
        "locationId": 3, "locationName": "장소A", "positive": [1], "negative": [2, 4], "unknown": [5],
        "positiveCount": 1, "negativeCount": 2, "unknownCount": 1, "totalCount": 4, "negativeRatio": 0.5,
    }])
    yield result
    analysis_store.clear()


def test_groups_single_or_many_without_reclassifying_or_storing_text(monkeypatch, snapshot):
    fetch = Mock(return_value=[{"guestbookId": 1, "locationId": 3, "text": "현재 긍정 원문"},
        {"guestbookId": 2, "locationId": 3, "text": "이후 수정된 원문"},
        {"guestbookId": 5, "locationId": 3, "text": None}])
    monkeypatch.setattr(tools, "get_clients", lambda: SimpleNamespace(spring=SimpleNamespace(get_guestbook_texts=fetch)))
    monkeypatch.setattr(tools, "get_sentiment_classifier", lambda: pytest.fail("원문 조회에서 모델을 호출하면 안 됩니다."))
    result = tools.get_guestbook_texts.invoke({"snapshotId": snapshot["snapshotId"], "guestbookIds": [5, 2, 1, 4, 2]})
    assert result == {"positive": [{"guestbookId": 1, "locationId": 3, "locationName": "장소A", "text": "현재 긍정 원문"}],
        "negative": [{"guestbookId": 2, "locationId": 3, "locationName": "장소A", "text": "이후 수정된 원문"}],
        "unknown": [{"guestbookId": 5, "locationId": 3, "locationName": "장소A", "text": None}], "notFoundIds": [4]}
    fetch.assert_called_once_with([5, 2, 1, 4])
    assert analysis_store.get(snapshot["snapshotId"]) == snapshot
    assert '"text"' not in json.dumps(analysis_store.get(snapshot["snapshotId"]))
    fetch.return_value = [{"guestbookId": 2, "locationId": 3, "text": "원문"}]
    one = tools.get_guestbook_texts.invoke({"snapshotId": snapshot["snapshotId"], "guestbookIds": [2]})
    assert len(one["negative"]) == 1 and one["positive"] == [] and one["unknown"] == []


@pytest.mark.parametrize("case", ["unknown_snapshot", "outside_snapshot"])
def test_invalid_snapshot_selection_never_calls_api(monkeypatch, snapshot, case):
    monkeypatch.setattr(tools, "get_clients", lambda: pytest.fail("잘못된 스냅샷으로 API를 호출하면 안 됩니다."))
    with pytest.raises(ToolException):
        tools.get_guestbook_texts.invoke({"snapshotId": "fake" if case == "unknown_snapshot" else snapshot["snapshotId"],
                                         "guestbookIds": [999] if case == "outside_snapshot" else [1]})


@pytest.mark.parametrize("ids", [[], [0], [-1], [True], ["1"], [None]])
def test_invalid_ids_are_rejected(snapshot, ids):
    with pytest.raises(ValidationError):
        tools.get_guestbook_texts.invoke({"snapshotId": snapshot["snapshotId"], "guestbookIds": ids})


def test_location_change_or_http_failure_is_not_missing(monkeypatch, snapshot):
    fetch = Mock(return_value=[{"guestbookId": 1, "locationId": 999, "text": "원문"}])
    monkeypatch.setattr(tools, "get_clients", lambda: SimpleNamespace(spring=SimpleNamespace(get_guestbook_texts=fetch)))
    with pytest.raises(ToolException, match="장소 연결"):
        tools.get_guestbook_texts.invoke({"snapshotId": snapshot["snapshotId"], "guestbookIds": [1]})
    fetch.side_effect = GuestbooksError("API 오류")
    with pytest.raises(ToolException, match="API 오류"):
        tools.get_guestbook_texts.invoke({"snapshotId": snapshot["snapshotId"], "guestbookIds": [1]})
    assert analysis_store.get(snapshot["snapshotId"]) == snapshot


def test_client_chunks_ids_and_returns_only_selected_rows():
    requests = []
    def handler(request):
        ids = list(map(int, request.url.params["guestbookIds"].split(",")))
        requests.append(ids)
        assert request.headers["X-Agent-Token"] == "private-test-token"
        assert "Authorization" not in request.headers
        assert request.url.params["page"] == "0" and request.url.params["size"] == "100"
        return httpx.Response(200, json={"guestbooks": [
            {"locationId": 3, "guestbookId": i, "text": None if i == 1 else "원문"} for i in ids if i != 101],
            "guestbooksPage": {"page": 0, "size": 100, "hasNext": False}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        rows = SpringClient(http).get_guestbook_texts(list(range(1, 206)) + [1])
    assert list(map(len, requests)) == [100, 100, 5]
    assert len(rows) == 204 and rows[0]["text"] is None


@pytest.mark.parametrize("case", ["http", "timeout", "schema", "unexpected_id", "duplicate", "page", "has_next", "size"])
def test_client_invalid_response_fails_without_raw_text(case):
    def handler(request):
        if case == "http": return httpx.Response(503, text="private response")
        if case == "timeout": raise httpx.ReadTimeout("private response", request=request)
        if case == "schema": return httpx.Response(200, json={"private response": "secret"})
        ids = [999] if case == "unexpected_id" else [1, 1] if case == "duplicate" else [1]
        return httpx.Response(200, json={"guestbooks": [{"locationId": 3, "guestbookId": i, "text": "private response"} for i in ids],
            "guestbooksPage": {"page": 1 if case == "page" else 0, "size": 50 if case == "size" else 100,
                               "hasNext": case == "has_next"}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(GuestbooksError) as error:
            SpringClient(http).get_guestbook_texts([1])
    assert "private response" not in str(error.value)


def test_second_batch_failure_does_not_return_first_batch():
    def handler(request):
        ids = list(map(int, request.url.params["guestbookIds"].split(",")))
        if ids[0] == 101: return httpx.Response(429)
        return httpx.Response(200, json={"guestbooks": [{"locationId": 3, "guestbookId": i, "text": "원문"} for i in ids],
            "guestbooksPage": {"page": 0, "size": 100, "hasNext": False}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(GuestbooksError, match="429"):
            SpringClient(http).get_guestbook_texts(list(range(1, 102)))
