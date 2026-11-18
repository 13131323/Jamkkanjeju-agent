"""전체 조회가 성공한 장소 목록을 현재 프로세스의 메모리에 보관한다."""

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Literal
from uuid import uuid4

from langchain_core.tools import ToolException
from pydantic import BaseModel, ConfigDict

SnapshotSource = Literal["DB", "TOURAPI"]


class SnapshotQueryInput(BaseModel):
    """전체 조회 Tool에는 페이지·인증 정보·장소 목록을 직접 입력하지 않는다."""

    model_config = ConfigDict(extra="forbid")  # 정의되지 않은 추가 입력값 금지


@dataclass(frozen=True)
class LocationSnapshot:
    snapshot_id: str
    source: SnapshotSource
    created_at: str
    locations: tuple[dict, ...]

    def summary(self) -> dict:
        """LLM에는 원본 목록 대신 조회 완료 여부와 참조 ID만 전달한다."""
        return {"snapshotId": self.snapshot_id, "source": self.source, "status": "COMPLETE",
                "count": len(self.locations), "createdAt": self.created_at}


class LocationSnapshotStore:
    """Tool들이 같은 프로세스에서 공유하며 파일·DB에 저장하지 않는 목록 저장소."""

    def __init__(self):
        self._snapshots: dict[str, LocationSnapshot] = {}
        self._lock = Lock()

    def save_complete(self, source: SnapshotSource, locations: list[dict]) -> dict:
        """전체 조회가 정상 종료한 뒤에만 원본과 분리된 사본을 저장한다."""
        if source not in {"DB", "TOURAPI"}:
            raise ToolException("알 수 없는 장소 스냅샷 출처입니다.")
        if source == "TOURAPI" and not locations:
            raise ToolException("TourAPI 수집 대상이 0건이어서 스냅샷을 생성하지 않았습니다.")
        ids = [row.get("contentId") for row in locations]
        if any(type(value) is not int or value <= 0 for value in ids) or len(set(ids)) != len(ids):
            raise ToolException("장소 ID가 잘못됐거나 중복되어 스냅샷을 생성하지 않았습니다.")
        snapshot = LocationSnapshot(
            snapshot_id=f"{source.lower()}-{uuid4().hex}", source=source,
            created_at=datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="seconds"),
            locations=tuple(deepcopy(locations)),
        )
        with self._lock:
            self._snapshots[snapshot.snapshot_id] = snapshot
        return snapshot.summary()

    def get(self, snapshot_id: str, source: SnapshotSource) -> LocationSnapshot:
        """없는 ID·다른 출처의 ID를 거부하고 호출자의 수정이 원본에 영향을 주지 않게 한다."""
        with self._lock:
            snapshot = self._snapshots.get(snapshot_id)
            if snapshot is None:
                raise ToolException("해당 스냅샷이 없습니다. 현재 실행에서 장소를 다시 전체 조회해 주세요.")
            if snapshot.source != source:
                raise ToolException(f"스냅샷 출처가 다릅니다. {source} 조회 Tool이 반환한 ID를 사용해 주세요.")
            return deepcopy(snapshot)

    def clear(self) -> None:
        """대화 초기화 시 원본 목록도 함께 비운다. 기존 ID는 더 이상 사용할 수 없다."""
        with self._lock:
            self._snapshots.clear()


snapshot_store = LocationSnapshotStore()


@dataclass(frozen=True)
class LocationSyncPlan:
    plan_id: str
    request_id: str
    stored_snapshot_id: str
    tourapi_snapshot_id: str
    additions: tuple[dict, ...]
    deletions: tuple[dict, ...]


class LocationSyncPlanStore:
    """LLM에는 계획 ID만 전달하며 원본과 실행 측 사본의 변경을 분리한다."""

    def __init__(self):
        self._plans: dict[str, LocationSyncPlan] = {}
        self._lock = Lock()

    def save(self, stored_snapshot_id: str, tourapi_snapshot_id: str,
             additions: list[dict], deletions: list[dict]) -> str:
        """삭제 상세 확인까지 끝난 전체 비교 결과만 실행 계획으로 고정한다."""
        plan = LocationSyncPlan(f"sync-plan-{uuid4().hex}", f"location-sync-{uuid4().hex}",
                                stored_snapshot_id, tourapi_snapshot_id,
                                tuple(deepcopy(additions)), tuple(deepcopy(deletions)))
        with self._lock:
            self._plans[plan.plan_id] = plan
        return plan.plan_id

    def get(self, plan_id: str) -> LocationSyncPlan:
        """계획과 원본 스냅샷이 현재 실행에 남아 있는지 확인하고 사본을 반환한다."""
        with self._lock:
            plan = self._plans.get(plan_id)
            if plan is None:
                raise ToolException("해당 동기화 계획이 없습니다. 전체 조회와 비교를 먼저 진행해 주세요.")
            plan = deepcopy(plan)
        snapshot_store.get(plan.stored_snapshot_id, "DB")
        snapshot_store.get(plan.tourapi_snapshot_id, "TOURAPI")
        return plan

    def clear(self) -> None:
        """대화 초기화·종료 시 계획도 재사용할 수 없게 한다."""
        with self._lock:
            self._plans.clear()


sync_plan_store = LocationSyncPlanStore()
