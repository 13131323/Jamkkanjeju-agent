"""도메인 규칙에 맞는 TourAPI 장소 전체를 스냅샷으로 저장하는 Tool."""

import httpx
from langchain_core.tools import ToolException, tool

from ..clients.tourapi import LOCATION_CATEGORIES, TourApiClient, TourApiError
from ..snapshots import SnapshotQueryInput, snapshot_store


@tool("get_tourapi_locations", args_schema=SnapshotQueryInput)
def get_tourapi_locations() -> dict:
    """제주 법정동 코드 50과 기존 6개 분류 기준으로 TourAPI 장소를 전체 수집한다.

    읽기 전용이며 입력은 없다. 원본 목록은 메모리에 보관하고 snapshotId·source=TOURAPI·count를 반환한다.
    조회 실패·페이지 누락·검증 실패 시 스냅샷을 만들지 않으며 비교를 진행하면 안 된다.
    비교 요청에서는 DB 조회가 성공한 뒤 이 Tool을 호출하고, 반환된 두 ID로 compare_locations를 호출한다.
    """
    try:
        with httpx.Client() as http:
            locations = TourApiClient(http).get_locations()
    except TourApiError as exc:
        raise ToolException(str(exc)) from None
    return {**snapshot_store.save_complete("TOURAPI", locations),
            "filters": {"lDongRegnCd": "50", "lclsSystm1": list(LOCATION_CATEGORIES)}}
