"""환경설정으로 Gemini API 호출용 클라이언트를 생성한다."""

import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import SecretStr


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
