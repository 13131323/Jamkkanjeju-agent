"""Spring의 읽기 전용 장소 조회 API를 호출하는 LangChain Tool."""

import os
from typing import Annotated
from urllib.parse import urlsplit

import httpx
from langchain_core.tools import StructuredTool, ToolException, tool
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class StoredLocationsInput(BaseModel):
    """한 번에 한 페이지를 조회하며 인증 정보는 모델 입력에 노출하지 않는다."""

    model_config = ConfigDict(extra="forbid")   #	정의되지 않은 추가 입력값 금지
    page: Annotated[int, Field(strict=True, ge=0, description="0부터 시작하는 페이지 번호")] = 0
    size: Annotated[int, Field(strict=True, ge=1, le=100, description="조회 개수, 1~100")] = 100


class StoredLocation(BaseModel):
    """동기화 비교에 필요한 저장 장소 정보. Spring 조회 API와 공유하는 응답 계약이다."""

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


def create_get_stored_locations(
    *, api_url: str | None = None, agent_token: str | None = None, client: httpx.Client | None = None
) -> StructuredTool:
    """호출 대상과 인증 정보는 실행 프로그램에서 주입한다. client 주입은 단독 검증에 사용한다."""

    @tool("get_stored_locations", args_schema=StoredLocationsInput)
    def read_locations(page: int = 0, size: int = 100) -> dict:
        """Spring DB에 저장된 장소 목록을 한 페이지 조회한다. TourAPI 동기화 전 현재 장소를 확인할 때 사용한다.

        page는 0부터 시작하며 size는 1~100이다. locationsPage.hasNext가 true이면 다음 page를 조회할 수 있다.
        읽기 전용 도구로 장소·미션·방명록을 변경하지 않는다. 오류를 빈 목록으로 해석하지 않는다.
        """
        url = api_url if api_url is not None else os.getenv("STORED_LOCATIONS_API_URL", "")
        token = agent_token if agent_token is not None else os.getenv("AGENT_API_TOKEN", "")
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.query:
            raise ToolException("STORED_LOCATIONS_API_URL에 인증 정보·쿼리가 없는 HTTP(S) 조회 API URL을 설정해 주세요.")
        if not token or not token.strip() or "\r" in token or "\n" in token:
            raise ToolException("AGENT_API_TOKEN에 에이전트 전용 고정 토큰을 설정해 주세요.")

        def fetch(http: httpx.Client) -> dict:
            try:
                response = http.get(
                    url, params={"page": page, "size": size},
                    headers={"X-Agent-Token": token, "Accept": "application/json"},
                    timeout=httpx.Timeout(15.0, connect=5.0), follow_redirects=False,
                )
            except httpx.TimeoutException:
                raise ToolException("장소 조회 요청 시간이 초과되었습니다. 빈 목록으로 처리하지 마세요.") from None
            except httpx.RequestError:
                raise ToolException("Spring 서버에 연결하지 못했습니다. 서버 주소와 실행 상태를 확인해 주세요.") from None
            if response.status_code == 401:
                raise ToolException("에이전트 인증에 실패했습니다. 양쪽 AGENT_API_TOKEN 설정을 확인해 주세요.")
            if response.status_code == 403:
                raise ToolException("에이전트에 허용되지 않은 API입니다. 조회 API 주소를 확인해 주세요.")
            if not response.is_success:
                raise ToolException(f"장소 조회 API 호출에 실패했습니다(HTTP {response.status_code}).")
            try:
                result = StoredLocationsResponse.model_validate(response.json())
            except (ValueError, ValidationError):
                raise ToolException("장소 조회 API 응답이 예상 스키마와 다릅니다. Spring 연동 계약을 확인해 주세요.") from None
            if result.locationsPage.page != page or result.locationsPage.size != size or len(result.locations) > size:
                raise ToolException("장소 조회 API의 페이지 정보가 요청과 일치하지 않습니다.")
            return result.model_dump()

        if client is not None:
            return fetch(client)
        with httpx.Client() as http:
            return fetch(http)

    return read_locations


get_stored_locations = create_get_stored_locations()
