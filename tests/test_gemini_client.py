"""Gemini 클라이언트의 설정 누락과 생성 시 외부 호출이 없는지 검증한다."""

import pytest
from google.genai.errors import ClientError, ServerError
from langchain_google_genai.chat_models import GoogleAPIError, GoogleRateLimitError

from jamkkanjeju_agent.clients import gemini


@pytest.mark.parametrize("code", [500, 502, 503, 504])
def test_server_error_reports_http_code_without_external_message(code):
    error = GoogleAPIError(code, {"error": {"message": "private-key https://secret.test/?key=private-key"}})
    diagnosis = gemini.describe_gemini_error(error)
    assert f"HTTP {code}" in diagnosis
    assert "private-key" not in diagnosis and "secret.test" not in diagnosis


def test_rate_limit_follows_sdk_exception_chain():
    error = GoogleRateLimitError("private-key")
    error.__cause__ = ClientError(429, {"error": {"message": "private-key"}})
    assert "HTTP 429" in gemini.describe_gemini_error(error)
    assert "private-key" not in gemini.describe_gemini_error(error)


def test_other_errors_keep_existing_safe_fallback():
    assert gemini.describe_gemini_error(RuntimeError("private-key")) is None


@pytest.mark.parametrize("missing", ["GOOGLE_API_KEY", "GEMINI_MODEL"])
def test_missing_configuration_fails_before_sdk_creation(monkeypatch, missing):
    monkeypatch.setattr(gemini, "load_dotenv", lambda *_: None)
    monkeypatch.setenv("GOOGLE_API_KEY", "private-test-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    monkeypatch.setenv(missing, " ")
    def unexpected(**_):
        pytest.fail("설정이 누락되면 SDK를 생성하면 안 됩니다.")
    monkeypatch.setattr(gemini, "ChatGoogleGenerativeAI", unexpected)
    with pytest.raises(ValueError, match=missing):
        gemini.create_gemini_client()


def test_sdk_receives_masked_key_and_creation_does_not_invoke(monkeypatch):
    monkeypatch.setattr(gemini, "load_dotenv", lambda *_: None)
    monkeypatch.setenv("GOOGLE_API_KEY", "private-test-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    class FakeClient:
        def __init__(self, **settings):
            assert settings["api_key"].get_secret_value() == "private-test-key"
            assert "private-test-key" not in repr(settings)
            assert settings["vertexai"] is False
        def invoke(self, *_):
            pytest.fail("객체 생성만으로 모델을 호출하면 안 됩니다.")
    monkeypatch.setattr(gemini, "ChatGoogleGenerativeAI", FakeClient)
    assert isinstance(gemini.create_gemini_client(), FakeClient)
