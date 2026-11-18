"""실제 CLI 사용자의 승인을 Tool 실행 코드에 전달한다. LLM 입력으로 승인을 대체하지 않는다."""

from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy

from langchain_core.tools import ToolException


_approval_handler: ContextVar[Callable[[dict], bool] | None] = ContextVar("location_sync_approval", default=None)


@contextmanager
def approval_scope(handler: Callable[[dict], bool]):
    """CLI가 실제 입력을 읽는 동안만 승인 경로를 제공한다."""
    token = _approval_handler.set(handler)
    try:
        yield
    finally:
        _approval_handler.reset(token)


def request_sync_approval(plan: dict) -> bool:
    """직접 Tool 호출 등 승인 통로가 없으면 변경 요청을 차단한다."""
    handler = _approval_handler.get()
    if handler is None:
        raise ToolException("사용자 승인 경로가 없습니다. jamkkanjeju CLI에서 변경 목록을 확인하고 승인해 주세요. DB 변경 API를 호출하지 않았습니다.")
    return handler(deepcopy(plan)) is True
