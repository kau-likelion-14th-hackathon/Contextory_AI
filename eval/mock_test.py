"""
mock_test.py — 실제 OpenAI API 키 없이 judge.py의 로직(에러 처리, A~D 검증 가드)을 검증한다.

llm_keyword_judge/keyword_judge는 client를 주입받을 수 있게 설계되어 있어서,
가짜 client로 OpenAI 응답을 흉내내면 API 키 없이도 성공/실패/규칙위반 케이스를 확인할 수 있다.
(진짜 GPT-4o가 프롬프트를 얼마나 잘 이해하는지는 검증 못 함 - 그건 API 키 받은 뒤 manual_test.py로 확인)

실행:
    python -m eval.mock_test
(Contextory_AI 폴더 루트에서 실행)
"""

import json

from eval.judge import keyword_judge


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, content=None, raise_exc=None):
        self._content = content
        self._raise_exc = raise_exc

    def create(self, **kwargs):
        if self._raise_exc:
            raise self._raise_exc
        return _FakeResponse(self._content)


class _FakeChat:
    def __init__(self, content=None, raise_exc=None):
        self.completions = _FakeCompletions(content=content, raise_exc=raise_exc)


class FakeClient:
    """OpenAI() 클라이언트를 흉내내는 가짜 클라이언트."""

    def __init__(self, content=None, raise_exc=None):
        self.chat = _FakeChat(content=content, raise_exc=raise_exc)


PR_TITLE = "테스트 PR"
PR_DESCRIPTION = "mock 테스트용 설명"
PR_DIFF = ""


def check(name, condition):
    status = "OK" if condition else "FAIL"
    print(f"[{status}] {name}")
    if not condition:
        raise AssertionError(name)


def main():
    # 1. 정상 응답: A 기준으로 important -> 그대로 통과해야 함
    ok_client = FakeClient(content=json.dumps({
        "thought_process": "criterion A(WHY)에 해당하는 근거가 있다고 판단함",
        "label": "important",
        "matched_criteria": ["A"],
        "reason": "테스트",
        "inferred_from_background_knowledge": False,
    }))
    result = keyword_judge(PR_TITLE, PR_DESCRIPTION, PR_DIFF, "애매한 키워드1", client=ok_client)
    check(
        "정상 응답(A 포함) -> important 그대로 통과",
        result["label"] == "important" and result.get("error") is None,
    )
    check(
        "thought_process 필드가 결과에 그대로 전달됨",
        bool(result.get("thought_process")),
    )

    # 2. 규칙 위반: E만 있는데 important라고 응답 -> 검증 가드가 not_important로 다운그레이드해야 함
    bad_client = FakeClient(content=json.dumps({
        "label": "important",
        "matched_criteria": ["E"],
        "reason": "테스트",
        "inferred_from_background_knowledge": False,
    }))
    result = keyword_judge(PR_TITLE, PR_DESCRIPTION, PR_DIFF, "애매한 키워드2", client=bad_client)
    check(
        "A~D 없이 important로 응답 -> not_important로 다운그레이드",
        result["label"] == "not_important",
    )

    # 3. 깨진 JSON -> 예외 처리로 크래시 없이 안전하게 처리돼야 함
    broken_client = FakeClient(content="이건 JSON이 아님")
    result = keyword_judge(PR_TITLE, PR_DESCRIPTION, PR_DIFF, "애매한 키워드3", client=broken_client)
    check(
        "깨진 JSON -> 크래시 없이 not_important + error 필드",
        result["label"] == "not_important" and bool(result.get("error")),
    )

    # 4. API 호출 자체가 실패 (rate limit, timeout 등)
    crashing_client = FakeClient(raise_exc=RuntimeError("rate limit exceeded"))
    result = keyword_judge(PR_TITLE, PR_DESCRIPTION, PR_DIFF, "애매한 키워드4", client=crashing_client)
    check(
        "API 예외 -> 크래시 없이 not_important + error 필드",
        result["label"] == "not_important" and bool(result.get("error")),
    )

    # 5. 오프라인에서 바로 걸러지는 키워드는 client가 아예 호출되면 안 됨
    unused_client = FakeClient(raise_exc=RuntimeError("호출되면 안 됨"))
    result = keyword_judge(PR_TITLE, PR_DESCRIPTION, PR_DIFF, "record-128", client=unused_client)
    check("오프라인에서 걸러지는 키워드는 LLM 호출 안 함", result["source"] == "offline")

    print("\n모든 mock 테스트 통과 (실제 API 호출 없음)")


if __name__ == "__main__":
    main()
