"""모델 API 없이 조회·분석·원문 비저장과 실패 원자성을 검증한다."""

import json
from types import SimpleNamespace
from unittest.mock import Mock
import httpx
import pytest
from langchain_core.tools import ToolException
from jamkkanjeju_agent.clients.spring_client import SpringClient, GuestbooksError
from jamkkanjeju_agent.memory.store import analysis_store
from jamkkanjeju_agent.services.guestbook_analysis import analyze_guestbooks
from jamkkanjeju_agent.tools import guestbook_tools as tools


class Classifier:
    def classify(self, texts):
        return [SimpleNamespace(label="UNKNOWN" if not text or not text.strip()
                else "NEGATIVE" if text.startswith("나쁨") else "POSITIVE", score=0.9) for text in texts]


@pytest.fixture(autouse=True)
def clean():
    analysis_store.clear()
    yield
    analysis_store.clear()


def reviews():
    return [{"locationId": 1, "guestbookId": i + 1,
             "text": "나쁨 원문" if i < 7 else "좋음 원문" if i < 9 else None} for i in range(10)]


def test_tool_counts_all_reviews_without_saving_text(monkeypatch):
    spring = SimpleNamespace(get_stored_locations=lambda: [{"locationId": 1, "title": "장소"},
        {"locationId": 2, "title": "빈 장소"}], get_location_guestbooks=reviews)
    monkeypatch.setattr(tools, "get_clients", lambda: SimpleNamespace(spring=spring))
    monkeypatch.setattr(tools, "get_sentiment_classifier", Classifier)
    result = tools.analyze_location_guestbooks.invoke({})
    place = result["locations"][0]
    assert place["negative"] == list(range(1, 8))
    assert place["positive"] == [8, 9] and place["unknown"] == [10]
    assert place["totalCount"] == 10 and place["negativeRatio"] == 0.7
    assert place["negativeRatio"] >= 0.69
    assert result["locations"][1]["negativeRatio"] is None
    assert result["locations"][1]["totalCount"] == 0
    encoded = json.dumps(result, ensure_ascii=False)
    assert all(word not in encoded for word in ("원문", '"text"', '"sentiment"', '"score"'))
    result["locations"][0]["negative"].clear()
    assert len(analysis_store.get(result["snapshotId"])["locations"][0]["negative"]) == 7
    copy = analysis_store.get(result["snapshotId"])
    copy["locations"].clear()
    assert len(analysis_store.get(result["snapshotId"])["locations"]) == 2


@pytest.mark.parametrize("text", [None, "", " \n\t"])
def test_blank_is_unknown(text):
    result = analyze_guestbooks([{"locationId": 1, "title": "A"}],
        [{"locationId": 1, "guestbookId": 1, "text": text}], Classifier())[0]
    assert result["unknown"] == [1] and result["negativeRatio"] == 0


@pytest.mark.parametrize("command", ["/clear", "/exit"])
def test_cli_clears_analysis_snapshots(command):
    from jamkkanjeju_agent.chat import run_chat
    snapshot = analysis_store.save([])
    answers = iter([command, "/exit"])
    run_chat(None, read=lambda _: next(answers), write=lambda _: None)
    with pytest.raises(ToolException):
        analysis_store.get(snapshot["snapshotId"])


@pytest.mark.parametrize("failure", ["source", "model", "location", "duplicate"])
def test_failure_never_saves_partial_snapshot(monkeypatch, failure):
    rows = reviews()
    if failure == "location":
        rows[-1]["locationId"] = 999
    if failure == "duplicate":
        rows[-1]["guestbookId"] = 1
    fetch = Mock(side_effect=GuestbooksError("조회 실패")) if failure == "source" else lambda: rows
    classifier = Classifier()
    if failure == "model":
        classifier.classify = Mock(side_effect=RuntimeError("private text"))
    monkeypatch.setattr(tools, "get_clients", lambda: SimpleNamespace(spring=SimpleNamespace(
        get_stored_locations=lambda: [{"locationId": 1, "title": "A"}], get_location_guestbooks=fetch)))
    monkeypatch.setattr(tools, "get_sentiment_classifier", lambda: classifier)
    save = Mock()
    monkeypatch.setattr(analysis_store, "save", save)
    with pytest.raises(ToolException) as error:
        tools.analyze_location_guestbooks.invoke({})
    save.assert_not_called()
    assert "private text" not in str(error.value)


def test_client_reads_every_page_with_fixed_token():
    pages = []
    def handler(request):
        assert request.headers["X-Agent-Token"] == "private-test-token"
        assert "Authorization" not in request.headers
        assert request.url.path == "/api/v2/agent/guestbooks"
        page = int(request.url.params["page"])
        pages.append(page)
        rows = [{"locationId": 1, "guestbookId": i, "text": None} for i in range(1, 101)] if page == 0 else [
            {"locationId": 1, "guestbookId": 101, "text": "글"}]
        return httpx.Response(200, json={"guestbooks": rows,
            "guestbooksPage": {"page": page, "size": 100, "hasNext": page == 0}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        assert len(SpringClient(http).get_location_guestbooks()) == 101
    assert pages == [0, 1]


def test_later_page_failure_does_not_return_partial_data():
    def handler(request):
        page = int(request.url.params["page"])
        if page == 1:
            raise httpx.ReadTimeout("private response", request=request)
        return httpx.Response(200, json={"guestbooks": [
            {"locationId": 1, "guestbookId": i, "text": None} for i in range(1, 101)],
            "guestbooksPage": {"page": 0, "size": 100, "hasNext": True}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(GuestbooksError) as error:
            SpringClient(http).get_location_guestbooks()
    assert "private response" not in str(error.value)


@pytest.mark.parametrize("kind", ["http", "schema", "page", "short", "duplicate", "reverse", "nullid"])
def test_client_rejects_incomplete_or_invalid_source(kind):
    rows = [{"locationId": 1, "guestbookId": 1, "text": None}]
    info = {"page": 0, "size": 100, "hasNext": False}
    if kind == "page": info["page"] = 1
    if kind == "short": info["hasNext"] = True
    if kind == "duplicate": rows *= 2
    if kind == "reverse": rows = [dict(rows[0], guestbookId=2), rows[0]]
    if kind == "nullid": rows[0]["guestbookId"] = None
    def handler(request):
        if kind == "http": return httpx.Response(503, text="private text")
        if kind == "schema": return httpx.Response(200, json={})
        return httpx.Response(200, json={"guestbooks": rows, "guestbooksPage": info})
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(GuestbooksError) as error:
            SpringClient(http).get_location_guestbooks()
    assert "private text" not in str(error.value)
