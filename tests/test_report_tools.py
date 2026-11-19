"""실제 임시 파일로 날짜·Markdown 저장·중복 이름·실패 처리를 검증한다."""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from langchain_core.tools import ToolException
from pydantic import ValidationError

from jamkkanjeju_agent.tools import report_tools as reports


@pytest.fixture
def directory(monkeypatch, tmp_path):
    target = tmp_path / "docs" / "report"
    monkeypatch.setattr(reports, "REPORT_DIRECTORY", target)
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            # UTC 날짜와 KST 날짜가 다른 시각으로 한국 날짜 적용을 확인한다.
            return datetime(2026, 10, 10, 16, 30, tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(reports, "datetime", FixedDateTime)
    return target


@pytest.mark.parametrize("name", ["긍정 방명록 보고서", "긍정 방명록 보고서.md", "긍정 방명록 보고서.MD"])
def test_saves_markdown_with_korean_today_and_returns_absolute_path(directory, name):
    content = "# 긍정 방명록\n\n## 행복밀\n- 원문을 그대로 보관합니다.\n"
    result = reports.save_markdown_report.invoke({"filename": name, "content": content})
    path = Path(result["path"])
    assert path == directory / "긍정 방명록 보고서_20261011.md"
    assert path.is_absolute()
    assert path.read_bytes() == content.encode("utf-8")
    assert not path.read_bytes().startswith(b"\xef\xbb\xbf")


def test_duplicate_preserves_previous_file_and_keeps_date_at_end(directory):
    first = reports.save_markdown_report.invoke({"filename": "보고서", "content": "첫 보고서"})
    second = reports.save_markdown_report.invoke({"filename": "보고서", "content": "두 번째"})
    assert Path(first["path"]).read_text(encoding="utf-8") == "첫 보고서"
    assert Path(second["path"]).name == "보고서_2_20261011.md"
    assert Path(second["path"]).read_text(encoding="utf-8") == "두 번째"


@pytest.mark.parametrize("name", ["", " ", ".", "..", ".md", "../escape", "..\\escape", "C:\\outside", "bad:name", "bad?name", "a\nb", "trailing."])
def test_invalid_filename_never_writes(directory, name):
    with pytest.raises(ValidationError):
        reports.save_markdown_report.invoke({"filename": name, "content": "본문"})
    assert not directory.exists()


@pytest.mark.parametrize("content", ["", " \n\t"])
def test_blank_content_rejected(directory, content):
    with pytest.raises(ValidationError):
        reports.save_markdown_report.invoke({"filename": "보고서", "content": content})
    assert not directory.exists()


def test_storage_failure_returns_tool_error_without_external_details(directory, monkeypatch):
    def fail(*args, **kwargs):
        raise PermissionError("secret-path")
    monkeypatch.setattr(Path, "mkdir", fail)
    with pytest.raises(ToolException) as error:
        reports.save_markdown_report.invoke({"filename": "보고서", "content": "본문"})
    assert "secret-path" not in str(error.value)


def test_failed_write_removes_partial_file(directory, monkeypatch):
    original_open = Path.open
    class BrokenWriter:
        def __init__(self, stream):
            self.stream = stream
        def __enter__(self):
            return self
        def write(self, content):
            self.stream.write("partial")
            raise OSError("disk write failure")
        def __exit__(self, *args):
            self.stream.close()
    def fail_write(path, mode="r", *args, **kwargs):
        stream = original_open(path, mode, *args, **kwargs)
        return BrokenWriter(stream) if mode == "x" else stream
    monkeypatch.setattr(Path, "open", fail_write)
    with pytest.raises(ToolException):
        reports.save_markdown_report.invoke({"filename": "보고서", "content": "본문"})
    assert list(directory.iterdir()) == []


def test_model_cannot_supply_directory_or_date(directory):
    with pytest.raises(ValidationError):
        reports.save_markdown_report.invoke({"filename": "보고서", "content": "본문", "directory": "elsewhere"})
    assert not directory.exists()


def test_report_tool_registered_in_default_agent(monkeypatch):
    from jamkkanjeju_agent import agent
    monkeypatch.setattr(agent, "create_agent", lambda **kwargs: kwargs)
    configured = agent.create_operations_agent(model=object())
    assert "save_markdown_report" in [item.name for item in configured["tools"]]
    assert "보고서 파일 생성 Tool은 아직 없습니다" not in configured["system_prompt"]
