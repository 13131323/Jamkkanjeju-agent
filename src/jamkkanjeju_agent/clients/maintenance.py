"""Spring의 점검 예약 API 호출과 응답 검증을 담당한다."""

import os
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field, ValidationError, model_validator


class MaintenanceApiError(RuntimeError):
    """인증 정보와 외부 오류 원문을 제외한 점검 예약 연동 오류."""


class MaintenanceReservation(BaseModel):
    """Spring 예약 API에서 반환하는 저장 결과 계약이다."""

    maintenanceId: int = Field(strict=True, gt=0)
    message: str = Field(strict=True, min_length=1, max_length=500)
    startsAt: datetime
    endsAt: datetime
    status: Literal["SCHEDULED"]

    @model_validator(mode="after")
    def validate_period(self):
        """기존 서비스의 KST·offset 없는 시각 계약과 점검 기간을 확인한다."""
        if (self.startsAt.tzinfo is not None or self.endsAt.tzinfo is not None
                or self.endsAt <= self.startsAt):
            raise ValueError("점검 시각 계약 불일치")
        return self


class MaintenanceClient:
    """HTTP 연결은 호출자가 관리하고 점검 시각 계산·저장은 Spring에 맡긴다."""

    def __init__(self, http_client: httpx.Client):
        self._http = http_client
        self._url = os.getenv("MAINTENANCE_API_URL", "")
        self._token = os.getenv("AGENT_API_TOKEN", "")
        parsed = urlsplit(self._url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise MaintenanceApiError("MAINTENANCE_API_URL에 인증 정보·쿼리가 없는 HTTP(S) 예약 API 주소를 설정해 주세요.")
        if not self._token.strip() or "\r" in self._token or "\n" in self._token:
            raise MaintenanceApiError("AGENT_API_TOKEN에 Spring과 동일한 에이전트 전용 토큰을 설정해 주세요.")

    def schedule(self, message: str, *, startsAt: str | None = None) -> MaintenanceReservation:
        """예약 등록 POST는 중복 생성을 피하기 위해 자동 재시도하지 않는다."""
        payload = {"message": message}
        if startsAt is not None:
            payload["startsAt"] = startsAt
        try:
            response = self._http.post(
                self._url, json=payload,
                headers={"X-Agent-Token": self._token, "Accept": "application/json"},
                timeout=httpx.Timeout(15.0, connect=5.0), follow_redirects=False,
            )
        except httpx.RequestError:
            raise MaintenanceApiError("점검 예약 API의 응답을 받지 못했습니다. 저장 여부가 미확인이므로 자동 재시도하지 말고 서버 예약 내역을 확인해 주세요.") from None
        if response.status_code in {401, 403}:
            raise MaintenanceApiError("점검 예약 인증·권한이 없습니다. Spring 예약 API의 에이전트 권한과 AGENT_API_TOKEN 설정을 확인해 주세요.")
        if response.status_code in {404, 405}:
            raise MaintenanceApiError("점검 예약 API가 제공되지 않습니다. Spring API 구현 및 MAINTENANCE_API_URL 설정이 필요합니다.")
        if not response.is_success:
            raise MaintenanceApiError(f"점검 예약 API 오류(HTTP {response.status_code}). 자동 재시도하지 말고 저장 여부를 확인해 주세요.")
        if response.status_code != 201:
            raise MaintenanceApiError("예약 API의 성공 상태가 계약(201)과 다릅니다. 저장 여부를 확인해 주세요.")
        try:
            return MaintenanceReservation.model_validate_json(response.content)
        except (ValueError, ValidationError):
            raise MaintenanceApiError("점검 예약 API 응답이 예상 계약과 다릅니다. 성공 여부가 미확인이므로 자동 재시도하지 말고 저장 내역을 확인해 주세요.") from None
