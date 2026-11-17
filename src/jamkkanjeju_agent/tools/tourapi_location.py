"""기존 장소의 존재 여부를 ID로 직접 재확인한다."""

import httpx
from langchain_core.tools import ToolException, tool
from pydantic import BaseModel, ConfigDict, Field

from ..clients.tourapi import TourApiClient, TourApiError


class TourapiLocationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contentId: int = Field(strict=True, gt=0, description="확인할 TourAPI 콘텐츠 ID")


@tool("get_tourapi_location", args_schema=TourapiLocationInput)
def get_tourapi_location(contentId: int) -> dict:
    """장소가 TourAPI에 실제 존재하는지 contentId로 직접 확인한다. 지역·분류 조건을 적용하지 않는다.

    사용자가 비교 결과에 이의를 제기하거나 특정 장소의 존재를 물으면 사용한다.
    contentId를 모르면 기존 조회 결과에서 확인하거나 사용자에게 물으며 임의로 만들지 않는다.
    정상 0건만 exists=false다. 조회 오류는 존재·삭제 여부 미확인이다.
    """
    try:
        with httpx.Client() as http:
            location = TourApiClient(http).get_location(contentId)
    except TourApiError as exc:
        raise ToolException(str(exc)) from None
    return {"contentId": contentId, "exists": location is not None, "location": location}
