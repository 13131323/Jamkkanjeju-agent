"""TourAPI 클라이언트의 수집 조건·응답 검증·재시도를 검증한다."""

import httpx
import pytest

from jamkkanjeju_agent.clients import tourapi_client as module


def item(content_id=1, category="FD", **updates):
    return dict(dict(contentid=str(content_id), lclsSystm1=category, title=" 장소 ",
                addr1="제주시", addr2=" 시청길 ", mapx="126.5", mapy="33.5", lDongRegnCd="50"), **updates)


def response(items, total=None, page=1, code="0000"):
    return {"response": {"header": {"resultCode": code}, "body": {
        "totalCount": len(items) if total is None else total, "pageNo": page,
        "numOfRows": module.ROWS_PER_PAGE, "items": {"item": items}}}}


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    monkeypatch.setenv("KTO_BASE_URL", "https://tour.test/KorService2")
    monkeypatch.setenv("KTO_SERVICE_KEY", "secret-tour-key")
    monkeypatch.setattr(module.time, "sleep", lambda _: None)


def collect(handler):
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        return module.TourApiClient(client).get_locations()


def test_fastapi_selection_and_mapping():
    def handler(request):
        params = request.url.params
        assert request.url.path == "/KorService2/areaBasedList2"
        assert params["lDongRegnCd"] == "50"
        assert "areaCode" not in params
        assert params["numOfRows"] == "1000"
        assert "contentTypeId" not in params
        assert params["serviceKey"] == "secret-tour-key"
        return httpx.Response(200, json=response([
            item(i, code) for i, code in enumerate([*module.LOCATION_CATEGORIES, "AC", "HS", "NA"], 1)]))
    result = collect(handler)
    assert len(result) == 6
    assert [r["category"] for r in result] == list(module.LOCATION_CATEGORIES.values())
    assert result[0] == dict(contentId=1, title="장소", address="제주시 시청길", category="축제·공연·행사", radius=100, longitude=126.5, latitude=33.5)


def test_all_pages_before_filtering(monkeypatch):
    monkeypatch.setattr(module, "ROWS_PER_PAGE", 2)
    def handler(request):
        page = int(request.url.params["pageNo"])
        return httpx.Response(200, json=response([item(1), item(2, "AC")] if page == 1 else [item(3)], total=3, page=page))
    assert [r["contentId"] for r in collect(handler)] == [1, 3]


@pytest.mark.parametrize("payload", [
    response([], total=1), response([item()], code="0022"),
    response([item(), item()]), response([item(mapx="nan")]),
    {},
    response([item(1, "AC")]), response([item()], page=2),
    {"response": {"header": {"resultCode": "0000"}, "body": {"totalCount": 1}}},
])
def test_invalid_or_empty_source_never_becomes_empty_success(payload):
    with pytest.raises(module.TourApiError) as error:
        collect(lambda _: httpx.Response(200, json=payload))
    assert "secret-tour-key" not in str(error.value)


@pytest.mark.parametrize("status,attempts", [(429,3),(503,3),(401,1),(302,1)])
def test_bounded_http_retry(status, attempts):
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(status)
    with pytest.raises(module.TourApiError):
        collect(handler)
    assert len(calls) == attempts


def test_transient_failure_then_success():
    calls=[]
    def handler(request):
        calls.append(request)
        if len(calls)==1:
            raise httpx.ReadTimeout("secret-tour-key", request=request)
        return httpx.Response(200, json=response([item()]))
    assert len(collect(handler)) == 1
    assert len(calls)==2


def test_late_page_failure_and_count_drift(monkeypatch):
    monkeypatch.setattr(module, "ROWS_PER_PAGE", 1)
    for second in [httpx.Response(500), httpx.Response(200,json=response([item(2)],total=3,page=2))]:
        def handler(request):
            return httpx.Response(200,json=response([item()],total=2)) if request.url.params["pageNo"]=="1" else second
        with pytest.raises(module.TourApiError):
            collect(handler)


