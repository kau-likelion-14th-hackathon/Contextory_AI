"""번역 서비스 테스트 — 클라이언트 주입, 모델 설정, 실패 시 원문 fallback"""

from core.config import settings
from services.translation_service import translate_pr_to_en_query


class _FakeCompletions:
    def __init__(self, content=None, raise_exc=None):
        self._content = content
        self._raise_exc = raise_exc
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._raise_exc:
            raise self._raise_exc
        return type(
            "Resp", (), {"choices": [type("C", (), {"message": type("M", (), {"content": self._content})()})()]}
        )()


class FakeClient:
    def __init__(self, content=None, raise_exc=None):
        self.chat = type("Chat", (), {})()
        self.chat.completions = _FakeCompletions(content=content, raise_exc=raise_exc)


def test_translation_uses_injected_client_and_configured_model():
    client = FakeClient(content="  JWT login token reissue  ")

    result = translate_pr_to_en_query("JWT 로그인 추가", "토큰 재발급", client=client)

    assert result == "JWT login token reissue"
    assert client.chat.completions.calls[0]["model"] == settings.TRANSLATION_MODEL


def test_api_failure_falls_back_to_original_text():
    client = FakeClient(raise_exc=RuntimeError("rate limit"))

    result = translate_pr_to_en_query("JWT 로그인 추가", "토큰 재발급", client=client)

    assert result == "JWT 로그인 추가 토큰 재발급"


def test_empty_response_falls_back_to_original_text():
    client = FakeClient(content="   ")

    assert translate_pr_to_en_query("제목", "설명", client=client) == "제목 설명"


def test_empty_input_skips_llm_call():
    client = FakeClient(raise_exc=RuntimeError("호출되면 안 됨"))

    assert translate_pr_to_en_query("", "", client=client) == ""
    assert client.chat.completions.calls == []
