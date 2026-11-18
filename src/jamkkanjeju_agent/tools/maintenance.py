"""공지 문구를 받아 Spring에 점검 예약을 등록하는 LangChain Tool."""

import httpx
from datetime import datetime
from langchain_core.tools import ToolException, tool
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..clients.maintenance import MaintenanceApiError, MaintenanceClient


DEFAULT_MAINTENANCE_MESSAGE = "장소 데이터 동기화를 위한 서비스 점검이 예정되어 있습니다. 점검 시간 동안 서비스 이용이 제한됩니다."


class ScheduleMaintenanceInput(BaseModel):
    """선택 공지와 KST 시작 시각을 입력받으며 종료 시각·인증 설정은 받지 않는다."""

    model_config = ConfigDict(extra="forbid")
    message: str = Field(
        default=DEFAULT_MAINTENANCE_MESSAGE, strict=True, max_length=500,
        description="선택 공지 문구. 생략·빈 문자열·공백은 장소 데이터 동기화 기본 공지를 사용한다.",
    )
    startsAt: str | None = Field(
        default=None, strict=True, pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$",
        description="사용자가 지정한 점검 시작 시각. 한국시간 YYYY-MM-DDTHH:mm:ss, offset 없음. 미래 시각에는 최소 대기 시간 제한이 없으며 1~2분 뒤도 가능하다. 생략 시에만 서버 현재 시각 1시간 뒤를 기본값으로 사용한다.",
    )

    @field_validator("startsAt")
    @classmethod
    def validate_start_time(cls, value):
        """문자열 형식뿐 아니라 실제 존재하는 날짜와 시각인지 확인한다."""
        if value is not None:
            datetime.fromisoformat(value)
        return value

    @field_validator("message", mode="before")
    @classmethod
    def normalize_message(cls, value):
        """생략에 해당하는 문구는 기본값으로 바꾸고 앞뒤 공백은 제거한다."""
        if value is None:
            return DEFAULT_MAINTENANCE_MESSAGE
        if isinstance(value, str):
            return value.strip() or DEFAULT_MAINTENANCE_MESSAGE
        return value


@tool("schedule_maintenance", args_schema=ScheduleMaintenanceInput)
def schedule_maintenance(message: str = DEFAULT_MAINTENANCE_MESSAGE, startsAt: str | None = None) -> dict:
    """사용자가 점검 예약을 명시적으로 요청하면 Spring에 예약 데이터를 등록한다.

    message는 선택 공지 문구다. 생략하면 장소 데이터 동기화 기본 공지를 사용한다.
    startsAt은 사용자가 지정한 한국시간 YYYY-MM-DDTHH:mm:ss다. 생략하면 서버 현재 시각 1시간 뒤 시작한다.
    1시간은 생략 시 기본값이며 최소 예약 간격이 아니다. 미래 시각에는 최소 대기 시간 제한이 없다.
    1~2분 뒤라도 임박했다는 이유로 거절하지 말고 지정한 시각으로 호출한다.
    과거이거나 해석이 불명확한 시각은 사용자에게 확인하며 임의로 시각을 변경하지 않는다.
    Spring이 시작 시각 1시간 뒤를 종료 예정으로 계산해 SCHEDULED 데이터를 저장한다.
    이 Tool은 신규 예약 등록 전용이다. 기존 예약을 조회·취소·변경하지 않으며 그런 기능을 안내하지 않는다.
    서버 응답으로 확인한 예약만 반환한다. 점검 시작·API 차단·장소 동기화를 직접 실행하지 않는다.
    저장 여부가 불명확한 실패에서는 중복 예약을 막기 위해 이 Tool을 자동 반복 호출하지 않는다.
    """
    try:
        with httpx.Client() as http:
            reservation = MaintenanceClient(http).schedule(message, startsAt=startsAt)
    except MaintenanceApiError as exc:
        raise ToolException(str(exc)) from None
    return reservation.model_dump(mode="json")
