"""장소 방명록 감성 집계와 선택한 방명록의 원문 조회 Tools."""

from typing import Annotated
from langchain_core.tools import ToolException, tool
from pydantic import BaseModel, ConfigDict, Field
from ..clients import get_clients
from ..clients.spring_client import GuestbooksError, StoredLocationsError
from ..memory.store import SnapshotQueryInput, analysis_store
from ..services.guestbook_analysis import analyze_guestbooks, get_sentiment_classifier


@tool("analyze_location_guestbooks", args_schema=SnapshotQueryInput)
def analyze_location_guestbooks() -> dict:
    """전체 장소의 ACTIVE 장소 방명록을 로컬 모델로 개별 분류한다. 입력은 없다.

    null·공백은 UNKNOWN, 글이 있으면 POSITIVE/NEGATIVE로 분류한다.
    snapshotId와 장소명·ID, positive/negative/unknown 방명록 ID 배열, 각각 건수,
    totalCount, negativeRatio(부정 수 / 미확인 포함 전체 수, 0~1)를 반환한다.
    방명록 없는 장소도 포함하며 비율은 null이다. 원문·개별 sentiment·점수는 저장하지 않는다.
    조회·분석이 모두 성공해야 메모리 스냅샷을 저장한다. DB는 변경하지 않는다.
    """
    try:
        spring = get_clients().spring
        locations = spring.get_stored_locations()
        guestbooks = spring.get_location_guestbooks()
        result = analyze_guestbooks(locations, guestbooks, get_sentiment_classifier())
    except (GuestbooksError, StoredLocationsError) as exc:
        raise ToolException(str(exc)) from None
    except Exception:
        raise ToolException("방명록 감성 분석에 실패했습니다. 모델 설치·로컬 캐시와 조회 데이터를 확인해 주세요. 분석 스냅샷을 생성하지 않았습니다.") from None
    return analysis_store.save(result)


class GuestbookTextsInput(BaseModel):
    """모델이 실제 분석 스냅샷과 조회할 방명록을 지정한다."""
    model_config = ConfigDict(extra="forbid")
    snapshotId: str = Field(strict=True, min_length=1, description="1번 분석 Tool이 반환한 snapshotId")
    guestbookIds: list[Annotated[int, Field(strict=True, gt=0)]] = Field(
        min_length=1, description="해당 스냅샷에서 원문을 조회할 방명록 ID 목록. 1개 또는 여러 개")


@tool("get_guestbook_texts", args_schema=GuestbookTextsInput)
def get_guestbook_texts(snapshotId: str, guestbookIds: list[int]) -> dict:
    """분석 스냅샷에서 선택한 방명록의 현재 원문을 조회한다. 하나 또는 여러 ID를 받는다.

    snapshotId와 guestbookIds는 1번 analyze_location_guestbooks의 실제 결과에서 선택한다.
    부정 비율 조건은 입력에 없다. 사용자가 조건을 말한 경우에만 1번 결과에서 해당 ID를 골라 전달한다.
    positive/negative/unknown 배열마다 guestbookId·locationId·locationName·text를 반환한다.
    분류와 장소명은 지정한 분석 시점의 결과를 사용하며 감성 모델을 다시 호출하지 않는다.
    조회 불가능한 ID는 notFoundIds에 별도로 반환한다. 미확인 감성으로 분류하지 않는다.
    스냅샷에 없는 ID는 거부한다. 통신 실패 시 부분 결과를 반환하지 않는다.
    원문은 Tool 응답으로만 전달하며 분석 스냅샷에 저장하지 않는다.
    """
    snapshot = analysis_store.get(snapshotId)
    selected_ids = list(dict.fromkeys(guestbookIds))
    index = {}
    for location in snapshot["locations"]:
        for label in ("positive", "negative", "unknown"):
            for guestbook_id in location[label]:
                index[guestbook_id] = (label, location["locationId"], location["locationName"])
    if any(guestbook_id not in index for guestbook_id in selected_ids):
        raise ToolException("지정한 분석 스냅샷에 없는 방명록 ID가 있습니다. 1번 Tool의 실제 결과에서 선택해 주세요.")
    try:
        rows = get_clients().spring.get_guestbook_texts(selected_ids)
    except GuestbooksError as exc:
        raise ToolException(str(exc)) from None
    found = {row["guestbookId"]: row for row in rows}
    result = {"positive": [], "negative": [], "unknown": [], "notFoundIds": []}
    for guestbook_id in selected_ids:
        if guestbook_id not in found:
            result["notFoundIds"].append(guestbook_id)
            continue
        row = found[guestbook_id]
        label, location_id, location_name = index[guestbook_id]
        if row["locationId"] != location_id:
            raise ToolException("분석 이후 방명록의 장소 연결이 변경되었습니다. 다시 분석해 주세요.")
        result[label].append({"guestbookId": guestbook_id, "locationId": location_id,
                              "locationName": location_name, "text": row["text"]})
    return result
