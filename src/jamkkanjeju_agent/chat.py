"""jamkkanjeju 명령으로 실행하는 터미널 대화 인터페이스."""

import argparse
import json
import os
from collections.abc import Callable
from importlib.metadata import version

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError

from .agent import create_operations_agent
from .clients import client_scope
from .memory.store import snapshot_store
from .services.sync_approval import approval_scope
from .tools.location_tools import clear_sync_executions

HELP = """자연어로 질문하세요. 예: 삭제될 장소 뽑아줘 / 추가될 장소 비교해줘
/help  도움말    /clear  대화 기록 초기화    /exit  종료
exit 또는 quit, Ctrl+D(Windows는 Ctrl+Z 후 Enter)로도 종료할 수 있습니다.
장소 조회·비교, 사용자 승인 후 동기화와 점검 예약 도구를 제공합니다. 점검 예약에는 실행 중인 Spring 예약 API가 필요합니다.
대화는 현재 실행 중에만 유지됩니다."""


def print_progress(text: str) -> None:
    """터미널이나 파이프 출력에서도 진행 상황이 버퍼에 머물지 않게 한다."""
    print(text, flush=True)


def run_chat(agent, *, read: Callable[[str], str] = input, write: Callable[[str], None] = print_progress) -> int:
    """대화 중 서비스별 연결을 공유하고 종료·오류 시 정리한다."""
    with client_scope():
        return _run_chat(agent, read=read, write=write)


def _run_chat(agent, *, read: Callable[[str], str] = input, write: Callable[[str], None] = print_progress) -> int:
    """완료한 대화만 기억한다. 실패·중단된 요청의 불완전한 도구 호출은 다음 턴에 남기지 않는다."""
    turns: list[list[BaseMessage]] = []

    def display(text: str) -> None:
        for name in ("GOOGLE_API_KEY", "AGENT_API_TOKEN", "KTO_SERVICE_KEY"):
            secret = os.getenv(name, "")
            if secret:
                text = text.replace(secret, "[비밀 정보 제외]")
        write(text)

    def approve_sync(plan: dict) -> bool:
        """모델의 승인 주장이 아니라 실제 터미널 입력으로 해당 계획만 승인한다."""
        display("[장소 동기화 실행 전 확인] requestId=" + plan["requestId"])
        display(f"추가 {len(plan['addLocations'])}개 / 삭제 {len(plan['deleteLocations'])}개")
        for label, rows in (("추가", plan["addLocations"]), ("삭제", plan["deletedLocationsReport"])):
            for row in rows:
                display(f"[{label}] " + json.dumps(row, ensure_ascii=False))
        display("[삭제 영향] " + plan["impact"])
        display("이 목록 그대로 승인한 경우에만 DB 변경 API를 한 번 호출합니다.")
        while True:
            try:
                decision = read("이 계획대로 실행하시겠습니까? (y/n) > ").strip().casefold()
            except (EOFError, KeyboardInterrupt):
                return False
            if decision == "y":
                return True
            if decision == "n":
                return False
            display("y 또는 n을 입력해 주세요. 아직 실행하지 않았습니다.")

    display("jamkkanjeju | 잠깐제주 운영 에이전트\n" + HELP)
    while True:
        try:
            prompt = read("나 > ").strip()
        except (EOFError, KeyboardInterrupt):
            display("\njamkkanjeju를 종료합니다.")
            return 0
        if not prompt:
            continue
        if prompt in {"/exit", "exit", "quit"}:
            display("jamkkanjeju를 종료합니다.")
            return 0
        if prompt == "/help":
            display(HELP)
            continue
        if prompt == "/clear":
            turns.clear()
            snapshot_store.clear()
            clear_sync_executions()
            display("대화 기록·장소 스냅샷·동기화 계획을 초기화했습니다.")
            continue
        if prompt.startswith("/"):
            display("알 수 없는 명령입니다. /help로 확인해 주세요.")
            continue

        history = [message for turn in turns for message in turn]
        current: list[BaseMessage] = [HumanMessage(prompt)]
        answered = False
        display("[처리 중]")
        try:
            with approval_scope(approve_sync):
                for update in agent.stream(
                    {"messages": [*history, *current]},
                    config={"recursion_limit": 20},
                    stream_mode="updates",
                ):
                    for state in update.values():
                        if not isinstance(state, dict):
                            continue
                        for message in state.get("messages", []):
                            current.append(message)
                            if isinstance(message, AIMessage):
                                for call in message.tool_calls:
                                    display(f"[도구 호출] {call['name']} {json.dumps(call['args'], ensure_ascii=False)}")
                                if not message.tool_calls and message.text:
                                    display("jamkkanjeju > " + message.text)
                                    answered = True
                            elif isinstance(message, ToolMessage):
                                if message.status == "error":
                                    display("[도구 오류] " + message.text)
                                else:
                                    display("[도구 완료] " + (message.name or "tool"))
            if answered:
                turns.append(current)
                # 도구 호출과 결과가 분리되지 않도록 메시지 대신 완료한 턴 단위로 유지한다.
                turns[:] = turns[-10:]
            else:
                display("답변을 얻지 못했습니다. 다시 요청해 주세요.")
        except KeyboardInterrupt:
            display("요청을 중단했습니다. 다음 질문을 입력하실 수 있습니다.")
        except GraphRecursionError:
            display("처리 단계 한도에 도달했습니다. 요청 범위를 줄여 다시 시도해 주세요.")
        except Exception as error:
            # 외부 SDK 예외 원문에는 URL·인증 정보가 섞일 수 있으므로 그대로 출력하지 않는다.
            display(f"요청 처리에 실패했습니다({type(error).__name__}). 연결 상태·모델 설정·API 한도를 확인해 주세요.")


def main() -> int:
    parser = argparse.ArgumentParser(prog="jamkkanjeju", description="잠깐제주 운영 AI 에이전트와 대화합니다.")
    parser.add_argument("--version", action="version", version=f"jamkkanjeju {version('jamkkanjeju-agent')}")
    parser.parse_args()
    try:
        agent = create_operations_agent()
    except ValueError:
        print("Gemini 클라이언트를 생성하지 못했습니다. .env의 GOOGLE_API_KEY와 GEMINI_MODEL 설정을 확인해 주세요.")
        return 1
    except Exception as error:
        print(f"에이전트를 시작하지 못했습니다({type(error).__name__}). 설치 및 환경설정을 확인해 주세요.")
        return 1
    return run_chat(agent)
