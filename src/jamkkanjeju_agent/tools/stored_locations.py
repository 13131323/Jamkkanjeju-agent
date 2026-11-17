"""DB 장소 전체를 조회하여 완료된 메모리 스냅샷을 생성하는 Tool."""

import httpx
from langchain_core.tools import ToolException, tool

from ..clients.stored_locations import StoredLocationClient, StoredLocationsError
from ..snapshots import SnapshotQueryInput, snapshot_store


@tool("get_stored_locations", args_schema=SnapshotQueryInput)
def get_stored_locations() -> dict:
    """Spring DB 장소를 마지막 페이지까지 전체 조회하고 메모리에 스냅샷으로 보관한다.

    읽기 전용이며 입력은 없다. 장소 목록 대신 snapshotId·source=DB·count·createdAt·status를 반환한다.
    비교 요청에서는 먼저 이 Tool을 호출한 뒤 get_tourapi_locations를 호출한다.
    한 페이지라도 실패하면 스냅샷을 만들지 않는다. 페이지 정보나 토큰을 직접 전달하지 않는다.
    """
    try:
        with httpx.Client() as http:
            locations = StoredLocationClient(http).get_locations()
    except StoredLocationsError as exc:
        raise ToolException(str(exc)) from None
    return snapshot_store.save_complete("DB", locations)
