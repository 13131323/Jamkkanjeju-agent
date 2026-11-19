"""여러 Tool이 공유하는 Spring API 클라이언트와 요청·응답 계약."""

import os
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, model_validator

class StoredLocationsError(RuntimeError):
    """토큰·외부 응답 원문을 포함하지 않는 저장 장소 조회 오류."""


class StoredLocation(BaseModel):
    locationId: int = Field(strict=True, gt=0)
    contentId: int = Field(strict=True, gt=0)
    title: str
    address: str
    category: str
    radius: int = Field(strict=True, ge=0)
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)


class LocationsPage(BaseModel):
    page: int = Field(strict=True, ge=0)
    size: int = Field(strict=True, ge=1, le=100)
    hasNext: bool = Field(strict=True)


class StoredLocationsResponse(BaseModel):
    locations: list[StoredLocation]
    locationsPage: LocationsPage


class GuestbooksError(RuntimeError):
    """방명록 조회의 인증 정보와 원문을 제외한 오류."""


class GuestbookText(BaseModel):
    locationId: int = Field(strict=True, gt=0)
    guestbookId: int = Field(strict=True, gt=0)
    text: str | None = Field(strict=True)


class GuestbooksResponse(BaseModel):
    guestbooks: list[GuestbookText]
    guestbooksPage: LocationsPage


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


class LocationSyncError(RuntimeError):
    """외부 응답 원문·인증 정보를 제외한 동기화 오류."""


class AddedLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contentId: int = Field(strict=True, gt=0)
    title: str = Field(strict=True, min_length=1, max_length=100)
    address: str = Field(strict=True, min_length=1, max_length=255)
    category: Literal["축제·공연·행사", "체험관광", "음식", "쇼핑", "문화관광", "레저스포츠"]
    radius: int = Field(strict=True, ge=0)
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)


class DeletedLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    locationId: int = Field(strict=True, gt=0)
    contentId: int = Field(strict=True, gt=0)


class SyncResult(BaseModel):
    requestId: str = Field(strict=True, min_length=1)
    status: Literal["COMPLETED"]
    addedCount: int = Field(strict=True, ge=0)
    deletedCount: int = Field(strict=True, ge=0)
    addedContentIds: list[StrictInt] = Field(strict=True)
    deletedContentIds: list[StrictInt] = Field(strict=True)

    @model_validator(mode="after")
    def verify_counts(self):
        for count, ids in ((self.addedCount, self.addedContentIds), (self.deletedCount, self.deletedContentIds)):
            if count != len(ids) or len(set(ids)) != len(ids) or any(type(i) is not int or i <= 0 for i in ids):
                raise ValueError("처리 건수·식별자 불일치")
        return self


