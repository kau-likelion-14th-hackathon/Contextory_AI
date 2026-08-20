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


def test_translation_request_is_reproducible():
    """
    같은 PR은 항상 같은 쿼리로 번역되어야 한다.
    쿼리가 흔들리면 검색 최고 유사도가 SIM_THRESHOLD를 넘나들어
    "근거 부족" 판정이 실행마다 뒤집힌다(실측으로 확인된 문제).
    """
    client = FakeClient(content="project domain CRUD API Project entity")

    translate_pr_to_en_query("프로젝트 도메인 구현", "Project 엔티티 추가", client=client)

    request = client.chat.completions.calls[0]
    assert request["temperature"] == settings.TRANSLATION_TEMPERATURE == 0.0
    assert request["seed"] == settings.TRANSLATION_SEED


def test_translation_prompt_preserves_identifiers():
    """
    검색 쿼리를 짧게 요약하면 유사도가 떨어져 멀쩡한 PR이 근거 부족으로 빠진다
    (실측: 식별자를 버리면 0.4462, 남기면 0.5458 — 임계값 0.5를 사이에 두고 갈렸다).
    프롬프트가 식별자 보존을 지시하는지 확인한다.
    """
    client = FakeClient(content="q")

    translate_pr_to_en_query("제목", "설명", client=client)

    prompt = client.chat.completions.calls[0]["messages"][0]["content"]
    assert "verbatim" in prompt
    assert "Do NOT summarize" in prompt


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
