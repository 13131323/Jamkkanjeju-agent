"""완료된 DB·TourAPI 스냅샷을 비교하고 삭제 검토 후보를 개별 재확인한다."""

from typing import Literal

import httpx
from langchain_core.tools import ToolException, tool
from pydantic import BaseModel, ConfigDict, Field

from ..clients.tourapi import TourApiClient, TourApiError
from ..snapshots import snapshot_store


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
    """
    stored_snapshot = snapshot_store.get(storedSnapshotId, "DB")
    tourapi_snapshot = snapshot_store.get(tourapiSnapshotId, "TOURAPI")
    stored = {row["contentId"]: row for row in stored_snapshot.locations}
    tourapi = {row["contentId"]: row for row in tourapi_snapshot.locations}
    common_ids = sorted(stored.keys() & tourapi.keys())
    added_ids = sorted(tourapi.keys() - stored.keys())
    missing_ids = sorted(stored.keys() - tourapi.keys())
    verified = {}
    if resultType in {"all", "deletions"} and missing_ids:
        try:
            with httpx.Client() as http:
                client = TourApiClient(http)
                verified = {content_id: client.get_location(content_id) for content_id in missing_ids}
        except TourApiError as exc:
            raise ToolException(f"{exc} 개별 확인에 실패하여 비교 결과를 반환하지 않았습니다.") from None

    result = {
        "status": "COMPLETE", "resultType": resultType,
        "storedSnapshot": stored_snapshot.summary(), "tourapiSnapshot": tourapi_snapshot.summary(),
        "storedCount": len(stored), "tourapiCount": len(tourapi),
        "commonCount": len(common_ids), "addedCount": len(added_ids), "missingCount": len(missing_ids),
    }
    if resultType == "all":
        result["commonContentIds"] = common_ids
    if resultType in {"all", "additions"}:
        result["addedLocations"] = [tourapi[i] for i in added_ids]
    if resultType in {"all", "deletions"}:
        result["missingLocations"] = [stored[i] for i in missing_ids]
        result["confirmedMissingLocations"] = [stored[i] for i in missing_ids if verified[i] is None]
        result["existingOutsideListLocations"] = [
            {"storedLocation": stored[i], "tourapiLocation": verified[i]}
            for i in missing_ids if verified[i] is not None
        ]
        result["missingLocationsMeaning"] = "missingLocations는 목록 차집합일 뿐 삭제 확정 아님. confirmedMissingLocations만 개별 상세 조회에서도 정상 0건으로 확인됨. existingOutsideListLocations는 존재하므로 삭제 후보 아님."
    return result
