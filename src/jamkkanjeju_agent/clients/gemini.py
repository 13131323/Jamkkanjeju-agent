"""환경설정으로 Gemini API 호출용 클라이언트를 생성한다."""

import os
from pathlib import Path

from dotenv import load_dotenv
from google.genai.errors import APIError
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.exceptions import ModelAPIError, ModelRateLimitError
from pydantic import SecretStr


def describe_gemini_error(error: Exception) -> str | None:
    """SDK 원문 대신 검증된 상태 코드로 원인을 표시해 URL·키 노출을 막는다."""
    if not isinstance(error, (APIError, ModelAPIError, ModelRateLimitError)):
        return None
    current = error
    seen = set()
    code = None
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        candidate = getattr(current, "code", None)
        if isinstance(current, APIError) and type(candidate) is int:
            code = candidate
            break
        current = current.__cause__ or current.__context__
    if code == 429 or isinstance(error, ModelRateLimitError):
        return "Gemini 호출 제한(HTTP 429): 요청 수·토큰 수 또는 사용량 한도에 걸렸습니다. Google AI Studio에서 해당 모델의 한도를 확인해 주세요."
    if code == 503:
        return "Gemini 서비스 오류(HTTP 503): Google 서버가 현재 요청을 처리할 수 없습니다. 잠시 후 다시 요청해 주세요."
    if code is not None and 500 <= code <= 599:
        return f"Gemini 서버 오류(HTTP {code}): Google 서버에서 요청 처리에 실패했습니다. 잠시 후 다시 요청해 주세요."
    if code == 400:
        return "Gemini 요청 오류(HTTP 400): 모델에 전달한 메시지·Tool 정의·요청 설정을 확인해야 합니다."
    if code in {401, 403}:
        return f"Gemini 인증·권한 오류(HTTP {code}): API 키와 프로젝트의 모델 사용 권한을 확인해 주세요."
    if code == 404:
        return "Gemini 모델 조회 오류(HTTP 404): GEMINI_MODEL과 모델 사용 가능 여부를 확인해 주세요."
    return "Gemini API 호출에 실패했습니다. 응답에서 HTTP 상태 코드를 확인할 수 없습니다."


def create_gemini_client() -> ChatGoogleGenerativeAI:
    """클라이언트만 생성하며 실제 요청은 반환 객체의 invoke() 호출 시 전송한다."""
    load_dotenv(Path(__file__).resolve().parents[3] / ".env")
    api_key = os.getenv("GOOGLE_API_KEY", "")
    model = os.getenv("GEMINI_MODEL", "").strip()
    if not api_key.strip():
        raise ValueError("GOOGLE_API_KEY를 설정해 주세요.")
    if not model:
        raise ValueError("GEMINI_MODEL을 설정해 주세요.")

    return ChatGoogleGenerativeAI(
        model=model,
        api_key=SecretStr(api_key),
        vertexai=False,
        timeout=30,
        max_retries=1,
    )
