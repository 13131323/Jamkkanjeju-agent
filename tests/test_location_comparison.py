"""스냅샷 참조와 비교 실패 차단을 검증한다."""

from jamkkanjeju_agent.clients.tourapi_client import TourApiClient, TourApiError
import jamkkanjeju_agent.clients as client_runtime


from unittest.mock import Mock

import pytest
from langchain_core.tools import ToolException
from pydantic import ValidationError

from jamkkanjeju_agent.memory.store import LocationSnapshotStore, snapshot_store
from jamkkanjeju_agent.tools import location_tools as module
from jamkkanjeju_agent.tools import location_tools as tour_tool


def pair(db=None,tour=None):
    a=snapshot_store.save_complete("DB", db if db is not None else [{"contentId":1,"title":"장모식탁"},{"contentId":2}])
    b=snapshot_store.save_complete("TOURAPI",tour if tour is not None else [{"contentId":2},{"contentId":3}])
    return {"storedSnapshotId":a["snapshotId"],"tourapiSnapshotId":b["snapshotId"]}


def test_tourapi_query_only_returns_snapshot_metadata(monkeypatch):
    monkeypatch.setattr(TourApiClient,"get_locations",lambda self:[{"contentId":1}])
    result=tour_tool.get_tourapi_locations.invoke({})
    assert result["count"]==1 and result["source"]=="TOURAPI"
    assert result["filters"]["lDongRegnCd"]=="50"
    assert "locations" not in result
    assert snapshot_store.get(result["snapshotId"],"TOURAPI").locations==({"contentId":1},)


def test_tourapi_failure_never_publishes_a_snapshot(monkeypatch):
    save=Mock()
    monkeypatch.setattr(snapshot_store,"save_complete",save)
    monkeypatch.setattr(TourApiClient,"get_locations",Mock(side_effect=TourApiError("조회 실패")))
    with pytest.raises(ToolException):
        tour_tool.get_tourapi_locations.invoke({})
    save.assert_not_called()


def test_compare_uses_snapshots_without_refetching_lists(monkeypatch):
    args=pair()
    monkeypatch.setattr(TourApiClient,"get_locations",lambda self:pytest.fail("목록 재조회 금지"))
    monkeypatch.setattr(TourApiClient,"get_location",lambda self,content_id:None)
    result=module.compare_locations.invoke(args)
    assert result["commonContentIds"]==[2]
    assert result["addedLocations"]==[{"contentId":3}]
    assert result["confirmedMissingLocations"][0]["contentId"]==1


def test_deletions_scope_does_not_send_added_locations(monkeypatch):
    monkeypatch.setattr(TourApiClient,"get_location",lambda self,content_id:None)
    result=module.compare_locations.invoke({**pair(),"resultType":"deletions"})
    assert result["addedCount"]==1
    assert "addedLocations" not in result
    assert result["confirmedMissingLocations"][0]["title"]=="장모식탁"


def test_additions_scope_does_not_query_missing_details(monkeypatch):
    monkeypatch.setattr(TourApiClient,"get_location",lambda *args:pytest.fail("추가 비교는 상세 재확인 불필요"))
    result=module.compare_locations.invoke({**pair(),"resultType":"additions"})
    assert result["addedLocations"]==[{"contentId":3}]
    assert "confirmedMissingLocations" not in result


def test_existing_detail_is_excluded_from_deletion_candidates(monkeypatch):
    monkeypatch.setattr(TourApiClient,"get_location",lambda self,content_id:{"contentId":content_id,"title":"장모식탁"})
    result=module.compare_locations.invoke(pair())
    assert result["confirmedMissingLocations"]==[]
    assert result["existingOutsideListLocations"][0]["tourapiLocation"]["title"]=="장모식탁"


def test_detail_failure_never_returns_partial_comparison(monkeypatch):
    monkeypatch.setattr(TourApiClient,"get_location",Mock(side_effect=TourApiError("조회 실패")))
    with pytest.raises(ToolException,match="비교 결과를 반환하지"):
        module.compare_locations.invoke(pair())


@pytest.mark.parametrize("mode",["unknown","reversed","cleared"])
def test_invalid_or_wrong_source_ids_fail_before_api_calls(monkeypatch,mode):
    args=pair()
    if mode=="unknown":args["storedSnapshotId"]="does-not-exist"
    if mode=="reversed":args={"storedSnapshotId":args["tourapiSnapshotId"],"tourapiSnapshotId":args["storedSnapshotId"]}
    if mode=="cleared":snapshot_store.clear()
    monkeypatch.setattr(client_runtime.httpx,"Client",lambda:pytest.fail("잘못된 스냅샷으로 외부 요청 금지"))
    with pytest.raises(ToolException):
        module.compare_locations.invoke(args)


@pytest.mark.parametrize("args",[{}, {"stored":[],"tourapi":[]}, {"storedSnapshotId":"x"}, {"storedSnapshotId":"x","tourapiSnapshotId":"y","resultType":"wrong"}])
def test_compare_requires_two_ids_and_valid_scope(args):
    with pytest.raises(ValidationError):
        module.compare_locations.invoke(args)


def test_store_copies_input_and_output_and_generates_distinct_ids():
    store=LocationSnapshotStore()
    rows=[{"contentId":1,"title":"원본"}]
    meta=store.save_complete("DB",rows)
    rows[0]["title"]="변경"
    result=store.get(meta["snapshotId"],"DB")
    result.locations[0]["title"]="또 변경"
    assert store.get(meta["snapshotId"],"DB").locations[0]["title"]=="원본"
    assert store.save_complete("DB",rows)["snapshotId"] != meta["snapshotId"]


@pytest.mark.parametrize("source,rows",[("TOURAPI",[]),("DB",[{"contentId":1},{"contentId":1}]),("DB",[{"contentId":True}])])
def test_invalid_snapshot_data_cannot_be_published(source,rows):
    with pytest.raises(ToolException):
        snapshot_store.save_complete(source,rows)
