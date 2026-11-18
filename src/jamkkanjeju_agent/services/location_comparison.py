"""완료된 스냅샷 비교와 삭제 후보의 TourAPI 상세 재확인."""

from langchain_core.tools import ToolException

from ..clients import AgentClients
from ..clients.tourapi_client import TourApiError
from ..memory.store import snapshot_store, sync_plan_store


def compare_location_snapshots(storedSnapshotId: str, tourapiSnapshotId: str,
                               resultType: str, clients: AgentClients) -> dict:
    """스냅샷만 비교하고 목록 차집합의 삭제 후보는 상세 조회로 확인한다."""
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
            verified = {content_id: clients.tourapi.get_location(content_id) for content_id in missing_ids}
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
    if resultType == "all":
        result["planId"] = sync_plan_store.save(storedSnapshotId, tourapiSnapshotId,
                                               result["addedLocations"], result["confirmedMissingLocations"])
    return result
