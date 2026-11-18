"""장소 조회·비교·승인 후 동기화를 제공하는 LangChain Tools."""

from threading import Lock
from typing import Literal

from langchain_core.tools import ToolException, tool
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..clients import get_clients
from ..clients.spring_client import AddedLocation, DeletedLocation, StoredLocationsError, LocationSyncError
from ..clients.tourapi_client import LOCATION_CATEGORIES, TourApiError
from ..memory.store import SnapshotQueryInput, snapshot_store, sync_plan_store
from ..services.location_comparison import compare_location_snapshots
from ..services.sync_approval import request_sync_approval


@tool("get_stored_locations", args_schema=SnapshotQueryInput)
def get_stored_locations() -> dict:
    """Spring DB 장소를 마지막 페이지까지 전체 조회하고 메모리에 스냅샷으로 보관한다.

    읽기 전용이며 입력은 없다. 장소 목록 대신 snapshotId·source=DB·count·createdAt·status를 반환한다.
    비교 요청에서는 먼저 이 Tool을 호출한 뒤 get_tourapi_locations를 호출한다.
    한 페이지라도 실패하면 스냅샷을 만들지 않는다. 페이지 정보나 토큰을 직접 전달하지 않는다.
    """
    try:
        locations = get_clients().spring.get_stored_locations()
    except StoredLocationsError as exc:
        raise ToolException(str(exc)) from None
    return snapshot_store.save_complete("DB", locations)


@tool("get_tourapi_locations", args_schema=SnapshotQueryInput)
def get_tourapi_locations() -> dict:
    """제주 법정동 코드 50과 기존 6개 분류 기준으로 TourAPI 장소를 전체 수집한다.

    읽기 전용이며 입력은 없다. 원본 목록은 메모리에 보관하고 snapshotId·source=TOURAPI·count를 반환한다.
    조회 실패·페이지 누락·검증 실패 시 스냅샷을 만들지 않으며 비교를 진행하면 안 된다.
    비교 요청에서는 DB 조회가 성공한 뒤 이 Tool을 호출하고, 반환된 두 ID로 compare_locations를 호출한다.
    """
    try:
        locations = get_clients().tourapi.get_locations()
    except TourApiError as exc:
        raise ToolException(str(exc)) from None
    return {**snapshot_store.save_complete("TOURAPI", locations),
            "filters": {"lDongRegnCd": "50", "lclsSystm1": list(LOCATION_CATEGORIES)}}


class TourapiLocationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contentId: int = Field(strict=True, gt=0, description="확인할 TourAPI 콘텐츠 ID")


@tool("get_tourapi_location", args_schema=TourapiLocationInput)
def get_tourapi_location(contentId: int) -> dict:
    """장소가 TourAPI에 실제 존재하는지 contentId로 직접 확인한다. 지역·분류 조건을 적용하지 않는다.

    사용자가 비교 결과에 이의를 제기하거나 특정 장소의 존재를 물으면 사용한다.
    contentId를 모르면 기존 조회 결과에서 확인하거나 사용자에게 물으며 임의로 만들지 않는다.
    정상 0건만 exists=false다. 조회 오류는 존재·삭제 여부 미확인이다.
    """
    try:
        location = get_clients().tourapi.get_location(contentId)
    except TourApiError as exc:
        raise ToolException(str(exc)) from None
    return {"contentId": contentId, "exists": location is not None, "location": location}


class ComparisonInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    storedSnapshotId: str = Field(strict=True, min_length=1, max_length=80, description="get_stored_locations가 반환한 DB snapshotId")
    tourapiSnapshotId: str = Field(strict=True, min_length=1, max_length=80, description="get_tourapi_locations가 반환한 TOURAPI snapshotId")
    resultType: Literal["all", "additions", "deletions"] = Field(default="all", description="원하는 결과 범위. 삭제 대상 질문은 deletions, 추가 대상 질문은 additions, 전체 비교는 all")


@tool("compare_locations", args_schema=ComparisonInput)
def compare_locations(storedSnapshotId: str, tourapiSnapshotId: str, resultType: str = "all") -> dict:
    """조회 Tool에서 받은 두 스냅샷 ID로 contentId 교집합·추가·삭제 검토 대상을 계산한다.

    DB·TourAPI 목록을 새로 조회하지 않는다. 실제로 발급된 서로 다른 출처의 스냅샷만 사용할 수 있다.
    삭제 검토는 DB에만 있는 장소를 detailCommon2로 재확인하며, 오류 시 비교 결과를 반환하지 않는다.
    confirmedMissingLocations는 상세 API에서도 정상 0건인 장소이며 폐업을 단정하지 않는다.
    existingOutsideListLocations는 상세에 존재하므로 삭제 후보에서 제외한다. 실제 DB를 변경하지 않는다.
    resultType=all이면 확인된 추가·삭제 결과를 메모리에 고정하고 planId를 반환한다.
    동기화 실행 요청에서는 이 planId를 synchronize_locations에 전달한다. 승인과 실제 반영은 그 Tool이 담당한다.
    """
    return compare_location_snapshots(storedSnapshotId, tourapiSnapshotId, resultType, get_clients())


class LocationSyncInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    planId: str = Field(strict=True, min_length=1, max_length=80,
                        description="compare_locations(resultType=all)가 반환한 planId. 스냅샷 ID나 장소 ID가 아니다.")


_execution_lock = Lock()
_approval_lock = Lock()
_executions: dict[str, dict] = {}


def clear_sync_executions() -> None:
    """CLI 대화 초기화 시 계획·실행 기록을 비운다. 서버 멱등성 기록은 별도다."""
    with _execution_lock:
        _executions.clear()
    sync_plan_store.clear()


@tool("synchronize_locations", args_schema=LocationSyncInput)
def synchronize_locations(planId: str) -> dict:
    """사용자가 장소 동기화 실행을 요청했을 때만 사용한다. 조회·비교 설명만 요청하면 실행하지 않는다.

    DB·TourAPI 조회 → compare_locations(resultType=all)에서 받은 planId로 바로 호출한다.
    목록 조회·집합 비교·상세 재확인을 반복하지 않고 해당 계획을 그대로 사용한다.
    이 Tool이 실제 CLI에 추가·삭제 전체 목록과 영향을 표시하고 y/n 승인을 한 번 받는다.
    호출 전에 LLM이 별도로 실행 허가를 묻거나 이중 승인을 요구하지 않는다.
    실제 사용자가 터미널의 승인 질문에 직접 동의하기 전에는 Spring 변경 API를 절대 호출하지 않는다.
    승인 여부나 장소 배열을 인자로 만들 수 없다. 반영 결과가 불명확하면 재호출하지 말고 서버 확인을 안내한다.
    Spring의 정상 완료 응답으로만 성공을 판단한다.
    """
    fixed_plan = sync_plan_store.get(planId)
    fingerprint = planId
    with _execution_lock:
        previous = _executions.get(fingerprint)
        if previous:
            if previous.get("status") == "COMPLETED":
                return {**previous, "alreadyExecuted": True}
            if previous.get("status") == "CANCELLED":
                return {**previous, "alreadyCancelled": True}
            raise ToolException("이 계획은 이미 실행 중이거나 반영 여부가 미확인입니다. 자동 재시도하지 말고 서버 처리 내역을 확인해 주세요.")

    try:
        additions = [AddedLocation.model_validate(row).model_dump() for row in fixed_plan.additions]
        deletions = [DeletedLocation.model_validate({"locationId": row.get("locationId"),
                      "contentId": row["contentId"]}).model_dump() for row in fixed_plan.deletions]
    except ValidationError:
        raise ToolException("동기화 대상 데이터가 저장 계약과 다릅니다. DB·TourAPI 장소를 다시 전체 조회해 주세요. 변경 API를 호출하지 않았습니다.") from None
    if not additions and not deletions:
        return {"status": "NO_CHANGES", "addedCount": 0, "deletedCount": 0}

    plan = {"requestId": fixed_plan.request_id, "storedSnapshotId": fixed_plan.stored_snapshot_id,
            "tourapiSnapshotId": fixed_plan.tourapi_snapshot_id, "addLocations": additions, "deleteLocations": deletions}
    report = {**plan, "planId": planId, "deletedLocationsReport": list(fixed_plan.deletions),
              "impact": "삭제 장소에 연결된 미션 비활성화, 관련 진행도·MISSION_REWARD_STATUS 삭제. 이미 지급한 보상은 회수하지 않습니다."}
    if not _approval_lock.acquire(blocking=False):
        raise ToolException("다른 장소 동기화 계획의 사용자 승인을 확인 중입니다. 동시에 승인 질문을 띄우지 않습니다.")
    try:
        approved = request_sync_approval(report)
    finally:
        _approval_lock.release()
    if not approved:
        result = {"status": "CANCELLED", "requestId": plan["requestId"], "message": "사용자가 승인하지 않아 변경 API를 호출하지 않았습니다."}
        with _execution_lock:
            _executions[fingerprint] = result
        return result

    with _execution_lock:
        if fingerprint in _executions:
            raise ToolException("같은 계획의 실행이 이미 시작됐습니다. 중복 호출하지 않습니다.")
        _executions[fingerprint] = {"status": "UNKNOWN", "requestId": plan["requestId"]}
    try:
        result = get_clients().spring.synchronize_locations(plan)
    except LocationSyncError as exc:
        raise ToolException(f"{exc} requestId={plan['requestId']}") from None
    with _execution_lock:
        _executions[fingerprint] = result
    return result

