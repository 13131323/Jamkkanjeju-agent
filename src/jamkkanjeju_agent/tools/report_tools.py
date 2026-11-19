"""LLM이 작성한 Markdown 보고서를 지정한 로컬 폴더에 저장한다."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import re

from langchain_core.tools import ToolException, tool
from pydantic import BaseModel, ConfigDict, Field, field_validator


REPORT_DIRECTORY = Path(__file__).resolve().parents[3] / "docs" / "report"


class SaveMarkdownReportInput(BaseModel):
    """보고서 이름과 본문만 입력받고 저장 위치·날짜는 코드에서 정한다."""
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(strict=True, min_length=1, description="LLM이 정한 보고서 이름. 경로·날짜 제외. .md 확장자는 생략 가능")
    content: str = Field(strict=True, min_length=1, description="조회 결과를 바탕으로 LLM이 작성한 Markdown 본문 전체")

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        """파일 이름만 허용하며 경로 입력과 Windows에서 사용할 수 없는 문자를 거부한다."""
        value = value.strip()
        if value.lower().endswith(".md"):
            value = value[:-3]
        if (not value or value in {".", ".."} or value.endswith((".", " "))
                or re.search(r'[<>:"/\\|?*\x00-\x1f\x7f]', value)):
            raise ValueError("보고서 이름에는 경로·특수문자 없이 파일 이름만 입력해 주세요.")
        return value

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        """본문의 Markdown 형식은 유지하고 공백뿐인 보고서는 거부한다."""
        if not value.strip():
            raise ValueError("보고서 본문이 비어 있습니다.")
        return value


@tool("save_markdown_report", args_schema=SaveMarkdownReportInput)
def save_markdown_report(filename: str, content: str) -> dict:
    """LLM이 작성한 보고서를 docs/report에 UTF-8 Markdown 파일로 저장한다.

    filename은 내용을 표현하도록 LLM이 정한 이름이며 경로·날짜는 넣지 않는다.
    content에는 실제 조회 결과에 근거해 작성한 Markdown 전체를 전달한다.
    실행 시점의 한국 날짜를 코드에서 붙여 '보고서이름_YYYYMMDD.md'로 저장한다.
    같은 이름이 있으면 '보고서이름_2_YYYYMMDD.md'처럼 번호를 넣고 기존 파일은 유지한다.
    저장 성공 시 실제 파일의 절대 경로 path를 반환한다. DB·LLM API는 호출하지 않는다.
    파일 저장을 요청한 경우에만 사용하고, 정상 결과를 받기 전 저장했다고 말하지 않는다.
    """
    date = datetime.now(timezone(timedelta(hours=9))).strftime("%Y%m%d")
    number = 1
    try:
        REPORT_DIRECTORY.mkdir(parents=True, exist_ok=True)
        while True:
            name = filename if number == 1 else f"{filename}_{number}"
            target = REPORT_DIRECTORY / f"{name}_{date}.md"
            try:
                stream = target.open("x", encoding="utf-8", newline="\n")
            except FileExistsError:
                number += 1
                continue
            try:
                with stream:
                    stream.write(content)
            except (OSError, UnicodeError):
                target.unlink(missing_ok=True)
                raise
            return {"path": str(target.resolve())}
    except (OSError, UnicodeError):
        raise ToolException("Markdown 보고서 저장에 실패했습니다. 저장 폴더 권한과 파일 이름을 확인해 주세요.") from None
