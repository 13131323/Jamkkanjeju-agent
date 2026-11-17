"""Spring 장소 조회 API를 마지막 페이지까지 읽고 전체 수집 결과를 검증한다."""

import os
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field


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


class StoredLocationClient:
    """HTTP 연결은 호출자가 관리하고 페이지 순회는 이 클라이언트가 처리한다."""

    def __init__(self, http_client: httpx.Client):
        self._http = http_client
        self._url = os.getenv("STORED_LOCATIONS_API_URL", "")
        self._token = os.getenv("AGENT_API_TOKEN", "")
        parsed = urlsplit(self._url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise StoredLocationsError("STORED_LOCATIONS_API_URL에 인증 정보·쿼리가 없는 HTTP(S) 조회 API URL을 설정해 주세요.")
        if not self._token.strip() or "\r" in self._token or "\n" in self._token:
            raise StoredLocationsError("AGENT_API_TOKEN에 에이전트 전용 고정 토큰을 설정해 주세요.")

    def _get_page(self, page: int) -> StoredLocationsResponse:
        """인증·응답 형식·요청과 응답 페이지의 일치를 확인한다."""
        try:
            response = self._http.get(
                self._url, params={"page": page, "size": 100},
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

    def get_locations(self) -> list[dict]:
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