class SpringClient:
    """공유 HTTP 연결로 장소 조회·점검 예약·동기화를 수행한다."""

    def __init__(self, http_client: httpx.Client):
        self._http = http_client
        self._token = os.getenv("AGENT_API_TOKEN", "")

    def _endpoint(self, name: str, error_type: type[RuntimeError], default: str = "") -> str:
        url = os.getenv(name, default)
        parsed = urlsplit(url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise error_type(f"{name}에 인증 정보·쿼리가 없는 HTTP(S) API 주소를 설정해 주세요.")
        if not self._token.strip() or "\r" in self._token or "\n" in self._token:
            raise error_type("AGENT_API_TOKEN에 Spring과 동일한 에이전트 전용 고정 토큰을 설정해 주세요.")
        return url

    def _get_page(self, page: int) -> StoredLocationsResponse:
        """인증·응답 형식·요청과 응답 페이지의 일치를 확인한다."""
        try:
            response = self._http.get(
                self._endpoint("STORED_LOCATIONS_API_URL", StoredLocationsError), params={"page": page, "size": 100},
                headers={"X-Agent-Token": self._token, "Accept": "application/json"},
                timeout=httpx.Timeout(15.0, connect=5.0), follow_redirects=False,
            )
        except httpx.RequestError:
            raise StoredLocationsError("Spring 장소 조회 연결에 실패했습니다. 스냅샷을 생성하지 않았습니다.") from None
        if response.status_code in {401, 403}:
            raise StoredLocationsError("에이전트 장소 조회 인증·권한이 없습니다. AGENT_API_TOKEN과 조회 API 설정을 확인해 주세요.")
        if not response.is_success:
            raise StoredLocationsError(f"장소 조회 API 호출에 실패했습니다(HTTP {response.status_code}).")
        try:
            result = StoredLocationsResponse.model_validate_json(response.content)
        except ValueError:
            raise StoredLocationsError("장소 조회 API 응답이 예상 스키마와 다릅니다. 스냅샷을 생성하지 않았습니다.") from None
        if result.locationsPage.page != page or result.locationsPage.size != 100:
            raise StoredLocationsError("장소 조회 API의 페이지 정보가 요청과 일치하지 않습니다.")
        return result


    def get_stored_locations(self) -> list[dict]:
        """모든 페이지 성공 시에만 목록을 반환하며 중복·순서·불완전한 페이지를 차단한다."""
        locations = []
        seen = set()
        previous_location_id = 0
        page = 0
        while True:
            result = self._get_page(page)
            rows = result.locations
            has_next = result.locationsPage.hasNext
            if (has_next and len(rows) != 100) or len(rows) > 100 or (page > 0 and not rows):
                raise StoredLocationsError("저장 장소 페이지가 불완전합니다. 스냅샷을 생성하지 않았습니다.")
            for row in rows:
                if row.contentId in seen or row.locationId <= previous_location_id:
                    raise StoredLocationsError("저장 장소 ID 중복 또는 정렬 오류입니다. 스냅샷을 생성하지 않았습니다.")
                seen.add(row.contentId)
                previous_location_id = row.locationId
                locations.append(row.model_dump())
            if not has_next:
                return locations
            page += 1


    def get_location_guestbooks(self) -> list[dict]:
        """방명록 전체를 읽고 페이지 누락·중복·역순을 거부한다."""
        default = self._endpoint("STORED_LOCATIONS_API_URL", GuestbooksError).rsplit("/", 1)[0] + "/guestbooks"
        url = self._endpoint("GUESTBOOKS_API_URL", GuestbooksError, default)
        rows, page, previous = [], 0, 0
        while True:
            try:
                response = self._http.get(url, params={"page": page, "size": 100},
                    headers={"X-Agent-Token": self._token, "Accept": "application/json"},
                    timeout=httpx.Timeout(30.0, connect=5.0), follow_redirects=False)
            except httpx.RequestError:
                raise GuestbooksError("방명록 조회 연결에 실패했습니다. 분석 스냅샷을 생성하지 않았습니다.") from None
            if not response.is_success:
                raise GuestbooksError(f"방명록 조회 API 오류(HTTP {response.status_code}). 분석 스냅샷을 생성하지 않았습니다.")
            try:
                result = GuestbooksResponse.model_validate_json(response.content)
            except ValueError:
                raise GuestbooksError("방명록 조회 응답 형식이 올바르지 않습니다.") from None
            info = result.guestbooksPage
            if (info.page != page or info.size != 100 or len(result.guestbooks) > 100
                    or (info.hasNext and len(result.guestbooks) != 100)
                    or (page > 0 and not result.guestbooks)):
                raise GuestbooksError("방명록 페이지가 누락되었거나 불완전합니다.")
            for item in result.guestbooks:
                if item.guestbookId <= previous:
                    raise GuestbooksError("방명록 ID 중복 또는 정렬 오류입니다.")
                previous = item.guestbookId
                rows.append(item.model_dump())
            if not info.hasNext:
                return rows
            page += 1

    def get_guestbook_texts(self, guestbook_ids: list[int]) -> list[dict]:
        """선택 ID만 100개씩 조회한다. 실패·계약 불일치는 부분 성공으로 반환하지 않는다."""
        if not guestbook_ids or any(type(value) is not int or value <= 0 for value in guestbook_ids):
            raise GuestbooksError("조회할 방명록 ID는 하나 이상의 양수 정수여야 합니다.")
        ids = list(dict.fromkeys(guestbook_ids))
        default = self._endpoint("STORED_LOCATIONS_API_URL", GuestbooksError).rsplit("/", 1)[0] + "/guestbooks"
        url = self._endpoint("GUESTBOOKS_API_URL", GuestbooksError, default)
        rows = []
        for start in range(0, len(ids), 100):
            batch = ids[start:start + 100]
            try:
                response = self._http.get(url, params={"page": 0, "size": 100,
                    "guestbookIds": ",".join(map(str, batch))},
                    headers={"X-Agent-Token": self._token, "Accept": "application/json"},
                    timeout=httpx.Timeout(30.0, connect=5.0), follow_redirects=False)
            except httpx.RequestError:
                raise GuestbooksError("방명록 원문 조회 연결에 실패했습니다. 부분 결과를 반환하지 않습니다.") from None
            if not response.is_success:
                raise GuestbooksError(f"방명록 원문 조회 API 오류(HTTP {response.status_code}). 부분 결과를 반환하지 않습니다.")
            try:
                result = GuestbooksResponse.model_validate_json(response.content)
            except ValueError:
                raise GuestbooksError("방명록 원문 조회 응답 형식이 올바르지 않습니다.") from None
            page = result.guestbooksPage
            returned_ids = [item.guestbookId for item in result.guestbooks]
            if (page.page != 0 or page.size != 100 or page.hasNext
                    or len(returned_ids) != len(set(returned_ids))
                    or not set(returned_ids).issubset(batch)
                    or returned_ids != sorted(returned_ids)):
                raise GuestbooksError("방명록 원문 조회 응답이 요청 ID 또는 페이지 계약과 다릅니다.")
            rows.extend(item.model_dump() for item in result.guestbooks)
        return rows

    def schedule_maintenance(self, message: str, *, startsAt: str | None = None) -> MaintenanceReservation:
        """예약 등록 POST는 중복 생성을 피하기 위해 자동 재시도하지 않는다."""
        payload = {"message": message}
        if startsAt is not None:
            payload["startsAt"] = startsAt
        try:
            response = self._http.post(
                self._endpoint("MAINTENANCE_API_URL", MaintenanceApiError), json=payload,
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


    def synchronize_locations(self, plan: dict) -> dict:
        """Spring은 requestId 멱등성과 승인 대상 일치·트랜잭션을 보장해야 한다."""
        try:
            response = self._http.post(self._endpoint("LOCATION_SYNC_API_URL", LocationSyncError, "http://localhost:8080/api/v2/agent/locations/sync"), json=plan,
                headers={"X-Agent-Token": self._token, "Idempotency-Key": plan["requestId"], "Accept": "application/json"},
                timeout=httpx.Timeout(60.0, connect=5.0), follow_redirects=False)
        except httpx.RequestError:
            raise LocationSyncError("동기화 API 응답을 받지 못해 반영 여부가 미확인입니다. 자동 재시도하지 말고 requestId로 서버 처리 내역을 확인해 주세요.") from None
        if response.status_code in {404, 405, 501}:
            raise LocationSyncError("장소 동기화 API가 아직 제공되지 않습니다. Spring 구현과 LOCATION_SYNC_API_URL 설정을 확인해 주세요. 성공으로 판단하지 않습니다.")
        if response.status_code in {401, 403}:
            raise LocationSyncError("장소 동기화 인증·권한이 없습니다. AGENT_API_TOKEN을 확인해 주세요.")
        if response.status_code == 409:
            raise LocationSyncError("비교 이후 데이터가 변경됐거나 요청 상태가 충돌했습니다. 서버 상태를 확인하고 새 비교 결과로 다시 승인받아 주세요.")
        if response.status_code != 200:
            raise LocationSyncError(f"동기화 API 오류(HTTP {response.status_code}). 반영 여부를 확인하고 자동 재시도하지 마세요.")
        try:
            result = SyncResult.model_validate_json(response.content)
            if (result.requestId != plan["requestId"]
                    or set(result.addedContentIds) != {row["contentId"] for row in plan["addLocations"]}
                    or set(result.deletedContentIds) != {row["contentId"] for row in plan["deleteLocations"]}):
                raise ValueError("승인 대상·처리 결과 불일치")
        except (ValueError, ValidationError):
            raise LocationSyncError("동기화 API 응답이 승인한 계획과 다릅니다. 반영 여부 미확인이므로 자동 재시도하지 말고 서버 처리 내역을 확인해 주세요.") from None
        return result.model_dump()

