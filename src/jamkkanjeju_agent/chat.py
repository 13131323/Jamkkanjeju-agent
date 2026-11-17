"""jamkkanjeju 명령으로 실행하는 터미널 대화 인터페이스."""

import argparse
import json
import os
from collections.abc import Callable
from importlib.metadata import version

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError

from .agent import create_operations_agent

HELP = """자연어로 질문하세요. 예: 저장된 장소 5개 보여줘 / 다음 페이지도 보여줘
/help  도움말    /clear  대화 기록 초기화    /exit  종료
exit 또는 quit, Ctrl+D(Windows는 Ctrl+Z 후 Enter)로도 종료할 수 있습니다.
현재는 장소 조회만 가능하며, 대화는 현재 실행 중에만 유지됩니다."""


def print_progress(text: str) -> None:
    """터미널이나 파이프 출력에서도 진행 상황이 버퍼에 머물지 않게 한다."""
    print(text, flush=True)


def run_chat(agent, *, read: Callable[[str], str] = input, write: Callable[[str], None] = print_progress) -> int:
    """완료한 대화만 기억한다. 실패·중단된 요청의 불완전한 도구 호출은 다음 턴에 남기지 않는다."""
    turns: list[list[BaseMessage]] = []

    def display(text: str) -> None:
        for name in ("GOOGLE_API_KEY", "AGENT_API_TOKEN"):
            secret = os.getenv(name, "")
            if secret:
                text = text.replace(secret, "[비밀 정보 제외]")
        write(text)

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
            display("대화 기록을 초기화했습니다.")
            continue
        if prompt.startswith("/"):
            display("알 수 없는 명령입니다. /help로 확인해 주세요.")
            continue

        history = [message for turn in turns for message in turn]
        current: list[BaseMessage] = [HumanMessage(prompt)]
        answered = False
        display("[처리 중]")
        try:
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