def test_constructor_does_not_call_api_or_close_injected_http_client():
    def unexpected_request(request):
        pytest.fail("객체 생성만으로 API를 호출하면 안 됩니다.")
    with httpx.Client(transport=httpx.MockTransport(unexpected_request)) as http:
        client = module.TourApiClient(http)
        assert not http.is_closed
        assert "secret-tour-key" not in repr(client)
    assert http.is_closed


def test_missing_key_fails_before_http_request(monkeypatch):
    monkeypatch.delenv("KTO_SERVICE_KEY")
    with httpx.Client() as http:
        with pytest.raises(module.TourApiError, match="KTO_BASE_URL과 KTO_SERVICE_KEY"):
            module.TourApiClient(http)


def test_actual_response_rows_can_be_smaller_than_requested_size():
    payload = response([item()])
    payload["response"]["body"]["numOfRows"] = 1
    assert len(collect(lambda _: httpx.Response(200, json=payload))) == 1


def test_actual_response_rows_on_last_page(monkeypatch):
    monkeypatch.setattr(module, "ROWS_PER_PAGE", 2)
    def handler(request):
        page = int(request.url.params["pageNo"])
        rows = [item(1), item(2)] if page == 1 else [item(3)]
        payload = response(rows, total=3, page=page)
        payload["response"]["body"]["numOfRows"] = len(rows)
        return httpx.Response(200, json=payload)
    assert len(collect(handler)) == 3


def test_short_page_is_still_rejected_even_if_num_rows_matches():
    payload = response([item()], total=2)
    payload["response"]["body"]["numOfRows"] = 1
    with pytest.raises(module.TourApiError, match="페이지 건수"):
        collect(lambda _: httpx.Response(200, json=payload))


def test_inconsistent_reported_rows_is_rejected():
    payload = response([item()])
    payload["response"]["body"]["numOfRows"] = 2
    with pytest.raises(module.TourApiError, match="numOfRows"):
        collect(lambda _: httpx.Response(200, json=payload))


def test_deprecated_area_code_can_be_empty_for_valid_jeju_location():
    row = item(2861967, title="장모식탁", areacode="")
    assert collect(lambda _: httpx.Response(200, json=response([row])))[0]["title"] == "장모식탁"


def test_wrong_legal_region_is_not_accepted_as_full_jeju_collection():
    with pytest.raises(module.TourApiError, match="법정동 지역코드"):
        collect(lambda _: httpx.Response(200, json=response([item(lDongRegnCd="11")])))


@pytest.mark.parametrize("exists", [True, False])
def test_detail_lookup_has_no_region_filter_and_distinguishes_normal_absence(exists):
    def handler(request):
        assert request.url.path == "/KorService2/detailCommon2"
        assert request.url.params["contentId"] == "2861967"
        assert "areaCode" not in request.url.params
        assert "lDongRegnCd" not in request.url.params
        return httpx.Response(200,json=response([item(2861967,title="장모식탁")] if exists else []))
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        result=module.TourApiClient(http).get_location(2861967)
    assert (result is not None) == exists
    if exists:
        assert result["title"] == "장모식탁"


@pytest.mark.parametrize("payload", [response([],code="0022"), response([],total=1), response([item(2)])])
def test_detail_error_or_mismatched_content_id_is_never_absence(payload):
    with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,json=payload))) as http:
        with pytest.raises(module.TourApiError):
            module.TourApiClient(http).get_location(2861967)


@pytest.mark.parametrize("exists", [True, False])
def test_individual_tool_reports_only_client_verified_existence(monkeypatch, exists):
    from jamkkanjeju_agent.tools.location_tools import get_tourapi_location
    detail = {"contentId":2861967,"title":"장모식탁"} if exists else None
    monkeypatch.setattr(module.TourApiClient, "get_location", lambda self, content_id: detail)
    result = get_tourapi_location.invoke({"contentId":2861967})
    assert result["exists"] is exists
    assert result["location"] == detail


@pytest.mark.parametrize("content_id", [0, -1, "2861967", True])
def test_individual_tool_rejects_invalid_content_id(content_id):
    from pydantic import ValidationError
    from jamkkanjeju_agent.tools.location_tools import get_tourapi_location
    with pytest.raises(ValidationError):
        get_tourapi_location.invoke({"contentId":content_id})
