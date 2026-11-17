"""TourAPI 인증·HTTP 호출·재시도·전체 장소 수집과 응답 검증을 담당한다."""

import math
import os
import time
from urllib.parse import urlsplit

import httpx

# FastAPI의 6개 카테고리는 유지하고 지역 조회는 현행 법정동 코드를 사용한다.
LOCATION_CATEGORIES = {
    "EV": "축제·공연·행사", "EX": "체험관광", "FD": "음식",
    "SH": "쇼핑", "VE": "문화관광", "LS": "레저스포츠",
}
ROWS_PER_PAGE = 1000


class TourApiError(RuntimeError):
    """인증키나 외부 응답 원문을 포함하지 않는 TourAPI 연동 오류."""


class TourApiClient:
    """여러 Tool에서 사용할 수 있는 TourAPI 클라이언트."""

    def __init__(self, http_client: httpx.Client):
        """HTTP 연결 수명은 호출자가 관리하며 설정과 인증 정보는 환경변수에서 읽는다."""
        self._http = http_client
        base = os.getenv("KTO_BASE_URL", "").rstrip("/")
        parsed = urlsplit(base)
        key = os.getenv("KTO_SERVICE_KEY", "").strip()
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment or not key):
            raise TourApiError("KTO_BASE_URL과 KTO_SERVICE_KEY를 설정해 주세요.")
        self._base_url = base
        self._service_key = key

    def _request(self, params: dict, *, endpoint: str = "areaBasedList2") -> dict:
        """연결 장애·429·5xx만 최대 3회 시도하며 원문 오류와 인증키는 노출하지 않는다."""
        for attempt in range(3):
            try:
                response = self._http.get(self._base_url + "/" + endpoint, params=params, timeout=30, follow_redirects=False)
            except httpx.RequestError:
                if attempt < 2:
                    time.sleep(0.25 * (attempt + 1))
                    continue
                raise TourApiError("TourAPI 연결에 실패했습니다.") from None
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < 2:
                    time.sleep(0.25 * (attempt + 1))
                    continue
            if not response.is_success:
                raise TourApiError(f"TourAPI 조회 실패(HTTP {response.status_code}).")
            try:
                payload = response.json()["response"]
                if payload["header"]["resultCode"] != "0000":
                    raise ValueError
                body = payload["body"]
                if not isinstance(body, dict):
                    raise ValueError
                return body
            except (ValueError, KeyError, TypeError):
                raise TourApiError("TourAPI 오류 응답 또는 잘못된 응답입니다.") from None
        raise AssertionError("도달할 수 없는 재시도 상태")

    def get_locations(self) -> list[dict]:
        """제주 전체를 수집·검증한 뒤 FastAPI의 6개 대분류를 선택한다."""
        params = {
            "serviceKey": self._service_key, "MobileOS": os.getenv("KTO_MOBILE_OS", "ETC"),
            "MobileApp": os.getenv("KTO_MOBILE_APP", "jamkkanjeju"),
            "_type": "json", "lDongRegnCd": "50", "numOfRows": ROWS_PER_PAGE,
        }
        total = None
        fetched = 0
        seen = set()
        locations = []
        page = 1
        try:
            while total is None or fetched < total:
                body = self._request({**params, "pageNo": page})
                count = int(str(body["totalCount"]))
                if count < 0 or (total is not None and count != total):
                    raise ValueError
                total = count
                if int(str(body["pageNo"])) != page:
                    raise TourApiError(f"TourAPI 페이지 번호가 요청과 다릅니다(요청 페이지 {page}).")
                container = body.get("items")
                if container in (None, ""):
                    items = []
                else:
                    items = container.get("item", [])
                if isinstance(items, dict):
                    items = [items]
                if not isinstance(items, list):
                    raise ValueError
                expected = min(ROWS_PER_PAGE, total - fetched)
                if len(items) != expected:
                    raise TourApiError(f"TourAPI 페이지 건수가 부족하거나 초과합니다(페이지 {page}, 예상 {expected}, 수신 {len(items)}).")
                # TourAPI는 마지막 페이지에서 요청 크기 대신 실제 반환 건수를 표시할 수 있다.
                reported_rows = int(str(body["numOfRows"]))
                if reported_rows not in {ROWS_PER_PAGE, len(items)}:
                    raise TourApiError(f"TourAPI numOfRows가 요청 크기·실제 건수와 맞지 않습니다(페이지 {page}).")
                for item in items:
                    if str(item.get("lDongRegnCd", "")) != "50":
                        raise TourApiError(f"TourAPI 법정동 지역코드가 제주(50)와 다릅니다(페이지 {page}).")
                    content_id = int(str(item["contentid"]).strip())
                    if content_id <= 0 or content_id in seen:
                        raise ValueError
                    seen.add(content_id)
                    category = LOCATION_CATEGORIES.get(str(item.get("lclsSystm1") or "").strip())
                    if category is None:
                        continue
                    title = str(item.get("title") or "").strip()
                    address = " ".join(str(item.get(k) or "").strip() for k in ("addr1", "addr2") if str(item.get(k) or "").strip())
                    longitude, latitude = float(item["mapx"]), float(item["mapy"])
                    if (not title or len(title) > 100 or not address or len(address) > 255
                            or not math.isfinite(longitude) or not math.isfinite(latitude)
                            or not -180 <= longitude <= 180 or not -90 <= latitude <= 90):
                        raise ValueError
                    locations.append(dict(contentId=content_id, title=title, address=address,
                                          category=category, radius=100, longitude=longitude, latitude=latitude))
                fetched += len(items)
                page += 1
            # FastAPI와 동일하게 저장 대상이 0개인 응답은 정상 비교 대상으로 허용하지 않는다.
            if not locations:
                raise ValueError
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
            raise TourApiError("TourAPI 전체 건수·페이지·장소 데이터 검증에 실패했습니다.") from None
        return sorted(locations, key=lambda row: row["contentId"])



    def get_location(self, content_id: int) -> dict | None:
        """지역·분류 필터 없이 ID로 존재를 확인하며 정상 0건만 None으로 구분한다."""
        body = self._request({
            "serviceKey": self._service_key, "MobileOS": os.getenv("KTO_MOBILE_OS", "ETC"),
            "MobileApp": os.getenv("KTO_MOBILE_APP", "jamkkanjeju"), "_type": "json",
            "contentId": content_id,
        }, endpoint="detailCommon2")
        try:
            total = int(str(body["totalCount"]))
            container = body.get("items")
            items = [] if container in (None, "") else container.get("item", [])
            if isinstance(items, dict):
                items = [items]
            if not isinstance(items, list) or total not in (0, 1) or len(items) != total:
                raise ValueError
            if total == 0:
                return None
            item = items[0]
            title = str(item.get("title") or "").strip()
            if int(str(item["contentid"])) != content_id or not title:
                raise ValueError
            return {
                "contentId": content_id, "title": title,
                "address": " ".join(str(item.get(k) or "").strip() for k in ("addr1", "addr2") if str(item.get(k) or "").strip()),
                "categoryCode": str(item.get("lclsSystm1") or ""),
                "regionCode": str(item.get("lDongRegnCd") or ""),
            }
        except (ValueError, KeyError, TypeError, AttributeError):
            raise TourApiError("TourAPI 개별 존재 확인 응답 검증에 실패했습니다. 없는 장소로 처리하지 마세요.") from None
