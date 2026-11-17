"""전체 DB 조회·스냅샷 생성·실패 시 비생성을 HTTP 대역으로 검증한다."""

import httpx
import pytest
from langchain_core.tools import ToolException
from pydantic import ValidationError

from jamkkanjeju_agent.clients.stored_locations import StoredLocationClient, StoredLocationsError
from jamkkanjeju_agent.snapshots import snapshot_store
from jamkkanjeju_agent.tools import stored_locations as module

LOCATION = {"locationId":1,"contentId":2853982,"title":"라토커피제주","address":"제주시",
            "category":"음식","radius":100,"latitude":33.5,"longitude":126.5}


def page(rows, number=0, has_next=False):
    return {"locations":rows,"locationsPage":{"page":number,"size":100,"hasNext":has_next}}


def client_read(handler):
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        return StoredLocationClient(http).get_locations()


def test_all_pages_are_collected_without_exposing_locations_to_llm(monkeypatch):
    requests=[]
    def handler(request):
        requests.append(request)
        assert request.headers["X-Agent-Token"] == "private-test-token"
        assert "Authorization" not in request.headers
        number=int(request.url.params["page"])
        assert request.url.params["size"]=="100"
        rows=[{**LOCATION,"locationId":i,"contentId":i} for i in range(1,101)] if number==0 else [{**LOCATION,"locationId":101,"contentId":101}]
        return httpx.Response(200,json=page(rows,number,number==0))
    real_client=httpx.Client
    monkeypatch.setattr(module.httpx,"Client",lambda:real_client(transport=httpx.MockTransport(handler)))
    result=module.get_stored_locations.invoke({})
    assert result["count"]==101 and result["source"]=="DB"
    assert "locations" not in result
    assert len(snapshot_store.get(result["snapshotId"],"DB").locations)==101
    assert [r.url.params["page"] for r in requests]==["0","1"]


def test_empty_db_is_a_successful_complete_snapshot(monkeypatch):
    monkeypatch.setattr(StoredLocationClient,"get_locations",lambda self:[])
    result=module.get_stored_locations.invoke({})
    assert result["status"]=="COMPLETE" and result["count"]==0


@pytest.mark.parametrize("data", [{}, page([{**LOCATION,"latitude":200}]),page([LOCATION],number=1),
                                  page([LOCATION],has_next=True),page([LOCATION,LOCATION])])
def test_invalid_response_never_becomes_complete(data):
    with pytest.raises(StoredLocationsError):
        client_read(lambda _:httpx.Response(200,json=data))


@pytest.mark.parametrize("status", [401,403,404,500,302])
def test_http_failure_does_not_leak_body_or_token(status):
    with pytest.raises(StoredLocationsError) as error:
        client_read(lambda _:httpx.Response(status,text="private-test-token"))
    assert "private-test-token" not in str(error.value)


@pytest.mark.parametrize("error_type", [httpx.ReadTimeout,httpx.ConnectError])
def test_transport_failure_is_not_empty(error_type):
    def handler(request):
        raise error_type("private-network-detail",request=request)
    with pytest.raises(StoredLocationsError) as error:
        client_read(handler)
    assert "private-network-detail" not in str(error.value)


def test_late_page_failure_publishes_no_snapshot(monkeypatch):
    save=[]
    def handler(request):
        if request.url.params["page"]=="0":
            return httpx.Response(200,json=page([{**LOCATION,"locationId":i,"contentId":i} for i in range(1,101)],has_next=True))
        return httpx.Response(500)
    real_client=httpx.Client
    monkeypatch.setattr(module.httpx,"Client",lambda:real_client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(snapshot_store,"save_complete",lambda *args:save.append(args))
    with pytest.raises(ToolException):
        module.get_stored_locations.invoke({})
    assert save==[]


def test_tool_schema_does_not_expose_page_raw_data_or_credentials():
    assert module.get_stored_locations.args == {}
    assert module.get_stored_locations.args_schema.model_json_schema()["additionalProperties"] is False


@pytest.mark.parametrize("setting,value",[("STORED_LOCATIONS_API_URL",""),("STORED_LOCATIONS_API_URL","file:///local"),("AGENT_API_TOKEN","")])
def test_invalid_config_fails_before_network(monkeypatch,setting,value):
    monkeypatch.setenv(setting,value)
    with httpx.Client() as http:
        with pytest.raises(StoredLocationsError):
            StoredLocationClient(http)
